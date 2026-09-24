"""Real proof/compiler/FFI tests with a deterministic model and no model service."""

from __future__ import annotations

import math

import pytest
from verified_model import CLAMP, MULTIPLY_ADD, Program, compile_fixture, fixture_project, model


def clamp(x: int, lo: int = 0, hi: int = 10) -> int:
    """Clamp x to the inclusive interval [lo, hi]."""


@pytest.mark.optional  # Fails only if the native build flags change.
@pytest.mark.lean
@pytest.mark.xdist_group("verified_contracts")
async def test_float_arithmetic_preserves_rounding_and_does_not_fuse_operations(
    tmp_path, native_runtime, verified_contracts
):
    def multiply_add_equals(a: float, b: float, c: float, expected: float) -> bool:
        """Compare a separately rounded multiply/add with the expected value."""

    candidate = Program(
        implementation="Float.beq (v0 * v1 + v2) v3",
        proof="by intro v0 v1 v2 v3 h; simp [{pre}, {post}, {implementation}]",
    )
    fn = compile_fixture(
        MULTIPLY_ADD, verified_contracts, model=model(MULTIPLY_ADD, candidate), cache_dir=tmp_path, max_attempts=0
    )(multiply_add_equals)
    assert await fn(1.0 + 2**-27, 1.0 - 2**-27, -1.0, 0.0) is True
    assert fn.run_sync(math.inf, 0.0, 1.0, math.nan) is False
    assert fn.run_sync(1e308, 2.0, 0.0, math.inf) is True


def test_original_function_attributes_cannot_override_compilation_state(monkeypatch):
    monkeypatch.setattr(clamp, "_artifact", object(), raising=False)
    fn = compile_fixture(CLAMP, fixture_project(), model=model(CLAMP), max_attempts=0)(clamp)
    assert not fn.is_compiled
    assert fn.__name__ == "clamp"
