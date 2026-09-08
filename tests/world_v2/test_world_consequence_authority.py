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
        model_result_ref="model-result:world-author:fixture",
        attempt_id="attempt:world-author:fixture",
        route=RecordedModelRoute(tier="flash", reason_code="life_development.world_author",
                                 router_version="life-development-router.2"),
        model_id="offline-fixture", model_version="fixture.1",
        request_hash=sha256(canonical_json(messages)), response_hash="a" * 64,
        decision_context=RecordedModelDecisionContext(
            decision_subject_hash="b" * 64,
            **authority.evaluated_cursor.model_dump(),
        ),
        status="candidate_returned",
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
