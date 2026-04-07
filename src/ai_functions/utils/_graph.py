from __future__ import annotations

from typing import Any

from ..types.graph import Node, ParameterView, Result


def collect_nodes(value: Any) -> list[Node]:
    """Recursively collect all Node instances from a value.

    Handles direct Node values, dicts (values only), and lists/tuples.
    """
    if isinstance(value, Node):
        return [value]

    if isinstance(value, dict):
        return [node for v in value.values() for node in collect_nodes(v)]

    if isinstance(value, (list, tuple)):
        return [node for item in value for node in collect_nodes(item)]

    return []


def unwrap_nodes(value: Any) -> Any:
    """Recursively replace Node instances with their ``.value``.

    Preserves the structure of dicts, lists, and tuples.
    """
    if isinstance(value, Node):
        return value.value

    if isinstance(value, dict):
        return {k: unwrap_nodes(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        unwrapped = [unwrap_nodes(item) for item in value]
        return type(value)(unwrapped)

    return value


def topological_sort(node: Node) -> list[Node]:
    """Return nodes in reverse topological order, excluding subtrees with no grad-enabled Parameter leaves."""
    visited: set[int] = set()
    order: list[Node] = []

    _has_grad_cache: dict[int, bool] = {}

    def _has_grad_parameter(n: Node) -> bool:
        """Check if this node is or leads to a Parameter with requires_grad=True."""
        nid = id(n)
        if nid in _has_grad_cache:
            return _has_grad_cache[nid]
        if isinstance(n, ParameterView):
            result = n.requires_grad
        elif isinstance(n, Result):
            result = any(_has_grad_parameter(c) for c in n.inputs + n.tool_results)
        else:
            result = False
        _has_grad_cache[nid] = result
        return result

    def _dfs(n: Node) -> None:
        nid = id(n)
        if nid in visited:
            return
        visited.add(nid)
        if isinstance(n, Result):
            for child in n.inputs:
                if _has_grad_parameter(child):
                    _dfs(child)
            for child in n.tool_results:
                if _has_grad_parameter(child):
                    _dfs(child)
        order.append(n)

    _dfs(node)
    return list(reversed(order))
