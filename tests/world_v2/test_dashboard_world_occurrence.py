"""Dashboard reads accepted environment bytes, never candidate/role audit prose."""

from datetime import timedelta
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from companion_daemon.config import Settings
from companion_daemon.world_v2.dashboard_home_snapshot import DashboardHomeSnapshotModule
from companion_daemon.world_v2.dashboard_world_occurrence import read_dashboard_world_occurrence
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.life_content_events import (
    LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED,
    LifeContentUserChannelAuthorityLimitedPayload,
)
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.longitudinal_demo import (
    SameOwnerDashboardSource,
    build_journey_dashboard_app,
)
from companion_daemon.world_v2.schemas import ProjectionCursor, WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import OWNER, WORLD_ID
from test_world_consequence_aftermath import _settled_author_cohort


def _cursor(projection):
    return ProjectionCursor(**{key: getattr(projection, key) for key in (
        "world_revision", "deliberation_revision", "ledger_sequence",
    )})


class _SelectedContentOnly:
    def __init__(self, store, occurrence, *, fault=None):
        self.store = store
        self.fault = fault
        self.reads = []
        self.allowed = {occurrence.result_payload_ref, next(
            item.content_ref for item in occurrence.candidate_outcomes
            if item.candidate_result_ref == occurrence.settled_outcome_ref
        )}

    def read_exact(self, *, content_ref):
        assert content_ref in self.allowed, "dashboard must not read raw model or role content"
        self.reads.append(content_ref)
        stored = self.store.read_exact(content_ref=content_ref)
        if self.fault == "missing":
            return None
        if self.fault == "hash":
            return SimpleNamespace(content_ref=stored.content_ref, content_kind=stored.content_kind,
                                   content_payload_hash=stored.content_payload_hash,
                                   text=stored.text + "forged")
        return stored


@pytest_asyncio.fixture
async def settled(tmp_path):
    path = tmp_path / "world.sqlite"
    settlement, body = await _settled_author_cohort(path)
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    projection = ledger.project()
    occurrence, = projection.world_occurrences
    assert occurrence.settlement_event_ref == settlement
    try:
        yield SimpleNamespace(
            path=path, ledger=ledger, store=store, projection=projection, occurrence=occurrence,
            body=json.loads(body)["environment_text"],
        )
    finally:
        store.close()
        ledger.close()


def _read(state, **changes):
    args = dict(ledger=state.ledger, store=state.store, projection=state.projection,
                cursor=_cursor(state.projection), occurrence=state.occurrence,
                actor_ref=OWNER, viewer_privacy_ceiling="shareable")
    args.update(changes)
    return read_dashboard_world_occurrence(**args)


@pytest.mark.asyncio
async def test_selected_published_environment_is_bound_to_real_events_and_read_only(settled):
    before = settled.ledger.export_replay_evidence()
    store = _SelectedContentOnly(settled.store, settled.occurrence)
    reading = _read(settled, store=store)
    assert (reading.status, reading.text, reading.truncated) == ("read", settled.body, False)
    assert _read(settled, store=store) == reading
    assert set(store.reads) == store.allowed
    short = _read(settled, store=store, max_characters=8)
    assert short.text == settled.body[:7] + "…"
    assert short.truncated
    assert settled.ledger.export_replay_evidence() == before
    cold = SQLiteWorldLedger(path=settled.path, world_id=WORLD_ID)
    try:
        assert cold.export_replay_evidence() == before
        assert cold.rebuild().semantic_hash == before.projection.semantic_hash
    finally:
        cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["actor", "world", "cursor", "unpublished", "descriptor_hash",
                                  "descriptor_ref", "settlement_hash", "settlement_ref", "unselected"])
