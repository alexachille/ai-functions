"""TextGrad-style optimizer over a reconstructed computation graph.

Walks a ``ThreadNode`` graph in reverse topological order, using an internal AI
function to distribute feedback from each node to its grad-enabled parameter
inputs, then consolidates feedback directly into the memory backends referenced
by each ``ParameterNode``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from strands.models import Model

from ..ai_thread import ai_function
from ..types.context import no_thread_scope
from ..types.graph import ParameterNode, ThreadNode
from ._graph import build_graph_from_result, topological_sort
from .rendering import render_inputs, render_messages

if TYPE_CHECKING:
    from ..memory.base import MemoryBackend
    from ..types.graph import Result

logger = logging.getLogger(__name__)


class Feedback(BaseModel):
    node_name: str = Field(..., description="The parameter node_id to provide feedback to.")
    feedback: str = Field(..., description="How the parameter should change.")


class Feedbacks(BaseModel):
    feedbacks: list[Feedback]


@ai_function[Feedbacks]
def _compute_gradients(
    parameters: str,
    trace: str,
    output: str,
    feedback: list[str],
) -> str:
    """Build the backward prompt that distributes feedback to parameters."""
    issues = "\n".join(f"- {f}" for f in feedback)
    return (
        "An agent received the following parameter inputs:\n"
        f"<parameters>\n{parameters}\n</parameters>\n\n"
        "The agent produced the following execution trace:\n"
        f"<trace>\n{trace}\n</trace>\n\n"
        "At the end, it produced the following output:\n"
        f"<output>\n{output}\n</output>\n\n"
        "The following issues need to be fixed:\n"
        f"<issues>\n{issues}\n</issues>\n\n"
        "Return feedback for each parameter that needs to change.\n"
        "Rules:\n"
        "- Only provide feedback relevant to the parameter's description.\n"
        "- Feedback must be general and applicable to different future inputs.\n"
        "- If a parameter doesn't need changes, omit it."
    )


class TextGradOptimizer:
    """Propagate feedback through a ThreadNode graph and consolidate into memory.

    One-call usage over a traced result::

        result = await email_writer.trace(jokes=cat, formatting_guidelines=fmt)
        optimizer = TextGradOptimizer(model=model)
        graph = await optimizer.step(result, "The email needs joke titles.", backends=[memory])

    Step-by-step usage over a reconstructed graph::

        graph = await build_graph(coord, thread_id, [memory])
        optimizer.backward(graph, "The output should be more concise.")
        optimizer.consolidate(graph)

    Attributes:
        last_dropped_feedback: Parameter ids from the most recent ``backward``
            for which the backward model returned feedback that matched no
            parameter (that feedback is dropped). Empty when nothing was lost.
    """

    def __init__(
        self,
        model: Model | str | None = None,
    ) -> None:
        self._backward_fn = _compute_gradients.replace(model=model)
        self.last_dropped_feedback: list[str] = []

    def backward(self, root: ThreadNode, feedback: str) -> None:
        """Propagate feedback through the graph.

        Appends feedback to root, then for each ThreadNode with gradients and
        grad-enabled parameters, runs the backward AI function to distribute
        feedback to individual parameters. A node's gradients are forwarded to
        its child threads, which re-refine them against their own parameters
        when visited — this carries feedback through a multi-level graph.
        """
        root.gradients.append(feedback)
        self.last_dropped_feedback = []

        for node in topological_sort(root):
            if not node.gradients:
                continue
            grad_params = [p for p in node.parameters if p.requires_grad]
            if not grad_params:
                for child in node.child_threads:
                    child.gradients.extend(node.gradients)
                continue

            # The backward model call must not attribute to any ambient thread
            # scope — it would spawn as a child of the user's thread and
            # pollute the event log the graph was built from.
            with no_thread_scope():
                result = self._backward_fn.run_sync(
                    parameters=render_inputs(grad_params),
                    trace=render_messages(node.messages, {}),
                    output=str(node.value or ""),
                    feedback=node.gradients,
                )

            param_map = {p.node_id: p for p in grad_params}
            for fb in result.feedbacks:
                if fb.node_name in param_map:
                    param_map[fb.node_name].gradients.append(fb.feedback)
                else:
                    self.last_dropped_feedback.append(fb.node_name)
                    logger.warning(
                        "Backward: feedback for '%s' but no such parameter in node %s",
                        fb.node_name,
                        node.thread_id,
                    )

            for child in node.child_threads:
                child.gradients.extend(node.gradients)

        if self.last_dropped_feedback:
            logger.warning(
                "Backward: %d feedback item(s) matched no parameter and were dropped: %s. "
                "Inspect TextGradOptimizer.last_dropped_feedback.",
                len(self.last_dropped_feedback),
                ", ".join(self.last_dropped_feedback),
            )

    def consolidate(self, root: ThreadNode) -> None:
        """Consolidate accumulated parameter gradients into their memory backends.

        Groups gradients by ``(backend, name)`` so a parameter recalled in
        several threads is consolidated once, via the node's direct backend ref.
        Search-derived retrieval context (``meta["results"]``, the
        ``{entry_id: value}`` mapping of the entries the forward pass actually
        retrieved) is merged across the group and passed along, so a backend
        can target consolidation at those entries instead of the full value.
        """
        grouped: dict[tuple[int, str], tuple[ParameterNode, list[str], dict[str, str]]] = {}
        for node in topological_sort(root):
            for p in node.parameters:
                if not p.gradients or p.backend is None:
                    continue
                key = (id(p.backend), p.name)
                if key not in grouped:
                    grouped[key] = (p, [], {})
                grouped[key][1].extend(p.gradients)
                results = p.meta.get("results")  # pyright: ignore[reportAny]
                if isinstance(results, dict):
                    grouped[key][2].update({str(k): str(v) for k, v in results.items()})  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]

        for (_, param_name), (param, feedbacks, retrieved) in grouped.items():
            assert param.backend is not None
            param.backend.consolidate(param_name, feedbacks, retrieved=retrieved or None)

    def zero_grad(self, root: ThreadNode) -> None:
        """Clear all node and parameter gradients in the graph."""
        for node in topological_sort(root):
            node.gradients.clear()
            for p in node.parameters:
                p.gradients.clear()

    async def step(
        self,
        result: Result[Any],  # pyright: ignore[reportExplicitAny]
        feedback: str,
        backends: list[MemoryBackend],
    ) -> ThreadNode:
        """Build the graph from a traced result, backpropagate, and consolidate.

        The whole optimization dance in one call: reconstructs the graph from
        ``result`` (spawned children from events, sibling edges from
        ``Result.inputs``), runs :meth:`backward` with ``feedback``, then
        :meth:`consolidate` — on the **same** graph object, preserving the key
        invariant that gradients accumulate and are consolidated on nodes
        built exactly once.

        ``backward`` and ``consolidate`` make blocking model calls, so they
        run in a worker thread to keep the event loop responsive.

        Args:
            result: The root ``Result`` returned by ``AIFunction.trace``.
            feedback: Natural-language feedback on the root's output.
            backends: Live memory backends, matched by ``backend_id``.

        Returns:
            The graph, for inspection (gradients, structure) after the step.
        """
        graph = await build_graph_from_result(result, backends)
        await asyncio.to_thread(self.backward, graph, feedback)
        await asyncio.to_thread(self.consolidate, graph)
        return graph
