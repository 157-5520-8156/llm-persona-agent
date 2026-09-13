"""The public wake reader must honor the existing model retry authority."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.appraisal_trigger import CHARACTER_INTERIOR_INBOUND_ATTEMPT_PREFIX
from companion_daemon.world_v2.declared_due import collect_projection_declared_dues
from companion_daemon.world_v2.schemas import ClaimLease, TriggerProcess
from test_expression_episode_lifecycle import NOW, _claimed_process, _projection


def _failed_pair():
    expression = _claimed_process(attempt=2)
    projection = _projection(processes=(expression,), logical_time=NOW + timedelta(minutes=2))
    terminal = projection.model_result_audits[0]
    projection.model_result_audits = tuple(
        SimpleNamespace(**(vars(terminal) | {"attempt_id": attempt,
            "deliberation_result_id": f"deliberation:{index}"}))
        for index, attempt in enumerate(expression.attempt_ids)
    )
    attempt = CHARACTER_INTERIOR_INBOUND_ATTEMPT_PREFIX + "fixture"
    appraisal = TriggerProcess(
        trigger_id="trigger:appraisal:fixture", trigger_ref="interaction:fixture",
        process_kind="interaction_appraisal", source_evidence_ref=expression.source_evidence_ref,
        state="claimed", attempt_ids=(attempt,), claim_lease=ClaimLease(
            owner_id="worker:state", attempt_id=attempt,
            acquired_at=NOW - timedelta(seconds=30), expires_at=NOW + timedelta(seconds=90),
        ),
    )
    projection.trigger_processes = (expression, appraisal)
    return projection, expression, appraisal


def _lease_dues(projection):
    return [target.due_at for target in collect_projection_declared_dues(projection)
            if target.field == "ClaimLease.expires_at"]


def test_completed_failed_expression_does_not_wake_before_its_retry():
    projection, expression, _appraisal = _failed_pair()
    projection.trigger_processes = (expression,)
    assert _lease_dues(projection)
    assert min(_lease_dues(projection)) == NOW + timedelta(minutes=30)


def test_inline_appraisal_waits_for_same_missing_role_result():
    projection, _expression, _appraisal = _failed_pair()
    assert min(_lease_dues(projection)) == NOW + timedelta(minutes=30)
    assert all(process.state == "claimed" for process in projection.trigger_processes)


@pytest.mark.parametrize("case", ["inflight", "unbound_failure", "other_observation", "standalone", "durable_role", "durable_appraisal", "missing_observation"])
def test_unproven_dependency_or_durable_result_keeps_appraisal_lease_due(case):
    projection, expression, appraisal = _failed_pair()
    if case == "inflight":
        projection.model_result_audits = ()
    elif case == "unbound_failure":
        for audit in projection.model_result_audits:
            audit.attempt_id = "attempt:another-author"
    elif case == "other_observation":
        appraisal = appraisal.model_copy(update={"source_evidence_ref": "observation:other"})
    elif case == "standalone":
        appraisal = appraisal.model_copy(update={"claim_lease": appraisal.claim_lease.model_copy(
            update={"attempt_id": "attempt:standalone-appraisal"})})
    elif case in {"durable_role", "durable_appraisal"}:
        projection.proposal_audits = (SimpleNamespace(
            proposal_id=("proposal:expression:fixture" if case == "durable_role" else "proposal:appraisal-draft:fixture"),
            proposal_kind="decision", trigger_ref=projection.committed_world_event_refs[0].event_id,
            attempt_id=expression.attempt_ids[-1] if case == "durable_role" else "attempt:appraisal",
        ),)
    else:
        projection.message_observations = ()
    projection.trigger_processes = (expression, appraisal)
    assert appraisal.claim_lease.expires_at in _lease_dues(projection)


def test_unrelated_claim_is_not_hidden_by_expression_backoff():
    projection, expression, _appraisal = _failed_pair()
    unrelated = SimpleNamespace(process_kind="unrelated", state="claimed",
        claim_lease=SimpleNamespace(expires_at=NOW))
    projection.trigger_processes = (expression, unrelated)
    assert NOW in _lease_dues(projection)
