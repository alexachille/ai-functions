"""``verified.ai_function``: an AI function that proves a Lean contract for every result.

The mode follows the AI function's return annotation: ``-> LeanTerm`` is term mode;
anything else is value mode, decoded into the Python result. The model gets these
tools, run sequentially on the call's ledger: ``lean(code)``, ``lean_eval(expr)``,
``lean_judge(symbol, args, value, justification)`` when judgments are allowed,
``lean_submit(answer, proof)``, and the observation tools.
"""

from collections.abc import Callable, Sequence
from typing import Unpack

from strands.tools import ToolProvider
from strands.types.tools import AgentTool

from ....ai_thread import AIFunction
from ....ai_thread.config import ThreadMergedKwargs
from ..dsl import Definition
from ..lean import LeanProject, LeanSymbol
from .tools import ObservationTool
from .types import Certificate

class VerifiedFunction[**P, T]:
    """An AI function whose every result carries an accepted certificate.

    Invariants:
        Calls share the project but never a ledger.
    """

    def __init__(
        self,
        func: AIFunction[P, T],
        *,
        contract: LeanSymbol | Definition,
        tools: Sequence[ObservationTool] = (),
        judgments: Sequence[LeanSymbol] = (),
        timeout: float = 120.0,
    ) -> None:
        """Store the configuration; runs no Lean.

        Args:
            func: The AI function to verify.
            contract: A result-first ``Prop``; a ``Definition`` stands for its symbol.
            tools: Observation tools.
            judgments: Opaque symbols the model may judge.
            timeout: Seconds per Lean request and per cold check.

        Raises:
            ValueError: ``timeout`` is not positive.
            LeanError: The symbols belong to different projects.
        """
        ...

    @property
    def name(self) -> str:
        """The AI function's name."""
        ...

    @property
    def contract(self) -> LeanSymbol:
        """The contract symbol."""
        ...

    @property
    def project(self) -> LeanProject:
        """The contract's project."""
        ...

    @property
    def with_certificate(self) -> VerifiedFunction[P, tuple[T, Certificate]]:
        """The same function, whose calls return ``(result, certificate)`` for that call."""
        ...

    def replace(self, **kwargs: Unpack[ThreadMergedKwargs]) -> VerifiedFunction[P, T]:
        """Return a copy with ``kwargs`` replaced in the AI function's configuration."""
        ...

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        """Run the AI function and return its certified result.

        Ensures:
            - The project is prepared and every symbol validated before any model
              call.
            - The model sees the ledger, the goal, and the source of the project
              modules the contract and tools reference.
            - The returned value equals the session's certified result.
            - The call's session is closed on every exit path.

        Raises:
            ContractNotProved: No accepted certificate proves the result.
            LeanError: The harness, a tool's interface, or Lean failed.

        Concurrency:
            Concurrent calls each get their own session.
        """
        ...

    def run_sync(self, *args: P.args, **kwargs: P.kwargs) -> T:
        """Run ``__call__`` from synchronous code."""
        ...

def ai_function[**P, T](
    *,
    contract: LeanSymbol | Definition,
    tools: Sequence[ObservationTool | AgentTool | ToolProvider | str] = (),
    judgments: Sequence[LeanSymbol] = (),
    timeout: float = 120.0,
    **config: Unpack[ThreadMergedKwargs],
) -> Callable[[Callable[P, T]], VerifiedFunction[P, T]]:
    """Build an AI function that must prove a result-first contract for every result.

    The output type is the prompt function's return annotation, as for a bare
    ``@ai_functions.ai_function``. The verification arguments are those of
    ``VerifiedFunction``.

    Args:
        tools: Observation tools (``@tool``), bound to each call's proof session,
            and ordinary tools, passed to the AI function. Only observations become
            Lean facts; the prompt tells the model that other tool output is not
            evidence.
        config: The AI function's configuration, as ``ai_functions.ai_function``
            takes it.

    Ensures:
        Every attempt that ends without a certified answer (a rejected submission,
        stopping without one, or output-token exhaustion) counts against the AI
        function's ``max_attempts``; past it, the call raises ``ContractNotProved``.
    """
    ...
