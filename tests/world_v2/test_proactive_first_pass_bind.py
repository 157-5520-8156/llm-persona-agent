from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.character_interior.structured_role import (
    _validate_proactive_payload,
)
from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute
from companion_daemon.world_v2.expression_draft import (
    bind_proactive_expression_wire,
    bind_proactive_world_claims,
    normalize_expression_draft_wire,
    TEXT_ONLY_EXPRESSION_CAPABILITIES,
)
from companion_daemon.world_v2.proactive_action import (
    ProactiveDraft,
    _materialize_interior_proactive_draft,
    _validate_proactive_grounding,
)
from companion_daemon.world_v2.proposal_envelope import ProposalEvidenceRef


def _payload(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "timing_choice": "now",
        "cadence": "conversational",
        "beats": [{"modality": "text", "text": "想到你了。"}],
        "stance": "warm",
        "brief_rationale": "I want to say this now.",
        "impulse_summary": "The counterpart crossed my mind.",
        "confidence": 6500,
        "world_claims": [],
    }
    value.update(updates)
    return value


def _validate(payload: dict[str, object]) -> None:
    _validate_proactive_payload(payload, frozenset())


def _bind(payload: dict[str, object]) -> dict[str, object]:
    return bind_proactive_expression_wire(normalize_expression_draft_wire(payload))


def test_now_plus_yield_binds_to_continue_and_accepts() -> None:
    payload = _payload(turn_posture="yield")

    bound = bind_proactive_expression_wire(payload)
    _validate(payload)

    assert bound["timing_choice"] == "now"
    assert bound["turn_posture"] == "continue"
    assert bound["delay_seconds"] is None


def test_now_plus_due_window_drops_the_window() -> None:
    bound = bind_proactive_expression_wire(
        _payload(delay_seconds=30, expires_after_seconds=90)
    )
    _validate(_payload(delay_seconds=30, expires_after_seconds=90))

    assert bound["timing_choice"] == "now"
    assert bound["delay_seconds"] is None
    assert bound["expires_after_seconds"] is None


def test_silent_with_visible_beats_keeps_the_words() -> None:
    bound = bind_proactive_expression_wire(_payload(timing_choice="silent"))
    _validate(_payload(timing_choice="silent"))

    assert bound["timing_choice"] == "now"
    assert bound["beats"] == [{"modality": "text", "text": "想到你了。"}]


def test_later_without_window_gets_cadence_defaults() -> None:
    bound = bind_proactive_expression_wire(
        _payload(timing_choice="later", delay_seconds=None, expires_after_seconds=None)
    )
    _validate(
        _payload(timing_choice="later", delay_seconds=None, expires_after_seconds=None)
    )

    assert bound["timing_choice"] == "later"
    assert bound["delay_seconds"] == 1800
    assert bound["expires_after_seconds"] == 7200


def test_empty_beats_collapse_to_silent() -> None:
    bound = bind_proactive_expression_wire(_payload(beats=[]))
    _validate(_payload(beats=[]))

    assert bound["timing_choice"] == "silent"
    assert bound["beats"] == []


def test_beat_option_aliases_and_unknown_keys_are_dropped() -> None:
    bound = bind_proactive_expression_wire(
        _payload(
            beats=[
                {
                    "modality": "text",
                    "text": "Hey, just checking in.",
                    "cadence": "hesitant",
                    "reaction_option_id": None,
                    "sticker_option_id": None,
                }
            ]
        )
    )
    _validate(
        _payload(
            beats=[
                {
                    "modality": "text",
                    "text": "Hey, just checking in.",
                    "cadence": "hesitant",
                    "reaction_option_id": None,
                }
            ]
        )
    )

    assert bound["beats"] == [{"modality": "text", "text": "Hey, just checking in."}]


def test_claim_alias_is_renamed_without_changing_the_words() -> None:
    payload = _payload(
        world_claims=[
            {
                "claim": "I found a rare poetry book",
                "scope": "subjective_or_hypothetical",
            }
        ]
    )
    bound = _bind(payload)
    _validate(payload)

    assert bound["world_claims"] == [
        {
            "claim_text": "I found a rare poetry book",
            "scope": "subjective_or_hypothetical",
        }
    ]


def test_empty_object_stays_invalid() -> None:
    with pytest.raises(ValueError):
        _validate({})


def _request(context: dict[str, object]) -> ModelInput:
    return ModelInput(
        call_id="call:proactive-bind",
        attempt_id="attempt:proactive-bind",
        route=ModelRoute(tier="flash", reason_code="fixture", router_version="fixture.1"),
        capsule_id="a" * 64,
        trigger_ref="event:life-development:activated:rain",
        evaluated_world_revision=3,
        evaluated_deliberation_revision=2,
        evaluated_ledger_sequence=9,
        model_content_json=json.dumps(context, ensure_ascii=False),
    )


