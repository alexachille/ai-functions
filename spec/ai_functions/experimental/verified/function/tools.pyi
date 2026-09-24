"""Observation tools: Python functions supplying values of opaque Lean symbols."""

from collections.abc import Callable
from dataclasses import dataclass

from strands.types.tools import AgentTool

from ..lean import LeanProject, LeanSymbol
from ._session import ProofSession

@dataclass(frozen=True)
class ObservationTool:
    """A Python function bound to an opaque symbol, exposed anew to each certified call.
    """

    fn: Callable[..., object]
    symbol: LeanSymbol
    stem: str
    name: str

    @property
    def project(self) -> LeanProject:
        """The symbol's project."""
        ...

    def bind(self, session: ProofSession) -> AgentTool:
        """Expose the tool to the model for one session.

        The model-visible tool takes one Lean expression string per parameter; its
        description is the function's docstring plus the symbol's Lean type.

        Ensures:
            - Each call evaluates the model's expressions at the symbol's argument
              types before the function runs.
            - A function exception is reported to the model and commits nothing.
            - Otherwise the result is committed with ``ProofSession.observe``.

        Raises:
            LeanError: The symbol is not opaque, a type is not a boundary type, or
                the arity differs from the function's.
        """
        ...

def tool(
    symbol: LeanSymbol,
    *,
    stem: str | None = None,
    name: str | None = None,
) -> Callable[[Callable[..., object]], ObservationTool]:
    """Declare a tool observing ``symbol``.

    The function may be sync or async and may return ``Certified(value,
    guarantees=...)``. Lean checks run when a certified call binds the tool.

    Args:
        symbol: An opaque symbol with boundary argument and result types.
        stem: Stem of the ledger names ``H.<stem><n>``; defaults to the function name.
        name: Model-visible tool name; defaults to the function name.
    """
    ...
