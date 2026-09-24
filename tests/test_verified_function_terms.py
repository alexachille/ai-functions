"""Checked terms retain their helpers after the invocation ends."""

import gc

import pytest

from ai_functions.experimental import verified
from ai_functions.experimental.verified import LeanTerm
from ai_functions.experimental.verified.lean import LeanProject
from ai_functions.experimental.verified.lean.checker import CheckSpec, check_source
from ai_functions.testing import ScriptedModel, Turn

pytestmark = [pytest.mark.lean, pytest.mark.xdist_group("function_terms")]


async def test_function_term_retains_inputs_helpers_and_named_proof():
    async def synthesize():
        project = LeanProject(offline=True)
        project.add("def Contract (f : Int → Int) (answer final : Int) : Prop := ∀ x, f x = x + answer + final")
        contract = project.symbols.Contract
        model = ScriptedModel(
            [
                Turn(
                    tool_calls=(
                        (
                            "lean",
                            {
                                "code": "def add (x : Int) : Int := x + H.answer + H.final\n"
                                f"theorem correct : {contract.name} add H.answer H.final := by intro x; rfl"
                            },
                        ),
                    )
                ),
                Turn(tool_calls=(("lean_submit", {"answer": "A.add", "proof": "A.correct"}),)),
            ]
        )

        @verified.ai_function(contract=contract, model=model)
        def make(answer: int, final: int) -> LeanTerm:
            """Return an addition function with its proof."""

        term, certificate = await make.with_certificate(2, 3)
        assert term.certificate is certificate
        assert not term.certificate.observations and model.remaining_turns == 0
        return term, project

    term, project = await synthesize()
    gc.collect()
    # Recheck the artifact cold: neither the function nor the call's server survives.
    checked = check_source(
        project,
        term.certificate.artifact + f"\nexample : {term.declaration} 37 = 42 := rfl\n",
        CheckSpec(axioms=(term.declaration, term.proof_declaration)),
    )
    assert checked.ok, checked.errors
