"""Reconstruct a single ``ThreadNode`` from one thread's event log.

The optimization graph is a pure function of the event log + the live memory
backends, built after a run. ``build_graph`` rebuilds one thread's node;
cross-thread structure (one thread's output feeding another) is wired by the
caller, since that Python-level dataflow is recorded in no event log.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..ai_thread.reconstruction import reconstruct_messages
from ..types.events import (
    MessageAssistantCompleteEvent,
    ParameterRecalledEvent,
    ThreadSpawnedEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from ..types.graph import ParameterNode, Result, ThreadNode, ToolCallNode
from ._formatting import unique_name

if TYPE_CHECKING:
    from ..memory.base import MemoryBackend
    from ..protocols import Coordinator
    from ..types.events import Event
    from ..types.ids import ThreadId

logger = logging.getLogger(__name__)


def topological_sort(node: ThreadNode) -> list[ThreadNode]:
    """Return ThreadNodes in reverse topological order, pruning grad-free subtrees."""
    visited: set[int] = set()
    order: list[ThreadNode] = []
    _has_grad_cache: dict[int, bool] = {}

    def _has_grad_parameter(n: ThreadNode) -> bool:
        nid = id(n)
        if nid in _has_grad_cache:
            return _has_grad_cache[nid]
        result = any(p.requires_grad for p in n.parameters) or any(_has_grad_parameter(c) for c in n.child_threads)
        _has_grad_cache[nid] = result
        return result

    def _dfs(n: ThreadNode) -> None:
        nid = id(n)
        if nid in visited:
            return
        visited.add(nid)
        for child in n.child_threads:
            if _has_grad_parameter(child):
                _dfs(child)
        order.append(n)

    _dfs(node)
    return list(reversed(order))


def _assistant_text(content: list[Any]) -> str:  # pyright: ignore[reportExplicitAny]
    """Concatenate the text blocks of an assistant turn's content."""
    return "".join(block["text"] for block in content if isinstance(block, dict) and "text" in block)


def _reconstruct_node(events: list[Event], backends: list[MemoryBackend]) -> ThreadNode:
    """Reconstruct one thread's computation node from its event log.

    Builds exactly one ``ThreadNode`` from a single thread's pre-fetched events.
    Cross-thread edges (``child_threads``) are wired by the caller (or by
    :func:`build_graph`, which recurses spawned children).

    Args:
        events: One thread's events in append order (oldest first).
        backends: Live memory backends, matched by ``backend_id``.

    Returns:
        A single childless ``ThreadNode``.
    """
    backend_map = {b.backend_id: b for b in backends}

    thread_id = ""
    func_name: str | None = None
    for evt in events:
        tid = getattr(evt, "thread_id", None)
        if tid:
            thread_id = str(tid)
        name = getattr(evt, "thread_name", None)
        if name and func_name is None:
            func_name = str(name)

    # Parameters: one ParameterNode per (backend_id, name); last recall wins.
    param_nodes: dict[tuple[str, str], ParameterNode] = {}
    tool_calls: list[ToolCallNode] = []
    tc_map: dict[str, ToolCallNode] = {}
    result_value: Any = None  # pyright: ignore[reportExplicitAny]

    for evt in events:
        if isinstance(evt, ParameterRecalledEvent):
            backend = backend_map.get(evt.backend_id)
            if backend is None:
                logger.warning("build_graph: no backend for id '%s' (param '%s')", evt.backend_id, evt.name)
                continue
            value = backend.deserialize_value(evt.name, evt.value)
            key = (evt.backend_id, evt.name)
            param_nodes[key] = ParameterNode(
                node_id=unique_name(evt.name),
                value=value,
                requires_grad=evt.requires_grad,
                name=evt.name,
                derivation=evt.derivation,
                backend=backend,
                description=evt.description,
                meta=dict(evt.meta),
            )
        elif isinstance(evt, ToolCallEvent):
            tc = ToolCallNode(
                tool_use_id=evt.tool_use_id,
                tool_name=evt.tool_name,
                arguments=dict(evt.arguments),
            )
            tc_map[evt.tool_use_id] = tc
            tool_calls.append(tc)
        elif isinstance(evt, ToolResultEvent):
            tc = tc_map.get(evt.tool_use_id)
            if tc is not None:
                tc.result = "".join(
                    block["text"] for block in evt.content if isinstance(block, dict) and "text" in block
                )
                tc.status = "error" if evt.status == "error" else "success"
        elif isinstance(evt, MessageAssistantCompleteEvent):
            result_value = _assistant_text(list(evt.content)) or result_value

    messages = reconstruct_messages(events)

    nid = f"{func_name or 'thread'}-{thread_id[:4]}"
    return ThreadNode(
        node_id=nid,
        thread_id=thread_id,
        func_name=func_name,
        messages=messages,
        value=result_value,
        parameters=list(param_nodes.values()),
        tool_calls=tool_calls,
        child_threads=[],
        events=list(events),
    )


