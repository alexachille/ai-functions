"""Python/Lean agreement for operators, scope, and assumptions."""

import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ai_functions.experimental.verified.dsl import assume, every
from ai_functions.experimental.verified.dsl.types import PROP, Arrow
from ai_functions.experimental.verified.lean import LeanProject
from ai_functions.experimental.verified.lean.execution import run_command

project = LeanProject(offline=True)


def _is_prop(definition):
    ty = definition.type
    while isinstance(ty, Arrow):
        ty = ty.codomain
    return ty == PROP


@project.proposition
def ordered(a: int, b: int) -> bool:
    return a <= b


@project.proposition
def shifted(x: int, y: int) -> bool:
    z = x + 1
    assume(y >= 0)
    assert z + y > x


@project.function
def positive(x: int) -> bool:
    return x > 0


@project.proposition
def all_positive(n: int) -> bool:
    return all(positive(i) for i in range(1, n))


@project.proposition
def capped(x: int, cap: int) -> bool:
    return (x if x <= cap else cap) <= cap


@project.proposition
def dense(lo: int, hi: int) -> bool:
    return lo < hi or not any(lo < m < hi for m in every(int))


@project.proposition
def starts(n: int) -> bool:
    return any(i == n for i in range(n, 10))


@project.proposition
def gap(_q1_x: int) -> bool:
    return all(x > _q1_x for x in range(_q1_x + 1, _q1_x + 3))


@project.proposition
def rebinding(x: int) -> bool:
    y = x + 1
    for x in range(0, 2):
        assert y > x


def _run_lean(lean_tools, tmp_path, source):
    """Elaborate ``source`` against core Lean only; this takes about a second."""
    path = tmp_path / "Check.lean"
    path.write_text(source)
    result = run_command(
        [str(lean_tools.lean), path.name],
        cwd=tmp_path,
        environment=lean_tools.environment(),
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    return result.returncode, output, [line for line in output.splitlines() if ": error" in line]


# One definition per row of the operator table, in the sort each lowers to.
# Python floors ``//`` and ``%``; Lean's ``Int`` ``/`` and ``%`` don't, so the
# negative operands are where a wrong mapping shows.


@project.function
def floor_div(a: int, b: int) -> int:
    return a // b


@project.function
def modulo(a: int, b: int) -> int:
    return a % b


@project.function
def ring(a: int, b: int, c: int) -> int:
    return a - b * c + -(a // c) * (b % c)


@project.function
def ordering(a: int, b: int, c: int) -> bool:
    return a < b <= c or a == c or (b > c and a >= b and a != c)


@project.function
def connectives(p: bool, q: bool, r: bool) -> bool:
    return (p and not q) or (r and not p)


@project.proposition
def propositional(a: int, b: int, c: int) -> bool:
    return (a < b and not b == c) or not (a >= c or b <= c)


@project.function
def none_divisible(lo: int, hi: int, k: int) -> bool:
    return all(i % k != 0 for i in range(lo, hi))


@project.proposition
def some_quotient(lo: int, hi: int, k: int) -> bool:
    return any(i // k == lo for i in range(lo, hi))


@project.proposition
def nonzero_above(xs: list[int], t: int) -> bool:
    return all(x > t for x in xs if x != 0)


@project.function
def some_multiple(xs: list[int], t: int) -> bool:
    return any(x % t == 0 for x in xs)


@project.proposition
def division_law(a: int, b: int) -> bool:
    assume(b != 0)
    q = a // b
    assert q * b + a % b == a
    assert a % b == 0 or (a % b > 0) == (b > 0)


small = st.integers(-30, 30)
nonzero = small.filter(lambda n: n != 0)
lists = st.lists(small, max_size=6)
DIFFERENTIAL = {
    floor_div: st.tuples(small, nonzero),
    modulo: st.tuples(small, nonzero),
    ring: st.tuples(small, small, nonzero),
    ordering: st.tuples(small, small, small),
    connectives: st.tuples(st.booleans(), st.booleans(), st.booleans()),
    propositional: st.tuples(small, small, small),
    none_divisible: st.tuples(small, small, nonzero),
    some_quotient: st.tuples(small, small, nonzero),
    nonzero_above: st.tuples(lists, small),
    some_multiple: st.tuples(lists, nonzero),
    division_law: st.tuples(small, small),
}


def _samples(strategy, count):
    """Draw Hypothesis examples in Python, so that one Lean run checks all of them."""
    found = []

    @settings(max_examples=count, database=None, deadline=None, suppress_health_check=list(HealthCheck))
    @given(strategy)
    def collect(args):
        found.append(args)

    collect()
    return found


def _literal(value):
    if type(value) is bool:
        return str(value).lower()
    if type(value) is list:
        return f"([{', '.join(map(str, value))}] : List Int)"
    return f"({value} : Int)"


@pytest.mark.lean
def test_python_and_lean_agree_on_operators_scope_and_assumptions(lean_tools, tmp_path):
    cases = {
        starts: [(3,), (9,), (12,)],
        gap: [(0,), (-5,)],
        rebinding: [(0,), (1,)],
        ordered: [(1, 2), (2, 1)],
        all_positive: [(0,), (4,)],
        capped: [(3, 5), (7, 5)],
        shifted: [(0, -1), (-3, 0), (3, 2)],
        **{definition: _samples(strategy, 25) for definition, strategy in DIFFERENTIAL.items()},
    }
    for definition in (floor_div, modulo):
        cases[definition] += [(-7, 3), (7, -3), (-7, -3), (0, -3)]
    source = "".join(definition.source + "\n\n" for definition in (positive, dense, *cases))
    checks = {}
    for definition, arguments in cases.items():
        name = definition.__name__
        for args in arguments:
            applied = " ".join([name, *map(_literal, args)])
            value = definition(*args)
            if _is_prop(definition):
                goal, tactic = (applied if value else f"¬ ({applied})"), f"unfold {name}; decide"
            else:
                goal, tactic = f"{applied} = {_literal(value)}", "decide"
            source += f"example : {goal} := by {tactic}\n"
            checks[source.count("\n")] = f"{name}{args} is {value!r} in Python"
    returncode, output, errors = _run_lean(lean_tools, tmp_path, source)
    lines = sorted({int(line) for line in re.findall(r"Check\.lean:(\d+):\d+: error", output)})
    assert returncode == 0 and not errors, "\n".join(checks.get(line, f"line {line}") for line in lines) + "\n" + output
