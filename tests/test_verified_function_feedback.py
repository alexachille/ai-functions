"""Lean errors in tool arguments are attributed to their argument, without starting Lean."""

from ai_functions.experimental.verified.dsl.types import NAT
from ai_functions.experimental.verified.function._session import _slot_errors
from ai_functions.experimental.verified.lean.server import ElabResult, Message


def test_errors_are_attributed_to_their_argument_and_unplaced_errors_survive():
    slots = [("quantity", '"three"', NAT), ("discount", "missing", NAT)]
    result = ElabResult(
        False,
        messages=(
            Message("error", "wrong type", offset=5),
            Message("error", "unknown identifier", offset=10),
            Message("warning", "ignored warning", offset=5),
            Message("error", "outside either argument", offset=20),
        ),
    )
    text = _slot_errors(result, slots, [(0, 10), (10, 20)])
    quantity, discount = text.split("discount:")
    assert "quantity:" in quantity and "wrong type" in quantity and "unknown identifier" not in quantity
    assert "unknown identifier" in discount and "wrong type" not in discount
    assert "outside either argument" in text and "ignored warning" not in text
