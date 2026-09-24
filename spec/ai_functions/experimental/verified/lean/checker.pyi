"""The trusted cold checker.

An artifact is compiled to ``.olean`` in a fresh, confined process, then the support
executable loads the compiled module in another fresh process without elaborating it
or running any of its code.

Invariants:
    V1, V2, V3.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .execution import LakeEnv
from .project import LeanProject
from .server import Source

MODULE: str
"""The module name a certified artifact is compiled under."""

def name_parts(name: str) -> list[str]:
    """Split a Lean name into its components, unquoting ``«»`` parts.

    Raises:
        ValueError: ``name`` is not a Lean name.
    """
    ...

@dataclass(frozen=True)
class NativeAudit:
    """``verified.ai_compile``'s policy for the exported native entry point.

    Attributes:
        answer: The implementation's declaration.
        proof: The proof of its contract.
        trusted_modules: Modules whose executable code may be reached (Lean core).
        fixed_declarations: The prelude's declarations, also reachable.
    """

    answer: str
    proof: str
    trusted_modules: frozenset[str]
    fixed_declarations: tuple[str, ...]

@dataclass(frozen=True)
class CheckSpec:
    """What the checker verifies about one compiled module.

    The expectations alone say what the module must contain: each declaration
    ``expected`` adds is a twin of the module's declaration of the same name. A
    twin stated as a ``def`` fixes the type and the value; one stated as an
    ``axiom`` fixes only the type, and stands for a theorem (the goal's proof) or a
    definition whose value the harness does not know (a term-mode answer). This is
    sound because model text cannot declare axioms, and the axiom inventory rejects
    any axiom it does not permit.

    Attributes:
        expected: The prelude and the twin of every harness declaration, as Lean text.
        axioms: Declarations whose axiom inventories are returned.
        native_audit: Also run the native audit.
        imports: The imports the module must have; the project's by default.
    """

    expected: str = ""
    axioms: tuple[str, ...] = ()
    native_audit: NativeAudit | None = None
    imports: tuple[str, ...] | None = None

    def to_json(self, module: str) -> dict[str, object]:
        """Encode for the checker.

        Ensures:
            Every field is present; ``None`` is ``null``.

        Raises:
            ValueError: ``imports`` is ``None``.
        """
        ...

@dataclass(frozen=True)
class CheckResult:
    """The verdict.

    Attributes:
        errors: Why the module was rejected or did not compile.
        axioms: Each name of ``CheckSpec.axioms`` mapped to its inventory.
        native_imports: C initializers of imported modules the native audit found
            proof-only, for the native build to stub.
    """

    ok: bool
    errors: str = ""
    axioms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    native_imports: tuple[str, ...] = ()

def compile_module(
    lake: LakeEnv,
    cache: Path,
    source: str | Source,
    directory: Path,
    module: str = ...,
    *,
    timeout: float,
    c: Path | None = None,
) -> CheckResult:
    """Elaborate ``source`` as ``module`` in a fresh process.

    Ensures:
        - The model ranges of ``source`` are confined.
        - ``module``'s ``.olean`` is written under ``directory`` iff the result is ok.
        - With ``c``, the module's C code is written there iff the result is ok.
    """
    ...

def check_module(
    lake: LakeEnv, cache: Path, directory: Path, spec: CheckSpec, module: str = ..., *, timeout: float
) -> CheckResult:
    """Check a compiled module found under ``directory`` against ``spec``.

    The result is ok iff all of the following hold.

    Ensures:
        - The module imports exactly ``Init`` and ``spec.imports``.
        - Every declaration of the module replays through the kernel.
        - ``spec.expected`` elaborates over the imports without the module.
        - Every declaration ``spec.expected`` adds exists in the module with a
          kernel-definitionally-equal type.
        - Where that twin is a ``def``, the module's is a ``def`` with a
          definitionally equal value; where it is a ``theorem``, the module's is a
          ``theorem``; where it is an ``axiom``, only the type is compared.
        - With ``native_audit``, the module passes the native policy.
        - ``axioms`` is collected by this process, not taken from the module.

    Raises:
        LeanError: The checker failed or replied malformed.
    """
    ...

def check_source(
    project: LeanProject,
    source: str | Source,
    spec: CheckSpec | None = None,
    *,
    timeout: float | None = None,
) -> CheckResult:
    """Compile ``source`` as a fresh module in a scratch directory, then check it.

    Ensures:
        ``spec.imports`` defaults to the prepared project's imports.
    """
    ...
