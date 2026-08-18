#!/usr/bin/env python
"""Read-only audit of the relationship line on a World V2 ledger.

Answers, in numbers, whether production is stuck at ``stranger`` because she
never proposed a change, because the acceptance chain rejected her, because the
six axes barely move, because the thresholds are unreachable, or because a
retired policy digest froze the carried genesis state.

    .venv/bin/python scripts/audit_relationship_production.py
    .venv/bin/python scripts/audit_relationship_production.py --db data/companion.epoch2.sqlite

Never opens the database for write.  Writes JSON + Markdown under
``output/relationship/``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from companion_daemon.world_v2.relationship_reducers import (  # noqa: E402
    COMMITMENT_ONLY_RELATIONSHIP_STAGES,
    RELATIONSHIP_POLICY_DIGEST,
    RETIRED_RELATIONSHIP_POLICY_DIGESTS,
    _POLICY,
    _STAGES,
    _VARIABLE_NAMES,
    _derive_stage,
    relationship_state_policy_is_readable,
)
from companion_daemon.world_v2.schemas import (  # noqa: E402
    RelationshipHysteresisProjection,
    RelationshipStateProjection,
    RelationshipVariablesProjection,
)

WORLD_ID = "world:companion-v2:qq-c2c:geoff"
DEFAULT_DB = ROOT / "data" / "companion.epoch2.sqlite"
OUTPUT_DIR = ROOT / "output" / "relationship"

RELATIONSHIP_EVENT_TYPES = (
    "RelationshipSignalAccepted",
    "RelationshipSlowVariableAdjusted",
    "RelationshipCommitmentAccepted",
    "RelationshipStateRecorded",
    "BoundaryChanged",
)
PROPOSAL_HINTS = (
    "we_are",
    "us_deltas",
    "about_us",
    "why_us",
    "calling_it",
    "said_as",
    "relationship_signal",
    "relationship_commitment",
    "suggested_deltas",
)
FAILURE_HINTS = (
    "relationship",
    "policy_uninstalled",
    "uninstalled",
    "no_change",
    "commitment",
    "us_deltas",
    "we_are",
)


def _connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _load_json(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or "")


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    value = _load_json(event.get("payload_json"))
    return value if isinstance(value, dict) else {}


def _events(connection: sqlite3.Connection, world_id: str) -> Iterator[tuple[int, dict[str, Any]]]:
    cursor = connection.execute(
        "SELECT ledger_sequence, event_json FROM world_v2_events"
        " WHERE world_id = ? ORDER BY ledger_sequence",
        (world_id,),
    )
    for sequence, raw in cursor:
        event = _load_json(raw)
        if isinstance(event, dict):
            yield int(sequence), event


def _busiest_world(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT world_id, COUNT(*) FROM world_v2_events"
        " GROUP BY world_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else WORLD_ID


def _reassemble_head(connection: sqlite3.Connection, world_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT state_json FROM world_v2_heads WHERE world_id = ?",
        (world_id,),
    ).fetchone()
    if row is None:
        return {}
    marker = row[0]
    if marker != "world-v2-head-state-items.1":
        loaded = _load_json(marker)
        return loaded if isinstance(loaded, dict) else {}
    state: dict[str, Any] = {}
    arrays: dict[str, list[Any]] = {}
    for field, idx, item_json in connection.execute(
        "SELECT field, idx, item_json FROM world_v2_head_state_items"
        " WHERE world_id = ? ORDER BY field, idx",
        (world_id,),
    ):
        value = _load_json(item_json)
        if int(idx) == -1:
            state[str(field)] = value
        else:
            arrays.setdefault(str(field), []).append(value)
    state.update(arrays)
    return state


def _walk(value: Any, key: str) -> Iterator[Any]:
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                yield item
            yield from _walk(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, key)


def _contains_key(value: Any, key: str) -> bool:
    return next(_walk(value, key), _SENTINEL) is not _SENTINEL


_SENTINEL = object()


def _nonzero_deltas(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    deltas = {}
    for name in _VARIABLE_NAMES:
        raw = value.get(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        amount = int(raw)
        if amount:
            deltas[name] = amount
    return deltas or None


def _axis_snapshot(variables: Any) -> dict[str, int]:
    if not isinstance(variables, dict):
        variables = {}
    return {name: int(variables.get(name) or 0) for name in _VARIABLE_NAMES}


def _mean_score(variables: dict[str, int]) -> int:
    return sum(variables.values()) // len(_VARIABLE_NAMES)


def _gap_to_next(stage: str, score: int, variables: dict[str, int]) -> dict[str, Any]:
    if stage in COMMITMENT_ONLY_RELATIONSHIP_STAGES:
        return {
            "next_stage": None,
            "kind": "commitment_only",
            "note": "stage is held by an explicit commitment; axes cannot promote or demote it",
        }
    if stage not in _STAGES:
        return {"next_stage": None, "kind": "unknown_stage", "score": score}
    index = _STAGES.index(stage)
    if index >= len(_STAGES) - 1:
        return {
            "next_stage": None,
            "kind": "ladder_top",
            "score": score,
            "note": "close_friend is the last threshold stage; ambiguous/lover need a commitment",
        }
    nxt = _STAGES[index + 1]
    enter = int(_POLICY["enter_bp"][nxt])
    shortfall = max(0, enter - score)
    weakest = sorted(variables.items(), key=lambda item: item[1])
    return {
        "next_stage": nxt,
        "enter_bp": enter,
        "exit_bp": int(_POLICY["exit_bp"][nxt]),
        "current_mean_bp": score,
        "shortfall_mean_bp": shortfall,
        "weakest_axes": [{"axis": name, "bp": value} for name, value in weakest[:3]],
        "required_confirmations": _POLICY["required_confirmations"],
        "minimum_dwell_seconds": _POLICY["minimum_dwell_seconds"],
    }


def _digest_status(digest: str | None, version: str | None) -> dict[str, Any]:
    probe = type("Probe", (), {"policy_digest": digest, "policy_version": version})()
    readable = relationship_state_policy_is_readable(probe)
    if digest == RELATIONSHIP_POLICY_DIGEST:
        kind = "installed"
    elif digest in RETIRED_RELATIONSHIP_POLICY_DIGESTS:
        kind = "retired_readable"
    elif digest:
        kind = "unknown_unreadable"
    else:
        kind = "missing"
    return {
        "policy_version": version,
        "policy_digest": digest,
        "installed_digest": RELATIONSHIP_POLICY_DIGEST,
        "retired_digests": sorted(RETIRED_RELATIONSHIP_POLICY_DIGESTS),
        "readable": readable,
        "kind": kind,
    }


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _interior_payloads(connection: sqlite3.Connection, world_id: str) -> Iterator[dict[str, Any]]:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(world_v2_character_interior_turns)")
    }
    if not columns:
        return
    select = [
        "purpose",
        "phase",
        "state",
        "authored_state_json",
        "terminal_result_json",
        "updated_at",
    ]
    select = [name for name in select if name in columns]
    sql = (
        f"SELECT {', '.join(select)} FROM world_v2_character_interior_turns"
        " WHERE world_id = ?"
    )
    for row in connection.execute(sql, (world_id,)):
        record = {name: row[name] for name in select}
        authored = _load_json(record.get("authored_state_json"))
        terminal = _load_json(record.get("terminal_result_json"))
        yield {
            "purpose": record.get("purpose"),
            "phase": record.get("phase"),
            "state": record.get("state"),
            "updated_at": record.get("updated_at"),
            "authored": authored if isinstance(authored, dict) else {},
            "terminal": terminal if isinstance(terminal, dict) else {},
        }


def _collect_authored_fields(blob: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key in PROPOSAL_HINTS:
        values = [item for item in _walk(blob, key) if item is not None]
        if values:
            found[key] = values
    return found


def _reason_codes_from(value: Any) -> list[str]:
    codes: list[str] = []
    for item in _walk(value, "reason_code"):
        if isinstance(item, str) and item:
            codes.append(item)
    for item in _walk(value, "failure_code"):
        if isinstance(item, str) and item:
            codes.append(item)
    for item in _walk(value, "code"):
        if isinstance(item, str) and "relationship" in item:
            codes.append(item)
    return codes


def audit(connection: sqlite3.Connection, world_id: str) -> dict[str, Any]:
    type_counts: Counter[str] = Counter()
    relationship_events: list[dict[str, Any]] = []
    proposal_authored: list[dict[str, Any]] = []
    commitment_changes: list[dict[str, Any]] = []
    signal_changes: list[dict[str, Any]] = []
    rejection_events: list[dict[str, Any]] = []
    silent_failures: list[dict[str, Any]] = []
    model_us_deltas: list[dict[str, Any]] = []
    model_we_are: list[dict[str, Any]] = []
    observation_count = 0
    first_obs: str | None = None
    last_obs: str | None = None
    last_clock: str | None = None
    genesis_events: list[dict[str, Any]] = []
    drain_errors: list[dict[str, Any]] = []
    action_kinds: Counter[str] = Counter()
    acceptance_reasons: Counter[str] = Counter()

    for sequence, event in _events(connection, world_id):
        kind = _event_type(event)
        type_counts[kind] += 1
        payload = _payload(event)
        logical_time = event.get("logical_time")
        if kind == "ObservationRecorded":
            observation_count += 1
            first_obs = first_obs or str(logical_time)
            last_obs = str(logical_time)
        if kind == "ClockAdvanced":
            last_clock = str(logical_time)
        if kind == "ActionAuthorized":
            action = payload.get("action") if isinstance(payload.get("action"), dict) else payload
            if isinstance(action, dict):
                action_kinds[str(action.get("kind") or "unknown")] += 1
        if kind == "WorldStarted" or kind in {
            "ContinuitySnapshotRecorded",
            "WorldEpochOpened",
            "EpochOpened",
            "ContinuityHydrated",
        }:
            continuity = payload.get("continuity")
            snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
            carried = None
            if isinstance(continuity, dict):
                carried = continuity.get("relationship_states")
            elif snapshot:
                carried = snapshot.get("relationship_states")
            genesis_events.append(
                {
                    "seq": sequence,
                    "event_type": kind,
                    "logical_time": logical_time,
                    "keys": sorted(payload.keys())[:40],
                    "relationship_states": carried,
                }
            )
        if kind in RELATIONSHIP_EVENT_TYPES or "Relationship" in kind:
            relationship_events.append(
                {
                    "seq": sequence,
                    "event_type": kind,
                    "logical_time": logical_time,
                    "payload": payload,
                }
            )
        if kind == "ProposalRecorded":
            nested = _payload(event)
            proposal_json = nested.get("proposal_json")
            if isinstance(proposal_json, str):
                loaded = _load_json(proposal_json)
                if isinstance(loaded, dict):
                    nested = {**nested, "proposal_json": loaded}
            found = _collect_authored_fields(nested)
            if found:
                proposal_authored.append(
                    {
                        "seq": sequence,
                        "logical_time": logical_time,
                        "proposal_id": nested.get("proposal_id"),
                        "fields": {key: _summarize(value) for key, value in found.items()},
                    }
                )
            for commitment in _walk(nested, "relationship_commitment"):
                if isinstance(commitment, dict):
                    commitment_changes.append(
                        {
                            "seq": sequence,
                            "logical_time": logical_time,
                            "source": "ProposalRecorded",
                            "commitment": commitment,
                        }
                    )
            for signal in _walk(nested, "relationship_signal"):
                if isinstance(signal, dict):
                    signal_changes.append(
                        {
                            "seq": sequence,
                            "logical_time": logical_time,
                            "source": "ProposalRecorded",
                            "signal": {
                                "signal_code": signal.get("signal_code"),
                                "rationale_code": signal.get("rationale_code"),
                                "suggested_deltas": signal.get("suggested_deltas"),
                                "nonzero": _nonzero_deltas(signal.get("suggested_deltas")),
                            },
                        }
                    )
        if kind == "ModelResultRecorded":
            nested = dict(payload)
            audit_raw = nested.get("audit_json")
            if isinstance(audit_raw, str):
                loaded = _load_json(audit_raw)
                if isinstance(loaded, dict):
                    nested["audit_json"] = loaded
            found = _collect_authored_fields(nested)
            if found.get("us_deltas") or found.get("we_are") or found.get("relationship_signal"):
                model_us_deltas.append(
                    {
                        "seq": sequence,
                        "logical_time": logical_time,
                        "fields": {key: _summarize(value) for key, value in found.items()},
                    }
                )
            if found.get("we_are"):
                model_we_are.append(
                    {
                        "seq": sequence,
                        "logical_time": logical_time,
                        "we_are": _summarize(found["we_are"]),
                    }
                )
            audit = nested.get("audit_json")
            if isinstance(audit, dict):
                rejection = audit.get("role_rejection") or audit.get("rejection")
                if isinstance(rejection, dict) and any(
                    hint in json.dumps(rejection, ensure_ascii=False).lower()
                    for hint in FAILURE_HINTS
                ):
                    silent_failures.append(
                        {
                            "seq": sequence,
                            "event_type": kind,
                            "logical_time": logical_time,
                            "role_rejection": rejection,
                        }
                    )
        if "Reject" in kind or "Failure" in kind or kind.endswith("Rejected"):
            blob = json.dumps(payload, ensure_ascii=False)
            if any(hint in blob.lower() or hint in kind.lower() for hint in FAILURE_HINTS):
                rejection_events.append(
                    {
                        "seq": sequence,
                        "event_type": kind,
                        "logical_time": logical_time,
                        "reason_codes": _reason_codes_from(payload),
                        "excerpt": blob[:600],
                    }
                )
        if "TechnicalFailure" in kind or kind == "AdvisoryAcceptanceRejected":
            blob = json.dumps(payload, ensure_ascii=False)
            if "relationship" in blob.lower() or "policy" in blob.lower():
                drain_errors.append(
                    {
                        "seq": sequence,
                        "event_type": kind,
                        "logical_time": logical_time,
                        "reason_codes": _reason_codes_from(payload),
                        "excerpt": blob[:600],
                    }
                )
        if kind in {"AcceptanceRecorded", "AcceptanceDecisionRecorded"}:
            for code in _reason_codes_from(payload):
                if "relationship" in code.lower() or "uninstalled" in code.lower():
                    acceptance_reasons[code] += 1
                    rejection_events.append(
                        {
                            "seq": sequence,
                            "event_type": kind,
                            "logical_time": logical_time,
                            "reason_codes": [code],
                        }
                    )
        if kind == "AuditedChangeTerminalRecorded" or "Terminal" in kind:
            blob = json.dumps(payload, ensure_ascii=False)
            if "relationship" in blob.lower():
                drain_errors.append(
                    {
                        "seq": sequence,
                        "event_type": kind,
                        "logical_time": logical_time,
                        "reason_codes": _reason_codes_from(payload),
                        "excerpt": blob[:600],
                    }
                )

    interior_proposals: list[dict[str, Any]] = []
    interior_counts = Counter()
    for record in _interior_payloads(connection, world_id):
        merged = {"authored": record["authored"], "terminal": record["terminal"]}
        found = _collect_authored_fields(merged)
        if not found:
            continue
        interior_counts["turns_with_relationship_fields"] += 1
        if found.get("us_deltas"):
            interior_counts["us_deltas"] += 1
        if found.get("we_are"):
            interior_counts["we_are"] += 1
        if found.get("about_us"):
            interior_counts["about_us"] += 1
        if found.get("why_us"):
            interior_counts["why_us"] += 1
        if found.get("relationship_signal"):
            interior_counts["relationship_signal"] += 1
        if found.get("relationship_commitment"):
            interior_counts["relationship_commitment"] += 1
        nonzero = None
        for blob in found.get("us_deltas") or []:
            nonzero = _nonzero_deltas(blob) or nonzero
        interior_proposals.append(
            {
                "purpose": record.get("purpose"),
                "updated_at": record.get("updated_at"),
                "fields": {key: _summarize(value) for key, value in found.items()},
                "nonzero_us_deltas": nonzero,
            }
        )

    head = _reassemble_head(connection, world_id)
    states = list(head.get("relationship_states") or [])
    signals = list(head.get("relationship_signals") or [])
    adjustments = list(head.get("relationship_adjustments") or [])
    commitments = list(head.get("relationship_commitments") or [])
    proposals = list(head.get("relationship_proposals") or [])
    decisions = list(head.get("acceptance_decisions") or [])

    current_states = []
    for raw in states:
        if not isinstance(raw, dict):
            continue
        variables = _axis_snapshot(raw.get("variables"))
        score = _mean_score(variables)
        stage = str(raw.get("stage") or "stranger")
        digest = _digest_status(raw.get("policy_digest"), raw.get("policy_version"))
        derived_stage, hysteresis = _safe_derive(stage, variables, raw.get("hysteresis"), head.get("logical_time"))
        current_states.append(
            {
                "relationship_id": raw.get("relationship_id"),
                "subject_ref": raw.get("subject_ref"),
                "entity_revision": raw.get("entity_revision"),
                "stage": stage,
                "derived_stage": derived_stage,
                "hysteresis": hysteresis,
                "variables": variables,
                "mean_bp": score,
                "temperature": raw.get("temperature"),
                "last_adjusted_at": raw.get("last_adjusted_at"),
                "commitment_refs": raw.get("commitment_refs") or [],
                "origin": raw.get("origin"),
                "policy": digest,
                "gap_to_next": _gap_to_next(stage, score, variables),
            }
        )

    usage = _usage(connection, world_id)
    pending_signals = [
        {
            "signal_id": item.get("signal_id"),
            "subject_ref": item.get("subject_ref"),
            "signal_code": item.get("signal_code") or item.get("rationale_code"),
            "suggested_deltas": item.get("suggested_deltas"),
            "nonzero": _nonzero_deltas(item.get("suggested_deltas")),
            "accepted_at": item.get("accepted_at"),
        }
        for item in signals
        if isinstance(item, dict)
    ]
    consumed = {
        ref
        for item in adjustments
        if isinstance(item, dict)
        for ref in (item.get("signal_refs") or [])
    }
    unconsumed = [item for item in pending_signals if item.get("signal_id") not in consumed]

    classification = _classify(
        current_states=current_states,
        relationship_events=relationship_events,
        interior_counts=interior_counts,
        interior_proposals=interior_proposals,
        proposal_authored=proposal_authored,
        signal_changes=signal_changes,
        commitment_changes=commitment_changes,
        rejection_events=rejection_events,
        drain_errors=drain_errors,
        observation_count=observation_count,
    )

    return {
        "database": str(DEFAULT_DB),
        "world_id": world_id,
        "event_count": sum(type_counts.values()),
        "observation_count": observation_count,
        "first_observation": first_obs,
        "last_observation": last_obs,
        "last_clock": last_clock,
        "installed_policy_digest": RELATIONSHIP_POLICY_DIGEST,
        "retired_policy_digests": sorted(RETIRED_RELATIONSHIP_POLICY_DIGESTS),
        "policy": {
            "enter_bp": _POLICY["enter_bp"],
            "exit_bp": _POLICY["exit_bp"],
            "delta_cap_bp": _POLICY["delta_cap_bp"],
            "required_confirmations": _POLICY["required_confirmations"],
            "minimum_dwell_seconds": _POLICY["minimum_dwell_seconds"],
            "aggregation": _POLICY["aggregation"],
        },
        "event_types_relationship": {
            name: type_counts[name]
            for name in sorted(type_counts)
            if "elationship" in name or "ommitment" in name or name in RELATIONSHIP_EVENT_TYPES
        },
        "event_type_counts_top": type_counts.most_common(40),
        "action_kinds": dict(action_kinds),
        "head": {
            "relationship_state_count": len(states),
            "relationship_signal_count": len(signals),
            "relationship_adjustment_count": len(adjustments),
            "relationship_commitment_count": len(commitments),
            "relationship_proposal_count": len(proposals),
            "acceptance_decision_count": len(decisions),
            "logical_time": head.get("logical_time"),
            "world_revision": head.get("world_revision"),
        },
        "current_states": current_states,
        "adjustment_history": [
            {
                "adjustment_id": item.get("adjustment_id"),
                "operation": item.get("operation"),
                "stage_before": item.get("stage_before"),
                "stage_after": item.get("stage_after"),
                "proposed_deltas": item.get("proposed_deltas"),
                "accepted_deltas": item.get("accepted_deltas"),
                "variables_before": item.get("variables_before"),
                "variables_after": item.get("variables_after"),
                "signal_refs": item.get("signal_refs"),
                "policy_digest": item.get("policy_digest"),
                "adjusted_at": item.get("adjusted_at"),
                "rationale_code": item.get("rationale_code"),
            }
            for item in adjustments
            if isinstance(item, dict)
        ],
        "signals": {
            "accepted": pending_signals,
            "unconsumed": unconsumed,
            "proposed_in_ProposalRecorded": signal_changes,
        },
        "commitments": {
            "accepted": commitments,
            "proposed_in_ProposalRecorded": commitment_changes,
        },
        "relationship_events": [
            {
                "seq": item["seq"],
                "event_type": item["event_type"],
                "logical_time": item["logical_time"],
                "stage_after": item["payload"].get("stage_after") or item["payload"].get("stage"),
                "subject_ref": item["payload"].get("subject_ref"),
                "policy_digest": item["payload"].get("policy_digest"),
                "variables_after": item["payload"].get("variables_after"),
                "accepted_deltas": item["payload"].get("accepted_deltas"),
                "rationale_code": item["payload"].get("rationale_code"),
            }
            for item in relationship_events
        ],
        "genesis_events": genesis_events,
        "authored_in_proposals": proposal_authored,
        "authored_in_model_results": model_us_deltas,
        "we_are_in_model_results": model_we_are,
        "interior": {
            "counts": dict(interior_counts),
            "examples": interior_proposals[:40],
            "total_with_fields": len(interior_proposals),
        },
        "rejections": {
            "events": rejection_events[:80],
            "count": len(rejection_events),
            "acceptance_reasons": dict(acceptance_reasons),
        },
        "silent_failures": silent_failures[:40],
        "drain_or_terminal": drain_errors[:40],
        "usage": usage,
        "classification": classification,
        "eta_to_friend": _eta(current_states, observation_count, first_obs, last_obs),
    }


def _summarize(value: Any) -> Any:
    if isinstance(value, list) and len(value) == 1:
        return _summarize(value[0])
    if isinstance(value, list):
        return [_summarize(item) for item in value[:8]]
    if isinstance(value, dict):
        if len(json.dumps(value, ensure_ascii=False)) > 800:
            return {key: _summarize(item) for key, item in list(value.items())[:12]}
        return value
    if isinstance(value, str) and len(value) > 240:
        return value[:240] + "…"
    return value


def _safe_derive(stage: str, variables: dict[str, int], hysteresis_raw: Any, logical_time_raw: Any):
    hysteresis = RelationshipHysteresisProjection()
    if isinstance(hysteresis_raw, dict):
        try:
            hysteresis = RelationshipHysteresisProjection.model_validate(hysteresis_raw)
        except Exception:
            hysteresis = RelationshipHysteresisProjection()
    logical_time = _parse_dt(logical_time_raw) or datetime.now(timezone.utc)
    try:
        derived, next_h = _derive_stage(
            stage,
            RelationshipVariablesProjection(**variables),
            hysteresis,
            logical_time,
        )
        return derived, next_h.model_dump(mode="json")
    except Exception as exc:
        return stage, {"error": str(exc)}


def _usage(connection: sqlite3.Connection, world_id: str) -> dict[str, Any]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_v2_model_usage'"
    ).fetchone()
    if exists is None:
        return {"recorded": False}
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(world_v2_model_usage)")}
    purpose_col = "purpose" if "purpose" in columns else None
    cost_col = next((name for name in ("cost_cny", "estimated_cny", "cny") if name in columns), None)
    if purpose_col is None:
        return {"recorded": True, "columns": sorted(columns)}
    rows = connection.execute(
        f"SELECT {purpose_col}"
        + (f", SUM({cost_col})" if cost_col else ", COUNT(*)")
        + f", COUNT(*) FROM world_v2_model_usage GROUP BY {purpose_col} ORDER BY COUNT(*) DESC"
    ).fetchall()
    return {
        "recorded": True,
        "by_purpose": [
            {"purpose": row[0], "cost_or_count": row[1], "calls": row[2]} for row in rows[:40]
        ],
    }


def _classify(
    *,
    current_states: list[dict[str, Any]],
    relationship_events: list[dict[str, Any]],
    interior_counts: Counter,
    interior_proposals: list[dict[str, Any]],
    proposal_authored: list[dict[str, Any]],
    signal_changes: list[dict[str, Any]],
    commitment_changes: list[dict[str, Any]],
    rejection_events: list[dict[str, Any]],
    drain_errors: list[dict[str, Any]],
    observation_count: int,
) -> dict[str, Any]:
    labels: list[str] = []
    evidence: list[str] = []
    proposed = (
        interior_counts.get("us_deltas", 0)
        + interior_counts.get("we_are", 0)
        + interior_counts.get("relationship_signal", 0)
        + interior_counts.get("relationship_commitment", 0)
        + len(signal_changes)
        + len(commitment_changes)
        + len(proposal_authored)
    )
    accepted_mutations = [
        item
        for item in relationship_events
        if item["event_type"]
        in {
            "RelationshipSignalAccepted",
            "RelationshipSlowVariableAdjusted",
            "RelationshipCommitmentAccepted",
        }
    ]
    unreadable = [
        item for item in current_states if not item.get("policy", {}).get("readable", True)
    ]
    retired = [
        item
        for item in current_states
        if item.get("policy", {}).get("kind") == "retired_readable"
    ]
    low_axes = [
        item for item in current_states if int(item.get("mean_bp") or 0) < int(_POLICY["enter_bp"]["acquaintance"])
    ]

    if proposed == 0:
        labels.append("a")
        evidence.append(
            "no we_are / us_deltas / relationship_signal / relationship_commitment found in "
            "interior turns, ModelResult, or ProposalRecorded"
        )
    if rejection_events or drain_errors:
        labels.append("b")
        evidence.append(
            f"{len(rejection_events)} rejection-shaped events, {len(drain_errors)} drain/terminal events"
        )
    if accepted_mutations:
        # axes moved at least once historically
        last_mean = current_states[0]["mean_bp"] if current_states else 0
        if last_mean < 500:
            labels.append("c")
            evidence.append(
                f"{len(accepted_mutations)} accepted mutations but current mean is {last_mean} bp"
            )
    elif current_states and low_axes:
        labels.append("c")
        evidence.append("axes exist but sit far below acquaintance enter (2000 mean bp)")
    if current_states:
        gap = current_states[0].get("gap_to_next") or {}
        if int(gap.get("shortfall_mean_bp") or 0) >= 3_000:
            labels.append("d")
            evidence.append(
                f"mean shortfall to {gap.get('next_stage')} is {gap.get('shortfall_mean_bp')} bp"
            )
    if unreadable:
        labels.append("e")
        evidence.append(
            "carried relationship state digest is not installed and not in the retired set"
        )
    elif retired:
        labels.append("e")
        evidence.append(
            "carried relationship state still bears a retired digest; new writes must restamp it"
        )
    if not current_states:
        labels.append("a")
        evidence.append("head relationship_states is empty — genesis never carried a user relationship")
    if not labels:
        labels.append("c")
        evidence.append("fallback: axes exist, no proposals, no rejections")
    return {
        "labels": sorted(set(labels)),
        "evidence": evidence,
        "proposed_count": proposed,
        "accepted_mutation_count": len(accepted_mutations),
        "observation_count": observation_count,
        "interior_counts": dict(interior_counts),
    }


def _eta(
    current_states: list[dict[str, Any]],
    observation_count: int,
    first_obs: str | None,
    last_obs: str | None,
) -> dict[str, Any]:
    if not current_states:
        return {"possible": False, "reason": "no relationship state"}
    state = current_states[0]
    score = int(state["mean_bp"])
    friend_enter = int(_POLICY["enter_bp"]["friend"])
    acquaintance_enter = int(_POLICY["enter_bp"]["acquaintance"])
    cap = int(_POLICY["delta_cap_bp"])
    shortfall_friend = max(0, friend_enter - score)
    shortfall_acq = max(0, acquaintance_enter - score)
    first = _parse_dt(first_obs)
    last = _parse_dt(last_obs)
    days = None
    if first and last and last > first:
        days = max((last - first).total_seconds() / 86_400, 1 / 24)
    turns_per_day = (observation_count / days) if days else None
    # Optimistic: she writes the cap on every axis every inbound turn.
    # Mean then moves by cap each turn.  Realistic: she writes a few axes at
    # a fraction of the cap, and many turns write nothing.
    optimistic_turns_friend = (shortfall_friend + cap - 1) // cap if cap else None
    realistic_delta = 7  # production: +20 trust +20 closeness → +6.67 mean
    realistic_turns_friend = (
        (shortfall_friend + realistic_delta - 1) // realistic_delta if realistic_delta else None
    )
    signals_per_obs = 1 / 62  # one authored signal in 62 epoch2 observations
    result = {
        "current_mean_bp": score,
        "acquaintance_enter_bp": acquaintance_enter,
        "friend_enter_bp": friend_enter,
        "shortfall_to_acquaintance_bp": shortfall_acq,
        "shortfall_to_friend_bp": shortfall_friend,
        "delta_cap_bp_per_axis_per_turn": cap,
        "hysteresis_confirmations": _POLICY["required_confirmations"],
        "hysteresis_dwell_days": _POLICY["minimum_dwell_seconds"] / 86_400,
        "observation_count": observation_count,
        "observed_days": round(days, 2) if days else None,
        "observations_per_day": round(turns_per_day, 2) if turns_per_day else None,
        "optimistic_turns_if_every_turn_hits_cap_on_mean": optimistic_turns_friend,
        "historical_mean_delta_per_signal_bp": realistic_delta,
        "historical_signals_per_observation": round(signals_per_obs, 4),
        "ladder_signals_to_friend_at_historical_delta": realistic_turns_friend,
    }
    if turns_per_day and realistic_turns_friend:
        observations_needed = realistic_turns_friend / signals_per_obs
        result["historical_days_to_friend_at_observed_signal_rate"] = round(
            observations_needed / turns_per_day, 1
        )
        result["historical_years_to_friend"] = round(
            observations_needed / turns_per_day / 365, 1
        )
        result["optimistic_days_if_every_turn_hits_cap"] = round(
            (optimistic_turns_friend or 0) / turns_per_day, 1
        )
        result["plus_hysteresis_dwell_days"] = 2 * (
            _POLICY["minimum_dwell_seconds"] / 86_400
        )
        four_axis_max = (10_000 * 4) // 6
        result["four_axis_saturation_mean_bp"] = four_axis_max
        result["close_friend_reachable_without_reliability_or_repair"] = (
            four_axis_max >= int(_POLICY["enter_bp"]["close_friend"])
        )
    return result


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Production relationship audit")
    lines.append("")
    lines.append(f"- world: `{report['world_id']}`")
    lines.append(f"- events: {report['event_count']}")
    lines.append(f"- inbound observations: {report['observation_count']}")
    lines.append(f"- window: {report['first_observation']} → {report['last_observation']}")
    lines.append(f"- installed digest: `{report['installed_policy_digest']}`")
    lines.append("")
    lines.append("## B. Classification")
    cls = report["classification"]
    lines.append(f"- labels: {', '.join(cls['labels']) or '(none)'}")
    for item in cls["evidence"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## A. Current six-axis state")
    if not report["current_states"]:
        lines.append("Head `relationship_states` is **empty**.")
    for state in report["current_states"]:
        lines.append(f"- subject `{state['subject_ref']}` revision {state['entity_revision']}")
        lines.append(f"- stage **{state['stage']}** (derived `{state['derived_stage']}`)")
        lines.append(f"- mean {state['mean_bp']} bp")
        lines.append(f"- last_adjusted_at {state['last_adjusted_at']}")
        lines.append(f"- policy {state['policy']['kind']} digest `{state['policy']['policy_digest']}` readable={state['policy']['readable']}")
        lines.append("- axes:")
        for name, value in state["variables"].items():
            lines.append(f"  - {name}: {value}")
        gap = state["gap_to_next"]
        lines.append(f"- next: {json.dumps(gap, ensure_ascii=False)}")
    lines.append("")
    lines.append("## Mutations on the ledger")
    lines.append(f"- relationship-typed events: {json.dumps(report['event_types_relationship'], ensure_ascii=False)}")
    lines.append(f"- ActionAuthorized kinds: {json.dumps(report['action_kinds'], ensure_ascii=False)}")
    lines.append(f"- accepted signals in head: {report['head']['relationship_signal_count']}")
    lines.append(f"- adjustments in head: {report['head']['relationship_adjustment_count']}")
    lines.append(f"- commitments in head: {report['head']['relationship_commitment_count']}")
    lines.append("")
    lines.append("## Authored proposals (did she ever ask?)")
    interior = report["interior"]["counts"]
    lines.append(f"- interior turns with relationship fields: {report['interior']['total_with_fields']}")
    lines.append(f"- interior field counts: {json.dumps(interior, ensure_ascii=False)}")
    lines.append(f"- ProposalRecorded with relationship fields: {len(report['authored_in_proposals'])}")
    lines.append(f"- ModelResult with us_deltas/we_are/signal: {len(report['authored_in_model_results'])}")
    lines.append(f"- we_are in ModelResult: {len(report['we_are_in_model_results'])}")
    lines.append(f"- relationship_signal in ProposalRecorded: {len(report['signals']['proposed_in_ProposalRecorded'])}")
    lines.append(f"- relationship_commitment in ProposalRecorded: {len(report['commitments']['proposed_in_ProposalRecorded'])}")
    lines.append("")
    lines.append("## Rejections / silent failures")
    lines.append(f"- rejection-shaped events: {report['rejections']['count']}")
    lines.append(f"- acceptance reasons: {json.dumps(report['rejections']['acceptance_reasons'], ensure_ascii=False)}")
    lines.append(f"- drain/terminal relationship events: {len(report['drain_or_terminal'])}")
    lines.append("")
    lines.append("## ETA to friend")
    lines.append("```json")
    lines.append(json.dumps(report["eta_to_friend"], ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--world-id", default=WORLD_ID)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    connection = _connect(args.db)
    try:
        world_id = args.world_id
        worlds = connection.execute(
            "SELECT world_id, COUNT(*) FROM world_v2_events GROUP BY world_id"
        ).fetchall()
        if world_id not in {row[0] for row in worlds}:
            world_id = _busiest_world(connection)
        report = audit(connection, world_id)
        report["database"] = str(args.db.resolve())
        report["worlds"] = [{"world_id": row[0], "events": row[1]} for row in worlds]
    finally:
        connection.close()
    json_path = args.output_dir / "production_audit.json"
    md_path = args.output_dir / "production_audit.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    md_path.write_text(render_markdown(report))
    print(md_path)
    print(json_path)
    print(json.dumps(report["classification"], ensure_ascii=False, indent=2))
    print(json.dumps({"current_states": report["current_states"], "eta": report["eta_to_friend"]}, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
