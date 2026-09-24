"""Lean-verified AI Functions.

``@ai_function(contract=...)`` builds an AI function whose every result carries a
cold-checked Lean proof of a result-first contract; ``@ai_compile(contract=...)``
implements a body-less Python function by synthesized native code proved to satisfy
one. Contracts are symbols of a ``LeanProject`` (``verified.lean``), written in Lean
or in the Python contract language of ``verified.dsl``. Both are Lean-based.

Dependencies go one way: ``dsl`` ← ``lean`` ← ``function`` ← ``compile``.
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
