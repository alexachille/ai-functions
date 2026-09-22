"""One Python decorator for synthesis, proof checking, caching, and native calls."""

from __future__ import annotations

import asyncio
import functools
import os
import shutil
import tempfile
import typing
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

import platformdirs
from botocore.config import Config as BotocoreConfig
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
from botocore.exceptions import ReadTimeoutError as BotoReadTimeoutError
from strands.models import BedrockModel
from strands.types.exceptions import MaxTokensReachedException
from urllib3.exceptions import ReadTimeoutError as HTTPReadTimeoutError

from ..ai_thread.ai_function import ai_function
from ..ai_thread.errors import AIFunctionError
from ..utils import run_blocking
from .compiler import (
    SPEC_HELPERS,
    Artifact,
    Candidate,
    build_candidate,
    cache_key,
    find_runtime,
    read_artifact,
    require_supported_python,
    write_manifest,
)
from .contracts import Scalar, Specification, kind_name, specification
from .errors import CandidateError, CompilerError, ModelSetupError, SynthesisError

if TYPE_CHECKING:
    from strands.models import Model

_DEFAULT_MODEL_ID = "global.anthropic.claude-opus-5"
_DEFAULT_MAX_TOKENS = 65536
_DEFAULT_READ_TIMEOUT = 900


@asynccontextmanager
async def _cache_lock(path: Path) -> AsyncIterator[None]:
    # File locks work across processes, threads and the separate loops used by
    # run_sync. Nonblocking polling keeps cancellation responsive.
    import fcntl

    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _prompt(spec: Specification) -> str:
    arguments = ", ".join(f"{name} is v{i}: {kind_name(kind)}" for i, (name, kind) in enumerate(spec.parameters))
    quantifier = f"forall {spec.binders}, " if spec.parameters else ""
    args = spec.arguments
    return f"""Synthesize a pure, total Lean {spec.result_type} function and its proof.
Return the structured Candidate with implementation and proof fields only.
Inputs: {arguments or "(no arguments)"}.
Author guidance (the formal contracts below are authoritative):
{spec.guidance}

The following declarations are fixed and cannot be changed:
{SPEC_HELPERS}
{spec.declarations()}

Your implementation field is ONLY the expression body of:
def implementation {spec.binders} : {spec.result_type} := ...
Your proof field is ONLY the term beginning with `by` proving:
{quantifier}pre {args} = true -> post (implementation {args}) {args} = true

Implementation vocabulary: inputs v0, v1, ...; locals t0, t1, ...; decimal or
hexadecimal integer literals; true/false; if/then/else; let; +, -, *; comparisons and Boolean
operators; min, max, abs, Int.natAbs, Int.ofNat, Int.ediv, Nat.sqrt; pure List/Array functions,
and Float arithmetic/classification. Explicitly terminating local recursion is allowed.
List inputs/outputs are List Int. Float inputs/outputs use binary64, not real arithmetic.
Use Float.beq for floating-point equality; NaN is unequal to itself, while signed zeroes compare equal.
Do not use floating-point bit inspection, IO, partial definitions, or panicking operations.
Proof vocabulary: intro, exact, apply, refine, have, show, cases, constructor,
split, simp, simp_all, only, at, all_goals, first, try, repeat, omega, grind,
decide, rfl, assumption, contradiction, trivial, by_cases, subst, rw, simpa, unfold,
dsimp, change, revert, rcases, induction, calc. Use explicit binders for local names.
Core Int/Nat/Bool/List/Array/Float lemmas are allowed.
The trusted environment imports Lean, including omega and grind, but no Mathlib.
Useful list lemmas include List.all_eq_true, List.any_eq_true, List.pairwise_cons,
List.findIdx_nil, List.findIdx_cons, List.findIdx_le_length, List.not_of_lt_findIdx,
List.Pairwise.rel_of_mem_take_of_mem_drop, List.take_succ_cons, List.drop_succ_cons,
List.length_take, List.length_drop, and List.length_cons. Sortedness is List.Pairwise.
For min/max arithmetic, unfold Int.min_def and Int.max_def before using omega.
For integer division, useful bounds are Int.mul_ediv_self_le (nonzero denominator)
and Int.lt_mul_ediv_self_add (positive denominator). Normalize distributive products
with Int.add_mul, Int.mul_add, and Int.sub_mul; explicit product sign or monotonicity
lemmas such as Int.mul_nonneg and Int.mul_le_mul_of_nonneg_left can reduce the
remaining obligations to linear arithmetic for omega.
Before omega on Int.ofNat expressions, normalize casts with
`simp only [Int.ofNat_eq_natCast] at *`. For nonnegative, in-range slice indices,
pythonIndex_ofNat, pythonSlice_prefix, and pythonSlice_suffix are available.
An often useful proof is: by intro v0 v1 v2 h; simp_all [pre, post, implementation]; split <;> simp_all <;> omega
Use the appropriate number of inputs. No comments, strings, imports, commands,
custom attributes, sorry/admit, unsafe code, native_decide, or run_tac.
"""