async def test_missing_or_mismatched_authority_never_displays_body(settled, fault):
    projection, occurrence = settled.projection, settled.occurrence
    changes = {}
    if fault == "actor":
        changes["actor_ref"] = "actor:someone-else"
    elif fault == "world":
        changes["ledger"] = SimpleNamespace(world_id="world:other")
    elif fault == "cursor":
        changes["cursor"] = _cursor(projection).model_copy(update={"ledger_sequence": 0})
    elif fault == "unpublished":
        projection = projection.model_copy(update={"life_content_descriptors": ()})
    elif fault.startswith("descriptor"):
        descriptor, = projection.life_content_descriptors
        key = "content_payload_hash" if fault.endswith("hash") else "descriptor_event_ref"
        value = "f" * 64 if fault.endswith("hash") else "event:missing-publication"
        projection = projection.model_copy(update={"life_content_descriptors": (
            descriptor.model_copy(update={key: value}),
        )})
    else:
        key = {"settlement_hash": "settlement_payload_hash", "settlement_ref": "settlement_event_ref",
               "unselected": "settled_outcome_ref"}[fault]
        occurrence = occurrence.model_copy(update={key: "f" * 64})
        projection = projection.model_copy(update={"world_occurrences": (occurrence,)})
    changes.update(projection=projection, occurrence=occurrence)
    reading = _read(settled, **changes)
    assert reading.status == "unavailable"
    assert reading.text is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["settlement", "publication"])
@pytest.mark.parametrize("fault", ["future_commit", "wrong_event_body"])
async def test_source_event_lookup_must_match_captured_prefix(settled, monkeypatch, source, fault):
    ref = settled.occurrence.settlement_event_ref if source == "settlement" else (
        settled.projection.life_content_descriptors[0].descriptor_event_ref
    )
    original = settled.ledger.lookup_event_commit

    def lookup(event_ref):
        located = original(event_ref)
        if event_ref != ref:
            return located
        event, commit = located
        if fault == "future_commit":
            commit = commit.model_copy(update={"ledger_sequence": settled.projection.ledger_sequence + 1})
        else:
            event = event.model_copy(update={"payload_json": "{}"})
        return event, commit

    monkeypatch.setattr(settled.ledger, "lookup_event_commit", lookup)
    reading = _read(settled)
    assert reading.status == "unavailable"
    assert reading.text is None


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["occurrence", "candidate", "descriptor"])
async def test_privacy_ceiling_precedes_sidecar_reads(settled, boundary):
    occurrence, projection = settled.occurrence, settled.projection
    if boundary == "occurrence":
        occurrence = occurrence.model_copy(update={"visibility": "private"})
    elif boundary == "candidate":
        occurrence = occurrence.model_copy(update={"candidate_outcomes": tuple(
            item.model_copy(update={"privacy_class": "private"}) for item in occurrence.candidate_outcomes
        )})
    else:
        projection = projection.model_copy(update={"life_content_descriptors": tuple(
            item.model_copy(update={"privacy_class": "private"}) for item in projection.life_content_descriptors
        )})
    projection = projection.model_copy(update={"world_occurrences": (occurrence,)})
    store = _SelectedContentOnly(settled.store, occurrence)
    reading = _read(settled, projection=projection, occurrence=occurrence, store=store)
    assert reading.status == "withheld" and reading.text is None
    assert store.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "hash"])
async def test_body_missing_or_corrupt_never_falls_back_to_other_content(settled, fault):
    store = _SelectedContentOnly(settled.store, settled.occurrence, fault=fault)
    reading = _read(settled, store=store)
    assert reading.status == "unavailable" and reading.text is None


@pytest.mark.asyncio
async def test_committed_user_channel_limit_is_preserved(settled):
    payload = LifeContentUserChannelAuthorityLimitedPayload(
        content_refs=(settled.occurrence.result_payload_ref,),
        inspected_media_delivery_count=0, inspected_media_delivery_action_count=0,
    ).model_dump(mode="json")
    event = WorldEvent.from_payload(
        schema_version="world-v2.1", event_id="event:dashboard-limit", world_id=WORLD_ID,
        event_type=LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED,
        logical_time=settled.projection.logical_time, created_at=settled.projection.logical_time,
        actor="system:dashboard-test", source="dashboard-test", trace_id="trace:dashboard-limit",
        causation_id="cause:dashboard-limit", correlation_id="correlation:dashboard-limit",
        idempotency_key=domain_idempotency_key(
            event_type=LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED, world_id=WORLD_ID, payload=payload,
        ), payload=payload,
    )
    settled.ledger.commit_at_cursor((event,), expected_cursor=_cursor(settled.projection))
    projection = settled.ledger.project()
    store = _SelectedContentOnly(settled.store, settled.occurrence)
    reading = _read(settled, projection=projection, cursor=_cursor(projection), store=store)
    assert reading.status == "withheld" and reading.text is None
    assert store.reads == []


