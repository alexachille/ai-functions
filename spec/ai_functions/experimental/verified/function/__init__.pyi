"""``verified.ai_function``: AI Functions whose every result carries a cold-checked Lean proof.

``@verified.ai_function(contract=..., tools=[...], judgments=[...])`` builds the AI
function. Observation tools are ``@tool(symbol)`` functions supplying values of
opaque symbols; judgments let the model assert readings of allowlisted opaque
symbols. Both become audited axioms, listed in the certificate.
"""

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
