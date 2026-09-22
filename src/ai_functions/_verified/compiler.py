"""Private proof checking, native compilation, and typed native calls.

Candidate text has a small lexical vocabulary and occupies only expression and
proof positions in a trusted template. In particular it cannot introduce
commands, imports, metaprograms, strings, attributes, options, or external code.
The saved declarations are replayed by the kernel in a separate process before
native compilation or loading. The public API never downloads a toolchain.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import sysconfig
import textwrap
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel, Field

from .contracts import Scalar, Specification
from .errors import CandidateError, CompilerError

LEAN_VERSION = "4.33.0"
RUNTIME_VERSION = "0.2.0"
TRANSLATOR_VERSION = 3
FFI_ABI_VERSION = 1
_NATIVE_KINDS = {int: 1, bool: 2, float: 3, list: 4}
_NATIVE_LOCK = threading.RLock()
_BRIDGES: dict[Path, ModuleType] = {}
_BRIDGE_PROCESS: int | None = None
_MAX_SOURCE = 32_768
_MAX_DIAGNOSTICS = 32_768

SPEC_HELPERS = """public def pythonIndex (length : Nat) (index : Int) : Nat :=
  (max 0 (min (Int.ofNat length) (if index < 0 then Int.ofNat length + index else index))).toNat
public def pythonSlice (xs : List Int) (start stop : Option Int) : List Int :=
  let first := (start.map (pythonIndex xs.length)).getD 0
  let last := (stop.map (pythonIndex xs.length)).getD xs.length
  (xs.drop first).take (last - first)
public theorem pythonIndex_ofNat (length index : Nat) (h : index <= length) :
    pythonIndex length (Int.ofNat index) = index := by
  simp [pythonIndex, Int.min_def, Int.max_def]
  omega
public theorem pythonSlice_prefix (xs : List Int) (n : Nat) (h : n <= xs.length) :
    pythonSlice xs none (some (Int.ofNat n)) = xs.take n := by
  change (xs.drop 0).take (pythonIndex xs.length (Int.ofNat n) - 0) = xs.take n
  rw [pythonIndex_ofNat xs.length n h]
  simp
public theorem pythonSlice_suffix (xs : List Int) (n : Nat) (h : n <= xs.length) :
    pythonSlice xs (some (Int.ofNat n)) none = xs.drop n := by
  change (xs.drop (pythonIndex xs.length (Int.ofNat n))).take
    (xs.length - pythonIndex xs.length (Int.ofNat n)) = xs.drop n
  rw [pythonIndex_ofNat xs.length n h]
  rw [← List.length_drop, List.take_length]
