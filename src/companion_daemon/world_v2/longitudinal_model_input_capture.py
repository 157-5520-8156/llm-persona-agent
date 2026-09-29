"""Private HTTP body evidence at an explicitly installed audit transport boundary.

This records the JSON body actually supplied to a client transport, not a
reconstruction of World Context or proof of provider acceptance/attention.
Install only on audit model clients. Headers, URLs and exception messages are
never archived. Response streams are copied as consumed, before HTTPX decoding
or provider normalization. Body EOF is not proof of complete model output.
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

from ..provider_request_audit import captured_usage_correlation


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
    """Lazy, exclusive 0600 JSONL archive with bounded response body files.

    The parent directory must already exist when the first request arrives.
    Reuse this instance across host restarts. Existing files are never reused
    or truncated. JSONL storage failures disable further archive writes; body
    storage failures stop that body's copy. health() exposes either gap while
    the model transport continues unchanged.

    Response files are exclusive 0600 files in a private 0700 directory. The
    default limits are 2 MiB per body, 32 MiB total body bytes and 1024 files.
    They limit evidence only. A response_body/pending record without a terminal
    record (including process death) is incomplete. Hashes always cover the
    recorded byte counts, so an interrupted/truncated prefix is never a full
    body hash. Preloaded responses are explicitly distinct from raw streams.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_response_body_bytes: int = 2 * 1024 * 1024,
        max_response_total_bytes: int = 32 * 1024 * 1024,
        max_response_files: int = 1024,
    ):
        for limit in (max_response_body_bytes, max_response_total_bytes, max_response_files):
            if type(limit) is not int or limit <= 0:
                raise ValueError("response capture limits must be positive integers")
        self.path = Path(path)
        self._lock = Lock()
        self._identity: tuple[int, int] | None = None
        self._size = 0
        self._records = 0
        self._requests = 0
        self._captured = 0
        self._error_type: str | None = None
        self._response_dir = self.path.with_name(self.path.name + ".responses")
        self._response_dir_identity: tuple[int, int] | None = None
        self._max_response_body_bytes = max_response_body_bytes
        self._max_response_total_bytes = max_response_total_bytes
        self._max_response_files = max_response_files
        self._response_files = 0
        self._response_bytes = 0
        self._responses_started = 0
        self._responses_complete = 0

    def _start_response(self, common: dict[str, object], byte_source: str) -> _ResponseBodyCapture:
        body = _ResponseBodyCapture(self, common, byte_source)
        with self._lock:
            self._responses_started += 1
            try:
                if self._error_type is not None:
                    body.capture_error = "archive_unavailable"
                elif self._response_files >= self._max_response_files:
                    body.capture_error = "file_limit"
                else:
                    if self._response_dir_identity is None:
                        self._response_dir.mkdir(mode=0o700)
                    directory = os.open(
                        self._response_dir, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
                    )
                    try:
                        info = os.fstat(directory)
                        identity = (info.st_dev, info.st_ino)
                        if self._response_dir_identity is None:
                            os.fchmod(directory, 0o700)
                            self._response_dir_identity = identity
                        elif self._response_dir_identity != identity:
                            raise OSError("response archive directory changed")
                        name = str(common["capture_id"]).removeprefix("model-input:") + ".body"
                        body.fd = os.open(
                            name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=directory,
                        )
                        body.body_file = str(Path(self._response_dir.name) / name)
                        self._response_files += 1
                        os.fchmod(body.fd, 0o600)
                    finally:
                        os.close(directory)
            except Exception as exc:
                body.capture_error = "storage_failed"
                body.error_type = type(exc).__name__
        self._append(body.record("pending"))
        return body

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
                "response_bodies_started": self._responses_started,
                "response_bodies_complete": self._responses_complete,
                "response_body_bytes": self._response_bytes,
                "response_body_files": self._response_files,
                "response_limits": {
                    "body_bytes": self._max_response_body_bytes,
                    "total_bytes": self._max_response_total_bytes,
                    "files": self._max_response_files,
                },
                "response_capture_status": "captured"
                if self._responses_started
                and self._responses_started == self._responses_complete
                and self._error_type is None
                else "unverified",
            }