def _context() -> dict[str, object]:
    rain = "event:life-development:activated:rain"
    library = (
        "dialogue:expression:plan:expression:library-beat:1"
    )
    return {
        "actor_ref": "agent:companion",
        "slices": {
            "recent_experiences": {
                "availability": "available",
                "items": [{"source_ref": rain, "value": {"source_refs": [rain]}}],
            },
            "recent_dialogue": {
                "availability": "available",
                "items": [
                    {
                        "source_ref": library,
                        "value": {
                            "speaker": "companion",
                            "speaker_ref": "agent:companion",
                            "source_refs": [library],
                        },
                    }
                ],
            },
        },
    }


def _draft_with_claims(*claims: dict[str, object]) -> ProactiveDraft:
    return ProactiveDraft.model_validate_json(
        json.dumps(
            {
                **_payload(),
                "private_turn_state": {
                    "inner_state_summary": "想轻轻说一句近况。",
                    "attended_source_refs": [
                        "event:life-development:activated:rain",
                        "dialogue:expression:plan:expression:library-beat:1",
                    ],
                },
                "world_claims": list(claims),
            },
            ensure_ascii=False,
        ),
        strict=True,
    )


def test_mismatched_dialogue_claim_is_rebound_instead_of_silencing() -> None:
    rain = "event:life-development:activated:rain"
    library = "dialogue:expression:plan:expression:library-beat:1"
    draft = _draft_with_claims(
        {
            "claim_text": "昨晚的雨打断了散步",
            "scope": "past_world",
            "source_refs": [rain],
        },
        {
            "claim_text": "此刻我在图书馆",
            "scope": "current_world",
            "source_refs": [library],
        },
    )
    request = _request(_context())

    bound = bind_proactive_world_claims(draft=draft, request=request)
    kept = _validate_proactive_grounding(draft=draft, request=request)

    assert [claim.scope for claim in bound.world_claims] == [
        "past_world",
        "shared_history",
    ]
    assert kept.world_claims == bound.world_claims
    assert kept.beats[0].text == "想到你了。"


def test_subjective_or_hypothetical_claim_is_kept() -> None:
    draft = _draft_with_claims(
        {
            "claim_text": "好像刚散过步",
            "scope": "subjective_or_hypothetical",
        }
    )
    request = _request(_context())

    bound = bind_proactive_world_claims(draft=draft, request=request)
    kept = _validate_proactive_grounding(draft=draft, request=request)

    assert [claim.scope for claim in bound.world_claims] == ["subjective_or_hypothetical"]
    assert bound.world_claims[0].claim_text == "好像刚散过步"
    assert kept.world_claims == bound.world_claims
    assert kept.beats[0].text == "想到你了。"


def test_subjective_claim_does_not_fail_a_mixed_unsupported_set() -> None:
    draft = _draft_with_claims(
        {
            "claim_text": "好像刚散过步",
            "scope": "subjective_or_hypothetical",
        },
        {
            "claim_text": "对方之前说去成都看熊猫",
            "scope": "counterpart_history",
            "source_refs": ["event:user:chengdu:not-in-context"],
        },
    )

    bound = bind_proactive_world_claims(draft=draft, request=_request(_context()))

    assert [claim.scope for claim in bound.world_claims] == ["subjective_or_hypothetical"]
    assert bound.beats[0].text == "想到你了。"


def test_unsupported_only_claim_still_rejects() -> None:
    draft = _draft_with_claims(
        {
            "claim_text": "对方之前说去成都看熊猫",
            "scope": "counterpart_history",
            "source_refs": ["event:user:chengdu:not-in-context"],
        }
    )

    with pytest.raises(ValueError, match="outside its semantic source lane"):
        bind_proactive_world_claims(draft=draft, request=_request(_context()))


def test_companion_own_speech_binds_as_shared_history() -> None:
    library = "dialogue:expression:plan:expression:library-beat:1"
    draft = _draft_with_claims(
        {
            "claim_text": "我说过在图书馆、阴天像要下雨",
            "scope": "shared_history",
            "source_refs": [library],
        },
        {
            "claim_text": "我说过晚上会发书店照片",
            "scope": "current_world",
            "source_refs": [library],
        },
    )
    request = _request(_context())

    bound = bind_proactive_world_claims(draft=draft, request=request)
    kept = _validate_proactive_grounding(draft=draft, request=request)

    assert [claim.scope for claim in bound.world_claims] == [
        "shared_history",
        "shared_history",
    ]
    assert all(claim.source_refs == (library,) for claim in bound.world_claims)
    assert kept.beats[0].text == "想到你了。"


