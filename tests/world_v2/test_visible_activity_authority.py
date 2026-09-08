"""Current role/lifecycle producers qualify only their exact activity state.

HTTP is MockTransport. No fixture directly appends accepted Plan/activity events.
"""

from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.character_interior.inbound_wire import (
    _known_capsule_source_refs,
    _source_closure_evidence,
)
from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute
from companion_daemon.world_v2.expression_draft import ExpressionDraft
from companion_daemon.world_v2.visible_source_closure_protocol import (
    VisibleSourceClosureWireFailure,
    compact_source_reference_table,
    parse_visible_source_closure,
    visible_source_closure_messages,
)
from test_chat_life_intent_runtime import INTENT, _run_http_journey
from test_completed_activity_context import open_journey_ledger
from test_current_activity_context import current_context


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _selected_evidence(capsule, source_ref, *, scope="current_world"):
    request = ModelInput(
        call_id="fixture:activity-review",
        attempt_id="fixture:activity-review",
        route=ModelRoute(tier="flash", reason_code="offline_fixture", router_version="fixture.1"),
        capsule_id=capsule.capsule_id,
        trigger_ref=capsule.trigger_ref,
        evaluated_world_revision=capsule.world_revision,
        evaluated_deliberation_revision=capsule.deliberation_revision,
        evaluated_ledger_sequence=capsule.ledger_sequence,
        model_content_json=capsule.model_content_json,
    )
    draft = ExpressionDraft(
        timing_choice="now",
        beats=({"modality": "text", "text": "这段活动的状态。"},),
        stance="fixture",
        brief_rationale="Fixture cites an already selected lifecycle source.",
        world_claims=(
            {"claim_text": "这段活动的状态", "scope": scope, "source_refs": (source_ref,)},
        ),
    )
    return _source_closure_evidence(
        request=request,
        draft=draft,
        visible_context_json=capsule.model_content_json,
        identity_frame=None,
    )


def _closed(rows, ref, *, actor="companion", text="这段活动已经结束了。"):
    index = next(i for i, row in enumerate(rows) if row["source_ref"] == ref)
    return parse_visible_source_closure(
        _json(
            {
                "contract": "visible-beat-source-verdict.1",
                "decisions": [
                    {
                        "beat_index": 0,
                        "verdict": "closed",
                        "semantic_role": "external_proposition",
                        "subject_role": actor,
                        "source_ref_indexes": [index],
                    }
                ],
            }
        ),
        visible_beats=(text,),
        source_references=rows,
        source_ref_kinds=tuple(row["kind"] for row in rows),
        source_ref_subject_roles=tuple(row["subject_role"] for row in rows),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "completed"])
