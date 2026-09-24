"""Declaration-time checks need neither Lean nor a model."""

import pytest

from ai_functions.experimental import verified
from ai_functions.experimental.verified import tool
from ai_functions.experimental.verified.lean import LeanError, LeanProject


def test_decorations_are_lazy_and_replace_keeps_the_contract(monkeypatch) -> None:
    project = LeanProject()
    project.add("def Contract (result : Nat) (x : Nat) : Prop := result = x")
    project.add("opaque observe : Nat → Nat")
    contract, observation = project.symbols.Contract, project.symbols.observe

    def unexpected(*args, **kwargs):
        raise AssertionError("Decoration must not prepare Lean or inspect metadata")

    monkeypatch.setattr(project, "prepare", unexpected)
    monkeypatch.setattr(project, "describe", unexpected)

    @tool(observation)
    def read(x: int) -> int:
        return x

    @verified.ai_function(contract=contract, tools=[read])
    def identity(x: int) -> int:
        """Return x."""

    variant = identity.replace(max_attempts=2)
    assert variant.contract is contract


def test_foreign_tool_and_judgment_owners_fail_without_preparing() -> None:
    project, other = LeanProject(), LeanProject()

    @tool(other.symbols.observe)
    def read(x: int) -> int:
        return x

    def identity(x: int) -> int:
        """Return x."""

    for kwargs in ({"tools": [read]}, {"judgments": [other.symbols.reading]}):
        with pytest.raises(LeanError, match="same LeanProject"):
            verified.ai_function(contract=project.symbols.Contract, **kwargs)(identity)
