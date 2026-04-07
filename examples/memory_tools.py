"""Memory Tool Provider Example.

Demonstrates how to give an AI agent direct access to memory parameters
via dynamically generated tools. The agent can recall, search, query,
save, and delete memory entries using tools whose names and descriptions
are derived from the schema.

Flow:
1. Seed a memory with travel preferences and a list of visited places
2. Create an ai_function with memory tools attached
3. Ask the agent to plan a trip — it will search/recall memory to personalize
4. Ask the agent to update memory with new preferences learned from the conversation
"""

import tempfile
from pathlib import Path

from pydantic import BaseModel, Field
from utils import display

from ai_functions import ai_function
from ai_functions.memory.json_backend import JSONMemoryBackend

model = None


class TravelMemory(BaseModel):
    preferences: str = Field(
        default="Prefers warm destinations. Likes hiking and local food. Budget-conscious.",
        description="Travel preferences and style of the user",
    )
    visited: list[str] = Field(
        default=[
            "Tokyo, Japan - loved the street food and temples",
            "Barcelona, Spain - enjoyed the architecture and beaches",
            "Banff, Canada - amazing hiking trails",
            "Marrakech, Morocco - great markets and riads",
            "Reykjavik, Iceland - stunning landscapes but too cold",
        ],
        description="Places the user has visited with brief notes",
    )


@ai_function(model=model)
def travel_assistant(request: str) -> str:
    """You are a travel planning assistant with access to the user's travel memory.
    Use the available tools to look up their preferences and past trips before
    making recommendations. You can also update their memory when they share
    new information.

    User request: {request}
    """


def main(path: str | Path):
    memory = JSONMemoryBackend(TravelMemory, "traveler-1", path=path, model=model)

    display("Initial Memory", str(memory))

    # Give the agent tools for both parameters
    tools = memory.tool_provider("preferences", "visited")
    assistant = travel_assistant.replace(tools=[tools])

    # Ask for a recommendation: the agent should search/recall memory
    response = assistant("I want to go somewhere new next month. Suggest a destination and explain why.")
    display("Recommendation", response, lang="markdown")

    # Ask the agent to update memory
    response = assistant(
        "I just got back from Lisbon, Portugal — loved the pastéis de nata and the tram rides. "
        "Also, I've decided I want to try more European cities. Please update my memory."
    )
    display("Update Response", response, lang="markdown")

    # Show updated memory
    display("Updated Memory", str(memory))

    # One more query to verify the agent uses the updated memory
    response = assistant("Based on what you know about me, what European city should I visit next?")
    display("Follow-up Recommendation", response, lang="markdown")

    memory.close()


if __name__ == "__main__":
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=True) as f:
        main(f.name)
