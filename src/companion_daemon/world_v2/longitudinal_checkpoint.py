"""Restore only a closed capture journey into a new, separately owned database."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class RestoredJourney:
    logical_at: datetime
    delivery_records: tuple[dict, ...]
    provenance: dict


def restore_closed_journey(*, source: Path, destination: Path, started_at: datetime) -> RestoredJourney:
    # Lazy import: the runner owns the existing artifact and cold-replay formats.
    from .longitudinal_journey import CONTRACT, cold_evidence, read_events, read_provider_usage_evidence

    if destination.exists():
        raise ValueError("continuation requires a new database")
    manifest_path = source / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    safety = manifest.get("safety", {})
    if (
        manifest.get("contract") != CONTRACT
        or safety.get("real_qq") is not False
        or safety.get("external_world_feeds") is not False
        or not manifest.get("replay", {}).get("replay_hash_matches")
        or str(manifest.get("stop_reason", "")).startswith("technical_failure:shutdown")
    ):
        raise ValueError("source must be a closed, verified capture journey")
    at = datetime.fromisoformat(manifest["final_logical_time"])
    if at.tzinfo is None or at - timedelta(seconds=manifest["elapsed_logical_seconds"]) != started_at:
        raise ValueError("continuation must retain the original calendar origin")
    artifacts = {}
    for name in ("evidence.jsonl", "timeline.jsonl", "provider-usage.json"):
        raw = (source / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["artifacts"].get(name):
            raise ValueError("closed journey artifact changed")
        artifacts[name] = raw
    events = [json.loads(line) for line in artifacts["evidence.jsonl"].splitlines()]
    usage = json.loads(artifacts["provider-usage.json"])
    deliveries = tuple(
        record
        for line in artifacts["timeline.jsonl"].splitlines()
        for record in json.loads(line).get("deliveries", [])
    )
    history_name = "capture-delivery-history.json"
    if history_name in manifest["artifacts"]:
        history_bytes = (source / history_name).read_bytes()
        if hashlib.sha256(history_bytes).hexdigest() != manifest["artifacts"][history_name]:
            raise ValueError("closed delivery history changed")
        deliveries = tuple(json.loads(history_bytes))
    elif safety.get("fresh_database") is not True:
        raise ValueError("continued source lacks inherited delivery history")
    if any(
        record.get("message_id") != f"journey-message-{index + 1}"
        or not isinstance(record.get("text"), str)
        for index, record in enumerate(deliveries)
    ):
        raise ValueError("captured message identity history is incomplete")
    with sqlite3.connect((source / "world.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(destination) as copied:
            original.backup(copied)
    if read_events(destination) != events:
        raise ValueError("snapshot differs from the closed event history")
    copied_usage = read_provider_usage_evidence(destination)
    if any(copied_usage[key] != usage[key] for key in ("usage_records", "reservation_records")):
        raise ValueError("snapshot differs from the closed billing history")
    cold = cold_evidence(destination)
    projection = cold.projection
    if (
        projection.ledger_sequence != manifest["final_ledger_sequence"]
        or projection.logical_time != at
        or projection.semantic_hash != cold.replay.semantic_hash
        or manifest_path.read_bytes() != manifest_bytes
    ):
        raise ValueError("closed snapshot identity changed")
    return RestoredJourney(
        logical_at=at,
        delivery_records=deliveries,
        provenance={
            "contract": "closed-journey-continuation.1",
            "source_manifest": str(manifest_path.resolve()),
            "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "source_synthetic": manifest["synthetic"],
            "source_final_ledger_sequence": projection.ledger_sequence,
            "source_semantic_hash": projection.semantic_hash,
            "inherited_delivery_count": len(deliveries),
            "inherited_usage_count": len(usage["usage_records"]),
            "inherited_reservation_count": len(usage["reservation_records"]),
            "billing_policy": "all copied usage and reservations retained; caller must admit the continuation allocation",
        },
    )
