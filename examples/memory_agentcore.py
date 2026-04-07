"""Memory optimization example using AgentCore backend.

This example demonstrates how to use TextGradOptimizer with AgentCoreMemoryBackend
to automatically improve memory parameters based on feedback.

NOTE: Requires AWS credentials with Bedrock permissions.
Set AWS_REGION environment variable if not using us-east-1.
"""

import os
import uuid
import boto3
from pydantic import BaseModel, Field

from ai_functions import ai_function
from ai_functions.types.graph import Result
from ai_functions.utils import show_graph, bullet_points, get_console
from ai_functions.optimizer.textgrad import TextGradOptimizer
from ai_functions.memory import AgentCoreMemoryBackend

from utils import display, wait_for_ltm_update

model = None  # use default model

current_region = (
    boto3.session.Session().region_name
    or os.getenv("AWS_REGION")
    or os.getenv("AWS_DEFAULT_REGION")
    or "us-east-1"
)

@ai_function(model=model, callback_handler=None)
def joke_writer(topic: str, joke_guidelines: str) -> str:
    """
    Write a joke about the following topic: "{topic}".

    Use the following guidelines:
    <joke_guidelines>
    {joke_guidelines}
    </joke_guidelines>
    """


@ai_function(model=model, callback_handler=None)
def email_writer(jokes: list[str], formatting_guidelines: str) -> str:
    """
     Write an email to Jane Doe containing the following jokes:
     {bullet_points(jokes)}

     Use the following email formatting guidelines:
     <formatting_guidelines>
     {formatting_guidelines}
     </formatting_guidelines>
     """


class Schema(BaseModel):
    joke_guidelines: str = Field("No specific guidelines yet.", description="Guidelines to write a good joke")
    formatting_guidelines: str = Field("No specific guidelines yet.",
                                      description="Guidelines for the layout and typography of the email.")


def main():
    # Create AgentCore memory backend (automatically gets/creates memory by name)
    memory = AgentCoreMemoryBackend(
        Schema,
        f"ai_functions_test_{uuid.uuid4()}",
        memory_name="ai_function_backprop_test",
        region_name=current_region
    )
    optimizer = TextGradOptimizer(model=model, quiet=True)

    display("Initial Memory", str(memory), lang="yaml")

    # To optimize memory, we need to track the graph of all function calls and memory parameter used
    # Using .invoke(...) automatically adds the necessary tracking information to the result
    cat_joke = joke_writer.trace('a joke about cats', joke_guidelines=memory.recall('joke_guidelines'))
    programmer_joke = joke_writer.trace('a joke about programmers', joke_guidelines=memory.recall('joke_guidelines'))
    result: Result[str] = email_writer.trace([cat_joke, programmer_joke],
                                              formatting_guidelines=memory.recall('formatting_guidelines'))

    # `result` contains the representation of the entire graph leading to it. To extract the actual output we use .value
    display("Email Written", result.value, lang="markdown")

    # We can now use feedback to optimize the memory, adjusting to user preferences or preventing errors
    feedback = (
        "Jokes about cats should always be about Siamese cats. "
        "Jokes about programmers should be about coffee. "
        "The email should include a title for each joke. "
    )

    display("Feedback", feedback, lang="text")

    # First, we need to propagate the feedback through the graph to determine which functions is responsible for what
    # issue and which parameters need to be updated
    with get_console().status("Propagating feedback..."):
        optimizer.backward(result, feedback)

    # We can now visualize the entire agent and parameter graph and see how the feedback has been propagated
    show_graph(result, open_browser=True)

    # Finally, we consolidate the feedback propagated to each parameter, and commit the new value to memory
    # Note: AgentCore consolidation adds feedback as conversation turns, which AgentCore's semantic
    # memory extractor will process asynchronously and consolidate into long-term memory
    with get_console().status("Consolidating memory..."):
        optimizer.consolidate(result)

    # Short term memory updates immediately, while long term memory updates asynchronously
    display("Current Memory (STM)", str(memory), lang="yaml")

    # Poll for LTM consolidation — waits until new long-term memory records appear
    wait_for_ltm_update(memory)

    # Display consolidated long-term memory
    display("Current Memory (LTM)", str(memory), lang="yaml")

    memory.delete_all(wait=False)

    memory.close()


if __name__ == '__main__':
    main()
