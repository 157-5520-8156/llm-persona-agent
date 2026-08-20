"""Inbound slim vs proactive ExpressionDraft: field-by-field capability map."""

from __future__ import annotations

from companion_daemon.world_v2.present_prompt import SLIM_CONSIDER_KEYS


# Decisions she can write on inbound slim. Proactive uses ExpressionDraft
# plus the same hitchhike keys as inbound slim.
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
        "appraisal_draft",
        "response_expectation",
        "response_expectation_assessment",
        "revisit",
        "media_request",
        "media_source_refs",
        "turn_posture",
        "waiting_for",
        "wait",
        "about_us",
        "why_us",
        "us_deltas",
        "we_are",
        "calling_it",
        "said_as",
        "keep_impression",
        "noticed",
        "declared_display",
    }
)

# Inbound-only aliases or reply-scene decisions. Each has a proactive native
# equivalent or a reason it cannot hitch onto a non-message turn.
INBOUND_ONLY_WITH_REASON = {
    "messages": "beats",
    "meaning_of_this": "appraisal_draft.meanings / brief_rationale",
    "my_state": "private_turn_state.inner_state_summary / impulse_summary",
    "later": "timing_choice=later + delay_seconds",
    "photo": "media_request",
    "come_back": "revisit",
    "come_back_in": "revisit.wait_seconds",
    "how_it_landed": "response_expectation_assessment; also inbound-only because it reads his reply",
    "stuck_with_me": "impulse_summary / private summary",
    "wants": "impulse_summary",
    "matters_bp": "appraisal_draft.confidence; full appraisal_draft is already on the proactive wire",
    "affect": "appraisal_draft.affect",
    "episode_id": "appraisal_draft",
    "components": "appraisal_draft",
    "resolution_summary": "appraisal_draft",
    "pressure_bp": "response_expectation.pressure_bp",
    "importance_bp": "response_expectation.importance_bp",
}


def test_inbound_hope_pair_is_now_on_proactive() -> None:
    assert "waiting_for" in INBOUND_SLIM_DECISIONS
    assert "wait" in INBOUND_SLIM_DECISIONS
    assert "waiting_for" in PROACTIVE_NATIVE
    assert "wait" in PROACTIVE_NATIVE


def test_proactive_has_inbound_relationship_and_impression_slim_keys() -> None:
    for key in (
        "us_deltas",
        "about_us",
        "why_us",
        "we_are",
        "calling_it",
        "said_as",
        "keep_impression",
        "noticed",
        "declared_display",
    ):
        assert key in INBOUND_SLIM_DECISIONS
        assert key in PROACTIVE_NATIVE


def test_remaining_inbound_keys_are_aliases_or_reply_scene() -> None:
    missing = INBOUND_SLIM_DECISIONS - PROACTIVE_NATIVE
    assert missing == frozenset(INBOUND_ONLY_WITH_REASON)
    assert "how_it_landed" in missing
    assert "interaction_act" not in INBOUND_SLIM_DECISIONS
