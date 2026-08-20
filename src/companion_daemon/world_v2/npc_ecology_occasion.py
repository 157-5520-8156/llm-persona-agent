"""Recorded NPC decision occasions and weekly actor-call caps.

Randomness here only decides whether an ambient wake opens a new NPC decision
opportunity and which eligible actor hash-selects into that slot. It does not
choose NPC behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json

from .weighted_table import inject_nothing_mass, pick_weighted_token

NPC_DECISION_OPPORTUNITY_REF = "npc-ecology:decision-opportunity"
NPC_DECISION_NOTHING_REF = "nothing:npc-ecology-decision"
# ~16% on ambient wakes → ~6–7 actor opportunities/week at ~6 wakes/day.
NPC_DECISION_OPPORTUNITY_MASS_BP = 1_600
NPC_WEEKLY_ACTOR_DECISION_CAP = 8
NPC_DECISION_WINDOW_DAYS = 7


def draw_npc_decision_occasion(
    *,
    wake_event_ref: str,
    opportunity_mass_bp: int = NPC_DECISION_OPPORTUNITY_MASS_BP,
) -> bool:
    """Return whether this ambient wake opens a new NPC actor decision occasion."""

    if opportunity_mass_bp <= 0:
        return False
    weights = inject_nothing_mass(
        {NPC_DECISION_OPPORTUNITY_REF: int(opportunity_mass_bp)},
        nothing_ref=NPC_DECISION_NOTHING_REF,
    )
    token = pick_weighted_token(
        weights,
        {
            "lane": "npc_ecology_decision_occasion",
            "wake_event_ref": wake_event_ref,
            "opportunity_mass_bp": int(opportunity_mass_bp),
        },
    )
    return token == NPC_DECISION_OPPORTUNITY_REF


def count_npc_actor_model_calls(
    *,
    projection: object,
    ledger: object,
    logical_time: datetime,
    window_days: int = NPC_DECISION_WINDOW_DAYS,
) -> int:
    """Count distinct NPC actor model attempts in the rolling logical-time window."""

    window_start = logical_time - timedelta(days=window_days)
    seen_attempt_ids: set[str] = set()
    for ref in getattr(projection, "committed_world_event_refs", ()):
        if ref.logical_time < window_start or ref.logical_time > logical_time:
            continue
        if ref.event_type != "ModelResultRecorded":
            continue
        lookup = getattr(ledger, "lookup_event_commit", None)
        if lookup is None:
            continue
        located = lookup(ref.event_id)
        if located is None:
            continue
        payload = located[0].payload()
        audit_json = payload.get("audit_json")
        if not isinstance(audit_json, str) or "npc_ecology_actor" not in audit_json:
            continue
        attempt_id = payload.get("attempt_id")
        if isinstance(attempt_id, str) and attempt_id:
            seen_attempt_ids.add(attempt_id)
            continue
        try:
            audit = json.loads(audit_json)
        except json.JSONDecodeError:
            continue
        route = audit.get("route") if isinstance(audit, dict) else None
        if isinstance(route, dict) and route.get("reason_code") == "npc_ecology_actor":
            seen_attempt_ids.add(str(payload.get("model_call_id") or ref.event_id))
    return len(seen_attempt_ids)


def npc_weekly_actor_cap_exceeded(
    *,
    projection: object,
    ledger: object,
    logical_time: datetime,
    weekly_cap: int = NPC_WEEKLY_ACTOR_DECISION_CAP,
) -> bool:
    if weekly_cap <= 0:
        return True
    return (
        count_npc_actor_model_calls(
            projection=projection,
            ledger=ledger,
            logical_time=logical_time,
        )
        >= weekly_cap
    )


__all__ = [
    "NPC_DECISION_NOTHING_REF",
    "NPC_DECISION_OPPORTUNITY_MASS_BP",
    "NPC_DECISION_OPPORTUNITY_REF",
    "NPC_DECISION_WINDOW_DAYS",
    "NPC_WEEKLY_ACTOR_DECISION_CAP",
    "count_npc_actor_model_calls",
    "draw_npc_decision_occasion",
    "npc_weekly_actor_cap_exceeded",
]
