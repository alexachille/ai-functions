"""Memory List Search & Consolidation Example.

Demonstrates BM25 search over list[str] memory parameters and tool-based
consolidation.  The example builds a simple recipe assistant that stores
cooking tips as a list of memories.  After the first run we supply feedback
and let the optimizer propagate it, then consolidate. The consolidation agent
uses tools (search, add, update, delete) to surgically edit the memory list
rather than rewriting it wholesale.

Flow:
1. Seed a memory with initial cooking tips
2. Run a recipe assistant that searches relevant tips
3. User provides feedback about missing / incorrect tips
4. Backward pass: propagate feedback through the graph
5. Consolidate: the consolidation agent uses CRUD tools to patch the list
6. Re-run to verify the tips have been updated
"""

import tempfile
from pathlib import Path

from pydantic import BaseModel, Field
from utils import display

from ai_functions import ai_function
from ai_functions.memory.json_backend import JSONMemoryBackend
from ai_functions.optimizer.textgrad import TextGradOptimizer
from ai_functions.types.graph import Result
from ai_functions.utils import bullet_points, get_console

model = None  # Use default model


@ai_function(model=model, callback_handler=None)
def recipe_assistant(dish: str, tips: list[str]) -> str:
    """Write a short recipe for "{dish}".

    Incorporate the following cooking tips where relevant:
    <tips>
    {bullet_points(tips)}
    </tips>
    """


class CookingMemory(BaseModel):
    tips: list[str] = Field(
        default=[
            "Always season pasta water generously with salt",
            "Let meat rest after cooking before slicing",
            "Toast spices in a dry pan to release their aroma",
            "Use cold butter for flaky pastry dough",
            "Deglaze the pan with wine or stock for a quick sauce",
            "Add a pinch of sugar to tomato sauces to balance acidity",
            "Pat proteins dry before searing for a better crust",
            "Finish dishes with fresh herbs for brightness",
            "Use a thermometer — don't guess doneness",
            "Rest pizza dough overnight in the fridge for better flavor",
        ],
        description="A collection of cooking tips the assistant can search and use.",
    )


def main(path: str | Path):
    memory = JSONMemoryBackend(CookingMemory, "chef-1", path=path, model=model, quiet=True)
    optimizer = TextGradOptimizer(model=model, quiet=True)

    display("Initial Tips", str(memory))

    # Search for tips relevant to making pasta, then write a recipe
    tips_view = memory.search("tips", "pasta sauce tomato", k=5)
    display("Retrieved Tips (search: 'pasta sauce tomato')",
            bullet_points(tips_view.value), lang="text")

    result: Result[str] = recipe_assistant.trace("spaghetti pomodoro", tips=tips_view)
    display("Recipe", result.value, lang="markdown")

    # Provide feedback
    feedback = (
        "The tip about sugar in tomato sauce is wrong — use a splash of balsamic vinegar instead. "
        "Also add a tip about using San Marzano tomatoes for the best pomodoro. "
        "The pasta water tip should mention saving a cup of pasta water for the sauce."
    )
    display("Feedback", feedback, lang="text")

    # Backward — propagate feedback to the tips parameter
    with get_console().status("Propagating feedback..."):
        optimizer.backward(result, feedback)

    # Show what gradients landed on the tips parameter
    display("Gradients for `tips`:", "\n".join([str(g) for g in tips_view.source.gradients]), lang="text")

    # Consolidate feedback. The consolidation agent will use its search/add/update/delete tools to update the list
    with get_console().status("Consolidating tips..."):
        optimizer.consolidate(result)

    display("Updated Tips", str(memory))

    # Re-run with updated memory
    tips_view = memory.search("tips", "pasta sauce tomato", k=5).value
    display("Retrieved Tips After Update", bullet_points(tips_view))

    result: str = recipe_assistant("spaghetti pomodoro", tips=tips_view)
    display("Recipe (after optimization)", result, lang="markdown")

    memory.close()


if __name__ == "__main__":
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=True) as f:
        main(f.name)
