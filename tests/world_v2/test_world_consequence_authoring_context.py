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
async def test_public_manifest_requires_a_selected_attempt_to_offer_its_execution(tmp_path):
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
    assert context.authority.execution_bindings == context.execution_materials == ()
    legacy = _build(ledger, store, manifest.model_copy(update={"execution_intention_sources_version": "3"}))
    assert [item.source_event_ref for item in legacy.authority.execution_bindings] == [source]
    from companion_daemon.world_v2.active_attempt_consequence import read_active_attempt_consequence
    selected = read_active_attempt_consequence(
        ledger=ledger, content_store=store, pinned_state=state, actor_ref="actor:companion",
        execution_event_ref=source, clock_event_ref=wake_ref, intention_reader_version="4",
    )
    assert selected is not None
    context = _build(ledger, store, manifest.model_copy(update={"active_attempt_consequence": selected}))
    assert context.authority.execution_bindings == (selected.execution_binding,)


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


async def _registered_identity_fixture(tmp_path):
    from companion_daemon.world_v2.event_identity import domain_idempotency_key
    from companion_daemon.world_v2.life_content_store import (
        StoredLifeContent,
        life_content_payload_hash,
    )
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentNpcCapability
    from companion_daemon.world_v2.schemas import EvidenceRef, NpcProjection, WorldEvent
    from test_life_projection import commit, mutation

    ledger, store, _, _ = await accepted_current_activity(
        sqlite_path=tmp_path / "npc-source.sqlite"
    )
    state = ledger.project()
    clock = max(
        (r for r in state.committed_world_event_refs if r.event_type == "ClockAdvanced"),
        key=lambda r: r.world_revision,
    )
    text = "小林，是角色在文学社认识的朋友。"
    stored = StoredLifeContent(
        content_ref="identity:registered-lin",
        content_kind="provisional_npc_introduction",
        text=text,
        content_payload_hash=life_content_payload_hash(text),
    )
    store.put_if_absent(stored)
    payload = {
        **mutation(
            "registered-lin",
            expected_revision=0,
            evidence_refs=[
                EvidenceRef(
                    ref_id=clock.event_id,
                    evidence_type="committed_world_event",
                    claim_purpose="current_fact",
                    immutable_hash=clock.payload_hash,
                    source_world_revision=clock.world_revision,
                ).model_dump(mode="json")
            ],
        ),
        "npc": NpcProjection(
            npc_id="registered-lin",
            entity_revision=1,
            stable_identity_ref=stored.content_ref,
            privacy_class="personal",
        ).model_dump(mode="json"),
    }
    event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:npc:registered-lin",
        world_id=ledger.world_id,
        event_type="NpcRegistered",
        logical_time=state.logical_time,
        created_at=state.logical_time,
        actor="operator:identity-fixture",
        source="identity-fixture",
        trace_id="trace:identity",
        causation_id=clock.event_id,
        correlation_id="identity",
        idempotency_key=domain_idempotency_key(
            event_type="NpcRegistered", world_id=ledger.world_id, payload=payload
        ),
        payload=payload,
    )
    commit(ledger, [event])
    cap = LifeDevelopmentNpcCapability(
        npc_ref="npc:registered-lin",
        lifecycle_state="active",
        identity_content_ref=stored.content_ref,
        identity_summary=text,
        identity_payload_hash=stored.content_payload_hash,
        authority_refs=(event.event_id,),
        current_location_ref="location:NOT_CURRENT_PRESENCE",
        protagonist_closeness_bp=9000,
    )
    manifest = _manifest(ledger, (clock.event_id,)).model_copy(
        update={
            "execution_intention_sources_version": "4",
            "entity_refs": (cap.npc_ref,),
            "grounding_refs": (event.event_id,),
            "npc_capabilities": (cap,),
        }
    )
    return ledger, store, manifest, event


