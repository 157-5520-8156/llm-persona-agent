"""JSON-lines operator interface for an isolated journey, with cancellable input."""

from __future__ import annotations

import asyncio
import io
import json
import os
import select
import stat
import sys


def _owned_fd(fd: int, access: int) -> int:
    if os.isatty(fd):
        # dup() shares O_NONBLOCK with inherited stdin/stdout/stderr on a PTY.
        # Opening the terminal itself creates an independent file description.
        return os.open(os.ttyname(fd), access | os.O_NOCTTY | os.O_NONBLOCK)
    # Readiness-driven pipe I/O below never changes the inherited flags.
    return os.dup(fd)


async def _ready_io(fd: int, operation, *, writing: bool = False):
    loop = asyncio.get_running_loop()
    result = loop.create_future()

    def ready():
        if result.done():
            return
        try:
            value = operation()
        except (BlockingIOError, InterruptedError):
            return
        except Exception as exc:
            result.set_exception(exc)
        else:
            result.set_result(value)

    register = loop.add_writer if writing else loop.add_reader
    remove = loop.remove_writer if writing else loop.remove_reader
    register(fd, ready)
    try:
        return await result
    finally:
        remove(fd)


async def write_json_line(value: object, *, stream=None) -> None:
    """Write one complete JSON line with cancellable terminal/pipe backpressure."""
    stream = sys.stdout if stream is None else stream
    text = json.dumps(value, ensure_ascii=False) + "\n"
    try:
        original_fd = stream.fileno()
    except (AttributeError, io.UnsupportedOperation):
        # In-memory capture streams have no kernel backpressure.
        stream.write(text)
        stream.flush()
        return
    fd = _owned_fd(original_fd, os.O_WRONLY)
    try:
        data = text.encode(getattr(stream, "encoding", None) or "utf-8")
        mode = os.fstat(fd).st_mode
        if stat.S_ISREG(mode):
            # Regular files cannot register a selector write callback.
            while data:
                written = os.write(fd, data)
                if not written:
                    raise BrokenPipeError("stdio output made no progress")
                data = data[written:]
            return
        # A writable pipe admits one atomic PIPE_BUF write. Perform it inside
        # the readiness callback, with no coroutine interleaving before write.
        # Terminal descriptors are independently nonblocking and may write more.
        chunk_size = 65536 if os.isatty(fd) else select.PIPE_BUF if stat.S_ISFIFO(mode) else 1
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + chunk_size]
            written = await _ready_io(fd, lambda: os.write(fd, chunk), writing=True)
            if not written:
                raise BrokenPipeError("stdio output made no progress")
            offset += written
    finally:
        os.close(fd)


class StdioJourneyCommands:
    """Own stdin reads without mutating inherited flags or blocking threads."""

    async def __aenter__(self):
        self.fd = _owned_fd(sys.stdin.fileno(), os.O_RDONLY)
        self.buffer = bytearray()
        self.eof = False
        return self

    async def __aexit__(self, *_exc):
        os.close(self.fd)

    async def _readline(self) -> bytes:
        while True:
            newline = self.buffer.find(b"\n")
            if newline > 16384 or (newline < 0 and len(self.buffer) > 16384):
                raise ValueError("operator command exceeds the input line limit")
            if newline >= 0:
                line = bytes(self.buffer[: newline + 1])
                del self.buffer[: newline + 1]
                return line
            if self.eof:
                line = bytes(self.buffer)
                self.buffer.clear()
                return line
            data = await _ready_io(self.fd, lambda: os.read(self.fd, 16385 - len(self.buffer)))
            self.eof = not data
            self.buffer.extend(data)

    async def next_command(self, observation: dict) -> dict | None:
        await write_json_line({"operator_observation": observation})
        line = await self._readline()
        return json.loads(line) if line else None
