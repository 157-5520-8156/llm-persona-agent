"""Proactive materialization must keep her appraisal without a JSON round-trip.

``_proposal_from_draft`` dumps ``mode="json"`` for inbound authors.  That turns
tuples into lists.  ``DecisionProposal`` is a strict FrozenModel, so
``model_validate`` on that dump rejects an equivalent sequence shape and used
to drop a legally authored proactive message.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from companion_daemon.world_v2.character_interior.inbound_appraisal_wire import (
    _decision_proposal_from_draft,
    _proposal_from_draft,
)
from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute
from companion_daemon.world_v2.expression_draft import TEXT_ONLY_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.proactive_action import (
    ProactiveDraft,
    _materialize_interior_proactive_draft,
    _proactive_appraisal_raw,
    _proactive_expression_plan_change,
)
from companion_daemon.world_v2.proposal_envelope import (
    DecisionProposal,
    ProposalEvidenceRef,
    validate_proposal_envelope,
)


SOURCE = "event:clock:ambient:1"
HASH = "sha256:" + "b" * 64


def _request() -> ModelInput:
    return ModelInput(
        call_id="call:proactive:appraisal-materialization:1",
        attempt_id="attempt:proactive:appraisal-materialization:1",
        route=ModelRoute(tier="flash", reason_code="test", router_version="test.1"),
        capsule_id="a" * 64,
        trigger_ref=SOURCE,
        evaluated_world_revision=9,
        evaluated_deliberation_revision=0,
        evaluated_ledger_sequence=12,
        trigger_evidence=(
            ProposalEvidenceRef(
                ref_id=SOURCE,
                evidence_kind="committed_world_event",
                source_world_revision=9,
                immutable_hash=HASH,
            ),
        ),
        model_content_json=json.dumps(
            {
                "logical_time": "2026-08-18T08:58:55.507262+00:00",
                "slices": {
                    "advisories": {
                        "items": [
                            {
                                "value": {
                                    "kind": "proactive_opportunity",
                                    "candidate_refs": ["ambient_presence:epoch:1"],
                                    "source_refs": [SOURCE],
                                    "candidates": [{"value": "ambient context"}],
                                }
                            }
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
    )


def _draft(**updates: object) -> ProactiveDraft:
    payload: dict[str, object] = {
        "timing_choice": "now",
        "cadence": "conversational",
        "beats": [
            {
                "modality": "text",
                "text": "对了，这个周末我打算去趟旧书市逛逛，你有兴趣一起吗？",
            }
        ],
        "stance": "warm",
        "brief_rationale": "他刚回了个简短的嗯，对话有点停在原地。",
        "impulse_summary": "想轻轻延续一下话题。",
        "confidence": 6000,
        "world_claims": [],
        "private_turn_state": {
            "inner_state_summary": "他回了个嗯，有点淡，但被在意的感觉有点暖。",
            "attended_source_refs": [SOURCE],
        },
    }
    payload.update(updates)
    return ProactiveDraft.model_validate_json(json.dumps(payload, ensure_ascii=False), strict=True)


def _materialize(draft: ProactiveDraft) -> DecisionProposal:
    return _materialize_interior_proactive_draft(
        draft=draft,
        request=_request(),
        target="user:primary",
        expression_capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES,
        grounding_outcome="not_required",
    )


def test_json_dump_of_appraisal_proposal_is_lists_and_strict_python_rejects_them() -> None:
    dumped = _proposal_from_draft(
        raw=_proactive_appraisal_raw(draft=_draft(mood="warmth")),
        request=_request(),
    )

    assert isinstance(dumped["evidence_refs"], list)
    assert isinstance(dumped["proposed_changes"], list)
    assert isinstance(dumped["appraisals"], list)
    with pytest.raises(ValidationError, match="tuple_type") as caught:
        DecisionProposal.model_validate(dumped)
    locations = {item["loc"][0] for item in caught.value.errors()}
    assert {"evidence_refs", "proposed_changes", "appraisals"} <= locations

    envelope = validate_proposal_envelope(dumped)
    typed = _decision_proposal_from_draft(
        raw=_proactive_appraisal_raw(draft=_draft(mood="warmth")),
        request=_request(),
    )
    assert isinstance(envelope, DecisionProposal)
    assert isinstance(typed.evidence_refs, tuple)
    assert typed.proposed_changes == envelope.proposed_changes


def test_mood_appraisal_branch_keeps_her_words_and_affect() -> None:
    proposal = _materialize(_draft(mood="warmth"))

    kinds = tuple(item.kind for item in proposal.proposed_changes)
    assert kinds[:2] == ("appraisal_transition", "affect_transition")
    assert kinds[-1] == "expression_plan_transition"
    assert proposal.affect_decision == "propose"
    assert proposal.affect_tendencies == ("warmth",)
    assert proposal.appraisals
    assert proposal.appraisals[0].summary == "他刚回了个简短的嗯，对话有点停在原地。"
    texts = [
        draft.get("inline_text")
        for change in proposal.proposed_changes
        if change.kind == "expression_plan_transition"
        for draft in change.payload.value()["beat_drafts"]
        if isinstance(draft, dict)
    ]
    assert texts == ["对了，这个周末我打算去趟旧书市逛逛，你有兴趣一起吗？"]
    assert all(intent.kind == "proactive_message" for intent in proposal.action_intents)
    assert len(proposal.proposed_changes) == 3
    assert _proactive_expression_plan_change(proposal).kind == "expression_plan_transition"


def test_mood_plus_explicit_appraisal_draft_keeps_the_explicit_reading() -> None:
    proposal = _materialize(
        _draft(
            mood="warmth",
            appraisal_draft={
                "appraise": True,
                "affect": "no_change",
                "brief_rationale": "被轻轻接住的感觉还在。",
                "behavior_tendency": "reach",
                "stance": "warm",
                "display_strategy": "light_invite",
                "confidence": 6400,
                "meanings": [{"meaning": "被在意", "confidence": 6400}],
                "attribution": "user",
                "severity": 4200,
            },
        )
    )

    kinds = tuple(item.kind for item in proposal.proposed_changes)
    assert "appraisal_transition" in kinds
    assert "affect_transition" not in kinds
    assert proposal.affect_decision == "no_change"
    assert proposal.appraisals[0].summary == "被轻轻接住的感觉还在。"
    assert all(intent.kind == "proactive_message" for intent in proposal.action_intents)


def test_explicit_appraisal_draft_branch_keeps_her_authored_reading() -> None:
    proposal = _materialize(
        _draft(
            appraisal_draft={
                "appraise": True,
                "affect": "no_change",
                "brief_rationale": "被轻轻接住的感觉还在。",
                "behavior_tendency": "reach",
                "stance": "warm",
                "display_strategy": "light_invite",
                "confidence": 6400,
                "meanings": [{"meaning": "被在意", "confidence": 6400}],
                "attribution": "user",
                "severity": 4200,
            }
        )
    )

    kinds = tuple(item.kind for item in proposal.proposed_changes)
    assert "appraisal_transition" in kinds
    assert "affect_transition" not in kinds
    assert kinds[-1] == "expression_plan_transition"
    assert proposal.affect_decision == "no_change"
    assert proposal.appraisals[0].summary == "被轻轻接住的感觉还在。"


def test_later_mood_appraisal_branch_keeps_her_words_and_affect() -> None:
    proposal = _materialize(
        _draft(
            mood="warmth",
            timing_choice="later",
            delay_seconds=600,
            expires_after_seconds=3600,
        )
    )

    kinds = tuple(item.kind for item in proposal.proposed_changes)
    assert kinds[:2] == ("appraisal_transition", "affect_transition")
    assert kinds[-1] == "expression_plan_transition"
    assert proposal.timing_choice == "later"
    assert proposal.affect_decision == "propose"
    assert proposal.affect_tendencies == ("warmth",)
    texts = [
        draft.get("inline_text")
        for change in proposal.proposed_changes
        if change.kind == "expression_plan_transition"
        for draft in change.payload.value()["beat_drafts"]
        if isinstance(draft, dict)
    ]
    assert texts == ["对了，这个周末我打算去趟旧书市逛逛，你有兴趣一起吗？"]
    assert proposal.action_intents
    assert all(intent.kind == "followup" for intent in proposal.action_intents)


def test_no_mood_and_no_appraisal_still_authorizes_the_message() -> None:
    proposal = _materialize(_draft())

    assert tuple(item.kind for item in proposal.proposed_changes) == (
        "expression_plan_transition",
    )
    assert proposal.affect_decision == "no_change"
    assert proposal.appraisals == ()
    assert proposal.action_intents[0].kind == "proactive_message"


def test_draft_keeps_mood_when_attention_overflows_private_turn_state_max() -> None:
    from types import SimpleNamespace

    from companion_daemon.world_v2.proactive_action import (
        _CharacterInteriorProactiveTransport,
    )

    transport = _CharacterInteriorProactiveTransport(
        character_interior=SimpleNamespace(),
        world_id="world:test",
        actor_ref="character:zhizhi",
        target="user:primary",
        expression_capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES,
    )
    refs = tuple(f"event:source:{index}" for index in range(10))
    decision = SimpleNamespace(
        summary="想轻轻说一句近况。",
        attended_source_refs=refs,
        decision={
            "contract": "character-interior-purpose-decision.1",
            "purpose": "proactive_contact",
            "payload": {
                "contract": "character-interior-proactive-contact-decision.1",
                "timing_choice": "now",
                "cadence": "conversational",
                "beats": [
                    {
                        "modality": "text",
                        "text": "对了，这个周末我打算去趟旧书市逛逛，你有兴趣一起吗？",
                    }
                ],
                "stance": "warm",
                "brief_rationale": "他刚回了个简短的嗯，对话有点停在原地。",
                "impulse_summary": "想轻轻延续一下话题。",
                "confidence": 6000,
                "world_claims": [],
                "mood": "warmth",
            },
        },
    )

    draft = transport._draft(decision=decision)

    assert draft.mood == "warmth"
    assert draft.private_turn_state is not None
    assert len(draft.private_turn_state.attended_source_refs) == 8
    assert draft.private_turn_state.attended_source_refs == refs[:8]
    assert draft.beats[0].text == "对了，这个周末我打算去趟旧书市逛逛，你有兴趣一起吗？"