def test_proactive_observation_trigger_binds_without_trigger_message() -> None:
    trig = (
        "event:trigger:observation:platform:qq:qq:2759284998:"
        "qq-coalesced:9b0c93fee624208d21e91a78af8f914ec7362474c4edbe403fb9d58fb2b6a612"
    )
    context = {
        "actor_ref": "agent:companion",
        "slices": {
            "recent_dialogue": {
                "availability": "available",
                "items": [
                    {
                        "item_ref": "dialogue:observation:observation:qq:haodi",
                        "source_ref": "dialogue:observation:observation:qq:haodi",
                        "value": {
                            "speaker": "counterpart",
                            "speaker_ref": "user:geoff",
                            "text": "好滴",
                            "source_refs": [trig, "dialogue:observation:observation:qq:haodi"],
                        },
                    }
                ],
            }
        },
    }
    request = ModelInput(
        call_id="call:proactive-obs",
        attempt_id="attempt:proactive-obs",
        route=ModelRoute(tier="flash", reason_code="fixture", router_version="fixture.1"),
        capsule_id="a" * 64,
        trigger_ref=trig,
        evaluated_world_revision=3,
        evaluated_deliberation_revision=2,
        evaluated_ledger_sequence=9,
        model_content_json=json.dumps(context, ensure_ascii=False),
        trigger_evidence=(
            ProposalEvidenceRef(
                ref_id=trig,
                evidence_kind="committed_world_event",
                source_world_revision=2,
                immutable_hash="sha256:" + "7" * 64,
            ),
        ),
    )
    draft = _draft_with_claims(
        {
            "claim_text": "他应了句好滴",
            "scope": "current_world",
            "source_refs": [trig],
        }
    )

    bound = bind_proactive_world_claims(draft=draft, request=request)

    assert [claim.scope for claim in bound.world_claims] == ["counterpart_history"]
    assert bound.world_claims[0].source_refs == (trig,)


def test_waiting_for_and_wait_compile_the_same_hope_as_inbound() -> None:
    bound = _bind(_payload(waiting_for="你那边下雨了没", wait=90))
    _validate(_payload(waiting_for="你那边下雨了没", wait=90))

    expectation = bound["response_expectation"]
    assert expectation["hoped_response"] == "你那边下雨了没"
    assert expectation["wait_seconds"] == 90
    assert expectation["expires_after_seconds"] == 150


def test_waiting_for_without_wait_does_not_compile_a_hope() -> None:
    bound = _bind(_payload(waiting_for="你那边下雨了没"))
    _validate(_payload(waiting_for="你那边下雨了没"))

    assert bound.get("response_expectation") is None


def test_wait_without_waiting_for_is_a_visible_failure() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_WAIT_PAIR_INCOMPLETE

    with pytest.raises(ValueError, match=SLIM_WAIT_PAIR_INCOMPLETE):
        _bind(_payload(wait=90))


def test_us_deltas_hitchhike_onto_private_state_and_appraisal() -> None:
    payload = _payload(
        about_us="被惦记的感觉",
        why_us="我自己先开口了",
        us_deltas={"closeness_bp": 80, "trust_bp": 40},
        private_turn_state={
            "inner_state_summary": "想靠近一点",
            "attended_source_refs": [],
        },
    )
    bound = _bind(payload)
    _validate(
        {
            key: value
            for key, value in payload.items()
            if key != "private_turn_state"
        }
    )

    state = bound["private_turn_state"]
    assert state["about_us"] == "被惦记的感觉"
    assert state["why_us"] == "我自己先开口了"
    signal = bound["appraisal_draft"]["relationship_signal"]
    assert signal["signal_code"] == "被惦记的感觉"
    assert signal["suggested_deltas"]["closeness_bp"] == 80
    assert signal["suggested_deltas"]["trust_bp"] == 40
    assert signal["suggested_deltas"]["respect_bp"] == 0


def test_us_deltas_without_about_us_is_a_visible_failure() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE

    with pytest.raises(ValueError, match=SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE):
        _bind(_payload(us_deltas={"closeness_bp": 80}))
    with pytest.raises(ValueError, match=SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE):
        _validate(_payload(us_deltas={"closeness_bp": 80}))


def test_we_are_without_said_as_is_a_visible_failure() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_COMMITMENT_TRIPLET_INCOMPLETE

    with pytest.raises(ValueError, match=SLIM_COMMITMENT_TRIPLET_INCOMPLETE):
        _bind(_payload(we_are="friend", calling_it="朋友"))


