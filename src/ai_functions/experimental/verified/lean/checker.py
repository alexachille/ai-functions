"""The trusted cold checker (``_lean/Check.lean``).

An artifact is compiled to ``.olean`` in a fresh, confined process, then the
support executable loads the compiled module in another fresh process without
elaborating it or running any of its code, and checks it against a ``CheckSpec``.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from ._support import SupportSession, build_support_executable
from .errors import LeanError, LeanTimeoutError
from .execution import LakeEnv
from .project import LeanProject
from .project import _name_parts as name_parts
from .server import Source
from .toolchain import cache_path

MODULE = "AIFunctions.Certificate"
"""The module name a certified artifact is compiled under."""


@dataclass(frozen=True)
class NativeAudit:
    """``verified.ai_compile``'s policy for the exported native entry point."""

    answer: str
    proof: str
    trusted_modules: frozenset[str]
    fixed_declarations: tuple[str, ...]


@dataclass(frozen=True)
class CheckSpec:
    """What the checker verifies about one compiled module.

    Each declaration ``expected`` adds is a twin of the module's declaration of
    the same name: a ``def`` twin fixes the type and the value, a ``theorem``
    twin the kind and the type, and an ``axiom`` twin only the type.
    """

    expected: str = ""
    axioms: tuple[str, ...] = ()
    native_audit: NativeAudit | None = None
    imports: tuple[str, ...] | None = None

    def to_json(self, module: str) -> dict[str, object]:
        """Encode for the checker."""
        if self.imports is None:
            raise ValueError("The checker needs the module's expected imports")
        audit = self.native_audit
        return {
            "module": name_parts(module),
            "imports": [name_parts(name) for name in self.imports],
            "expected": self.expected,
            "axioms": [name_parts(name) for name in self.axioms],
            "native_audit": None
            if audit is None
            else {
                "answer": name_parts(audit.answer),
                "proof": name_parts(audit.proof),
                "trusted_modules": sorted(audit.trusted_modules),
                "fixed_declarations": [name_parts(name) for name in audit.fixed_declarations],
            },
        }


@dataclass(frozen=True)
class CheckResult:
    """The verdict; ``errors`` explains a rejection or a failed compile."""

    ok: bool
    errors: str = ""
    axioms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    native_imports: tuple[str, ...] = ()


def _run(lake: LakeEnv, cache: Path, mode: str, request: dict, directory: Path, timeout: float) -> dict:
    """Answer one request in a fresh support process."""
    executable = build_support_executable(tools=lake.tools, cache=cache, timeout=timeout)
    environment = {"LEAN_PATH": str(directory)} if mode == "check" else None
    with SupportSession(lake, executable, [mode], cwd=directory, extra_env=environment) as session:
        return session.request(request, timeout=timeout)


def compile_module(
    lake: LakeEnv,
    cache: Path,
    source: str | Source,
    directory: Path,
    module: str = MODULE,
    *,
    timeout: float,
    c: Path | None = None,
) -> CheckResult:
    """Elaborate ``source`` as ``module`` in a fresh process, confining its model ranges; untrusted.

    The module's C code is written to ``c`` if given.
    """
    source = Source.of(source)
    olean = directory.joinpath(*name_parts(module)).with_suffix(".olean")
    olean.parent.mkdir(parents=True, exist_ok=True)
    request = {
        "module": name_parts(module),
        "source": source.text,
        "olean": str(olean),
        "c": None if c is None else str(c),
        "model": source.ranges,
    }
    try:
        reply = _run(lake, cache, "compile", request, directory, timeout)
    except LeanTimeoutError:
        raise
    except LeanError as exc:
        # The compile runs the artifact's Lean: a crash is a failed compile.
        return CheckResult(False, str(exc))
    errors = "\n".join(map(str, reply.get("errors", ())))
    if reply.get("ok") is not True or errors:
        return CheckResult(False, errors or "Compilation failed")
    return CheckResult(True)


def check_module(
    lake: LakeEnv, cache: Path, directory: Path, spec: CheckSpec, module: str = MODULE, *, timeout: float
) -> CheckResult:
    """Check a compiled module found under ``directory`` against ``spec``."""
    reply = _run(lake, cache, "check", spec.to_json(module), directory, timeout)
    try:
        errors = "\n".join(str(error) for error in reply["errors"])
        axioms = {str(name): tuple(map(str, found)) for name, found in reply["axioms"].items()}
        return CheckResult(reply["ok"] is True and not errors, errors, axioms, tuple(map(str, reply["native_imports"])))
    except (KeyError, TypeError, AttributeError) as exc:
        raise LeanError(f"The Lean checker returned a malformed reply: {reply}") from exc


def check_source(
    project: LeanProject,
    source: str | Source,
    spec: CheckSpec | None = None,
    *,
    timeout: float | None = None,
) -> CheckResult:
    """Compile ``source`` as a fresh module in a scratch directory, then check it."""
    prepared = project.prepare()
    spec = spec or CheckSpec()
    if spec.imports is None:
        spec = replace(spec, imports=prepared.imports)
    seconds = 120.0 if timeout is None else timeout
    cache = cache_path(project.toolchain)
    with tempfile.TemporaryDirectory(prefix="cold-", dir=project.root / ".ai_functions" / "work") as temporary:
        directory = Path(temporary)
        compiled = compile_module(prepared.lake, cache, source, directory, timeout=seconds)
        if not compiled.ok:
            return compiled
        return check_module(prepared.lake, cache, directory, spec, timeout=seconds)
