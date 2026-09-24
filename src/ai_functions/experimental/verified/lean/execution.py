"""Process execution, deadlines, cancellation and cache locks for Lean consumers."""

from __future__ import annotations

import asyncio
import math
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import filelock

from .errors import LeanError, LeanSetupError, LeanTimeoutError

if TYPE_CHECKING:
    from .toolchain import ResolvedToolchain

__all__ = [
    "Deadline",
    "LakeEnv",
    "file_lock",
    "run_command",
    "run_command_async",
    "run_in_thread",
    "run_lake",
    "spawn_lake",
]

_MAX_DIAGNOSTICS = 32_768


class Deadline:
    """A monotonic deadline shared by the steps of one bounded operation.

    ``timeout=None`` never expires. ``remaining()`` raises ``LeanTimeoutError``
    with ``message`` (or an override) once no time is left.
    """

    def __init__(self, timeout: float | None, message: str) -> None:
        self._end = None if timeout is None else time.monotonic() + timeout
        self._message = message

    def remaining(self, message: str | None = None) -> float:
        """Return the positive seconds left, raising once the deadline has passed."""
        if self._end is None:
            return math.inf
        seconds = self._end - time.monotonic()
        if seconds <= 0:
            raise LeanTimeoutError(message or self._message)
        return seconds


@contextmanager
def file_lock(path: Path, *, timeout: float | None = None) -> Iterator[None]:
    """Hold the ``filelock`` lock at ``path``, excluding other threads and processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.FileLock(path, timeout=-1 if timeout is None else timeout):
            yield
    except filelock.Timeout as exc:
        raise LeanTimeoutError(f"Timed out waiting for Lean cache lock: {path}") from exc


@dataclass(frozen=True)
class LakeEnv:
    """Everything needed to run Lake for one build directory.

    ``offline`` disables Lake's artifact cache and allows only local fetches.
    """

    tools: ResolvedToolchain
    root: Path
    offline: bool = False


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # PermissionError: macOS refuses to signal a group whose leader is a zombie.
        pass


def _terminate_process(proc: subprocess.Popen) -> None:
    """Kill, reap, and close a persistent Lean process and its children."""
    _kill_process_group(proc.pid)
    proc.wait()
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def spawn_lake(
    lake: LakeEnv,
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start ``lake -d lake.root`` with binary pipes in a new process group."""
    tools = lake.tools
    environment = tools.environment()
    environment.update(extra_env or {})
    flags = ["--no-cache"] if lake.offline else []
    if lake.offline:
        environment["GIT_ALLOW_PROTOCOL"] = "file"
        environment["GIT_TERMINAL_PROMPT"] = "0"
    command = [str(tools.lake), "-d", str(lake.root), "--keep-toolchain", *flags, *args]
    try:
        return subprocess.Popen(
            command,
            cwd=cwd or lake.root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise LeanError(f"Could not start {command[0]}: {exc}") from exc


def run_lake(
    lake: LakeEnv,
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    extra_env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run Lake with full metadata output, reaping its process tree on failure."""
    proc = spawn_lake(lake, args, cwd=cwd, extra_env=extra_env)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except BaseException as exc:
        _terminate_process(proc)
        if isinstance(exc, subprocess.TimeoutExpired):
            raise LeanTimeoutError(f"Lean command timed out after {timeout}s: {' '.join(args)}") from exc
        raise
    finally:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
    result = subprocess.CompletedProcess(
        proc.args, proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    )
    if check and result.returncode:
        raise LeanError(f"Lean command failed ({' '.join(args)}):\n{result.stdout}{result.stderr}")
    return result


def _completed(
    command: Sequence[str], returncode: int, stdout: bytes, stderr: bytes, check: bool
) -> subprocess.CompletedProcess[str]:
    result = subprocess.CompletedProcess(
        command,
        returncode,
        stdout.decode("utf-8", errors="replace")[-_MAX_DIAGNOSTICS:],
        stderr.decode("utf-8", errors="replace")[-_MAX_DIAGNOSTICS:],
    )
    if check and returncode:
        raise LeanSetupError(f"Command failed: {' '.join(command)}\n{result.stdout}{result.stderr}")
    return result


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a process, killing and reaping its whole group on timeout/interruption.

    ``environment`` replaces the inherited environment when supplied. Nonzero
    exit codes raise ``LeanSetupError`` unless ``check=False``. Returned output
    retains the last 32,768 characters of each stream. Timeouts raise ``LeanTimeoutError``.
    """
    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise LeanSetupError(f"Could not run {command[0]}: {exc}") from exc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except BaseException as exc:
        _kill_process_group(proc.pid)
        proc.wait()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise LeanTimeoutError(f"Command timed out after {timeout}s: {' '.join(command)}") from exc
        raise
    finally:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
    return _completed(command, proc.returncode, stdout, stderr, check)


async def run_command_async(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run with the same policy as ``run_command`` and reap children on cancellation."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise LeanSetupError(f"Could not run {command[0]}: {exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException as exc:
        _kill_process_group(proc.pid)
        await proc.wait()
        if isinstance(exc, TimeoutError):
            raise LeanTimeoutError(f"Command timed out after {timeout}s: {' '.join(command)}") from exc
        raise
    return _completed(command, proc.returncode or 0, stdout, stderr, check)


async def run_in_thread[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Drain blocking work on cancellation before the caller releases its locks."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                pass
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