def test_invalid_declared_display_is_a_visible_failure() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_DECLARED_DISPLAY_INVALID

    with pytest.raises(ValueError, match=SLIM_DECLARED_DISPLAY_INVALID):
        _bind(_payload(declared_display="nude"))


def test_keep_impression_and_noticed_hitchhike_onto_private_state() -> None:
    bound = _bind(
        _payload(
            noticed="窗外开始下雨了",
            keep_impression=True,
            private_turn_state={
                "inner_state_summary": "雨点打在玻璃上",
                "attended_source_refs": [],
            },
        )
    )
    _validate(_payload(noticed="窗外开始下雨了", keep_impression=True))

    state = bound["private_turn_state"]
    assert state["noticed"] == "窗外开始下雨了"
    assert state["keep_impression"] is True
    assert bound["appraisal_draft"]["appraise"] is True


def test_we_are_triplet_hitchhikes_commitment() -> None:
    bound = _bind(
        _payload(
            we_are="friend",
            calling_it="朋友",
            said_as="我们现在就是朋友吧",
            private_turn_state={
                "inner_state_summary": "想把话说清楚",
                "attended_source_refs": [],
            },
        )
    )
    _validate(
        _payload(we_are="friend", calling_it="朋友", said_as="我们现在就是朋友吧")
    )

    state = bound["private_turn_state"]
    assert state["we_are"] == "friend"
    assert state["said_as"] == "我们现在就是朋友吧"
    commitment = bound["appraisal_draft"]["relationship_commitment"]
    assert commitment["target_stage"] == "friend"
    assert commitment["visible_text_span"] == "我们现在就是朋友吧"


def test_we_are_on_silence_is_a_visible_failure() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_COMMITMENT_REQUIRES_SPEECH

    with pytest.raises(ValueError, match=SLIM_COMMITMENT_REQUIRES_SPEECH):
        _bind(
            _payload(
                timing_choice="silent",
                beats=[],
                we_are="friend",
                calling_it="朋友",
                said_as="我们现在就是朋友吧",
            )
        )


def test_hitchhiked_us_deltas_materialize_a_relationship_signal() -> None:
    rain = "event:life-development:activated:rain"
    bound = _bind(
        _payload(
            about_us="被惦记的感觉",
            why_us="我自己先开口了",
            us_deltas={"closeness_bp": 80, "trust_bp": 40},
            private_turn_state={
                "inner_state_summary": "想靠近一点",
                "attended_source_refs": [rain],
            },
        )
    )
    draft = ProactiveDraft.model_validate_json(json.dumps(bound, ensure_ascii=False), strict=True)
    context = _context()
    context["logical_time"] = "2026-08-18T08:58:55.507262+00:00"
    context["slices"]["advisories"] = {
        "availability": "available",
        "items": [
            {
                "value": {
                    "kind": "proactive_opportunity",
                    "candidate_refs": ["ambient_presence:epoch:1"],
                    "source_refs": [rain],
                    "candidates": [{"value": "ambient context"}],
                }
            }
        ],
    }
    context["inner_life_snapshot"] = {
        "materials": {
            "relationship": [{"subject_ref": "user:primary", "stage": "friend"}]
        }
    }
    request = ModelInput(
        call_id="call:proactive-bind",
        attempt_id="attempt:proactive-bind",
        route=ModelRoute(tier="flash", reason_code="fixture", router_version="fixture.1"),
        capsule_id="a" * 64,
        trigger_ref=rain,
        evaluated_world_revision=3,
        evaluated_deliberation_revision=2,
        evaluated_ledger_sequence=9,
        trigger_evidence=(
            ProposalEvidenceRef(
                ref_id=rain,
                evidence_kind="committed_world_event",
                source_world_revision=3,
                immutable_hash="sha256:" + "b" * 64,
            ),
        ),
        model_content_json=json.dumps(context, ensure_ascii=False),
    )

    proposal = _materialize_interior_proactive_draft(
        draft=draft,
        request=request,
        target="user:primary",
        expression_capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES,
        grounding_outcome="not_required",
    )

    signals = [
        item.payload.value()
        for item in proposal.proposed_changes
        if item.kind == "relationship_signal"
    ]
    assert signals
    assert signals[0]["subject_ref"] == "user:primary"
    assert signals[0]["suggested_deltas"]["closeness_bp"] == 80
    assert proposal.private_turn_state is not None
    assert proposal.private_turn_state.about_us == "被惦记的感觉"

    from companion_daemon.world_v2.production_proposal_grammar import (
        production_proposal_grammar,
    )

    production_proposal_grammar("proactive").validate(proposal)
