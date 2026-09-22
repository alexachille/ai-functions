"""Failure-boundary checks that do not need an installed native toolchain."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from ai_functions._verified.compiler import Runtime, read_artifact, require_supported_python, run_tool
from ai_functions._verified.errors import CompilerError
from ai_functions._verified.function import _cache_lock


@pytest.mark.parametrize("how", ["cancel", "timeout"])
async def test_compiler_process_is_reaped_on_cancel_or_timeout(tmp_path, how, monkeypatch):
    binary = tmp_path / "toolchain" / "bin" / "slow"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexec /bin/sleep 30\n")
    binary.chmod(0o755)
    original_spawn = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    processes = []

    async def record_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        processes.append(process)
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)
    task = asyncio.create_task(run_tool(Runtime(tmp_path), tmp_path, ["slow"], 0.2 if how == "timeout" else 30))
    await asyncio.wait_for(spawned.wait(), 5)
    pid = processes[0].pid
    if how == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    else:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, 5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_waiting_for_a_cache_lock_is_cancellable(tmp_path):
    path = tmp_path / "lock"
    entered = asyncio.Event()

    async def waiter():
        async with _cache_lock(path):
            entered.set()

    async with _cache_lock(path):
        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        assert not entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with _cache_lock(path):
        pass


@pytest.mark.parametrize(
    "manifest",
    [
        [],
        None,
        {"module": [], "key": "k", "files": {}},
        {"module": "Verified" + "a" * 40, "key": "k", "files": []},
        {"module": "../escape", "key": "k", "files": {}},
    ],
)
def test_malformed_cache_metadata_is_a_cache_miss(tmp_path, manifest):
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert read_artifact(tmp_path, Runtime(tmp_path), "k") is None


def test_unsupported_python_is_rejected_only_by_the_new_feature(monkeypatch):
    import ai_functions
    from ai_functions._verified import compiler

    assert callable(ai_functions.ai_function)
    monkeypatch.setattr(compiler.sys, "version_info", (3, 11, 0))
    with pytest.raises(CompilerError, match="requires CPython 3.12"):
        require_supported_python()


@pytest.mark.parametrize("version", [(3, 12, 0), (3, 13, 0), (3, 14, 0)])
def test_supported_cpython_versions(version, monkeypatch):
    from ai_functions._verified import compiler

    monkeypatch.setattr(compiler.sys, "version_info", version)
    require_supported_python()


def test_free_threaded_python_is_rejected(monkeypatch):
    from ai_functions._verified import compiler

    monkeypatch.setattr(compiler.sys, "version_info", (3, 14, 0))
    monkeypatch.setattr(compiler.sysconfig, "get_config_var", lambda name: 1 if name == "Py_GIL_DISABLED" else None)
    with pytest.raises(CompilerError, match="GIL"):
        require_supported_python()
