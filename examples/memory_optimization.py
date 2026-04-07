"""Memory Optimization Example — Full Learning Loop.

Demonstrates how to use the TextGrad optimizer with a JSON-backed memory to
iteratively refine AI-function behavior based on user feedback.

The example builds a small agent graph: two ``joke_writer`` calls feed into an
``email_writer``.  Memory parameters (joke guidelines, formatting guidelines)
start with generic defaults.  After the first run we supply explicit feedback
("jokes about cats should be about Siamese cats", "include a title for each
joke", etc.) and let the optimizer:

1. **Backward pass** — propagate the feedback through the call graph so each
   function and parameter receives the portion of feedback relevant to it.
2. **Consolidate** — merge the propagated feedback into the memory parameters,
   producing updated guidelines that will steer future invocations.
3. **Verification run** — re-run the same workflow with updated memory to show
   the output has actually improved.

This demonstrates the complete learning loop: run → feedback → optimize → run again → better result.
"""

import tempfile
from pathlib import Path

from pydantic import BaseModel, Field
from utils import display

from ai_functions import ai_function
from ai_functions.memory.json_backend import JSONMemoryBackend
from ai_functions.optimizer.textgrad import TextGradOptimizer
from ai_functions.types.graph import Result
from ai_functions.utils import bullet_points as bullet_points  # noqa: F401
from ai_functions.utils import get_console, show_graph

model = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


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


def main(path: str | Path):
    # Set quiet=False to visualize the work during backpropagation and memory update
    memory = JSONMemoryBackend(Schema, 'test-1', path=path, model=model, quiet=True)
    optimizer = TextGradOptimizer(model=model, quiet=True)

    display("Initial Memory", str(memory))

    # To optimize memory, we need to track the graph of all function calls and memory parameter used
    # Using .trace(...) automatically adds the necessary tracking information to the result
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
    with get_console().status("Consolidating memory..."):
        optimizer.consolidate(result)

    # We can now show the new value of the memory
    display("Updated Memory", str(memory))

    # Re-run with updated memory to confirm improvement. We don't need .trace() since we are not creating a graph
    display("Re-running with Updated Memory", "Running the same workflow again to verify improvement...")

    cat_joke = joke_writer('a joke about cats', joke_guidelines=memory.recall('joke_guidelines').value)
    programmer_joke = joke_writer('a joke about programmers', joke_guidelines=memory.recall('joke_guidelines').value)
    result: str = email_writer([cat_joke, programmer_joke],
                               formatting_guidelines=memory.recall('formatting_guidelines').value)

    display("Email Written (after optimization)", result, lang="markdown")

    # At the end we close the memory to ensure the new memory is written and resources are released properly
    memory.close()


if __name__ == '__main__':
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=True) as f:
        main(f.name)
