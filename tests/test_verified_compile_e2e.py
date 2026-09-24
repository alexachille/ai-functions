"""Native compilation succeeds after a retry; its final check independently rejects tampering."""

from dataclasses import replace

import pytest

from ai_functions.experimental.verified.compile import CompilerError, ai_compile, compiler
from ai_functions.experimental.verified.compile.signature import signature
from ai_functions.experimental.verified.function._session import ProofSession
from ai_functions.testing import ScriptedModel, Turn

pytestmark = [pytest.mark.lean, pytest.mark.xdist_group("compile_fixture")]


def adjusted_total(base: int, enabled: bool, values: list[int]) -> int:
    """Add the values to base when enabled."""


def reference(base: int, enabled: bool, values: list[int]) -> int:
    return base + sum(values) if enabled else base


def submit(answer: str) -> Turn:
    return Turn(
        tool_calls=(
            (
                "lean_submit",
                {"answer": answer, "proof": "by intro base enabled xs; simp [Fixture.Contract, A.total_eq]"},
            ),
        )
    )


async def test_failed_then_correct_submission_compiles_and_runs(tmp_path, native_runtime, compile_project):
    model = ScriptedModel(
        [
            Turn(
                tool_calls=(
                    (
                        "lean",
                        {
                            "code": "abbrev UInt8 := String\nabbrev Int := Bool\n"
                            "def total (acc : _root_.Int) : List _root_.Int → _root_.Int\n"
                            "| [] => acc\n| x :: xs => total (acc + x) xs\n"
                            "theorem total_eq (acc : _root_.Int) (xs : List _root_.Int) :\n"
                            "  total acc xs = xs.foldl (· + ·) acc := by\n"
                            "  induction xs generalizing acc <;> simp [total, List.foldl, *]"
                        },
                    ),
                )
            ),
            # Ignores `enabled`, so the proof fails and the agent must resubmit.
            submit("fun base _ xs => base + A.total 0 xs"),
            submit("fun base enabled xs => if enabled then base + A.total 0 xs else base"),
        ]
    )
    fn = ai_compile(contract=compile_project.symbols.Fixture.Contract, model=model, cache_dir=tmp_path, max_attempts=1)(
        adjusted_total
    )

    big = 2**20000
    cases = [(0, True, []), (5, False, [1, 2]), (-3, True, [1, -2, 3]), (big, True, [big, -1]), (-big, False, [big])]
    assert [await fn(*case) for case in cases] == [reference(*case) for case in cases]
    assert model.remaining_turns == 0
    assert fn.run_sync(1, enabled=True, values=list(range(1000))) == 1 + sum(range(1000))


async def test_final_native_check_rejects_tampered_certified_proof(tmp_path, native_runtime, compile_project):
    contract = compile_project.symbols.Fixture.Contract
    session = ProofSession(contract, {}, term_result=True)
    try:
        result = session.submit(
            "fun base enabled xs => if enabled then base + xs.foldl (· + ·) 0 else base",
            "by intro base enabled xs; exact rfl",
        )
        assert result.ok, result.message
        term = session.result
    finally:
        session.close()
    source = term.source
    tampered = replace(term, source=replace(source, text=source.text.replace("base +", "base -")))
    assert tampered.source.text != source.text
    with pytest.raises(CompilerError, match="Lean rejected"):
        compiler.build(native_runtime, signature(adjusted_total), contract, tampered, tmp_path, 120)
