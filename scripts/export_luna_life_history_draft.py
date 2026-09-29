#!/usr/bin/env python3
"""Deterministically export the unreviewed Celia life-history draft."""
from __future__ import annotations

import calendar
from datetime import UTC, datetime, time
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "fixtures/world_v2/luna_life_history_expansion_draft.json"
OUTPUT = ROOT / "fixtures/world_v2/luna_life_history_expansion_unreviewed.json"
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
WORLD_STARTED_AT = datetime.fromisoformat("2026-08-13T17:42:55.383658+00:00")
ZONE_NAME = "Asia/Shanghai"
ZONE = ZoneInfo(ZONE_NAME)


def inclusive_interval(time_data: dict[str, str]) -> tuple[datetime, datetime, str]:
    start_text, end_text = time_data["from"], time_data["until"]
    precision = time_data["precision"]
    if precision == "month":
        year, month = map(int, start_text.split("-"))
        if end_text != start_text:
            raise ValueError("month precision requires one shared YYYY-MM value")
        final_day = calendar.monthrange(year, month)[1]
        start = datetime.combine(datetime(year, month, 1).date(), time.min, ZONE)
        end = datetime.combine(datetime(year, month, final_day).date(), time(23, 59, 59), ZONE)
        return start, end, "month"
    if precision == "year_range":
        start_day = datetime.fromisoformat(start_text).date()
        end_day = datetime.fromisoformat(end_text).date()
        start = datetime.combine(start_day, time.min, ZONE)
        end = datetime.combine(end_day, time(23, 59, 59), ZONE)
        return start, end, "interval"
    raise ValueError(f"unsupported source time precision: {precision}")


def statement_for(record: dict[str, object]) -> str:
    parts = [f"事件：{record['event']['factual_detail']}"]
    if record["then_understanding"]:
        parts.append(f"当时感受：{record['then_understanding']}")
    if record["later_understanding"]:
        parts.append(f"后来回看（World 启动前）：{record['later_understanding']}")
    statement = "\n".join(parts)
    if len(statement) > 1600:
        raise ValueError(f"statement exceeds PrehistoryRecord limit: {record['id']}")
    return statement


def export_document(source: dict[str, object]):
    from companion_daemon.world_v2.character_prehistory import (
        HistoricalEntity,
        PrehistoryArchiveDocument,
        PrehistoryRecord,
    )

    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    entity_by_ref = {
        item["entity_ref"]: HistoricalEntity(
            entity_ref=item["entity_ref"], kind="person", label=item["label"]
        )
        for item in source["directory"]["historical_entities"]
    }
    used_refs = {
        ref
        for item in source["records"]
        for ref in item["participant_entity_refs"]
    }
    missing_entities = used_refs - entity_by_ref.keys()
    if missing_entities:
        raise ValueError(f"source record references undeclared entities: {sorted(missing_entities)}")
    records = []
    source_record_ids = {item["id"] for item in source["records"]}
    for item in source["records"]:
        occurred_from, occurred_until, precision = inclusive_interval(item["time"])
        if not occurred_from <= occurred_until or not occurred_until.astimezone(UTC) < WORLD_STARTED_AT:
            raise ValueError(f"record interval is not wholly before World start: {item['id']}")
        if any(ref not in source_record_ids for ref in item["related_record_ids"]):
            raise ValueError(f"related reference does not resolve inside this draft: {item['id']}")
        related = tuple(f"prehistory-record:{ref}" for ref in item["related_record_ids"])
        records.append(PrehistoryRecord(
            record_id=f"prehistory-record:{item['id']}",
            occurred_from=occurred_from,
            occurred_until=occurred_until,
            time_precision=precision,
            timezone_name=ZONE_NAME,
            statement=statement_for(item),
            participant_refs=tuple(item["participant_entity_refs"]),
            location_ref=None,
            related_record_refs=related,
            privacy_class="personal",
        ))

    document = PrehistoryArchiveDocument(
        contract="character-prehistory-archive.1",
        archive_id="prehistory-archive:celia-life-expansion-20260929",
        world_id=WORLD_ID,
        actor_ref=source["actor_ref"],
        source_artifact_ref=(
            "fixtures/world_v2/luna_life_history_expansion_draft.json"
            f"#sha256={source_hash}"
        ),
        entities=tuple(entity_by_ref[ref] for ref in sorted(entity_by_ref)),
        records=tuple(records),
    )
    return document


def main() -> None:
    from companion_daemon.world_v2.character_prehistory import (
        PrehistoryArchiveDocument,
        digest,
    )

    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    document = export_document(source)
    # Validate both the complete document and the manifest contract deterministically.
    parsed = PrehistoryArchiveDocument.model_validate_json(document.model_dump_json())
    manifest = parsed.manifest()
    reparsed = PrehistoryArchiveDocument.model_validate_json(parsed.model_dump_json())
    if digest(manifest) != digest(reparsed.manifest()):
        raise ValueError("manifest changed across deterministic parse")
    OUTPUT.write_text(
        json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output_hash = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"records={len(parsed.records)} entities={len(parsed.entities)}")
    print(f"source_sha256={hashlib.sha256(SOURCE.read_bytes()).hexdigest()}")
    print(f"manifest_sha256={digest(manifest)}")
    print(f"output_sha256={output_hash}")
    print(f"output={OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
