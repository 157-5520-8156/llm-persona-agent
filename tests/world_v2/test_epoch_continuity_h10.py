from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.affect_live import live_component_intensity_bp
from companion_daemon.world_v2.epoch_continuity import (
    apply_continuity_snapshot,
    compile_continuity_snapshot,
)
from companion_daemon.world_v2.epoch_genesis import archive_sqlite_file, write_epoch_ledger
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.fact_accepted_contracts import FactCommitIntentV2
from companion_daemon.world_v2.fact_correction_lifecycle import FactCorrectionLifecycle
from companion_daemon.world_v2.reducers import ReducerState, make_projection
from companion_daemon.world_v2.ledger_context_resolver import _typed_refs
from companion_daemon.world_v2.relationship_reducers import RELATIONSHIP_POLICY_DIGEST
from companion_daemon.world_v2.schemas import (
    EvidenceRef,
    FactAssertionBinding,
    FactOrigin,
    FactProjection,
    FactValues,
    Observation,
    PrivateImpressionOrigin,
    PrivateImpressionProjection,
    RelationshipStateOrigin,
    RelationshipStateProjection,
    ThreadOrigin,
    ThreadProjection,
    ThreadValues,
    WorldEvent,
    fact_conflict_key,
    fact_semantic_fingerprint,
    thread_semantic_fingerprint,
)
from test_affect_module import appraisal as make_appraisal
from test_affect_module import episode as make_affect
from test_memory_candidate_authority import candidate as make_memory
from test_memory_candidate_authority import hardened_experience_authority
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_affect_module import component as affect_component
from test_affect_module import episode as affect_episode


NOW = datetime(2026, 8, 13, 15, 0, tzinfo=UTC)
WORLD = "world:epoch-h10"
POLICY = ("policy:fact-v1",)


def _fact() -> FactProjection:
    binding = FactAssertionBinding(
        source_kind="operator_observation",
        source_ref="operator:epoch",
        asserted_subject_ref="subject:user",
        content_payload_hash="a" * 64,
    )
    anchors = (
        EvidenceRef(
            ref_id="operator:epoch",
            evidence_type="operator_observation",
            claim_purpose="current_fact",
            immutable_hash="a" * 64,
        ),
    )
    values = FactValues(
        subject_ref="subject:user",
        predicate_code="location.current",
        cardinality="single",
        conflict_key=fact_conflict_key(
            subject_ref="subject:user", predicate_code="location.current"
        ),
        value_ref="value:user-location:shanghai",
        value_hash="c" * 64,
        assertion_binding=binding,
        anchor_evidence_refs=anchors,
        source_evidence_refs=anchors,
        confidence_bp=9000,
        privacy_class="private",
        status="active",
    )
    origin = FactOrigin(
        change_id="change:transition:fact:home:1",
        transition_id="transition:fact:home:1",
        policy_refs=POLICY,
        accepted_event_ref="event:old-fact-commit",
    )
    return FactProjection(
        fact_id="fact:user-home",
        entity_revision=1,
        semantic_fingerprint=fact_semantic_fingerprint(
            subject_ref=values.subject_ref,
            predicate_code=values.predicate_code,
            cardinality=values.cardinality,
            conflict_key=values.conflict_key,
            value_hash=values.value_hash,
            assertion_binding=values.assertion_binding,
            anchor_evidence_refs=values.anchor_evidence_refs,
            policy_refs=origin.policy_refs,
        ),
        values=values,
        origin=origin,
        committed_at=NOW,
        updated_at=NOW,
    )


def _observation_event() -> WorldEvent:
    payload = {"observation_id": "observation:old-archive-message"}
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event-old-obs",
        world_id=WORLD,
        event_type="ObservationRecorded",
        logical_time=NOW,
        created_at=NOW,
        actor="system:test",
        source="test",
        trace_id="trace-old",
        causation_id="cause-old",
        correlation_id="correlation-old",
        idempotency_key="identity:event-old-obs",
        payload=payload,
    )


