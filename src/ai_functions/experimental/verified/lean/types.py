"""The value boundary: decoding introspected types and encoding values as Lean literals."""

from __future__ import annotations

from ..dsl.types import BOOL, INT, NAT, STRING, Arrow, Const, LeanType, Prod, Unsupported, render_type


class EncodingError(TypeError):
    """A value cannot be represented at its declared Lean type."""


# The scalars that cross the certified data boundary.
_BOUNDARY = (INT, NAT, BOOL, STRING)


def type_from_json(data: object, type_str: str = "?") -> LeanType:
    """Decode Lean's introspected type tree; types outside the model are ``Unsupported(type_str)``."""

    def tree(node: object) -> LeanType | None:
        match node:
            case {"k": "arrow", "d": domain, "c": codomain}:
                d, c = tree(domain), tree(codomain)
                return Arrow(d, c) if d is not None and c is not None else None
            case {"k": "prod", "l": left, "r": right}:
                left_type, right_type = tree(left), tree(right)
                return Prod((left_type, right_type)) if left_type is not None and right_type is not None else None
            case {"k": "const", "n": str(name)}:
                args = tuple(tree(arg) for arg in node.get("a", ()))
                return Const(name, args) if all(arg is not None for arg in args) else None
        return None

    return tree(data) or Unsupported(type_str)


def is_boundary(lean_type: LeanType) -> bool:
    """Whether values of this type can cross the certified data boundary."""
    match lean_type:
        case Const("List", (element,)):
            return is_boundary(element)
        case Prod(parts):
            return all(is_boundary(part) for part in parts)
    return lean_type in _BOUNDARY


class RawLean:
    """An application-authored Lean term, passed through without literal encoding."""

    def __init__(self, source: str) -> None:
        self._source = source

    def __str__(self) -> str:
        """Return the literal source."""
        return self._source


def _string(value: str) -> str:
    # Lean accepts multiline strings; escape controls using its syntax.
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t").replace("\0", "\\x00")
    return f'"{escaped}"'


def _mismatch(value: object, lean_type: LeanType, slot: str | None) -> EncodingError:
    where = f" for {slot}" if slot else ""
    return EncodingError(f"expected {render_type(lean_type)}{where}, got {value!r}")


def encode(value: object, lean_type: LeanType, *, slot: str | None = None) -> str:
    """Encode a Python value as a Lean literal at its declared type; ``slot`` names it in errors."""
    if isinstance(value, RawLean):
        return str(value)
    match lean_type:
        case Const("Nat", ()) if type(value) is int and value >= 0:
            return str(value)
        case Const("Int", ()) if type(value) is int:
            return str(value) if value >= 0 else f"({value})"
        case Const("Bool", ()) if type(value) is bool:
            return "true" if value else "false"
        case Const("String", ()) if isinstance(value, str):
            return _string(value)
        case Const("List", (element,)) if isinstance(value, list):
            return "[" + ", ".join(encode(item, element, slot=slot) for item in value) + "]"
        case Prod(parts) if isinstance(value, (tuple, list)) and len(value) == len(parts):
            return "(" + ", ".join(encode(v, t, slot=slot) for v, t in zip(value, parts, strict=True)) + ")"
    raise _mismatch(value, lean_type, slot)


def decode(data: object, lean_type: LeanType, *, slot: str | None = None) -> object:
    """Decode Lean's JSON for a closed value (products arrive as nested pairs)."""
    match lean_type:
        case Const("List", (element,)) if isinstance(data, list):
            return [decode(item, element, slot=slot) for item in data]
        case Prod(parts):
            values, node = [], data
            for part in parts[:-1]:
                if not (isinstance(node, list) and len(node) == 2):
                    raise _mismatch(data, lean_type, slot)
                values.append(decode(node[0], part, slot=slot))
                node = node[1]
            values.append(decode(node, parts[-1], slot=slot))
            return tuple(values)
    if lean_type in _BOUNDARY:
        encode(data, lean_type, slot=slot)
        return data
    raise _mismatch(data, lean_type, slot)
