from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest

from companion_daemon.world_v2.longitudinal_model_input_capture import (
    ModelInputCaptureTransport,
    PrivateModelInputCapture,
    model_input_capture_scope,
)


@pytest.mark.asyncio
async def test_capture_binds_actual_body_and_preserves_transport_arguments(tmp_path: Path) -> None:
    path = tmp_path / "model-inputs.jsonl"
    sink = PrivateModelInputCapture(path)
    body = json.dumps(
        {
            "model": "fixture-model",
            "messages": [{"role": "system", "content": "实际系统材料。"}],
            "tools": [{"type": "function", "function": {"name": "answer", "parameters": {}}}],
            "tool_choice": {"type": "function", "function": {"name": "answer"}},
            "temperature": 0.8,
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    request = httpx.Request(
        "POST",
        "https://fixture.invalid/chat/completions",
        content=body,
        headers={"Authorization": "Bearer fixture-credential"},
    )
    response = httpx.Response(200, content=b"fixture response")
    received = []

    async def handle(actual: httpx.Request) -> httpx.Response:
        received.append(actual)
        return response

    transport = ModelInputCaptureTransport(
        inner=httpx.MockTransport(handle),
        capture=sink,
        model_role="flash",
    )
    assert sink.read_since() == (0, ())
    with model_input_capture_scope(step_id="step-1", virtual_at=datetime(2026, 9, 7, tzinfo=UTC)):
        returned = await transport.handle_async_request(request)
    assert received == [request]
    assert received[0] is request
    assert returned is response
    offset, records = sink.read_since()
    captured = records[0]
    assert captured["kind"] == "request"
    assert captured["model_content_json"].encode("utf-8") == body
    assert captured["content_hash"] == hashlib.sha256(body).hexdigest()
    assert captured["scope"] == {"step_id": "step-1", "virtual_at": "2026-09-07T00:00:00+00:00"}
    assert captured["provider_acceptance"] == "unverified"
    assert records[1]["capture_id"] == captured["capture_id"]
    assert records[1]["status"] == "response_headers_received"
    assert sink.read_since(offset) == (offset, ())
    assert "fixture-credential" not in path.read_text()
    assert "fixture.invalid" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    await transport.aclose()


def _request() -> httpx.Request:
    return httpx.Request(
        "POST",
        "https://fixture.invalid/chat/completions",
        json={"model": "fixture", "messages": [{"role": "user", "content": "hi"}]},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("claims", [b"", b',"world_claims":null', b',"world_claims":[]'])
async def test_response_capture_keeps_exact_wire_before_json_normalization(
    tmp_path: Path, claims: bytes
) -> None:
    body = b'{ "messages":["hello"]' + claims + b" }\n"

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body[:8]
            yield body[8:]

    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    async with httpx.AsyncClient(
        transport=ModelInputCaptureTransport(
            inner=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream())),
            capture=sink,
            model_role="flash",
        )
    ) as client:
        response = await client.send(_request())
    assert response.content == body
    _, records = sink.read_since()
    evidence = [row for row in records if row["kind"] == "response_body"][-1]
    assert evidence["capture_id"] == records[0]["capture_id"]
    assert evidence["body_complete"] is True
    assert evidence["stream_status"] == "eof"
    assert evidence["byte_source"] == "transport_response_stream"
    assert evidence["model_output_completion"] == "unverified"
    artifact = sink.path.parent / evidence["body_file"]
    assert artifact.read_bytes() == body
    assert evidence["captured_bytes"] == len(body)
    assert evidence["captured_bytes_sha256"] == hashlib.sha256(body).hexdigest()
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert artifact.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_transport_failure_preserves_exception_and_retains_input(tmp_path: Path) -> None:
    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    request = _request()
    failure = httpx.ConnectError("private fixture failure detail", request=request)

    async def fail(actual: httpx.Request) -> httpx.Response:
        assert actual is request
        raise failure

    transport = ModelInputCaptureTransport(
        inner=httpx.MockTransport(fail),
        capture=sink,
        model_role="world_support",
    )
    with pytest.raises(httpx.ConnectError) as caught:
        await transport.handle_async_request(request)
    assert caught.value is failure
    _, records = sink.read_since()
    assert records[0]["model_facing"] is True
    assert records[0]["scope"] is None
    assert records[0]["audit_association"] == "unverified"
    assert records[1]["status"] == "failed"
    assert records[1]["error_type"] == "ConnectError"
    assert records[1]["response_body_complete"] is False
    assert "private fixture failure detail" not in sink.path.read_text()


@pytest.mark.asyncio
async def test_cancelled_request_stays_cancelled_with_its_input_record(tmp_path: Path) -> None:
    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending(_request: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    transport = ModelInputCaptureTransport(
        inner=httpx.MockTransport(pending),
        capture=sink,
        model_role="thinking",
    )
    task = asyncio.create_task(transport.handle_async_request(_request()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    _, records = sink.read_since()
    assert records[0]["verification"] == "supplied_to_client_transport"
    assert records[1]["status"] == "cancelled"
    assert records[1]["capture_id"] == records[0]["capture_id"]
    assert records[1]["response_body_complete"] is False


@pytest.mark.asyncio
async def test_response_stream_keeps_chunk_delivery_without_read_ahead(tmp_path: Path) -> None:
    release_tail = asyncio.Event()
    first = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    last = b"data: [DONE]\n\n"

    class Stream(httpx.AsyncByteStream):
        consumed = False
        closed = False

        async def __aiter__(self):
            self.consumed = True
            yield first
            await release_tail.wait()
            yield last

        async def aclose(self):
            self.closed = True

    stream = Stream()
    response = httpx.Response(200, stream=stream)
    inner = httpx.MockTransport(lambda _request: response)
    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    transport = ModelInputCaptureTransport(inner=inner, capture=sink, model_role="flash")
    returned = await transport.handle_async_request(_request())
    assert returned is response
    assert not stream.consumed
    iterator = returned.aiter_bytes()
    assert await asyncio.wait_for(anext(iterator), timeout=1) == first
    _, records = sink.read_since()
    assert records[-1]["stream_status"] == "pending"
    assert records[-1]["body_complete"] is False
    assert (sink.path.parent / records[-1]["body_file"]).read_bytes() == first
    release_tail.set()
    assert [chunk async for chunk in iterator] == [last]
    await returned.aclose()
    assert stream.closed
    assert sink.health()["stream_completion"] == "unverified"
    _, records = sink.read_since()
    assert records[-1]["body_complete"] is True
    assert (sink.path.parent / records[-1]["body_file"]).read_bytes() == first + last


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["close", "cancel", "fail", "close_fail"])
async def test_interrupted_body_is_incomplete_and_preserves_original_stream_outcome(
    tmp_path: Path, end: str
) -> None:
    waiting = asyncio.Event()
    failure = httpx.ReadError("private response error text")

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"partial"
            waiting.set()
            if end == "fail":
                raise failure
            if end == "cancel":
                await asyncio.Future()

        async def aclose(self):
            self.closed = True
            if end == "close_fail":
                raise failure

    stream = Stream()
    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    transport = ModelInputCaptureTransport(
        inner=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
        capture=sink,
        model_role="flash",
    )
    response = await transport.handle_async_request(_request())
    iterator = response.aiter_raw()
    assert await anext(iterator) == b"partial"
    if end == "cancel":
        pending = asyncio.create_task(anext(iterator))
        await waiting.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    elif end in {"fail", "close_fail"}:
        with pytest.raises(httpx.ReadError) as caught:
            await anext(iterator)
        assert caught.value is failure
    await response.aclose()
    assert stream.closed
    _, records = sink.read_since()
    final = records[-1]
    assert (
        final["stream_status"]
        == {
            "close": "closed_before_eof",
            "cancel": "cancelled",
            "fail": "failed",
            "close_fail": "failed",
        }[end]
    )
    assert final["body_complete"] is False
    assert final["captured_bytes_sha256"] == hashlib.sha256(b"partial").hexdigest()
    assert (sink.path.parent / final["body_file"]).read_bytes() == b"partial"
    assert sink.health()["response_capture_status"] == "unverified"
    assert "private response error text" not in sink.path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limits", "expected_prefixes", "expected_errors"),
    [
        ({"max_response_body_bytes": 3}, [b"abc", b"abc"], ["byte_limit", "byte_limit"]),
        ({"max_response_total_bytes": 7}, [b"abcdef", b"a"], [None, "byte_limit"]),
        ({"max_response_files": 1}, [b"abcdef", None], [None, "file_limit"]),
    ],
)
async def test_response_limits_bound_private_artifacts_without_truncating_model_output(
    tmp_path: Path, limits: dict, expected_prefixes: list, expected_errors: list
) -> None:
    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl", **limits)

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"ab"
            yield b"cdef"

    async with httpx.AsyncClient(
        transport=ModelInputCaptureTransport(
            inner=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream())),
            capture=sink,
            model_role="flash",
        )
    ) as client:
        for _ in range(2):
            assert (await client.send(_request())).content == b"abcdef"
    _, records = sink.read_since()
    finals = [row for row in records if row.get("stream_status") == "eof"]
    assert len(finals) == 2
    for final, prefix, error in zip(finals, expected_prefixes, expected_errors, strict=True):
        assert final["capture_error"] == error
        assert final["body_complete"] is (error is None)
        assert final["observed_bytes"] == 6
        assert final["observed_bytes_sha256"] == hashlib.sha256(b"abcdef").hexdigest()
        if prefix is None:
            assert final["body_file"] is None
        else:
            assert (sink.path.parent / final["body_file"]).read_bytes() == prefix
            assert final["captured_bytes"] == len(prefix)
            assert final["captured_bytes_sha256"] == hashlib.sha256(prefix).hexdigest()
    assert sink.health()["response_capture_status"] == "unverified"


