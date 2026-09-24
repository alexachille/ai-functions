"""Actual Lean certification; only model responses are scripted."""

from __future__ import annotations

import asyncio
import io
from contextlib import contextmanager

import pytest
from rich.console import Console

from ai_functions import scope
from ai_functions.experimental import verified
from ai_functions.experimental.verified import ContractNotProved, tool
from ai_functions.experimental.verified.function._session import ProofSession
from ai_functions.experimental.verified.lean import LeanError
from ai_functions.testing import ScriptedModel, Turn

pytestmark = [
    pytest.mark.integration,
    pytest.mark.lean,
    pytest.mark.xdist_group("verified_pricing"),
]


@contextmanager
def open_session(session):
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def project(pricing_project):
    return pricing_project


def basket_function(project, model):
    @tool(project.symbols.Pricing.priceCents, stem="price")
    def lookup_price(sku: str) -> int:
        """Read the unit price."""
        return {"widget": 1299, "gizmo": 450}[sku]

    @verified.ai_function(
        contract=project.symbols.Pricing.IsTotalFor, tools=[lookup_price], model=model, max_attempts=1
    )
    def basket_total(basket: list[tuple[str, int]]) -> int:
        """Compute the basket total."""

    return basket_total


BASKET_PROOF = "by simp [Pricing.IsTotalFor, H.basket, Pricing.lineCents, H.price1_spec, H.price2_spec]"


def test_dynamic_tools_retry_and_certificate(project, tmp_path):
    model = ScriptedModel(
        [
            Turn(
                tool_calls=(
                    ("lookup_price", {"sku": '"missing"'}),
                    ("lookup_price", {"sku": "H.basket[0]!.1"}),
                    ("lookup_price", {"sku": "H.basket[1]!.1"}),
                    ("lean_submit", {"answer": "1", "proof": BASKET_PROOF}),
                )
            ),
            Turn(tool_calls=(("lean_submit", {"answer": "4797", "proof": BASKET_PROOF}),)),
        ]
    )
    fn = basket_function(project, model)
    total, certificate = fn.with_certificate.run_sync([("widget", 3), ("gizmo", 2)])
    assert total == 4797
    assert {"H.price1_spec", "H.price2_spec"} <= set(certificate.axioms)
    assert set(certificate.used) == {"H.price1", "H.price2"}
    assert certificate.project_fingerprint == project.fingerprint
    path = certificate.write(tmp_path / "replay.lean")
    assert path.read_text() == certificate.artifact
    assert model.remaining_turns == 0


def test_judgments_are_transactional_and_certificate_reports_only_used_evidence(project):
    arguments = {"message": "Please book four working days"}
    with open_session(ProofSession(project.days_contract, arguments, judgments=[project.days])) as session:
        with pytest.raises(ContractNotProved, match="lean_submit"):
            session.check_result(4)
        days, reason = project.days.name, "The message explicitly requests four working days"
        assert not session.judge(days, ["H.message"], "4", "four days").ok
        assert not session.judge(days, ["H.missing"], "4", reason).ok
        assert session.observations == ()
        assert session.judge(days, ["H.message"], "4", reason).ok
        assert session.judge(days, ["H.message"], "4", reason).ok
        assert not session.judge(days, ["H.message"], "5", reason).ok
        assert len(session.observations) == 1
        # Keep an unused tool observation and an unused interpretation in the full trace.
        price = project.symbols.Pricing.priceCents
        assert session.observe(price, ["widget"], 7, origin="catalog", stem="price").ok
        assert session.judge(days, ['"unused"'], "0", "This input does not request any days off").ok
        assert session.submit("4", "by exact J.days1.symm").ok
        with pytest.raises(ContractNotProved, match="wrong Lean"):
            session.check_result(True)
        with pytest.raises(ContractNotProved, match="certificate proves"):
            session.check_result(5)
        certificate = session.certificate
        assert "J.days1" in certificate.axioms and "H.price1_spec" not in certificate.axioms
        assert set(certificate.used) == {"J.days1"} and len(certificate.observations) == 3
        assert certificate.used["J.days1"].kind == "judgment"
        assert certificate.used["J.days1"].arguments == ('"Please book four working days"',)
        output = io.StringIO()
        certificate.summary(Console(file=output, width=200))
        assert "J.days1" in output.getvalue() and "H.price1" not in output.getvalue()


async def test_concurrent_calls_share_project_but_not_ledger_and_keep_ambient_scope(project):
    def identity(value):
        @verified.ai_function(
            contract=project.identity,
            model=ScriptedModel([Turn(tool_calls=(("lean_submit", {"answer": str(value), "proof": "rfl"}),))]),
        )
        def result(ignored: bool = True, x: int = value) -> int:
            """Return x."""

        return result

    functions = [identity(value) for value in (3, 7)]
    async with scope():
        results = await asyncio.gather(*(fn.with_certificate() for fn in functions))
    assert [(value, certificate.answer) for value, certificate in results] == [(3, 3), (7, 7)]
    assert results[0][1].artifact != results[1][1].artifact


async def test_tool_conflict_is_fatal_to_the_agent(project):
    count = 0

    @tool(project.symbols.Pricing.priceCents)
    def changing_price(sku: str) -> int:
        """Read a changing observation."""
        nonlocal count
        count += 1
        return count

    model = ScriptedModel(
        [
            Turn(
                tool_calls=(
                    ("changing_price", {"sku": '"widget"'}),
                    ("changing_price", {"sku": '"widget"'}),
                    ("lean_submit", {"answer": "1", "proof": "trivial"}),
                )
            )
        ]
    )

    @verified.ai_function(contract=project.truth, tools=[changing_price], model=model)
    def answer() -> int:
        """Return one."""

    with pytest.raises(LeanError, match="Conflicting observations"):
        await answer()
