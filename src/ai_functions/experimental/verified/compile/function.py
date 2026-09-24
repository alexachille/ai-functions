"""The ``verified.ai_compile`` decorator: synthesis, checking, caching and native calls."""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import math
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import platformdirs
from botocore.config import Config
from filelock import AsyncFileLock
from strands.models import BedrockModel, Model

from ....ai_thread.errors import AIFunctionError
from ....utils import run_blocking
from ..dsl import Definition
from ..function import ContractNotProved, LeanTerm, ai_function
from ..lean import LeanError, LeanProject, LeanSymbol
from ..lean.execution import run_in_thread
from ..lean.toolchain import cache_path
from ._runtime import Runtime, require_supported_python, resolve_runtime
from .compiler import LIBRARY, build, cache_key, load
from .errors import CompilerError, SynthesisError
from .signature import signature

_logger = logging.getLogger(__name__)

_PROMPT = """\
Implement the Python function `{name}{python}` as a pure, total Lean function and prove
the goal for every input. Python int is Lean Int, float is Float, bool is Bool and
list[int] is List Int.

The implementation is compiled to native code. It may use Lean core and total definitions
in the ledger, including the prelude; imported project modules may be used in proofs only.
Do not use runtime tracing, panics, sleeping, platform-dependent results or Float.toBits.

Author guidance:
{guidance}
"""


class _CompiledFunction[**P, T]:
    """A callable whose implementation is a cached, verified native artifact."""

    def __init__(
        self,
        fn: Callable[P, T],
        *,
        contract: LeanSymbol | Definition,
        model: Model | str | None = None,
        max_attempts: int = 10,
        compile_timeout: float = 120,
        cache_dir: str | Path | None = None,
    ) -> None:
        require_supported_python()
        if type(max_attempts) is not int or max_attempts < 0:
            raise ValueError("max_attempts must be a nonnegative int: the retries after the first submission")
        if type(compile_timeout) not in (int, float) or not 0 < compile_timeout < math.inf:
            raise ValueError("compile_timeout must be a positive, finite number of seconds")
        contract = contract.symbol if isinstance(contract, Definition) else contract
        if not isinstance(contract, LeanSymbol):
            raise TypeError("contract must be a LeanSymbol or a Definition")
        self._signature = signature(fn)
        self._contract = contract
        self._docstring = inspect.getdoc(fn) or ""
        self._model = model
        self._max_attempts = max_attempts
        self._timeout = float(compile_timeout)
        default = Path(platformdirs.user_cache_dir("ai_functions")) / "verified" / "compiled"
        self._cache = Path(cache_dir).expanduser().resolve() if cache_dir is not None else default
        self._directory: Path | None = None
        self._native: Callable[[dict[str, object]], object] | None = None
        functools.update_wrapper(self, fn, updated=())

    @property
    def contract(self) -> LeanSymbol:
        return self._contract

    @property
    def project(self) -> LeanProject:
        return self._contract.project

    @property
    def name(self) -> str:
        return self._signature.name

    @property
    def is_compiled(self) -> bool:
        return self._native is not None

    @property
    def artifact_dir(self) -> Path | None:
        return self._directory

    async def compile(self) -> _CompiledFunction[P, T]:
        if self._native is not None:
            return self
        try:
            prepared = await run_in_thread(self.project.prepare, timeout=self._timeout)
            await run_in_thread(self._signature.check, self._contract)
            runtime = await run_in_thread(resolve_runtime, prepared.lake.tools, cache_path(self.project.toolchain))
            await run_in_thread(runtime.preflight, self._timeout)
        except LeanError as exc:
            raise CompilerError(f"Could not prepare {self.project.root}: {exc}", function_name=self.name) from exc
        target = self._cache / cache_key(self._signature, self._contract, runtime, self._docstring)
        self._cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        async with AsyncFileLock(target.with_suffix(".lock")):
            if (target / LIBRARY).exists():
                _logger.info("Reusing verified %s from %s", self.name, target)
            else:
                await self._synthesize(runtime, target)
            self._native = await run_in_thread(load, runtime, self._signature, target)
            self._directory = target
        return self

    async def _synthesize(self, runtime: Runtime, target: Path) -> None:
        """Certify an implementation, then build its artifact and publish it by renaming its directory."""
        prompt = _PROMPT.format(name=self.name, python=self._signature.python, guidance=self._docstring)
        model = self._model or BedrockModel(
            model_id="global.anthropic.claude-opus-5",
            max_tokens=65536,
            boto_client_config=Config(read_timeout=900, connect_timeout=30),
        )

        @ai_function(
            contract=self._contract,
            timeout=self._timeout,
            system_prompt=prompt,
            model=model,
            max_attempts=self._max_attempts,
            coordinator_tools_enabled=False,
            callback_handler=None,
        )
        def synthesize() -> LeanTerm:
            """Implement the function and prove its contract for every input."""

        _logger.info("Synthesizing %s with at most %d retries", self.name, self._max_attempts)
        try:
            term = await synthesize()
        except ContractNotProved as exc:
            raise SynthesisError(self.name, list(exc.diagnostics) or [str(exc)]) from exc
        except LeanError as exc:
            raise CompilerError(f"Certification failed: {exc}", function_name=self.name) from exc
        except AIFunctionError as exc:
            raise SynthesisError(self.name, [str(exc)]) from exc
        work = Path(tempfile.mkdtemp(prefix="build-", dir=self._cache))
        try:
            await run_in_thread(build, runtime, self._signature, self._contract, term, work, self._timeout)
            shutil.rmtree(target, ignore_errors=True)
            work.rename(target)
        except CompilerError as exc:
            exc.function_name = self.name
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        _logger.info("Verified and compiled %s in %s", self.name, target)

    def compile_sync(self) -> _CompiledFunction[P, T]:
        return run_blocking(self.compile)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        values = self._signature.bind(args, kwargs)
        await self.compile()
        return await asyncio.to_thread(self._invoke, values)

    def run_sync(self, *args: P.args, **kwargs: P.kwargs) -> T:
        values = self._signature.bind(args, kwargs)
        if self._native is None:
            self.compile_sync()
        return self._invoke(values)

    def _invoke(self, values: dict[str, object]) -> T:
        assert self._native is not None
        try:
            return cast(T, self._native(values))
        except Exception as exc:
            raise CompilerError(
                f"Native execution of {self.name!r} failed.", diagnostics=str(exc), function_name=self.name
            ) from None


def ai_compile[**P, T](
    *,
    contract: LeanSymbol | Definition,
    model: Model | str | None = None,
    max_attempts: int = 10,
    compile_timeout: float = 120,
    cache_dir: str | Path | None = None,
) -> Callable[[Callable[P, T]], _CompiledFunction[P, T]]:
    """Implement a body-less function by synthesized native code proved to satisfy ``contract``."""

    def decorate(fn: Callable[P, T]) -> _CompiledFunction[P, T]:
        return _CompiledFunction(
            fn,
            contract=contract,
            model=model,
            max_attempts=max_attempts,
            compile_timeout=compile_timeout,
            cache_dir=cache_dir,
        )

    return decorate