def test_snapshot_keeps_facts_and_drops_process_tables() -> None:
    fact = _fact()
    projection = SimpleNamespace(
        world_id=WORLD,
        world_revision=88,
        semantic_hash="b" * 64,
        logical_time=NOW,
        facts=(fact,),
        memory_candidates=(),
        relationship_states=(),
        affect_episodes=(),
        appraisals=(),
        threads=(),
        commitments=(),
        experiences=(),
        life_arcs=(),
        npcs=(),
        private_impressions=(),
        character_core=None,
        trigger_processes=("process:should-not-copy",),
        completed_trigger_ids=("trigger:done",),
        clock_transition_history=("clock:1",),
        model_result_audits=("audit:1",),
        observation_refs=("observation:old-archive-message",),
    )
    snapshot = compile_continuity_snapshot(projection, epoch_id="epoch:2")
    assert snapshot.facts
    assert snapshot.facts[0]["fact_id"] == "fact:user-home"
    dumped = snapshot.model_dump(mode="json")
    assert "trigger_processes" not in dumped
    assert "observation_refs" not in dumped
    assert dumped["facts"][0]["origin"]["accepted_event_ref"] == "event:old-fact-commit"


def test_genesis_ledger_hydrates_facts_without_old_observation_keys(tmp_path) -> None:
    source = tmp_path / "live.sqlite"
    archive = tmp_path / "archive.sqlite"
    target = tmp_path / "epoch2.sqlite"
    live = SQLiteWorldLedger(path=source, world_id=WORLD)
    live.commit(
        [_observation_event()],
        expected_world_revision=0,
        expected_deliberation_revision=0,
    )
    live.close()
    archive_sqlite_file(source=source, destination=archive)
    assert archive.exists()
    assert archive.stat().st_mode & 0o222 == 0

    fact = _fact()
    snapshot = compile_continuity_snapshot(
        SimpleNamespace(
            world_id=WORLD,
            world_revision=2,
            semantic_hash="d" * 64,
            logical_time=NOW,
            facts=(fact,),
            memory_candidates=(),
            relationship_states=(),
            affect_episodes=(),
            appraisals=(),
            threads=(),
            commitments=(),
            experiences=(),
            life_arcs=(),
            npcs=(),
            private_impressions=(),
            character_core=None,
        ),
        epoch_id="epoch:2",
    )
    new_ledger = write_epoch_ledger(path=target, world_id=WORLD, now=NOW, snapshot=snapshot)
    try:
        projection = new_ledger.project()
        assert any(
            item.event_type == "WorldStarted" for item in projection.committed_world_event_refs
        )
        assert projection.facts
        assert projection.facts[0].fact_id == "fact:user-home"
        assert projection.facts[0].origin.accepted_event_ref.startswith(
            "event:world-v2-epoch:epoch:2:WorldStarted:"
        )
        assert projection.facts[0].semantic_fingerprint == fact.semantic_fingerprint
        assert "observation:old-archive-message" not in projection.observation_refs
        assert projection.trigger_processes == ()
        assert projection.clock_transition_history == ()
    finally:
        new_ledger.close()
    assert archive.stat().st_mtime == archive.stat().st_mtime
    assert archive.stat().st_mode & 0o222 == 0


def test_reopen_skips_full_cold_replay_when_prefix_proof_is_complete(tmp_path) -> None:
    path = tmp_path / "fast-start.sqlite"
    first = SQLiteWorldLedger(path=path, world_id=WORLD)
    first.commit(
        [_observation_event()],
        expected_world_revision=0,
        expected_deliberation_revision=0,
    )
    first.close()
    reopened = SQLiteWorldLedger(path=path, world_id=WORLD)
    try:
        counters = reopened.performance_counters()
        assert counters.cold_history_replayed is False
        assert reopened.project().observation_refs == ("observation:old-archive-message",)
        assert reopened.rebuild() == reopened.project()
    finally:
        reopened.close()


def test_live_intensity_falls_with_time_without_rewriting_the_anchor() -> None:
    item = affect_component(intensity_bp=8_000)
    later = NOW + timedelta(hours=2)
    live = live_component_intensity_bp(item, baseline_bp=0, at=later)
    assert live < item.intensity_bp
    assert item.decay_anchor_intensity_bp == 8_000
    state = ReducerState(logical_time=later, affect_episodes=(affect_episode(),))
    # The helper episode uses 4000 intensity and a 60s delay / 1h half-life.
    projected = make_projection(
        world_id=WORLD,
        world_revision=0,
        deliberation_revision=0,
        ledger_sequence=0,
        state=state,
    )
    stored = state.affect_episodes[0].components[0].intensity_bp
    shown = projected.affect_episodes[0].components[0].intensity_bp
    assert shown < stored
    assert state.affect_episodes[0].components[0].decay_anchor_intensity_bp == stored


