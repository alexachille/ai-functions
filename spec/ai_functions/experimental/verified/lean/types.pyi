"""The certified data boundary: introspected types and values as Lean literals.

This module owns ``is_boundary``, ``encode`` and ``decode`` over the type model of
``verified.dsl.types``. Values return from Lean as closed-term JSON and are decoded
at their declared Lean type; pretty-printed Lean is never parsed.
"""

from dataclasses import dataclass

from ..dsl.types import LeanType

class EncodingError(TypeError):
    """A Python value does not fit its slot's Lean type, or the type has no encoding."""

def type_from_json(data: object, type_str: str = "?") -> LeanType:
    """Decode the type tree reported by Lean introspection.

    Ensures:
        - Right-nested products come out flat.
        - Anything outside the model is ``Unsupported(type_str)``.
    """
    ...

def is_boundary(lean_type: LeanType) -> bool:
    """Return whether values of ``lean_type`` can cross the certified data boundary.

    Boundary types are ``Int``, ``Nat``, ``Bool``, ``String``, and lists and
    products of boundary types.
    """
    ...

def encode(value: object, lean_type: LeanType, *, slot: str | None = None) -> str:
    """Encode a Python value as a Lean literal at its declared type.

    Args:
        value: The value; a ``RawLean`` passes through unchanged.
        lean_type: The declared type of the slot.
        slot: Names the slot in the error, e.g. ``"parameter x"``.

    Ensures:
        ``decode`` of the literal's reduced JSON at ``lean_type`` equals ``value``.

    Raises:
        EncodingError: ``value`` does not fit ``lean_type``, e.g. a negative ``Nat``.
    """
    ...

def decode(data: object, lean_type: LeanType, *, slot: str | None = None) -> object:
    """Decode Lean's closed-term JSON to a Python value at ``lean_type``.

    Args:
        data: JSON as the server reports it; products arrive as nested pairs.
        lean_type: The declared type of the value.
        slot: Names the slot in the error.

    Returns:
        ``int``, ``bool``, ``str``, or lists and tuples of them.

    Raises:
        EncodingError: ``data`` is not a value of ``lean_type``.
    """
    ...

@dataclass(frozen=True)
class RawLean:
    """Lean source standing in a slot that would otherwise be literal-encoded."""

    source: str

    def __str__(self) -> str:
        """Return the source unchanged."""
        ...
