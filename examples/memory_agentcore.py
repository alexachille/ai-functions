"""Memory + optimizer with the AWS Bedrock AgentCore backend.

Same self-improving joke -> email flow as ``memory_optimization.py``, but
parameters live in an AgentCore Memory resource instead of a JSON file. The
workflow is identical — ``recall`` + ``trace`` wire the graph, ``step`` runs
backward and consolidation — only the backend changes.

AgentCore consolidation differs from the JSON backend: rather than running an
explicit merge AI function, ``consolidate`` appends feedback as conversation
turns, which AgentCore's semantic-memory strategy extracts into long-term
memory **asynchronously**. Short-term memory reflects the turns immediately;
long-term records appear after a delay.

Requires AWS credentials with Bedrock AgentCore permissions and the optional
dependency:  ``pip install strands-ai-functions[agentcore]``.
Set AWS_REGION if you are not using us-east-1.
"""

import asyncio
import os
import uuid

from pydantic import BaseModel, Field

from ai_functions import AgentCoreMemoryBackend, TextGradOptimizer, Traceable, ai_function

model = None  # use the default model
region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


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


async def wait_for_ltm_update(
    memory: AgentCoreMemoryBackend,
    max_wait: int = 180,
    poll_interval: int = 10,
) -> bool:
    """Poll AgentCore until new LTM records appear after consolidation.

    ``record_counts()`` returns ``(stm_count, ltm_count)``; semantic extraction
    runs asynchronously, so the long-term count only rises some time after the
    consolidation turns are appended. Returns True once it does, or False on
    timeout.
    """
    _, initial_ltm = memory.record_counts()
    elapsed = 0
    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        _, current_ltm = memory.record_counts()
        if current_ltm > initial_ltm:
            print(f"LTM updated after {elapsed}s ({initial_ltm} -> {current_ltm} records).")
            return True
    print(f"Timed out after {max_wait}s waiting for LTM consolidation.")
    return False


async def main() -> None:
    # Get-or-create an AgentCore memory resource by name; namespaced per actor.
    memory = AgentCoreMemoryBackend(
        WritingMemory,
        actor_id=f"writer-{uuid.uuid4()}",
        memory_name="ai_function_backprop_test",
        region_name=region,
        model=model,
    )
    optimizer = TextGradOptimizer(model=model)

    print("=== Initial Memory ===")
    print(memory)

    # ── Forward pass: trace() records which recalled parameters and prior
    # results each run consumed — passing them as arguments wires the graph.
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

    print("\nRunning optimizer step (consolidation appends turns; LTM extraction is async)...")
    await optimizer.step(email, feedback, backends=[memory])

    # Short-term memory reflects the new turns immediately.
    print("\n=== Current Memory (STM) ===")
    print(memory)

    # Long-term memory catches up asynchronously as AgentCore's semantic
    # strategy extracts the appended turns — poll until those records appear.
    print("\nWaiting for LTM consolidation...")
    await wait_for_ltm_update(memory)

    print("\n=== Current Memory (LTM) ===")
    print(memory)

    # Tidy up the test memory resource. Drop these two lines to inspect LTM
    # in the AgentCore console once semantic extraction has run.
    memory.delete_all(wait=False)
    memory.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
