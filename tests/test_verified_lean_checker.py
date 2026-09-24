"""The trusted cold checker rejects hand-written artifacts that differ from what the harness intended."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ai_functions.experimental.verified.lean.checker import (
    CheckSpec,
    NativeAudit,
    check_module,
    compile_module,
)
from ai_functions.experimental.verified.lean.toolchain import cache_path

pytestmark = [
    pytest.mark.integration,
    pytest.mark.lean,
    pytest.mark.xdist_group("init"),
]

FIXED = """\
namespace Checked
opaque price : String → Nat
def Contract (result x : Nat) : Prop := result = price "widget" + x
end Checked
"""
FINAL = 'theorem H.final : Checked.Contract 8 H.x := show 8 = Checked.price "widget" + H.x from H.price1_spec ▸ rfl'

# Laid out the way CertifiedRun renders a run: the fixed declarations,
# bindings, a provenance fact, and the final theorem.
ARTIFACT = (
    FIXED
    + """\
namespace H
def x : Nat := 1
def price1 : Nat := 7
axiom price1_spec : Checked.price "widget" = (7 : Nat)
end H
"""
    + FINAL
    + "\n"
)

# What the harness intended, as Lean text the checker elaborates without the
# artifact: the fixed declarations again, then one twin per harness declaration.
EXPECTED = (
    FIXED
    + """\
def H.x : Nat := 1
def H.price1 : Nat := 7
axiom H.price1_spec : Checked.price "widget" = (7 : Nat)
axiom H.final : Checked.Contract 8 H.x
"""
)
SPEC = CheckSpec(expected=EXPECTED, axioms=("H.final",))


@pytest.fixture(scope="session")
def project(init_project):
    return init_project


@pytest.fixture(scope="module")
def intended_artifact(project, tmp_path_factory):
    directory = tmp_path_factory.mktemp("intended")
    lake, cache = project.prepare().lake, cache_path(project.toolchain)
    compiled = compile_module(lake, cache, ARTIFACT, directory, timeout=60)
    assert compiled.ok, compiled.errors
    return lake, cache, directory


def test_intended_artifact_is_accepted_with_its_cold_inventory(project, intended_artifact):
    result = check_module(*intended_artifact, replace(SPEC, imports=project.imports), timeout=60)
    assert result.ok, result.errors
    assert result.axioms == {"H.final": ("H.price1_spec",)}


@pytest.mark.parametrize(
    ("old", "new", "diagnostic"),
    [
        ("H.final : Checked.Contract 8 H.x", "H.final : True", "H.final does not have the type"),
        ("def H.price1 : Nat := 7", "def H.price1 : Nat := 8", "H.price1 does not have the value"),
        (
            'H.price1_spec : Checked.price "widget" =',
            "H.price1_spec : ∀ s, Checked.price s =",
            "H.price1_spec does not have the type",
        ),
        ('Prop := result = price "widget" + x', "Prop := True", "Checked.Contract does not have the value"),
    ],
    ids=["goal", "value", "axiom-type", "contract"],
)
def test_artifact_must_match_the_expected_declarations(project, intended_artifact, old, new, diagnostic):
    # The comparator needs one compiled artifact, not a compilation for every mismatch.
    assert old in EXPECTED
    spec = replace(SPEC, expected=EXPECTED.replace(old, new), imports=project.imports)
    result = check_module(*intended_artifact, spec, timeout=60)
    assert not result.ok and diagnostic in result.errors


NATIVE = """\
namespace Verified
def answer (value : Int) : Int := value + 1
def answer' (value : Int) : Int := value + 1
theorem answer_correct : ∀ value, answer value = value + 1 := fun _ => rfl
@[export ai_verified_entry]
def nativeEntry (_token : UInt8) (v0 : Int) : Int := answer v0
end Verified
"""


@pytest.mark.parametrize(
    ("injected", "diagnostic"),
    [
        ("initialize side : Nat ← pure 0", "module initializer"),
        ("@[csimp] theorem answer_eq : @Verified.answer = @Verified.answer' := rfl", "compiler substitution"),
        ('def unused (value : Int) := dbgTrace "must not execute" (fun _ => value)', "runtime effect"),
    ],
    ids=["initialize", "csimp", "effect"],
)
def test_native_audit_reads_the_module_s_own_compiler_attributes(
    project, trusted_lean_modules, tmp_path, injected, diagnostic
):
    # The audit runs on the replayed module, where its declarations are local:
    # each attribute must still be seen, read from the module's object file.
    lake = project.prepare().lake
    cache = cache_path(project.toolchain)
    assert compile_module(lake, cache, NATIVE + injected + "\n", tmp_path, "Verified", timeout=60).ok
    audit = NativeAudit("Verified.answer", "Verified.answer_correct", trusted_lean_modules, ())
    result = check_module(lake, cache, tmp_path, CheckSpec(native_audit=audit, imports=()), "Verified", timeout=60)
    assert not result.ok and diagnostic in result.errors


def test_checker_rejects_changed_goal_meaning_without_confinement(project, tmp_path):
    fixed = "def Identity (result x : Nat) : Prop := result = x\n"
    goal = "_root_.Identity 999 1"
    source = (
        fixed
        + 'namespace A\nnotation "_root_.Identity" => fun _ _ => True\nend A\n'
        + f"theorem H.final : {goal} := trivial\n"
    )
    lake, cache = project.prepare().lake, cache_path(project.toolchain)
    # The false claim must elaborate first: rejection by the guard would not test the cold boundary.
    compiled = compile_module(lake, cache, source, tmp_path, timeout=60)
    assert compiled.ok, compiled.errors
    spec = CheckSpec(expected=fixed + f"axiom H.final : {goal}", axioms=("H.final",), imports=())
    checked = check_module(lake, cache, tmp_path, spec, timeout=60)
    assert not checked.ok and "H.final does not have the type" in checked.errors
