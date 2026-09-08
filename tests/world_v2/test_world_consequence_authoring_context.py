"""Only readable execution sources inside the original manifest are offered."""

from __future__ import annotations

from datetime import timedelta

import pytest

import current_activity_fixture
from current_activity_fixture import accepted_current_activity
from companion_daemon.world_v2.life_development_draft import LifeDevelopmentCapabilityManifest
from companion_daemon.world_v2.schemas import ProjectionCursor


def _manifest(ledger, anchors=(), *, actor="actor:companion", marker="world-consequence.2"):
    state = ledger.project()
    return LifeDevelopmentCapabilityManifest(
        version="life-development-capabilities.2", owner_actor_ref=actor,
        pinned_cursor=ProjectionCursor(
            world_revision=state.world_revision, deliberation_revision=state.deliberation_revision,
            ledger_sequence=state.ledger_sequence,
        ),
        anchor_refs=tuple(sorted(anchors)), outcome_contract=marker,
        max_future_days=1, max_window_minutes=60,
    )


def _build(ledger, store, manifest, *, actor="actor:companion"):
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        build_world_consequence_authoring_context,
    )

    return build_world_consequence_authoring_context(
        ledger=ledger, content_store=store, manifest=manifest, actor_ref=actor,
    )


def _validate(ledger, store, manifest, context):
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        validate_world_consequence_authoring_context,
    )

    validate_world_consequence_authoring_context(
        ledger=ledger, content_store=store, manifest=manifest, actor_ref="actor:companion",
        authority=context.authority, execution_materials=context.execution_materials,
    )


def _operator_transition(ledger, plan_id, event_type, identity):
    """An explicit operator transition of an already role-authored real Plan."""
    from companion_daemon.world_v2.event_identity import domain_idempotency_key
    from companion_daemon.world_v2.schemas import WorldEvent
    from test_life_projection import commit, evidence, mutation

    state = ledger.project()
    observation = "operator:" + identity

    def event(event_id, kind, payload):
        return WorldEvent.from_payload(
            schema_version="world-v2.1", event_id=event_id, world_id=ledger.world_id,
            event_type=kind, logical_time=state.logical_time, created_at=state.logical_time,
            actor="operator:authoring-context-fixture", source="authoring-context-fixture",
            trace_id="trace:" + identity, causation_id="cause:" + identity,
            correlation_id="correlation:authoring-context",
            idempotency_key=domain_idempotency_key(
                event_type=kind, world_id=ledger.world_id, payload=payload,
            ) or "identity:" + event_id,
            payload=payload,
        )

    commit(ledger, [event("event:" + observation, "OperatorObservationRecorded", {
        "observation_id": observation, "observation_hash": "b" * 64,
    })])
    plan = next(item for item in state.plans if item.plan_id == plan_id)
    source = event(identity, event_type, {
        **mutation(identity, expected_revision=plan.entity_revision, evidence_refs=[
            evidence(observation, "operator_observation", "current_fact"),
        ]),
        "plan_id": plan_id, "transitioned_at": state.logical_time.isoformat(),
        "reason_ref": observation,
    })
    commit(ledger, [source])
    return source.event_id


@pytest.mark.asyncio
async def test_original_manifest_anchor_offers_exact_readable_execution(tmp_path):
    ledger, store, _, source_ref = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        build_world_consequence_authoring_context,
        validate_world_consequence_authoring_context,
    )

    manifest = _manifest(ledger, (source_ref,))
    context = build_world_consequence_authoring_context(
        ledger=ledger, content_store=store, manifest=manifest, actor_ref="actor:companion",
    )
    assert len(context.execution_materials) == 1
    assert context.execution_materials[0].status == "available"
    assert context.authority.execution_bindings[0].source_event_ref == source_ref
    assert context.execution_materials[0].execution_binding == context.authority.execution_bindings[0]
    validate_world_consequence_authoring_context(
        ledger=ledger, content_store=store, manifest=manifest, actor_ref="actor:companion",
        authority=context.authority, execution_materials=context.execution_materials,
    )


@pytest.mark.asyncio
async def test_missing_anchor_does_not_scan_life_history_for_extra_permission(tmp_path):
    ledger, store, _, _ = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        build_world_consequence_authoring_context,
    )

    context = build_world_consequence_authoring_context(
        ledger=ledger, content_store=store, manifest=_manifest(ledger), actor_ref="actor:companion",
    )
    assert context.authority.execution_bindings == ()
    assert context.execution_materials == ()


