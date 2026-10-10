#!/usr/bin/env python3
"""Audit captured provider inputs and settled usage across ALL purposes.

Read-only inputs, no model calls. Sizes are bytes, never token estimates. Missing
usage stays visible; no prices or assumed cache hit rates are substituted.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import statistics


def _decoded_user_views(body: dict) -> list[dict]:
    views = []
    for message in body.get("messages", []):
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        try:
            view = json.loads(message["content"])
        except json.JSONDecodeError:
            continue
        if isinstance(view, dict):
            views.append(view)
    return views


def audit(captures: Path, usage: Path) -> dict:
    captured_bytes, usage_bytes = captures.read_bytes(), usage.read_bytes()
    bills = json.loads(usage_bytes)
    if not isinstance(bills, list):
        raise ValueError("usage must contain a list of original billing rows")
    by_reservation = {}
    for bill in bills:
        reservation = bill.get("reservation_id")
        if not isinstance(reservation, str) or not reservation or reservation in by_reservation:
            raise ValueError("billing reservation missing or duplicated")
        by_reservation[reservation] = bill
    groups = {}
    matched = set()
    for line in captured_bytes.decode("utf-8").splitlines():
        row = json.loads(line)
        if row.get("kind") != "request":
            continue
        raw = row["model_content_json"]
        if hashlib.sha256(raw.encode()).hexdigest() != row["content_hash"]:
            raise ValueError("captured request hash mismatch")
        body = json.loads(raw)
        reservation = row.get("usage_reservation_id")
        bill = by_reservation.get(reservation)
        if bill is not None:
            if reservation in matched:
                raise ValueError("one bill joined to multiple requests")
            matched.add(reservation)
        purpose = bill["purpose"] if bill is not None else "unmatched_usage"
        key = (str(body.get("model")), purpose)
        group = groups.setdefault(key, {
            "requests": 0, "wire_bytes": [], "statuses": Counter(), "billing_states": Counter(),
            "prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0,
            "cache_miss_tokens": 0, "known_cost_cny": Decimal(0),
            "missing_usage": 0, "unpriced_usage": 0,
            "missing_token_fields": Counter(),
            "observed_snapshots": 0, "observed_selected_recall": 0,
        })
        group["requests"] += 1
        group["wire_bytes"].append(len(raw.encode()))
        views = _decoded_user_views(body)
        snapshots = [view["inner_life_snapshot"] for view in views
                     if isinstance(view.get("inner_life_snapshot"), dict)]
        group["observed_snapshots"] += bool(snapshots)
        group["observed_selected_recall"] += any(
            isinstance(snapshot.get("materials"), dict)
            and "selected_recall" in snapshot["materials"] for snapshot in snapshots)
        if bill is None:
            group["missing_usage"] += 1
            continue
        group["statuses"][bill.get("status", "missing")] += 1
        group["billing_states"][bill.get("billing_state", "missing")] += 1
        for field in ("prompt_tokens", "completion_tokens", "cache_hit_tokens", "cache_miss_tokens"):
            value = bill.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                group[field] += value
            elif value is None:
                group["missing_token_fields"][field] += 1
            else:
                raise ValueError(f"invalid billing {field}")
        if bill.get("billing_state") == "known" and bill.get("cost_cny") is not None:
            cost = Decimal(str(bill["cost_cny"]))
            if not cost.is_finite() or cost < 0:
                raise ValueError("invalid settled cost")
            group["known_cost_cny"] += cost
        else:
            group["unpriced_usage"] += 1
    records = []
    for (model, purpose), group in sorted(groups.items()):
        sizes = group.pop("wire_bytes")
        group["known_cost_cny"] = str(group["known_cost_cny"])
        records.append({"model": model, "purpose": purpose, **group,
                        "wire_bytes_total": sum(sizes), "wire_bytes_median": statistics.median(sizes),
                        "wire_bytes_max": max(sizes)})
    return {
        "contract": "model-context-calls-audit.1",
        "measurement": "original request UTF-8 bytes and settled usage; no token/cost extrapolation",
        "coverage_note": "Observed fields only; opaque/nested payloads are not decoded. Absence does not prove missing evidence.",
        "input_sha256": {"captures": hashlib.sha256(captured_bytes).hexdigest(),
                         "usage": hashlib.sha256(usage_bytes).hexdigest()},
        "captured_requests": sum(row["requests"] for row in records),
        "matched_bills": len(matched),
        "unmatched_bills": len(by_reservation.keys() - matched),
        "missing_usage_requests": sum(row["missing_usage"] for row in records),
        "known_cost_cny": str(sum((Decimal(row["known_cost_cny"]) for row in records), Decimal(0))),
        "purposes": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", required=True, type=Path)
    parser.add_argument("--usage", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve() in {args.captures.resolve(), args.usage.resolve()}:
        raise ValueError("output cannot overwrite an input")
    result = audit(args.captures, args.usage)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in (
        "captured_requests", "matched_bills", "unmatched_bills", "missing_usage_requests", "known_cost_cny",
    )}))


if __name__ == "__main__":
    main()
