"""Exact source aliases do not decide the role's semantic claim scope."""

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from companion_daemon.world_v2 import expression_draft as expression
from companion_daemon.world_v2.proactive_action import ProactiveDraft
from test_character_interior_inbound_wire import _qq_request


def test_unambiguous_world_claim_text_alias_normalizes_without_dropping_content():
    raw = {
        "world_claims": [{
            "text": "后来她说照片已经用过。",
            "scope": "past_world",
            "source_refs": ["S1"],
        }],
    }

    normalized = expression._normalize_world_claim_aliases(raw)

    assert normalized["world_claims"] == [{
        "claim_text": "后来她说照片已经用过。",
        "scope": "past_world",
        "source_refs": ["S1"],
    }]
    claim = expression.WorldClaimDraft.model_validate_json(
        json.dumps(normalized["world_claims"][0], ensure_ascii=False),
    )
    assert claim.claim_text == "后来她说照片已经用过。"
    ambiguous = expression._normalize_world_claim_aliases({
        "world_claims": [{
            "text": "旧正文。",
            "claim_text": "规范字段已有正文。",
            "scope": "past_world",
            "source_refs": ["S1"],
        }],
    })
    with pytest.raises(ValidationError):
        expression.WorldClaimDraft.model_validate_json(
            json.dumps(ambiguous["world_claims"][0], ensure_ascii=False),
        )


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


def test_one_story_claim_can_cite_a_bounded_source_closed_scene_chain():
    refs = tuple(f"S{index}" for index in range(13))
    claim = expression.WorldClaimDraft(
        claim_text="她们后来把那次照片的事说开了一些。",
        scope="past_world",
        source_refs=refs,
    )

    assert claim.source_refs == refs
    with pytest.raises(ValidationError):
        expression.WorldClaimDraft(
            claim_text="超过有界场景链的来源数。",
            scope="past_world",
            source_refs=tuple(f"S{index}" for index in range(17)),
        )


def test_unreviewed_inbound_does_not_advertise_settled_event_share_sources():
    settled_ref = "event:settlement:life-story"
    item_ref = "occurrence:life-story"
    context = {
        "actor_ref": "agent:companion",
        "logical_time": "2026-09-30T00:00:00Z",
        "slices": {
            "world_life": {
                "availability": "available",
                "items": [{
                    "item_ref": item_ref,
                    "source_bindings": [{
                        "source_kind": "committed_event",
                        "authority_type": "WorldOccurrenceSettled",
                        "ref": settled_ref,
                        "source_world_revision": 4,
                        "immutable_hash": "a" * 64,
                    }],
                    "value": {"context_kind": "settled_world_occurrence"},
                }],
            },
        },
    }
    request = _qq_request().model_copy(update={"model_content_json": json.dumps(context)})

    allowed = expression.world_claim_source_refs_by_scope(
        context=context,
        request=request,
        prehistory_source_authority_context_json=json.dumps(context),
    )

    assert settled_ref not in allowed["current_world"]
    assert settled_ref not in allowed["past_world"]
    assert item_ref not in allowed["current_world"]
    assert item_ref not in allowed["past_world"]


@pytest.mark.parametrize("scope", [
    "current_world", "past_world", "shared_history", "counterpart_history",
    "stable_identity", "subjective_or_hypothetical",
])
def test_proactive_claim_wire_aliases_preserve_authored_scope_and_material(scope):
    refs = [] if scope == "subjective_or_hypothetical" else ["event:fixture:exact-source"]
    raw = {
        "timing_choice": "now", "stance": "test", "brief_rationale": "test",
        "impulse_summary": "test", "beats": [{"modality": "text", "text": "角色自己的声明。"}],
        "world_claims": [{"claim": "角色自己的声明。", "scope": scope, "exact_source_refs": refs}],
    }
    original = deepcopy(raw)
    bound = expression.bind_proactive_expression_wire(expression.normalize_expression_draft_wire(raw))
    draft = ProactiveDraft.model_validate_json(json.dumps(bound), strict=True)
    assert raw == original
    assert draft.world_claims[0].model_dump(mode="json") == {
        "claim_text": "角色自己的声明。", "scope": scope, "source_refs": refs,
    }


@pytest.mark.parametrize("scope", [
    "current_world", "past_world", "shared_history", "counterpart_history",
])
@pytest.mark.parametrize("refs_field", [{}, {"source_refs": []}])
def test_proactive_mixed_claims_cannot_hide_one_missing_source(scope, refs_field):
    raw = {
        "timing_choice": "now", "stance": "test", "brief_rationale": "test",
        "impulse_summary": "test", "beats": [{"modality": "text", "text": "两项角色声明。"}],
        "world_claims": [
            {"claim_text": "角色感受。", "scope": "subjective_or_hypothetical", "source_refs": []},
            {"claim_text": "需要来源的经历。", "scope": scope, **refs_field},
        ],
    }
    original = deepcopy(raw)
    bound = expression.bind_proactive_expression_wire(expression.normalize_expression_draft_wire(raw))
    assert raw == original
    assert bound["world_claims"] == raw["world_claims"]
    with pytest.raises(ValidationError, match="world claim scope requires matching source refs"):
        ProactiveDraft.model_validate_json(json.dumps(bound), strict=True)
