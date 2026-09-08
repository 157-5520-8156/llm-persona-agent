"""Owner display from accepted temporary-ledger sources, without author calls."""

from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.dashboard_home_snapshot import DashboardHomeSnapshotModule
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_day_open_life_intent_runtime import INTENT, _accept, _choice, _record, _seed
from test_life_projection import WORLD_ID


async def _capture(ledger):
    return (await DashboardHomeSnapshotModule(
        ledger=ledger, deployment_id="deployment:recording", boot_id="boot:recording",
        clock=lambda: ledger.project().logical_time,
    ).capture()).to_payload()


def _highlights(payload, section="overview_life"):
    return payload["sections"][section]["data"]["highlights"]


def _values(item):
    return {value["key"]: value["value"] for value in item["values"]}


@pytest.mark.asyncio
async def test_owner_reads_original_day_intention_after_reopen_without_advancing_world(tmp_path):
    path = tmp_path / "day.sqlite"
    ledger, first = _seed(path)
    proposal, cursor = _record(ledger, *_choice(ledger, first))
    _accept(ledger, proposal, cursor)
    before = ledger.project()
    payload = await _capture(ledger)
    plan, = [item for item in _highlights(payload) if item["kind"] == "plan"]

    assert _values(plan).get("intention") == INTENT["intention"]
    assert plan["title"] == INTENT["intention"]
    assert plan["status_code"] == "planned"
    assert _values(plan)["intent_source"] == "day_open"
    assert _values(plan)["execution_scope"] == "self_directed"
    assert _values(plan)["selected_at"] == first.logical_time.isoformat()
    assert _values(plan)["scheduled_start"] == (first.logical_time + timedelta(seconds=15)).isoformat()
    assert _values(plan)["scheduled_end"] == (first.logical_time + timedelta(seconds=135)).isoformat()
    assert not any(item["kind"] == "experience" for item in _highlights(payload))
    assert ledger.project() == before
    encoded = json.dumps(payload, ensure_ascii=False)
    assert proposal.proposal_id not in encoded
    assert before.plans[0].plan_id not in encoded
    assert "snapshot_hash" not in json.dumps(plan)
    ledger.close()
    reopened = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    try:
        assert await _capture(reopened) == payload
        assert reopened.project() == before
    finally:
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("privacy", ["private", "withhold"])
async def test_owner_distinguishes_memory_review_from_retention_and_hides_withheld_values(tmp_path, privacy):
    from test_memory_candidate_authority import (
        NOW, WORLD, candidate, initialized_ledger_with_fact, mutation, record_memory_accept_mutate,
    )

    ledger, source = initialized_ledger_with_fact(SQLiteWorldLedger(path=tmp_path / "memory.sqlite", world_id=WORLD))
    try:
        opened = candidate(source, privacy_ceiling=privacy, review_due_at=NOW + timedelta(days=1))
        record_memory_accept_mutate(ledger, mutation(
            opened, operation="open", evaluated_world_revision=ledger.project().world_revision,
        ))
        pending = next(item for item in _highlights(await _capture(ledger), "facts_memory_inner")
                       if item["kind"] == "memory_candidate")
        if privacy == "private":
            assert pending["status_label"] == "待复核"
            assert _values(pending)["retention_rationales"] == "以后有用"
            assert _values(pending)["source_kind"] == "fact"
            assert _values(pending)["review_due_at"] == (NOW + timedelta(days=1)).isoformat()
        else:
            assert pending["values"] == [] and pending["status_code"] is None
        accepted = candidate(source, revision=2, status="active", privacy_ceiling=privacy,
                             accepted_event_ref="event:memory:accepted", reviewed_at=NOW,
                             review_due_at=NOW + timedelta(days=1))
        record_memory_accept_mutate(ledger, mutation(
            accepted, operation="accept", before=opened,
            evaluated_world_revision=ledger.project().world_revision,
        ))
        before = ledger.project()
        payload = await _capture(ledger)
        active = next(item for item in _highlights(payload, "facts_memory_inner") if item["kind"] == "memory_candidate")
        if privacy == "private":
            assert active["status_label"] == "已保留"
            assert _values(active)["reviewed_at"] == NOW.isoformat()
        else:
            assert active["values"] == [] and active["status_code"] is None
        assert source.source_id not in json.dumps(payload)
        assert ledger.project() == before
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_owner_activity_start_and_completion_keep_world_response_intention_separate(tmp_path):
    from companion_daemon.world_v2.activity_lifecycle_runtime import (
        ActivityLifecycleAcceptanceRuntime, ActivityLifecycleProposalRecorder,
    )
    from companion_daemon.world_v2.activity_lifecycle_worker import ActivityLifecycleWorker
    from companion_daemon.world_v2.chat_life_intent_runtime import CompositeActivityPlanMaterialReader
    from companion_daemon.world_v2.life_ecology_activity import ActivityOpeningCatalog
    from companion_daemon.world_v2.life_ecology_contract import LifeEcologyRunKey
    from companion_daemon.world_v2.life_ecology_trigger_store import LedgerLifeEcologyTriggerStore
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime
    from test_activity_lifecycle_runtime import _Interior
    from test_world_life_intent_runtime import ACTOR, INTENT as WORLD_INTENT, _advance_clock, _audit, _seed as seed_world

    class ChoosingInterior(_Interior):
        async def consider(self, opportunity):
            result = await super().consider(opportunity)
            openings = opportunity.capability_manifest.payload["openings"]
            chosen = next((item for item in openings if item["safe_summary"].startswith(
                "finish the current abstract activity"
            )), openings[0])
            return result.model_copy(update={"decision": {
                **result.decision, "payload": {**result.decision["payload"],
                                              "selected_token": chosen["opening_token"]},
            }})

    path = tmp_path / "world.sqlite"
    ledger, issuer = seed_world(path)
    proposal, cursor = _audit(ledger)
    runtime = WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR)
    runtime.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id)
    initial_experiences = ledger.project().experiences
    interior = ChoosingInterior()
    try:
        for seconds, status in ((1, "active"), (120, "completed")):
            wake = _advance_clock(ledger, seconds, status)
            claim = await LedgerLifeEcologyTriggerStore(ledger=ledger, owner_id="test:lifecycle").claim_or_join(
                key=LifeEcologyRunKey(world_id=WORLD_ID, wake_event_ref=wake, catalog_version="life-ecology.1"),
                trace_id="trace:lifecycle", correlation_id="correlation:lifecycle",
            )
            worker = ActivityLifecycleWorker(
                ledger=ledger, catalog=ActivityOpeningCatalog(owner_actor_ref=ACTOR),
                character_interior=interior, owner_actor_ref=ACTOR,
                proposal_recorder=ActivityLifecycleProposalRecorder(ledger=ledger),
                acceptance_runtime=ActivityLifecycleAcceptanceRuntime(ledger=ledger, batch_issuer=issuer),
                ecology_catalog_version="life-ecology.1",
                plan_material_reader=CompositeActivityPlanMaterialReader(runtime),
            )
            assert (await worker.advance_once(
                wake_event_ref=wake, trigger_id=claim.trigger_id, logical_time=ledger.project().logical_time,
                actor="test:lifecycle", trace_id="trace:lifecycle", correlation_id="correlation:lifecycle",
            )).status == "transitioned"
            before = ledger.project()
            payload = await _capture(ledger)
            (tmp_path / f"owner-{status}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            plan = next(item for item in _highlights(payload) if "intention" in _values(item))
            assert plan["status_code"] == status
            assert _values(plan)["intention"] == WORLD_INTENT["intention"]
            assert _values(plan)["intent_source"] == "world"
            assert plan["occurred_at"] == before.logical_time.isoformat().replace("+00:00", "Z")
            occurrence = next(item for item in _highlights(payload) if item["kind"] == "world_occurrence")
            assert _values(occurrence).get("world_environment_status") == "not_read"
            assert occurrence["detail"] is None
            assert before.experiences == initial_experiences
            assert ledger.project() == before
        assert len(interior.opportunities) == 2
    finally:
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_creation", "missing_model", "bad_proposal_hash", "bad_proposal_body", "future_commit", "withhold"])
async def test_owner_keeps_lifecycle_metadata_but_never_borrows_unproved_intention(tmp_path, fault):
    ledger, first = _seed(tmp_path / "proof.sqlite")
    proposal, cursor = _record(ledger, *_choice(ledger, first))
    _accept(ledger, proposal, cursor)
    original = ledger.project()
    projection = original
    if fault == "missing_model":
        projection = original.model_copy(update={"model_result_audits": ()})
    elif fault == "withhold":
        projection = original.model_copy(update={"plans": (
            original.plans[0].model_copy(update={"privacy_class": "withhold"}),
        )})

    class ReadOnlyView:
        world_id = WORLD_ID
        blocks_event_loop = False

        def project(self):
            return projection

        def lookup_event_commit(self, ref):
            located = ledger.lookup_event_commit(ref)
            if located is None:
                return None
            event, commit = located
            if fault == "missing_creation" and event.event_type == "ActivityPlanned":
                return None
            if fault == "bad_proposal_hash" and event.event_type == "ProposalRecorded":
                return event.model_copy(update={"payload_hash": "f" * 64}), commit
            if fault == "bad_proposal_body" and event.event_type == "ProposalRecorded":
                return event.model_copy(update={"payload_json": "{}"}), commit
            if fault == "future_commit":
                return event, commit.model_copy(update={"ledger_sequence": original.ledger_sequence + 1})
            return located

    try:
        payload = await _capture(ReadOnlyView())
        plan, = [item for item in _highlights(payload) if item["kind"] == "plan"]
        assert "intention" not in _values(plan)
        assert INTENT["intention"] not in json.dumps(payload, ensure_ascii=False)
        if fault == "withhold":
            assert plan["values"] == [] and plan["status_code"] is None
        else:
            assert plan["status_code"] == "planned"
        assert ledger.project() == original
    finally:
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("response_text", [None, "PRIVATE_RESPONSE_CANARY：我想先留着自己的感受。"])
async def test_owner_keeps_exact_experience_sources_distinct_without_exposing_private_response(
    tmp_path, monkeypatch, response_text,
):
    from companion_daemon.world_v2.character_life_experience_runtime import CharacterLifeExperienceRuntime
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from test_character_life_experience_runtime import _accepted_response, _cursor
    from test_world_stimulus_life_intent import ACTOR, WORLD

    # The shared public app fixture installs only httpx.MockTransport. Capture
    # receives no model or content-store port and runs after that app is closed.
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "experience.sqlite"
    source_ref, response_ref = await _accepted_response(path, response_text)
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        CharacterLifeExperienceRuntime(ledger=ledger, content_store=store, owner_actor_ref=ACTOR).accept(
            world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref,
        )
        before = ledger.project()
        payload = await _capture(ledger)
        experience, = [item for item in _highlights(payload) if item["kind"] == "experience"]
        values = _values(experience)
        assert values.get("source_kind") == "world_life_response"
        assert values["world_environment_status"] == "not_read"
        assert values["character_response_status"] == ("explicit_none" if response_text is None else "recorded_private")
        assert experience["detail"] is None
        encoded = json.dumps(payload, ensure_ascii=False)
        assert "PRIVATE_RESPONSE_CANARY" not in encoded
        assert source_ref not in encoded and response_ref not in encoded
        assert ledger.project() == before
    finally:
        store.close()
        ledger.close()