"""


class Candidate(BaseModel):
    """The model provides two terms, never module configuration or declarations."""

    implementation: str = Field(max_length=_MAX_SOURCE, description="An executable Lean expression using v0, v1, ...")
    proof: str = Field(max_length=_MAX_SOURCE, description="A Lean proof beginning with by, for the supplied theorem")


_IMPL_WORDS = {
    "if",
    "then",
    "else",
    "let",
    "in",
    "true",
    "false",
    "Int",
    "Nat",
    "Bool",
    "min",
    "max",
    "abs",
    "Int.natAbs",
    "Int.ofNat",
    "Int.negOfNat",
    "Int.negSucc",
    "Int.toNat",
    "Int.ediv",
    "List",
    "Array",
    "Option",
    "Float",
    "UInt64",
    "some",
    "none",
    "fun",
    "match",
    "with",
    "rec",
    "termination_by",
    "decreasing_by",
    "by",
    "decide",
    "Nat.sqrt",
    "Float.ofBits",
    "Float.add",
    "Float.sub",
    "Float.mul",
    "Float.div",
    "Float.neg",
    "Float.abs",
    "Float.beq",
    "Float.le",
    "Float.lt",
    "Float.isFinite",
    "Float.isInf",
    "Float.isNaN",
    "nil",
    "cons",
    "id",
    "Nat.succ",
    "Nat.pred",
    "Nat.zero",
}
_PROOF_WORDS = _IMPL_WORDS | {
    "by",
    "intro",
    "intros",
    "exact",
    "apply",
    "refine",
    "have",
    "show",
    "from",
    "fun",
    "cases",
    "case",
    "induction",
    "match",
    "with",
    "constructor",
    "left",
    "right",
    "split",
    "simp",
    "simp_all",
    "only",
    "at",
    "all_goals",
    "any_goals",
    "first",
    "next",
    "repeat",
    "try",
    "omega",
    "grind",
    "decide",
    "rfl",
    "assumption",
    "contradiction",
    "trivial",
    "classical",
    "by_cases",
    "by_contra",
    "rename_i",
    "subst",
    "rw",
    "rwa",
    "simpa",
    "suffices",
    "pre",
    "post",
    "implementation",
    "isTrue",
    "isFalse",
    "pos",
    "neg",
    "zero",
    "succ",
    "True",
    "False",
    "And",
    "Or",
    "Not",
    "Eq",
    "congrArg",
    "congrFun",
    "of_decide_eq_true",
    "decide_eq_true_eq",
    "Bool.true_eq_false",
    "True.intro",
    "False.elim",
    "And.intro",
    "And.left",
    "And.right",
    "Or.inl",
    "Or.inr",
    "Or.elim",
    "Eq.refl",
    "Eq.symm",
    "Eq.trans",
    "unfold",
    "dsimp",
    "change",
    "revert",
    "specialize",
    "rcases",
    "rintro",
    "obtain",
    "calc",
    "ext",
    "congr",
    "trans",
    "exact_mod_cast",
    "assumption_mod_cast",
    "min_def",
    "max_def",
    "le_refl",
    "le_trans",
    "lt_of_lt_of_le",
    "lt_of_le_of_lt",
    "le_of_lt",
    "not_lt",
    "not_le",
    "le_total",
    "pythonSlice",
    "pythonIndex",
    "pythonIndex_ofNat",
    "pythonSlice_prefix",
    "pythonSlice_suffix",
    "if_neg",
    "if_pos",
    "if_false",
    "if_true",
    "dif_neg",
    "dif_pos",
    "take",
    "drop",
    "length",
    "getD",
}

_FORBIDDEN_WORDS = {
    "unsafe",
    "unsafeCast",
    "sorry",
    "sorryAx",
    "admit",
    "axiom",
    "axioms",
    "partial",
    "partial_fixpoint",
    "initialize",
    "builtin_initialize",
    "set_option",
    "attribute",
    "extern",
    "implemented_by",
    "import",
    "namespace",
    "section",
    "end",
    "open",
    "export",
    "def",
    "theorem",
    "opaque",
    "constant",
    "macro",
    "syntax",
    "elab",
    "run_tac",
    "run_elab",
    "run_cmd",
    "native_decide",
    "native",
    "IO",
    "BaseIO",
    "EIO",
    "Lean",
    "System",
    "eval",
    "eval_expr",
    "panic",
    "unreachable",
}


def _local_names(source: str) -> set[str]:
    """Recognize explicit binders without allowing new global capabilities."""
    names: set[str] = set()
    patterns = [
        r"\b(?:intro|intros|rename_i)\s+([^;\n<|]+)",
        r"\b(?:let|have|by_cases|by_contra|obtain)\s+(?:rec\s+)?([A-Za-z_][A-Za-z0-9_']*)",
        r"\bfun\s+([^=\n]+?)\s*=>",
        r"\(([A-Za-z_][A-Za-z0-9_' ]*)\s*:",
        r"\|\s*([^=>\n]+?)\s*=>",
        r"\bcase\s+([^=>\n]+?)\s*=>",
        r"\brcases[^\n]*?\bwith\s+([^;\n]+)",
    ]
    for pattern in patterns:
        for group in re.findall(pattern, source):
            names.update(re.findall(r"[A-Za-z_][A-Za-z0-9_']*", group))
    return names - _FORBIDDEN_WORDS


def validate_candidate(candidate: Candidate, arity: int) -> None:
    """Reject source capable of changing the trusted template or running metacode."""
    for name, source, words in (
        ("implementation", candidate.implementation, _IMPL_WORDS),
        ("proof", candidate.proof, _PROOF_WORDS),
    ):
        if not source.strip() or len(source) > _MAX_SOURCE or any(c in source for c in ("--", "/-", "-/")):
            raise CandidateError(
                f"The {name} must be a nonempty term without comments, at most {_MAX_SOURCE} characters."
            )
        if re.search(r"[^a-zA-Z0-9_\s()\[\]{}:;,=<>+*/!&|.?'\-≤≥≠¬∧∨→←↔↦∀∃∈∉⟨⟩↑·]", source):
            raise CandidateError(f"The {name} contains unsupported syntax. Do not use strings, comments, or commands.")
        if re.search(r"[\]A-Za-z0-9_']!(?!=)", source):
            raise CandidateError("Panicking operations and native proof shortcuts are not permitted.")
        names_source = re.sub(r"\b(?:0[xX][0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)\b", "0", source)
        locals_ = _local_names(source)
        for word in re.findall(r"[a-zA-Z_][a-zA-Z0-9_'.]*", names_source):
            if word in _FORBIDDEN_WORDS or word.split(".")[0] in ("Lean", "IO", "System"):
                raise CandidateError(f"The {name} cannot use {word!r}.")
            if word in words or word == "_":
                continue
            if word in locals_:
                continue
            if re.fullmatch(r"v\d+", word) and int(word[1:]) < arity:
                continue
            if re.fullmatch(r"t\d+", word):
                continue
            root, _, projection = word.partition(".")
            if name == "proof" and root in locals_ and projection:
                continue
            if (root in locals_ or re.fullmatch(r"[vt]\d+", root)) and projection in (
                "length",
                "size",
                "toArray",
                "toList",
                "toNat",
                "natAbs",
                "isEmpty",
                "1",
                "2",
                "mp",
                "mpr",
            ):
                continue
            if re.fullmatch(r"(?:List|Array)\.[a-zA-Z0-9_'.]+", word):
                continue
            if name == "implementation" and word in _PROOF_WORDS and "by" in source:
                continue
            if name == "proof":
                if re.fullmatch(r"[a-z][0-9']*", word) or re.fullmatch(r"(?:pre|post)\d+", word):
                    continue
                if re.fullmatch(r"(?:Int|Nat|Bool|Float|Option|UInt64)\.[a-zA-Z0-9_'.]+", word):
                    continue
            raise CandidateError(
                f"Unsupported {name} identifier {word!r}; use the vocabulary in the synthesis instructions."
            )
    if not candidate.proof.lstrip().startswith("by"):
        raise CandidateError("The proof must begin with 'by'.")


def source(spec: Specification, candidate: Candidate, module: str) -> str:
    """Place model terms in a fixed module with a fixed theorem and FFI entry."""
    validate_candidate(candidate, len(spec.parameters))
    quantifier = f"forall {spec.binders}, " if spec.parameters else ""
    args = spec.arguments
    actual = f"(implementation {args})" if args else "implementation"
    theorem = f"{quantifier}pre {args} = true -> post {actual} {args} = true"
    declarations = spec.declarations().replace("\ndef ", "\npublic def ")
    if declarations.startswith("def "):
        declarations = "public " + declarations
    implementation = textwrap.indent(candidate.implementation.strip(), "    ")
    proof = textwrap.indent(candidate.proof.strip(), "  ")
    return f"""module
