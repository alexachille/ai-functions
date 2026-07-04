"""The code-execution prompt preamble that advertises the sandbox namespace.

When ``code_execution_mode="local"``, the runtime tells the agent which modules
are importable, which recalled ``Procedural`` helpers are already defined —
**by signature and docstring**, so the agent knows *when* to call each — and
which other bound variables are in scope. Without this, a recalled helper is
defined in the sandbox but never surfaced to the model.
"""

from __future__ import annotations

import importlib.util

import pytest
from pydantic import BaseModel, Field

from ai_functions import ai_function
from ai_functions.ai_thread.ai_thread import AIThread
from ai_functions.memory import Procedural
from ai_functions.testing import RuntimeHarness, ScriptedModel, Turn
from ai_functions.tools.local_python_executor import procedural_signatures
from ai_functions.types import EventKind, MessageUserEvent

_HAS_SMOLAGENTS = importlib.util.find_spec("smolagents") is not None


# ── procedural_signatures helper ──────────────────────────────────────────────


def test_procedural_signatures_captures_signature_docstring_and_return() -> None:
    """Each block is the def line (with return annotation) plus the docstring."""
    code = (
        "def greet(name: str) -> str:\n"
        '    """Say hello to someone by name."""\n'
        "    return f'hi {name}'\n"
    )
    (block,) = procedural_signatures(code)
    assert block == 'def greet(name: str) -> str:\n    """Say hello to someone by name."""'


def test_procedural_signatures_handles_async_and_missing_docstring() -> None:
    code = "async def fetch(url, *, timeout=5):\n    return url\n"
    (block,) = procedural_signatures(code)
    # No docstring → a `...` body stands in; async prefix preserved.
    assert block == "async def fetch(url, *, timeout=5):\n    ..."


def test_procedural_signatures_skips_underscored_and_nested_defs() -> None:
    """Only top-level, non-underscored defs are advertised (they are the callable
    names in the namespace); classes and private helpers are omitted."""
    code = (
        "def public(x):\n"
        '    """Public helper."""\n'
        "    def _inner():\n"
        "        return 1\n"
        "    return _inner()\n\n"
        "def _private(y):\n"
        "    return y\n\n"
        "class Helper:\n"
        "    pass\n"
    )
    blocks = procedural_signatures(code)
    assert len(blocks) == 1
    assert blocks[0].startswith("def public(x):")
    assert "_private" not in " ".join(blocks)
    assert "_inner" not in " ".join(blocks)
    assert "class Helper" not in " ".join(blocks)


def test_procedural_signatures_returns_empty_on_syntax_error() -> None:
    assert procedural_signatures("def broken(") == []


# ── _code_env_preamble ────────────────────────────────────────────────────────


def _thread_with_bound_args(fn, bound_args: dict[str, object]) -> AIThread:
    thread = AIThread(fn, fn.config)
    thread._bound_args = bound_args  # noqa: SLF001 — exercising the preamble directly
    return thread


def test_preamble_empty_when_code_execution_disabled() -> None:
    @ai_function[str]
    def f(helpers: Procedural):
        """{helpers}"""

    thread = _thread_with_bound_args(f, {"helpers": "def h():\n    return 1\n"})
    assert thread._code_env_preamble(f.config) == ""  # noqa: SLF001


def test_preamble_lists_helper_signature_with_docstring() -> None:
    @ai_function[str](code_execution_mode="local", code_executor_additional_imports=["numpy.*"])
    def f(helpers: Procedural, topic: str):
        """Work on {topic}."""

    thread = _thread_with_bound_args(
        f,
        {
            "helpers": (
                "def secret_greeting(name):\n"
                '    """Return the secret greeting for a person."""\n'
                "    return f'Zphqr, {name}!'\n"
            ),
            "topic": "cats",
            "_hidden": "x",
        },
    )
    preamble = thread._code_env_preamble(f.config)  # noqa: SLF001

    # Helper advertised by signature AND docstring — but not its body.
    assert "def secret_greeting(name):" in preamble
    assert "Return the secret greeting for a person." in preamble
    assert "Zphqr" not in preamble
    # Importable modules listed (both the extra and a built-in one).
    assert "numpy.*" in preamble
    assert "math" in preamble
    # Regular arg listed; private (_-prefixed) arg skipped.
    assert "topic" in preamble
    assert "_hidden" not in preamble


# ── Integration: the preamble (with docstring) reaches the emitted user turn ──


@pytest.mark.skipif(not _HAS_SMOLAGENTS, reason="LOCAL execution requires smolagents")
async def test_preamble_with_docstring_appears_in_message_user_event() -> None:
    """In LOCAL mode the preamble — including the helper docstring — is folded
    into the prompt's single MESSAGE_USER turn."""

    class Answer(BaseModel):
        answer: str = Field(description="the answer")

    @ai_function[Answer](code_execution_mode="local")
    def run_task(helpers: Procedural):
        """Use the helper to answer."""

    async with RuntimeHarness() as h:
        # The scripted model calls the executor with code that returns via
        # final_answer, so the cycle completes cleanly.
        model = ScriptedModel(
            [Turn(tool_calls=(("python_executor", {"code": "final_answer(answer=greet('x'))"}),))],
        )
        handle = await h.spawn(run_task.replace(model=model), thread_name="run_task")
        await handle.run(
            helpers=(
                "def greet(name):\n"
                '    """Greet a person warmly by name."""\n'
                "    return f'hello {name}'\n"
            )
        )

        user_events = [
            e for e in await h.events(handle.id, kinds=[EventKind.MESSAGE_USER]) if isinstance(e, MessageUserEvent)
        ]
        # A single prompt turn carries the task, the environment preamble, the
        # helper signature, AND its docstring.
        assert len(user_events) == 1
        text = user_events[0].text
        assert "Use the helper to answer." in text
        assert "python execution environment" in text
        assert "def greet(name):" in text
        assert "Greet a person warmly by name." in text
