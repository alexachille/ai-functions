"""Project fingerprints and an imported project with DSL registrations."""

from pathlib import Path

import pytest

from ai_functions.experimental.verified.lean import LeanProject
from ai_functions.experimental.verified.lean.server import LeanServer


def _library(root: Path, code: str) -> Path:
    root.mkdir()
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.33.1\n")
    (root / "lakefile.toml").write_text('name = "fixture"\n[[lean_lib]]\nname = "Contracts"\n')
    (root / "Contracts.lean").write_text(code)
    return root


def test_fingerprint_covers_toolchain_imports_inline_code_and_files(tmp_path):
    from ai_functions.experimental.verified.lean._workspace import fingerprint

    source, build = tmp_path / "source", tmp_path / "build"
    (source / ".lake").mkdir(parents=True)
    build.mkdir()
    inputs = dict(identity="lean-version", imports=(), prelude="def value := 1")
    original = fingerprint(source, build, **inputs)
    for change in ({"identity": "other-version"}, {"imports": ("Other",)}, {"prelude": "def value := 2"}):
        assert fingerprint(source, build, **(inputs | change)) != original
    (source / ".lake" / "Output.olean").write_text("build output")
    assert fingerprint(source, build, **inputs) == original
    (source / "Module.lean").write_text("def value := 1")
    first = fingerprint(source, build, **inputs)
    assert first != original
    (source / "Module.lean").write_text("def value := 2")
    second = fingerprint(source, build, **inputs)
    assert second != first
    (build / "lake-manifest.json").write_text("{}")
    assert fingerprint(source, build, **inputs) != second


@pytest.mark.lean
def test_imported_and_inline_dsl_definitions_prove_their_contract(tmp_path: Path) -> None:
    source = _library(
        tmp_path / "source",
        "def Pricing.Increment (result x : Int) : Prop := result = x + 1\n"
        "def Pricing.double (x : Int) : Int := x * 2\n",
    )
    before = {path: path.read_bytes() for path in source.rglob("*")}
    project = LeanProject(source, imports=("Contracts",), offline=True)

    @project.function(name="plan_base")
    def plan_base(x: int) -> int:
        return x + 1

    project.add("def RawHelper (x : Int) : Int := Pricing.double (plan_base x)")

    @project.external(project.symbols.RawHelper)
    def raw_helper(x: int) -> int:
        return (x + 1) * 2

    @project.function(name="Plan.derived")
    def derived(x: int) -> int:
        return raw_helper(x) + 1

    @project.proposition(name="doubled")
    def doubled(result: int, x: int) -> bool:
        return result == 2 * x

    with LeanServer(project) as server:
        checked = server.probe(
            f"example : Pricing.Increment ({derived.symbol.name} 2) 6 := rfl\n"
            f"example : {doubled.symbol.name} 4 2 := rfl\n"
        )
        assert checked.ok, checked.errors
    assert project.symbols.Pricing.Increment.info.module == "Contracts"
    assert set(project.prepare().fixed_declarations) == {"plan_base", "RawHelper", "Plan.derived", "doubled"}
    assert {path: path.read_bytes() for path in source.rglob("*")} == before
