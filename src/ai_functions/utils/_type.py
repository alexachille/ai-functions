"""Type utility functions for AI Functions.

This module provides helper functions for type introspection and validation.
"""

import inspect
import typing
from typing import Any, get_args, get_origin

from pydantic import BaseModel, TypeAdapter


def is_pydantic_model(type_: type) -> bool:
    """Check if a type is a Pydantic model.

    Args:
        type_: The type to check

    Returns:
        True if the type is a Pydantic BaseModel subclass
    """
    return isinstance(type_, type) and issubclass(type_, BaseModel)


def is_json_serializable_type(type_: type) -> bool:
    """Check if a type can be serialized to/from JSON using Pydantic's TypeAdapter.

    Args:
        type_: The type to check

    Returns:
        True if the type is JSON-serializable, False otherwise
    """
    # Pydantic models are always JSON-serializable
    if is_pydantic_model(type_):
        return True

    # Use Pydantic's TypeAdapter as the authoritative check
    try:
        adapter: TypeAdapter[type] = TypeAdapter(type_)
        adapter.json_schema(mode="serialization")
        return True
    except Exception:
        return False


def _simplify_annotation(annotation: type | None) -> type:
    """Replace Pydantic model types with ``dict`` in a type annotation.

    This is applied recursively so that generic aliases like
    ``list[MyModel]`` become ``list[dict]``.
    """
    if annotation is None:
        return type(None)
    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        new_args = tuple(_simplify_annotation(a) for a in args)
        # typing.Optional / Union need special reconstruction
        if origin is typing.Union:
            return typing.Union[new_args]  # type: ignore[no-any-return] #noqa: UP007
        try:
            return origin[new_args]  # type: ignore[no-any-return]
        except TypeError:
            return annotation
    if is_pydantic_model(annotation):
        return dict
    return annotation


def generate_signature_from_model(model: type[BaseModel], func_name: str = "final_answer") -> str:
    """Generate function signature corresponding to the constructor of a pydantic model."""
    # Create parameter list
    params = []
    for field_name, field_info in model.model_fields.items():
        annotation = _simplify_annotation(field_info.annotation)
        if field_info.is_required():
            params.append(inspect.Parameter(field_name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation))
        else:
            params.append(
                inspect.Parameter(
                    field_name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=field_info.default,
                    annotation=annotation,
                )
            )

    # Sort so that required (no default) params come before optional ones.
    # This avoids "non-default argument follows default argument" errors.
    params.sort(key=lambda p: p.default is not inspect.Parameter.empty)

    # Create signature
    sig = inspect.Signature(params)
    return f"{func_name}{sig}"


def add_parameter_to_signature(
    func: Any,
    name: str,
    *,
    kind: Any = inspect.Parameter.KEYWORD_ONLY,
    default: Any = inspect.Parameter.empty,
    annotation: Any = inspect.Parameter.empty,
) -> Any:
    """Add a parameter to a function's visible signature.

    Mutates func.__signature__ in place and returns func for convenience.
    """
    sig = inspect.signature(func)
    new_param = inspect.Parameter(
        name,
        kind=kind,
        default=default,
        annotation=annotation,
    )

    params = list(sig.parameters.values())

    # Insert based on kind: put it after existing params of the same or
    # lower kind, but before VAR_KEYWORD (**kwargs).
    non_var_kw = [p for p in params if p.kind != inspect.Parameter.VAR_KEYWORD]
    var_kw = [p for p in params if p.kind == inspect.Parameter.VAR_KEYWORD]

    func.__signature__ = sig.replace(parameters=non_var_kw + [new_param] + var_kw)
    return func