@pytest.mark.asyncio
async def test_only_newest_four_available_anchors_are_offered_with_full_intention(tmp_path, monkeypatch):
    intention = "我想仔细读公告里的投稿要求。" * 50
    monkeypatch.setattr(current_activity_fixture, "CURRENT_ACTIVITY_INTENTION", intention)
    ledger, store, plan_id, start = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    sources = [start]
    for index in range(5):
        _operator_transition(ledger, plan_id, "ActivityPaused", f"event:pause:{index}")
        sources.append(_operator_transition(ledger, plan_id, "ActivityResumed", f"event:resume:{index}"))
    # Input ordering cannot choose a different subset. Non-execution and unknown
    # anchors do not consume one of the four available execution slots.
    manifest = _manifest(ledger, (*sources, "event:pause:4", "event:unknown"))
    context = _build(ledger, store, manifest)
    assert [item.execution_binding.source_event_ref for item in context.execution_materials] == list(
        reversed(sources[-4:])
    )
    assert len(context.authority.execution_bindings) == 4
    assert all(item.authorized_intention.text == intention for item in context.execution_materials)
    assert len(intention) > 480
    assert current_activity_fixture.UNSETTLED_OUTCOME_TEXT not in context.model_dump_json()
    _validate(ledger, store, manifest, context)
    reordered = context.model_copy(update={
        "execution_materials": tuple(reversed(context.execution_materials)),
        "authority": context.authority.model_copy(update={
            "execution_bindings": tuple(reversed(context.authority.execution_bindings)),
        }),
    })
    with pytest.raises(ValueError, match="differs from original pinned"):
        _validate(ledger, store, manifest, reordered)
    # A recorded anchor whose original event cannot be read is not an offer;
    # keep looking for four available sources instead of counting the miss.
    class MissingLatestSource:
        world_id = ledger.world_id

        def project_at(self, cursor):
            return ledger.project_at(cursor)

        def lookup_event_commit(self, event_ref):
            return None if event_ref == sources[-1] else ledger.lookup_event_commit(event_ref)

    after_missing = _build(MissingLatestSource(), store, manifest)
    assert [item.source_event_ref for item in after_missing.authority.execution_bindings] == list(
        reversed(sources[-5:-1])
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["withhold", "foreign", "sidecar_missing"])
async def test_unreadable_or_foreign_activity_is_not_offered(tmp_path, mode):
    from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore

    ledger, store, _, source = await accepted_current_activity(
        sqlite_path=tmp_path / "life.sqlite", privacy_class="withhold" if mode == "withhold" else "personal",
    )
    actor = "actor:another" if mode == "foreign" else "actor:companion"
    if mode == "sidecar_missing":
        store = InMemoryImmutableLifeContentStore()
    context = _build(ledger, store, _manifest(ledger, (source,), actor=actor), actor=actor)
    assert context.authority.execution_bindings == ()
    assert context.execution_materials == ()


def test_generic_started_plan_and_receipt_remain_unavailable_without_readable_role_scope(tmp_path):
    from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore
    from test_world_consequence_authority import _activity, _transition, _receipt_case

    activity_path = tmp_path / "activity"
    activity_path.mkdir()
    ledger, _ = _activity(activity_path)
    start = _transition(ledger)
    context = _build(ledger, InMemoryImmutableLifeContentStore(), _manifest(ledger, (start.event_id,)))
    assert context.execution_materials == ()
    receipt_path = tmp_path / "receipt"
    receipt_path.mkdir()
    ledger, _, case = _receipt_case(receipt_path)
    receipt = case["source_events"][0]
    context = _build(ledger, InMemoryImmutableLifeContentStore(), _manifest(ledger, (receipt.event_id,)))
    assert context.authority.execution_bindings == ()
    assert context.execution_materials == ()


@pytest.mark.parametrize("observed_state", ["provider_accepted", "unknown"])
def test_ack_or_unknown_receipt_is_filtered_without_an_execution_offer(tmp_path, observed_state):
    from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore
    from test_experience_authority import event as receipt_event
    from test_life_projection import commit
    from test_world_consequence_authority import _receipt_case

    ledger, _, _ = _receipt_case(tmp_path)
    prior = ledger.project().execution_receipts[0]
    receipt = prior.model_copy(update={
        "receipt_id": "receipt:uncertain", "result_id": "result:uncertain",
        "observed_state": observed_state,
        "receipt_kind": "ack" if observed_state == "provider_accepted" else "terminal",
        "is_terminal": observed_state != "provider_accepted",
    })
    source = receipt_event("receipt:uncertain-recorded", "ExecutionReceiptRecorded", {
        "receipt": receipt.model_dump(mode="json"),
    })
    commit(ledger, [source])
    context = _build(ledger, InMemoryImmutableLifeContentStore(), _manifest(ledger, (source.event_id,)))
    assert context.authority.execution_bindings == ()
    assert context.execution_materials == ()


