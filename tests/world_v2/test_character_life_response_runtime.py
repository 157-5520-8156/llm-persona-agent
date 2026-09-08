"""Actor-authored life responses through immutable SQLite proposal authority."""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.character_interior.run_result import CausalOpportunityIdentity
from companion_daemon.world_v2.proposal_audit_schemas import (
    ModelResultRecordedPayload,
    ProposalRecordedV2Payload,
    RecordedCharacterInteriorTurnLineage,
    RecordedModelResultAudit,
    RecordedModelRoute,
    canonical_json,
    model_audit_json,
    sha256,
)
from companion_daemon.world_v2.proposal_envelope import (
    CanonicalTypedPayload,
    DecisionProposal,
    ProposalEvidenceRef,
    TypedChange,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_projection import WORLD_ID, commit, event
from test_world_life_intent_runtime import ACTOR, SOURCE, INTENT, _advance_clock, _cursor, _seed


def _audit_response(
    ledger,
    *,
    text,
    identity="one",
    actor=ACTOR,
    source_ref=SOURCE,
    purpose="world_stimulus_appraisal",
    lineage_actor=None,
    evidence_hash=None,
    source_refs=None,
    selections=None,
    include_plan=False,
):
    from companion_daemon.world_v2.character_life_response_runtime import character_life_response_id

    projection = ledger.project()
    world_id = ledger.world_id
    selections = selections if selections is not None else ((source_ref, text),)
    sources = {x.event_id: x for x in projection.committed_world_event_refs}
    changes = tuple(
        TypedChange(
            change_id="change:world-response:" + identity + ":" + str(index),
            kind="world_life_response",
            target_id=character_life_response_id(
                world_id=world_id,
                actor_ref=actor,
                source_event_ref=ref,
            ),
            transition="record",
            expected_entity_revision=0,
            evidence_refs=(ref,),
            policy_refs=("policy:character-life-response.1",),
            payload=CanonicalTypedPayload.from_value(
                payload_schema="world_life_response.v1",
                value={"actor_ref": actor, "source_event_ref": ref, "response_text": selected_text},
            ),
        )
        for index, (ref, selected_text) in enumerate(selections)
    )
    if include_plan:
        from companion_daemon.world_v2.world_life_intent_runtime import world_life_plan_id

        changes += (
            TypedChange(
                change_id="change:world-response:plan:" + identity,
                kind="world_life_intent",
                target_id=world_life_plan_id(
                    world_id=world_id,
                    actor_ref=actor,
                    source_event_ref=source_ref,
                ),
                transition="plan",
                expected_entity_revision=0,
                evidence_refs=(source_ref,),
                policy_refs=("policy:world-life-intent.1",),
                payload=CanonicalTypedPayload.from_value(
                    payload_schema="world_life_intent.v1",
                    value={
                        **INTENT,
                        "actor_ref": actor,
                        "source_event_ref": source_ref,
                    },
                ),
            ),
        )
    proposal = DecisionProposal(
        proposal_id="proposal:world-response:" + identity,
        schema_registry_version="world-v2-proposals.5",
        trigger_ref=source_ref,
        evaluated_world_revision=projection.world_revision,
        evidence_refs=(
            ProposalEvidenceRef(
                ref_id=source_ref,
                evidence_kind="settled_world_event",
                source_world_revision=sources[source_ref].world_revision,
                immutable_hash="sha256:" + (evidence_hash or sources[source_ref].payload_hash),
            ),
        )
        + tuple(
            ProposalEvidenceRef(
                ref_id=ref,
                evidence_kind="settled_world_event",
                source_world_revision=sources[ref].world_revision,
                immutable_hash="sha256:" + sources[ref].payload_hash,
            )
            for ref in sorted({ref for ref, _ in selections} - {source_ref})
        ),
        proposed_changes=changes,
        confidence=7000,
        brief_rationale="我自己怎样看待这件事。",
        behavior_tendency="自行理解",
        stance="自行决定",
        display_strategy="withhold",
        timing_choice="silent",
    )
    opportunity = CausalOpportunityIdentity.from_source_refs(
        world_id=world_id,
        actor_ref=lineage_actor or actor,
        purpose=purpose,
        source_refs=source_refs or tuple(sorted({ref for ref, _ in selections})),
        epoch=source_ref,
    )
    model_call = "model-call:world-response:" + identity
    snapshot_hash = sha256("snapshot:" + identity)
    response_hash = sha256(canonical_json({"response_text": text}))
    request_hash = sha256("request:" + identity)
    audit = RecordedModelResultAudit(
        model_call_id=model_call,
        model_result_ref="model-result:"
        + sha256(
            canonical_json(
                {
                    "model_call_id": model_call,
                    "response_hash": response_hash,
                }
            )
        ),
        attempt_id="inner-turn:world-response:" + identity,
        route=RecordedModelRoute(
            tier="flash",
            reason_code="character_interior_world_stimulus",
            router_version="character-interior-world-stimulus-appraisal-result.3",
        ),
        model_id="offline-character",
        model_version="fixture.1",
        request_hash=request_hash,
        response_hash=response_hash,
        character_interior_lineage=RecordedCharacterInteriorTurnLineage(
            inner_turn_id="inner-turn:world-response:" + identity,
            purpose=purpose,
            opportunity_ref=opportunity.opportunity_ref,
            causal_world_id=world_id,
            causal_actor_ref=lineage_actor or actor,
            causal_source_refs=opportunity.source_refs,
            causal_epoch=opportunity.epoch,
            causal_contract_version=opportunity.contract_version,
            snapshot_id="inner-life-snapshot:sha256:" + snapshot_hash,
            snapshot_hash=snapshot_hash,
            capability_ref="capability:world-response:" + identity,
            author_model_id="offline-character",
            author_model_version="fixture.1",
            author_model_call_id=model_call,
            author_request_hash="sha256:" + request_hash,
            author_response_hash="sha256:" + response_hash,
            author_attempt_ordinal=0,
            private_self_lineage_hash="sha256:" + "7" * 64,
            decision_hash="sha256:" + "8" * 64,
        ),
        status="proposal_validated",
    )
    audit_json = model_audit_json(audit)
    shared = dict(
        model_result_ref=audit.model_result_ref,
        deliberation_result_id="deliberation:"
        + sha256(
            canonical_json(
                {
                    "capsule_id": snapshot_hash,
                    "proposal_hash": proposal.proposal_hash,
                    "attempt_audits": [json.loads(audit_json)],
                }
            )
        ),
        proposal_hash=proposal.proposal_hash,
        model_call_id=model_call,
        attempt_id=audit.attempt_id,
        capsule_id=snapshot_hash,
        trigger_ref=source_ref,
        evaluated_world_revision=projection.world_revision,
    )
    model_payload = ModelResultRecordedPayload(
        **shared,
        audit_contract="model-result-audit.7",
        attempt_index=0,
        attempt_count=1,
        audit_json=audit_json,
        audit_hash=sha256(audit_json),
    )
    proposal_payload = ProposalRecordedV2Payload(
        **shared,
        proposal_id=proposal.proposal_id,
        proposal_kind="decision",
        proposal_json=canonical_json(proposal.model_dump(mode="json")),
    )
    from companion_daemon.world_v2.event_identity import domain_idempotency_key
    from companion_daemon.world_v2.schemas import WorldEvent

    events = []
    for event_type, value in (
        ("ModelResultRecorded", model_payload.model_dump(mode="json")),
        ("ProposalRecorded", proposal_payload.model_dump(mode="json")),
    ):
        events.append(
            WorldEvent.from_payload(
                schema_version="world-v2.1",
                world_id=world_id,
                event_id=event_type + ":world-response:" + identity,
                event_type=event_type,
                logical_time=projection.logical_time,
                created_at=projection.logical_time,
                actor="system:offline-test",
                source="offline-test",
                trace_id="trace:response",
                causation_id=source_ref,
                correlation_id="response-fixture",
                idempotency_key=domain_idempotency_key(
                    event_type=event_type, world_id=world_id, payload=value
                ),
                payload=value,
            )
        )
    commit(ledger, events)
    return proposal, _cursor(ledger)


@pytest.mark.parametrize("text", [None, "这件事让我有一点意外，我想先想想。"])
def test_same_role_response_and_explicit_null_are_source_bound_and_cold_replay_once(tmp_path, text):
    from companion_daemon.world_v2.character_life_response_contract import (
        CharacterLifeResponseRecordedPayload,
    )
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )

    path = tmp_path / "response.sqlite"
    ledger, issuer = _seed(path)
    proposal, cursor = _audit_response(ledger, text=text)
    before = ledger.project()
    chosen_at = ledger.project().logical_time
    runtime = CharacterLifeResponseRuntime(ledger=ledger, owner_actor_ref=ACTOR)
    receipts = runtime.accept(
        world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id
    )
    sources = [
        x
        for x in ledger.project().committed_world_event_refs
        if x.event_type == "CharacterLifeResponseRecorded"
    ]
    assert len(sources) == len(receipts) == 1
    original = ledger.lookup_event_commit(sources[0].event_id)[0]
    payload = CharacterLifeResponseRecordedPayload.model_validate_json(original.payload_json)
    assert payload.response_text == text and payload.actor_ref == ACTOR
    assert payload.origin.source_event_ref == SOURCE
    assert payload.origin.selected_at == chosen_at
    assert (
        ledger.project().plans == before.plans
        and ledger.project().experiences == before.experiences
    )
    ledger.close()

    restarted = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    recovered = CharacterLifeResponseRuntime(ledger=restarted, owner_actor_ref=ACTOR)
    assert (
        recovered.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id)
        == receipts
    )
    assert restarted.lookup_event_commit(original.event_id)[0] == original
    assert (
        len(
            [
                x
                for x in restarted.project().committed_world_event_refs
                if x.event_type == "CharacterLifeResponseRecorded"
            ]
        )
        == 1
    )
    restarted.close()


