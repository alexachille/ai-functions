"""Python functions implemented by synthesized, formally verified native code.

``@verified.ai_compile(contract=...)`` decorates a body-less Python function whose
signature uses ``int``, ``bool``, ``float`` and ``list[int]``. The contract is
result-first, as for ``verified.ai_function``: ``C (result : R) (a : A) (b : B) : Prop`` for
``def f(a: A, b: B) -> R``. The implementation ``f : A → B → R`` (``R``
without parameters) must satisfy ``∀ a b, C (f a b) a b``. Executable code may use
Lean core and the prelude; imported project modules are proof-only.

The native ABI maps ``int`` to ``Int``, ``bool`` to ``Bool``, ``float`` to
``Float`` and ``list[int]`` to ``List Int``; this table is private to
``verified.compile``.

Invariants:
    V1, V3, V4.
"""

from collections.abc import Callable
from pathlib import Path

from strands.models import Model

from ..dsl import Definition
from ..lean import LeanProject, LeanSymbol

class _CompiledFunction[**P, T]:
    """A callable whose implementation is a cached, verified native artifact.

    Lifecycle:
        UNCOMPILED → COMPILED; ``compile`` is idempotent once COMPILED.
    """

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
        """Inspect the Python signature; runs no Lean.

        Args:
            fn: A synchronous function whose docstring is the author's guidance.
            contract: The result-first ``Prop``; a ``Definition`` stands for its
                symbol.
            model: The synthesis model; defaults to a Bedrock-hosted Claude model.
            max_attempts: Retries after the first final submission.
            compile_timeout: Seconds per Lean request, check, and native build step.
            cache_dir: Artifact cache; defaults to the platform AI Functions cache.

        Raises:
            ContractError: ``fn`` is async or a generator, or its signature leaves
                the native ABI.
            CompilerError: The interpreter is not a supported CPython build.
            ValueError: ``max_attempts`` is negative, or ``compile_timeout`` is not
                positive and finite.
        """
        ...

    @property
    def contract(self) -> LeanSymbol:
        """The contract symbol."""
        ...

    @property
    def project(self) -> LeanProject:
        """The contract's project."""
        ...

    @property
    def name(self) -> str:
        """The Python function's name."""
        ...

    @property
    def is_compiled(self) -> bool:
        """Whether a verified artifact is loaded."""
        ...

    @property
    def artifact_dir(self) -> Path | None:
        """The artifact directory, or ``None`` before compilation.

        It holds ``Verified.lean`` (prelude, implementation, proof and export
        theorem), compiled as ``lean.checker.MODULE`` like a ``verified.ai_function`` certificate;
        its ``.olean`` and C code; the generated C adapter; and the native library.
        Treat it as read-only.
        """
        ...

    async def compile(self) -> _CompiledFunction[P, T]:
        """Load a verified implementation, synthesizing one if none is cached.

        The cache key covers the contract, the project fingerprint, the signature,
        the guidance, the toolchain, the host ABI and the compiler's sources.

        Ensures:
            - The contract's type is checked to be ``R → A → B → Prop`` for the
              signature before any model call.
            - The proved goal is ``∀ a b, C (f a b) a b`` for the synthesized
              ``f``, stated in harness text.
            - A cache hit makes no model call.
            - A synthesized implementation is certified in term mode with no
              observations or judgments.
            - The module with the native entry point and its correctness theorem
              passes the trusted checker, native audit included, before it is linked.
            - A new artifact is published by renaming a complete directory.

        Raises:
            ContractError: The contract's type does not match the signature.
            SynthesisError: No implementation was accepted within ``max_attempts``.
            CompilerError: Lean, the checker, or the native build failed.

        Concurrency:
            Serialized per cache key across threads and processes.
        """
        ...

    def compile_sync(self) -> _CompiledFunction[P, T]:
        """Run ``compile`` from synchronous code."""
        ...

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        """Call the native implementation, compiling first if needed.

        Ensures:
            ``list`` arguments are copied before the call.

        Raises:
            TypeError: An argument's type is not exactly the annotated one.
            CompilerError: Native execution failed.
        """
        ...

    def run_sync(self, *args: P.args, **kwargs: P.kwargs) -> T:
        """Call from synchronous code; no model call once compiled."""
        ...

def ai_compile[**P, T](
    *,
    contract: LeanSymbol | Definition,
    model: Model | str | None = None,
    max_attempts: int = 10,
    compile_timeout: float = 120,
    cache_dir: str | Path | None = None,
) -> Callable[[Callable[P, T]], _CompiledFunction[P, T]]:
    """Decorate a body-less function; the arguments are those of ``_CompiledFunction``."""
    ...