@pytest.mark.asyncio
async def test_response_storage_collision_preserves_evidence_and_model_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "capture.jsonl"
    existing = path.with_name(path.name + ".responses")
    existing.mkdir()
    retained = existing / "retained"
    retained.write_bytes(b"older evidence")
    sink = PrivateModelInputCapture(path)
    async with httpx.AsyncClient(
        transport=ModelInputCaptureTransport(
            inner=httpx.MockTransport(lambda _: httpx.Response(200, content=b"actual response")),
            capture=sink,
            model_role="flash",
        )
    ) as client:
        assert (await client.send(_request())).content == b"actual response"
    _, records = sink.read_since()
    assert records[-1]["capture_error"] == "storage_failed"
    assert records[-1]["error_type"] == "FileExistsError"
    assert records[-1]["body_complete"] is False
    assert records[-1]["body_file"] is None
    assert retained.read_bytes() == b"older evidence"
    assert sink.health()["response_capture_status"] == "unverified"


@pytest.mark.asyncio
async def test_partial_response_write_failure_keeps_hash_of_saved_prefix_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write = os.write

    def failing_write(fd, data):
        if bytes(data) == b"abcdef":
            return original_write(fd, data[:2])
        if bytes(data) == b"cdef":
            raise OSError("private disk error detail")
        return original_write(fd, data)

    monkeypatch.setattr(os, "write", failing_write)
    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    async with httpx.AsyncClient(
        transport=ModelInputCaptureTransport(
            inner=httpx.MockTransport(lambda _: httpx.Response(200, content=b"abcdef")),
            capture=sink,
            model_role="flash",
        )
    ) as client:
        assert (await client.send(_request())).content == b"abcdef"
    _, records = sink.read_since()
    result = records[-1]
    assert result["capture_error"] == "storage_failed"
    assert result["body_complete"] is False
    assert result["observed_bytes_sha256"] == hashlib.sha256(b"abcdef").hexdigest()
    assert result["captured_bytes_sha256"] == hashlib.sha256(b"ab").hexdigest()
    assert (sink.path.parent / result["body_file"]).read_bytes() == b"ab"
    assert "private disk error detail" not in sink.path.read_text()


