"""Local bridge builds, publication, and setup failures."""

import os
import subprocess
import sys
from importlib.resources import files


def test_source_resources_and_imports_need_no_runtime_setup(tmp_path):
    resource = files("ai_functions.experimental.verified.compile").joinpath("_native")
    assert resource.joinpath("bridge.c").is_file()
    assert resource.joinpath("ffi.h").is_file()
    script = """
import subprocess, sys
def forbidden(*args, **kwargs):
    raise AssertionError("import ran toolchain setup")
subprocess.Popen = forbidden
import ai_functions
assert "ai_functions.experimental.verified.compile" not in sys.modules
assert not hasattr(ai_functions, "ai_compile")
assert not hasattr(ai_functions, "ai_verified_function")
from ai_functions.experimental.verified.compile import ai_compile
from ai_functions.experimental.verified.lean.toolchain import LeanConfig
LeanConfig()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "AI_FUNCTIONS_LEAN_CACHE_DIR": str(tmp_path / "cache")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "cache").exists()
