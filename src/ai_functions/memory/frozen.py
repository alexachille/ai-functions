"""Marker for memory fields that should not receive gradients by default."""

from typing import Annotated, TypeVar

T = TypeVar("T")


class FrozenMarker:
    """Tag that identifies Frozen-typed fields (requires_grad=False by default)."""


Frozen = Annotated[T, FrozenMarker()]
"""Type annotation to mark memory fields as frozen (no gradient updates by default).

A frozen parameter is still recalled and used as input, but the optimizer
will not propagate feedback into it unless ``requires_grad=True`` is
passed explicitly at the call site.

Usage::

    class Schema(BaseModel):
        system_prompt: Frozen[str] = Field("You are helpful", description="...")
"""
