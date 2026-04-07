"""Console utilities for AI Functions."""

import contextvars
import io
import os
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# When True, console output is suppressed regardless of the env variable.
# This is parallelism-safe (each async task / thread gets its own value).
_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("console_quiet", default=False)

# Shared stdout console singleton.  Using a single instance ensures that
# Rich's live-rendering protocol (used by ``console.status()``) can
# coordinate with regular ``console.print()`` calls — it automatically
# pauses the spinner, prints the content, and restores the spinner.
_stdout_console: Console | None = None


def get_console() -> Console:
    """Return the shared stdout :class:`Console` singleton.

    The singleton is created on first call.  All code that needs to write
    visible output should go through this console so that live displays
    (spinners, progress bars, etc.) are not corrupted.
    """
    global _stdout_console
    if _stdout_console is None:
        _stdout_console = Console(markup=False)
    return _stdout_console


@contextmanager
def quiet_console(suppress: bool = True) -> Iterator[None]:
    """Context manager that suppresses console output for the current execution context.

    Args:
        suppress: Whether to actually suppress output. When False, the context
            manager is a no-op. Defaults to True.

    Safe to use concurrently — each thread/async task has its own value.
    """
    if not suppress:
        yield
        return
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


def create() -> Console:
    """Return a console instance for the current context.

    If STRANDS_TOOL_CONSOLE_MODE environment variable is set to "enabled"
    and quiet mode is not active, returns the shared stdout singleton so
    that output coordinates with any active live displays (e.g. spinners).

    Returns:
        Console instance.
    """
    if _quiet.get() or os.getenv("STRANDS_TOOL_CONSOLE_MODE") != "enabled":
        return Console(file=io.StringIO())
    return get_console()


def print_in_box(text: str, *, title: str = "", console: Console | None = None) -> None:
    """Print text inside a rich panel (bounding box).

    Args:
        text: The text to display.
        title: Optional panel title.
        console: Console instance to use. Creates one via ``create()`` if not provided.
    """
    if console is None:
        console = create()
    console.print(Panel(Text(text), title=title, expand=False))
