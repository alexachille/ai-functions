"""Memory + optimizer — self-improving workflow via textual gradients.

Two ``joke_writer`` runs feed an ``email_writer``. Memory parameters start with
generic defaults; ``recall`` pulls them into each run, and ``trace`` records
which recalled parameters and prior results each run consumed. After the runs,
``optimizer.step`` reconstructs the execution graph from the event logs,
backpropagates natural-language feedback through it, and consolidates the
result back into memory.

The mental model is PyTorch autograd:
- ``recall()``   ≈ reading a learnable weight into the forward pass
- ``trace()``    ≈ a forward pass that remembers its inputs
- ``step(fb)``   ≈ ``loss.backward()`` + ``optimizer.step()`` in one call

No coordinator, worker, or graph wiring: passing a ``Result`` (``cat_joke``)
or a recalled ``ParameterView`` directly as an argument is what creates the
graph edge. Interpolating them into an f-string still computes the right
value, but drops the edge.
"""

import asyncio
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from ai_functions import JSONMemoryBackend, TextGradOptimizer, Traceable, ai_function

model = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


# Traceable[str] = str | ParameterView[str] | Result[str]: the parameter takes
# a plain string or a dataflow handle (recalled parameter / traced result).
@ai_function[str](model=model)
def joke_writer(topic: str, joke_guidelines: Traceable[str]):
    """
    Write a joke about the following topic: "{topic}".
    Use the following guidelines:
    <joke_guidelines>
    {joke_guidelines}
    </joke_guidelines>
    """


@ai_function[str](model=model)
def email_writer(joke_1: Traceable[str], joke_2: Traceable[str], formatting_guidelines: Traceable[str]):
    """
    Write an email to Jane Doe containing the following jokes:
    Joke 1: {joke_1}
    Joke 2: {joke_2}
    Use the following email formatting guidelines:
    <formatting_guidelines>
    {formatting_guidelines}
    </formatting_guidelines>
    """


class WritingMemory(BaseModel):
    joke_guidelines: str = Field(
        "No specific guidelines yet.",
        description="Guidelines to write a good joke",
    )
    formatting_guidelines: str = Field(
        "No specific guidelines yet.",
        description="Guidelines for the layout and typography of the email.",
    )


async def main(path: str | Path) -> None:
    memory = JSONMemoryBackend(WritingMemory, actor_id="writer-1", path=path, model=model)
    optimizer = TextGradOptimizer(model=model)

    print("=== Initial Memory ===")
    print(memory)

    # ── Forward pass ──
    # trace() runs the function like a call, but returns a Result that
    # remembers the recalled parameters and Results passed to it.
    cat_joke = await joke_writer.trace(
        topic="cats",
        joke_guidelines=await memory.recall("joke_guidelines"),
    )
    print(f"\n=== Cat Joke ===\n{cat_joke}")

    prog_joke = await joke_writer.trace(
        topic="programmers",
        joke_guidelines=await memory.recall("joke_guidelines"),
    )
    print(f"\n=== Programmer Joke ===\n{prog_joke}")

    # Passing the Results directly (not f-strings of them) wires the edges.
    email = await email_writer.trace(
        joke_1=cat_joke,
        joke_2=prog_joke,
        formatting_guidelines=await memory.recall("formatting_guidelines"),
    )
    print(f"\n=== Email ===\n{email}")

    # ── Optimize: build graph + backward + consolidate, in one call ──
    feedback = (
        "Jokes about cats should always be about Siamese cats. "
        "Jokes about programmers should be about coffee. "
        "The email should include a title for each joke."
    )
    print(f"\n=== Feedback ===\n{feedback}")

    print("\nRunning optimizer step...")
    graph = await optimizer.step(email, feedback, backends=[memory])

    print("\n=== Graph ===")
    print(f"Root {graph.thread_id}: params={[p.name for p in graph.parameters]}")
    for child in graph.child_threads:
        print(f"  {child.thread_id}: params={[p.name for p in child.parameters]}")
        for p in child.parameters:
            if p.gradients:
                print(f"    {p.name}: {p.gradients}")

    print("\n=== Final Memory ===")
    print(memory)

    memory.close()
    print("\nDone — recall() now returns the improved guidelines.")


if __name__ == "__main__":
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=True) as f:
        asyncio.run(main(f.name))
