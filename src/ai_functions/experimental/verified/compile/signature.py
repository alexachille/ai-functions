"""The native ABI: which Python signatures compile, and their Lean types."""

import inspect
import typing
from dataclasses import dataclass

from ..dsl.types import BOOL, INT, Const, LeanType, render_type
from ..lean import LeanError, LeanSymbol
from .errors import ContractError

# Annotation: (Lean type, the bridge's kind code, C type, `av_value` member).
ABI: dict[object, tuple[LeanType, int, str, str]] = {
    int: (INT, 1, "lean_object *", "object"),
    bool: (BOOL, 2, "uint8_t", "boolean"),
    float: (Const("Float"), 3, "double", "floating"),
    list[int]: (Const("List", (INT,)), 4, "lean_object *", "object"),
}


@dataclass(frozen=True)
class Signature:
    """A Python function's native signature: the parameters' annotations, then the result's."""

    name: str
    python: inspect.Signature
    annotations: tuple[object, ...]

    @property
    def types(self) -> list[LeanType]:
        """The Lean types, parameters first."""
        return [ABI[a][0] for a in self.annotations]

    @property
    def arity(self) -> int:
        """The number of parameters."""
        return len(self.annotations) - 1

    def check(self, contract: LeanSymbol) -> None:
        """Require the contract's type to be ``R → A → B → Prop`` for ``def f(a: A, b: B) -> R``."""
        *parameters, result = self.types
        shape = " → ".join(render_type(t) for t in [result, *parameters, Const("Prop")])
        try:
            info = contract.info
        except LeanError as exc:
            raise ContractError(str(exc), function_name=self.name) from exc
        if not (info.simple and info.prop and all(p.explicit for p in info.parameters)) or [
            p.type for p in info.parameters
        ] != [result, *parameters]:
            raise ContractError(
                f"The contract {contract.name} : {info.type_str} must have type {shape}", function_name=self.name
            )

    def bind(self, args: tuple[object, ...], kwargs: dict[str, object]) -> dict[str, object]:
        """The bridge's arguments ``v0, v1, …``, type-checked exactly, lists copied."""
        bound = self.python.bind(*args, **kwargs)
        bound.apply_defaults()
        values = {}
        for i, (name, value) in enumerate(bound.arguments.items()):
            annotation = self.annotations[i]
            if annotation == list[int]:
                if type(value) is not list or any(type(item) is not int for item in value):
                    raise TypeError(f"{self.name}(): {name!r} must be a list of int values")
                value = list(value)
            elif type(value) is not annotation:
                raise TypeError(f"{self.name}(): {name!r} must be {annotation.__name__}")  # type: ignore[attr-defined]
            values[f"v{i}"] = value
        return values


def signature(fn: typing.Callable[..., object]) -> Signature:
    """Read the native signature of a synchronous function."""
    name = getattr(fn, "__name__", repr(fn))
    if not inspect.isfunction(fn) or inspect.iscoroutinefunction(fn) or inspect.isgeneratorfunction(fn):
        raise ContractError("verified.ai_compile requires a synchronous, non-generator function", function_name=name)
    hints = typing.get_type_hints(fn)
    python = inspect.signature(fn)
    annotations = []
    for slot, parameter in [*python.parameters.items(), ("return", None)]:
        annotation = hints.get(slot)
        if parameter is not None and parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise ContractError("Native signatures need explicit parameters", function_name=name)
        if annotation not in ABI:
            raise ContractError(
                f"Native signatures use int, bool, float and list[int]; {slot} is {annotation!r}", function_name=name
            )
        annotations.append(annotation)
    return Signature(name, python, tuple(annotations))
