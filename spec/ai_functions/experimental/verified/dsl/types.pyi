"""The structural Lean type model, shared by the DSL and the Lean substrate.

The DSL's annotation table lives with the DSL and the native ABI's with
``verified.compile``; both build ``LeanType`` values from this module.
"""

from dataclasses import dataclass

@dataclass(frozen=True)
class Const:
    """A named Lean type applied to zero or more type arguments, e.g. ``List Nat``.
    """

    name: str
    args: tuple[LeanType, ...] = ()

@dataclass(frozen=True)
class Prod:
    """A product type in canonical form.

    ``A × (B × C)`` and ``A × B × C`` are the same ``Prod((A, B, C))``, which
    corresponds to ``tuple[a, b, c]``; ``(A × B) × C`` keeps its nested first part.

    Invariants:
        ``parts[-1]`` is never a ``Prod``.
    """

    parts: tuple[LeanType, ...]

    def __post_init__(self) -> None:
        """Flatten a right-nested last part into this product.

        Raises:
            ValueError: Fewer than two parts.
        """
        ...

@dataclass(frozen=True)
class Arrow:
    """A nondependent function type ``domain → codomain``.
    """

    domain: LeanType
    codomain: LeanType

@dataclass(frozen=True)
class Unsupported:
    """A Lean type outside the structural model, kept with Lean's own spelling.
    """

    type_str: str

LeanType = Const | Prod | Arrow | Unsupported

INT: Const
NAT: Const
BOOL: Const
STRING: Const
PROP: Const
UNIT: Const

def render_type(lean_type: LeanType) -> str:
    """Render a type as Lean source, e.g. ``List (Nat × Int)``; ``Unsupported`` as Lean printed it."""
    ...
