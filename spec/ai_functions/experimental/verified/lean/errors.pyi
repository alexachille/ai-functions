"""Errors raised by the Lean substrate.

Every failure of the substrate is a ``LeanError``; subclasses exist only where a
caller reacts differently. Only ``LeanError`` is re-exported by the package.
"""

class LeanError(RuntimeError):
    """A Lean operation failed."""

class LeanSetupError(LeanError):
    """A toolchain or project could not be prepared."""

class LeanTimeoutError(LeanError):
    """A Lean operation exceeded its deadline."""

class LeanProjectStateError(LeanError):
    """Registration after preparation began, or a prepared value read before it."""
