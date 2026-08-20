#!/usr/bin/env python3
"""Clone proofs: media send-window facts meet text expiry lanes (and reverse).

Never writes ``data/``. Output: ``output/media-text-cross-lane/``.

Three cross scenarios (deterministic projection + optional pinned overlay words):

1. hope + lapsed send window — text lane sees media no longer auto-sending
2. hope + delivery on ledger — text lane sees photo already reached him
3. media selection — sees queued later text from text lane

Usage::

    .venv/bin/python scripts/prove_media_text_cross_lane.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))


from companion_daemon.world_v2.media_conversation_window import (
    media_cross_lane_timing_clause,
)
from companion_daemon.world_v2.media_selection_occasion import (
    compile_text_cross_lane_facts,
)
from companion_daemon.world_v2.response_expectation_view import (
    expired_hope_advisory_value,
    living_hope_hitch_clause,
)

OUTPUT = (REPO / "output" / "media-text-crosslane").resolve()
NOW = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)
CANDIDATE = "candidate:crosslane"

HER_WORDS = {
    "photo_promise": "那我挑一张发你，等我一下",
    "hope_after_share": "你觉得这张怎么样",
    "later_photo": "照片我晚点发你",
}


def _projection(
    *,
    decided_at: datetime,
    delivered: bool = False,
    candidate_expired: bool = False,
) -> SimpleNamespace:
    expires = NOW - timedelta(minutes=5) if candidate_expired else decided_at + timedelta(hours=48)
    return SimpleNamespace(
        logical_time=NOW,
        world_id="world:companion-v2:qq-c2c:geoff",
        world_revision=100,
        deliberation_revision=1,
        ledger_sequence=1000,
        committed_world_event_refs=(
            SimpleNamespace(
                event_id="event:proposal:1",
                logical_time=decided_at,
                event_type="MediaSelectionProposalRecorded",
                world_revision=90,
                payload_hash="a" * 64,
            ),
        ),
        proposal_revisions=(
            SimpleNamespace(
                candidate_id=CANDIDATE,
                proposal_event_ref="event:proposal:1",
                decided_at=decided_at,
            ),
        ),
        media_opportunities=(
            SimpleNamespace(opportunity_id="opp:1", candidate_id=CANDIDATE),
        ),
        media_plans=(SimpleNamespace(plan_id="plan:1", opportunity_id="opp:1"),),
        media_deliveries=(SimpleNamespace(plan_id="plan:1"),) if delivered else (),
        actions=(),
        photo_candidates=(
            SimpleNamespace(
                candidate_id=CANDIDATE,
                status="expired" if candidate_expired else "generated",
                opened_at=decided_at,
                expires_at=expires,
                source_events=(SimpleNamespace(event_ref="event:settled:1"),),
                source_event_refs=("event:settled:1",),
            ),
        ),
        message_observations=(),
        execution_receipts=(),
        expression_plan_manifests=(),
        response_expectation_assessments=(),
        trigger_processes=(),
    )


def scenario_hope_lapsed_send() -> dict[str, Any]:
    projection = _projection(decided_at=NOW - timedelta(minutes=42))
    hope = HER_WORDS["hope_after_share"]
    advisory = expired_hope_advisory_value(
        hoped_response=hope,
        seconds_since_he_last_spoke=600,
        spoken_since_declared=False,
        projection=projection,
    )
    return {
        "id": "hope_lapsed_send_window",
        "her_words": hope,
        "media_clause": media_cross_lane_timing_clause(projection),
        "advisory_excerpt": advisory[:400],
        "pass": "no longer auto-sending" in advisory,
    }


def scenario_delivery_visible() -> dict[str, Any]:
    projection = _projection(decided_at=NOW - timedelta(minutes=8), delivered=True)
    hope = HER_WORDS["hope_after_share"]
    hitch = living_hope_hitch_clause(
        hoped_response=hope,
        seconds_since_he_last_spoke=180,
        projection=projection,
    )
    return {
        "id": "delivery_visible_to_hope_lane",
        "her_words": hope,
        "hitch_excerpt": hitch[:400],
        "pass": "already reached him" in hitch,
    }


def scenario_media_sees_queued_later() -> dict[str, Any]:
    written = NOW - timedelta(minutes=15)
    send_at = NOW + timedelta(minutes=5)
    text = HER_WORDS["later_photo"]
    projection = SimpleNamespace(
        logical_time=NOW,
        committed_world_event_refs=(
            SimpleNamespace(
                event_id="event:beat:later",
                world_revision=50,
                payload_hash="b" * 64,
            ),
        ),
        actions=(
            SimpleNamespace(
                action_id="action:later:photo",
                kind="followup",
                state="authorized",
                logical_time=written,
                not_before=send_at,
                expression_plan_id="plan:later:photo",
                expression_beat_id="beat:later:photo",
                payload_ref="payload:later:photo",
            ),
        ),
        expression_beats=(
            SimpleNamespace(
                beat_id="beat:later:photo",
                event_ref="event:beat:later",
                payload_ref="payload:later:photo",
            ),
        ),
        stored_message_payloads=(
            SimpleNamespace(payload_ref="payload:later:photo", text=text),
        ),
        trigger_processes=(),
        message_observations=(),
        execution_receipts=(),
        expression_plan_manifests=(),
        response_expectation_assessments=(),
    )
    facts = compile_text_cross_lane_facts(projection, logical_time=NOW)
    queued = [item for item in facts if item.get("kind") == "text_lane_queued_message"]
    return {
        "id": "media_sees_queued_later",
        "her_words": text,
        "queued_facts": queued,
        "pass": bool(queued) and text in str(queued[0].get("text")),
    }


def scenario_candidate_expired() -> dict[str, Any]:
    projection = _projection(
        decided_at=NOW - timedelta(hours=2),
        candidate_expired=True,
    )
    clause = media_cross_lane_timing_clause(projection)
    return {
        "id": "candidate_expired_visible",
        "media_clause": clause[:400],
        "pass": "expired" in clause and "no longer choosable" in clause,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = [
        scenario_hope_lapsed_send(),
        scenario_delivery_visible(),
        scenario_media_sees_queued_later(),
        scenario_candidate_expired(),
    ]
    report = {
        "accepted": all(item.get("pass") for item in results),
        "scenarios": results,
    }
    (OUTPUT / "REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["accepted"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
