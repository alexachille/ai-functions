"""Test procedural memory: single-node optimization of a Procedural parameter."""

import tempfile
from pathlib import Path

from pydantic import BaseModel, Field
from utils import display

from ai_functions import ai_function
from ai_functions.memory import JSONMemoryBackend, Procedural
from ai_functions.optimizer.textgrad import TextGradOptimizer
from ai_functions.utils import show_graph


class Schema(BaseModel):
    helper_functions: Procedural = Field(
        default="def greet(name):\n    return f'Hello, {name}'\n",
    )


@ai_function(code_execution_mode="local")
def run_task(helper_functions: str) -> str:
    """
    Use the python execution environment to greet "Alice" in Spanish and return the greeting.
    """


def main(path: Path):
    mem = JSONMemoryBackend(Schema, "test-1", path=path)

    helpers = mem.recall("helper_functions")
    display('Initial Procedural Memory', helpers.value, 'python')

    # Run the task. Procedural memories are automatically loaded into the agent's python env
    result = run_task.trace(helper_functions=helpers)
    display('Result', result.value, 'text')

    # Backprop feedback into the single procedural parameter
    optimizer = TextGradOptimizer()
    optimizer.backward(result, "Analyze the execution trace and create and save reusable helper functions.")

    show_graph(result)

    # Consolidate feedback into memory
    optimizer.consolidate(result)

    display('Final Procedural Memory', mem.recall("helper_functions").value, 'python')

    mem.close()


if __name__ == "__main__":
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=True) as f:
        main(Path(f.name))