class _VerifiedFunction[**P, T]:
    """A Python callable whose cached implementation has passed formal verification."""

    def __init__(
        self,
        fn: Callable[P, T],
        *,
        pre_conditions: Sequence[Callable[..., object]] = (),
        post_conditions: Sequence[Callable[..., object]] = (),
        model: Model | str | None = None,
        max_attempts: int = 10,
        compile_timeout: float = 120,
        cache_dir: str | Path | None = None,
        output_type: type[T] | None = None,
    ) -> None:
        require_supported_python()
        if type(max_attempts) is not int or max_attempts < 0:
            raise ValueError("max_attempts must be a nonnegative integer (the number of retries).")
        if type(compile_timeout) not in (int, float) or not 0 < compile_timeout < float("inf"):
            raise ValueError("compile_timeout must be a positive, finite number of seconds.")
        self._spec = specification(fn, tuple(pre_conditions), tuple(post_conditions), output_type)
        self._model = _DEFAULT_MODEL_ID if model is None else model
        self._max_attempts = max_attempts
        self._timeout = compile_timeout
        self._cache = (
            Path(cache_dir).expanduser().resolve()
            if cache_dir is not None
            else Path(platformdirs.user_cache_dir("ai_functions")) / "verified"
        )
        self._artifact: Artifact | None = None
        functools.update_wrapper(self, fn, updated=())

    @property
    def name(self) -> str:
        """Return the original Python function's name."""
        return self._spec.name

    @property
    def is_compiled(self) -> bool:
        """Whether this object has resolved a verified native artifact."""
        return self._artifact is not None

    @property
    def artifact_dir(self) -> Path | None:
        """Directory of the verified sources and binary, or None before compilation.

        This is optional diagnostic access. Reading the property never starts
        synthesis or compilation. Treat the cached files as read-only.
        """
        return self._artifact.directory if self._artifact is not None else None

    async def compile(self) -> _VerifiedFunction[P, T]:
        """Prepare one reusable implementation, or reuse a verified cached artifact.

        Synthesis runs once for all inputs satisfying the Python preconditions.
        The compiler runtime must already have been installed during Python
        setup. This method never installs software or downloads compiler tools.
        """
        if self._artifact is not None:
            return self
        runtime = find_runtime()
        key = cache_key(self._spec, runtime)
        self._cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self._cache / key
        async with _cache_lock(self._cache / (key + ".lock")):
            artifact = await asyncio.to_thread(read_artifact, target, runtime, key)
            await asyncio.to_thread(runtime.preflight)
            if artifact is not None:
                self._artifact = artifact
                return self

            synthesis_model = self._model
            if isinstance(synthesis_model, str) and synthesis_model == _DEFAULT_MODEL_ID:
                synthesis_model = BedrockModel(
                    model_id=_DEFAULT_MODEL_ID,
                    max_tokens=_DEFAULT_MAX_TOKENS,
                    boto_client_config=BotocoreConfig(read_timeout=_DEFAULT_READ_TIMEOUT, connect_timeout=30),
                )

            @ai_function[Candidate](
                model=synthesis_model,
                max_attempts=0,
                coordinator_tools_enabled=False,
                callback_handler=None,
                system_prompt=(
                    "Produce only the requested implementation and a complete proof of the fixed specification."
                ),
            )
            def synthesize(prompt: str) -> str:
                return prompt

            # Proof/code messages belong to the internal compiler, not the
            # application's conversation or normal event feed.
            handle = await synthesize.spawn()
            diagnostics: list[str] = []
            prompt = _prompt(self._spec)
            try:
                for _attempt in range(self._max_attempts + 1):
                    try:
                        candidate = await handle.run(prompt)
                    except MaxTokensReachedException:
                        diagnostics.append(
                            "The model exhausted its output-token limit before returning a complete candidate."
                        )
                        prompt = (
                            "Your previous output reached the token limit. Return a concise, complete Candidate "
                            "containing implementation and proof. Reuse standard-library lemmas where possible. "
                            "Do not repeat the specification or explain your approach."
                        )
                        continue
                    except (NoCredentialsError, PartialCredentialsError):
                        raise ModelSetupError(
                            f"Cannot synthesize {self.name!r}: Amazon Bedrock credentials are missing or incomplete. "
                            "Set AWS_PROFILE to an authenticated AWS profile, or pass a configured model=.",
                            function_name=self.name,
                        ) from None
                    except (BotoReadTimeoutError, HTTPReadTimeoutError):
                        raise ModelSetupError(
                            f"The model request for {self.name!r} timed out before returning a complete candidate. "
                            "Retry the call, or pass a model configured with a longer read timeout.",
                            function_name=self.name,
                        ) from None
                    with tempfile.TemporaryDirectory(prefix="candidate-", dir=self._cache) as temporary:
                        directory = Path(temporary)
                        try:
                            module = await build_candidate(runtime, self._spec, candidate, directory, self._timeout)
                        except CandidateError as exc:
                            diagnostics.append(str(exc))
                            prompt = (
                                "Your candidate failed verification. Keep the specification unchanged and return "
                                "a revised "
                                f"implementation and proof. Diagnostics:\n{exc}"
                            )
                            continue
                        write_manifest(directory, module, key)
                        if target.exists():
                            # This is an invalid entry in our own generated cache;
                            # it must never mask a newly checked artifact.
                            shutil.rmtree(target)
                        directory.rename(target)
                        self._artifact = Artifact(target, module, runtime)
                        return self
            finally:
                await handle.terminate_now()
            raise SynthesisError(self.name, diagnostics)

    def compile_sync(self) -> _VerifiedFunction[P, T]:
        """Prepare the verified implementation from synchronous Python code."""
        return run_blocking(self.compile)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        """Validate inputs, compile if needed, and call the verified native function."""
        values = self._spec.bind(*args, **kwargs)
        await self.compile()
        return await asyncio.to_thread(self._invoke, values)

    def _invoke(self, values: dict[str, Scalar]) -> T:
        artifact = self._artifact
        if artifact is None:
            raise CompilerError("The verified implementation is not ready.", function_name=self.name)
        try:
            result = artifact.invoke(self._spec, values)
        except AIFunctionError:
            raise
        except Exception as exc:
            error = CompilerError(f"Native execution failed for {self.name!r}.", function_name=self.name)
            error.diagnostics = str(exc)
            raise error from None
        # This is an additional check of the trusted conversion boundary, not
        # a substitute for the proof, and does not execute Python callbacks.
        for condition in self._spec.post:
            if not condition.predicate.evaluate({**values, "r": result}):
                raise CompilerError(
                    f"Compiled result failed contract {condition.name!r} ({condition.location}).",
                    function_name=self.name,
                )
        return typing.cast(T, result)

    def run_sync(self, *args: P.args, **kwargs: P.kwargs) -> T:
        """Call from synchronous Python, with no model calls after compilation."""
        values = self._spec.bind(*args, **kwargs)
        if self._artifact is None:
            self.compile_sync()
        return self._invoke(values)


