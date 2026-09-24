"""The structural Lean type model shared by the DSL and the Lean substrate."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Const:
    """A named Lean type, optionally applied to type arguments."""

    name: str
    args: tuple[LeanType, ...] = ()


@dataclass(frozen=True)
class Prod:
    """A product type in canonical form: a right-nested product is one flat ``Prod``.

    ``A × (B × C)`` and ``A × B × C`` are the same Lean type, so the last part is
    never itself a ``Prod``. ``(A × B) × C`` keeps its nested first part.
    """

    parts: tuple[LeanType, ...]

    def __post_init__(self) -> None:
        """Flatten a right-nested last part into this product."""
        parts = tuple(self.parts)
        if len(parts) < 2:
            raise ValueError("A Lean product has at least two parts")
        if isinstance(parts[-1], Prod):
            parts = (*parts[:-1], *parts[-1].parts)
        object.__setattr__(self, "parts", parts)


@dataclass(frozen=True)
class Arrow:
    """A nondependent function type."""

    domain: LeanType
    codomain: LeanType


@dataclass(frozen=True)
class Unsupported:
    """A Lean type outside the structural model, kept with Lean's own spelling."""

    type_str: str


LeanType = Const | Prod | Arrow | Unsupported


INT = Const("Int")
NAT = Const("Nat")
BOOL = Const("Bool")
STRING = Const("String")
PROP = Const("Prop")
UNIT = Const("Unit")


def render_type(lean_type: LeanType) -> str:
    """Render a type as Lean source, e.g. ``List (Nat × Int)``."""

    def nested(ty: LeanType) -> str:
        text = render_type(ty)
        return text if isinstance(ty, Const) and not ty.args else f"({text})"

    match lean_type:
        case Const(name, args):
            return " ".join([name, *(nested(arg) for arg in args)])
        case Prod(parts):
            return " × ".join(nested(part) for part in parts)
        case Arrow(domain, codomain):
            return f"{nested(domain)} → {render_type(codomain)}"
        case Unsupported(type_str):
            return type_str