@pytest.mark.asyncio
async def test_response_file_limit_counts_files_even_if_permission_setup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_open = os.open
    original_chmod = os.fchmod
    body_fds = set()

    def observed_open(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if str(path).endswith(".body"):
            body_fds.add(fd)
        else:
            body_fds.discard(fd)
        return fd

    def failed_chmod(fd, mode):
        if fd in body_fds:
            raise OSError("fixture permission failure")
        return original_chmod(fd, mode)

    monkeypatch.setattr(os, "open", observed_open)
    monkeypatch.setattr(os, "fchmod", failed_chmod)
    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl", max_response_files=1)
    async with httpx.AsyncClient(
        transport=ModelInputCaptureTransport(
            inner=httpx.MockTransport(lambda _: httpx.Response(200, content=b"abcdef")),
            capture=sink,
            model_role="flash",
        )
    ) as client:
        for _ in range(2):
            assert (await client.send(_request())).content == b"abcdef"
    assert len(list(tmp_path.rglob("*.body"))) == 1
    _, records = sink.read_since()
    assert records[-1]["capture_error"] == "file_limit"
    assert records[-1]["body_complete"] is False


@pytest.mark.asyncio
async def test_unread_request_stream_is_not_consumed_by_capture(tmp_path: Path) -> None:
    consumed = False

    async def body():
        nonlocal consumed
        consumed = True
        yield b'{"messages":[]}'

    request = httpx.Request("POST", "https://fixture.invalid", content=body())

    class Inner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, actual):
            assert actual is request
            assert not consumed
            assert await actual.aread() == b'{"messages":[]}'
            return httpx.Response(200)

    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    transport = ModelInputCaptureTransport(inner=Inner(), capture=sink, model_role="flash")
    await transport.handle_async_request(request)
    _, records = sink.read_since()
    assert records[0]["verification"] == "unverified"
    assert records[0]["reason"] == "unread_request_body"
    assert "model_content_json" not in records[0]
    assert sink.health()["status"] == "unverified"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not JSON", b'{"messages":[],"api_key":"fixture-body-key"}'])
