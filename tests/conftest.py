"""Shared fixtures for comprehensive unit tests.

This module provides common fixtures used across all test modules.
"""

import os
import shutil
import subprocess
from pathlib import Path

import platformdirs
import pytest


@pytest.fixture(scope="session", autouse=True)
def lean_cache():
    """Keep compiled bridges and support executables in a persistent test-only cache.

    Entries are keyed by toolchain and source identity, so reusing them across
    runs is safe and saves rebuilding them on every run.
    """
    previous = os.environ.get("AI_FUNCTIONS_LEAN_CACHE_DIR")
    if previous is None:
        os.environ["AI_FUNCTIONS_LEAN_CACHE_DIR"] = platformdirs.user_cache_dir("ai_functions-tests")
    yield
    if previous is None:
        os.environ.pop("AI_FUNCTIONS_LEAN_CACHE_DIR", None)


@pytest.fixture(scope="session")
def lean_tools(lean_cache):
    """Resolve Lean without requiring the C bridge or a native SDK."""
    from ai_functions.experimental.verified.lean.toolchain import LeanConfig

    return LeanConfig().setup(offline=True)


@pytest.fixture(scope="session")
def trusted_lean_modules(lean_tools):
    """Core modules the native audit permits as executable dependencies."""
    core = lean_tools.root / "lib" / "lean"
    return frozenset(".".join(path.relative_to(core).with_suffix("").parts) for path in core.rglob("*.olean"))


@pytest.fixture(scope="session")
def native_runtime(lean_cache):
    """Run real Lean tests offline, optionally requiring native prerequisites."""
    from ai_functions.experimental.verified.compile._runtime import resolve_runtime
    from ai_functions.experimental.verified.compile.errors import CompilerError
    from ai_functions.experimental.verified.lean import LeanError
    from ai_functions.experimental.verified.lean.toolchain import LeanConfig, cache_path

    try:
        config = LeanConfig()
        runtime = resolve_runtime(config.setup(offline=True), cache_path(config))
        runtime.preflight()
        return runtime
    except (CompilerError, LeanError):
        if os.environ.get("AI_FUNCTIONS_REQUIRE_VERIFIED_NATIVE"):
            raise
        pytest.skip("Prepare Lean with LeanConfig().setup() to run native integration tests")


# Shared prepared projects, one per fixture project. Each is prepared once per
# session; a test that uses one is marked with the matching ``xdist_group`` so
# that, under ``--dist loadgroup``, one worker prepares it once. Tests about
# preparation itself create their own project instead.


@pytest.fixture(scope="session")
def init_project(lean_cache):
    """A prepared project with only Lean core in scope and nothing registered. Group: ``init``."""
    from ai_functions.experimental.verified.lean import LeanProject

    project = LeanProject(offline=True)
    project.prepare()
    return project


@pytest.fixture(scope="session")
def pricing_project(lean_cache):
    """The prepared `fixtures/verified/pricing` project with the contracts the function tests use.

    Group: ``verified_pricing``.
    """
    from ai_functions.experimental.verified.lean import LeanProject

    project = LeanProject(Path(__file__).parent / "fixtures" / "verified" / "pricing", imports=("Pricing",))
    project.add("def Identity (result : Nat) (x : Nat) : Prop := result = x")
    project.add("def Truth (result : Nat) : Prop := True")
    project.add("opaque days : String → Nat")
    project.add("def Days (result : Nat) (message : String) : Prop := result = days message")
    project.identity, project.truth = project.symbols.Identity, project.symbols.Truth
    project.days, project.days_contract = project.symbols.days, project.symbols.Days
    project.prepare()
    return project


@pytest.fixture(scope="session")
def verified_contracts(lean_cache):
    """The prepared `fixtures/verified/contracts` project. Group: ``verified_contracts``."""
    from verified_model import fixture_project

    project = fixture_project()
    project.prepare()
    return project


@pytest.fixture(scope="session")
def compile_project(lean_cache):
    """The prepared `fixtures/verified/compile` project. Group: ``compile_fixture``."""
    from ai_functions.experimental.verified.lean import LeanProject

    project = LeanProject(
        Path(__file__).parent / "fixtures" / "verified" / "compile", imports=("Fixture",), offline=True
    )
    project.prepare()
    return project


_LEAN_EXECUTABLES = frozenset({"elan", "lake", "lean", "leanc", "leanchecker"})


def _starts_real_lean(args: object, executable: object) -> bool:
    """Whether a process launch runs a real Lean tool rather than a test's shell-script stand-in."""
    if executable is None:
        executable = args if isinstance(args, (str, bytes, os.PathLike)) else next(iter(args))  # type: ignore[call-overload]
    program = os.fsdecode(executable)  # type: ignore[arg-type]
    program = program.split()[0] if program.strip() else program
    if os.path.basename(program) not in _LEAN_EXECUTABLES:
        return False
    path = program if os.sep in program else shutil.which(program)
    if path is None or not os.path.isfile(path):
        return False
    with open(path, "rb") as file:
        return file.read(2) != b"#!"


class _NoLeanPopen(subprocess.Popen):
    def __init__(self, args, *rest, **kwargs):  # type: ignore[no-untyped-def]
        if _starts_real_lean(args, kwargs.get("executable")):
            pytest.fail(f"a test not marked `lean` started a Lean process: {args!r}", pytrace=False)
        super().__init__(args, *rest, **kwargs)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):  # type: ignore[no-untyped-def]
    """Fail any test not marked `lean` that starts a real Lean process.

    Every launch (``execution.spawn_lake``, ``run_lake``, ``run_command`` and
    its async form, and direct ``subprocess`` calls) goes through
    ``subprocess.Popen``, so guarding it covers them all.
    """
    if item.get_closest_marker("lean") is not None:
        yield
        return
    original = subprocess.Popen
    subprocess.Popen = _NoLeanPopen  # type: ignore[misc]
    try:
        yield
    finally:
        subprocess.Popen = original  # type: ignore[misc]