public import Init
meta import all Lean
set_option maxHeartbeats 400000
set_option maxRecDepth 1024
set_option linter.unusedVariables false
namespace {module}
{SPEC_HELPERS}
{declarations}
public def implementation {spec.binders} : {spec.result_type} :=
  (
{implementation}
  )
public theorem implementation_correct : {theorem} :=
{proof}
open Lean Elab Command in
run_cmd do
  let axioms <- collectAxioms `{module}.implementation_correct
  for ax in axioms do
    unless #[`propext, `Quot.sound, `Classical.choice].contains ax do
      throwError "Unexpected proof assumption: {{ax}}"
@[export ai_verified_entry_{module}]
public def nativeEntry (_token : UInt8) {spec.binders} : {spec.result_type} :=
  {actual}
end {module}
"""


def native_shim(spec: Specification, module: str) -> str:
    """Generate the small, typed C adapter; it never contains model-written C."""
    c_types = {int: "lean_object *", list: "lean_object *", bool: "uint8_t", float: "double"}
    members = {int: "object", list: "object", bool: "boolean", float: "floating"}
    kinds = [kind for _, kind in spec.parameters]
    parameters = ", ".join(["uint8_t", *(c_types[kind] for kind in kinds)])
    arguments = ", ".join(["0", *(f"arguments[{i}].{members[kind]}" for i, kind in enumerate(kinds))])
    signature = ", ".join(str(_NATIVE_KINDS[kind]) for kind in [*kinds, spec.output_type])
    return f"""#include "ffi.h"
