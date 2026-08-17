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
)
from companion_daemon.world_v2.proactive_action import (
    ProactiveDraft,
    _validate_proactive_grounding,
)


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
