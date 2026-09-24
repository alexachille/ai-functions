"""The Python/Lean type model: structure and the value codec."""

from __future__ import annotations

import pytest

from ai_functions.experimental.verified.dsl.types import INT, NAT, Const, Prod, Unsupported
from ai_functions.experimental.verified.lean.types import EncodingError, decode, encode, type_from_json


def _const(name: str) -> dict:
    return {"k": "const", "n": name, "a": []}


def _prod(left: dict, right: dict) -> dict:
    return {"k": "prod", "l": left, "r": right}


INT_JSON = _const("Int")


def test_right_nested_product_is_one_flat_type() -> None:
    # Lean's `Int × Int × Int` is `Int × (Int × Int)`; it arrives flat, once.
    triple = type_from_json(_prod(INT_JSON, _prod(INT_JSON, INT_JSON)))
    assert triple == Prod((INT, INT, INT))
    assert Prod((INT, Prod((INT, INT)))) == triple
    assert encode((1, -2, 3), triple) == "(1, (-2), 3)"
    # Lean's closed-term JSON for products is nested pairs.
    assert decode([1, [-2, 3]], triple) == (1, -2, 3)


def test_left_nested_product_stays_distinct() -> None:
    nested = type_from_json(_prod(_prod(INT_JSON, INT_JSON), INT_JSON))
    assert nested == Prod((Prod((INT, INT)), INT))
    assert nested != Prod((INT, INT, INT))
    assert encode(((1, 2), 3), nested) == "((1, 2), 3)"
    assert decode([[1, 2], 3], nested) == ((1, 2), 3)
    with pytest.raises(EncodingError):
        encode((1, 2, 3), nested)
    with pytest.raises(EncodingError):
        decode([1, [2, 3]], nested)


def test_mismatch_errors_name_the_lean_type_and_slot() -> None:
    with pytest.raises(EncodingError, match="expected Nat for parameter x") as caught:
        encode(-1, NAT, slot="parameter x")
    assert "Const(" not in str(caught.value)
    with pytest.raises(EncodingError, match=r"expected List Nat, got 'a'"):
        encode("a", Const("List", (NAT,)))


@pytest.mark.parametrize("value,lean_type", [(True, INT), (-1, NAT), (1.25, Const("Float"))])
def test_codec_rejects_values_outside_its_supported_type(value, lean_type):
    with pytest.raises(EncodingError):
        encode(value, lean_type)


def test_unsupported_replaces_none() -> None:
    assert type_from_json(None, "Fin 3") == Unsupported("Fin 3")
    assert type_from_json(_prod(INT_JSON, None), "Int × Fin 3") == Unsupported("Int × Fin 3")