@pytest.mark.parametrize(
    "options,error",
    [
        ({"actor": "actor:other"}, "actor_mismatch"),
        ({"purpose": "inbound_turn"}, "inner_turn_authority_invalid"),
        ({"source_ref": "clock-life"}, "settlement_authority_invalid"),
        ({"source_refs": ("clock-life",)}, "inner_turn_authority_invalid"),
        ({"lineage_actor": "actor:other"}, "inner_turn_authority_invalid"),
        ({"evidence_hash": "0" * 64}, "source_binding_invalid"),
    ],
)
def test_response_requires_the_exact_source_actor_and_role_audit(tmp_path, options, error):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )

    ledger, _ = _seed(tmp_path / "binding.sqlite")
    proposal, cursor = _audit_response(ledger, text=None, **options)
    before = ledger.project()
    with pytest.raises(ValueError, match=error):
        CharacterLifeResponseRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
            world_id=WORLD_ID,
            audit_cursor=cursor,
            proposal_id=proposal.proposal_id,
        )
    assert ledger.project() == before
    ledger.close()


def test_duplicate_merged_source_is_rejected_without_partial_effect(tmp_path):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )

    ledger, _ = _seed(tmp_path / "duplicate.sqlite")
    proposal, cursor = _audit_response(
        ledger, text=None, selections=((SOURCE, None), (SOURCE, "另外一段。"))
    )
    before = ledger.project()
    with pytest.raises(ValueError, match="duplicate_source"):
        CharacterLifeResponseRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
            world_id=WORLD_ID,
            audit_cursor=cursor,
            proposal_id=proposal.proposal_id,
        )
    assert ledger.project() == before
    ledger.close()


