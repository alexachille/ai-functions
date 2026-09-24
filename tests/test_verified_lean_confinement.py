"""Model-authored Lean is confined by Lean itself, on the parse tree and the elaborated environment.

Each test sends the model's text to a warm server as a block (or to the
checker's compile step) and checks the reply: rejected constructs are named
and never elaborate, allowed ones elaborate as usual.
"""

from __future__ import annotations

import pytest

from ai_functions.experimental.verified.lean.checker import CheckSpec, check_source
from ai_functions.experimental.verified.lean.server import LeanServer, Source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.lean,
    pytest.mark.xdist_group("init"),
]

REJECTED = "not permitted in model-authored Lean"


@pytest.fixture(scope="session")
def project(init_project):
    return init_project


@pytest.fixture(scope="module")
def server(project):
    with LeanServer(project, timeout=30) as server:
        yield server


def _touch(marker) -> str:
    return f'IO.Process.output {{cmd := "touch", args := #["{marker}"]}}'


@pytest.mark.parametrize(
    "attack",
    [
        "open Nat in #eval {io}",
        "def x := 1 #eval {io}",
    ],
    ids=["open-in", "same-line"],
)
def test_known_guard_bypasses_run_nothing_and_name_the_command(server, tmp_path, attack):
    marker = tmp_path / "pwned"
    result = server.probe(Source.model(attack.format(io=_touch(marker))))
    assert not result.ok
    assert not marker.exists()
    assert "`#eval`" in result.errors and "Lean.Parser.Command.eval" in result.errors
    assert REJECTED in result.raw["rejected"]


ATTACKS = [
    'notation "contract" => True',
    'infix:65 " +++ " => Nat.add',
    'infixl:65 " +++ " => Nat.add',
    'prefix:max "√" => Nat.succ',
    'postfix:max "!" => Nat.succ',
    'macro "contract" : term => `(True)',
    'syntax "contract" : term',
    'elab "contract" : term => pure default',
    "@[term_elab Lean.Parser.Term.app] def evil : Nat := 0",
    "@[«term_elab» Lean.Parser.Term.app] def evil : Nat := 0",
    "@[simp, command_elab Lean.Parser.Command.check] def evil : Nat := 0",
    "@[local macro Lean.Parser.Term.app] def evil : Nat := 0",
    "@[implemented_by Nat.succ] def evil (n : Nat) : Nat := n",
    "attribute [simp] Nat.add_comm",
    "initialize pure ()",
    "run_cmd pure ()",
    "theorem evil : True := by run_tac pure ()",
    "def evil : Nat := by_elab pure default",
    "def evil : Nat := unsafeBaseIO (pure 0)",
    "def evil : Nat := «unsafeBaseIO» (pure 0)",
    "def evil : Nat := open Nat in unsafeBaseIO (pure 0)",
    "def evil (n : Nat) : Int := unsafeCast n",
    "def evil : Nat := unsafe (unsafeCast 0)",
    "unsafe def evil : Nat := 0",
    "partial def evil (n : Nat) : Nat := evil n",
    "axiom evil : False",
    "opaque evil : Nat",
    "instance evil : Inhabited Nat := ⟨1⟩",
    "set_option debug.skipKernelTC true",
    "def evil : Nat := set_option maxRecDepth 1 in 0",
    "namespace Evil",
    "export Nat (succ)",
    "def _root_.evil : Nat := 0",
    'def evil : String := include_str "/etc/passwd"',
    "theorem evil : False := sorry",
    "theorem evil : 2 + 2 = 4 := by native_decide",
    "theorem evil : 2 + 2 = 4 := by decide +native",
    '#eval IO.println "ran"',
]


@pytest.mark.parametrize("attack", ATTACKS)
def test_confinement_rejects_extensions_io_and_escapes_however_placed(server, attack):
    # Alone, on one line after another command, and after `open … in`.
    for source in (attack, "def before : Nat := 1 " + attack, "open Nat in " + attack):
        result = server.probe(Source.model(source))
        assert not result.ok, source
        assert REJECTED in result.errors, (source, result.errors)


def test_harness_text_around_model_text_is_not_confined(server):
    # The harness writes axioms and `#check` itself; only the model's part is checked.
    block = Source("axiom harness : 1 = 1\n#check ") + Source.model("harness")
    assert server.probe(block).ok
    rejected = server.probe(Source("#check ") + Source.model("(unsafeCast 0 : Nat)"))
    assert not rejected.ok and REJECTED in rejected.errors


@pytest.mark.parametrize(
    "source",
    [
        "def two : Nat := 2\ntheorem two_eq : two = 2 := rfl",
        "abbrev Two : Nat := 2\nexample : Two = 2 := by decide",
        "structure Point where\n  x : Nat\n  y : Nat\nderiving Repr\ndef origin : Point := ⟨0, 0⟩",
        "inductive Color | red | green\ndef pick : Color → Nat\n  | .red => 0\n  | .green => 1",
        "@[simp] theorem add_zero' (n : Nat) : n + 0 = n := rfl\nexample (n : Nat) : n + 0 = n := by simp",
        "@[reducible] def three : Nat := 3\n@[local simp, irreducible] def four : Nat := 4",
        "theorem comm (a b : Nat) : a + b = b + a := by omega",
        "open Nat in\ndef next (n : Nat) : Nat := succ n",
        "variable (n : Nat) in\ntheorem self_eq : n = n := rfl",
        "private def hidden : Nat := 5",
        'def text : String := "#eval @[term_elab x] unsafeBaseIO notation"\n-- #eval unsafeIO\ndef six : Nat := 6',
        "def fib : Nat → Nat\n  | 0 => 0\n  | 1 => 1\n  | n + 2 => fib n + fib (n + 1)",
    ],
)
def test_ordinary_declarations_and_proofs_elaborate(server, source):
    result = server.probe(Source.model(source))
    assert result.ok, result.errors


def test_confined_blocks_report_every_command_s_diagnostics(server):
    result = server.probe(Source.model("def broken : Nat := true\ndef fine : Nat := 1"))
    assert not result.ok and "rejected" not in result.raw
    assert "Bool" in result.errors
    assert "2 : Nat" in server.probe(Source("#check ") + Source.model("(2 : Nat)")).stdout


def test_cold_compile_has_the_same_confinement(project, tmp_path):
    marker = tmp_path / "pwned"
    source = Source("theorem harness : 1 = 1 := rfl\n") + Source.model(f"open Nat in #eval {_touch(marker)}\n")
    result = check_source(project, source, CheckSpec(axioms=("harness",)))
    assert not result.ok
    assert not marker.exists()
    assert REJECTED in result.errors and "`#eval`" in result.errors
