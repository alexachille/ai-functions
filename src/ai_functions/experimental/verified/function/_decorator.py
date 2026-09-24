"""``verified.ai_function``: an AI function whose every result carries a cold-checked Lean proof."""

from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Callable, Sequence
from typing import Unpack, cast

from strands.tools import ToolProvider
from strands.tools.executors import SequentialToolExecutor
from strands.types.exceptions import MaxTokensReachedException
from strands.types.tools import AgentTool

from ....ai_thread import AIFunction
from ....ai_thread import ai_function as _ai_function
from ....ai_thread.config import CodeExecutionMode, ThreadMergedKwargs
from ....utils import run_blocking
from ..dsl import Definition
from ..dsl.types import render_type
from ..lean import LeanError, LeanProject, LeanSymbol
from ..lean.execution import run_in_thread
from ._session import ProofSession
from .tools import ObservationTool, _lean_tools
from .types import Certificate, LeanTerm

_PROTOCOL = """\
You must prove the result's Lean contract before this function can return.

The ledger below and the project source define the vocabulary and the contract. The
Python arguments are bound as transparent `H.<parameter>` definitions, a LeanTerm
argument as its Lean expression; use those names in computations and tool arguments.

Observation tools take Lean expressions at their declared types: a string argument is
Lean source with quotes, e.g. `"widget"`, or a ledger expression such as
`H.skus[0]!`. An observation returns a transparent value `H.price1` and an equation
`H.price1_spec` between the project's opaque function and that literal. Compute with
the value; rewrite with the equation in proofs. When an argument is an expression such
as `H.skus[0]!`, the tool also records `H.item1_spec_as_written`, the same equation
stated at that expression, so `rw` applies to the goal without unfolding the inputs.
Only observation tools produce Lean facts: the output of any other tool is not evidence.

`lean` appends definitions and theorems in namespace A; everywhere else (answers,
proofs, `lean_eval`, tool arguments) refer to them as `A.name`. `lean_eval` evaluates
a term or runs #check/#print commands. Tools in one message run in order. Write no
namespace or import commands, instances, axioms, unsafe code, or placeholders; the
harness owns namespace H. Mathlib is not available unless the project imports it: use
core tactics and lemmas such as simp, rw, rfl, decide, omega, `split` and
`Int.le_refl`, not `split_ifs` or bare `le_refl`. Native proof tactics (including
native_decide) are disabled. Inspect an unfamiliar name with `lean_eval("#check Name")`.

Finish with `lean_submit(answer, proof)`: the answer is a Lean expression and the
proof a Lean proof term (`by ...` or a theorem name) for the goal below. A failed
submission shows Lean's error and the exact theorem; a successful one is checked
again from scratch and returns the answer. Use lean_submit instead of FinalAnswer.
"""


