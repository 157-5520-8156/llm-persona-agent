"""Private input evidence at an explicitly installed model transport boundary.

This records the JSON body actually supplied to a client transport, not a
reconstruction of World Context or proof of provider acceptance/attention.
Install only on model clients. Headers, URLs, responses and exception messages
are never archived. Streaming response objects and cancellation pass through.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from typing import Iterator
from uuid import uuid4

import httpx


_SCOPE: ContextVar[tuple[str, str] | None] = ContextVar(
    "longitudinal_model_input_scope", default=None
)
# Body-only fields of the installed chat adapters. An unfamiliar request is
# explicitly unverified instead of dumping arbitrary client traffic or keys.
_CHAT_BODY_FIELDS = frozenset(
    {
        "model",
        "messages",
        "temperature",
        "max_tokens",
        "max_completion_tokens",
        "thinking",
        "reasoning_effort",
        "tools",
        "tool_choice",
        "response_format",
        "stream",
        "stream_options",
        "top_p",
        "stop",
        "frequency_penalty",
        "presence_penalty",
        "parallel_tool_calls",
        "seed",
        "logprobs",
        "top_logprobs",
        "n",
        "logit_bias",
    }
)


@contextmanager
def model_input_capture_scope(*, step_id: str, virtual_at: datetime | str) -> Iterator[None]:
    """Associate child-task requests with their originating experiment step.

    The scope is descriptive. It supplies no model prompt or authority. A
    detached task retains its origin even if it runs during a later step.
    """
    if not isinstance(step_id, str) or not step_id:
        raise ValueError("capture scope requires a step_id")
    at = datetime.fromisoformat(virtual_at) if isinstance(virtual_at, str) else virtual_at
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("capture scope requires timezone-aware virtual_at")
    token = _SCOPE.set((step_id, at.isoformat()))
    try:
        yield
    finally:
        _SCOPE.reset(token)


class PrivateModelInputCapture:
    """Lazy, exclusive 0600 JSONL archive; does not keep a file open.

    The parent directory must already exist when the first request arrives.
    Reuse this instance across host restarts. Existing files are never reused
    or truncated. Storage failures disable further writes and are exposed by
    health(), while the model transport continues unchanged.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = Lock()
        self._identity: tuple[int, int] | None = None
        self._size = 0
        self._records = 0
        self._requests = 0
        self._captured = 0
        self._error_type: str | None = None

    def _open_owned(self, flags: int) -> int:
        create = self._identity is None
        fd = os.open(
            self.path,
            flags | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if create else 0),
            0o600,
        )
        try:
            info = os.fstat(fd)
            identity = (info.st_dev, info.st_ino)
            if create:
                os.fchmod(fd, 0o600)
                self._identity = identity
            elif identity != self._identity or info.st_size != self._size:
                raise OSError("capture archive changed")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _append(self, record: dict[str, object]) -> None:
        with self._lock:
            is_request = record["kind"] == "request"
            self._requests += int(is_request)
            if self._error_type is not None:
                return
            try:
                raw = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode(
                    "utf-8"
                )
                fd = self._open_owned(os.O_WRONLY | os.O_APPEND)
                try:
                    remaining = memoryview(raw)
                    while remaining:
                        written = os.write(fd, remaining)
                        if written <= 0:
                            raise OSError("capture write made no progress")
                        remaining = remaining[written:]
                finally:
                    os.close(fd)
                self._size += len(raw)
                self._records += 1
                self._captured += int(is_request and record.get("model_facing") is True)
            except Exception as exc:
                # No exception text: OS or codec messages may contain private
                # paths/content. A failed observer cannot replace model behavior.
                self._error_type = type(exc).__name__

    def read_since(self, offset: int = 0) -> tuple[int, tuple[dict[str, object], ...]]:
        """Read complete records after a previously returned byte offset.

        No records means no supplied-input evidence. Check health() to
        distinguish an unused adapter from an unavailable archive.
        """
        with self._lock:
            if type(offset) is not int or not 0 <= offset <= self._size:
                raise ValueError("invalid capture byte offset")
            if offset == self._size:
                return offset, ()
            try:
                fd = self._open_owned(os.O_RDONLY)
                with os.fdopen(fd, "rb") as stream:
                    stream.seek(offset)
                    raw = stream.read(self._size - offset)
                records = tuple(json.loads(line) for line in raw.splitlines())
                return self._size, records
            except Exception as exc:
                self._error_type = type(exc).__name__
                return offset, ()

    def health(self) -> dict[str, object]:
        with self._lock:
            return {
                "status": "captured"
                if self._requests and self._requests == self._captured and self._error_type is None
                else "unverified",
                "requests_seen": self._requests,
                "requests_captured": self._captured,
                "records_written": self._records,
                "file_bytes": self._size,
                "error_type": self._error_type,
                "provider_acceptance": "unverified",
                "stream_completion": "unverified",
            }


def _request_material(request: httpx.Request) -> dict[str, object]:
    try:
        body = request.content
    except httpx.RequestNotRead:
        # Consuming an async request stream here would change its behavior.
        return {
            "model_facing": False,
            "verification": "unverified",
            "reason": "unread_request_body",
        }
    try:
        raw = body.decode("utf-8")
        parsed = json.loads(raw)
    except (ValueError, UnicodeError):
        return {
            "model_facing": False,
            "verification": "unverified",
            "reason": "non_json_request_body",
        }
    if (
        not isinstance(parsed, dict)
        or not isinstance(parsed.get("messages"), list)
        or set(parsed) - _CHAT_BODY_FIELDS
    ):
        return {
            "model_facing": False,
            "verification": "unverified",
            "reason": "unsupported_request_body",
        }
    return {
        "model_facing": True,
        "verification": "supplied_to_client_transport",
        "model_content_json": raw,
        "content_hash": hashlib.sha256(body).hexdigest(),
        "body_size_bytes": len(body),
    }


class ModelInputCaptureTransport(httpx.AsyncBaseTransport):
    """Observe request bodies without rebuilding requests or wrapping streams.

    model_role is only a caller-provided route label, never semantic authority.
    The caller supplies the original transport with its own proxy/TLS settings.
    response_headers_received says nothing about completion of a stream or
    whether the provider accepted, billed, attended to, or used the input.
    """

    def __init__(
        self, *, inner: httpx.AsyncBaseTransport, capture: PrivateModelInputCapture, model_role: str
    ):
        self._inner = inner
        self._capture = capture
        self._role = model_role

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        capture_id = "model-input:" + uuid4().hex
        scope = _SCOPE.get()
        common = {
            "contract": "longitudinal-model-input.1",
            "capture_id": capture_id,
            "model_role": self._role,
            "scope": {"step_id": scope[0], "virtual_at": scope[1]} if scope else None,
            "provider_acceptance": "unverified",
            "audit_association": "unverified",
        }
        try:
            material = _request_material(request)
        except Exception as exc:
            material = {
                "model_facing": False,
                "verification": "unverified",
                "reason": "capture_error",
                "error_type": type(exc).__name__,
            }
        self._capture._append(
            {
                **common,
                "kind": "request",
                "captured_at": datetime.now(UTC).isoformat(),
                **material,
            }
        )
        try:
            response = await self._inner.handle_async_request(request)
        except BaseException as exc:
            self._capture._append(
                {
                    **common,
                    "kind": "transport_result",
                    "status": "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                    "error_type": type(exc).__name__,
                }
            )
            raise
        self._capture._append(
            {
                **common,
                "kind": "transport_result",
                "status": "response_headers_received",
                "http_status": response.status_code,
            }
        )
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()
