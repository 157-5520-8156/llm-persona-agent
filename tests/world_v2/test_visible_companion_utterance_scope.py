"""Recorded speech remains usable without becoming independent event evidence.

The real semantic false acceptance is still a release gate. These tests verify
source preparation and privacy/binding, not the model's entailment judgment.
"""
import json

import pytest

from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute
from companion_daemon.world_v2.visible_review_context import compile_visible_selected_source_context
from companion_daemon.world_v2.visible_source_closure_protocol import (
    compact_source_reference_table, visible_source_closure_messages,
)
from test_chat_life_intent_runtime import INTENT, _run_http_journey
from test_completed_activity_context import open_journey_ledger
from test_current_activity_context import current_context


@pytest.mark.asyncio
async def test_delivered_companion_source_scope_survives_all_reviewer_transports(tmp_path, monkeypatch):
    result, _, _, output, _ = await _run_http_journey(
        tmp_path, monkeypatch, intent=INTENT, duration_minutes=3, prefer_complete=True,
    )
    assert result["completed"], result["stop_reason"]
    ledger = open_journey_ledger(output)
    try:
        capsule, _, _ = current_context(ledger, None, actor_ref="agent:companion")
        frozen = capsule.model_dump_json()
        request = ModelInput(
            call_id="fixture:speech-scope", attempt_id="fixture:speech-scope",
            route=ModelRoute(tier="flash", reason_code="offline_fixture", router_version="fixture.1"),
            capsule_id=capsule.capsule_id, trigger_ref=capsule.trigger_ref,
            evaluated_world_revision=capsule.world_revision,
            evaluated_deliberation_revision=capsule.deliberation_revision,
            evaluated_ledger_sequence=capsule.ledger_sequence,
            model_content_json=capsule.model_content_json,
        )
        evidence = compile_visible_selected_source_context(request=request, capsule=capsule)
        own = [entry for entry in evidence["entries"] if entry["authority"] == "companion_expression_record"]
        assert own, "fixture must include an actual accepted companion expression"
        originals = {item.item_ref: item for item in capsule.recent_dialogue.items}
        for entry in own:
            assert entry["scope"] == "recorded_companion_utterance_only"
            assert "needs the original event evidence" in entry["does_not_authorize"]
            original = originals[entry["item"]["item_ref"]]
            assert entry["item"]["value"] == json.loads(original.payload_json)
            assert entry["item"]["source_hash"] == original.source_hash
            assert entry["item"]["value"]["delivery_state"] == "delivered"
        assert all("scope" not in entry for entry in evidence["entries"] if entry not in own)
        rows = compact_source_reference_table(evidence)
        own_refs = {ref for entry in own for ref in entry["source_refs"]}
        own_rows = [row for row in rows if row["source_ref"] in own_refs]
        assert own_rows and all(row["support_eligibility"] == "eligible" for row in own_rows)
        for version in ("1", "2", "3", "4", "5", "6", "7", "8"):
            packet = json.loads(visible_source_closure_messages(
                visible_beats=("刚才那句话是我说的。", "我确实做过那件事。"),
                world_claims=(), source_references=rows, version=version,
            )[1]["content"])
            material = [m for m in packet["source_materials"] if m.get("authority") == "companion_expression_record"]
            assert len(material) == len(own)
            for row, entry in zip(material, own, strict=True):
                assert row["scope"] == entry["scope"]
                assert row["does_not_authorize"] == entry["does_not_authorize"]
                for field in ("text", "speaker_ref", "occurred_at", "delivery_state"):
                    assert row["item"]["value"][field] == entry["item"]["value"][field]
        assert capsule.model_dump_json() == frozen
    finally:
        ledger.close()
