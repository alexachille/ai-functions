"""Failures of ``verified.ai_compile``.

Every failure is an ``AIFunctionError`` carrying the Python function's name, so
callers can catch the library's base error. Model-provider errors (credentials,
timeouts) are not wrapped; they propagate as the provider raised them.
"""

from ....ai_thread.errors import AIFunctionError

class ContractError(AIFunctionError):
    """The function or its contract is outside what compilation supports.

    Raised before any model call: the function is async or a generator, its
    signature leaves the native ABI (``int``, ``bool``, ``float``, ``list[int]``),
    or the contract's type is not ``R → A → B → Prop`` for that signature.
    """

class CompilerError(AIFunctionError):
    """Lean, the checker, or the native build failed; retrying the model cannot help.

    Attributes:
        diagnostics: Raw tool output (compiler, linker, checker), or ``""``.
    """

    diagnostics: str

    def __init__(self, message: str, *, diagnostics: str = "", function_name: str = "") -> None: ...

class SynthesisError(AIFunctionError):
    """No implementation was accepted within the attempt budget; nothing was installed.

    Attributes:
        diagnostics: Each failed final submission's diagnostics, in order.
        attempts: ``len(diagnostics)``.
    """

    diagnostics: tuple[str, ...]
    attempts: int

    def __init__(self, function_name: str, diagnostics: list[str]) -> None: ...
