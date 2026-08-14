from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
    InboundToolContracts,
    _expand_compact_gate_payload,
)
from companion_daemon.world_v2.character_interior.inbound_wire import (
    _is_lossless_minimal_reply_draft,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.expression_draft import (
    ExpressionDraft,
    QQ_NAPCAT_EXPRESSION_CAPABILITIES,
)
from companion_daemon.world_v2.present_prompt import (
    compile_slim_consider_payload,
    compile_slim_interior_envelope,
    json_schema_g4_metrics,
)
from test_expectation_feelings import (
    HOPED,
    NOW as EXPECTATION_NOW,
    _expectation,
    _fake_projection,
)
from test_social_initiative import _compiler_fixture


def _slim_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "messages": ["那件事后来怎么样了"],
        "felt": "还挂着他没回的那句",
        "stuck_with_me": "我说完就在等他开口",
        "wants": "想听他怎么说",
        "photo": False,
    }
    payload.update(updates)
    return payload


def test_slim_waiting_for_becomes_a_declared_expectation() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(waiting_for="他回来把那件事说完")
    )

    assert compiled is not None
    expectation = compiled["expression_draft"]["response_expectation"]
    assert expectation["hoped_response"] == "他回来把那件事说完"
    assert expectation["pressure_bp"] == 5_000
    assert expectation["importance_bp"] == 5_000
    assert expectation["expires_after_seconds"] > expectation["wait_seconds"]
    envelope = compile_slim_interior_envelope(
        _slim_payload(waiting_for="他回来把那件事说完"),
        reply_only=True,
    )
    assert envelope is not None
    assert envelope["events"][0]["response_expectation"]["hoped_response"] == (
        "他回来把那件事说完"
    )


def test_slim_does_not_infer_expectation_from_a_question() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(messages=["那件事后来怎么样了？"])
    )

    assert compiled is not None
    assert compiled["expression_draft"].get("response_expectation") is None


def test_slim_waiting_for_is_clipped_instead_of_failing_the_turn() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(waiting_for="他" * 200)
    )

    assert compiled is not None
    assert compiled["expression_draft"]["response_expectation"]["hoped_response"] == (
        "他" * 128
    )


def test_slim_consider_schema_still_fits_g4_after_waiting_for() -> None:
    from companion_daemon.world_v2.present_prompt import slim_consider_json_schema

    required, total, depth = json_schema_g4_metrics(slim_consider_json_schema())
    assert required <= 3
    assert total <= 8
    assert depth <= 2
    compact = InboundToolContracts().compact_gate_for(
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=True,
    )
    compact_required, compact_total, compact_depth = json_schema_g4_metrics(
        compact.provider_tools[0]["function"]["parameters"]
    )
    assert compact_required <= 3
    assert compact_total <= 8
    assert compact_depth <= 2


def test_silence_consider_reads_the_pending_expectation_she_declared() -> None:
    from companion_daemon.world_v2.response_expectation_view import (
        attach_pending_expectation_advisory,
    )

    projection = _fake_projection(
        logical_time=EXPECTATION_NOW + timedelta(minutes=10),
        expectation=_expectation(expires_at=EXPECTATION_NOW + timedelta(hours=1)),
    )
    context = {
        "world_id": "world:h13-silence",
        "actor_ref": "character:zhizhi",
        "world_revision": 5,
        "deliberation_revision": 0,
        "ledger_sequence": 1,
        "logical_time": (EXPECTATION_NOW + timedelta(minutes=10)).isoformat(),
        "consumer_scope": "deliberation_internal",
        "viewer_privacy_ceiling": "private",
        "context_compiler_version": "context-capsule-compiler:test",
        "truncation": {},
        "slices": {},
    }

    attached = attach_pending_expectation_advisory(
        context,
        projection,
        anchor_event_ref="event:receipt:invite",
    )
    snapshot = compile_inner_life_snapshot(attached)
    advisories = snapshot.materials.get("advisories")

    assert isinstance(advisories, list)
    assert advisories
    assert advisories[0]["kind"] == "response_expectation"
    assert HOPED in advisories[0]["candidates"][0]["value"]


