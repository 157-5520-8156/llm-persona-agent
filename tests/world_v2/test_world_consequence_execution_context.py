"""Readable execution scope comes from the original role's accepted intention."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

import current_activity_fixture
from current_activity_fixture import accepted_current_activity
from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore


def _read(ledger, store, source, *, pinned=None, actor="actor:companion"):
    from companion_daemon.world_v2.world_consequence_execution_context import (
        build_world_consequence_execution_materials,
    )

    return build_world_consequence_execution_materials(
        ledger=ledger, content_store=store, pinned_state=pinned or ledger.project(),
        actor_ref=actor, source_events=(source,),
    )


@pytest.mark.asyncio
async def test_open_life_execution_reads_full_role_intention_without_world_author_candidates(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    intention = "我想仔细读公告里的投稿要求。" * 50
    monkeypatch.setattr(current_activity_fixture, "CURRENT_ACTIVITY_INTENTION", intention)
    ledger, store, _, source_ref = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    from companion_daemon.world_v2.world_consequence_execution_context import (
        build_world_consequence_execution_materials,
    )

    source = ledger.lookup_event_commit(source_ref)[0]
    values = build_world_consequence_execution_materials(
        ledger=ledger, content_store=store, pinned_state=ledger.project(),
        actor_ref="actor:companion", source_events=(source,),
    )
    assert len(values) == 1 and values[0].status == "available"
    material = values[0]
    assert material.authorized_intention.text == intention
    assert len(material.authorized_intention.text) > 480
    assert material.authorized_intention.model_result_ref
    assert material.authorized_intention.proposal_event_ref
    rendered = material.model_dump_json()
    assert current_activity_fixture.UNSETTLED_OUTCOME_TEXT not in rendered
    assert current_activity_fixture.CURRENT_ACTIVITY_PREMISE not in rendered
    assert material.authorized_intention.epistemic_scope == (
        "authorized_intention_not_embedded_history_or_execution_success"
    )


def test_receipt_has_no_readable_action_scope_without_a_content_reader(tmp_path):
    from companion_daemon.world_v2.world_consequence_execution_context import (
        build_world_consequence_execution_materials,
    )
    from test_world_consequence_authority import _receipt_case

    ledger, _, case = _receipt_case(tmp_path)
    values = build_world_consequence_execution_materials(
        ledger=ledger, content_store=InMemoryImmutableLifeContentStore(),
        pinned_state=ledger.project(), actor_ref="actor:companion",
        source_events=case["source_events"],
    )
    assert values[0].status == "unavailable"
    assert values[0].unavailable_reason == "receipt_content_reader_not_installed"
    assert values[0].authorized_intention is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "resumed"])
async def test_life_intention_survives_cold_restart_and_later_clock_without_current_fallback(
    tmp_path, status,
):
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from test_life_development_runtime import _seed_clock

    path = tmp_path / "restart.sqlite"
    ledger, store, _, source_ref = await accepted_current_activity(sqlite_path=path, status=status)
    pinned = ledger.project()
    source = ledger.lookup_event_commit(source_ref)[0]
    before = _read(ledger, store, source, pinned=pinned)
    _seed_clock(ledger, event_id="event:clock:after-author", logical_time_from=pinned.logical_time,
                logical_time=pinned.logical_time + timedelta(minutes=2))
    world_id = ledger.world_id
    ledger.close()
    store.close()
    reopened = SQLiteWorldLedger(path=path, world_id=world_id)
    reopened_store = SQLiteImmutableLifeContentStore(path=path, world_id=world_id)
    after = _read(reopened, reopened_store, source, pinned=pinned)
    assert after == before
    assert after[0].status == "available"
    assert after[0].execution_binding.source_event_type == (
        "ActivityResumed" if status == "resumed" else "ActivityStarted"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "wrong_hash", "wrong_text"])
async def test_life_intention_requires_complete_original_sidecar(tmp_path, mode):
    ledger, store, _, source_ref = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    source = ledger.lookup_event_commit(source_ref)[0]
    reads = []

    class UnavailableStore:
        def read_exact(self, *, content_ref):
            reads.append(content_ref)
            assert "character-intention:" in content_ref
            original = store.read_exact(content_ref=content_ref)
            if mode == "missing":
                return None
            return SimpleNamespace(
                content_ref=original.content_ref, content_kind=original.content_kind,
                content_payload_hash="f" * 64 if mode == "wrong_hash" else original.content_payload_hash,
                text="A different attempt" if mode == "wrong_text" else original.text,
            )

    value = _read(ledger, UnavailableStore(), source)[0]
    assert reads
    assert value.status == "unavailable" and value.authorized_intention is None
    assert current_activity_fixture.CURRENT_ACTIVITY_INTENTION not in value.model_dump_json()


@pytest.mark.asyncio
async def test_later_model_audit_lookup_cannot_fill_the_original_source_prefix(tmp_path):
    ledger, store, _, source_ref = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    pinned = ledger.project()
    source = ledger.lookup_event_commit(source_ref)[0]

    class LaterModelLookup:
        world_id = ledger.world_id

        def project_at(self, cursor):
            return ledger.project_at(cursor)

        def lookup_event_commit(self, event_ref):
            found = ledger.lookup_event_commit(event_ref)
            if found and found[0].event_type == "ModelResultRecorded":
                return found[0], found[1].model_copy(update={
                    "ledger_sequence": pinned.ledger_sequence + 1,
                })
            return found

    value = _read(LaterModelLookup(), store, source, pinned=pinned)[0]
    assert value.status == "unavailable"
    assert value.unavailable_reason == "character_audit_unavailable"


@pytest.mark.asyncio
async def test_withheld_activity_and_other_actor_do_not_gain_intention_text(tmp_path):
    ledger, store, _, source_ref = await accepted_current_activity(
        sqlite_path=tmp_path / "life.sqlite", privacy_class="withhold",
    )
    source = ledger.lookup_event_commit(source_ref)[0]
    own = _read(ledger, store, source)[0]
    assert own.status == "unavailable" and own.unavailable_reason == "withheld"
    with pytest.raises(ValueError, match="activity_actor_or_plan"):
        _read(ledger, store, source, actor="actor:another")


def test_generic_started_plan_without_role_text_does_not_gain_scope_from_its_label(tmp_path):
    from test_world_consequence_authority import _activity, _transition

    ledger, _ = _activity(tmp_path)
    source = _transition(ledger)
    value = _read(ledger, InMemoryImmutableLifeContentStore(), source)[0]
    assert value.status == "unavailable"
    assert value.unavailable_reason == "activity_intention_unavailable"
    assert value.authorized_intention is None


def test_world_intent_uses_its_original_character_proposal_and_model(tmp_path):
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime
    from test_world_life_intent_runtime import _seed, _audit, INTENT
    from test_life_projection import WORLD_ID, commit, event, evidence, mutation, register_operator_observations

    ledger, _ = _seed(tmp_path / "world.sqlite")
    proposal, cursor = _audit(ledger)
    WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref="actor:companion").accept(
        world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id,
    )
    plan = next(item for item in ledger.project().plans if item.plan_id.startswith("plan:world-life-intent:"))
    register_operator_observations(ledger, "operator:execution")
    source = event("world-intent:started", "ActivityStarted", {
        **mutation("world-intent:started", expected_revision=1, evidence_refs=[
            evidence("operator:execution", "operator_observation", "current_fact"),
        ]),
        "plan_id": plan.plan_id,
        "transitioned_at": ledger.project().logical_time.isoformat(),
        "reason_ref": "operator:execution",
    })
    commit(ledger, [source])
    value = _read(ledger, InMemoryImmutableLifeContentStore(), source)[0]
    assert value.status == "available"
    assert value.authorized_intention.text == INTENT["intention"]
    assert value.authorized_intention.proposal_event_ref == "proposal:world-intent:one"
    assert value.authorized_intention.model_result_event_ref == "model:world-intent:one"


@pytest.mark.asyncio
async def test_real_chat_carrier_plan_reads_original_intention_through_public_mock_journey(
    tmp_path, monkeypatch,
):
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from test_chat_life_intent_runtime import _run_http_journey, INTENT

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    result, events, _, output, _ = await _run_http_journey(tmp_path, monkeypatch)
    assert result["completed"], result["stop_reason"]
    started = next(item for item in events if item["event_type"] == "ActivityStarted")
    ledger = SQLiteWorldLedger(path=output / "world.sqlite", world_id=started["world_id"])
    source = ledger.lookup_event_commit(started["event_id"])[0]
    owner = next(item.owner_actor_ref for item in ledger.project().plans
                 if item.plan_id == source.payload()["plan_id"])
    value = _read(ledger, InMemoryImmutableLifeContentStore(), source, actor=owner)[0]
    assert value.status == "available"
    assert value.authorized_intention.text == INTENT["intention"]
    model_event = ledger.lookup_event_commit(value.authorized_intention.model_result_event_ref)[0]
    assert model_event.event_type == "ModelResultRecorded"
    assert model_event.payload_hash == value.authorized_intention.model_result_payload_hash
    assert ledger.lookup_event_commit(value.authorized_intention.proposal_event_ref)
