"""Process lifetimes, environments, cancellation and cache locks."""

import asyncio
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from ai_functions.experimental.verified.lean.errors import LeanTimeoutError
from ai_functions.experimental.verified.lean.execution import (
    LakeEnv,
    file_lock,
    run_command,
    run_command_async,
    run_in_thread,
    run_lake,
)


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
    task = asyncio.create_task(run_command_async([str(binary)], cwd=tmp_path, timeout=0.2 if how == "timeout" else 30))
    await asyncio.wait_for(spawned.wait(), 5)
    pid = processes[0].pid
    if how == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    else:
        with pytest.raises(LeanTimeoutError):
            await asyncio.wait_for(task, 5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_supplied_environment_is_not_merged_with_ambient_state(asynchronous, monkeypatch):
    monkeypatch.setenv("LEAN_TEST_AMBIENT", "must not escape")
    command = [sys.executable, "-c", "import os; print(os.environ.get('LEAN_TEST_AMBIENT', 'absent'))"]
    if asynchronous:
        result = await run_command_async(command, environment={})
    else:
        result = run_command(command, environment={})
    assert result.stdout.strip() == "absent"


async def test_cancelled_thread_work_finishes_before_caller_releases_state():
    entered, finish = threading.Event(), threading.Event()
    state = []

    def blocking_work():
        entered.set()
        assert finish.wait(5)
        state.append("work finished")

    async def caller():
        try:
            await run_in_thread(blocking_work)
        finally:
            state.append("caller released state")

    task = asyncio.create_task(caller())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert state == []
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert state == ["work finished", "caller released state"]


def test_cache_lock_excludes_other_threads_until_its_timeout(tmp_path):
    path = tmp_path / "lock"
    with file_lock(path), ThreadPoolExecutor(max_workers=1) as pool:

        def wait_for_lock():
            with file_lock(path, timeout=0.1):
                pytest.fail("lock acquired concurrently")

        with pytest.raises(LeanTimeoutError, match="cache lock"):
            pool.submit(wait_for_lock).result(timeout=5)


def test_run_lake_timeout_terminates_child_processes(tmp_path):
    lake = tmp_path / "lake"
    lake.write_text("#!/bin/sh\nsleep 60 &\nwait\n")
    lake.chmod(0o755)
    tools = SimpleNamespace(lake=lake, environment=lambda: {})
    before = time.monotonic()
    with pytest.raises(LeanTimeoutError):
        run_lake(LakeEnv(tools, tmp_path), ["env", "lean"], timeout=0.1)  # type: ignore[arg-type]
    assert time.monotonic() - before < 5