def test_committed_world_event_fact_rebinds_to_genesis_and_recomputes_fingerprint() -> None:
    operator = EvidenceRef(
        ref_id="operator:epoch",
        evidence_type="operator_observation",
        claim_purpose="current_fact",
        immutable_hash="a" * 64,
    )
    world = EvidenceRef(
        ref_id="event:old-world",
        evidence_type="committed_world_event",
        claim_purpose="current_fact",
        immutable_hash="a" * 64,
        source_world_revision=88,
    )
    binding = FactAssertionBinding(
        source_kind="operator_observation",
        source_ref="operator:epoch",
        asserted_subject_ref="subject:user",
        content_payload_hash="a" * 64,
    )
    values = FactValues(
        subject_ref="subject:user",
        predicate_code="location.current",
        cardinality="single",
        conflict_key=fact_conflict_key(
            subject_ref="subject:user", predicate_code="location.current"
        ),
        value_ref="value:user-location:shanghai",
        value_hash="c" * 64,
        assertion_binding=binding,
        anchor_evidence_refs=(world,),
        source_evidence_refs=(operator, world),
        confidence_bp=9000,
        privacy_class="private",
        status="active",
    )
    origin = FactOrigin(
        change_id="change:transition:fact:home:1",
        transition_id="transition:fact:home:1",
        policy_refs=POLICY,
        accepted_event_ref="event:old-fact-commit",
    )
    fact = FactProjection(
        fact_id="fact:user-home",
        entity_revision=1,
        semantic_fingerprint=fact_semantic_fingerprint(
            subject_ref=values.subject_ref,
            predicate_code=values.predicate_code,
            cardinality=values.cardinality,
            conflict_key=values.conflict_key,
            value_hash=values.value_hash,
            assertion_binding=values.assertion_binding,
            anchor_evidence_refs=values.anchor_evidence_refs,
            policy_refs=origin.policy_refs,
        ),
        values=values,
        origin=origin,
        committed_at=NOW,
        updated_at=NOW,
    )
    snapshot = compile_continuity_snapshot(
        SimpleNamespace(
            world_id=WORLD,
            world_revision=88,
            semantic_hash="e" * 64,
            logical_time=NOW,
            facts=(fact,),
            memory_candidates=(),
            relationship_states=(),
            affect_episodes=(),
            appraisals=(),
            threads=(),
            commitments=(),
            experiences=(),
            life_arcs=(),
            npcs=(),
            private_impressions=(),
            character_core=None,
        ),
        epoch_id="epoch:2",
    )
    hydrated = apply_continuity_snapshot(
        snapshot,
        genesis_event_id="event:world-v2-epoch:epoch:2:WorldStarted:abc",
        genesis_payload_hash="f" * 64,
        logical_time=NOW,
    )
    imported = hydrated["facts"][0]
    assert imported.origin.accepted_event_ref.endswith("WorldStarted:abc")
    assert imported.values.anchor_evidence_refs[0].ref_id.endswith("WorldStarted:abc")
    assert imported.semantic_fingerprint != fact.semantic_fingerprint


