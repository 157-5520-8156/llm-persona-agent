#!/usr/bin/env python3
"""Read-only production audit of the Life Ecology chain.

Never writes ``data/``. Uses ``sqlite3.connect(..., uri=True) + mode=ro``.
The ledger is ``data/companion.epoch2.sqlite``. Artifacts go to
``output/life-ecology/``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import json
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
OUTPUT = (REPO / "output" / "life-ecology").resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")

LANE_EVENT_TYPES = {
    "biographical": (
        "LifeArcOpened",
        "LifeArcClosed",
        "LifeArcChanged",
        "NpcRegistered",
        "NpcStatusChanged",
    ),
    "activity": (
        "ActivityPlanned",
        "ActivityStarted",
        "ActivityCompleted",
        "ActivityPaused",
        "ActivityResumed",
        "ActivityAbandoned",
        "PlanCreated",
        "PlanAccepted",
        "PlanAbandoned",
    ),
    "aftermath": (
        "WorldOccurrenceActivated",
        "WorldOccurrenceSettled",
        "WorldOccurrenceCommitted",
        "ExperienceCommitted",
        "OutcomeObservationRecorded",
        "OutcomeProposalRecorded",
        "LifeContentRecorded",
    ),
    "life_development": (
        "LifeDevelopmentProposed",
        "LifeDevelopmentAccepted",
        "ProposalRecorded",
    ),
    "npc": (
        "NpcStateChanged",
        "NpcInitiativeProposed",
        "NpcDecisionRecorded",
    ),
    "open_world": (
        "OpenWorldEventProposed",
        "OpenWorldEventCommitted",
    ),
    "visual_evidence": (
        "ImageEvidenceDeclared",
        "RecipientScopedImageEvidenceDeclared",
        "RandomDrawRecorded",
    ),
    "media": (
        "PhotoCandidateOpened",
        "MediaOpportunityAuthorized",
        "MediaPlanAccepted",
        "MediaPreviewGenerated",
        "MediaDeliveryShared",
    ),
}

LIFE_USAGE_PURPOSES = (
    "life_development_draft",
    "life_development_choice",
    "activity_lifecycle_choice",
    "outcome_selection",
    "experience_memory",
    "npc_ecology",
    "open_world",
)


def parse_event(raw: str | bytes) -> dict[str, Any]:
    event = json.loads(raw)
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            event["_payload"] = json.loads(payload)
        except json.JSONDecodeError:
            event["_payload"] = {}
    elif isinstance(payload, dict):
        event["_payload"] = payload
    else:
        payload_json = event.get("payload_json")
        if isinstance(payload_json, str):
            try:
                event["_payload"] = json.loads(payload_json)
            except json.JSONDecodeError:
                event["_payload"] = {}
        else:
            event["_payload"] = {}
    return event


def event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or event["_payload"].get("event_type") or "")


def as_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        from datetime import UTC

        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def local_day(value: object) -> str | None:
    parsed = as_dt(value)
    if parsed is None:
        return None
    return parsed.astimezone(SHANGHAI).date().isoformat()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def payload_process_kind(payload: dict[str, Any]) -> str | None:
    process = payload.get("process")
    if isinstance(process, dict) and process.get("process_kind"):
        return str(process["process_kind"])
    if payload.get("process_kind"):
        return str(payload["process_kind"])
    return None


def payload_outcome(payload: dict[str, Any]) -> str | None:
    for key in (
        "runtime_outcome_ref",
        "outcome",
        "outcome_ref",
        "reason_code",
        "status",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    process = payload.get("process")
    if isinstance(process, dict):
        for key in ("runtime_outcome_ref", "outcome"):
            value = process.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def nested_get(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def audit(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return _audit(conn, path)
    finally:
        conn.close()


def _audit(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    tables = list_tables(conn)
    event_types: Counter[str] = Counter()
    events: list[dict[str, Any]] = []
    world_count = conn.execute(
        "SELECT COUNT(*) FROM world_v2_events WHERE world_id = ?", (WORLD_ID,)
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT ledger_sequence, event_json FROM world_v2_events "
        "WHERE world_id = ? ORDER BY ledger_sequence",
        (WORLD_ID,),
    )
    for seq, raw in rows:
        event = parse_event(raw)
        kind = event_type(event)
        event_types[kind] += 1
        payload = event["_payload"]
        events.append(
            {
                "seq": int(seq),
                "event_type": kind,
                "event_id": event.get("event_id"),
                "logical_time": event.get("logical_time"),
                "created_at": event.get("created_at"),
                "actor": event.get("actor"),
                "source": event.get("source"),
                "payload": payload,
            }
        )

    clocks = [e for e in events if e["event_type"] == "ClockAdvanced"]
    clock_reasons: Counter[str] = Counter()
    clock_gaps_seconds: list[float] = []
    last_clock = None
    for item in clocks:
        payload = item["payload"]
        reason = (
            payload.get("reason")
            or payload.get("tick_reason")
            or nested_get(payload, "clock", "reason")
        )
        if isinstance(reason, str):
            clock_reasons[reason] += 1
        else:
            clock_reasons["(missing)"] += 1
        current = as_dt(item["logical_time"])
        if last_clock is not None and current is not None:
            clock_gaps_seconds.append((current - last_clock).total_seconds())
        last_clock = current

    life_clocks = [
        item
        for item in clocks
        if str(item["payload"].get("reason") or "").find("life_ecology") >= 0
        or str(item["payload"].get("reason") or "") == "qq_c2c_life_ecology_due_wake"
    ]

    triggers = [
        e
        for e in events
        if e["event_type"]
        in {
            "TriggerProcessOpened",
            "TriggerProcessClaimed",
            "TriggerProcessCompleted",
            "TriggerProcessFailed",
        }
    ]
    trigger_kinds: Counter[str] = Counter()
    trigger_outcomes: Counter[str] = Counter()
    ecology_triggers: list[dict[str, Any]] = []
    for item in triggers:
        kind = payload_process_kind(item["payload"]) or "(missing)"
        trigger_kinds[kind] += 1
        if "life" in kind or kind == "life_ecology":
            outcome = payload_outcome(item["payload"])
            if outcome:
                trigger_outcomes[outcome] += 1
            ecology_triggers.append(
                {
                    "seq": item["seq"],
                    "event_type": item["event_type"],
                    "logical_time": item["logical_time"],
                    "process_kind": kind,
                    "outcome": outcome,
                    "trigger_id": item["payload"].get("trigger_id")
                    or (item["payload"].get("process") or {}).get("trigger_id"),
                    "cadence_delay_seconds": item["payload"].get("cadence_delay_seconds"),
                    "runtime_outcome_ref": item["payload"].get("runtime_outcome_ref"),
                }
            )

    model_results = [e for e in events if e["event_type"] == "ModelResultRecorded"]
    model_purposes: Counter[str] = Counter()
    model_ids: Counter[str] = Counter()
    life_model_results: list[dict[str, Any]] = []
    weighted_table_hits = 0
    for item in model_results:
        payload = item["payload"]
        purpose = (
            payload.get("purpose")
            or (payload.get("audit") or {}).get("purpose")
            or (payload.get("route") or {}).get("reason_code")
            or ""
        )
        model_id = (
            payload.get("model_id")
            or payload.get("model")
            or (payload.get("audit") or {}).get("model_id")
            or ""
        )
        model_purposes[str(purpose or "(missing)")] += 1
        model_ids[str(model_id or "(missing)")] += 1
        blob = json.dumps(payload, ensure_ascii=False)
        is_life = any(
            marker in blob
            for marker in (
                "life_development",
                "activity_lifecycle",
                "npc_ecology",
                "open_world",
                "life_aftermath",
                "outcome_selection",
                "weighted-table",
                "life-ecology",
            )
        )
        if "deterministic:weighted-table" in blob:
            weighted_table_hits += 1
            is_life = True
        if is_life:
            life_model_results.append(
                {
                    "seq": item["seq"],
                    "logical_time": item["logical_time"],
                    "purpose": purpose,
                    "model_id": model_id,
                    "weighted_table": "deterministic:weighted-table" in blob,
                    "excerpt": {
                        key: payload.get(key)
                        for key in ("purpose", "model_id", "status", "decision")
                        if key in payload
                    },
                }
            )

    technical_failures = [
        e
        for e in events
        if "TechnicalFailure" in e["event_type"] or e["event_type"].endswith("Failed")
    ]
    technical_codes: Counter[str] = Counter()
    life_failures: list[dict[str, Any]] = []
    for item in technical_failures:
        payload = item["payload"]
        code = (
            payload.get("failure_code")
            or payload.get("reason_code")
            or payload.get("error_class")
            or item["event_type"]
        )
        technical_codes[str(code)] += 1
        blob = json.dumps(payload, ensure_ascii=False)
        if any(
            marker in blob or marker in item["event_type"]
            for marker in (
                "life",
                "activity",
                "npc",
                "ecology",
                "aftermath",
                "visual",
                "biograph",
            )
        ):
            life_failures.append(
                {
                    "seq": item["seq"],
                    "event_type": item["event_type"],
                    "logical_time": item["logical_time"],
                    "code": code,
                }
            )

    activity_events = [
        {
            "seq": e["seq"],
            "event_type": e["event_type"],
            "logical_time": e["logical_time"],
            "plan_id": e["payload"].get("plan_id"),
            "activity_kind": e["payload"].get("activity_kind")
            or (e["payload"].get("plan") or {}).get("activity_kind"),
            "opening_token": e["payload"].get("opening_token"),
            "privacy": e["payload"].get("privacy"),
            "visibility": e["payload"].get("visibility"),
            "window": {
                key: e["payload"].get(key)
                for key in (
                    "opens_at",
                    "closes_at",
                    "scheduled_start",
                    "scheduled_end",
                    "window_start",
                    "window_end",
                )
                if key in e["payload"]
            },
        }
        for e in events
        if e["event_type"].startswith("Activity") or e["event_type"] in {"PlanCreated", "PlanAccepted"}
    ]

    occurrence_events = [
        {
            "seq": e["seq"],
            "event_type": e["event_type"],
            "logical_time": e["logical_time"],
            "occurrence_id": e["payload"].get("occurrence_id"),
            "visibility": e["payload"].get("visibility"),
            "privacy": e["payload"].get("privacy"),
            "status": e["payload"].get("status"),
            "source": e["source"],
        }
        for e in events
        if "Occurrence" in e["event_type"]
    ]

    random_draws = []
    for e in events:
        if e["event_type"] != "RandomDrawRecorded":
            continue
        payload = e["payload"]
        catalog = payload.get("catalog_version") or payload.get("catalog")
        selected = payload.get("selected_candidate_ref") or payload.get("selected")
        random_draws.append(
            {
                "seq": e["seq"],
                "logical_time": e["logical_time"],
                "catalog_version": catalog,
                "selected": selected,
                "source": e["source"],
            }
        )
    draw_catalogs: Counter[str] = Counter(
        str(item["catalog_version"] or "(missing)") for item in random_draws
    )

    usage: list[dict[str, Any]] = []
    usage_by_purpose: Counter[str] = Counter()
    usage_cost_by_purpose: dict[str, float] = defaultdict(float)
    usage_status: Counter[str] = Counter()
    if table_exists(conn, "world_v2_model_usage"):
        for row in conn.execute(
            "SELECT id, recorded_at, purpose, model, status, cost_cny, "
            "prompt_tokens, completion_tokens, error, world_id FROM world_v2_model_usage "
            "ORDER BY id"
        ):
            item = dict(row)
            usage.append(item)
            purpose = str(item.get("purpose") or "(empty)")
            usage_by_purpose[purpose] += 1
            usage_cost_by_purpose[purpose] += float(item.get("cost_cny") or 0)
            usage_status[str(item.get("status") or "(empty)")] += 1

    life_usage = [
        item
        for item in usage
        if any(
            marker in str(item.get("purpose") or "")
            for marker in (
                "life",
                "activity",
                "npc",
                "aftermath",
                "outcome",
                "open_world",
                "visual",
            )
        )
    ]

    sidecar_tables = {}
    for name in (
        "world_v2_daily_occasions",
        "world_v2_occasion_spends",
        "world_v2_life_ecology_leases",
        "world_v2_life_ecology_schedule_overlay",
        "world_v2_character_interior_turns",
    ):
        if not table_exists(conn, name):
            sidecar_tables[name] = {"exists": False}
            continue
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({name})")]
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {name} LIMIT 200")]
        sidecar_tables[name] = {
            "exists": True,
            "columns": cols,
            "count": int(count),
            "rows": rows,
        }

    lease_outcomes: Counter[str] = Counter()
    lease_rows = sidecar_tables.get("world_v2_life_ecology_leases", {}).get("rows") or []
    for row in lease_rows:
        lease_outcomes[str(row.get("outcome") or "(none)")] += 1

    interior_turns = []
    interior_purposes: Counter[str] = Counter()
    if table_exists(conn, "world_v2_character_interior_turns"):
        cols = [row[1] for row in conn.execute("PRAGMA table_info(world_v2_character_interior_turns)")]
        query_cols = [c for c in cols if c in {
            "turn_id", "purpose", "status", "created_at", "logical_time",
            "opportunity_ref", "failure_code", "world_id", "actor_ref",
        }]
        if "purpose" in cols:
            for row in conn.execute(
                f"SELECT {', '.join(query_cols)} FROM world_v2_character_interior_turns"
            ):
                item = dict(row)
                interior_turns.append(item)
                interior_purposes[str(item.get("purpose") or "(empty)")] += 1

    clocks_by_day: Counter[str] = Counter()
    life_clocks_by_day: Counter[str] = Counter()
    for item in clocks:
        day = local_day(item["logical_time"])
        if day:
            clocks_by_day[day] += 1
    for item in life_clocks:
        day = local_day(item["logical_time"])
        if day:
            life_clocks_by_day[day] += 1

    first_logical = events[0]["logical_time"] if events else None
    last_logical = events[-1]["logical_time"] if events else None
    span_hours = None
    first_dt = as_dt(first_logical)
    last_dt = as_dt(last_logical)
    if first_dt and last_dt:
        span_hours = round((last_dt - first_dt).total_seconds() / 3600, 2)

    never_succeeded = []
    lane_success = {
        "biographical": event_types.get("LifeArcOpened", 0)
        + event_types.get("LifeArcChanged", 0)
        + event_types.get("NpcRegistered", 0),
        "activity": event_types.get("ActivityStarted", 0),
        "aftermath": event_types.get("WorldOccurrenceSettled", 0),
        "life_development": event_types.get("ActivityPlanned", 0)
        + sum(1 for item in ecology_triggers if "life_development_plan" in str(item.get("outcome"))),
        "npc": event_types.get("NpcStateChanged", 0),
        "open_world": event_types.get("OpenWorldEventCommitted", 0)
        + event_types.get("OpenWorldEventProposed", 0),
        "visual_evidence": event_types.get("ImageEvidenceDeclared", 0)
        + event_types.get("RecipientScopedImageEvidenceDeclared", 0),
        "media": event_types.get("PhotoCandidateOpened", 0),
    }

    # Open world is structurally uninstalled when life_development is composed.
    from companion_daemon.world_v2.life_ecology_lease_store import (
        SILENT_LIFE_ECOLOGY_OUTCOMES,
    )
    from companion_daemon.world_v2.life_ecology_trigger_store import (
        _AMBIENT_CADENCE_SECONDS,
        _SEMANTIC_STIMULUS_SECONDS,
    )
    from companion_daemon.world_v2.activity_timing import (
        MIN_ACTIVITY_COMPLETION_SECONDS,
        MIN_ACTIVITY_TRANSITION_DWELL_SECONDS,
    )
    from companion_daemon.world_v2.life_ecology_runtime import LifeEcologyRuntime
    import inspect as _inspect

    runtime_src = _inspect.getsource(LifeEcologyRuntime.advance_once)
    open_world_gated = (
        "self._life_development_followup is None" in runtime_src
        and "self._open_world_followup is not None" in runtime_src
    )
    compose_path = (
        REPO / "src" / "companion_daemon" / "world_v2" / "production_turn_application.py"
    )
    compose_src = compose_path.read_text(encoding="utf-8")
    open_world_uninstalled_in_prod = (
        "open_world_followup=open_world_event if life_development is None else None"
        in compose_src
    )

    weighted_still_called = False
    from companion_daemon.world_v2 import npc_ecology as npc_mod
    from companion_daemon.world_v2 import life_development_runtime as ld_mod

    npc_src = _inspect.getsource(npc_mod.NpcEcology._actor_decide)
    ld_src = _inspect.getsource(ld_mod.LifeDevelopmentRuntime._world_author_draft)
    npc_calls_weighted = "_weighted_actor_decision" in npc_src
    ld_short_circuits = "deterministic:weighted-table" in ld_src and "return" in ld_src
    # More precise: does _world_author_draft still return a table no_op?
    ld_returns_table = (
        "pick_weighted_token" in ld_src and "decision" in ld_src and "no_op" in ld_src
    )

    for lane, count in lane_success.items():
        if count == 0:
            never_succeeded.append(lane)

    return {
        "database": str(path),
        "world_id": WORLD_ID,
        "tables": tables,
        "event_count": int(world_count),
        "event_types": dict(event_types.most_common()),
        "logical_span": {
            "first": first_logical,
            "last": last_logical,
            "hours": span_hours,
            "first_local": local_day(first_logical),
            "last_local": local_day(last_logical),
        },
        "clocks": {
            "count": len(clocks),
            "reasons": dict(clock_reasons.most_common()),
            "life_ecology_due_wakes": len(life_clocks),
            "by_local_day": dict(clocks_by_day),
            "life_by_local_day": dict(life_clocks_by_day),
            "gap_seconds_p50": (
                sorted(clock_gaps_seconds)[len(clock_gaps_seconds) // 2]
                if clock_gaps_seconds
                else None
            ),
            "gap_seconds_min": min(clock_gaps_seconds) if clock_gaps_seconds else None,
            "gap_seconds_max": max(clock_gaps_seconds) if clock_gaps_seconds else None,
            "median_life_gap_hours": None,
        },
        "triggers": {
            "process_kinds": dict(trigger_kinds.most_common()),
            "ecology_outcomes": dict(trigger_outcomes.most_common()),
            "ecology_events": ecology_triggers,
        },
        "model_results": {
            "count": len(model_results),
            "purposes": dict(model_purposes.most_common()),
            "model_ids": dict(model_ids.most_common()),
            "weighted_table_hits": weighted_table_hits,
            "life": life_model_results,
        },
        "usage": {
            "count": len(usage),
            "by_purpose": dict(usage_by_purpose.most_common()),
            "cost_cny_by_purpose": {
                key: round(value, 4) for key, value in sorted(usage_cost_by_purpose.items())
            },
            "status": dict(usage_status),
            "life_rows": [
                {
                    "id": item.get("id"),
                    "recorded_at": item.get("recorded_at"),
                    "purpose": item.get("purpose"),
                    "model": item.get("model"),
                    "status": item.get("status"),
                    "cost_cny": item.get("cost_cny"),
                    "error": item.get("error"),
                }
                for item in life_usage
            ],
            "life_cost_cny": round(sum(float(i.get("cost_cny") or 0) for i in life_usage), 4),
        },
        "interior_turns": {
            "count": len(interior_turns),
            "by_purpose": dict(interior_purposes.most_common()),
            "life": [
                item
                for item in interior_turns
                if any(
                    marker in str(item.get("purpose") or "")
                    for marker in ("activity", "life", "outcome", "npc")
                )
            ],
        },
        "sidecars": sidecar_tables,
        "activity_events": activity_events,
        "occurrence_events": occurrence_events,
        "random_draws": {
            "count": len(random_draws),
            "catalogs": dict(draw_catalogs.most_common()),
            "items": random_draws[:80],
        },
        "technical_failures": {
            "codes": dict(technical_codes.most_common()),
            "life": life_failures,
        },
        "lane_success_proxy": lane_success,
        "never_succeeded_lanes": never_succeeded,
        "lease_outcomes": dict(lease_outcomes.most_common()),
        "code_facts": {
            "ambient_cadence_seconds": list(_AMBIENT_CADENCE_SECONDS),
            "semantic_stimulus_seconds": list(_SEMANTIC_STIMULUS_SECONDS),
            "silent_outcomes": sorted(SILENT_LIFE_ECOLOGY_OUTCOMES),
            "min_activity_completion_seconds": MIN_ACTIVITY_COMPLETION_SECONDS,
            "min_activity_transition_dwell_seconds": MIN_ACTIVITY_TRANSITION_DWELL_SECONDS,
            "open_world_gated_behind_no_life_development": open_world_gated,
            "open_world_uninstalled_when_life_development_composed": open_world_uninstalled_in_prod,
            "npc_actor_decide_calls_weighted": npc_calls_weighted,
            "life_development_world_author_still_short_circuits": ld_returns_table,
            "weighted_methods_still_defined": {
                "npc_ecology._weighted_actor_decision": hasattr(
                    npc_mod.NpcEcology, "_weighted_actor_decision"
                ),
                "npc_ecology._weighted_world_decision": hasattr(
                    npc_mod.NpcEcology, "_weighted_world_decision"
                ),
            },
        },
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if not PRODUCTION_DB.exists():
        raise SystemExit(f"missing production ledger: {PRODUCTION_DB}")
    report = audit(PRODUCTION_DB)
    (OUTPUT / "production-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(
        {
            "event_count": report["event_count"],
            "event_types_top": dict(list(report["event_types"].items())[:25]),
            "clocks": report["clocks"],
            "triggers_kinds": report["triggers"]["process_kinds"],
            "ecology_outcomes": report["triggers"]["ecology_outcomes"],
            "usage_by_purpose": report["usage"]["by_purpose"],
            "interior_by_purpose": report["interior_turns"]["by_purpose"],
            "lane_success_proxy": report["lane_success_proxy"],
            "never_succeeded_lanes": report["never_succeeded_lanes"],
            "lease_outcomes": report.get("lease_outcomes"),
            "code_facts": report["code_facts"],
            "sidecar_counts": {
                name: value.get("count") if value.get("exists") else None
                for name, value in report["sidecars"].items()
            },
            "activity_events": report["activity_events"],
            "occurrence_events": report["occurrence_events"],
            "weighted_table_hits": report["model_results"]["weighted_table_hits"],
            "life_usage_cost": report["usage"]["life_cost_cny"],
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))


if __name__ == "__main__":
    main()
