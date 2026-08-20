#!/usr/bin/env python3
"""Trace user fact recall through each context-capsule hop at ledger head."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from companion_daemon.world_v2.context_capsule import (
    ContextCapsuleBudgetPolicy,
    SliceBudget,
    _compile_slice,
)
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    _bounded_domain_items,
    _rank,
    context_capsule_compiler_from_ledger,
    fact_recall_items,
)
from companion_daemon.world_v2.present_prompt import PRESENT_CAPSULE_HARD_MAX_CHARACTERS
from companion_daemon.world_v2.situation_compiler import SituationCompiler
from companion_daemon.world_v2.epoch_migration_source import default_epoch_archive_path
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

WORLD = "world:companion-v2:qq-c2c:geoff"
USER = "user:geoff"


@dataclass(frozen=True)
class ArchiveLedgerReader:
    _database_path: Path
    _world_id: str


def _chat_budget() -> ContextCapsuleBudgetPolicy:
    return ContextCapsuleBudgetPolicy(
        hard_max_characters=PRESENT_CAPSULE_HARD_MAX_CHARACTERS,
        available_capabilities=SliceBudget(max_items=4, max_fields=48, max_characters=1_200),
        action_budget=SliceBudget(max_items=4, max_fields=40, max_characters=1_200),
    )


def _item_size(item) -> int:
    return len(item.model_dump_json())


def _fact_row(item, logical_time: datetime | None, query_text: str = "") -> dict:
    return {
        "fact_id": item.fact_id,
        "predicate_code": item.predicate_code,
        "source_excerpt": (item.source_excerpt or "")[:120],
        "confidence_bp": item.confidence_bp,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "committed_at": item.committed_at.isoformat() if item.committed_at else None,
        "occurred_at": item.occurred_at.isoformat() if item.occurred_at else None,
        "rank_score_bp": _rank("relevant_facts", item, logical_time, query_text),
        "serialized_chars": _item_size(item),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "companion.epoch2.sqlite")
    parser.add_argument("--archive", type=Path, default=ROOT / "data" / "companion.epoch1.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "facts-reaching-her")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    clone = args.output / "clone-readonly.sqlite"
    if clone.exists():
        clone.unlink()
    shutil.copy2(args.database, clone)

    ledger = SQLiteWorldLedger(path=clone, world_id=WORLD)
    archive_path = args.archive if args.archive.is_file() else default_epoch_archive_path(clone)
    archive = (
        ArchiveLedgerReader(_database_path=archive_path.resolve(), _world_id=WORLD)
        if archive_path is not None and Path(archive_path).is_file()
        else None
    )
    projection = ledger.project()
    logical_time = projection.logical_time
    user_facts = [
        item
        for item in projection.facts
        if item.values.subject_ref == USER and item.values.status == "active"
    ]
    recalls = fact_recall_items(
        ledger=ledger,
        projection=projection,
        facts=tuple(user_facts),
        archive_ledger=archive,
    )
    ranked = _bounded_domain_items("relevant_facts", recalls, logical_time, query_text="")
    assert ranked is not None

    trigger_ref = (
        str(projection.message_observations[-1].source_event_id)
        if projection.message_observations
        else str(projection.committed_world_event_refs[-1].event_id)
    )
    query = query_from_projection(
        projection,
        actor_ref="agent:companion",
        trigger_ref=trigger_ref,
    )
    policy = _chat_budget()
    compiler = context_capsule_compiler_from_ledger(
        ledger=ledger,
        situation_compiler=SituationCompiler(local_chronology=LocalChronology("Asia/Shanghai")),
        policy=policy,
        relevance_scope=ContextRelevanceScope(
            actor_ref="agent:companion",
            related_subject_refs=(USER,),
        ),
        archive_ledger=archive,
    )
    capsule = compiler.compile(query)
    _, request = compiler._resolve(query)  # noqa: SLF001 - audit-only introspection
    slice_pre_global, slice_log = _compile_slice(
        slice_name="relevant_facts",
        bound=request.relevant_facts,
        limit=policy.relevant_facts,
    )

    fact_slice = capsule.relevant_facts
    truncation = [
        entry.model_dump(mode="json")
        for entry in capsule.budget.truncation_log
        if entry.slice_name == "relevant_facts"
    ]
    global_omissions = sum(
        entry.omitted_count
        for entry in capsule.budget.truncation_log
        if entry.slice_name == "relevant_facts" and entry.reason == "global_character_budget"
    )

    hop1 = [_fact_row(item, logical_time) for item in recalls]
    hop2 = [_fact_row(item, logical_time) for item in ranked]
    hop3 = []
    if request.relevant_facts is not None and request.relevant_facts.value:
        hop3 = [_fact_row(item, logical_time) for item in request.relevant_facts.value if hasattr(item, "fact_id")]
    hop4 = []
    for item in fact_slice.items:
        payload = json.loads(item.payload_json)
        hop4.append(
            {
                "item_ref": item.item_ref,
                "predicate_code": payload.get("predicate_code"),
                "source_excerpt": (payload.get("source_excerpt") or "")[:120],
                "rank_score_bp": item.rank_score_bp,
                "payload_chars": item.character_count,
            }
        )

    payload = {
        "head_seq": projection.ledger_sequence,
        "logical_time": logical_time.isoformat() if logical_time else None,
        "counts": {
            "active_user_facts": len(user_facts),
            "fact_recall_items": len(recalls),
            "bounded_domain_items": len(ranked),
            "resolver_relevant_facts": len(hop3),
            "capsule_items": len(hop4),
        },
        "limits": {
            "slice_budget": policy.relevant_facts.model_dump(mode="json"),
            "hard_max_characters": policy.hard_max_characters,
            "minimum_retained_items_relevant_facts": 2,
        },
        "truncation_log_relevant_facts": truncation,
        "global_omissions_relevant_facts": global_omissions,
        "slice_budget_used": fact_slice.budget.model_dump(mode="json"),
        "hop1_fact_recall_items_top10": hop1[:10],
        "hop1_fact_recall_items_bottom10": hop1[-10:],
        "hop1_display_name": next((row for row in hop1 if row["predicate_code"] == "profile.display_name"), None),
        "hop2_bounded_ranked_top10": hop2[:10],
        "hop2_display_name_rank": next(
            (index for index, row in enumerate(hop2) if row["predicate_code"] == "profile.display_name"),
            None,
        ),
        "hop3_resolver_count": len(hop3),
        "hop3_slice_compile_items": len(slice_pre_global.items),
        "hop3_slice_compile_truncation": [entry.model_dump(mode="json") for entry in slice_log],
        "hop3_slice_compile_top10": [
            {
                "item_ref": item.item_ref,
                "predicate_code": json.loads(item.payload_json).get("predicate_code"),
                "rank_score_bp": item.rank_score_bp,
                "payload_chars": item.character_count,
            }
            for item in slice_pre_global.items[:10]
        ],
        "hop4_capsule_items": hop4,
        "all_hop1_by_rank": sorted(hop1, key=lambda row: -row["rank_score_bp"]),
    }
    (args.output / "trace.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
