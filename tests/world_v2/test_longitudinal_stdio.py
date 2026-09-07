import asyncio
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import textwrap

import pytest

from companion_daemon.world_v2.longitudinal_stdio import StdioJourneyCommands, write_json_line


@contextmanager
def _pty_child(script):
    master, slave = pty.openpty()
    root = Path(__file__).resolve().parents[2]
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(script)],
            stdin=slave,
            stdout=slave,
            stderr=subprocess.PIPE,
            cwd=root,
            env={**os.environ, "PYTHONPATH": str(root / "src")},
        )
    finally:
        os.close(slave)
    try:
        yield process, master
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
        os.close(master)


def test_pty_reader_does_not_change_inherited_stdin_or_stdout_flags():
    with _pty_child(
        """
        import asyncio, fcntl, json, os
        from companion_daemon.world_v2.longitudinal_stdio import StdioJourneyCommands

        async def main():
            before = [fcntl.fcntl(fd, fcntl.F_GETFL) for fd in (0, 1)]
            async with StdioJourneyCommands():
                inside = [fcntl.fcntl(fd, fcntl.F_GETFL) for fd in (0, 1)]
            after = [fcntl.fcntl(fd, fcntl.F_GETFL) for fd in (0, 1)]
            os.write(2, json.dumps([before, inside, after]).encode())

        asyncio.run(main())
        """
    ) as (process, _master):
        _stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 0, stderr.decode()
    before, inside, after = json.loads(stderr)
    assert inside == after == before


def test_large_pty_observation_waits_for_output_capacity_without_blocking_loop():
    with _pty_child(
        """
        import asyncio, os
        from companion_daemon.world_v2.longitudinal_stdio import StdioJourneyCommands

        async def main():
            async with StdioJourneyCommands() as operator:
                task = asyncio.create_task(operator.next_command({"payload": "x" * 262144}))
                await asyncio.sleep(0.05)
                os.write(2, b"ALIVE_PENDING" if not task.done() else b"ALIVE_FINISHED")
                assert await task is None

        asyncio.run(main())
        """
    ) as (process, master):
        ready, _, _ = select.select([process.stderr], [], [], 5)
        assert ready, "Output backpressure blocked the child's event loop"
        assert os.read(process.stderr.fileno(), 128) == b"ALIVE_PENDING"
        output = bytearray()
        while b"\n" not in output:
            ready, _, _ = select.select([master], [], [], 5)
            assert ready, "Observation output did not resume when consumed"
            output.extend(os.read(master, 65536))
        assert json.loads(output.split(b"\n", 1)[0]) == {
            "operator_observation": {"payload": "x" * 262144}
        }
        os.write(master, b"null\n")
        _stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 0, stderr.decode()


def test_pty_output_backpressure_can_be_cancelled_without_reading_stdout():
    with _pty_child(
        """
        import asyncio, fcntl, os
        from companion_daemon.world_v2.longitudinal_stdio import StdioJourneyCommands

        async def main():
            before = [fcntl.fcntl(fd, fcntl.F_GETFL) for fd in (0, 1)]
            async with StdioJourneyCommands() as operator:
                task = asyncio.create_task(operator.next_command({"payload": "x" * 262144}))
                await asyncio.sleep(0.05)
                assert not task.done(), "Observation must await reader capacity"
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("Cancellation must propagate")
            after = [fcntl.fcntl(fd, fcntl.F_GETFL) for fd in (0, 1)]
            assert after == before
            os.write(2, b"CANCELLED")

        asyncio.run(main())
        """
    ) as (process, _master):
        _stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 0, stderr.decode()
    assert stderr == b"CANCELLED"


@pytest.mark.asyncio
async def test_pipe_output_preserves_flags_and_cancels_under_backpressure():
    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(write_fd, "w", encoding="utf-8") as stream:
            flags = os.O_NONBLOCK | os.O_APPEND | os.O_ACCMODE | getattr(os, "O_ASYNC", 0)
            # Darwin adds a kernel bookkeeping bit on any first os.write;
            # compare the inherited I/O status flags, not that incidental bit.
            before = fcntl.fcntl(stream.fileno(), fcntl.F_GETFL) & flags
            task = asyncio.create_task(write_json_line({"payload": "x" * 262144}, stream=stream))
            await asyncio.sleep(0.05)
            assert not task.done()
            assert fcntl.fcntl(stream.fileno(), fcntl.F_GETFL) & flags == before
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert fcntl.fcntl(stream.fileno(), fcntl.F_GETFL) & flags == before
            os.fstat(stream.fileno())
    finally:
        os.close(read_fd)


@pytest.mark.asyncio
async def test_stdio_retains_buffered_lines_and_closes_only_its_owned_pipe(monkeypatch, capsys):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b'{"wait_until_minutes":2}\nnull\n')
    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        async with StdioJourneyCommands() as operator:
            assert await operator.next_command({"steps": []}) == {"wait_until_minutes": 2}
            assert await operator.next_command({"steps": []}) is None
            assert await operator.next_command({"steps": []}) is None  # EOF
        os.fstat(stream.fileno())
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(output) == 3
    assert all(row == {"operator_observation": {"steps": []}} for row in output)


@pytest.mark.asyncio
async def test_stdio_wait_cancels_without_a_blocked_reader_thread(monkeypatch):
    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(read_fd, "rb") as stream:
            monkeypatch.setattr(sys, "stdin", stream)
            async with StdioJourneyCommands() as operator:
                task = asyncio.create_task(operator.next_command({"steps": []}))
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    finally:
        os.close(write_fd)
