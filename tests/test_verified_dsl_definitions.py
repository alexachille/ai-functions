"""Definition composition and the errors of registration."""

import inspect

import pytest

from ai_functions.experimental.verified.dsl import assume, every
from ai_functions.experimental.verified.dsl.errors import NotComputable, TranslationError
from ai_functions.experimental.verified.lean import LeanProject

RATE = 3
project = LeanProject(offline=True)


@project.function
def scaled(x: int) -> int:
    return x * RATE


@project.proposition
def scaling(x: int, result: int) -> bool:
    return result == scaled(x)


@project.proposition
def scaling_everywhere(k: int) -> bool:
    return all(scaling(x, x * k) for x in every(int))


@project.proposition
def bounded(values: list[int], limit: int) -> bool:
    assume(limit >= 0)
    for value in values:
        if value >= 0:
            assert value <= limit
    assert limit >= 0


@project.proposition
def checked_ratio(a: int, b: int) -> bool:
    assert a >= 0
    assume(b != 0)
    assert a // b <= a


def test_live_calls_run_the_python_body():
    assert scaled(4) == 12
    assert scaling(4, 12) is True
    assert scaling(4, 13) is False
    assert bounded([1, -3, 4], 4)
    assert not bounded([5], 4)
    assert not bounded([-3, 5], 4)
    # A false assumption holds vacuously, but an assert before it still refutes.
    assert bounded([100], -1) is True
    assert checked_ratio(-1, 0) is False
    # The assumption guards the rest of the block, so `4 // 0` never runs.
    assert checked_ratio(4, 0) is True
    assert checked_ratio(4, 2) is True
    with pytest.raises(NotComputable, match="every value"):
        scaling_everywhere(3)


def test_translation_errors_fail_at_decoration_and_name_their_cause():
    # In Python a false `assume` ends the whole call, so it matches Lean only at the top level.
    def looped(values: list[int], limit: int) -> bool:
        for value in values:
            assume(value >= 0)
            assert value <= limit

    lines, start = inspect.getsourcelines(looped)
    line = start + next(i for i, text in enumerate(lines) if "assume(" in text)
    with pytest.raises(TranslationError, match=rf"test_verified_dsl_definitions\.py:{line}: .*`if p: rest`"):
        project.proposition(name="looped")(looped)

    with pytest.raises(TranslationError, match="one return statement"):

        @project.function(name="loop")
        def loop(x: int) -> int:
            while x:
                x -= 1
            return x

    with pytest.raises(TranslationError, match="annotation"):

        @project.function(name="missing")
        def missing(x):
            return x

    def ordinary(x: int) -> int:
        return x + 1

    with pytest.raises(TranslationError, match="decorated"):

        @project.function(name="invalid")
        def invalid(x: int) -> int:
            return ordinary(x)

    with pytest.raises(TranslationError, match=r"test_verified_dsl_definitions\.py:\d+: later is not bound"):

        @project.function(name="early")
        def early(x: int) -> int:
            return later(x)  # noqa: F821

    other = LeanProject(offline=True)
    with pytest.raises(TranslationError, match="another project"):

        @other.function(name="foreign")
        def foreign(x: int) -> int:
            return scaled(x)

    with pytest.raises(TranslationError, match="name="):
        project.proposition(ordinary)
    for name in ("AIFunctions.Ledger.helper", "H.message", "«A».helper", "J.fake"):
        with pytest.raises(TranslationError, match="lies under"):
            project.function(name=name)(scaled.fn)