async def build_graph(
    coordinator: Coordinator,
    thread_id: ThreadId,
    backends: list[MemoryBackend],
) -> ThreadNode:
    """Reconstruct a thread's computation graph, recursing into spawned children.

    Reads ``thread_id``'s event log from ``coordinator`` and builds its node,
    then follows each ``ThreadSpawnedEvent`` to reconstruct the child's subtree
    and wire the ``child_threads`` / ``parent`` edges. Cross-thread edges that
    live only in Python dataflow (one thread's result passed into another) are
    recorded in no event log and remain the caller's to wire.

    Args:
        coordinator: Coordinator holding the event logs.
        thread_id: Root thread to reconstruct.
        backends: Live memory backends, matched by ``backend_id``.

    Returns:
        The root ``ThreadNode`` with its spawned-child subtree attached.
    """
    return await _build_subtree(coordinator, thread_id, backends, set())


async def _build_subtree(
    coordinator: Coordinator,
    thread_id: ThreadId,
    backends: list[MemoryBackend],
    seen: set[str],
) -> ThreadNode:
    """Build ``thread_id``'s node and recurse its spawned children.

    ``seen`` guards against a thread id appearing twice in the spawn events
    (self-spawn or a cycle), so the walk stays finite.
    """
    seen.add(str(thread_id))
    events = await coordinator.get_events(thread_id)
    node = _reconstruct_node(events, backends)

    children: list[ThreadNode] = []
    for evt in events:
        if isinstance(evt, ThreadSpawnedEvent) and str(evt.child_thread_id) not in seen:
            child = await _build_subtree(coordinator, evt.child_thread_id, backends, seen)
            child.parent = node
            children.append(child)
    node.child_threads = children
    return node


async def build_graph_from_result(
    result: Result[Any],  # pyright: ignore[reportExplicitAny]
    backends: list[MemoryBackend],
) -> ThreadNode:
    """Build the full ``ThreadNode`` graph from a traced :class:`Result`.

    Combines the two edge sources: spawned children come from each thread's
    ``ThreadSpawnedEvent`` s (via :func:`build_graph`), sibling dataflow edges
    come from ``Result.inputs`` (discovered by argument scanning at trace
    time). ``ParameterView`` inputs are not grafted here — their recall events
    were emitted by ``AIFunction.trace``, so ``_reconstruct_node`` already
    materializes them as ``ParameterNode`` s.

    The returned graph is a DAG, built once: a ``Result`` consumed by several
    traces (a diamond) resolves to a **single shared node object** reachable
    from every consumer, so ``backward`` accumulates feedback from all of them
    and ``consolidate`` reads those same nodes. ``parent`` is set by the first
    consumer that grafts a node (first-consumer-wins; it is informational —
    traversal follows ``child_threads``).

    Args:
        result: The root ``Result`` returned by ``AIFunction.trace``.
        backends: Live memory backends, matched by ``backend_id``.

    Returns:
        The root ``ThreadNode`` with spawned and sibling subtrees attached.
    """
    built: dict[str, ThreadNode] = {}
    return await _assemble(result, backends, built)


def _register_subtree(node: ThreadNode, built: dict[str, ThreadNode]) -> None:
    """Index ``node`` and its spawned descendants by thread id.

    Keeps the first node seen for an id, so a thread reachable both as a
    spawned child and as a sibling ``Result`` resolves to one object.
    """
    built.setdefault(node.thread_id, node)
    for child in node.child_threads:
        _register_subtree(child, built)


async def _assemble(
    result: Result[Any],  # pyright: ignore[reportExplicitAny]
    backends: list[MemoryBackend],
    built: dict[str, ThreadNode],
) -> ThreadNode:
    """Build (or reuse) ``result``'s node and graft its sibling ``Result`` inputs."""
    tid = str(result.thread_id)
    node = built.get(tid)
    if node is None:
        node = await build_graph(result.coordinator, result.thread_id, backends)
        _register_subtree(node, built)
        node = built[tid]

    for inp in result.inputs:
        if not isinstance(inp, Result):
            continue
        child = await _assemble(inp, backends, built)
        if child is node or any(c.thread_id == child.thread_id for c in node.child_threads):
            continue
        if child.parent is None:
            child.parent = node
        node.child_threads.append(child)

    return node
