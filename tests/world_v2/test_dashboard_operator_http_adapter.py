from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from companion_daemon.world_v2.dashboard_home_snapshot import DashboardHomeSnapshotModule
from companion_daemon.world_v2.dashboard_operator_http import (
    DashboardHomeSourceError,
    QQDashboardHomeHttpAdapter,
)
from companion_daemon.world_v2.ledger import WorldLedger


@pytest.mark.asyncio
async def test_daemon_adapter_reads_only_the_fixed_typed_owner_snapshot() -> None:
    snapshot = await DashboardHomeSnapshotModule(
        ledger=WorldLedger.in_memory(world_id="world:dashboard-adapter"),
        deployment_id="deployment:test",
        boot_id="boot:test",
        clock=lambda: datetime(2026, 8, 12, 2, 0, tzinfo=UTC),
    ).capture()
    etag = f'"{snapshot.snapshot_hash}"'
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(
            200,
            json=snapshot.to_payload(),
            headers={"ETag": etag},
        )

    adapter = QQDashboardHomeHttpAdapter(
        base_url="http://127.0.0.1:8787",
        operator_token="machine-read-secret",
        transport=httpx.MockTransport(respond),
    )

    first = await adapter.fetch()
    unchanged = await adapter.fetch(if_none_match=etag)

    assert first.not_modified is False
    assert first.snapshot == snapshot
    assert first.etag == etag
    assert unchanged.not_modified is True
    assert unchanged.snapshot is None
    assert unchanged.etag == etag
    assert [str(item.url) for item in requests] == [
        "http://127.0.0.1:8787/internal/world-v2/dashboard/operator-snapshot",
        "http://127.0.0.1:8787/internal/world-v2/dashboard/operator-snapshot",
    ]
    assert all(item.method == "GET" for item in requests)
    assert all(item.headers["Accept"] == "application/json" for item in requests)
    assert all(
        item.headers["X-World-V2-Internal-Token"] == "machine-read-secret"
        for item in requests
    )
    assert "machine-read-secret" not in str(requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (httpx.Response(302, headers={"Location": "https://attacker.invalid"}), "redirect_rejected"),
        (httpx.Response(403, text="secret upstream diagnostic"), "owner_auth_misconfigured"),
        (httpx.Response(503, text="private owner state"), "owner_unavailable"),
        (httpx.Response(500, text="private traceback"), "unexpected_owner_status"),
    ],
)
async def test_daemon_adapter_maps_owner_status_without_leaking_upstream_data(
    response: httpx.Response,
    expected_code: str,
) -> None:
    adapter = QQDashboardHomeHttpAdapter(
        base_url="http://127.0.0.1:8787",
        operator_token="machine-read-secret",
        transport=httpx.MockTransport(lambda _request: response),
    )

    with pytest.raises(DashboardHomeSourceError) as raised:
        await adapter.fetch()

    assert raised.value.code == expected_code
    assert str(raised.value) == f"dashboard owner source failed: {expected_code}"
    assert "machine-read-secret" not in str(raised.value)
    assert "private" not in str(raised.value)
    assert "attacker" not in str(raised.value)


@pytest.mark.asyncio
async def test_daemon_adapter_rejects_non_json_and_unverified_owner_snapshots() -> None:
    valid = await DashboardHomeSnapshotModule(
        ledger=WorldLedger.in_memory(world_id="world:dashboard-invalid-owner"),
        deployment_id="deployment:test",
        boot_id="boot:test",
        clock=lambda: datetime(2026, 8, 12, 2, 0, tzinfo=UTC),
    ).capture()
    payload = valid.to_payload()

    responses = iter(
        (
            httpx.Response(
                200,
                content=valid.model_dump_json().encode(),
                headers={"Content-Type": "text/plain", "ETag": f'"{valid.snapshot_hash}"'},
            ),
            httpx.Response(
                200,
                json={**payload, "snapshot_hash": "0" * 64},
                headers={"ETag": '"' + "0" * 64 + '"'},
            ),
            httpx.Response(
                200,
                json=payload,
                headers={"ETag": '"wrong"'},
            ),
        )
    )
    adapter = QQDashboardHomeHttpAdapter(
        base_url="http://127.0.0.1:8787",
        operator_token="machine-read-secret",
        transport=httpx.MockTransport(lambda _request: next(responses)),
    )

    codes: list[str] = []
    for _ in range(3):
        with pytest.raises(DashboardHomeSourceError) as raised:
            await adapter.fetch()
        codes.append(raised.value.code)

    assert codes == [
        "invalid_content_type",
        "invalid_owner_contract",
        "etag_mismatch",
    ]


@pytest.mark.asyncio
async def test_daemon_adapter_bounds_payload_and_normalizes_connect_failure() -> None:
    oversized = QQDashboardHomeHttpAdapter(
        base_url="http://127.0.0.1:8787",
        operator_token="machine-read-secret",
        max_response_bytes=8,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b"123456789",
                headers={"Content-Type": "application/json", "ETag": '"irrelevant"'},
            )
        ),
    )

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("contains-internal-network-name", request=request)

    unavailable = QQDashboardHomeHttpAdapter(
        base_url="http://127.0.0.1:8787",
        operator_token="machine-read-secret",
        transport=httpx.MockTransport(fail),
    )

    with pytest.raises(DashboardHomeSourceError) as too_large:
        await oversized.fetch()
    with pytest.raises(DashboardHomeSourceError) as unreachable:
        await unavailable.fetch()

    assert too_large.value.code == "response_too_large"
    assert unreachable.value.code == "owner_unreachable"
    assert "contains-internal-network-name" not in str(unreachable.value)


@pytest.mark.asyncio
async def test_daemon_adapter_stops_streaming_as_soon_as_the_body_limit_is_crossed() -> None:
    observed_chunks: list[bytes] = []

    class ChunkStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in (b"12345", b"67890", b"must-not-be-read"):
                observed_chunks.append(chunk)
                yield chunk

    adapter = QQDashboardHomeHttpAdapter(
        base_url="http://127.0.0.1:8787",
        operator_token="machine-read-secret",
        max_response_bytes=8,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                stream=ChunkStream(),
                headers={"Content-Type": "application/json", "ETag": '"irrelevant"'},
            )
        ),
    )

    with pytest.raises(DashboardHomeSourceError) as raised:
        await adapter.fetch()

    assert raised.value.code == "response_too_large"
    assert observed_chunks == [b"12345", b"67890"]


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com:8787",
        "https://owner.example:8787",
        "http://user:password@127.0.0.1:8787",
        "http://127.0.0.1:8787/health",
        "http://127.0.0.1:8787?debug=1",
        "ftp://127.0.0.1:8787",
    ],
)
def test_daemon_adapter_rejects_unsafe_owner_origins(base_url: str) -> None:
    with pytest.raises(ValueError):
        QQDashboardHomeHttpAdapter(
            base_url=base_url,
            operator_token="machine-read-secret",
        )
