"""Process execution, deadlines, and cancellation for Lean consumers.

Every child runs in its own process group, which is killed and reaped on timeout or
cancellation. Cache locks are ``filelock`` locks, one lock file per guarded
directory, shared by threads and processes.
"""

import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

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

class Deadline:
    """A monotonic deadline shared by the steps of one bounded operation."""

    def __init__(self, timeout: float | None, message: str) -> None:
        """Start the deadline now; ``timeout=None`` never expires."""
        ...

    def remaining(self, message: str | None = None) -> float:
        """Return the positive seconds left.

        Raises:
            LeanTimeoutError: No time is left; carries ``message`` or the default.
        """
        ...

@dataclass(frozen=True)
class LakeEnv:
    """Everything needed to run Lake for one build directory.

    Attributes:
        offline: Disable Lake's artifact cache and allow only local fetches.
    """

    tools: ResolvedToolchain
    root: Path
    offline: bool = False

def file_lock(path: Path, *, timeout: float | None = None) -> AbstractContextManager[None]:
    """Hold the ``filelock`` lock at ``path``, excluding other threads and processes.

    Raises:
        LeanTimeoutError: ``timeout`` expired while waiting.
    """
    ...

def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a process to completion.

    Args:
        environment: Replaces the inherited environment when given.
        check: Raise on a nonzero exit code.

    Ensures:
        Each returned stream keeps its last 32,768 characters.

    Raises:
        LeanSetupError: The process cannot start, or exits nonzero with ``check``.
        LeanTimeoutError: ``timeout`` expired; the process group was killed.
    """
    ...

async def run_command_async(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a process like ``run_command`` without blocking the event loop.

    Ensures:
        The process group is killed and reaped on cancellation.
    """
    ...

async def run_in_thread[**P, T](function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run blocking work in a thread.

    Ensures:
        On cancellation, the work finishes before ``CancelledError`` propagates,
        so the caller releases its locks only after the work stops.
    """
    ...

def spawn_lake(
    lake: LakeEnv,
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start ``lake -d lake.root`` with binary pipes in a new process group.

    Raises:
        LeanError: Lake cannot start.
    """
    ...

def run_lake(
    lake: LakeEnv,
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    extra_env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run Lake to completion.

    Raises:
        LeanError: Lake exits nonzero with ``check``.
        LeanTimeoutError: ``timeout`` expired; the process group was killed.
    """
    ...