@pytest.mark.asyncio
async def test_public_manifest_compiler_can_offer_the_visible_started_source(tmp_path):
    from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
    from companion_daemon.world_v2.life_development_capability import ProjectionLifeCapabilityManifestCompiler
    from companion_daemon.world_v2.local_chronology import LocalChronology
    from test_current_activity_context import current_context
    from test_life_development_production import _open_life_seed

    ledger, store, _, source = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    capsule, _, _ = current_context(ledger, store)
    state = ledger.project()
    wake_ref = next(item.event_id for item in state.committed_world_event_refs if item.event_type == "ClockAdvanced")
    catalog = ReviewedLifeSeedCatalog.from_yaml(
        path=_open_life_seed(tmp_path / "seed.yaml"), chronology=LocalChronology("Asia/Shanghai"),
    )
    manifest = ProjectionLifeCapabilityManifestCompiler(
        owner_actor_ref="actor:companion", catalog=catalog, content_store=store,
    ).compile(projection=state, wake=ledger.lookup_event_commit(wake_ref)[0], capsule=capsule)
    assert source in capsule.current_situation.source_refs
    assert source in manifest.anchor_refs
    # The caller explicitly opts in; an old manifest remains the old protocol.
    manifest = manifest.model_copy(update={"outcome_contract": "world-consequence.2"})
    context = _build(ledger, store, manifest)
    assert [item.source_event_ref for item in context.authority.execution_bindings] == [source]


@pytest.mark.asyncio
async def test_original_pin_survives_cold_restart_and_cannot_gain_later_execution(tmp_path):
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from test_life_development_runtime import _seed_clock

    path = tmp_path / "life.sqlite"
    ledger, store, plan_id, start = await accepted_current_activity(sqlite_path=path)
    manifest = _manifest(ledger, (start, "event:later-resume"))
    original = _build(ledger, store, manifest)
    now = ledger.project().logical_time
    _seed_clock(ledger, event_id="event:later-clock", logical_time_from=now,
                logical_time=now + timedelta(minutes=2))
    _operator_transition(ledger, plan_id, "ActivityPaused", "event:later-pause")
    _operator_transition(ledger, plan_id, "ActivityResumed", "event:later-resume")
    world_id = ledger.world_id
    ledger.close()
    store.close()
    reopened = SQLiteWorldLedger(path=path, world_id=world_id)
    reopened_store = SQLiteImmutableLifeContentStore(path=path, world_id=world_id)
    assert _build(reopened, reopened_store, manifest) == original
    assert [item.source_event_ref for item in original.authority.execution_bindings] == [start]
    _validate(reopened, reopened_store, manifest, original)
    newer = _build(reopened, reopened_store, _manifest(reopened, manifest.anchor_refs))
    assert len(newer.execution_materials) == 2
    with pytest.raises(ValueError, match="differs from original pinned"):
        _validate(reopened, reopened_store, manifest, newer)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["old_contract", "owner_mismatch"])
async def test_old_manifest_or_different_owner_cannot_enable_new_authority(tmp_path, mode):
    ledger, store, _, source = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    manifest = _manifest(ledger, (source,), marker=None if mode == "old_contract" else "world-consequence.2")
    with pytest.raises(ValueError, match="explicit .2 manifest|differs from manifest owner"):
        _build(ledger, store, manifest, actor="actor:another" if mode == "owner_mismatch" else "actor:companion")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["text", "model_hash", "omitted", "unavailable"])
async def test_reverse_validation_requires_exact_full_available_material(tmp_path, mode):
    ledger, store, _, source = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    manifest = _manifest(ledger, (source,))
    original = _build(ledger, store, manifest)
    material = original.execution_materials[0]
    if mode in {"text", "model_hash"}:
        field = "text" if mode == "text" else "model_result_payload_hash"
        value = material.authorized_intention.text[:3] if mode == "text" else "f" * 64
        material = material.model_copy(update={
            "authorized_intention": material.authorized_intention.model_copy(update={field: value}),
        })
    if mode == "unavailable":
        material = material.model_copy(update={
            "status": "unavailable", "authorized_intention": None,
            "unavailable_reason": "activity_intention_unavailable",
        })
    changed = original.model_copy(update={"execution_materials": () if mode == "omitted" else (material,)})
    with pytest.raises(ValueError):
        _validate(ledger, store, manifest, changed)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["hash", "commit_after_pin", "wrong_projection"])
async def test_source_lookup_and_projection_cannot_substitute_later_or_different_data(tmp_path, mode):
    ledger, store, _, source = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    manifest = _manifest(ledger, (source,))

    class WrongLookup:
        world_id = ledger.world_id

        def project_at(self, cursor):
            state = ledger.project_at(cursor)
            return state.model_copy(update={"ledger_sequence": state.ledger_sequence + 1}) if mode == "wrong_projection" else state

        def lookup_event_commit(self, event_ref):
            found = ledger.lookup_event_commit(event_ref)
            if event_ref != source:
                return found
            event, commit = found
            if mode == "hash":
                event = event.model_copy(update={"payload_hash": "f" * 64})
            if mode == "commit_after_pin":
                commit = commit.model_copy(update={"ledger_sequence": manifest.pinned_cursor.ledger_sequence + 1})
            return event, commit

    with pytest.raises(ValueError, match="exact pinned event|differs from manifest pin"):
        _build(WrongLookup(), store, manifest)