@pytest.mark.asyncio
async def test_authenticated_same_owner_http_shows_environment_and_truncation_without_effects(tmp_path, monkeypatch):
    from test_world_stimulus_life_intent import NOW, WORLD, _build, _model
    from test_world_stimulus_life_response import _ResponseHTTP
    import test_world_consequence_aftermath as cohort

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "owner.sqlite"
    provider = _ResponseHTTP(fault="provider_failure")
    model = _model(provider)
    bootstrap = _build(path, model)
    await bootstrap.aclose()
    draft = cohort._draft
    body = "院内落着细小冰粒，雨棚下仍能听见滴水声。" * 20

    def long_environment(wake):
        value = draft(wake)
        for outcome in value["outcomes"]:
            outcome["world_consequence"]["environment_text"] = body
        return value

    monkeypatch.setattr(cohort, "_draft", long_environment)
    await _settled_author_cohort(path, world_id=WORLD, start=NOW + timedelta(minutes=1))
    owner = _build(path, model)
    source = SameOwnerDashboardSource()
    source.attach(owner)
    token = "fixture-dashboard-token-never-in-response"
    app = build_journey_dashboard_app(settings=Settings(
        _env_file=None, database_path=tmp_path / "unused.sqlite",
        WORLD_V2_DASHBOARD_AUTH_ENABLED=True, WORLD_V2_DASHBOARD_OPERATOR_TOKEN=token,
    ), source=source)
    before = owner.export_replay_evidence()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            assert (await client.get("/world-v2/dashboard/home")).status_code == 403
            response = await client.post("/world-v2/dashboard/session", data={"operator_token": token},
                                         headers={"Origin": "http://127.0.0.1"})
            assert response.status_code in {302, 303}
            response = await client.get("/world-v2/dashboard/home")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["world_id"] == WORLD
            assert payload["cursor"] == _cursor(before.projection).model_dump(mode="json")
            occurrence, = [item for item in payload["sections"]["overview_life"]["data"]["highlights"]
                           if item["kind"] == "world_occurrence"]
            assert occurrence["detail"] == body[:239] + "…"
            values = {item["key"]: item for item in occurrence["values"]}
            assert values["world_environment_status"]["value"] == "read"
            assert values["world_environment_truncated"]["value_label"] == "已节选"
            assert token not in response.text
            repeated = await client.get("/world-v2/dashboard/home",
                                        headers={"If-None-Match": response.headers["ETag"]})
            assert repeated.status_code == 304
        assert provider.requests == []
        assert owner.export_replay_evidence() == before
    finally:
        await source.detach(owner)
        await owner.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_no_reader_and_unsettled_remain_explicit(settled):
    snapshot = await DashboardHomeSnapshotModule(
        ledger=settled.ledger, deployment_id="deployment:test", boot_id="boot:test",
    ).capture()
    occurrence, = [item for item in snapshot.to_payload()["sections"]["overview_life"]["data"]["highlights"]
                   if item["kind"] == "world_occurrence"]
    assert occurrence["detail"] is None
    assert next(item["value"] for item in occurrence["values"]
                if item["key"] == "world_environment_status") == "not_read"
    active = settled.occurrence.model_copy(update={"status": "active"})
    projection = settled.projection.model_copy(update={"world_occurrences": (active,)})
    store = _SelectedContentOnly(settled.store, active)
    reading = _read(settled, projection=projection, occurrence=active, store=store)
    assert reading.status == "not_settled" and reading.text is None
    assert store.reads == []