def test_slim_how_it_landed_becomes_an_assessment_on_the_same_inbound() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(
            messages=["嗯，我听完了"],
            felt="他终于把那件事说清楚了",
            how_it_landed="fulfilled",
        )
    )

    assert compiled is not None
    assessment = compiled["expression_draft"]["response_expectation_assessment"]
    assert assessment["status"] == "fulfilled"
    assert assessment["reason"] == "他终于把那件事说清楚了"
    envelope = compile_slim_interior_envelope(
        _slim_payload(
            messages=["嗯，我听完了"],
            felt="他终于把那件事说清楚了",
            how_it_landed="fulfilled",
        ),
        reply_only=True,
    )
    assert envelope is not None
    assert envelope["events"][0]["response_expectation_assessment"]["status"] == (
        "fulfilled"
    )
    expanded = _expand_compact_gate_payload(
        {
            "result_kind": "reply_only",
            "payload_json": json.dumps(
                _slim_payload(
                    messages=["嗯，我听完了"],
                    felt="他终于把那件事说清楚了",
                    how_it_landed="fulfilled",
                ),
                ensure_ascii=False,
            ),
        }
    )
    assert expanded["events"][0]["response_expectation_assessment"]["status"] == (
        "fulfilled"
    )


def test_assessment_bearing_slim_reply_stays_lossless_for_minimal_recovery() -> None:
    draft = ExpressionDraft.model_validate_json(
        json.dumps(
            {
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "他终于把那件事说清楚了",
                    "attended_source_refs": [],
                },
                "timing_choice": "now",
                "cadence": "conversational",
                "beats": [{"modality": "text", "text": "嗯，我听完了"}],
                "stance": "acknowledge_briefly",
                "brief_rationale": "他终于把那件事说清楚了",
                "confidence": 5000,
                "world_claims": [],
                "media_request": "none",
                "media_source_refs": [],
                "response_expectation_assessment": {
                    "status": "fulfilled",
                    "reason": "他终于把那件事说清楚了",
                },
            },
            ensure_ascii=False,
        ),
        strict=True,
    )

    assert _is_lossless_minimal_reply_draft(draft) is True


@pytest.mark.asyncio
async def test_pending_expectation_does_not_mint_a_generic_idle_opportunity() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=True)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **kwargs: SimpleNamespace(
            selected_candidate_ref="delay:3600",
            draw_id="draw:h13-idle",
        )
    )
    action = SimpleNamespace(action_id="action:invite", kind="reply", state="delivered")
    receipt_ref = SimpleNamespace(
        event_id="event:receipt:invite",
        event_type="ExecutionReceiptRecorded",
        world_revision=2,
        logical_time=EXPECTATION_NOW,
    )
    projection.actions = (action,)
    projection.execution_receipts = (
        SimpleNamespace(action_id="action:invite", observed_state="delivered"),
    )
    projection.committed_world_event_refs = (receipt_ref,)
    projection.expression_plan_manifests = (
        SimpleNamespace(
            plan_id="plan:invite",
            acceptance_event_ref="event:acceptance:invite",
            recorded_at_world_revision=1,
            response_expectation=SimpleNamespace(
                source_beat_id="beat:invite",
                hoped_response=HOPED,
                pressure_bp=5_000,
                importance_bp=5_000,
                not_before=EXPECTATION_NOW + timedelta(minutes=1),
                expires_at=projection.logical_time + timedelta(hours=6),
                delivery_requirement="provider_accepted_or_delivered",
            ),
            beats=(SimpleNamespace(beat_id="beat:invite", action=action),),
        ),
    )
    projection.response_expectation_assessments = ()

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is None
    assert committed == []
