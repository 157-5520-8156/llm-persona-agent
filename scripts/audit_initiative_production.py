#!/usr/bin/env python3
"""Read-only production audit of initiative / expectation / revisit lanes.

Never writes ``data/``. Uses ``sqlite3.connect(..., uri=True) + mode=ro``.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
OUTPUT = (REPO / "output" / "initiative").resolve()


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


def walk(value: object) -> list[tuple[tuple[str, ...], object]]:
    out: list[tuple[tuple[str, ...], object]] = []

    def _walk(node: object, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                _walk(item, (*path, str(key)))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                _walk(item, (*path, str(index)))
        else:
            out.append((path, node))

    _walk(value, ())
    return out


def current_constants() -> dict[str, Any]:
    from companion_daemon.world_v2.present_prompt import (
        _SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS,
        _SLIM_WAIT_FLOOR_SECONDS,
        _SLIM_WAIT_MAX_SECONDS,
        slim_consider_json_schema,
        SLIM_CONSIDER_KEYS,
    )
    from companion_daemon.world_v2.response_expectation_view import EXPIRED_EXPECTATION_GRACE
    from companion_daemon.world_v2.revisit_intention_view import REVISIT_INTENTION_GRACE
    from companion_daemon.world_v2.social_initiative import (
        _AMBIENT_EXPIRY_GRACE_SECONDS,
        _SITUATION_STIMULUS_EVENT_TYPES,
        _SITUATION_WINDOW,
        SocialInitiativeContextPolicy,
        SocialInitiativePolicy,
    )
    from companion_daemon.world_v2.production_turn_application import WorldV2TurnApplicationConfig
    from companion_daemon.world_v2.occasion import _DEFAULT_TTL, PURPOSE_OCCASION_KIND
    from companion_daemon.world_v2.character_interior import production as interior_production

    policy = SocialInitiativePolicy()
    dummy_cfg = getattr(
        WorldV2TurnApplicationConfig, "__dataclass_fields__", {}
    )
    schema_keys = set(slim_consider_json_schema()["properties"])
    drain_source = interior_production._CharacterInteriorBackgroundDriver.drain_private_impression_once
    private_impression_closed = "return None" in (drain_source.__doc__ or "") or True
    # The production driver hard-returns None; confirm by source bytes.
    import inspect

    impression_src = inspect.getsource(
        interior_production._CharacterInteriorBackgroundDriver.drain_private_impression_once
    )
    return {
        "wait_floor_seconds": _SLIM_WAIT_FLOOR_SECONDS,
        "wait_max_seconds": _SLIM_WAIT_MAX_SECONDS,
        "chase_after_wait_seconds": _SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS,
        "expired_expectation_grace_seconds": int(EXPIRED_EXPECTATION_GRACE.total_seconds()),
        "revisit_intention_grace_seconds": int(REVISIT_INTENTION_GRACE.total_seconds()),
        "spontaneous_idle_seconds": policy.spontaneous_idle_seconds,
        "spontaneous_expiry_seconds": policy.spontaneous_expiry_seconds,
        "contact_cooldown_seconds": policy.contact_cooldown_seconds,
        "ambient_expiry_grace_seconds": _AMBIENT_EXPIRY_GRACE_SECONDS,
        "situation_window_seconds": int(_SITUATION_WINDOW.total_seconds()),
        "situation_stimulus_event_types": sorted(_SITUATION_STIMULUS_EVENT_TYPES),
        "cadence_floor_seconds_non_override": 2_700,
        "consideration_bands_seconds": {
            "close_friend_or_lover": [3_600, 7_200],
            "friend_or_ambiguous": [7_200, 14_400],
            "acquaintance": [10_800, 21_600],
            "stranger": [21_600, 28_800],
        },
        "context_policy_version": SocialInitiativeContextPolicy.version,
        "silence_appraisal_idle_seconds_default": 3_600,
        "quiet_gap_occasion_ttl_seconds": int(_DEFAULT_TTL["quiet_gap"].total_seconds()),
        "purpose_occasion_kind": dict(PURPOSE_OCCASION_KIND),
        "slim_instruction_keys": sorted(SLIM_CONSIDER_KEYS),
        "slim_json_schema_keys": sorted(schema_keys),
        "schema_missing_vs_instruction": sorted(SLIM_CONSIDER_KEYS - schema_keys),
        "private_impression_drain_returns_none": "return None" in impression_src
        and impression_src.count("return") == 1,
        "turn_config_has_silence_field": "silence_appraisal_idle_seconds" in dummy_cfg,
        "notes": {
            "situation_change_independent_mint": (
                "social_initiative.next_opportunity no longer mints source_kind="
                "situation_change; life/affect events hitch onto an already-paid "
                "consider via _hitch_situation_materials. Recovery/retry may still "
                "surface source_kind=situation_change for historically minted processes."
            ),
            "expired_window": (
                "Wake at not_before (= declared wait, floored at 30s). Hope itself "
                "expires at wait+60s. Chase opportunity remains until expires_at + 1h."
            ),
        },
    }


def audit_ledger(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        event_types: Counter[str] = Counter()
        process_kinds: Counter[str] = Counter()
        process_outcomes: Counter[str] = Counter()
        action_kinds: Counter[str] = Counter()
        delivered_kinds: Counter[str] = Counter()
        draw_catalogs: Counter[str] = Counter()
        field_hits: Counter[str] = Counter()
        field_samples: dict[str, list[dict[str, Any]]] = {
            "waiting_for": [],
            "wait": [],
            "come_back": [],
            "come_back_in": [],
            "hoped_response": [],
            "response_expectation": [],
            "revisit": [],
        }
        expectation_plans = 0
        revisit_plans = 0
        expression_plans = 0
        media_requests = 0
        last_observation = None
        last_clock = None
        last_proactive_process = None
        trigger_samples: list[dict[str, Any]] = []
        cadence_codes: Counter[str] = Counter()

        rows = conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? ORDER BY ledger_sequence",
            (WORLD_ID,),
        )
        for seq, raw in rows:
            event = parse_event(raw)
            kind = str(event.get("event_type") or "")
            event_types[kind] += 1
            payload = event.get("_payload") or {}
            logical_time = event.get("logical_time")

            if kind == "ObservationRecorded":
                last_observation = {
                    "seq": seq,
                    "logical_time": logical_time,
                    "observation_id": payload.get("observation_id"),
                    "text_excerpt": str(
                        (payload.get("observation") or payload).get("text")
                        if isinstance(payload.get("observation"), dict)
                        else payload.get("text") or ""
                    )[:160],
                }
            if kind == "ClockAdvanced":
                last_clock = {
                    "seq": seq,
                    "logical_time": logical_time,
                    "to": payload.get("logical_time_to"),
                }
            if kind == "TriggerProcessOpened":
                process = payload.get("process") if isinstance(payload.get("process"), dict) else payload
                pk = str(process.get("process_kind") or payload.get("process_kind") or "")
                process_kinds[pk] += 1
                if pk in {
                    "proactive_action_deliberation",
                    "silence_appraisal",
                    "media_request",
                    "life_reflection",
                    "world_stimulus",
                } or "proactive" in pk or "silence" in pk:
                    sample = {
                        "seq": seq,
                        "logical_time": logical_time,
                        "process_kind": pk,
                        "trigger_ref": process.get("trigger_ref") or payload.get("trigger_ref"),
                        "source_evidence_ref": process.get("source_evidence_ref")
                        or payload.get("source_evidence_ref"),
                        "state": process.get("state"),
                    }
                    trigger_samples.append(sample)
                    last_proactive_process = sample if "proactive" in pk else last_proactive_process
            if kind == "TriggerProcessCompleted":
                process = payload.get("process") if isinstance(payload.get("process"), dict) else payload
                outcome = str(
                    process.get("runtime_outcome_ref")
                    or payload.get("runtime_outcome_ref")
                    or payload.get("outcome")
                    or ""
                )
                process_outcomes[outcome[:80] or "(empty)"] += 1
            if kind == "ActionAuthorized":
                action = payload.get("action") if isinstance(payload.get("action"), dict) else payload
                action_kinds[str(action.get("kind") or "unknown")] += 1
            if kind == "ActionDelivered":
                action = payload.get("action") if isinstance(payload.get("action"), dict) else payload
                delivered_kinds[str(action.get("kind") or payload.get("kind") or "unknown")] += 1
            if kind == "RandomDrawRecorded":
                catalog = str(payload.get("catalog_version") or payload.get("catalog") or "")
                draw_catalogs[catalog or "(empty)"] += 1
                for code in payload.get("reason_codes") or ():
                    cadence_codes[str(code)] += 1
            if kind == "ExpressionPlanAccepted":
                expression_plans += 1
                if payload.get("response_expectation"):
                    expectation_plans += 1
                material = payload.get("material") if isinstance(payload.get("material"), dict) else {}
                if material.get("response_expectation") or payload.get("response_expectation"):
                    expectation_plans = max(
                        expectation_plans,
                        1 if payload.get("response_expectation") or material.get("response_expectation") else 0,
                    )
                leftover = payload.get("revisit") or material.get("revisit")
                if leftover:
                    revisit_plans += 1
                if (payload.get("media_request") or material.get("media_request") or "none") not in {
                    "none",
                    None,
                    "",
                }:
                    media_requests += 1

            blob_paths = walk(payload)
            interesting = {
                "waiting_for",
                "wait",
                "come_back",
                "come_back_in",
                "hoped_response",
                "response_expectation",
                "revisit",
            }
            seen_this_event: set[str] = set()
            for path, value in blob_paths:
                leaf = path[-1] if path else ""
                if leaf not in interesting:
                    continue
                empty = value in (None, "", [], {}, False)
                if empty:
                    continue
                field_hits[leaf] += 1
                if leaf in field_samples and len(field_samples[leaf]) < 8 and leaf not in seen_this_event:
                    seen_this_event.add(leaf)
                    field_samples[leaf].append(
                        {
                            "seq": seq,
                            "event_type": kind,
                            "logical_time": logical_time,
                            "path": ".".join(path),
                            "value": value if not isinstance(value, (dict, list)) else str(value)[:240],
                        }
                    )

        # Recount expectation/revisit more carefully: any non-null object.
        expectation_non_null = 0
        revisit_non_null = 0
        waiting_for_non_null = 0
        come_back_non_null = 0
        wait_non_null = 0
        come_back_in_non_null = 0
        how_it_landed_non_null = 0
        later_non_null = 0
        photo_true = 0
        media_request_non_none = 0
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events WHERE world_id = ?",
            (WORLD_ID,),
        ):
            event = parse_event(raw)
            payload = event.get("_payload") or {}
            for path, value in walk(payload):
                leaf = path[-1] if path else ""
                if leaf == "response_expectation" and value not in (None, "", {}, []):
                    expectation_non_null += 1
                elif leaf == "revisit" and value not in (None, "", {}, []):
                    revisit_non_null += 1
                elif leaf == "waiting_for" and value not in (None, ""):
                    waiting_for_non_null += 1
                elif leaf == "come_back" and value not in (None, ""):
                    come_back_non_null += 1
                elif leaf == "wait" and value not in (None, "", 0):
                    wait_non_null += 1
                elif leaf == "come_back_in" and value not in (None, "", 0):
                    come_back_in_non_null += 1
                elif leaf == "how_it_landed" and value not in (None, ""):
                    how_it_landed_non_null += 1
                elif leaf == "later" and value not in (None, "", 0, False):
                    later_non_null += 1
                elif leaf == "photo" and value in (True, "true", "consider_available_candidate"):
                    photo_true += 1
                elif leaf == "media_request" and value not in (None, "", "none"):
                    media_request_non_none += 1

        usage: list[dict[str, Any]] = []
        usage_by_purpose: Counter[str] = Counter()
        usage_cost: Counter[str] = Counter()
        try:
            for row in conn.execute(
                "SELECT purpose, status, cost_cny, model, error FROM world_v2_model_usage"
            ):
                purpose = str(row["purpose"] or "unknown")
                usage_by_purpose[purpose] += 1
                usage_cost[purpose] += float(row["cost_cny"] or 0)
                if purpose in {
                    "proactive_contact",
                    "world_stimulus_appraisal",
                    "inbound_turn",
                    "media_selection",
                    "activity_lifecycle_choice",
                    "life_development",
                } or "proactive" in purpose or "silence" in purpose:
                    usage.append(
                        {
                            "purpose": purpose,
                            "status": row["status"],
                            "cost_cny": row["cost_cny"],
                            "model": row["model"],
                            "error": (row["error"] or "")[:200],
                        }
                    )
        except sqlite3.OperationalError as exc:
            usage = [{"error": str(exc)}]

        relationship = []
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events WHERE world_id = ? "
            "AND json_extract(event_json,'$.event_type') IN "
            "('RelationshipSlowVariableAdjusted','RelationshipStateRecorded',"
            "'RelationshipCommitmentAccepted') ORDER BY ledger_sequence",
            (WORLD_ID,),
        ):
            event = parse_event(raw)
            payload = event["_payload"]
            relationship.append(
                {
                    "seq": seq,
                    "event_type": event.get("event_type"),
                    "stage": payload.get("stage_after") or payload.get("stage"),
                    "logical_time": event.get("logical_time"),
                }
            )

        return {
            "database": str(path),
            "event_count": sum(event_types.values()),
            "event_types": dict(event_types.most_common()),
            "process_kinds_opened": dict(process_kinds),
            "process_outcomes": dict(process_outcomes.most_common(30)),
            "action_kinds_authorized": dict(action_kinds),
            "action_kinds_delivered": dict(delivered_kinds),
            "random_draw_catalogs": dict(draw_catalogs),
            "field_hits_non_empty": dict(field_hits),
            "field_samples": field_samples,
            "expression_plan_accepted": expression_plans,
            "response_expectation_non_null_nodes": expectation_non_null,
            "revisit_non_null_nodes": revisit_non_null,
            "waiting_for_non_null_nodes": waiting_for_non_null,
            "wait_non_null_nodes": wait_non_null,
            "come_back_non_null_nodes": come_back_non_null,
            "come_back_in_non_null_nodes": come_back_in_non_null,
            "how_it_landed_non_null_nodes": how_it_landed_non_null,
            "later_non_null_nodes": later_non_null,
            "photo_true_nodes": photo_true,
            "media_request_non_none_nodes": media_request_non_none,
            "last_observation": last_observation,
            "last_clock": last_clock,
            "last_proactive_process": last_proactive_process,
            "trigger_samples_tail": trigger_samples[-25:],
            "usage_by_purpose": dict(usage_by_purpose),
            "usage_cost_cny_by_purpose": {k: round(v, 4) for k, v in usage_cost.items()},
            "usage_interesting_tail": usage[-20:],
            "relationship_mutations_tail": relationship[-8:],
            "life_counts": {
                name: event_types.get(name, 0)
                for name in (
                    "ActivityPlanned",
                    "ActivityStarted",
                    "ActivityCompleted",
                    "WorldOccurrenceActivated",
                    "WorldOccurrenceSettled",
                    "LifeArcChanged",
                    "AffectEpisodeOpened",
                    "PhotoCandidateOpened",
                    "MediaOpportunityAuthorized",
                    "ResponseExpectationAssessed",
                    "PrivateImpressionAccepted",
                )
            },
        }
    finally:
        conn.close()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    constants = current_constants()
    ledger = audit_ledger(PRODUCTION_DB)
    report = {
        "generated_at": datetime.now().isoformat(),
        "constants": constants,
        "production": ledger,
    }
    target = OUTPUT / "production_audit.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(
        {
            "wrote": str(target),
            "event_count": ledger["event_count"],
            "waiting_for_non_null_nodes": ledger["waiting_for_non_null_nodes"],
            "come_back_non_null_nodes": ledger["come_back_non_null_nodes"],
            "process_kinds_opened": ledger["process_kinds_opened"],
            "action_kinds_authorized": ledger["action_kinds_authorized"],
            "last_observation": ledger["last_observation"],
            "wait_floor_seconds": constants["wait_floor_seconds"],
            "spontaneous_idle_seconds": constants["spontaneous_idle_seconds"],
            "schema_missing_vs_instruction": constants["schema_missing_vs_instruction"],
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))


if __name__ == "__main__":
    main()
