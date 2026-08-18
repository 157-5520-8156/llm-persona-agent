"""A living private impression may become a consider source without leaking its body."""

from __future__ import annotations

from datetime import UTC, datetime
import json

from types import SimpleNamespace

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
from companion_daemon.world_v2.social_initiative import (
    PRIVATE_IMPRESSION_OPPORTUNITY_CONTEXT,
    private_impression_opportunity_context,
    private_impression_source_binds_head,
)


NOW = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
HASH = "b" * 64
SECRET = "把路灯梧桐叶记成了猫猫"


def _opportunity() -> ProactiveOpportunity:
    return ProactiveOpportunity(
        source_kind="private_impression",
        source_id="impression:living",
        source_event_ref="event:private-impression:accepted:1",
        source_event_hash=HASH,
        source_world_revision=9,
        trace_id="trace:private-impression",
        correlation_id="correlation:private-impression",
        created_at=NOW,
        consideration_id="consideration:private-impression:living",
        scheduled_for=NOW,
    )


def test_envelope_literals_accept_private_impression_so_speak_does_not_fail_closed() -> None:
    hashed = "sha256:" + HASH
    ProactiveOpportunityDecision(
        source_kind="private_impression",
        source_event_ref="event:private-impression:accepted:1",
        source_payload_hash=hashed,
        source_world_revision=9,
        disposition="engage_now",
        decision_origin="model",
    )
    ProactiveExpressionSourceBinding(
        source_kind="private_impression",
        source_event_ref="event:private-impression:accepted:1",
        source_payload_hash=hashed,
        source_world_revision=9,
        response_payload_hash=hashed,
        target_ref="user:primary",
    )
    ProactiveExpressionPlanSourceBindingV2(
        source_kind="private_impression",
        source_event_ref="event:private-impression:accepted:1",
        source_payload_hash=hashed,
        source_world_revision=9,
        plan_id="plan:proactive:1",
        beat_payload_hashes=[hashed],
        target_ref="user:primary",
    )


def test_opportunity_context_is_eligibility_only_and_omits_the_withheld_body() -> None:
    context = _proactive_opportunity_context(
        opportunity=_opportunity(),
        event=None,
        head=None,
        projection=None,
    )
    assert context == private_impression_opportunity_context()
    assert context == PRIVATE_IMPRESSION_OPPORTUNITY_CONTEXT
    assert SECRET not in context
    assert "reflection_summary" not in context
    assert "she still decides" in context


def test_source_frame_promotes_a_private_impression_advisory() -> None:
    context = private_impression_opportunity_context()
    payload = json.dumps(
        {
            "slices": {
                "advisories": {
                    "items": [
                        {
                            "value": {
                                "kind": "proactive_opportunity",
                                "candidate_refs": ["private_impression:impression:living"],
                                "source_refs": ["event:private-impression:accepted:1"],
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
    assert frame["source_kind"] == "private_impression"
    assert _proactive_source_kind(payload) == "private_impression"
    assert SECRET not in json.dumps(frame, ensure_ascii=False)


def test_source_binds_the_accepted_private_impression_head_without_reading_the_body() -> None:
    event = SimpleNamespace(
        event_type="PrivateImpressionAccepted",
        event_id="event:private-impression:accepted:1",
    )
    projection = SimpleNamespace(
        private_impressions=(
            SimpleNamespace(
                impression_id="impression:living",
                status="active",
                reflection_summary=SECRET,
                origin=SimpleNamespace(
                    accepted_event_ref="event:private-impression:accepted:1"
                ),
            ),
        )
    )
    assert private_impression_source_binds_head(
        projection=projection,
        event=event,
        opportunity=_opportunity(),
    )
    context = _proactive_opportunity_context(
        opportunity=_opportunity(),
        event=event,
        head=event,
        projection=projection,
    )
    assert SECRET not in context