def test_observed_message_fact_rebinds_binding_and_preserves_archive_observation() -> None:
    archive_observation = "observation:qq:2759284998:qq-coalesced:archive-name"
    binding = FactAssertionBinding(
        source_kind="observed_message",
        source_ref=archive_observation,
        asserted_subject_ref="user:geoff",
        actor_ref="user:geoff",
        channel="qq",
        payload_ref="ingress:qq:2759284998:qq-coalesced:archive-name",
        content_payload_hash="a" * 64,
    )
    observation_evidence = EvidenceRef(
        ref_id=archive_observation,
        evidence_type="observed_message",
        claim_purpose="current_fact",
        immutable_hash="a" * 64,
    )
    commit_evidence = EvidenceRef(
        ref_id="event:old-fact-commit",
        evidence_type="committed_world_event",
        claim_purpose="current_fact",
        immutable_hash="a" * 64,
        source_world_revision=88,
    )
    anchors = (commit_evidence,)
    values = FactValues(
        subject_ref="user:geoff",
        predicate_code="profile.display_name",
        cardinality="single",
        conflict_key=fact_conflict_key(
            subject_ref="user:geoff", predicate_code="profile.display_name"
        ),
        value_ref="value:observation:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        value_hash="c" * 64,
        assertion_binding=binding,
        anchor_evidence_refs=anchors,
        source_evidence_refs=(observation_evidence, commit_evidence),
        confidence_bp=9000,
        privacy_class="private",
        status="active",
    )
    origin = FactOrigin(
        change_id="change:transition:fact:name:1",
        transition_id="transition:fact:name:1",
        policy_refs=POLICY,
        accepted_event_ref="event:old-fact-commit",
    )
    fact = FactProjection(
        fact_id="fact:user-name",
        entity_revision=1,
        semantic_fingerprint=fact_semantic_fingerprint(
            subject_ref=values.subject_ref,
            predicate_code=values.predicate_code,
            cardinality=values.cardinality,
            conflict_key=values.conflict_key,
            value_hash=values.value_hash,
            assertion_binding=values.assertion_binding,
            anchor_evidence_refs=values.anchor_evidence_refs,
            policy_refs=origin.policy_refs,
        ),
        values=values,
        origin=origin,
        committed_at=NOW,
        updated_at=NOW,
    )
    hydrated = apply_continuity_snapshot(
        compile_continuity_snapshot(
            SimpleNamespace(
                world_id=WORLD,
                world_revision=88,
                semantic_hash="e" * 64,
                logical_time=NOW,
                facts=(fact,),
                memory_candidates=(),
                relationship_states=(),
                affect_episodes=(),
                appraisals=(),
                threads=(),
                commitments=(),
                experiences=(),
                life_arcs=(),
                npcs=(),
                private_impressions=(),
                character_core=None,
            ),
            epoch_id="epoch:2",
        ),
        genesis_event_id="event:world-v2-epoch:epoch:2:WorldStarted:abc",
        genesis_payload_hash="f" * 64,
        logical_time=NOW,
    )
    imported = hydrated["facts"][0]
    assert imported.values.assertion_binding.source_kind == "operator_observation"
    assert imported.values.assertion_binding.source_ref.endswith("WorldStarted:abc")
    assert imported.values.assertion_binding.actor_ref is None
    observation_refs = [
        item
        for item in imported.values.source_evidence_refs
        if item.ref_id == archive_observation and item.evidence_type == "observed_message"
    ]
    assert len(observation_refs) == 1
    assert len(imported.values.source_evidence_refs) == len(
        {(item.evidence_type, item.ref_id) for item in imported.values.source_evidence_refs}
    )


