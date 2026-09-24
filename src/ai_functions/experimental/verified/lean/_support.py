"""The support executables, built once per toolchain and source hash, and their JSON-lines transport."""

from __future__ import annotations

import collections
import hashlib
import json
import shutil
import tempfile
import threading
import weakref
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType

from .errors import LeanError, LeanTimeoutError
from .execution import Deadline, LakeEnv, _kill_process_group, _terminate_process, file_lock, run_lake, spawn_lake
from .toolchain import ResolvedToolchain

# The support executable's root module first, then the modules compiled with it.
_MODULES = ("Server", "Introspect", "Confine", "Check")


def build_support_executable(*, tools: ResolvedToolchain, cache: Path, timeout: float) -> Path:
    """Return the executable built from these sources for this Lean installation, building it if missing."""
    remaining = Deadline(timeout, "Building the Lean support executable exceeded its deadline").remaining
    sources = {name: (Path(__file__).parent / "_lean" / f"{name}.lean").read_bytes() for name in _MODULES}
    digest = hashlib.sha256(f"{tools.identity}\0{tools.lean.parent.parent}".encode())
    for name, source in sources.items():
        digest.update(b"\0" + name.encode() + b"\0" + source)
    directory = cache / "lean" / "support" / "Server" / digest.hexdigest()
    executable = directory / "server"
    with file_lock(directory.parent / ".locks" / f"{digest.hexdigest()}.lock", timeout=remaining()):
        if executable.is_file():
            return executable
        with tempfile.TemporaryDirectory(prefix="ai-functions-lean-support-") as temporary:
            build = Path(temporary).resolve()
            for name, source in sources.items():
                (build / f"{name}.lean").write_bytes(source)
            (build / "lakefile.toml").write_text(
                f'name = "ai_functions_support"\n[[lean_lib]]\nname = "Support"\nroots = {json.dumps(_MODULES[1:])}\n'
                '[[lean_exe]]\nname = "server"\nroot = "Server"\nsupportInterpreter = true\n'
            )
            run_lake(LakeEnv(tools, build, offline=True), ["build", "server"], timeout=remaining())
            directory.mkdir(parents=True, exist_ok=True)
            shutil.copy2(build / ".lake" / "build" / "bin" / "server", directory / "executable.tmp")
            (directory / "executable.tmp").replace(executable)
    return executable


class SupportSession:
    """One support process under ``lake env`` answering JSON requests in turn.

    ``timeout`` bounds the process's whole life (a preparation deadline); the
    ``timeout`` of ``request`` bounds one request. On expiry, death, or a
    ``fatal`` reply the process is killed and the session is closed for good.
    """

    def __init__(
        self,
        lake: LakeEnv,
        executable: Path,
        args: Sequence[str] = (),
        *,
        cwd: Path | None = None,
        extra_env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> None:
        self._name = executable.name
        self._proc = spawn_lake(lake, ["env", str(executable), *args], cwd=cwd, extra_env=extra_env)
        self._finalizer = weakref.finalize(self, _terminate_process, self._proc)
        self._expired = False
        self._stderr: collections.deque[bytes] = collections.deque(maxlen=16)
        threading.Thread(target=self._drain, daemon=True).start()
        self._lifetime = self._timer(timeout)

    def _drain(self) -> None:
        try:
            while chunk := self._proc.stderr.read1(65536):  # type: ignore[union-attr]
                self._stderr.append(chunk)
        except (OSError, ValueError):
            pass

    def _timer(self, timeout: float | None) -> threading.Timer | None:
        if timeout is None:
            return None
        timer = threading.Timer(timeout, self._expire)
        timer.daemon = True
        timer.start()
        return timer

    def _expire(self) -> None:
        self._expired = True
        _kill_process_group(self._proc.pid)

    def request(self, payload: Mapping[str, object], *, timeout: float | None = None) -> dict:
        """Send one request and return its reply.

        Raises:
            LeanTimeoutError: A deadline expired.
            LeanError: The process died, replied malformed or ``fatal``.
        """
        if not self._finalizer.alive:
            raise LeanError(f"The Lean {self._name} helper is closed")
        stdin, stdout = self._proc.stdin, self._proc.stdout
        assert stdin is not None and stdout is not None
        timer = self._timer(timeout)
        try:
            stdin.write(json.dumps(payload).encode() + b"\n")
            stdin.flush()
            line = stdout.readline()
        except (OSError, ValueError):
            line = b""
        finally:
            if timer is not None:
                timer.cancel()
        try:
            if self._expired:
                raise LeanTimeoutError(f"The Lean {self._name} helper timed out")
            if not line:
                stderr = b"".join(self._stderr).decode(errors="replace")
                raise LeanError(f"The Lean {self._name} helper exited unexpectedly:\n{stderr}")
            reply = json.loads(line)
            if not isinstance(reply, dict):
                raise LeanError(f"The Lean {self._name} helper wrote a non-object reply: {line!r}")
            if "fatal" in reply:
                raise LeanError(f"The Lean {self._name} helper rejected a request: {reply['fatal']}")
        except BaseException as exc:
            self.close()
            if isinstance(exc, ValueError):
                raise LeanError(f"The Lean {self._name} helper wrote invalid output: {line!r}") from exc
            raise
        return reply

    def close(self) -> None:
        """Stop the process; idempotent."""
        if self._lifetime is not None:
            self._lifetime.cancel()
        self._finalizer()

    def __enter__(self) -> SupportSession:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()
