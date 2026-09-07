"""JSON-lines operator interface for an isolated journey, with cancellable input."""

from __future__ import annotations

import asyncio
import json
import os
import sys


class StdioJourneyCommands:
    """Own a duplicate stdin pipe; waiting never leaves a blocked reader thread."""

    async def __aenter__(self):
        self.reader = asyncio.StreamReader(limit=16384)
        pipe = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
        try:
            self.transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(self.reader), pipe
            )
        except BaseException:
            pipe.close()
            raise
        return self

    async def __aexit__(self, *_exc):
        self.transport.close()

    async def next_command(self, observation: dict) -> dict | None:
        print(json.dumps({"operator_observation": observation}, ensure_ascii=False), flush=True)
        line = await self.reader.readline()
        return json.loads(line) if line else None