def test_genesis_display_name_can_be_corrected_from_a_live_observation(tmp_path) -> None:
    archive_observation = "observation:qq:2759284998:qq-coalesced:archive-name"
    binding = FactAssertionBinding(
        source_kind="observed_message",
        source_ref=archive_observation,
        asserted_subject_ref="user:geoff",
        actor_ref="user:geoff",
        channel="qq",
        payload_ref="ingress:qq:2759284998:qq-coalesced:archive-name",
        content_payload_hash="a" * 64,
    )
    observation_evidence = EvidenceRef(
        ref_id=archive_observation,
        evidence_type="observed_message",
        claim_purpose="current_fact",
        immutable_hash="a" * 64,
    )
    commit_evidence = EvidenceRef(
        ref_id="event:old-fact-commit",
        evidence_type="committed_world_event",
        claim_purpose="current_fact",
        immutable_hash="a" * 64,
        source_world_revision=88,
    )
    values = FactValues(
        subject_ref="user:geoff",
        predicate_code="profile.display_name",
        cardinality="single",
        conflict_key=fact_conflict_key(
            subject_ref="user:geoff", predicate_code="profile.display_name"
        ),
        value_ref="value:observation:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        value_hash="c" * 64,
        assertion_binding=binding,
        anchor_evidence_refs=(commit_evidence,),
        source_evidence_refs=(observation_evidence, commit_evidence),
        confidence_bp=9000,
        privacy_class="private",
        status="active",
    )
    origin = FactOrigin(
        change_id="change:transition:fact:name:1",
        transition_id="transition:fact:name:1",
        policy_refs=POLICY,
        accepted_event_ref="event:old-fact-commit",
    )
    genesis_fact = FactProjection(
        fact_id="fact:user-name",
        entity_revision=1,
        semantic_fingerprint=fact_semantic_fingerprint(
            subject_ref=values.subject_ref,
            predicate_code=values.predicate_code,
            cardinality=values.cardinality,
            conflict_key=values.conflict_key,
            value_hash=values.value_hash,
            assertion_binding=values.assertion_binding,
            anchor_evidence_refs=values.anchor_evidence_refs,
            policy_refs=origin.policy_refs,
        ),
        values=values,
        origin=origin,
        committed_at=NOW,
        updated_at=NOW,
    )
    snapshot = compile_continuity_snapshot(
        SimpleNamespace(
            world_id=WORLD,
            world_revision=88,
            semantic_hash="e" * 64,
            logical_time=NOW,
            facts=(genesis_fact,),
            memory_candidates=(),
            relationship_states=(),
            affect_episodes=(),
            appraisals=(),
            threads=(),
            commitments=(),
            experiences=(),
            life_arcs=(),
            npcs=(),
            private_impressions=(),
            character_core=None,
        ),
        epoch_id="epoch:2",
    )
    ledger = write_epoch_ledger(
        path=tmp_path / "epoch-name.sqlite",
        world_id=WORLD,
        now=NOW,
        snapshot=snapshot,
    )
    try:
        text = "丁奥轩✅"
        observation = Observation(
            schema_version="world-v2.1",
            observation_id="observation:live-name",
            world_id=WORLD,
            logical_time=NOW,
            created_at=NOW,
            trace_id="trace:live-name",
            causation_id="cause:live-name",
            correlation_id="correlation:live-name",
            source="test:live-name",
            source_event_id="source:live-name",
            actor="user:geoff",
            channel="qq",
            payload_ref="payload:live-name",
            payload_hash=hashlib.sha256(text.encode()).hexdigest(),
            text=text,
            received_at=NOW,
        )
        payload = observation.model_dump(mode="json")
        observation_event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id="event:live-name",
            world_id=WORLD,
            event_type="ObservationRecorded",
            logical_time=NOW,
            created_at=NOW,
            actor=observation.actor,
            source=observation.source,
            trace_id=observation.trace_id,
            causation_id=observation.causation_id,
            correlation_id=observation.correlation_id,
            idempotency_key=domain_idempotency_key(
                event_type="ObservationRecorded", world_id=WORLD, payload=payload
            )
            or "identity:event-live-name",
            payload=payload,
        )
        head = ledger.project()
        ledger.commit(
            (observation_event,),
            expected_world_revision=head.world_revision,
            expected_deliberation_revision=head.deliberation_revision,
        )
        stored = ledger.lookup_event_commit(observation_event.event_id)
        assert stored is not None
        before = next(
            item
            for item in ledger.project().facts
            if item.values.predicate_code == "profile.display_name"
        )
        digest = hashlib.sha256("丁奥轩".encode()).hexdigest()
        after = FactCorrectionLifecycle(
            ledger=ledger,
            actor="operator:test",
            source="world-v2:operator-fact-correction",
        ).correct(
            before=before,
            intent=FactCommitIntentV2.model_validate(
                {
                    "subject_ref": "user:geoff",
                    "predicate_code": "profile.display_name",
                    "value_ref": f"value:observation:{digest}",
                    "value_hash": f"sha256:{digest}",
                    "assertion_source_ref": observation.observation_id,
                    "evidence_uses": (
                        {
                            "evidence_ref": observation.observation_id,
                            "purpose": "current_fact",
                            "anchor": True,
                        },
                    ),
                    "confidence_bp": 9500,
                    "privacy_class": "private",
                }
            ),
            observation=observation,
            observation_event=stored[0],
            observation_world_revision=stored[1].world_revision,
            logical_time=ledger.project().logical_time,
            created_at=NOW,
        )
        assert after.values.value_hash == digest
        assert after.values.assertion_binding.source_ref == observation.observation_id
    finally:
        ledger.close()


