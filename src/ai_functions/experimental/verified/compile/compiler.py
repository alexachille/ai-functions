"""The verified module: a certified term, its native entry point and that entry point's correctness theorem.

The module is compiled to ``.olean`` and C, checked by the trusted checker with the
native audit, and only then linked with a generated C adapter into a library the
bridge loads.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

from ..dsl.types import render_type
from ..function import LeanTerm
from ..lean import LeanError, LeanSymbol
from ..lean.checker import MODULE, CheckSpec, NativeAudit, check_module, compile_module
from ..lean.execution import run_command
from ..lean.toolchain import cache_path
from ._runtime import Runtime
from .errors import CompilerError
from .signature import ABI, Signature

# The module is compiled under the session's main module, so private names agree.
LIBRARY = "Verified" + (".dylib" if sys.platform == "darwin" else ".so")
_INITIALIZER = "initialize_" + "_".join(part.replace("_", "__") for part in MODULE.split("."))


def cache_key(signature: Signature, contract: LeanSymbol, runtime: Runtime, guidance: str) -> str:
    """Identify an artifact by everything that determines it."""
    root = Path(__file__).parent.parent
    sources = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.suffix in (".py", ".lean", ".c", ".h"):
            sources.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    identity = {
        "contract": contract.name,
        "project": contract.project.fingerprint,
        "signature": [render_type(t) for t in signature.types],
        "guidance": guidance,
        "runtime": runtime.identity,
        "sources": sources.hexdigest(),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def build(
    runtime: Runtime, signature: Signature, contract: LeanSymbol, term: LeanTerm, directory: Path, timeout: float
) -> None:
    """Write, compile, check and link the verified module in ``directory``."""
    prepared = contract.project.prepare()
    tools = runtime.toolchain
    *parameters, result = signature.types
    variables = [f"v{i}" for i in range(signature.arity)]
    binders = " ".join(f"({v} : {render_type(t)})" for v, t in zip(variables, parameters, strict=True))
    entry = f"{MODULE}.nativeEntry"
    entry_type = f"(_token : UInt8) {binders} : {render_type(result)}"
    # The whole-function goal, for the entry point the library exports.
    applied = " ".join([f"({entry} 0)", *variables])
    goal = (f"∀ {binders}, " if binders else "") + " ".join([contract.name, f"({applied})", *variables])
    source = (
        term.source
        + f"\nnamespace {MODULE}\n\n@[export ai_verified_entry]\n"
        + f"def nativeEntry {entry_type} := {' '.join([term.declaration, *variables])}\n\n"
        + f"theorem nativeEntry_correct : {goal} := {term.proof_declaration}\n\nend {MODULE}\n"
    )
    (directory / "Verified.lean").write_text(source.text)
    core = tools.root / "lib" / "lean"
    spec = CheckSpec(
        expected=f"{prepared.prelude}\naxiom {entry} {entry_type}\naxiom {entry}_correct : {goal}\n",
        native_audit=NativeAudit(
            term.declaration,
            term.proof_declaration,
            frozenset(".".join(p.relative_to(core).with_suffix("").parts) for p in core.rglob("*.olean")),
            prepared.fixed_declarations,
        ),
        imports=prepared.imports,
    )
    cache = cache_path(contract.project.toolchain)
    try:
        compiled = compile_module(prepared.lake, cache, source, directory, timeout=timeout, c=directory / "Verified.c")
        if not compiled.ok:
            raise CompilerError("Lean rejected the verified module.", diagnostics=compiled.errors)
        checked = check_module(prepared.lake, cache, directory, spec, timeout=timeout)
        if not checked.ok:
            raise CompilerError("The trusted checker rejected the verified module.", diagnostics=checked.errors)
        # Imported project modules are proof-only: stub the initializers the C code calls.
        c_code = (directory / "Verified.c").read_text()
        stubs = [name for name in checked.native_imports if re.search(rf"\b{re.escape(name)}\(", c_code)]
        (directory / "proof_imports.c").write_text(
            '#include "lean/lean.h"\n'
            + "".join(
                f"lean_object *{n}(uint8_t b) {{ (void)b; return lean_io_result_mk_ok(lean_box(0)); }}\n" for n in stubs
            )
        )
        (directory / "adapter.c").write_text(_adapter(signature))
        if sys.platform == "darwin":
            visibility = ["-Wl,-exported_symbol,_ai_verified_descriptor_v1"]
        else:
            (directory / "exports.map").write_text("{ global: ai_verified_descriptor_v1; local: *; };\n")
            visibility = [f"-Wl,--version-script={directory / 'exports.map'}"]
        linked = run_command(
            [
                str(tools.leanc),
                *("-shared", "-DLEAN_EXPORTING", "-O2", "-ffp-contract=off", "-o", LIBRARY),
                *("Verified.c", "proof_imports.c", "adapter.c", "-I", str(runtime.directory)),
                *tools.native_flags,
                *tools.link_args(),
                *visibility,
            ],
            cwd=directory,
            environment=tools.environment(directory),
            timeout=timeout,
            check=False,
        )
        if linked.returncode:
            raise CompilerError("The verified module could not be linked.", diagnostics=linked.stdout + linked.stderr)
    except LeanError as exc:
        raise CompilerError(f"Compiling the verified module failed: {exc}", diagnostics=str(exc)) from exc


def _adapter(signature: Signature) -> str:
    """The C adapter from the bridge's descriptor to the entry point; it contains no model text."""
    *parameters, result = [ABI[a] for a in signature.annotations]
    c_parameters = ", ".join(["uint8_t", *(c for _, _, c, _ in parameters)])
    arguments = ", ".join(["0", *(f"arguments[{i}].{member}" for i, (_, _, _, member) in enumerate(parameters))])
    kinds = ", ".join(str(kind) for _, kind, _, _ in [*parameters, result])
    return f"""#include "ffi.h"
extern {result[2]} ai_verified_entry({c_parameters});
extern lean_object *{_INITIALIZER}(uint8_t);
static void invoke(const av_value *arguments, av_value *result) {{
    (void)arguments;
    result->{result[3]} = ai_verified_entry({arguments});
}}
static const uint8_t signature[] = {{{kinds}}};
static const av_descriptor descriptor = {{
    AV_ABI_MAGIC, AV_ABI_VERSION, {len(parameters)}, signature, {_INITIALIZER}, invoke
}};
LEAN_EXPORT const av_descriptor *ai_verified_descriptor_v1(void) {{ return &descriptor; }}
"""


def load(runtime: Runtime, signature: Signature, directory: Path) -> Callable[[dict[str, object]], object]:
    """Load the library in ``directory`` through the bridge."""
    kinds = bytes(ABI[a][1] for a in signature.annotations)
    return runtime.bridge().load(str(directory / LIBRARY), kinds)
