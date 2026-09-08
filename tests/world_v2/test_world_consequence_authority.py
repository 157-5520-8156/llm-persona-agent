"""Exact, pre-author execution authority; no provider or prose classification."""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.proposal_audit_schemas import (
    RecordedModelDecisionContext,
    RecordedModelResultAudit,
    RecordedModelRoute,
    canonical_json,
    sha256,
)
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_projection import (
    LIFE_TIME,
    WORLD_ID,
    commit,
    event,
    evidence,
    mutation,
    register_operator_observations,
    seed_through_proposal,
)

ACTOR = "actor:companion"


def _activity(tmp_path):
    path = tmp_path / "world.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    seed_through_proposal(ledger)
    register_operator_observations(ledger, "operator:activity")
    return ledger, path


def _transition(ledger, event_type="ActivityStarted", revision=1):
    identity = "execution:" + event_type
    source = event(identity, event_type, {
        **mutation(identity, expected_revision=revision, evidence_refs=[
            evidence("operator:activity", "operator_observation", "current_fact"),
        ]),
        "plan_id": "plan-tea",
        "transitioned_at": LIFE_TIME.isoformat(),
        "reason_ref": "reason:" + identity,
    })
    commit(ledger, [source])
    return source


def _messages(authority):
    return [
        {"role": "system", "content": "Propose environmental consequences only."},
        {"role": "user", "content": canonical_json({
            "execution_authority": authority.model_dump(mode="json"),
            "pinned_context": {"environment": "窗外在下雨。"},
        })},
    ]


def _audit(authority, messages):
    return RecordedModelResultAudit(
        model_call_id="model-call:world-author:fixture",
        model_result_ref="model-result:" + sha256(canonical_json({
            "model_call_id": "model-call:world-author:fixture", "response_hash": "a" * 64,
        })),
        attempt_id="attempt:world-author:fixture",
        route=RecordedModelRoute(tier="flash", reason_code="life_development.world_author",
                                 router_version="life-development-router.2"),
        model_id="offline-fixture", model_version="fixture.1",
        request_hash=sha256(canonical_json(messages)), response_hash="a" * 64,
        decision_context=RecordedModelDecisionContext(
            decision_subject_hash="b" * 64,
            **authority.evaluated_cursor.model_dump(),
        ),
        status="candidate_returned", slot="primary", outcome="returned",
    )


def test_started_execution_is_bound_to_original_sqlite_pin_after_cold_restart(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        AuthorizedAttemptResult,
        WorldConsequenceV2,
        derive_world_consequence_authority,
        validate_world_consequence_authority,
    )

    ledger, path = _activity(tmp_path)
    source = _transition(ledger)
    pinned = ledger.project()
    authority = derive_world_consequence_authority(
        pinned_state=pinned, actor_ref=ACTOR, source_events=(source,),
    )
    binding = authority.execution_bindings[0]
    assert binding.plan_id == "plan-tea"
    assert binding.plan_entity_revision == 2
    assert binding.source_event_ref == source.event_id
    messages = _messages(authority)
    audit = _audit(authority, messages)
    consequence = WorldConsequenceV2(
        environment_text="雨水沿着窗玻璃往下淌。",
        authorized_attempt_result=AuthorizedAttemptResult(
            text="这一次泡出的茶汤颜色较浅。", execution_binding=binding,
        ),
    )
    _transition(ledger, "ActivityCompleted", 2)
    reopened = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    original = reopened.project_at(ProjectionCursor(**authority.evaluated_cursor.model_dump()))
    validate_world_consequence_authority(
        consequence=consequence, authority=authority, pinned_state=original,
        source_events=(reopened.lookup_event_commit(source.event_id)[0],),
        author_messages=messages, author_audit=audit,
    )
    with pytest.raises(ValueError, match="pinned_cursor"):
        validate_world_consequence_authority(
            consequence=consequence, authority=authority, pinned_state=reopened.project(),
            source_events=(source,), author_messages=messages, author_audit=audit,
        )


