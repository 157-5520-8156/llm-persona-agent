"""One real host owns setup, memory, preview and cold restart (offline provider)."""
from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
from companion_daemon.world_v2.qq_c2c_host import qq_c2c_world_id
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_character_prehistory import reapprove, reviewed_archive
from test_longitudinal_cli import _cli
from test_memory_candidate_authority import salience
from test_world_stimulus_life_intent import _http_result


def archive_for(primary_user_id):
    value = reviewed_archive().document.model_dump(mode="json")
    value.update(world_id=qq_c2c_world_id(primary_user_id), actor_ref="agent:companion")
    return reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(value)))


@pytest.mark.asyncio
async def test_setup_memory_and_authenticated_preview_share_one_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "fixture-debug-key")
    monkeypatch.setenv("DEEPSEEK_CHARACTER_THINKING_ENABLED", "false")
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    primary = "clean-demo-test"
    archive = archive_for(primary)
    archive_path = tmp_path / "reviewed.json"
    archive_path.write_text(archive.model_dump_json())
    token = "offline-preview-token-never-in-artifacts"
    token_file = tmp_path / "token"
    token_file.write_text(token)
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({"scenario_id": "same-owner-demo", "started_at": "2026-09-20T10:00:00Z",
                                    "duration_minutes": 5, "turns": []}))
    requests = []
    original_transport = httpx.AsyncHTTPTransport

    async def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        material = json.loads(body["messages"][-1]["content"])
        assert material["inner_turn"]["purpose"] == "fact_memory_retention"
        capability = material["capability_manifest"]
        assert capability["payload"]["verified_source_text"] == archive.document.records[0].statement
        return _http_result(body, {"status": "decision", "summary": "fixture retains reviewed history",
            "attended_source_refs": capability["source_refs"], "recall_query": None, "proposals": [],
            "decision": {"source_refs": capability["source_refs"], "payload": {"retain": True,
                "cue_kind": "identity", "retention_rationales": ["identity_relevance"],
                "salience": salience().model_dump(mode="json", exclude={"matrix_digest", "matrix_version"})}}})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(provider))
    snapshots = []
    preview_url = None

    async def decide(observation):
        nonlocal preview_url
        bootstrap, = observation["steps"]
        setup = bootstrap["setup"]
        assert setup["prehistory_initialization"][0]["status"] == "retained"
        assert setup["remaining_setup_steps"] == 0
        preview_url = setup["dashboard"]["url"]
        base = preview_url.removesuffix("/dashboard")
        async with httpx.AsyncClient(transport=original_transport(), base_url=base) as client:
            assert (await client.get("/world-v2/dashboard/home")).status_code == 403
            login = await client.post("/world-v2/dashboard/session", data={"operator_token": token},
                                      headers={"Origin": base})
            assert login.status_code in {302, 303}
            response = await client.get("/world-v2/dashboard/home")
            assert response.status_code == 200, response.text
            snapshot = response.json()
            snapshots.append(snapshot)
            assert snapshot["world_id"] == qq_c2c_world_id(primary)
            assert (await client.get("/world-v2/dashboard/home",
                                    headers={"If-None-Match": response.headers["ETag"]})).status_code == 304
            assert (await client.get("/health")).status_code == 404
            assert (await client.post("/chat", json={"text": "must not dispatch"})).status_code == 404
        assert len(requests) == 1
        return None

    cli = _cli()
    output = tmp_path / "run"
    manifest = await cli.run(cli.parse_options([
        "--scenario", str(scenario), "--output", str(output), "--interactive",
        "--primary-user-id", primary, "--reviewed-prehistory", str(archive_path),
        "--initialize-prehistory-steps", "1", "--dashboard-port", "0",
        "--dashboard-token-file", str(token_file), "--model-mode", "real-provider", "--allow-real-provider",
    ]), next_command=decide)
    assert manifest["stop_reason"] == "operator_stopped", manifest.get("stop_reason")
    assert manifest["replay"]["replay_hash_matches"]
    assert snapshots and len(requests) == 1
    with sqlite3.connect(output / "world.sqlite") as connection:
        assert connection.execute("SELECT world_id FROM world_v2_heads").fetchall() == [(qq_c2c_world_id(primary),)]
    ledger = SQLiteWorldLedger(path=output / "world.sqlite", world_id=qq_c2c_world_id(primary))
    try:
        projection = ledger.project()
        assert len(projection.prehistory_records) == len(projection.memory_candidates) == 1
        assert projection.memory_candidates[0].values.source_bindings[0].source_kind == "prehistory"
        assert ledger.rebuild().semantic_hash == projection.semantic_hash
    finally:
        ledger.close()
    for filename in ("manifest.json", "timeline.jsonl", "provider-usage.json"):
        assert token not in (output / filename).read_text()
    async with httpx.AsyncClient(transport=original_transport()) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get(preview_url)


@pytest.mark.asyncio
async def test_wrong_world_archive_rejected_before_owner_or_network(tmp_path, monkeypatch):
    archive_path = tmp_path / "reviewed.json"
    archive_path.write_text(archive_for("another-demo").model_dump_json())
    cli = _cli()
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="another World"):
        await cli.run(cli.parse_options(["--output", str(output), "--primary-user-id", "this-demo",
                                          "--reviewed-prehistory", str(archive_path)]))
    assert not output.exists()


def test_dashboard_token_must_be_compatible_with_existing_authentication(tmp_path):
    from companion_daemon.world_v2.longitudinal_demo import LongitudinalDemoSetup

    token_file = tmp_path / "token"
    token_file.write_text("无法用于ASCII鉴权的口令")
    with pytest.raises(ValueError, match="token file is invalid"):
        LongitudinalDemoSetup(settings=None, dashboard_port=0, token_file=token_file)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_request", [False, True])
async def test_detach_joins_snapshot_before_owner_shutdown(cancel_request):
    import asyncio
    from companion_daemon.world_v2.dashboard_operator_http import DashboardHomeSourceError
    from companion_daemon.world_v2.longitudinal_demo import SameOwnerDashboardSource

    entered, resume = asyncio.Event(), asyncio.Event()

    class Owner:
        async def dashboard_home_snapshot(self):
            entered.set()
            await resume.wait()
            return None

    source = SameOwnerDashboardSource()
    old = Owner()
    source.attach(old)
    fetching = asyncio.create_task(source.fetch())
    await entered.wait()
    if cancel_request:
        fetching.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fetching
    detaching = asyncio.create_task(source.detach(old))
    await asyncio.sleep(0)
    assert not detaching.done(), "The database must remain open until the snapshot read finishes"
    with pytest.raises(DashboardHomeSourceError):
        await source.fetch()
    resume.set()
    await detaching
    if not cancel_request:
        with pytest.raises(DashboardHomeSourceError):
            await fetching
    source.attach(Owner())