async def test_current_chat_intent_lifecycle_reaches_review_without_goal_success(
    tmp_path,
    monkeypatch,
    status,
):
    result, events, _, output, calls = await _run_http_journey(
        tmp_path,
        monkeypatch,
        intent={**INTENT, "duration_seconds": 180 if status == "completed" else 600},
        duration_minutes=5 if status == "completed" else 3,
        prefer_complete=True,
    )
    assert result["completed"], result["stop_reason"]
    assert calls == 1
    for kind in ("ActivityPlanned", "ActivityStarted"):
        assert len([event for event in events if event["event_type"] == kind]) == 1
    assert sum(event["event_type"] == "ActivityCompleted" for event in events) == (
        status == "completed"
    )
    assert not any(
        event["event_type"] in {"WorldOccurrenceCommitted", "ExperienceCommitted"}
        for event in events
    )
    event_type = "ActivityCompleted" if status == "completed" else "ActivityStarted"
    beat = "这段活动已经结束了。" if status == "completed" else "这段活动正在进行中。"
    lifecycle = next(event for event in events if event["event_type"] == event_type)
    ledger = open_journey_ledger(output)
    try:
        capsule, _, _ = current_context(ledger, None, actor_ref="agent:companion")
        item = next(
            item
            for item in json.loads(capsule.model_content_json)["slices"]["world_life"]["items"]
            if item["value"].get("context_kind") == f"{status}_activity"
        )
        original = deepcopy(item)
        # The existing source producer resolves a Started ref through the
        # earlier current_situation slice. Select the already visible complete
        # world-life item by its exact value hash for this qualification case.
        # The canonical-ref-only limitation remains an explicit negative below.
        if status == "active":
            shadowed = compact_source_reference_table(
                _selected_evidence(capsule, lifecycle["event_id"])
            )
            assert all(row["support_eligibility"] == "baseline_only" for row in shadowed)
            assert not any(row.get("activity_support") for row in shadowed)
            with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
                _closed(shadowed, lifecycle["event_id"], text=beat)
        evidence = _selected_evidence(
            capsule,
            original["value_hash"] if status == "active" else lifecycle["event_id"],
            scope="past_world" if status == "completed" else "current_world",
        )
        rows = compact_source_reference_table(evidence)
        row = next(row for row in rows if row["source_ref"] == lifecycle["event_id"])
        assert row["support_eligibility"] == "eligible"
        assert row["support_subject_role"] == "companion"
        assert row["support_subject_ref"] == "agent:companion"
        assert row["activity_support"] == {
            "contract": "visible-activity-source.1",
            "status": status,
            "source_event_type": event_type,
            "scope": "activity_lifecycle_ended_not_intention_fulfilled"
            if status == "completed"
            else "activity_in_progress_not_intention_fulfilled",
        }
        assert row["review_material"]["lane"] == "world_life"
        packet = json.loads(
            visible_source_closure_messages(
                visible_beats=(beat,),
                world_claims=(),
                source_references=rows,
            )[1]["content"]
        )
        selected = packet["source_materials"][
            next(
                row["material_index"]
                for row in packet["source_references"]
                if row["source_ref"] == lifecycle["event_id"]
            )
        ]
        visible_items = selected["slice"]["items"] if "slice" in selected else [selected["item"]]
        visible = next(
            value for value in visible_items if value["item_ref"] == original["item_ref"]
        )
        for field in ("value", "value_hash", "source_hash", "source_bindings", "privacy_class"):
            assert visible[field] == original[field]
        assert (
            hashlib.sha256(_json(visible["value"]).encode()).hexdigest() == original["value_hash"]
        )
        assert _known_capsule_source_refs(evidence) == frozenset(
            ref for entry in evidence["entries"] for ref in entry["source_refs"]
        )
        assert _closed(rows, lifecycle["event_id"], text=beat)
        with pytest.raises(VisibleSourceClosureWireFailure, match="actor"):
            _closed(rows, lifecycle["event_id"], actor="counterpart", text=beat)
        for other in rows:
            if other["source_ref"] != lifecycle["event_id"]:
                assert other["support_eligibility"] == "baseline_only"
                with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
                    _closed(rows, other["source_ref"])

        # Corrupt the host material after projection. No accepted domain event
        # is fabricated; parser must recheck exact material, not trust a label.
        for mutation in ("text", "owner", "status", "binding", "source_hash", "privacy", "scope"):
            changed = deepcopy(rows)
            changed_row = changed[row["source_ref_index"]]
            changed_material = changed_row["review_material"]
            changed_item = (
                changed_material["item"]
                if "item" in changed_material
                else changed_material["slice"]["items"][0]
            )
            if mutation == "text":
                changed_item["value"]["accepted_intention"]["text"] += " invented"
            elif mutation == "owner":
                changed_item["value"]["owner_actor_ref"] = "user:unrelated"
            elif mutation == "status":
                changed_item["value"]["status"] = "completed" if status == "active" else "active"
            elif mutation == "binding":
                next(
                    binding
                    for binding in changed_item["source_bindings"]
                    if binding["ref"] == lifecycle["event_id"]
                )["authority_type"] = "ActivityPlanned"
            elif mutation == "source_hash":
                changed_item["source_hash"] = "0" * 64
            elif mutation == "privacy":
                changed_item["privacy_class"] = "withhold"
            else:
                changed_row["activity_support"]["scope"] = "intention_fulfilled"
            with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
                _closed(changed, lifecycle["event_id"], text=beat)

        # Even a newly calculated outer value hash cannot authorize an
        # incomplete intention, wrong typed state, or mismatching inner proof.
        for mutation in (
            "intention_hash",
            "truncated",
            "owner",
            "status",
            "inner_binding",
            "event_type",
        ):
            altered = deepcopy(evidence)
            entry = altered["entries"][0]
            item = entry["item"] if "item" in entry else entry["slice"]["items"][0]
            value = item["value"]
            if mutation == "intention_hash":
                value["accepted_intention"]["text"] += " invented"
            elif mutation == "truncated":
                value["accepted_intention"]["truncated"] = True
            elif mutation == "owner":
                value["owner_actor_ref"] = "actor:not-in-evidence-subjects"
            elif mutation == "status":
                value["status"] = "planned"
            elif mutation == "inner_binding":
                value["source_bindings"][0]["authority_payload_hash"] = "0" * 64
            else:
                next(
                    binding
                    for binding in item["source_bindings"]
                    if binding["ref"] == lifecycle["event_id"]
                )["authority_type"] = "ActivityPlanned"
                item["source_hash"] = hashlib.sha256(
                    _json(item["source_bindings"]).encode()
                ).hexdigest()
            item["value_hash"] = hashlib.sha256(_json(value).encode()).hexdigest()
            invalid = compact_source_reference_table(altered)
            assert (
                next(r for r in invalid if r["source_ref"] == lifecycle["event_id"])[
                    "support_eligibility"
                ]
                == "baseline_only"
            ), mutation
            with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
                _closed(invalid, lifecycle["event_id"], text=beat)

        for privacy_level in ("entry", "item", "value", "unavailable"):
            hidden = deepcopy(evidence)
            entry = hidden["entries"][0]
            item = entry["item"] if "item" in entry else entry["slice"]["items"][0]
            if privacy_level == "unavailable":
                entry["availability"] = "unavailable"
            else:
                target = {"entry": entry, "item": item, "value": item["value"]}[privacy_level]
                target["privacy_class"] = "withhold"
            hidden_rows = compact_source_reference_table(hidden)
            hidden_messages = visible_source_closure_messages(
                visible_beats=(beat,),
                world_claims=(),
                source_references=hidden_rows,
            )
            assert original["value"]["accepted_intention"]["text"] not in _json(hidden_messages)
            assert all(r["support_eligibility"] == "baseline_only" for r in hidden_rows)
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_accepted_future_chat_plan_does_not_gain_lifecycle_support(tmp_path, monkeypatch):
    result, events, _, output, calls = await _run_http_journey(
        tmp_path,
        monkeypatch,
        duration_minutes=3,
        initial_lifecycle_decision="no_op",
    )
    assert result["completed"] and calls == 1
    planned = next(event for event in events if event["event_type"] == "ActivityPlanned")
    assert not any(
        event["event_type"] in {"ActivityStarted", "ActivityCompleted"} for event in events
    )
    ledger = open_journey_ledger(output)
    try:
        capsule, _, _ = current_context(ledger, None, actor_ref="agent:companion")
        evidence = _selected_evidence(capsule, planned["event_id"])
        rows = compact_source_reference_table(evidence)
        assert not any(row.get("activity_support") for row in rows)
        assert all(row["support_eligibility"] == "baseline_only" for row in rows)
        # An absent source cannot be supplied as a closed reference either.
        if rows:
            with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
                _closed(rows, rows[0]["source_ref"])
    finally:
        ledger.close()
