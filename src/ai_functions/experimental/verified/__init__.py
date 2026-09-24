"""Lean-verified AI Functions: ``ai_function`` proves each result, ``ai_compile`` a native implementation.

Contracts are Lean propositions of a ``LeanProject`` (``ai_functions.experimental.verified.lean``),
written in Lean or in the Python contract language of ``verified.dsl``.
"""

from .compile import CompilerError, ContractError, SynthesisError, ai_compile
from .function import Certificate, ContractNotProved, LeanTerm, ai_function, tool

__all__ = [
    "Certificate",
    "CompilerError",
    "ContractError",
    "ContractNotProved",
    "LeanTerm",
    "SynthesisError",
    "ai_compile",
    "ai_function",
    "tool",
]