class VerifiedFunction[**P, T]:
    """An AI function whose every result carries an accepted certificate."""

    def __init__(
        self,
        func: AIFunction[P, T],
        *,
        contract: LeanSymbol | Definition,
        tools: Sequence[ObservationTool] = (),
        judgments: Sequence[LeanSymbol] = (),
        timeout: float = 120.0,
    ) -> None:
        symbol = cast(LeanSymbol, contract.symbol) if isinstance(contract, Definition) else contract
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if any(s.project is not symbol.project for s in [*(t.symbol for t in tools), *judgments]):
            raise LeanError("The contract, tools and judgments must use the same LeanProject instance")
        self._fn = func
        self._contract = symbol
        self._tools = tuple(tools)
        self._judgments = tuple(judgments)
        self._timeout = timeout
        self._paired = False

    @property
    def name(self) -> str:
        return self._fn.name

    @property
    def contract(self) -> LeanSymbol:
        return self._contract

    @property
    def project(self) -> LeanProject:
        return self._contract.project

    @property
    def with_certificate(self) -> VerifiedFunction[P, tuple[T, Certificate]]:
        paired = copy.copy(self)
        paired._paired = True
        return cast(VerifiedFunction[P, tuple[T, Certificate]], paired)

    def replace(self, **kwargs: Unpack[ThreadMergedKwargs]) -> VerifiedFunction[P, T]:
        variant = copy.copy(self)
        variant._fn = self._fn.replace(**kwargs)
        return variant

    def _open(self, arguments: dict[str, object], opened: list[ProofSession]) -> AIFunction[P, T]:
        """Prepare the project, open the call's session and return the configured AI function."""
        self.project.prepare(timeout=self._timeout)
        symbols = [self._contract, *self._judgments, *(t.symbol for t in self._tools)]
        self.project.describe(*symbols)
        term = self._fn.output_type is LeanTerm
        session = ProofSession(
            self._contract,
            arguments,
            judgments=self._judgments,
            term_result=term,
            max_attempts=self._fn.config.max_attempts,
            timeout=self._timeout,
        )
        opened.append(session)
        tools = [*_lean_tools(session, judge=bool(self._judgments)), *(t.bind(session) for t in self._tools)]
        parts = [self._fn.config.system_prompt or "", _PROTOCOL]
        if term:
            parts.append(
                "Return mode: checked Lean term. Submit the expression itself, which may be a function. "
                f"The harness binds it as `def {session._answer} : {render_type(session.result_type)}` "
                "and proves the goal below for that binding; A helpers are retained. The answer must be "
                "executable: no noncomputable definitions or Classical.choose. The proof may reason classically."
            )
        else:
            parts.append(
                "Return mode: Python value. The answer must reduce to a literal at the contract's result type; "
                "the harness checks that the returned value is exactly the value proved."
            )
        if source := self.project.source(*symbols):
            parts.append(f"Project source relevant to the contract and tools:\n```lean\n{source}\n```")
        parts.append(f"Initial Lean ledger:\n```lean\n{session.ledger()}```")
        parts.append(f"Goal for the answer:\n```lean\n{session.goal('<answer>')}\n```")
        if self._judgments:
            parts.append(
                "Symbols you may judge with `lean_judge`: " + ", ".join(s.name for s in self._judgments) + ". "
                "Each reading needs closed arguments, a concrete value (a literal, or for a proposition or a theory "
                "type a term over the project's vocabulary) and a sentence of justification from the input; it "
                "cannot be revised. The tool returns the J.<name> axiom and, for expression arguments such "
                "as H.message, J.<name>_as_written stated at them, to rewrite the goal with."
            )
        return self._fn.replace(
            tools=[*self._fn.config.tools, *tools],
            post_conditions=[*self._fn.config.post_conditions, session.check_result],
            system_prompt="\n\n".join(parts),
            structured_output=True,
            result_tool="lean_submit",
            code_execution_mode=CodeExecutionMode.DISABLED,
            coordinator_tools_enabled=False,
            tool_executor=SequentialToolExecutor(),
            # The session enforces the attempt budget, so the AI function must not stop first.
            max_attempts=self._fn.config.max_attempts + 1,
        )

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        bound = inspect.signature(self._fn.prompt_fn).bind(*args, **kwargs)
        bound.apply_defaults()
        opened: list[ProofSession] = []
        agent: asyncio.Task[T] | None = None
        try:
            variant = await run_in_thread(self._open, dict(bound.arguments), opened)
            session = opened[0]

            async def run() -> T:
                handle = await variant._spawn_in_context()
                try:
                    while True:
                        try:
                            return await handle.run(*args, **kwargs)
                        except MaxTokensReachedException:
                            session.fail_attempt("The model exhausted its output-token limit before submitting.")
                finally:
                    await handle.terminate_now()

            agent = asyncio.create_task(run())
            # Strands reports a tool's exception to the model and the AI function retries a failed
            # result, so an aborted session would keep the agent running: cancel it instead.
            loop = asyncio.get_running_loop()
            session.on_abort(lambda: loop.call_soon_threadsafe(agent.cancel))
            try:
                answer = await agent
            except BaseException as exc:
                fatal = session._fatal
                current = asyncio.current_task()
                if fatal is None or fatal is exc or current is not None and current.cancelling():
                    raise
                raise fatal from None
            session.check_result(answer)
            assert session.certificate is not None
            return cast(T, (answer, session.certificate)) if self._paired else answer
        finally:
            if agent is not None:
                agent.cancel()
                # Tool threads drain before the cancelled agent task completes.
                await asyncio.gather(agent, return_exceptions=True)
            if opened:
                await run_in_thread(opened[0].close)

    def run_sync(self, *args: P.args, **kwargs: P.kwargs) -> T:
        return run_blocking(lambda: self(*args, **kwargs))


def ai_function[**P, T](
    *,
    contract: LeanSymbol | Definition,
    tools: Sequence[ObservationTool | AgentTool | ToolProvider | str] = (),
    judgments: Sequence[LeanSymbol] = (),
    timeout: float = 120.0,
    **config: Unpack[ThreadMergedKwargs],  # pyright: ignore[reportGeneralTypeIssues]  # `tools` is the keyword
) -> Callable[[Callable[P, T]], VerifiedFunction[P, T]]:
    """Build an AI function that must prove a result-first contract for every result.

    ``config`` configures the AI function as ``ai_functions.ai_function`` does. Observation
    tools in ``tools`` are bound to each call's proof session; the others pass through.
    """

    def decorate(fn: Callable[P, T]) -> VerifiedFunction[P, T]:
        ordinary = [t for t in tools if not isinstance(t, ObservationTool)]
        return VerifiedFunction(
            _ai_function(**(config | {"tools": ordinary}))(fn),
            contract=contract,
            tools=[t for t in tools if isinstance(t, ObservationTool)],
            judgments=judgments,
            timeout=timeout,
        )

    return decorate
