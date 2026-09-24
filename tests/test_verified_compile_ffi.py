"""Native ABI, ownership, threading, and integer-representation regressions."""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import random
import struct
import sys
import threading

import pytest
from verified_model import (
    FLOAT_CLASS,
    INT_IDENTITY,
    MIXED,
    NINTH,
    SAME_LENGTH,
    Program,
    compile_fixture,
    model,
)

# The bridge is hand-written C that rarely changes; run these when touching it.
pytestmark = [
    pytest.mark.optional,
    pytest.mark.lean,
    pytest.mark.xdist_group("verified_contracts"),
]


def make_function(project, fn, contract, candidate, cache):
    return compile_fixture(contract, project, model=model(contract, candidate), cache_dir=cache, max_attempts=0)(fn)


def integer(value: int) -> int:
    """Return the integer unchanged."""


IDENTITY = Program(implementation="v0", proof="by intro v0 h; simp [{pre}, {post}, {implementation}]")


@pytest.fixture(scope="module")
async def integer_identity(tmp_path_factory, native_runtime, verified_contracts):
    fn = make_function(verified_contracts, integer, INT_IDENTITY, IDENTITY, tmp_path_factory.mktemp("integer"))
    await fn.compile()
    return fn


def test_integer_digits_cross_python_and_gmp_boundaries(integer_identity):
    fn = integer_identity
    randomizer = random.Random(7403)
    values = [0, 1, -1]
    for bits in (15, 29, 30, 31, 32, 59, 60, 63, 64, 65, 89, 90, 127, 128, 129, 1024, 20000, 100000):
        for number in (2**bits - 1, 2**bits, 2**bits + 1, randomizer.getrandbits(bits) | 1):
            values.extend((number, -number))
    for value in values:
        assert fn.run_sync(value) == value
    # The same primitive must preserve ownership on Python worker threads.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(fn.run_sync, values)) == values


@pytest.fixture(scope="module")
async def reverse_list(tmp_path_factory, native_runtime, verified_contracts):
    def reverse(values: list[int]) -> list[int]:
        """Reverse the sequence."""

    fn = make_function(
        verified_contracts,
        reverse,
        SAME_LENGTH,
        Program(implementation="List.reverse v0", proof="by intro v0 h; simp [{pre}, {post}, {implementation}]"),
        tmp_path_factory.mktemp("reverse"),
    )
    await fn.compile()
    return fn


def test_native_list_ownership_and_input_references(reverse_list):
    fn = reverse_list
    values = [-(2**20000), 2**64, 0, -(2**31), 2**20000 + 19]
    expected = values[::-1]
    # Account for the independent oracle's references before checking for leaks.
    references = [sys.getrefcount(item) for item in values]
    for _ in range(300):
        result = fn.run_sync(values)
        assert result == expected
        assert result is not values
        del result
    assert [sys.getrefcount(item) for item in values] == references
    assert fn.run_sync([]) == []
    large = list(range(10000))
    assert fn.run_sync(large) == large[::-1]
    assert large == list(range(10000))


async def test_async_arguments_are_frozen_before_yielding(reverse_list, monkeypatch):
    fn = reverse_list
    original = fn._invoke
    entered, release = threading.Event(), threading.Event()

    def delayed(values):
        entered.set()
        assert release.wait(10)
        return original(values)

    monkeypatch.setattr(fn, "_invoke", delayed)
    value = [2**20000, -7]
    expected = value[::-1]
    task = asyncio.create_task(fn(value))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        value[:] = [False, 3.5]
    finally:
        release.set()
    assert await task == expected


async def test_mixed_native_argument_registers(tmp_path, native_runtime, verified_contracts):
    def mixed(count: int, enabled: bool, threshold: float, values: list[int]) -> bool:
        """Check a mixed scalar/list condition."""

    fn = make_function(
        verified_contracts,
        mixed,
        MIXED,
        Program(
            implementation="v1 && decide (v0 = Int.ofNat v3.length) && Float.le (Float.ofBits 0) v2",
            proof="by intro v0 v1 v2 v3 h; simp [{pre}, {post}, {implementation}]",
        ),
        tmp_path,
    )
    await fn.compile()
    for count in (-1, 0, 2, 2**20000):
        for enabled in (False, True):
            for threshold in (-1.0, -0.0, 1.0, math.inf, math.nan):
                assert fn.run_sync(count, enabled, threshold, [3, 7]) is (enabled and count == 2 and threshold >= 0.0)


async def test_large_arity_and_consumed_unused_arguments(tmp_path, native_runtime, verified_contracts):
    def ninth(a: int, b: int, c: int, d: int, e: int, f: int, g: int, h: int, i: int) -> int:
        """Return the ninth argument."""

    fn = make_function(
        verified_contracts,
        ninth,
        NINTH,
        Program(
            implementation="v8", proof="by intro v0 v1 v2 v3 v4 v5 v6 v7 v8 h; simp [{pre}, {post}, {implementation}]"
        ),
        tmp_path,
    )
    await fn.compile()
    values = [2 ** (2000 + index) for index in range(9)]
    for _ in range(100):
        assert fn.run_sync(*values) == values[-1]


async def test_float_bit_patterns_through_native_calls(tmp_path, native_runtime, verified_contracts):
    def echo(value: float) -> float:
        """Preserve the float's value and classification."""

    fn = make_function(verified_contracts, echo, FLOAT_CLASS, IDENTITY, tmp_path)
    await fn.compile()
    randomizer = random.Random(629)
    bits = [0, 1, 2**63, 0x7FF0000000000000, 0xFFF0000000000000, 0x7FF0000000000001, 0xFFF8123456789ABC]
    bits.extend(randomizer.getrandbits(64) for _ in range(500))
    for pattern in bits:
        value = struct.unpack("=d", struct.pack("=Q", pattern))[0]
        output = fn.run_sync(value)
        expected = 0x7FF8000000000000 if math.isnan(value) else pattern
        assert struct.unpack("=Q", struct.pack("=d", output))[0] == expected


def test_bridge_rejects_signature_mismatch_and_bad_values(native_runtime, integer_identity):
    fn = integer_identity
    bridge = native_runtime.bridge()
    extension = ".dylib" if sys.platform == "darwin" else ".so"
    library = str(fn.artifact_dir / ("Verified" + extension))
    with pytest.raises(ImportError, match="types do not match"):
        bridge.load(library, bytes([2, 2]))
    direct = bridge.load(library, bytes([1, 1]))
    for bad in (True, 1.5, "1", [1]):
        with pytest.raises(TypeError, match="exact Python ints"):
            direct({"v0": bad})
    with pytest.raises(TypeError, match="Missing native argument"):
        direct({"wrong": 1})
    with pytest.raises(TypeError, match="Incorrect number"):
        direct({})
    assert direct({"v0": 2**20000}) == 2**20000