def test_replay_accepts_world_started_fact_head_when_values_hash_drifted() -> None:
    from companion_daemon.world_v2.reducers import (
        _REPLAY_GENESIS_FACT_HEAD,
        _canonical_model_hash,
        _validate_evidence_authority,
    )
    from companion_daemon.world_v2.schemas import (
        CommittedWorldEventRef,
        FactTransitionProjection,
    )

    genesis_id = "event:world-started"
    first = _fact().model_copy(
        update={
            "origin": _fact().origin.model_copy(update={"accepted_event_ref": genesis_id}),
        }
    )
    name_values = first.values.model_copy(
        update={
            "predicate_code": "profile.display_name",
            "conflict_key": fact_conflict_key(
                subject_ref="subject:user", predicate_code="profile.display_name"
            ),
            "value_ref": "value:observation:name",
            "value_hash": "d" * 64,
        }
    )
    name_origin = FactOrigin(
        change_id="change:transition:fact:name:1",
        transition_id="transition:fact:name:1",
        policy_refs=POLICY,
        accepted_event_ref=genesis_id,
    )
    name_fact = first.model_copy(
        update={
            "fact_id": "fact:user-name",
            "values": name_values,
            "origin": name_origin,
            "semantic_fingerprint": fact_semantic_fingerprint(
                subject_ref=name_values.subject_ref,
                predicate_code=name_values.predicate_code,
                cardinality=name_values.cardinality,
                conflict_key=name_values.conflict_key,
                value_hash=name_values.value_hash,
                assertion_binding=name_values.assertion_binding,
                anchor_evidence_refs=name_values.anchor_evidence_refs,
                policy_refs=name_origin.policy_refs,
            ),
        }
    )

    def _transition(fact: FactProjection) -> FactTransitionProjection:
        return FactTransitionProjection(
            transition_id=fact.origin.transition_id,
            fact_id=fact.fact_id,
            entity_revision=fact.entity_revision,
            operation="commit",
            values_before=None,
            values_after=fact.values,
            semantic_fingerprint_after=fact.semantic_fingerprint,
            change_id=fact.origin.change_id,
            policy_refs=POLICY,
            accepted_event_ref=genesis_id,
            accepted_at=NOW,
        )

    state = ReducerState(
        committed_world_event_refs=(
            CommittedWorldEventRef(
                event_id=genesis_id,
                event_type="WorldStarted",
                world_revision=1,
                payload_hash="b" * 64,
                logical_time=NOW,
            ),
        ),
        facts=(first, name_fact),
        fact_transitions=(
            _transition(first),
            _transition(name_fact),
        ),
    )
    matching = EvidenceRef(
        ref_id=genesis_id,
        evidence_type="committed_fact",
        claim_purpose="current_fact",
        source_world_revision=1,
        immutable_hash=_canonical_model_hash(name_values),
    )
    _validate_evidence_authority(state, (matching,), require_all=True)
    drifted = matching.model_copy(update={"immutable_hash": "e" * 64})
    with pytest.raises(ValueError, match="committed-fact"):
        _validate_evidence_authority(state, (drifted,), require_all=True)
    token = _REPLAY_GENESIS_FACT_HEAD.set(True)
    try:
        _validate_evidence_authority(state, (drifted,), require_all=True)
    finally:
        _REPLAY_GENESIS_FACT_HEAD.reset(token)


def _thread_with_archive_event() -> ThreadProjection:
    archive = EvidenceRef(
        ref_id="event:appraisal-mutation:old-thread",
        evidence_type="committed_world_event",
        claim_purpose="conversation_continuity",
        source_world_revision=88,
        immutable_hash="a" * 64,
    )
    values = ThreadValues(
        kind="topic_open",
        subject_ref="subject:user-day",
        conversation_ref="conversation:1",
        anchor_evidence_refs=(archive,),
        source_evidence_refs=(archive,),
        importance_bp=6500,
        resolution_contract_ref="resolution-contract:topic-understood",
        privacy_class="private",
        status="open",
    )
    origin = ThreadOrigin(
        change_id="change:thread:archive",
        transition_id="transition:thread:archive",
        policy_refs=POLICY,
        accepted_event_ref="event:thread:archive",
    )
    return ThreadProjection(
        thread_id="thread:archive",
        entity_revision=1,
        semantic_fingerprint=thread_semantic_fingerprint(
            kind=values.kind,
            subject_ref=values.subject_ref,
            conversation_ref=values.conversation_ref,
            anchor_evidence_refs=values.anchor_evidence_refs,
            resolution_contract_ref=values.resolution_contract_ref,
            policy_refs=origin.policy_refs,
        ),
        values=values,
        origin=origin,
        opened_at=NOW,
        updated_at=NOW,
    )