@pytest.mark.asyncio
async def test_registered_identity_is_an_exact_source_not_current_npc_state(tmp_path):
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        read_registered_npc_identity_sources,
    )

    ledger, store, manifest, event = await _registered_identity_fixture(tmp_path)
    try:
        before = ledger.project()
        source = read_registered_npc_identity_sources(
            ledger=ledger, content_store=store, manifest=manifest
        )
        (row,) = source["items"]
        assert row["identity_summary"] == manifest.npc_capabilities[0].identity_summary
        assert row["authority_refs"] == [event.event_id]
        assert row["source_payload_hash"] == event.payload_hash
        assert row["privacy_class"] == "personal"
        assert "current_location_ref" not in row and "protagonist_closeness_bp" not in row
        assert source["manifest_hash"] == manifest.manifest_hash and source[
            "pinned_cursor"
        ] == manifest.pinned_cursor.model_dump(mode="json")
        for version in (None, "2", "3"):
            assert (
                read_registered_npc_identity_sources(
                    ledger=ledger,
                    content_store=store,
                    manifest=manifest.model_copy(
                        update={"execution_intention_sources_version": version}
                    ),
                )
                is None
            )
        assert ledger.project() == before and ledger.rebuild() == before
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["hash", "text", "identity_ref", "unoffered", "privacy"])
async def test_registered_identity_cannot_substitute_source_or_permission(tmp_path, mode):
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentNpcPrivacyFloor
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        read_registered_npc_identity_sources,
    )

    ledger, store, manifest, _ = await _registered_identity_fixture(tmp_path)
    try:
        cap = manifest.npc_capabilities[0]
        changes = (
            {"npc_capabilities": (cap.model_copy(update={"identity_payload_hash": "f" * 64}),)}
            if mode == "hash"
            else {
                "npc_capabilities": (cap.model_copy(update={"identity_summary": "编造的童年旧友"}),)
            }
            if mode == "text"
            else {
                "npc_capabilities": (
                    cap.model_copy(update={"identity_content_ref": "identity:another"}),
                )
            }
            if mode == "identity_ref"
            else {"entity_refs": ()}
            if mode == "unoffered"
            else {
                "npc_privacy_floors": (
                    LifeDevelopmentNpcPrivacyFloor(npc_ref=cap.npc_ref, privacy_class="public"),
                )
            }
        )
        before = ledger.project()
        with pytest.raises(ValueError, match="NPC identity"):
            read_registered_npc_identity_sources(
                ledger=ledger, content_store=store, manifest=manifest.model_copy(update=changes)
            )
        assert ledger.project() == before
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_registered_identity_missing_evidence_never_falls_back_to_manifest_summary(tmp_path):
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        read_registered_npc_identity_sources,
    )

    ledger, store, manifest, _ = await _registered_identity_fixture(tmp_path)

    class MissingBytes:
        def read_exact(self, **kwargs):
            return None

    try:
        assert (
            read_registered_npc_identity_sources(
                ledger=ledger, content_store=MissingBytes(), manifest=manifest
            )
            is None
        )
        assert (
            read_registered_npc_identity_sources(
                ledger=ledger,
                content_store=store,
                manifest=manifest.model_copy(update={"grounding_refs": ()}),
            )
            is None
        )
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_both_reviewers_share_exact_identity_and_field_privacy(tmp_path):
    import json
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentPossibilityDraft
    from companion_daemon.world_v2.life_development_source_closure import (
        life_development_source_closure_messages,
        life_development_novel_origin_messages,
    )
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        read_registered_npc_identity_sources,
    )
    from test_world_consequence_producer import _draft

    ledger, store, manifest, event = await _registered_identity_fixture(tmp_path)
    try:
        material = read_registered_npc_identity_sources(
            ledger=ledger, content_store=store, manifest=manifest
        )
        value = _draft(event)
        value.pop("location_ref", None)
        value.pop("location_capability_ref", None)
        for outcome in value["outcomes"]:
            outcome["visual_evidence"] = None
        draft = LifeDevelopmentPossibilityDraft.model_validate_json(json.dumps(value))
        context = {"world_id": ledger.world_id, "actor_ref": manifest.owner_actor_ref, "slices": {}}
        general = json.loads(
            life_development_source_closure_messages(
                context=context,
                manifest=manifest,
                draft=draft,
                cited_events=(),
                execution_authority=_build(ledger, store, manifest).model_dump(mode="json"),
                registered_npc_identities=material,
            )[1]["content"]
        )
        focused = json.loads(
            life_development_novel_origin_messages(
                context=context,
                manifest=manifest,
                draft=draft,
                registered_npc_identities=material,
                execution_authority=_build(ledger, store, manifest).model_dump(mode="json"),
            )[1]["content"]
        )
        a = general["pinned_source_evidence"]["registered_npc_identities"]
        b = focused["pinned_authority"]["registered_npc_identities"]
        assert a == b
        assert (
            a["publication_privacy"]["outcomes.0.world_consequence.environment_text"]
            == draft.outcomes[0].privacy_class
        )
        assert a["items"][0]["privacy_class"] == "personal"
        changed = {**material, "manifest_hash": "f" * 64}
        with pytest.raises(ValueError, match="original manifest pin"):
            life_development_novel_origin_messages(
                context=context,
                manifest=manifest,
                draft=draft,
                registered_npc_identities=changed,
                execution_authority=_build(ledger, store, manifest).model_dump(mode="json"),
            )
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["hash", "later_commit", "wrong_projection", "missing_registration"]
)
async def test_registered_identity_rejects_substituted_lookup_or_future_pin(tmp_path, mode):
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        read_registered_npc_identity_sources,
    )

    ledger, store, manifest, event = await _registered_identity_fixture(tmp_path)

    class ChangedReader:
        world_id = ledger.world_id

        def project_at(self, cursor):
            state = ledger.project_at(cursor)
            return (
                state.model_copy(update={"world_revision": state.world_revision + 1})
                if mode == "wrong_projection"
                else state
            )

        def lookup_event_commit(self, ref):
            found = ledger.lookup_event_commit(ref)
            if ref != event.event_id:
                return found
            if mode == "missing_registration":
                return None
            source, commit = found
            if mode == "hash":
                source = source.model_copy(update={"payload_hash": "f" * 64})
            if mode == "later_commit":
                commit = commit.model_copy(
                    update={"ledger_sequence": manifest.pinned_cursor.ledger_sequence + 1}
                )
            return source, commit

    try:
        before = ledger.project()
        if mode == "missing_registration":
            assert (
                read_registered_npc_identity_sources(
                    ledger=ChangedReader(), content_store=store, manifest=manifest
                )
                is None
            )
        else:
            with pytest.raises(ValueError, match="NPC identity"):
                read_registered_npc_identity_sources(
                    ledger=ChangedReader(), content_store=store, manifest=manifest
                )
        assert ledger.project() == before
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "uncited", "identity", "hash", "future"])
async def test_epoch_carried_registered_identity_uses_its_exact_current_world_root(tmp_path, mode):
    from companion_daemon.world_v2.epoch_continuity import compile_continuity_snapshot
    from companion_daemon.world_v2.epoch_genesis import write_epoch_ledger
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        read_registered_npc_identity_sources,
    )

    old, content, original, registration = await _registered_identity_fixture(tmp_path)
    epoch = None
    current_content = None
    try:
        old_state = old.project()
        snapshot = compile_continuity_snapshot(old_state, epoch_id="epoch:npc-source")
        path = tmp_path / "epoch-npc.sqlite"
        epoch = write_epoch_ledger(
            path=path, world_id=old.world_id + ":new", now=old_state.logical_time, snapshot=snapshot
        )
        current_content = SQLiteImmutableLifeContentStore(path=path, world_id=epoch.world_id)
        current_content.put_if_absent(
            content.read_exact(content_ref=original.npc_capabilities[0].identity_content_ref)
        )
        state = epoch.project()
        (root,) = (r for r in state.committed_world_event_refs if r.event_type == "WorldStarted")
        assert epoch.lookup_event_commit(registration.event_id) is None
        manifest = _manifest(epoch).model_copy(
            update={
                "execution_intention_sources_version": "4",
                "grounding_refs": (root.event_id,),
                "entity_refs": original.entity_refs,
                "npc_capabilities": original.npc_capabilities,
            }
        )
        if mode == "uncited":
            assert (
                read_registered_npc_identity_sources(
                    ledger=epoch,
                    content_store=current_content,
                    manifest=manifest.model_copy(update={"grounding_refs": ()}),
                )
                is None
            )
            return
        if mode == "identity":
            changed = manifest.model_copy(
                update={
                    "npc_capabilities": (
                        manifest.npc_capabilities[0].model_copy(
                            update={"identity_content_ref": "identity:wrong"}
                        ),
                    )
                }
            )
            with pytest.raises(ValueError, match="NPC identity"):
                read_registered_npc_identity_sources(
                    ledger=epoch, content_store=current_content, manifest=changed
                )
            return
        if mode in {"hash", "future"}:

            class WrongWitness:
                world_id = epoch.world_id

                def project_at(self, cursor):
                    return epoch.project_at(cursor)

                def lookup_event_commit(self, ref):
                    event, commit = epoch.lookup_event_commit(ref)
                    if mode == "hash":
                        event = event.model_copy(update={"payload_hash": "f" * 64})
                    else:
                        commit = commit.model_copy(
                            update={"world_revision": state.world_revision + 1}
                        )
                    return event, commit

            with pytest.raises(ValueError, match="NPC identity"):
                read_registered_npc_identity_sources(
                    ledger=WrongWitness(), content_store=current_content, manifest=manifest
                )
            return
        source = read_registered_npc_identity_sources(
            ledger=epoch, content_store=current_content, manifest=manifest
        )
        assert source and source["items"][0]["authority_refs"] == [root.event_id]
        assert (
            source["items"][0]["identity_summary"] == original.npc_capabilities[0].identity_summary
        )
        assert source["items"][0]["original_registration_ref"] == registration.event_id
        assert source["items"][0]["source_kind"] == "epoch_continuity_npc_identity"
        from types import SimpleNamespace
        from test_life_development_audit_context_recovery import _catalog
        from companion_daemon.world_v2.life_development_capability import (
            ProjectionLifeCapabilityManifestCompiler,
        )

        compiled = ProjectionLifeCapabilityManifestCompiler(
            owner_actor_ref=manifest.owner_actor_ref,
            catalog=_catalog(tmp_path),
            content_store=current_content,
        ).compile(
            projection=state,
            wake=epoch.lookup_event_commit(root.event_id)[0],
            capsule=SimpleNamespace(model_content_json="{}"),
        )
        assert registration.event_id not in compiled.grounding_refs
        assert compiled.npc_capabilities[0].authority_refs == (root.event_id,)
        assert root.event_id in compiled.grounding_refs
        assert read_registered_npc_identity_sources(
            ledger=epoch, content_store=current_content, manifest=compiled
        )
        assert epoch.project() == state and epoch.rebuild() == state
    finally:
        if current_content is not None:
            current_content.close()
        if epoch is not None:
            epoch.close()
        content.close()
        old.close()