@pytest.mark.parametrize("plan_first", [False, True])
def test_joint_registry_preserves_one_original_response_and_plan_choice(tmp_path, plan_first):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    ledger, _ = _seed(tmp_path / "joint.sqlite")
    proposal, cursor = _audit_response(ledger, text=None, include_plan=True)
    before = ledger.project()
    responders = [
        CharacterLifeResponseRuntime(ledger=ledger, owner_actor_ref=ACTOR),
        WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR),
    ]
    if plan_first:
        responders.reverse()
    for runtime in responders:
        runtime.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id)
    projection = ledger.project()
    plans = [x for x in projection.plans if x.plan_id.startswith("plan:world-life-intent:")]
    assert len(plans) == 1 and plans[0].scheduled_window.opens_at == before.logical_time
    assert (
        len(
            [
                x
                for x in projection.committed_world_event_refs
                if x.event_type == "CharacterLifeResponseRecorded"
            ]
        )
        == 1
    )
    assert projection.experiences == before.experiences
    for runtime in responders:
        runtime.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id)
    assert ledger.project() == projection
    ledger.close()


@pytest.mark.parametrize(
    "field",
    [
        "response_text",
        "source_payload_hash",
        "source_world_revision",
        "model_call_id",
        "actor",
        "source",
    ],
)
def test_direct_event_append_cannot_rewrite_the_author_or_source(tmp_path, field):
    from companion_daemon.world_v2.character_life_response_runtime import (
        derive_character_life_responses,
        EVENT_PREFIX,
        RESPONSE_PREFIX,
        SOURCE as RESPONSE_SOURCE,
    )

    ledger, _ = _seed(tmp_path / "forged.sqlite")
    proposal, _ = _audit_response(ledger, text=None)
    payload = derive_character_life_responses(
        state=ledger.project(),
        world_id=WORLD_ID,
        proposal_id=proposal.proposal_id,
        owner_actor_ref=ACTOR,
    )[0]
    value = payload.model_dump(mode="json")
    actor, source = ACTOR, RESPONSE_SOURCE
    if field == "response_text":
        value[field] = "这不是角色原先写下的回应。"
    elif field == "source_payload_hash":
        value["origin"][field] = "0" * 64
    elif field == "source_world_revision":
        value["origin"][field] += 1
    elif field == "model_call_id":
        value["origin"][field] = "model-call:other"
    elif field == "actor":
        actor = "actor:other"
    else:
        source = "world-author"
    forged = event(
        EVENT_PREFIX + payload.response_id.removeprefix(RESPONSE_PREFIX),
        "CharacterLifeResponseRecorded",
        value,
    ).model_copy(
        update={"actor": actor, "source": source, "causation_id": payload.origin.proposal_event_ref}
    )
    before = ledger.project()
    with pytest.raises(ValueError):
        commit(ledger, [forged])
    assert ledger.project() == before
    ledger.close()


