"""Brief cognition is model-owned; runtime sources retain narrow authority."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace as N

import pytest
from pydantic import ValidationError

from companion_daemon.world_v2.character_interior.life_runtime_readings import runtime_readings
from companion_daemon.world_v2.character_interior.life_source_origin import canonical, digest
from companion_daemon.world_v2.proposal_envelope import validate_proposal_envelope
from test_character_interior_world_stimulus import _RoleModel, _runtime


class BriefRole(_RoleModel):
    async def complete(self, messages, *, temperature=0.8):
        value = json.loads(await super().complete(messages, temperature=temperature))
        value["summary"] = "开心。"
        p = value["proposals"][0]
        p.update(
            reflection_depth="brief",
            brief_rationale="开心。",
            behavior_tendency=None,
            stance=None,
            display_strategy=None,
        )
        p["meaning_candidates"] = [{"meaning": "开心。", "confidence": 6000}]
        return json.dumps(value, ensure_ascii=False)


@pytest.mark.asyncio
async def test_brief_feeling_reaches_affect_without_inventing_behavior_explanations():
    model = BriefRole(decision="activate", include_affect=True)
    runtime, ledger, _ = _runtime(model=model)
    result = await runtime.drain_one()
    assert result.work_status == "accepted" and model.calls == 1
    state = ledger.project()
    assert state.affect_episodes[0].components[0].dimension == "joy"
    proposals = [json.loads(p.proposal_json) for p in state.proposal_audits]
    authored = next(p for p in proposals if p.get("interior_reflection_depth") == "brief")
    assert (
        authored["behavior_tendency"] is authored["stance"] is authored["display_strategy"] is None
    )
    assert not authored["action_intents"]
    packet = json.loads(model.messages[0][1]["content"])
    assert "proposal_example_activate" not in packet["purpose_contract"]
    assert packet["reflection_scope"]["contract"] == "role-owned-reflection-depth.1"


def test_absent_explanation_cannot_sneak_into_visible_action_proposal():
    from test_expression_plan_acceptance import _proposal

    raw = _proposal().model_dump(mode="json")
    raw.update(interior_reflection_depth="brief", behavior_tendency=None)
    with pytest.raises(ValidationError, match="internal-only"):
        validate_proposal_envelope(raw)


def receipt_case():
    at = datetime(2026, 9, 28, tzinfo=UTC)
    receipt = dict(
        schema_version="world-v2.1",
        receipt_id="receipt",
        result_id="result",
        action_id="action",
        provider="platform:test",
        provider_ref="transport:private",
        source_event_id="source",
        receipt_kind="terminal",
        observed_state="delivered",
        is_terminal=True,
        artifact_refs=[],
        cost_actual=0,
        error_class=None,
        received_at=at.isoformat(),
        raw_payload_hash="raw",
    )
    payload = {"receipt": receipt}
    sha = digest(canonical(payload))
    cap = {
        "source_event": {
            "event_id": "event:receipt",
            "event_type": "ExecutionReceiptRecorded",
            "logical_time": at.isoformat(),
            "payload_hash": sha,
            "payload": payload,
        }
    }
    manifest = {
        "capability_kind": "world_stimulus_appraisal",
        "payload": cap,
        "payload_hash": "sha256:" + digest(canonical(cap)),
    }
    snapshot = N(
        actor_ref="companion",
        cursor=N(world_revision=5),
        logical_time=at,
        source_inventory=[],
        materials={
            "capability_evidence": [
                {
                    "source_ref": "event:receipt",
                    "event_type": "ExecutionReceiptRecorded",
                    "payload_hash": sha,
                    "source_world_revision": 5,
                }
            ]
        },
    )
    return N(author_payload=lambda: {"capability_manifest": manifest}), snapshot, manifest


def test_receipt_grants_transport_evidence_not_another_persons_attention():
    view, snapshot, _ = receipt_case()
    (row,) = runtime_readings(view=view, snapshot=snapshot)
    assert row["value"]["observed_state"] == "delivered"
    assert "read" in row["scope"] and "does not prove" in row["scope"]
    assert not {"provider_ref", "action_id", "raw_payload_hash"}.intersection(row["value"])


@pytest.mark.parametrize("fault", ["payload", "pin", "hash"])
def test_runtime_readings_need_original_capability_event_and_snapshot(fault):
    view, snapshot, manifest = receipt_case()
    if fault == "payload":
        manifest["payload"]["source_event"]["payload"]["receipt"]["observed_state"] = "unknown"
        with pytest.raises(ValueError):
            runtime_readings(view=view, snapshot=snapshot)
    else:
        proof = snapshot.materials["capability_evidence"][0]
        proof["source_world_revision" if fault == "pin" else "payload_hash"] = (
            6 if fault == "pin" else "wrong"
        )
        assert runtime_readings(view=view, snapshot=snapshot) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('dimension', ['anger', 'hurt', 'loneliness'])
async def test_brief_negative_affect_is_accepted_persisted_and_not_reauthored(dimension):
    class NegativeRole(BriefRole):
        async def complete(self, messages, *, temperature=0.8):
            value = json.loads(await super().complete(messages, temperature=temperature))
            value['proposals'][0]['affect_transition']['component_targets'][0]['dimension'] = dimension
            text = {'anger': '有点生气。', 'hurt': '有点受伤。', 'loneliness': '有点孤独。'}[dimension]
            value['summary'] = text
            value['proposals'][0]['brief_rationale'] = text
            value['proposals'][0]['meaning_candidates'][0]['meaning'] = text
            return json.dumps(value, ensure_ascii=False)

    model = NegativeRole(decision='activate', include_affect=True)
    runtime, ledger, _ = _runtime(model=model)
    assert (await runtime.drain_one()).work_status == 'accepted'
    first = ledger.project()
    assert first.affect_episodes[0].components[0].dimension == dimension
    assert first.affect_episodes[0].components[0].intensity_bp > 0
    await runtime.drain_one()
    assert model.calls == 1 and ledger.project().affect_episodes == first.affect_episodes
