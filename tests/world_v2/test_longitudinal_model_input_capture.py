from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json
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


@pytest.mark.asyncio
async def test_response_stream_is_returned_untouched_and_not_consumed(tmp_path: Path) -> None:
    class Stream(httpx.AsyncByteStream):
        consumed = False
        closed = False

        async def __aiter__(self):
            self.consumed = True
            yield b"first"
            yield b"second"

        async def aclose(self):
            self.closed = True

    stream = Stream()
    response = httpx.Response(200, stream=stream)
    inner = httpx.MockTransport(lambda _request: response)
    sink = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    transport = ModelInputCaptureTransport(inner=inner, capture=sink, model_role="flash")
    returned = await transport.handle_async_request(_request())
    assert returned is response
    assert returned.stream is stream
    assert not stream.consumed
    assert [chunk async for chunk in returned.aiter_bytes()] == [b"first", b"second"]
    await returned.aclose()
    assert stream.closed
    assert sink.health()["stream_completion"] == "unverified"


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
    assert len(second) == 2
    assert second[0]["scope"] is None
    assert sink.health()["requests_captured"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_installed_deepseek_adapter_keeps_one_request_and_original_output(
    tmp_path: Path,
    streaming: bool,
) -> None:
    from companion_daemon.llm import DeepSeekChatModel

    seen = []
    payload_usage = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}

    async def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        if streaming:
            frames = [
                {"choices": [{"delta": {"content": '{"ok":'}}]},
                {"choices": [{"delta": {"content": "true}"}}]},
                {"choices": [], "usage": payload_usage},
            ]
            wire = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=wire + "data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": payload_usage,
            },
        )

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
    assert len(records) == 2
    assert records[0]["model_content_json"].encode("utf-8") == seen[0]
    body = json.loads(records[0]["model_content_json"])
    assert body["messages"] == messages
    assert body["thinking"] == {"type": "enabled"}
    assert body["max_tokens"] == 128
    assert bool(body.get("stream")) is streaming
    assert "offline-fixture-credential" not in sink.path.read_text()


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
