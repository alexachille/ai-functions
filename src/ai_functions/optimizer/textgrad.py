"""Optimizer loosely inspired by TextGrad (https://arxiv.org/abs/2406.07496).

TextGrad proposes automatic differentiation via text: natural-language feedback is
propagated backward through a computation graph, node by node, to update the text-based
parameters that produced a given output.

This module adapts the idea to AI Function graphs. For each Result node, an LLM receives
the node's inputs, execution trace, output, and accumulated feedback, and produces
targeted feedback for each input. The feedback propagates down the graph until it reaches
the leaf parameters, where it is collected for consolidation by the memory backend.
"""

from typing import Any

from pydantic import BaseModel, Field
from strands.models import Model

from .. import ai_function
from ..types.graph import Node, ParameterGradient, ParameterView, Result
from ..utils import (
    bullet_points,  # noqa: F401
    quiet_console,
    topological_sort,
)
from .base import Optimizer
from .rendering import render_inputs, render_messages


class Feedback(BaseModel):
    """A single feedback entry targeting a specific node."""

    node_id: str = Field(..., description="The id of the node or tool call to which you are providing feedback.")
    feedback: str = Field(
        ...,
        description="How the input node should change. "
        "The feedback MUST be relevant to the node description, if any. "
        "Feedback MUST be general and applicable to different future inputs.",
    )


class Feedbacks(BaseModel):
    """Collection of feedback entries produced by the optimizer."""

    feedbacks: list[Feedback]


OPTIMIZE_TOOLS_PROMPT = (
    "You can also provide feedback to function calls that appears in the trace. "
    "However try to minimize the number of function calls to which you provide feedback. "
    "You cannot provide feedback to tool calls."
)

DO_NOT_OPTIMIZE_TOOLS_PROMPT = "Do not provide any feedback to tool calls or function calls."


@ai_function(callback_handler=None)
def compute_gradients(inputs: str, trace: str, output: Any, feedback: list[str], optimize_tools: bool) -> Feedbacks:  # type: ignore[empty-body]
    """\
    You are an optimization agent. You analyze conversation traces to determine how
    parameters and inputs to an agent should be updated. You will be provided with
    input results, parameters (with their current values) and the execution
    trace of an agent using this information.

    # Inputs to the agent
    {inputs}

    ## Conversation trace
    {trace}

    ## Agent Output
    {output}

    ## Issues
    {bullet_points(feedback)}

    ## Rules
    Analyze the trace and produce per-input feedback.

    1. If the input has parameter type, provide feedback using the following bullet point format:
    ```
    - add: <text> — information to add to the input
    - update: <text> — information to change in the input
    - delete: <text> — information to remove from the input
    ```

    2. If the input has result type, provide feedback using the following format:
    ```
    - improve: <text> — concrete feedback of how this specific result should change to improve resolve the issues
    ```

    3. ONLY provide feedback that is relevant to the parameter's description. It may be that some of the user
      feedback is not relevant to any of the input nodes. It is fine to ignore this feedback.

    4. The feedback you provide should always be general and applicable to future inputs.

    {OPTIMIZE_TOOLS_PROMPT if optimize_tools else DO_NOT_OPTIMIZE_TOOLS_PROMPT}
    """
    ...


class TextGradOptimizer(Optimizer):
    """Optimizer that uses TextGrad to compute and apply parameter gradients."""

    def __init__(self, optimize_tools: bool = False, model: Model | str | None = None, quiet: bool = True):
        """Initialize the TextGrad optimizer.

        Args:
            optimize_tools: Whether to include tool calls in optimization feedback.
            model: LLM model for computing gradients.
            quiet: Whether to suppress console output.
        """
        self.optimize_tools = optimize_tools
        self.quiet = quiet
        self.backward_fn = compute_gradients.replace(model=model)

    def backward(self, node: Node[Any], feedback: str) -> None:
        """Compute gradients for all parameters reachable from ``node``."""
        sorted_nodes = topological_sort(node)

        # Clear all intermediate node gradients to prevent duplication on repeated backward calls.
        # The accumulated parameter gradients live on ParameterRef and are cleared by zero_grad().
        for v in sorted_nodes:
            v.gradients.clear()

        # Seed the root node with the feedback
        node.gradients.append(feedback)

        for v in sorted_nodes:
            if v.gradients:
                if isinstance(v, ParameterView):
                    for g in v.gradients:
                        v.source.gradients.append(ParameterGradient(feedback=g, derivation=v.derivation))
                elif isinstance(v, Result):
                    self.optimize_node(v)
                else:
                    raise ValueError(f"Invalid node type {v.__class__.__name__} in graph.")

    def optimize_node(self, node: Result[Any]) -> None:
        """Compute and distribute feedback for a single Result node."""
        all_nodes = {v.name: v for v in node.inputs + node.tool_results}
        valid_ids = set(all_nodes)
        tool_id_to_tool_result_id = {v.tool_id: v.name for v in node.tool_results if v.tool_id is not None}

        def _node_ids_valid(result: Feedbacks) -> None:
            for fb in result.feedbacks:
                assert fb.node_id in valid_ids, (
                    f"Feedback references unknown node '{fb.node_id}'. Valid nodes: {sorted(valid_ids)}"
                )

        with quiet_console(self.quiet):
            result = self.backward_fn.replace(post_conditions=[_node_ids_valid])(
                inputs=render_inputs(node.inputs),
                trace=render_messages(node.agent.messages, tool_id_to_tool_result_id),
                output=node.value,
                feedback=node.gradients,
                optimize_tools=self.optimize_tools,
            )
        for feedback in result.feedbacks:
            all_nodes[feedback.node_id].gradients.append(feedback.feedback)
