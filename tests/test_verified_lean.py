"""Project registration before preparation, and the warm server's lifecycle."""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from ai_functions.experimental.verified.lean import LeanError, LeanProject
from ai_functions.experimental.verified.lean.errors import LeanProjectStateError
from ai_functions.experimental.verified.lean.server import LeanServer
from ai_functions.experimental.verified.lean.toolchain import LeanConfig


def _template(path: Path) -> Path:
    path.mkdir()
    (path / "lakefile.toml").write_text('name = "fixture"\n[[lean_lib]]\nname = "Example"\n')
    (path / "lean-toolchain").write_text("leanprover/lean4:v4.33.1\n")
    (path / "Example.lean").write_text(
        "namespace Example\n"
        "def Contract (result : Int) (x : Int) : Prop := result = x + 1\n"
        "abbrev Count := Nat\n"
        "opaque observe (x : String) : Count\n"
        "def «odd-name» : Nat := 42\n"
        "end Example\n"
    )
    return path


def test_a_path_needs_imports_and_registration_runs_no_lean(tmp_path: Path) -> None:
    source = _template(tmp_path / "original")
    with pytest.raises(ValueError, match="imports"):
        LeanProject(source)
    project = LeanProject(source, imports=["Example"], toolchain=LeanConfig(cache_dir=str(tmp_path / "cache")))
    reference = project.symbols.Example.Contract
    assert reference.project is project
    project.add("-- Use the packaged predicate.\ndef Contract (n : Int) : Prop := Example.Contract n 1")
    project.add("def Wrapped (n : Int) : Prop := Contract n")
    assert project.symbols.Wrapped.name == "Wrapped"
    with pytest.raises(LeanProjectStateError):
        _ = project.fingerprint
    assert project.root.is_relative_to(tmp_path / "cache") and not project.root.exists()


@pytest.mark.lean
@pytest.mark.xdist_group("init")
def test_server_probes_are_isolated_and_a_dead_server_stays_closed(init_project) -> None:
    with LeanServer(init_project, timeout=30) as server:
        assert server.commit("def value : Nat := 42").ok
        assert server.probe("def speculative : Nat := 7").ok
        assert not server.probe("#check speculative").ok
        assert not server.commit("def broken : Nat := true").ok
        result, values = server.evaluate("", ["value"])
        assert result.ok and values == [42]
        assert server._session is not None
        os.killpg(server._session._proc.pid, signal.SIGKILL)
        with pytest.raises(LeanError):
            server.probe("#check value")
        with pytest.raises(LeanError, match="closed"):
            server.probe("#check value")
