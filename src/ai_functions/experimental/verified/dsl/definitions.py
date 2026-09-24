"""DSL runtime: ``Definition``, made by a project's decorators, and the DSL helpers."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, NoReturn, Protocol

from .errors import NotComputable
from .types import PROP, Arrow, LeanType


class Symbol(Protocol):
    """A backend's handle on a declaration: its full name and the project that owns it."""

    @property
    def name(self) -> str:
        """The fully qualified name."""
        ...

    @property
    def project(self) -> Any:
        """The owning project; definitions of different projects never mix."""
        ...


class _AssumptionFailed(Exception):
    """A false ``assume``: the enclosing proposition holds vacuously."""


def implies(left: object, right: object) -> bool:
    """Return ``not left or bool(right)``; in a DSL body, Lean ``→``."""
    return not left or bool(right)


def assume(condition: object) -> None:
    """Introduce a hypothesis; a false one makes the calling proposition ``True``."""
    if not condition:
        raise _AssumptionFailed


def every(annotation: object) -> NoReturn:
    """Denote the unbounded quantifier domain of ``annotation`` in a proposition."""
    raise NotComputable(f"Cannot enumerate every value of {annotation!r}")


class Definition:
    """A registered DSL definition, callable in Python and named in Lean by ``symbol``."""

    def __init__(
        self,
        fn: Callable[..., Any],
        symbol: Symbol,
        source: str,
        parameters: tuple[tuple[str, LeanType], ...],
        result: LeanType,
        *,
        runtime: bool = True,
    ) -> None:
        self.fn, self.symbol, self.source, self.parameters = fn, symbol, source, parameters
        self._result, self._runtime = result, runtime
        self.type = functools.reduce(lambda codomain, p: Arrow(p[1], codomain), reversed(parameters), result)
        self.signature = inspect.signature(fn)
        functools.update_wrapper(self, fn)

    @property
    def project(self) -> Any:
        """The project this definition is registered in."""
        return self.symbol.project

    def __call__(self, *args: object, **kwargs: object) -> Any:
        """Run the Python function; a proposition is ``False`` on a failed ``assert``, else ``True``."""
        if not self._runtime:
            raise NotComputable(f"{self.__qualname__} has no Python evaluation")
        if self._result != PROP:
            return self.fn(*args, **kwargs)
        try:
            result = self.fn(*args, **kwargs)
        except _AssumptionFailed:
            return True
        except AssertionError:
            return False
        return True if result is None else result
