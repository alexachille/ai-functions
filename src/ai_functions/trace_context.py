"""Trace context for tracking AI function execution graphs."""

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Self

# Stores the current trace context (used during graph construction to automatically register new graph nodes)


class TraceContext:
    """Hierarchical execution trace node.

    Tracks inputs and tool results observed during a single AIFunction invocation,
    forming a parent/child chain that mirrors the call stack.
    """

    def __init__(self, name: str, parent: Self | None = None) -> None:
        """Initialize a trace context node.

        Args:
            name: Name of this trace scope.
            parent: Parent trace context, if any.
        """
        self.name = name
        self.parent = parent
        self.inputs: list[Any] = []
        self.tool_results: list[Any] = []

    def __repr__(self) -> str:  # noqa: D105
        chain = []
        node: TraceContext | None = self
        while node:
            chain.append(node.name)
            node = node.parent
        s = " → ".join(reversed(chain))
        s += f"(inputs={self.inputs}, tool_results={self.tool_results})"
        return s


_trace_ctx: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "trace_ctx",
    default=TraceContext("root"),  # noqa: B039
)


@contextmanager
def trace_scope(name: str = "sub") -> Iterator[TraceContext]:
    """Open a child trace scope, restoring the parent on exit."""
    parent = _trace_ctx.get()
    child = TraceContext(name, parent=parent)
    token = _trace_ctx.set(child)
    try:
        yield child
    finally:
        _trace_ctx.reset(token)


def get_context() -> TraceContext:
    """Return the current trace context."""
    ctx = _trace_ctx.get()
    assert ctx is not None, "Trace context is not initialized"
    return ctx