def _impression_with_archive_event() -> PrivateImpressionProjection:
    return PrivateImpressionProjection(
        impression_id="impression:archive",
        entity_revision=1,
        subject_ref="subject:user",
        interpretation_refs=("appraisal:compiled:old:meaning:hurt",),
        source_refs=("event:appraisal-mutation:old-impression",),
        confidence_bp=6500,
        first_seen=NOW,
        last_supported=NOW,
        expiry_condition="until_appraisal_contradicted",
        status="active",
        origin=PrivateImpressionOrigin(
            change_id="change:impression:archive",
            transition_id="transition:impression:archive",
            policy_refs=("policy:private-impression.1",),
            accepted_event_ref="event:private-impression:accepted:old",
        ),
    )


def _relationship_with_archive_event() -> RelationshipStateProjection:
    return RelationshipStateProjection(
        relationship_id="relationship:user",
        subject_ref="user:geoff",
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        origin=RelationshipStateOrigin(
            change_id="change:relationship:archive",
            transition_id="transition:relationship:archive",
            policy_refs=("policy:relationship-v1",),
            accepted_event_ref="event:relationship-adjustment-mutation:old",
        ),
    )


def _continuity_projection(**updates) -> SimpleNamespace:
    fields = dict(
        world_id=WORLD,
        world_revision=88,
        semantic_hash="e" * 64,
        logical_time=NOW,
        facts=(),
        memory_candidates=(),
        relationship_states=(),
        affect_episodes=(),
        appraisals=(),
        threads=(),
        commitments=(),
        experiences=(),
        life_arcs=(),
        npcs=(),
        private_impressions=(),
        character_core=None,
    )
    fields.update(updates)
    return SimpleNamespace(**fields)


def test_genesis_rebinds_capsule_sources_and_survives_reopen(tmp_path) -> None:
    genesis_id = "event:world-v2-epoch:epoch:2:WorldStarted:abc"
    _, _, _, memory_source = hardened_experience_authority()
    snapshot = compile_continuity_snapshot(
        _continuity_projection(
            facts=(_fact(),),
            memory_candidates=(
                make_memory(
                    memory_source,
                    status="active",
                    reviewed_at=NOW,
                    opened_at=NOW,
                    updated_at=NOW,
                ),
            ),
            relationship_states=(_relationship_with_archive_event(),),
            affect_episodes=(make_affect(),),
            appraisals=(make_appraisal(),),
            threads=(_thread_with_archive_event(),),
            private_impressions=(_impression_with_archive_event(),),
        ),
        epoch_id="epoch:2",
    )
    hydrated = apply_continuity_snapshot(
        snapshot,
        genesis_event_id=genesis_id,
        genesis_payload_hash="f" * 64,
        logical_time=NOW,
    )
    for item in (
        *hydrated["affect_episodes"],
        *hydrated["appraisals"],
        *hydrated["threads"],
        *hydrated["memory_candidates"],
        *hydrated["private_impressions"],
        *hydrated["relationship_states"],
    ):
        refs = _typed_refs(item, observation_aliases={}) or ()
        assert refs
        assert set(refs) == {genesis_id}

    target = tmp_path / "epoch2.sqlite"
    ledger = write_epoch_ledger(path=target, world_id=WORLD, now=NOW, snapshot=snapshot)
    ledger.close()
    reopened = SQLiteWorldLedger(path=target, world_id=WORLD)
    try:
        projection = reopened.project()
        present = {item.event_id for item in projection.committed_world_event_refs}
        assert projection.world_revision == 1
        for group in (
            projection.affect_episodes,
            projection.appraisals,
            projection.threads,
            projection.memory_candidates,
            projection.private_impressions,
            projection.relationship_states,
        ):
            assert group
            for item in group:
                refs = _typed_refs(item, observation_aliases={}) or ()
                assert refs
                assert set(refs) <= present
    finally:
        reopened.close()
