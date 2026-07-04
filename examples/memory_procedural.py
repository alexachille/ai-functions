"""Procedural memory: optimize a Python-code parameter the agent executes.

A ``Procedural`` memory parameter holds reusable Python helper functions. With
``code_execution_mode="local"``, the recalled code is loaded into a sandboxed
Python environment (smolagents' AST-based ``LocalPythonExecutor``); the agent
runs a task using those helpers and returns its answer by calling
``final_answer(...)`` inside executed code. Feedback is then backpropagated and
consolidated to improve the stored code.

The prompt parameter is annotated ``Traceable[Procedural]``: it accepts either
the raw code string or the ``ParameterView`` that ``recall`` returns, and the
``Procedural`` marker still tells the runtime to define the code in the
execution environment.

This example is also a live check of the code-execution *advertisement*: the
runtime tells the agent which helpers are already defined — by signature and
docstring — so the task prompts never name ``secret_greeting`` or spell out how
to call it. The second turn proves both that the seed helper survives the
optimizer's rewrite and that the advertisement is enough for the model to find
and call it unaided (the greeting's code is non-guessable, so a correct answer
can only come from actually executing the recalled helper).
"""

import asyncio
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from ai_functions import JSONMemoryBackend, Procedural, TextGradOptimizer, Traceable, ai_function

# Use default model
model = None


@ai_function[str](model=model, code_execution_mode="local")
def run_task(task: str, helper_functions: Traceable[Procedural]):
    """
    {task}

    Use the Python execution environment. Prefer helpers that already exist over
    writing new logic, and return your answer via final_answer.
    """


class Schema(BaseModel):
    # A non-obvious helper: the model cannot guess its output, so any task that
    # depends on it only succeeds if the recalled code is actually executed. The
    # docstring is what the runtime advertises, so the agent can pick the right
    # helper without the prompt naming it.
    helper_functions: Procedural = Field(
        default=(
            "def secret_greeting(name):\n"
            '    """Return a special secret greeting, use only if requested."""\n'
            "    return f'Zphqr, {name}! (code 7731)'\n"
        ),
        description="Reusable Python helper functions available to the agent.",
    )


async def main(path: str | Path) -> None:
    memory = JSONMemoryBackend(Schema, actor_id="coder-1", path=path, model=model)
    optimizer = TextGradOptimizer(model=model)

    print("=== Initial Procedural Memory ===")
    print(memory)

    # ── Turn 1: an open-ended task that the seed code does not directly solve ──
    # Only `secret_greeting` is defined, so the run exercises the environment;
    # feedback then grows the stored code into reusable multi-language helpers.
    result = await run_task.trace(
        task="Greet Alice in Spanish.",
        helper_functions=await memory.recall("helper_functions"),
    )
    print(f"\n=== Turn 1 Result ===\n{result}")

    feedback = (
        "Analyze the execution trace and create and save reusable helper functions."
    )
    print(f"\n=== Feedback ===\n{feedback}")

    print("\nRunning optimizer step...")
    graph = await optimizer.step(result, feedback, backends=[memory])
    for p in graph.parameters:
        if p.gradients:
            print(f"  {p.name}: {p.gradients}")

    print("\n=== Updated Procedural Memory ===")
    print(memory)

    # ── Turn 2: does the seed helper survive, and can the agent find it unaided? ──
    # We recall the *updated* code and ask for the secret greeting WITHOUT naming
    # the function or its signature. The agent has to read the advertised helper
    # signatures + docstrings to pick secret_greeting; a correct, non-guessable
    # answer proves both that it survived consolidation and that the
    # advertisement works.
    secret = await run_task.trace(
        task="Return the caller's personal secret greeting for the name 'Bob', exactly as the helper produces it.",
        helper_functions=await memory.recall("helper_functions"),
    )
    print(f"\n=== Turn 2 Result (expected to contain 'Zphqr, Bob! (code 7731)') ===\n{secret}")
    ok = "Zphqr, Bob! (code 7731)" in str(secret)
    print(f"\nsecret_greeting survived and was found via the advertisement: {ok}")

    memory.close()
    print("\nDone.")


if __name__ == "__main__":
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=True) as f:
        asyncio.run(main(f.name))
