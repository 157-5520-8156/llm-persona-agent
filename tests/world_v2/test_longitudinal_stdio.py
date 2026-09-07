import asyncio
import json
import os
import sys

import pytest

from companion_daemon.world_v2.longitudinal_stdio import StdioJourneyCommands


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