extern {c_types[spec.output_type]} ai_verified_entry_{module}({parameters});
extern lean_object *runtime_initialize_{module}(uint8_t);
static void invoke(const av_value *arguments, av_value *result) {{
    (void)arguments;
    result->{members[spec.output_type]} = ai_verified_entry_{module}({arguments});
}}
static const uint8_t signature[] = {{{signature}}};
static const av_descriptor descriptor = {{
    AV_ABI_MAGIC, AV_ABI_VERSION, {len(kinds)}, signature,
    runtime_initialize_{module}, invoke
}};
LEAN_EXPORT const av_descriptor *ai_verified_descriptor_v1(void) {{ return &descriptor; }}
"""


def require_supported_python() -> None:
    """Require a compatible CPython interpreter for the native extension."""
    if sys.implementation.name != "cpython" or sys.version_info < (3, 12):
        raise CompilerError("ai_verified_function requires CPython 3.12 or newer.")
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise CompilerError("ai_verified_function requires a CPython build with the GIL enabled.")
    if sys.platform not in ("darwin", "linux"):
        raise CompilerError("ai_verified_function currently supports macOS and Linux.")


@dataclass(frozen=True)
class Runtime:
    """Paths to the runtime installed by the normal Python dependency resolver."""

    directory: Path

    @property
    def toolchain(self) -> Path:
        """Return the private, pinned native toolchain root."""
        return self.directory / "toolchain"

    @property
    def extension(self) -> Path:
        """Return the extension built for this CPython minor version."""
        return self.directory / f"_bridge{sysconfig.get_config_var('EXT_SUFFIX')}"

    @property
    def identity(self) -> str:
        """Identify compiler, FFI ABI and platform in cache keys."""
        # Native libraries reference this installation's runtime libraries.
        # A different virtual environment must not reuse dangling RPATHs.
        identity = f"{RUNTIME_VERSION}:{LEAN_VERSION}:{sys.platform}:{os.uname().machine}:{self.directory.resolve()}"
        # Include linkage in the identity so cached libraries always use the
        # same explicit dependency resolution as newly compiled artifacts.
        if flags := self.native_link_args():
            identity += ":" + json.dumps(flags, separators=(",", ":"))
        return identity

    def native_link_args(self) -> list[str]:
        """Resolve plugins against the bridge's installed shared runtime."""
        library_dir = str(self.toolchain / "lib" / "lean")
        if sys.platform == "darwin":
            # Do not rely on the loader's caller (e.g. a sanitizer interceptor)
            # to supply its own RPATHs when resolving this artifact's libraries.
            return [
                "-Xlinker",
                "-rpath",
                "-Xlinker",
                library_dir,
                "-Xlinker",
                "-rpath",
                "-Xlinker",
                str(self.toolchain / "lib"),
            ]
        # Python loads extensions with RTLD_LOCAL. An ELF plugin therefore
        # needs explicit dependencies on Lean's libraries, rather than relying
        # on their symbols being globally visible. Reject unresolved symbols
        # while linking instead of letting the loader terminate Python later.
        return ["-Wl,-z,defs", f"-Wl,-rpath,{library_dir}", "-L", library_dir, "-lleanshared", "-lInit_shared"]

    def environment(self, directory: Path) -> dict[str, str]:
        """Isolate compiler discovery from user or ambient compiler configuration."""
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("LEAN", "LAKE", "DYLD_")) and k not in ("LD_PRELOAD", "LD_LIBRARY_PATH")
        }
        env.update(
            {
                "PATH": str(self.toolchain / "bin") + os.pathsep + os.environ.get("PATH", ""),
                "LEAN_SYSROOT": str(self.toolchain),
                "LEAN_PATH": str(directory),
                "LC_ALL": "C",
            }
        )
        if sys.platform == "darwin":
            env["MACOSX_DEPLOYMENT_TARGET"] = "15.0"
        return env

    def bridge(self) -> ModuleType:
        """Load the prebuilt native extension once, without setup downloads."""
        global _BRIDGE_PROCESS
        with _NATIVE_LOCK:
            if _BRIDGES and _BRIDGE_PROCESS != os.getpid():
                raise CompilerError("Compiled functions require a fresh Python process after fork; use spawn.")
            if _BRIDGES and self.directory not in _BRIDGES:
                raise CompilerError("The compiler runtime cannot be switched in a running process. Restart Python.")
            if self.directory not in _BRIDGES:
                try:
                    module_name = "_ai_functions_verified_runtime._bridge"
                    spec = importlib.util.spec_from_file_location(module_name, self.extension)
                    if spec is None or spec.loader is None:
                        raise ImportError("Missing native extension loader")
                    bridge = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(bridge)
                    if bridge.ABI_VERSION != FFI_ABI_VERSION:
                        raise ImportError("Native bridge ABI does not match the compiler adapter")
                except (ImportError, OSError, RuntimeError, SystemError) as exc:
                    raise CompilerError(
                        "The verified-function runtime could not be loaded. Reinstall strands-ai-functions[verified]."
                    ) from exc
                _BRIDGES[self.directory] = bridge
                _BRIDGE_PROCESS = os.getpid()
            return _BRIDGES[self.directory]

    def preflight(self) -> None:
        """Check compiler availability before spending an LLM attempt."""
        for binary in ("lean", "leanchecker", "leanc", "clang"):
            if not (self.toolchain / "bin" / binary).is_file():
                raise CompilerError(
                    "The verified-function runtime is incomplete. Reinstall strands-ai-functions[verified]."
                )
        try:
            result = subprocess.run(
                [str(self.toolchain / "bin" / "lean"), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                env=self.environment(self.directory),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CompilerError(
                "The private compiler could not start. Reinstall strands-ai-functions[verified]."
            ) from exc
        if result.returncode or f"version {LEAN_VERSION}," not in result.stdout:
            raise CompilerError(
                "The verified-function runtime has an incompatible version. Reinstall the verified extra."
            )
        self.bridge()


def find_runtime() -> Runtime:
    """Find installed runtime data; an override supports offline/development setups."""
    require_supported_python()
    override = os.environ.get("AI_FUNCTIONS_VERIFIED_RUNTIME")
    if override:
        directory = Path(override).expanduser().resolve()
    else:
        spec = importlib.util.find_spec("_ai_functions_verified_runtime")
        if spec is None or not spec.submodule_search_locations:
            raise CompilerError(
                "Verification support is not installed. Install it during Python setup with "
                "pip install 'strands-ai-functions[verified]'."
            )
        directory = Path(next(iter(spec.submodule_search_locations))).resolve()
    runtime = Runtime(directory)
    if not runtime.extension.is_file() or not (directory / "ffi.h").is_file():
        raise CompilerError("Verification support is incomplete. Reinstall strands-ai-functions[verified].")
    try:
        metadata = json.loads((directory / "runtime.json").read_text())
        if metadata.get("version") != RUNTIME_VERSION or metadata.get("ffi_abi") != FFI_ABI_VERSION:
            raise ValueError("Incompatible native runtime")
    except (OSError, ValueError, AttributeError) as exc:
        raise CompilerError("Verification support needs an update. Reinstall strands-ai-functions[verified].") from exc
    return runtime


async def run_tool(runtime: Runtime, directory: Path, args: list[str], timeout: float) -> tuple[int, str]:
    """Run a private compiler process; terminate its process group on cancellation."""
    try:
        process = await asyncio.create_subprocess_exec(
            str(runtime.toolchain / "bin" / args[0]),
            *args[1:],
            cwd=directory,
            env=runtime.environment(directory),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        raise CompilerError("The private compiler could not be started.") from exc
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.CancelledError, TimeoutError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    return process.returncode or 0, output.decode("utf-8", errors="replace")[-_MAX_DIAGNOSTICS:]


async def build_candidate(
    runtime: Runtime,
    spec: Specification,
    candidate: Candidate,
    directory: Path,
    timeout: float,
) -> str:
    """Check the proof, replay its declarations, then build the exact checked code."""
    identity = f"{TRANSLATOR_VERSION}\n{runtime.identity}\n{spec.identity()}\n{candidate.model_dump_json()}"
    module = "Verified" + hashlib.sha256(identity.encode()).hexdigest()[:40]
    code = source(spec, candidate, module)
    (directory / f"{module}.lean").write_text(code)
    try:
        status, output = await run_tool(
            runtime,
            directory,
            ["lean", "-o", f"{module}.olean", "-c", f"{module}.c", f"{module}.lean"],
            timeout,
        )
        if status:
            raise CandidateError(output or "The candidate did not satisfy the specification.")
        status, output = await run_tool(runtime, directory, ["leanchecker", module], timeout)
        if status:
            raise CandidateError(output or "The proof failed independent kernel replay.")
    except TimeoutError as exc:
        raise CandidateError("Proof checking exceeded its time budget; simplify the implementation or proof.") from exc
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    shim = f"{module}.ffi.c"
    (directory / shim).write_text(native_shim(spec, module))
    try:
        status, output = await run_tool(
            runtime,
            directory,
            [
                "leanc",
                "-shared",
                "-DLEAN_EXPORTING",
                "-O2",
                "-ffp-contract=off",
                "-o",
                module + suffix,
                f"{module}.c",
                shim,
                "-I",
                str(runtime.directory),
                *runtime.native_link_args(),
            ],
            timeout,
        )
    except TimeoutError as exc:
        raise CompilerError("Native compilation exceeded its time budget.") from exc
    if status:
        error = CompilerError("The verified implementation could not be compiled to native code.")
        error.diagnostics = output
        raise error
    return module


@dataclass(frozen=True)
class Artifact:
    """A published, immutable native artifact and its compilation identity."""

    directory: Path
    module: str
    runtime: Runtime
    _native: Callable[[dict[str, Scalar]], Scalar] | None = field(default=None, compare=False, repr=False)

    def invoke(self, spec: Specification, values: dict[str, Scalar]) -> Scalar:
        """Call a cached typed C entry without serializing arguments or results."""
        native = self._native
        if native is None:
            with _NATIVE_LOCK:
                native = self._native
                if native is None:
                    bridge = self.runtime.bridge()
                    suffix = ".dylib" if sys.platform == "darwin" else ".so"
                    signature = bytes(
                        [_NATIVE_KINDS[kind] for _, kind in spec.parameters] + [_NATIVE_KINDS[spec.output_type]]
                    )
                    native = bridge.load(str(self.directory / (self.module + suffix)), signature)
                    object.__setattr__(self, "_native", native)
        return native(values)


def cache_key(spec: Specification, runtime: Runtime) -> str:
    """Separate artifacts by semantics, compiler version, platform and ABI."""
    value = f"{TRANSLATOR_VERSION}\n{runtime.identity}\n{spec.identity()}"
    return hashlib.sha256(value.encode()).hexdigest()


def write_manifest(directory: Path, module: str, key: str) -> None:
    """Record every artifact byte before atomically publishing the directory."""
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.is_file()}
    (directory / "manifest.json").write_text(json.dumps({"key": key, "module": module, "files": files}, sort_keys=True))


def read_artifact(directory: Path, runtime: Runtime, key: str) -> Artifact | None:
    """Reject missing, incomplete or corrupted cache entries."""
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        if not isinstance(manifest, dict):
            return None
        module = manifest["module"]
        if not isinstance(module, str) or manifest["key"] != key or not re.fullmatch(r"Verified[0-9a-f]{40}", module):
            return None
        files = manifest["files"]
        if not isinstance(files, dict):
            return None
        suffix = ".dylib" if sys.platform == "darwin" else ".so"
        if not {module + ".lean", module + ".olean", module + suffix}.issubset(files):
            return None
        for name, expected in files.items():
            if not isinstance(name, str) or Path(name).name != name or (directory / name).is_symlink():
                return None
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
                return None
        return Artifact(directory, module, runtime)
    except (OSError, ValueError, KeyError, TypeError):
        return None
