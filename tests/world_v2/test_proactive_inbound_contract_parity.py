"""Inbound slim vs proactive ExpressionDraft: field-by-field capability map."""

from __future__ import annotations

from companion_daemon.world_v2.present_prompt import SLIM_CONSIDER_KEYS


# Decisions she can write on inbound slim. Proactive uses ExpressionDraft
# plus the waiting_for/wait hitchhike restored to H21.
INBOUND_SLIM_DECISIONS = frozenset(SLIM_CONSIDER_KEYS)
PROACTIVE_NATIVE = frozenset(
    {
        "timing_choice",
        "cadence",
        "beats",
        "delay_seconds",
        "expires_after_seconds",
        "stance",
        "brief_rationale",
        "impulse_summary",
        "confidence",
        "world_claims",
        "mood",
        "appraisal_draft",
        "response_expectation",
        "response_expectation_assessment",
        "revisit",
        "media_request",
        "media_source_refs",
        "turn_posture",
        "waiting_for",
        "wait",
    }
)


def test_inbound_hope_pair_is_now_on_proactive() -> None:
    assert "waiting_for" in INBOUND_SLIM_DECISIONS
    assert "wait" in INBOUND_SLIM_DECISIONS
    assert "waiting_for" in PROACTIVE_NATIVE
    assert "wait" in PROACTIVE_NATIVE


def test_proactive_still_lacks_relationship_and_impression_slim_keys() -> None:
    missing = INBOUND_SLIM_DECISIONS - PROACTIVE_NATIVE - {
        "messages",
        "felt",
        "later",
        "photo",
        "how_it_landed",
        "come_back",
        "come_back_in",
    }
    # later ↔ timing_choice/delay_seconds; come_back ↔ revisit;
    # how_it_landed ↔ response_expectation_assessment; messages ↔ beats.
    assert "us_deltas" in missing
    assert "about_us" in missing
    assert "why_us" in missing
    assert "we_are" in missing
    assert "keep_impression" in missing
    assert "noticed" in missing
    assert "declared_display" in missing