def test_cas_interruption_recovers_the_durable_null_without_new_author(tmp_path, monkeypatch):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )
    from companion_daemon.world_v2.errors import ConcurrencyConflict

    path = tmp_path / "interrupted.sqlite"
    ledger, issuer = _seed(path)
    proposal, cursor = _audit_response(ledger, text=None)
    before = ledger.project()
    with monkeypatch.context() as local:

        def conflict(*args, **kwargs):
            raise ConcurrencyConflict("offline concurrent append")

        local.setattr(ledger, "commit_at_cursor", conflict)
        with pytest.raises(ConcurrencyConflict):
            CharacterLifeResponseRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
                world_id=WORLD_ID,
                audit_cursor=cursor,
                proposal_id=proposal.proposal_id,
            )
    assert ledger.project() == before
    ledger.close()
    restarted = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    _advance_clock(restarted, 30, "late-response")
    receipt = CharacterLifeResponseRuntime(ledger=restarted, owner_actor_ref=ACTOR).accept(
        world_id=WORLD_ID,
        audit_cursor=cursor,
        proposal_id=proposal.proposal_id,
    )[0]
    payload = restarted.lookup_event_commit(receipt.event_ids[0])[0].payload()
    assert payload["response_text"] is None
    assert payload["origin"]["selected_at"] == before.logical_time.isoformat().replace(
        "+00:00", "Z"
    )
    assert restarted.project().model_result_audits == before.model_result_audits
    restarted.close()


@pytest.mark.parametrize(
    "version", ["world-v2-proposals.1", "world-v2-proposals.3", "world-v2-proposals.4"]
)
def test_legacy_proposal_cannot_acquire_a_response_by_relabeling(tmp_path, version):
    ledger, _ = _seed(tmp_path / "legacy.sqlite")
    proposal, _ = _audit_response(ledger, text=None)
    raw = proposal.model_dump(mode="json")
    raw["schema_registry_version"] = version
    with pytest.raises(ValueError):
        DecisionProposal.model_validate_json(json.dumps(raw))
    ledger.close()


def test_missing_response_is_not_an_authored_null():
    from companion_daemon.world_v2.character_life_response_contract import (
        CharacterLifeResponsePayload,
    )

    with pytest.raises(ValueError):
        CharacterLifeResponsePayload.model_validate(
            {"actor_ref": ACTOR, "source_event_ref": SOURCE}
        )


