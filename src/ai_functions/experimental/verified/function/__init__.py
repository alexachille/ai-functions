"""``verified.ai_function``: AI Functions whose every result carries a cold-checked Lean proof."""

from ._decorator import VerifiedFunction, ai_function
from .errors import ContractNotProved
from .tools import ObservationTool, tool
from .types import Certificate, Certified, LeanTerm, Observation

__all__ = [
    "Certificate",
    "Certified",
    "ContractNotProved",
    "LeanTerm",
    "Observation",
    "ObservationTool",
    "VerifiedFunction",
    "ai_function",
    "tool",
]
