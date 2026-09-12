"""Exact source aliases do not decide the role's semantic claim scope."""

from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2 import expression_draft as expression
from test_character_interior_inbound_wire import _qq_request


@pytest.mark.parametrize("scope,lane,event_type,value,text", [
    ("current_world", "current_situation", "ClockAdvanced",
     {"logical_time": "2026-07-30T06:08:00Z"}, "现在是下午两点零八分。"),
    ("past_world", "recent_experiences", "ExperienceCommitted",
     {"experience_id": "experience:one", "summary": "整理活动结束了。"}, "整理活动结束了。"),
    ("shared_history", "recent_dialogue", "ActionAuthorized",
     {"speaker": "companion", "text": "我想喝水。", "epistemic_scope": "companion_expression_record"},
     "我刚才说我想喝水。"),
])
def test_valid_scope_and_exact_alias_produce_the_same_immutable_proposal(
    monkeypatch, scope, lane, event_type, value, text,
):
    ref = "event:source:" + event_type + ":" + "a" * 64
    context = {
        "logical_time": "2026-07-30T06:08:00Z", "actor_ref": "agent:companion",
        "slices": {lane: {"availability": "available", "source_refs": [ref], "items": [{
            "item_ref": ref, "value": value, "source_bindings": [{
                "source_kind": "committed_event", "authority_type": event_type,
                "ref": ref, "source_world_revision": 3, "immutable_hash": "b" * 64,
            }],
        }]}},
    }
    request = _qq_request().model_copy(update={"model_content_json": json.dumps(context)})
    allowed = expression.world_claim_source_refs_by_scope(context=context)
    assert ref in allowed[scope]
    aliases = expression.build_source_ref_alias_table(request=request)
    alias = aliases.alias_for(ref)
    assert alias is not None and alias != ref
    raw = {
        "timing_choice": "now", "stance": "test", "brief_rationale": "test",
        "beats": [{"modality": "text", "text": text}],
        "world_claims": [{"claim_text": text, "scope": scope, "source_refs": [ref]}],
    }
    original = deepcopy(raw)
    observed_scopes = []
    validate = expression._validate_world_claims

    def record_validation(**kwargs):
        observed_scopes.append(kwargs["draft"].world_claims[0].scope)
        return validate(**kwargs)

    monkeypatch.setattr(expression, "_validate_world_claims", record_validation)
    canonical = expression.materialize_expression_draft(
        value=raw, request=request, capabilities=expression.TEXT_ONLY_EXPRESSION_CAPABILITIES,
        source_ref_aliases=aliases,
    )
    aliased = deepcopy(raw)
    aliased["world_claims"][0]["source_refs"] = [alias]
    short = expression.materialize_expression_draft(
        value=aliased, request=request, capabilities=expression.TEXT_ONLY_EXPRESSION_CAPABILITIES,
        source_ref_aliases=aliases,
    )
    assert raw == original
    assert observed_scopes == [scope, scope]
    assert canonical.model_dump_json() == short.model_dump_json()
    assert any(item.ref_id == ref for item in short.evidence_refs)
    plan = short.proposed_changes[0].payload.value()
    assert plan["beat_drafts"][0]["inline_text"] == text
