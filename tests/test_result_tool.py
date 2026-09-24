"""An explicit result tool replaces the generated structured-output tool."""

from pydantic import BaseModel
from strands import tool
from strands.types.tools import ToolContext

from ai_functions import ai_function
from ai_functions.testing import ScriptedModel, Turn


class Answer(BaseModel):
    answer: int


@tool(context=True)
def checked_result(value: int, tool_context: ToolContext) -> str:
    """Return an application-checked answer."""
    state = tool_context.invocation_state["request_state"]
    state["tool_result"] = Answer(answer=value)
    state["stop_event_loop"] = True
    return "Accepted."


async def test_result_tool_is_the_only_advertised_completion_channel(monkeypatch):
    model = ScriptedModel(
        [
            Turn(text="No checked answer yet."),
            Turn(tool_calls=(("checked_result", {"value": 42}),)),
        ]
    )
    requests = []
    stream = model.stream

    def record(messages, tool_specs=None, system_prompt=None, **kwargs):
        requests.append((messages.copy(), tool_specs))
        return stream(messages, tool_specs=tool_specs, system_prompt=system_prompt, **kwargs)

    monkeypatch.setattr(model, "stream", record)

    def validate(value):
        assert type(value) is int and value == 42

    @ai_function(
        model=model,
        tools=[checked_result],
        result_tool="checked_result",
        max_attempts=1,
        post_conditions=[validate],
    )
    def answer() -> int:
        """Find the answer."""

    assert await answer() == 42
    assert model.remaining_turns == 0
    assert len(requests) == 2
    for messages, tools in requests:
        assert "FinalAnswer" not in {tool["name"] for tool in tools}
        assert "checked_result" in {tool["name"] for tool in tools}
        text = "\n".join(item.get("text", "") for message in messages for item in message["content"])
        assert "use the checked_result tool" in text
        assert "use the FinalAnswer tool" not in text
    assert "No result was produced" in text


async def test_default_structured_result_channel_is_unchanged():
    @ai_function(model=ScriptedModel([Turn(tool_calls=(("FinalAnswer", {"answer": 42}),))]))
    def answer() -> int:
        """Find the answer."""

    assert await answer() == 42
