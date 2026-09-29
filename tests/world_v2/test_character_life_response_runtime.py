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
from test_life_projection import commit
from test_world_life_intent_runtime import ACTOR, INTENT, _cursor
from test_world_stimulus_life_response import _ResponseHTTP, _settled
from test_world_stimulus_life_intent import WORLD as WORLD_ID, _build, _model


@pytest.fixture(autouse=True)
def isolated_usage(monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")


async def _seed(path):
    provider = _ResponseHTTP()
    model = _model(provider)
    app = _build(path, model)
    try:
        await _settled(app)
    finally:
        await app.aclose()
        await model.aclose()
    return SQLiteWorldLedger(path=path, world_id=WORLD_ID), None


def _source(ledger):
    return next(
        x.event_id
        for x in ledger.project().committed_world_event_refs
        if x.event_type == "WorldOccurrenceSettled"
    )


def _advance_clock(ledger, seconds, label):
    from datetime import timedelta
    from companion_daemon.world_v2.schemas import WorldEvent

    now = ledger.project().logical_time
    at = now + timedelta(seconds=seconds)
    commit(
        ledger,
        [
            WorldEvent.from_payload(
                schema_version="world-v2.1",
                world_id=ledger.world_id,
                event_id="clock:" + label,
                event_type="ClockAdvanced",
                logical_time=at,
                created_at=at,
                actor="system:fixture",
                source="fixture",
                trace_id="trace:" + label,
                causation_id="cause:" + label,
                correlation_id="response-fixture",
                idempotency_key="clock:" + label,
                payload={"logical_time_from": now.isoformat(), "logical_time_to": at.isoformat()},
            )
        ],
    )


def _audit_response(
    ledger,
    *,
    text,
    identity="one",
    actor=ACTOR,
    source_ref=None,
    purpose="world_stimulus_appraisal",
    lineage_actor=None,
    evidence_hash=None,
    source_refs=None,
    selections=None,
    include_plan=False,
    omit_responses=False,
    registry_version="world-v2-proposals.5",
):
    from companion_daemon.world_v2.character_life_response_runtime import character_life_response_id

    projection = ledger.project()
    world_id = ledger.world_id
    source_ref = source_ref or _source(ledger)
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
    if omit_responses:
        changes = ()
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
        schema_registry_version=registry_version,
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
@pytest.mark.asyncio
async def test_same_role_response_and_explicit_null_are_source_bound_and_cold_replay_once(
    tmp_path, text
):
    from companion_daemon.world_v2.character_life_response_contract import (
        CharacterLifeResponseRecordedPayload,
    )
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )

    path = tmp_path / "response.sqlite"
    ledger, issuer = await _seed(path)
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
    assert payload.origin.source_event_ref == _source(ledger)
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
        ({"source_ref": "event:trigger:clock:activate-rain"}, "settlement_authority_invalid"),
        ({"source_refs": ("event:trigger:clock:activate-rain",)}, "inner_turn_authority_invalid"),
        ({"lineage_actor": "actor:other"}, "inner_turn_authority_invalid"),
        ({"evidence_hash": "0" * 64}, "source_binding_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_response_requires_the_exact_source_actor_and_role_audit(tmp_path, options, error):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )

    ledger, _ = await _seed(tmp_path / "binding.sqlite")
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


@pytest.mark.asyncio
async def test_duplicate_merged_source_is_rejected_without_partial_effect(tmp_path):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )

    ledger, _ = await _seed(tmp_path / "duplicate.sqlite")
    proposal, cursor = _audit_response(
        ledger, text=None, selections=((_source(ledger), None), (_source(ledger), "另外一段。"))
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
@pytest.mark.asyncio
async def test_joint_registry_preserves_one_original_response_and_plan_choice(tmp_path, plan_first):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    ledger, _ = await _seed(tmp_path / "joint.sqlite")
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
@pytest.mark.asyncio
async def test_direct_event_append_cannot_rewrite_the_author_or_source(tmp_path, field):
    from companion_daemon.world_v2.character_life_response_runtime import (
        derive_character_life_responses,
        EVENT_PREFIX,
        RESPONSE_PREFIX,
        SOURCE as RESPONSE_SOURCE,
    )

    ledger, _ = await _seed(tmp_path / "forged.sqlite")
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
    from companion_daemon.world_v2.schemas import WorldEvent
    from companion_daemon.world_v2.event_identity import domain_idempotency_key

    forged = WorldEvent.from_payload(
        schema_version="world-v2.1",
        world_id=ledger.world_id,
        event_id=EVENT_PREFIX + payload.response_id.removeprefix(RESPONSE_PREFIX),
        event_type="CharacterLifeResponseRecorded",
        logical_time=ledger.project().logical_time,
        created_at=ledger.project().logical_time,
        actor=actor,
        source=source,
        trace_id="trace:forged",
        causation_id=payload.origin.proposal_event_ref,
        correlation_id="response-fixture",
        payload=value,
        idempotency_key=domain_idempotency_key(
            event_type="CharacterLifeResponseRecorded",
            world_id=ledger.world_id,
            payload=value,
        ),
    )
    before = ledger.project()
    with pytest.raises(ValueError):
        commit(ledger, [forged])
    assert ledger.project() == before
    ledger.close()


@pytest.mark.asyncio
async def test_cas_interruption_recovers_the_durable_null_without_new_author(tmp_path, monkeypatch):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )
    from companion_daemon.world_v2.errors import ConcurrencyConflict

    path = tmp_path / "interrupted.sqlite"
    ledger, issuer = await _seed(path)
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
@pytest.mark.asyncio
async def test_legacy_proposal_cannot_acquire_a_response_by_relabeling(tmp_path, version):
    ledger, _ = await _seed(tmp_path / "legacy.sqlite")
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
            {"actor_ref": ACTOR, "source_event_ref": "event:missing"}
        )


