"""The Lean substrate of ``verified``: projects, their symbols, and the toolchain behind them.

A ``LeanProject`` is a Lake project's imports plus a prelude registered at setup
with ``project.add`` and the ``@project.function``, ``@project.proposition`` and
``@project.external`` decorators, which translate through ``verified.dsl``.
``project.symbols`` names any declaration. ``verified.ai_function`` and
``verified.ai_compile`` consume symbols and never register. The remaining modules
of this package are machinery for those consumers.
"""

from .errors import LeanError
from .project import LeanProject, LeanSymbol, Parameter, SymbolInfo

__all__ = ["LeanError", "LeanProject", "LeanSymbol", "Parameter", "SymbolInfo"]
