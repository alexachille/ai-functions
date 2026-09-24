"""Build and load the CPython/Lean bridge (``_native/bridge.c``) in the Lean cache."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import sysconfig
import tempfile
import threading
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import ModuleType

from ..lean import LeanError
from ..lean.execution import file_lock, run_command
from ..lean.toolchain import ResolvedToolchain
from .errors import CompilerError

FFI_ABI_VERSION = 1
_SOURCES = ("bridge.c", "ffi.h")
_LOCK = threading.Lock()
# The one bridge this process loaded: its directory, the loading process, the module.
_BRIDGE: tuple[Path, int, ModuleType] | None = None


def require_supported_python() -> None:
    """Require the interpreters and platforms the bridge supports."""
    if sys.implementation.name != "cpython" or sys.version_info < (3, 12):
        raise CompilerError("verified.ai_compile requires CPython 3.12 or newer.")
    if sysconfig.get_config_var("Py_GIL_DISABLED") or sysconfig.get_config_var("Py_DEBUG"):
        raise CompilerError("verified.ai_compile requires a non-debug CPython build with the GIL enabled.")
    if sys.platform not in ("darwin", "linux"):
        raise CompilerError("verified.ai_compile supports macOS and Linux.")


@dataclass(frozen=True)
class Runtime:
    """A Lean toolchain and the bridge built for it and this interpreter."""

    toolchain: ResolvedToolchain
    directory: Path
    identity: str
    includes: tuple[str, ...]

    @property
    def extension(self) -> Path:
        """The bridge's extension module; it exists only in a complete bridge directory."""
        return self.directory / f"_bridge{sysconfig.get_config_var('EXT_SUFFIX')}"

    def preflight(self, timeout: float = 120) -> None:
        """Build the bridge unless it is cached, then load it."""
        try:
            with file_lock(self.directory.with_suffix(".lock"), timeout=timeout):
                if not self.extension.exists():
                    self._build(timeout)
        except LeanError as exc:
            raise CompilerError(
                "Could not build the Python/Lean bridge. Check the Lean installation, the Python "
                "development headers and the host C toolchain.",
                diagnostics=str(exc),
            ) from exc
        self.bridge()

    def _build(self, timeout: float) -> None:
        work = Path(tempfile.mkdtemp(prefix="bridge-", dir=self.directory.parent))
        try:
            for name in _SOURCES:
                (work / name).write_bytes(files(__package__).joinpath("_native", name).read_bytes())
            output = work / self.extension.name
            tools = self.toolchain
            command = [
                str(tools.leanc),
                *("-shared", "-O3", "-std=c11", "-Wall", "-Wextra", "-Werror=implicit-function-declaration"),
                *tools.native_flags,
                *(flag for include in self.includes for flag in ("-I", include)),
                str(work / "bridge.c"),
                *tools.link_args(python_extension=True),
                *("-o", str(output)),
            ]
            run_command(command, environment=tools.environment(work), timeout=timeout)
            if sys.platform == "darwin":
                run_command(["/usr/bin/codesign", "--force", "--sign", "-", str(output)], timeout=timeout)
            shutil.rmtree(self.directory, ignore_errors=True)
            work.rename(self.directory)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def bridge(self) -> ModuleType:
        """Load the bridge once per process; a process uses one bridge, and a forked child none."""
        global _BRIDGE
        with _LOCK:
            if _BRIDGE is None:
                try:
                    spec = importlib.util.spec_from_file_location(f"{__package__}._bridge", self.extension)
                    if spec is None or spec.loader is None:
                        raise ImportError(f"No extension loader for {self.extension}")
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    if module.ABI_VERSION != FFI_ABI_VERSION:
                        raise ImportError("The bridge's ABI does not match the compiler")
                except (ImportError, OSError) as exc:
                    raise CompilerError(f"The Python/Lean bridge could not be loaded: {exc}") from exc
                _BRIDGE = (self.directory, os.getpid(), module)
            directory, pid, module = _BRIDGE
            if pid != os.getpid():
                raise CompilerError("Compiled functions need a fresh Python process after fork; use spawn.")
            if directory != self.directory:
                raise CompilerError("A running process cannot switch Lean runtimes. Restart Python.")
            return module


def resolve_runtime(toolchain: ResolvedToolchain, cache: Path) -> Runtime:
    """The bridge for ``toolchain`` and this interpreter, under ``cache``; builds nothing."""
    require_supported_python()
    includes = tuple(dict.fromkeys(sysconfig.get_path(name) for name in ("include", "platinclude")))
    for header in ("Python.h", "pyconfig.h", "cpython/longintrepr.h"):
        if not any((Path(include) / header).is_file() for include in includes):
            raise CompilerError(f"The running CPython is missing {header}; install its development headers.")
    try:
        native = toolchain.native_identity
    except LeanError as exc:
        raise CompilerError(f"Lean cannot compile native code on this host: {exc}") from exc
    sources = {name: files(__package__).joinpath("_native", name).read_bytes() for name in _SOURCES}
    identity = json.dumps(
        {
            "native": native,
            "python": sys.version,
            "soabi": sysconfig.get_config_var("SOABI"),
            "includes": includes,
            "abi": FFI_ABI_VERSION,
            "sources": {name: hashlib.sha256(source).hexdigest() for name, source in sources.items()},
        },
        sort_keys=True,
    )
    directory = cache / "lean" / "bridges" / hashlib.sha256(identity.encode()).hexdigest()
    return Runtime(toolchain, directory, identity, includes)