async def test_unsupported_body_is_unverified_and_forwarded_unchanged(tmp_path: Path, body: bytes):
    received = []

    async def handle(request):
        received.append(request.content)
        return httpx.Response(200)

    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    transport = ModelInputCaptureTransport(
        inner=httpx.MockTransport(handle),
        capture=sink,
        model_role="flash",
    )
    await transport.handle_async_request(
        httpx.Request("POST", "https://fixture.invalid", content=body)
    )
    assert received == [body]
    _, records = sink.read_since()
    assert records[0]["verification"] == "unverified"
    assert "model_content_json" not in records[0]
    assert "fixture-body-key" not in sink.path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("symlink", [False, True])
async def test_existing_archive_is_preserved_and_storage_failure_does_not_block_model(
    tmp_path: Path,
    symlink: bool,
) -> None:
    retained = tmp_path / "retained"
    retained.write_bytes(b"existing private evidence")
    path = tmp_path / "capture.jsonl" if symlink else retained
    if symlink:
        path.symlink_to(retained)
    sink = PrivateModelInputCapture(path)
    responses = []

    async def handle(request):
        responses.append(request)
        return httpx.Response(200)

    transport = ModelInputCaptureTransport(
        inner=httpx.MockTransport(handle),
        capture=sink,
        model_role="flash",
    )
    assert (await transport.handle_async_request(_request())).status_code == 200
    assert len(responses) == 1
    assert retained.read_bytes() == b"existing private evidence"
    assert sink.health()["status"] == "unverified"
    assert sink.health()["error_type"] == "FileExistsError"
    assert sink.read_since() == (0, ())


