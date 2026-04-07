"""Utility helpers for memory schema introspection."""

from typing import Any

from pydantic import BaseModel
from pydantic.fields import FieldInfo


def is_list_field(field_info: FieldInfo) -> bool:
    """Return True if the field annotation is list[str]."""
    ann = field_info.annotation
    origin = getattr(ann, "__origin__", None)
    return origin is list


def flatten_schema(schema: type[BaseModel], prefix: str = "") -> list[tuple[str, FieldInfo]]:
    """Yield (slash_separated_path, FieldInfo) for every leaf field in the schema."""
    leaves: list[tuple[str, FieldInfo]] = []
    for name, field_info in schema.model_fields.items():
        path = f"{prefix}/{name}" if prefix else name
        annotation = field_info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            leaves.extend(flatten_schema(annotation, path))
        else:
            leaves.append((path, field_info))
    return leaves


def unflatten_fields(flat: dict[str, Any]) -> dict[str, Any]:
    """Turn {'a/b': 1, 'a/c': 2, 'd': 3} into {'a': {'b': 1, 'c': 2}, 'd': 3}."""
    result: dict[str, Any] = {}
    for key, value in flat.items():
        parts = key.split("/")
        node = result
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return result


def get_nested_attr(obj: Any, path: str) -> Any:
    """Traverse slash-separated path on a pydantic model."""
    for part in path.split("/"):
        obj = getattr(obj, part)
    return obj


def set_nested_attr(obj: Any, path: str, value: Any) -> None:
    """Set a value at a slash-separated path on a pydantic model."""
    parts = path.split("/")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)