class _ResponseBodyCapture:
    def __init__(self, sink: PrivateModelInputCapture, common: dict[str, object], byte_source: str):
        self.sink = sink
        self.common = common
        self.byte_source = byte_source
        self.fd: int | None = None
        self.body_file: str | None = None
        self.capture_error: str | None = None
        self.error_type: str | None = None
        self.observed_bytes = 0
        self.captured_bytes = 0
        self.observed_hash = hashlib.sha256()
        self.captured_hash = hashlib.sha256()
        self.finished = False

    def observe(self, chunk: bytes) -> None:
        if self.finished:
            return
        self.observed_bytes += len(chunk)
        self.observed_hash.update(chunk)
        with self.sink._lock:
            if self.capture_error is not None or self.fd is None:
                return
            allowed = min(
                len(chunk),
                self.sink._max_response_body_bytes - self.captured_bytes,
                self.sink._max_response_total_bytes - self.sink._response_bytes,
            )
            try:
                remaining = memoryview(chunk)[:allowed]
                while remaining:
                    written = os.write(self.fd, remaining)
                    if written <= 0:
                        raise OSError("response capture write made no progress")
                    self.captured_hash.update(remaining[:written])
                    self.captured_bytes += written
                    self.sink._response_bytes += written
                    remaining = remaining[written:]
                if allowed < len(chunk):
                    self.capture_error = "byte_limit"
            except Exception as exc:
                self.capture_error = "storage_failed"
                self.error_type = type(exc).__name__

    def record(self, stream_status: str) -> dict[str, object]:
        return {
            **self.common,
            "kind": "response_body",
            "response_contract": "longitudinal-model-response.1",
            "byte_source": self.byte_source,
            "body_file": self.body_file,
            "stream_status": stream_status,
            "body_complete": stream_status in {"eof", "preloaded"} and self.capture_error is None,
            "capture_error": self.capture_error,
            "error_type": self.error_type,
            "observed_bytes": self.observed_bytes,
            "captured_bytes": self.captured_bytes,
            "observed_bytes_sha256": self.observed_hash.hexdigest(),
            "captured_bytes_sha256": self.captured_hash.hexdigest(),
            "model_output_completion": "unverified",
        }

    def finish(self, status: str, error: BaseException | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        if error is not None:
            self.error_type = type(error).__name__
        if self.fd is not None:
            try:
                os.close(self.fd)
            except Exception as exc:
                self.capture_error = "storage_failed"
                self.error_type = type(exc).__name__
            self.fd = None
        record = self.record(status)
        with self.sink._lock:
            self.sink._responses_complete += int(record["body_complete"])
        self.sink._append(record)


class _CapturedResponseStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, body: _ResponseBodyCapture):
        self.inner = inner
        self.body = body
        self.eof = False

    async def __aiter__(self):
        try:
            async for chunk in self.inner:
                self.body.observe(chunk)
                yield chunk
            self.eof = True
        except BaseException as exc:
            self.body.finish(
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed", exc
            )
            raise

    async def aclose(self):
        try:
            await self.inner.aclose()
        except BaseException as exc:
            self.body.finish(
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed", exc
            )
            raise
        else:
            self.body.finish("eof" if self.eof else "closed_before_eof")


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
    from ..llm import captured_reference_request_identity

    local_identity = captured_reference_request_identity()
    return {
        "model_facing": True,
        "verification": "supplied_to_client_transport",
        "model_content_json": raw,
        "content_hash": hashlib.sha256(body).hexdigest(),
        "body_size_bytes": len(body),
        **({"local_reference_identity": local_identity} if local_identity is not None else {}),
    }


class ModelInputCaptureTransport(httpx.AsyncBaseTransport):
    """Observe HTTP bodies without rebuilding requests or draining response streams.

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
            **captured_usage_correlation(request.extensions),
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
                    "response_body_complete": False,
                    "response_body_status": "not_received",
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
        if material.get("model_facing") is True:
            # A preloaded response (e.g. MockTransport) may already be decoded;
            # keep its provenance distinct from original transport stream bytes.
            preloaded = response.is_stream_consumed
            body = self._capture._start_response(
                common, "preloaded_response_content" if preloaded else "transport_response_stream"
            )
            if preloaded:
                body.observe(response.content)
                body.finish("preloaded")
            else:
                response.stream = _CapturedResponseStream(response.stream, body)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()
