"""A due stale later may become a consider without the host rewriting her words."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import json

from companion_daemon.world_v2.later_expression_freshness import (
    LATER_REFRESH_OPPORTUNITY_CONTEXT,
    later_refresh_opportunity_context,
    later_refresh_source_binds_head,
)
from companion_daemon.world_v2.proactive_action import (
    ProactiveOpportunity,
    _proactive_opportunity_context,
    _proactive_source_frame,
    _proactive_source_kind,
)
from companion_daemon.world_v2.proposal_envelope import (
    ProactiveExpressionPlanSourceBindingV2,
    ProactiveExpressionSourceBinding,
    ProactiveOpportunityDecision,
)


NOW = datetime(2026, 8, 19, 1, 54, tzinfo=UTC)
HASH = "b" * 64
FROZEN = "早～外面好像要下雨了，你那边呢"


def _opportunity() -> ProactiveOpportunity:
    return ProactiveOpportunity(
        source_kind="later_expression_refresh",
        source_id="action:later:1",
        source_event_ref="event:beat:later",
        source_event_hash=HASH,
        source_world_revision=9,
        trace_id="trace:later-refresh",
        correlation_id="correlation:later-refresh",
        created_at=NOW,
        consideration_id="consideration:later-refresh:1",
        scheduled_for=NOW,
    )


def test_envelope_literals_accept_later_expression_refresh() -> None:
    hashed = "sha256:" + HASH
    ProactiveOpportunityDecision(
        source_kind="later_expression_refresh",
        source_event_ref="event:beat:later",
        source_payload_hash=hashed,
        source_world_revision=9,
        disposition="engage_now",
        decision_origin="model",
    )
    ProactiveExpressionSourceBinding(
        source_kind="later_expression_refresh",
        source_event_ref="event:beat:later",
        source_payload_hash=hashed,
        source_world_revision=9,
        response_payload_hash=hashed,
        target_ref="user:primary",
    )
    ProactiveExpressionPlanSourceBindingV2(
        source_kind="later_expression_refresh",
        source_event_ref="event:beat:later",
        source_payload_hash=hashed,
        source_world_revision=9,
        plan_id="plan:later-refresh:1",
        beat_payload_hashes=[hashed],
        target_ref="user:primary",
    )


def test_opportunity_context_is_eligibility_only() -> None:
    context = _proactive_opportunity_context(
        opportunity=_opportunity(),
        event=None,
        head=None,
        projection=None,
    )
    assert context == later_refresh_opportunity_context()
    assert context == LATER_REFRESH_OPPORTUNITY_CONTEXT
    assert FROZEN not in context
    assert "she still decides" in context
    assert "repeat" not in context.lower()


def test_source_frame_promotes_a_later_refresh_advisory() -> None:
    context = later_refresh_opportunity_context()
    payload = json.dumps(
        {
            "slices": {
                "advisories": {
                    "items": [
                        {
                            "value": {
                                "kind": "proactive_opportunity",
                                "candidate_refs": ["later_expression_refresh:action:later:1"],
                                "source_refs": ["event:beat:later"],
                                "candidates": [{"value": context}],
                            }
                        }
                    ]
                }
            }
        }
    )
    frame = _proactive_source_frame(payload)
    assert frame is not None
    assert frame["source_kind"] == "later_expression_refresh"
    assert _proactive_source_kind(payload) == "later_expression_refresh"
    assert FROZEN not in json.dumps(frame, ensure_ascii=False)


def test_source_binds_the_due_stale_later_without_reading_the_body() -> None:
    event = SimpleNamespace(
        event_type="ExpressionBeatAuthorized",
        event_id="event:beat:later",
    )
    written = datetime(2026, 8, 19, 0, 53, tzinfo=UTC)
    later = SimpleNamespace(
        action_id="action:later:1",
        kind="followup",
        state="authorized",
        logical_time=written,
        not_before=NOW,
        expression_plan_id="plan:later:1",
        expression_beat_id="beat:later:1",
        payload_ref="payload:later:1",
    )
    spoken = SimpleNamespace(
        action_id="action:now:1",
        kind="proactive_message",
        state="delivered",
        logical_time=datetime(2026, 8, 19, 1, 9, tzinfo=UTC),
        expression_plan_id="plan:now:1",
        expression_beat_id="beat:now:1",
    )
    projection = SimpleNamespace(
        logical_time=NOW,
        actions=(later, spoken),
        expression_beats=(
            SimpleNamespace(
                beat_id="beat:later:1",
                event_ref="event:beat:later",
                payload_ref="payload:later:1",
            ),
        ),
        committed_world_event_refs=(),
        trigger_processes=(),
        stored_message_payloads=(
            SimpleNamespace(payload_ref="payload:later:1", text=FROZEN),
        ),
    )
    assert later_refresh_source_binds_head(
        projection=projection, event=event, opportunity=_opportunity()
    )
    dumped = json.dumps(later.payload_ref)
    assert FROZEN not in dumped