class _TypedDecorator[T]:
    def __init__(self, output_type: type[T]) -> None:
        self.output_type = output_type

    @overload
    def __call__[**P](self, fn: Callable[P, T], /) -> _VerifiedFunction[P, T]: ...

    @overload
    def __call__(self, **kwargs: Any) -> Callable[[Callable[..., T]], _VerifiedFunction[..., T]]: ...

    def __call__(self, fn: Callable[..., T] | None = None, /, **kwargs: Any) -> Any:
        def decorate(function: Callable[..., T]) -> _VerifiedFunction[..., T]:
            return _VerifiedFunction(function, output_type=self.output_type, **kwargs)

        return decorate(fn) if fn is not None else decorate


class _VerifiedFactory:
    """The single public decorator; implementation and runtime types stay private."""

    def __getitem__[T](self, output_type: type[T]) -> _TypedDecorator[T]:
        return _TypedDecorator(output_type)

    @overload
    def __call__[**P, T](self, fn: Callable[P, T], /) -> _VerifiedFunction[P, T]: ...

    @overload
    def __call__[T](
        self,
        *,
        pre_conditions: Sequence[Callable[..., object]] = (),
        post_conditions: Sequence[Callable[..., object]] = (),
        model: Model | str | None = None,
        max_attempts: int = 10,
        compile_timeout: float = 120,
        cache_dir: str | Path | None = None,
    ) -> Callable[[Callable[..., T]], _VerifiedFunction[..., T]]: ...

    def __call__(self, fn: Callable[..., Any] | None = None, /, **kwargs: Any) -> Any:
        def decorate(function: Callable[..., Any]) -> _VerifiedFunction[..., Any]:
            return _VerifiedFunction(function, **kwargs)

        return decorate(fn) if fn is not None else decorate


ai_verified_function = _VerifiedFactory()
"""Generate and cache a verified native implementation from Python contracts.

Requires CPython 3.12+ and the ``verified`` installation extra. Preconditions and
postconditions use ordinary synchronous Python validator functions. The initial
supported domain is pure ``int``/``bool``/``float``/``list[int]`` functions with explicit type hints.
``max_attempts`` is the number of retries after the initial synthesis attempt.
"""