@pytest.mark.asyncio
async def test_concurrent_scopes_remain_separate_and_offsets_read_only_new_records(tmp_path: Path):
    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    transport = ModelInputCaptureTransport(
        inner=httpx.MockTransport(lambda _: httpx.Response(200)),
        capture=sink,
        model_role="flash",
    )

    async def call(step_id: str):
        with model_input_capture_scope(step_id=step_id, virtual_at="2026-09-07T00:00:00+00:00"):
            await asyncio.sleep(0)
            await transport.handle_async_request(_request())

    await asyncio.gather(call("step-a"), call("step-b"))
    offset, first = sink.read_since()
    requests = [row for row in first if row["kind"] == "request"]
    assert {row["scope"]["step_id"] for row in requests} == {"step-a", "step-b"}
    assert len({row["capture_id"] for row in requests}) == 2
    await transport.handle_async_request(_request())
    next_offset, second = sink.read_since(offset)
    assert next_offset > offset
    assert [row["kind"] for row in second] == [
        "request",
        "transport_result",
        "response_body",
        "response_body",
    ]
    assert second[0]["scope"] is None
    assert {row["capture_id"] for row in second} == {second[0]["capture_id"]}
    assert second[-1]["body_complete"] is True
    assert sink.health()["requests_captured"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_installed_deepseek_adapter_keeps_one_request_and_original_output(
    tmp_path: Path,
    streaming: bool,
) -> None:
    from companion_daemon.llm import DeepSeekChatModel

    seen = []
    response_bodies = []
    payload_usage = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}

    class Stream(httpx.AsyncByteStream):
        def __init__(self, body: bytes):
            self.body = body

        async def __aiter__(self):
            # Split within JSON/SSE boundaries; capture must preserve framing.
            yield self.body[:19]
            yield self.body[19:]

    async def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        if streaming:
            frames = [
                {"choices": [{"delta": {"content": '{"ok":'}}]},
                {"choices": [{"delta": {"content": "true}"}}]},
                {"choices": [], "usage": payload_usage},
            ]
            wire = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
            response_bodies.append((wire + "data: [DONE]\n\n").encode())
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                stream=Stream(response_bodies[-1]),
            )
        response_bodies.append(
            json.dumps(
                {
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": payload_usage,
                }
            ).encode()
        )
        return httpx.Response(200, stream=Stream(response_bodies[-1]))

    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    model = DeepSeekChatModel(
        api_key="offline-fixture-credential",
        base_url="https://fixture.invalid",
        model="fixture",
        thinking_enabled=True,
        max_completion_tokens=128,
        transport=ModelInputCaptureTransport(
            inner=httpx.MockTransport(respond),
            capture=sink,
            model_role="thinking",
        ),
    )
    messages = [
        {"role": "system", "content": "actual compiled context"},
        {"role": "user", "content": "actual input"},
    ]
    deltas = []
    try:
        if streaming:
            text, usage = await model.complete_json_stream_with_usage(
                messages,
                on_text_delta=deltas.append,
            )
        else:
            text, usage = await model.complete_json_with_usage(messages)
    finally:
        await model.aclose()
    assert text == '{"ok":true}'
    assert usage is not None
    assert len(seen) == 1
    if streaming:
        assert deltas == ['{"ok":', "true}"]
    _, records = sink.read_since()
    assert len(records) == 4
    assert records[0]["model_content_json"].encode("utf-8") == seen[0]
    body = json.loads(records[0]["model_content_json"])
    assert body["messages"] == messages
    assert body["thinking"] == {"type": "enabled"}
    assert body["max_tokens"] == 128
    assert bool(body.get("stream")) is streaming
    result = records[-1]
    assert result["body_complete"] is True
    assert result["stream_status"] == "eof"
    assert result["capture_id"] == records[0]["capture_id"]
    assert result["captured_bytes_sha256"] == hashlib.sha256(response_bodies[0]).hexdigest()
    assert (sink.path.parent / result["body_file"]).read_bytes() == response_bodies[0]
    assert "offline-fixture-credential" not in sink.path.read_text()


@pytest.mark.asyncio
async def test_capture_preserves_compressed_transport_bytes_before_httpx_decoding(tmp_path: Path):
    decoded = b'{"world_claims":null}'
    encoded = gzip.compress(decoded, mtime=0)

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield encoded

    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    async with httpx.AsyncClient(
        transport=ModelInputCaptureTransport(
            inner=httpx.MockTransport(
                lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Stream())
            ),
            capture=sink,
            model_role="flash",
        )
    ) as client:
        response = await client.send(_request())
    assert response.content == decoded
    _, records = sink.read_since()
    result = records[-1]
    assert result["byte_source"] == "transport_response_stream"
    assert result["body_complete"] is True
    assert (sink.path.parent / result["body_file"]).read_bytes() == encoded


@pytest.mark.asyncio
async def test_closing_wrapper_only_closes_supplied_transport(tmp_path: Path) -> None:
    class Inner(httpx.AsyncBaseTransport):
        close_count = 0

        async def aclose(self):
            self.close_count += 1

    inner = Inner()
    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    transport = ModelInputCaptureTransport(inner=inner, capture=sink, model_role="flash")
    await transport.aclose()
    assert inner.close_count == 1
    assert not sink.path.exists()
    assert sink.health()["status"] == "unverified"
