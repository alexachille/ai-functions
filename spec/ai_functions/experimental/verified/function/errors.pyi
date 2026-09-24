"""Certification failures.

A ``verified.ai_function`` call returns a certified result, raises ``ContractNotProved`` when the
model produced no accepted proof, or raises ``LeanError`` when the harness, a tool's
interface or Lean failed.
"""

class ContractNotProved(AssertionError):
    """No accepted certificate proves the returned value.

    Attributes:
        diagnostics: Each failed final submission's diagnostics, in order, when the
            submission budget ran out; empty otherwise.
    """

    diagnostics: tuple[str, ...]
    def __init__(self, message: str, diagnostics: tuple[str, ...] = ()) -> None: ...
