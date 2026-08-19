"""Gate: allowed decisions stay reachable on every expression format."""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute, TriggerMessage
from companion_daemon.world_v2.expression_decision_channel import (
    INSTALLED_DECISION_CHANNELS,
    assert_expression_decision_channel_coverage,
    formats_covering,
    required_decision_ids,
)
from companion_daemon.world_v2.expression_draft import (
    materialize_expression_draft,
    qq_expression_capabilities,
)
from companion_daemon.world_v2.present_prompt import compile_slim_interior_envelope


def _qq_request() -> ModelInput:
    return ModelInput(
        call_id="call:1",
        attempt_id="attempt:1",
        route=ModelRoute(tier="flash", reason_code="test", router_version="test.1"),
        capsule_id="a" * 64,
        trigger_ref="trigger:1",
        evaluated_world_revision=3,
        model_content_json=json.dumps(
            {"logical_time": "2026-08-19T06:00:45+00:00", "slices": {}},
            ensure_ascii=False,
        ),
        trigger_message=TriggerMessage(
            event_ref="event:observation:qq:1",
            event_payload_hash="sha256:" + "b" * 64,
            observation_ref="observation:qq:1",
            source_world_revision=3,
            actor="user:primary",
            channel="qq",
            reply_target="conversation:qq:c2c:owner",
            platform_message_id="qq-message-7788",
            text="现在能发一张吗",
        ),
    )


def test_expression_decision_channel_coverage_holds() -> None:
    assert_expression_decision_channel_coverage()


def test_media_intent_covers_all_installed_formats() -> None:
    assert "media_intent" in required_decision_ids()
    assert formats_covering("media_intent") == frozenset(
        {"reply_only_slim", "reply_only_events", "full_turn"}
    )
    assert len(INSTALLED_DECISION_CHANNELS) >= 3


def test_gate_reds_when_reply_only_silently_drops_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """Example of what the gate catches: reply_only strips photo again."""

    from companion_daemon.world_v2 import expression_decision_channel as channel
    from companion_daemon.world_v2 import present_prompt as present

    original = present.compile_slim_interior_envelope

    def broken(value, *, reply_only: bool):  # type: ignore[no-untyped-def]
        envelope = original(value, reply_only=reply_only)
        if reply_only and envelope is not None:
            head = envelope["events"][0]
            assert isinstance(head, dict)
            head["media_request"] = "none"
            head["media_source_refs"] = []
        return envelope

    monkeypatch.setattr(present, "compile_slim_interior_envelope", broken)
    monkeypatch.setattr(channel, "compile_slim_interior_envelope", broken)
    with pytest.raises(AssertionError, match="media intent was dropped|channel gaps"):
        assert_expression_decision_channel_coverage()


def test_reply_only_photo_compiles_without_silent_strip() -> None:
    envelope = compile_slim_interior_envelope(
        {"messages": ["发你"], "felt": "想分享", "photo": True},
        reply_only=True,
    )
    assert envelope is not None
    assert envelope["events"][0]["media_request"] == "consider_available_candidate"


def test_illegal_media_source_ref_still_rejected() -> None:
    """Boundary stays hard: unpinned refs fail; widening the channel does not loosen it."""

    request = _qq_request()
    with pytest.raises(ValueError, match="unpinned source ref|immutable event authority"):
        materialize_expression_draft(
            value={
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "I want to send a photo.",
                    "attended_source_refs": [],
                },
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "发你一张。"}],
                "stance": "considering",
                "brief_rationale": "A photo I cannot legally cite.",
                "confidence": 7_500,
                "world_claims": [],
                "media_request": "consider_available_candidate",
                "media_source_refs": ["event:photo-candidate:opened:missing"],
            },
            request=request,
            capabilities=qq_expression_capabilities(
                "napcat", media_request_available=True
            ),
            private_state_context_json=request.model_content_json,
        )
