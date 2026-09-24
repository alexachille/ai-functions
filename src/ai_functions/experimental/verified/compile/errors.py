"""Failures of ``verified.ai_compile``; each is an ``AIFunctionError``."""

from ....ai_thread.errors import AIFunctionError


class ContractError(AIFunctionError):
    """The function or its contract is outside what compilation supports."""


class CompilerError(AIFunctionError):
    """Lean, the checker, or the native build failed; retrying the model cannot help."""

    def __init__(self, message: str, *, diagnostics: str = "", function_name: str = "") -> None:
        super().__init__(message, function_name=function_name)
        self.diagnostics = diagnostics


class SynthesisError(AIFunctionError):
    """No implementation was accepted within the attempt budget; nothing was installed."""

    def __init__(self, function_name: str, diagnostics: list[str]) -> None:
        self.diagnostics = tuple(diagnostics)
        self.attempts = len(diagnostics)
        super().__init__(
            f"No verified implementation of {function_name!r} was accepted in {self.attempts} attempt(s).",
            function_name=function_name,
        )