def test_future_plan_is_not_execution_authority(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        derive_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    source = ledger.lookup_event_commit("activity-planned")[0]
    with pytest.raises(ValueError, match="source_type"):
        derive_world_consequence_authority(
            pinned_state=ledger.project(), actor_ref=ACTOR, source_events=(source,),
        )


def _validation_case(ledger, source_events=()):
    from companion_daemon.world_v2.world_consequence_contract import (
        AuthorizedAttemptResult,
        WorldConsequenceV2,
        derive_world_consequence_authority,
    )

    pinned = ledger.project()
    authority = derive_world_consequence_authority(
        pinned_state=pinned, actor_ref=ACTOR, source_events=source_events,
    )
    messages = _messages(authority)
    result = None
    if authority.execution_bindings:
        result = AuthorizedAttemptResult(
            text="仅说明这个已开始尝试的客观结果。",
            execution_binding=authority.execution_bindings[0],
        )
    return dict(
        consequence=WorldConsequenceV2(environment_text="窗外雨势渐小。",
                                       authorized_attempt_result=result),
        authority=authority, pinned_state=pinned, source_events=source_events,
        author_messages=messages, author_audit=_audit(authority, messages),
    )


def test_environment_only_requires_no_character_execution(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        validate_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    case = _validation_case(ledger)
    validate_world_consequence_authority(**case)
    assert case["authority"].execution_bindings == ()
    assert "authorized_attempt_result" not in case["consequence"].model_dump(mode="json")


def test_resumed_and_prior_started_attempts_preserve_their_own_revisions(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        derive_world_consequence_authority,
        validate_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    started = _transition(ledger)
    _transition(ledger, "ActivityPaused", 2)
    resumed = _transition(ledger, "ActivityResumed", 3)
    _transition(ledger, "ActivityCompleted", 4)
    case = _validation_case(ledger, (resumed,))
    validate_world_consequence_authority(**case)
    assert case["authority"].execution_bindings[0].plan_entity_revision == 4
    prior = derive_world_consequence_authority(
        pinned_state=ledger.project(), actor_ref=ACTOR, source_events=(started,),
    )
    assert prior.execution_bindings[0].plan_entity_revision == 2
    assert prior.execution_bindings[0].source_event_ref == started.event_id


def test_execution_after_original_author_pin_cannot_be_added_retroactively(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        derive_world_consequence_authority,
        validate_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    before = _validation_case(ledger)
    source = _transition(ledger)
    with pytest.raises(ValueError, match="source_not_in_pin"):
        derive_world_consequence_authority(
            pinned_state=before["pinned_state"], actor_ref=ACTOR, source_events=(source,),
        )
    after = _validation_case(ledger, (source,))
    before["consequence"] = after["consequence"]
    with pytest.raises(ValueError, match="attempt_not_authorized"):
        validate_world_consequence_authority(**before)


@pytest.mark.parametrize("source_ref", ["clock-life", "activity-planned"])
def test_clock_and_plan_refs_do_not_authorize_execution(tmp_path, source_ref):
    from companion_daemon.world_v2.world_consequence_contract import (
        derive_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    source = ledger.lookup_event_commit(source_ref)[0]
    with pytest.raises(ValueError, match="source_type"):
        derive_world_consequence_authority(
            pinned_state=ledger.project(), actor_ref=ACTOR, source_events=(source,),
        )


@pytest.mark.parametrize("field,value", [
    ("source_payload_hash", "f" * 64),
    ("source_world_revision", 1),
    ("source_event_ref", "selection-token:one"),
    ("plan_id", "plan:another"),
    ("activity_id", "activity:another"),
    ("plan_entity_revision", 99),
    ("actor_ref", "actor:someone-else"),
])
def test_result_cannot_change_exact_execution_binding(tmp_path, field, value):
    from companion_daemon.world_v2.world_consequence_contract import (
        validate_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    case = _validation_case(ledger, (_transition(ledger),))
    result = case["consequence"].authorized_attempt_result
    forged = result.execution_binding.model_copy(update={field: value})
    case["consequence"] = case["consequence"].model_copy(update={
        "authorized_attempt_result": result.model_copy(update={"execution_binding": forged}),
    })
    with pytest.raises(ValueError, match="attempt_not_authorized"):
        validate_world_consequence_authority(**case)


@pytest.mark.parametrize("change", ["missing", "duplicate", "different", "non_user"])
def test_original_request_must_contain_one_exact_authority_object(tmp_path, change):
    from companion_daemon.world_v2.world_consequence_contract import (
        validate_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    case = _validation_case(ledger, (_transition(ledger),))
    messages = case["author_messages"]
    value = json.loads(messages[1]["content"])
    if change == "missing":
        del value["execution_authority"]
    elif change == "different":
        value["execution_authority"]["execution_bindings"] = []
    elif change == "duplicate":
        messages.append(dict(messages[1]))
    else:
        messages[1]["role"] = "assistant"
    messages[1]["content"] = canonical_json(value)
    # Even a real model call with these bytes cannot prove the omitted source.
    case["author_audit"] = _audit(case["authority"], messages)
    with pytest.raises(ValueError, match="author_request_coverage"):
        validate_world_consequence_authority(**case)


def test_actual_request_hash_and_author_role_cannot_be_relabelled(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        validate_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    case = _validation_case(ledger, (_transition(ledger),))
    case["author_messages"].append({"role": "user", "content": "a different request"})
    with pytest.raises(ValueError, match="author_request_hash"):
        validate_world_consequence_authority(**case)
    case["author_messages"].pop()
    audit = case["author_audit"]
    case["author_audit"] = audit.model_copy(update={"route": audit.route.model_copy(update={
        "reason_code": "life_development.world_author_novel_origin_critic",
    })})
    with pytest.raises(ValueError, match="author_audit_binding"):
        validate_world_consequence_authority(**case)


@pytest.mark.parametrize("coordinate", ["world_revision", "deliberation_revision", "ledger_sequence"])
def test_author_decision_context_must_match_all_original_cursor_coordinates(tmp_path, coordinate):
    from companion_daemon.world_v2.world_consequence_contract import (
        validate_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    case = _validation_case(ledger, (_transition(ledger),))
    audit = case["author_audit"]
    context = audit.decision_context
    case["author_audit"] = audit.model_copy(update={
        "decision_context": context.model_copy(update={coordinate: getattr(context, coordinate) + 1}),
    })
    with pytest.raises(ValueError, match="author_audit_binding"):
        validate_world_consequence_authority(**case)


def test_raw_source_cannot_replace_committed_world_actor_or_payload(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        derive_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    source = _transition(ledger)
    pinned = ledger.project()
    for changed in (source.model_copy(update={"world_id": "other-world"}),
                    source.model_copy(update={"payload_hash": "f" * 64})):
        with pytest.raises(ValueError):
            derive_world_consequence_authority(
                pinned_state=pinned, actor_ref=ACTOR, source_events=(changed,),
            )
    with pytest.raises(ValueError, match="activity_actor_or_plan"):
        derive_world_consequence_authority(
            pinned_state=pinned, actor_ref="actor:someone-else", source_events=(source,),
        )
    with pytest.raises(ValueError, match="duplicate_source"):
        derive_world_consequence_authority(
            pinned_state=pinned, actor_ref=ACTOR, source_events=(source, source),
        )


def _receipt_case(tmp_path):
    from test_experience_authority import WORLD, initialized

    path = tmp_path / "receipt.sqlite"
    ledger = initialized(lambda **kwargs: SQLiteWorldLedger(path=path, **kwargs))
    assert ledger.world_id == WORLD
    source = ledger.lookup_event_commit("receipt:recorded")[0]
    return ledger, path, _validation_case(ledger, (source,))


def test_terminal_cancelled_receipt_is_an_exact_failure_result_after_restart(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        validate_world_consequence_authority,
    )

    ledger, path, case = _receipt_case(tmp_path)
    binding = case["authority"].execution_bindings[0]
    assert binding.observed_state == "cancelled"
    assert binding.action_id == "action:experience"
    assert binding.receipt_id == "receipt:experience"
    assert binding.privacy_class == "private"
    reopened = SQLiteWorldLedger(path=path, world_id=ledger.world_id)
    case["pinned_state"] = reopened.project()
    validate_world_consequence_authority(**case)
    # The protocol preserves the receipt state; prose entailment is not a local check.
    assert binding.model_dump(mode="json")["observed_state"] == "cancelled"


@pytest.mark.parametrize("field,value", [
    ("observed_state", "delivered"), ("action_id", "another-action"),
    ("action_payload_hash", "f" * 64), ("receipt_id", "another-receipt"),
    ("receipt_hash", "f" * 64), ("result_id", "another-result"),
    ("raw_payload_hash", "f" * 64),
])
def test_receipt_result_cannot_change_observed_state_or_action_identity(tmp_path, field, value):
    from companion_daemon.world_v2.world_consequence_contract import (
        validate_world_consequence_authority,
    )

    _, _, case = _receipt_case(tmp_path)
    result = case["consequence"].authorized_attempt_result
    case["consequence"] = case["consequence"].model_copy(update={
        "authorized_attempt_result": result.model_copy(update={
            "execution_binding": result.execution_binding.model_copy(update={field: value}),
        }),
    })
    with pytest.raises(ValueError, match="attempt_not_authorized"):
        validate_world_consequence_authority(**case)


def test_receipt_cannot_authorize_another_actor_or_unsettled_action(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        derive_world_consequence_authority,
    )

    ledger, _, case = _receipt_case(tmp_path)
    with pytest.raises(ValueError, match="receipt_action_binding"):
        derive_world_consequence_authority(
            pinned_state=ledger.project(), actor_ref="actor:another",
            source_events=case["source_events"],
        )
    state = ledger.project()
    # A forged/partial projection cannot pair a receipt with a different Action head.
    with pytest.raises(ValueError, match="receipt_action_binding"):
        derive_world_consequence_authority(
            pinned_state=state.model_copy(update={
                "actions": (state.actions[0].model_copy(update={"state": "authorized"}),),
            }), actor_ref=ACTOR, source_events=case["source_events"],
        )


@pytest.mark.parametrize("observed_state", ["provider_accepted", "unknown"])
def test_recorded_ack_or_unknown_receipt_never_becomes_objective_result(tmp_path, observed_state):
    from companion_daemon.world_v2.world_consequence_contract import (
        derive_world_consequence_authority,
    )
    from test_experience_authority import event as receipt_event

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
    with pytest.raises(ValueError, match="receipt_not_observed_result"):
        derive_world_consequence_authority(
            pinned_state=ledger.project(), actor_ref=ACTOR, source_events=(source,),
        )


def test_receipt_with_durable_reconciliation_cannot_be_promoted(tmp_path):
    from companion_daemon.world_v2.schemas import ActionReconciliation
    from companion_daemon.world_v2.world_consequence_contract import (
        derive_world_consequence_authority,
    )
    from test_experience_authority import event as receipt_event

    ledger, _, case = _receipt_case(tmp_path)
    receipt = ledger.project().execution_receipts[0]
    reconciliation = ActionReconciliation(
        reconciliation_id="reconcile:receipt", result_id=receipt.result_id,
        action_id=receipt.action_id, reason="terminal_conflict",
        observed_state=receipt.observed_state, existing_state="delivered",
        provider=receipt.provider, provider_ref=receipt.provider_ref,
        raw_payload_hash=receipt.raw_payload_hash,
    )
    commit(ledger, [receipt_event("receipt:reconciliation", "ActionReconciliationRequired", {
        "reconciliation": reconciliation.model_dump(mode="json"),
    })])
    with pytest.raises(ValueError, match="receipt_not_observed_result"):
        derive_world_consequence_authority(
            pinned_state=ledger.project(), actor_ref=ACTOR, source_events=case["source_events"],
        )


def test_legacy_ownerless_plan_is_not_granted_new_authority(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        derive_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    source = _transition(ledger)
    state = ledger.project()
    legacy = state.plans[0].model_copy(update={
        "owner_actor_ref": "legacy:unknown-owner", "authority_origin": None,
    })
    with pytest.raises(ValueError, match="activity_actor_or_plan"):
        derive_world_consequence_authority(
            pinned_state=state.model_copy(update={"plans": (legacy,)}),
            actor_ref=ACTOR, source_events=(source,),
        )


def test_original_correction_request_keeps_same_pin_but_its_own_request_hash(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        validate_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    case = _validation_case(ledger, (_transition(ledger),))
    original_hash = case["author_audit"].request_hash
    case["author_messages"].append({"role": "user", "content": canonical_json({
        "failure_code": "world_consequence.attempt_not_authorized",
    })})
    case["author_audit"] = _audit(case["authority"], case["author_messages"])
    assert case["author_audit"].request_hash != original_hash
    validate_world_consequence_authority(**case)


def test_missing_original_author_context_is_not_legacy_execution_permission(tmp_path):
    from companion_daemon.world_v2.world_consequence_contract import (
        validate_world_consequence_authority,
    )

    ledger, _ = _activity(tmp_path)
    case = _validation_case(ledger)
    case["author_audit"] = case["author_audit"].model_copy(update={"decision_context": None})
    with pytest.raises(ValueError, match="author_audit_binding"):
        validate_world_consequence_authority(**case)