@pytest.mark.asyncio
async def test_merged_real_settlements_resume_after_only_one_response_committed(
    tmp_path, monkeypatch
):
    """Both source events come from the installed public HTTP outcome chain."""
    from datetime import timedelta
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )
    from companion_daemon.world_v2.errors import ConcurrencyConflict
    from companion_daemon.world_v2.occurrence_content_coordinator import (
        OccurrenceContentCommitRequest,
        OutcomeCandidateContent,
    )
    from companion_daemon.world_v2.schemas import (
        DueWindow,
        EvidenceRef,
        OutcomeObservation,
        WorldOccurrenceProjection,
    )
    from test_world_stimulus_life_intent import (
        CANDIDATE,
        CONSIDERED,
        WORLD,
        _RoleHTTP,
        _accepted_settlement,
        _build,
        _clock,
        _model,
    )

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _RoleHTTP(intent="null")
    model = _model(provider)
    path = tmp_path / "two-sources.sqlite"
    app = _build(path, model)
    try:
        first = await _accepted_settlement(app)
        provider.source_ref = first.event_id
        await app.drain_background_once()
        second_at = CONSIDERED + timedelta(minutes=1)
        await app.commit_occurrence(
            OccurrenceContentCommitRequest(
                world_id=WORLD,
                occurrence=WorldOccurrenceProjection(
                    occurrence_id="occurrence:world-response:second",
                    entity_revision=1,
                    trigger_ref="trigger:world-response:second",
                    participant_refs=(ACTOR,),
                    location_ref=None,
                    time_window=DueWindow(
                        opens_at=CONSIDERED,
                        closes_at=second_at + timedelta(minutes=10),
                    ),
                    candidate_outcome_refs=(CANDIDATE,),
                    visibility="private",
                    status="committed",
                ),
                candidate_contents=(
                    OutcomeCandidateContent(
                        candidate_result_ref=CANDIDATE,
                        result_id="result:world-response:second",
                        result_payload_ref="payload:world-response:second",
                        result_payload_hash="sha256:" + "b" * 64,
                        privacy_class="private",
                        content_ref="content:world-response:second",
                        text="短雨之后天空明亮了一点。",
                    ),
                ),
                change_id="change:world-response:second",
                transition_id="transition:world-response:second",
                evidence_refs=(
                    EvidenceRef(
                        ref_id="clock:" + CONSIDERED.isoformat(),
                        evidence_type="clock_observation",
                        claim_purpose="current_fact",
                    ),
                ),
                logical_time=CONSIDERED,
                created_at=CONSIDERED,
                actor="system:offline-world",
                source="test",
                trace_id="trace:second",
                causation_id="cause:second",
                correlation_id="world-response",
            )
        )
        await app.advance(_clock("second-activate", CONSIDERED, second_at))
        await app.record_outcome_observation(
            OutcomeObservation(
                schema_version="world-v2.1",
                observation_id="observation:world-response:second",
                world_id=WORLD,
                logical_time=second_at,
                created_at=second_at,
                trace_id="trace:second-observed",
                causation_id="sensor:second",
                correlation_id="world-response",
                occurrence_id="occurrence:world-response:second",
                source_kind="committed_world_event",
                source_refs=("event:trigger:clock:second-activate",),
                observed_payload_ref="sensor-payload:second",
                observed_payload_hash="c" * 64,
                observed_at=second_at,
                confidence_bp=9500,
            )
        )
        await app.drain_background_once()
        sources = [
            row.event
            for row in app.export_replay_evidence().events
            if row.event.event_type == "WorldOccurrenceSettled"
        ]
        assert len(sources) == 2
    finally:
        app.close()
        await model.aclose()

    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    expected = {sources[0].event_id: None, sources[1].event_id: "我还想再感受一下。"}
    proposal, cursor = _audit_response(
        ledger,
        text=None,
        source_ref=sources[0].event_id,
        selections=tuple(expected.items()),
    )
    before_models = ledger.project().model_result_audits
    original_commit = ledger.commit_at_cursor
    calls = 0
    with monkeypatch.context() as local:

        def stop_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ConcurrencyConflict("offline second-response crash")
            return original_commit(*args, **kwargs)

        local.setattr(ledger, "commit_at_cursor", stop_second)
        with pytest.raises(ConcurrencyConflict):
            CharacterLifeResponseRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
                world_id=WORLD,
                audit_cursor=cursor,
                proposal_id=proposal.proposal_id,
            )
    partial = [
        x
        for x in ledger.project().committed_world_event_refs
        if x.event_type == "CharacterLifeResponseRecorded"
    ]
    assert len(partial) == 1
    original_event = ledger.lookup_event_commit(partial[0].event_id)[0]
    ledger.close()
    reopened = SQLiteWorldLedger(path=path, world_id=WORLD)
    runtime = CharacterLifeResponseRuntime(ledger=reopened, owner_actor_ref=ACTOR)
    receipts = runtime.accept(world_id=WORLD, audit_cursor=cursor, proposal_id=proposal.proposal_id)
    accepted = [
        x
        for x in reopened.project().committed_world_event_refs
        if x.event_type == "CharacterLifeResponseRecorded"
    ]
    assert len(accepted) == len(receipts) == 2
    assert {
        reopened.lookup_event_commit(x.event_id)[0].payload()["origin"][
            "source_event_ref"
        ]: reopened.lookup_event_commit(x.event_id)[0].payload()["response_text"]
        for x in accepted
    } == expected
    assert reopened.lookup_event_commit(original_event.event_id)[0] == original_event
    assert reopened.project().model_result_audits == before_models
    assert (
        runtime.accept(world_id=WORLD, audit_cursor=cursor, proposal_id=proposal.proposal_id)
        == receipts
    )
    reopened.close()
