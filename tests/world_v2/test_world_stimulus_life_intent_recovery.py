"""Recover the life facet when another real consumer already settled Appraisal."""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.life_events import ActivityPlannedPayload
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_life_intent_runtime import PLAN_PREFIX
from test_character_interior_world_stimulus import (
    SOURCE_REF,
    _RoleModel,
    _cursor_for,
    _runtime_for_ledger,
)
from test_life_projection import WORLD_ID, commit, seed_through_proposal, settlement_batch


class _AppraisalAndIntent(_RoleModel):
    async def complete(self, messages, *, temperature=0.8):
        result = json.loads(await super().complete(messages, temperature=temperature))
        result["proposals"][0]["life_intent"] = {
            "source_event_ref": self.source_ref,
            "execution_scope": "self_directed",
            "intention": "我想理一理接下来要做的事。",
            "start_after_seconds": 0,
            "duration_seconds": 120,
            "importance_bp": 5300,
        }
        return json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_terminal_appraisal_recovers_unsettled_life_intent_without_reauthoring(
    tmp_path, monkeypatch
):
    path = tmp_path / "terminal-life-intent.sqlite"
    issuer = AcceptedLedgerBatchIssuer()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    seed_through_proposal(ledger, participant_refs=("actor:companion",))
    commit(ledger, settlement_batch(participant_refs=("actor:companion",)))
    model = _AppraisalAndIntent()
    runtime, _, _ = _runtime_for_ledger(
        ledger=ledger,
        issuer=issuer,
        model=model,
        source_ref=SOURCE_REF,
        companion_actor_ref="actor:companion",
    )

    def crash_before_plan(**kwargs):
        raise RuntimeError("crash before life intent acceptance")

    # Crash after the actual character audit, without fabricating accepted data.
    monkeypatch.setattr(runtime._world_life_intent, "accept", crash_before_plan)
    with pytest.raises(RuntimeError, match="crash before life intent acceptance"):
        await runtime.drain_one()
    assert model.calls == 1
    audit = next(
        x
        for x in ledger.project().proposal_audits
        if x.proposal_id.startswith("proposal:character-interior-world-stimulus:")
    )
    audit_commit = ledger.lookup_event_commit(audit.event_ref)[1]
    audit_cursor = ProjectionCursor(
        world_revision=audit_commit.world_revision,
        deliberation_revision=audit_commit.deliberation_revision,
        ledger_sequence=audit_commit.ledger_sequence,
    )
    # The independently available emotion worker can consume this same audit.
    emotion = runtime._emotion_worker.process(
        world_id=WORLD_ID,
        audit_cursor=audit_cursor,
        current_cursor=_cursor_for(ledger),
        proposal_id=audit.proposal_id,
    )
    assert emotion.status == "appraisal_only"
    assert any(
        x.state == "terminal" and x.source_evidence_ref == SOURCE_REF
        for x in ledger.project().trigger_processes
    )
    assert not any(x.plan_id.startswith(PLAN_PREFIX) for x in ledger.project().plans)
    ledger.close()

    issuer = AcceptedLedgerBatchIssuer()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    recovery_model = _RoleModel(failure=AssertionError("must reuse the durable role decision"))
    recovered, _, _ = _runtime_for_ledger(
        ledger=ledger,
        issuer=issuer,
        model=recovery_model,
        source_ref=SOURCE_REF,
        companion_actor_ref="actor:companion",
    )
    try:
        assert (await recovered.drain_one()).work_status == "accepted"
        plans = [x for x in ledger.project().plans if x.plan_id.startswith(PLAN_PREFIX)]
        assert len(plans) == 1
        planned = ledger.lookup_event_commit(
            "event:world-life-intent:" + plans[0].plan_id.removeprefix(PLAN_PREFIX)
        )[0]
        origin = ActivityPlannedPayload.model_validate_json(
            planned.payload_json
        ).world_intent_origin
        assert origin.proposal_id == audit.proposal_id
        assert origin.source_event_ref == SOURCE_REF
        assert plans[0].scheduled_window.opens_at == origin.selected_at
        assert recovery_model.calls == 0
        before = ledger.project()
        assert (await recovered.drain_one()).status == "idle"
        assert ledger.project() == before
        assert ledger.rebuild() == before
    finally:
        ledger.close()
