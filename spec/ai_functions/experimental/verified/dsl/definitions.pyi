"""The Python DSL: decorated Python functions that are also Lean declarations.

Definitions are made only by a project's decorators, which translate the function to
finished Lean text when they run. This package imports no backend: a definition
names its declaration through any ``Symbol``. Supported subset:

- Types: ``int`` (Lean ``Int``, never ``Nat``), ``bool``, ``list[int]``. Every
  parameter and the result are annotated; this annotation table is private to the
  DSL.
- Expressions (``@project.function``, one ``return``): ``+ - *``, ``//`` and ``%``
  (floored, as in Python), comparisons and chains, ``and``/``or``/``not``,
  conditional expressions, list literals, ``len``, ``sum``, ``range``, ``all``/
  ``any`` over one generator, ``implies``, and calls of earlier definitions of the
  same project.
- Statements (``@project.proposition``): ``assert`` (conjunction), top-level ``assume``
  (implication), local assignment, ``if``/``else``, ``for`` (bounded ``∀``) and
  ``pass``. ``every(T)`` is an unbounded quantifier domain, valid only here.
"""

import inspect
from collections.abc import Callable
from typing import Any, NoReturn, Protocol

from .types import LeanType

class Symbol(Protocol):
    """A backend's handle on a declaration, such as a ``LeanSymbol``."""

    @property
    def name(self) -> str:
        """The fully qualified name."""
        ...

    @property
    def project(self) -> Any:
        """The owning project; definitions of different projects never mix."""
        ...

def implies(left: object, right: object) -> bool:
    """Return ``not left or bool(right)``; in a DSL body, Lean ``→``."""
    ...

def assume(condition: object) -> None:
    """Introduce a hypothesis in a proposition block.

    Ensures:
        A false ``condition`` makes the calling proposition evaluate to ``True``.
    """
    ...

def every(annotation: object) -> NoReturn:
    """Denote the unbounded quantifier domain of ``annotation`` in a proposition.

    Raises:
        NotComputable: Always, when evaluated in Python.
    """
    ...

class Definition:
    """A registered DSL definition, callable in Python and named in Lean.

    Returned by ``project.function``, ``project.proposition`` and ``project.external``; never
    constructed directly. Consumer decorators accept it wherever they accept its
    ``symbol``. Its Lean name is ``symbol.name``.

    Attributes:
        symbol: The registered declaration, or the adapted one for ``external``.
        source: The Lean text appended to the prelude; empty for an adapter.
        type: The Lean type of ``symbol``.
        parameters: Python parameter names with their Lean types, in order.

    Immutable:
        Yes.

    Invariants:
        ``source`` is fixed when the decorator runs; rebinding a global later does
        not change it.
    """

    symbol: Symbol
    source: str
    type: LeanType
    parameters: tuple[tuple[str, LeanType], ...]
    signature: inspect.Signature
    fn: Callable[..., Any]
    __name__: str
    __qualname__: str

    @property
    def project(self) -> Any:
        """The project this definition is registered in."""
        ...

    def __call__(self, *args: object, **kwargs: object) -> Any:
        """Evaluate the original Python function with its DSL meaning.

        Ensures:
            - A proposition returns ``False`` when an ``assert`` fails.
            - A proposition returns ``True`` when an ``assume`` fails or control
              falls off its end.

        Raises:
            NotComputable: The definition has no Python evaluation (an ``every``
                domain, or an adapter declared with ``runtime=False``).
        """
        ...