@pytest.mark.asyncio
async def test_merged_real_settlements_resume_after_only_one_response_committed(
    tmp_path, monkeypatch
):
    """Both source events come from the installed public HTTP outcome chain."""
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )
    from companion_daemon.world_v2.errors import ConcurrencyConflict
    from test_world_stimulus_life_intent import WORLD

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    # A failed role response cannot consume either source or invent null. It
    # leaves both publicly accepted sources for the explicit coalesced audit.
    provider = _ResponseHTTP(fault="provider_failure")
    model = _model(provider)
    path = tmp_path / "two-sources.sqlite"
    app = _build(path, model)
    try:
        first = await _settled(app)
        second = await _settled(app, name="second")
        sources = (first, second)
    finally:
        await app.aclose()
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


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["missing_current", "extra_legacy", "only_legacy"])
async def test_reducer_requires_exact_new_source_set_without_promoting_legacy(tmp_path, selection):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )

    path = tmp_path / "source-set.sqlite"
    provider = _ResponseHTTP()
    model = _model(provider)
    app = _build(path, model)
    try:
        first = await _settled(app)
        second = await _settled(app, name="second")
        legacy = await _settled(app, name="legacy", current=False)
        assert provider.stimulus_requests == []
    finally:
        await app.aclose()
        await model.aclose()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    try:
        selected = [(first.event_id, None), (second.event_id, None)]
        if selection == "missing_current":
            selected.pop()
        elif selection == "extra_legacy":
            selected.append((legacy.event_id, None))
        else:
            selected = [(legacy.event_id, None)]
        proposal, cursor = _audit_response(
            ledger,
            text=None,
            source_ref=first.event_id,
            selections=tuple(selected),
            source_refs=tuple(sorted([first.event_id, second.event_id, legacy.event_id])),
        )
        before = ledger.project()
        with pytest.raises(ValueError, match="response_source_coverage_invalid"):
            CharacterLifeResponseRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
                world_id=WORLD_ID,
                audit_cursor=cursor,
                proposal_id=proposal.proposal_id,
            )
        assert ledger.project() == before
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_later_settlement_does_not_expand_original_response_obligation(tmp_path):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )

    path = tmp_path / "late-source.sqlite"
    ledger, _ = await _seed(path)
    proposal, cursor = _audit_response(ledger, text=None)
    original_source = _source(ledger)
    selected_at = ledger.project().logical_time
    ledger.close()
    model = _model(_ResponseHTTP(fault="provider_failure"))
    app = _build(path, model)
    try:
        later = await _settled(app, name="late")
    finally:
        await app.aclose()
        await model.aclose()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    try:
        original_models = ledger.project().model_result_audits
        receipts = CharacterLifeResponseRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
            world_id=WORLD_ID,
            audit_cursor=cursor,
            proposal_id=proposal.proposal_id,
        )
        assert len(receipts) == 1
        result = ledger.lookup_event_commit(receipts[0].event_ids[0])[0].payload()
        assert result["origin"]["source_event_ref"] == original_source != later.event_id
        assert result["origin"]["selected_at"] == selected_at.isoformat().replace("+00:00", "Z")
        assert ledger.project().model_result_audits == original_models
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_delayed_event_at_pin_does_not_replace_the_world_selection_clock(tmp_path):
    from datetime import timedelta

    from companion_daemon.world_v2.character_life_response_runtime import CharacterLifeResponseRuntime
    from companion_daemon.world_v2.schemas import BudgetAccount, WorldEvent

    path = tmp_path / "delayed-selection-clock.sqlite"
    ledger, _ = await _seed(path)
    _advance_clock(ledger, 60, "selection-clock")
    selected_at = ledger.project().logical_time
    stale_at = selected_at - timedelta(hours=1)
    # Transport/accounting can append an earlier observed timestamp without
    # rewinding the world's ClockAdvanced state.
    commit(ledger, [WorldEvent.from_payload(
        schema_version="world-v2.1", world_id=WORLD_ID,
        event_id="budget:delayed-selection", event_type="BudgetAccountConfigured",
        logical_time=stale_at, created_at=selected_at, actor="system:fixture",
        source="fixture", trace_id="trace:delayed-selection",
        causation_id="cause:delayed-selection", correlation_id="delayed-selection",
        idempotency_key="budget:delayed-selection",
        payload={"account": BudgetAccount(
            account_id="account:delayed-selection", category="audit",
            window_id="day:delayed-selection", limit=100,
        ).model_dump(mode="json")},
    )])
    proposal, cursor = _audit_response(ledger, text="雨停了，我松了一口气。")
    _advance_clock(ledger, 60, "later-than-selection")
    runtime = CharacterLifeResponseRuntime(ledger=ledger, owner_actor_ref=ACTOR)
    receipts = runtime.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id)
    event = ledger.lookup_event_commit(receipts[0].event_ids[0])[0]
    assert event.payload()["origin"]["selected_at"] == selected_at.isoformat().replace("+00:00", "Z")
    models = ledger.project().model_result_audits
    ledger.close()
    restarted = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    try:
        recovered = CharacterLifeResponseRuntime(ledger=restarted, owner_actor_ref=ACTOR)
        assert recovered.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id) == receipts
        assert restarted.project().model_result_audits == models
        assert restarted.lookup_event_commit(event.event_id)[0] == event
    finally:
        restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["world-v2-proposals.4", "world-v2-proposals.5"])
async def test_plan_consumer_cannot_bypass_required_response_in_same_audit(tmp_path, version):
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    ledger, _ = await _seed(tmp_path / "missing-response-plan.sqlite")
    try:
        proposal, cursor = _audit_response(
            ledger,
            text=None,
            include_plan=True,
            omit_responses=True,
            registry_version=version,
        )
        before = ledger.project()
        with pytest.raises(ValueError):
            WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
                world_id=WORLD_ID,
                audit_cursor=cursor,
                proposal_id=proposal.proposal_id,
            )
        assert ledger.project() == before
    finally:
        ledger.close()
