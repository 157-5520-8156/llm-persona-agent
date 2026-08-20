"""Path A: compile InnerLifeSnapshot the same way production inbound does."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from companion_daemon.world_v2.biographical_lifecycle import BiographicalLifecycleCatalog
from companion_daemon.world_v2.biographical_timeline_authority import (
    BiographicalTimelineConfiguredPayload,
)
from companion_daemon.world_v2.character_interior import InteriorOpportunity
from companion_daemon.world_v2.character_interior.production import (
    _LedgerCapsuleInteriorProjection,
)
from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy, SliceBudget
from companion_daemon.world_v2.expression_payload_store import SQLiteImmutableExpressionPayloadStore
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.life_content_store import (
    SQLiteImmutableLifeContentStore,
    StoredLifeContent,
    life_content_payload_hash,
)
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.perception_result_context import PerceptionResultContent
from companion_daemon.world_v2.present_prompt import PRESENT_CAPSULE_HARD_MAX_CHARACTERS
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.situation_compiler import SituationCompiler
from companion_daemon.world_v2.epoch_migration_source import default_epoch_archive_path
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

from .types import SeenView


SEED_PATH = Path("configs/world_seed.yaml")


class _ReadOnlyPerceptionReader:
    """Clone-local reader for vision dispatch rows. Never calls a provider."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

    def read_exact(self, *, result_ref: str) -> PerceptionResultContent | None:
        try:
            row = self._conn.execute(
                "SELECT result_ref, result_hash, result_text "
                "FROM world_v2_perception_dispatch WHERE result_ref = ?",
                (result_ref,),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        try:
            return PerceptionResultContent(
                result_ref=str(row["result_ref"]),
                result_hash=str(row["result_hash"]),
                text=str(row["result_text"]),
            )
        except (TypeError, ValueError):
            return None

    def close(self) -> None:
        self._conn.close()


@dataclass
class CompiledSeen:
    seen: SeenView
    model_view: dict[str, Any]
    materials: dict[str, Any]
    snapshot: Any
    cursor: ProjectionCursor
    trigger_ref: str
    compile_ms: float
    projection: Any
    ledger: SQLiteWorldLedger
    stores_to_close: tuple[Any, ...]


def _chat_budget() -> ContextCapsuleBudgetPolicy:
    return ContextCapsuleBudgetPolicy(
        hard_max_characters=PRESENT_CAPSULE_HARD_MAX_CHARACTERS,
        available_capabilities=SliceBudget(max_items=4, max_fields=48, max_characters=1_200),
        action_budget=SliceBudget(max_items=4, max_fields=40, max_characters=1_200),
    )


def _trigger_ref(projection: Any, world_id: str) -> str:
    observations = getattr(projection, "message_observations", ()) or ()
    if observations:
        return str(observations[-1].source_event_id)
    refs = getattr(projection, "committed_world_event_refs", ()) or ()
    if refs:
        return str(refs[-1].event_id)
    raise RuntimeError(f"{world_id} has no committed events to pin a trigger_ref")


def _seed_npc_summaries(
    store: SQLiteImmutableLifeContentStore, seed_path: Path, timezone_name: str
) -> dict[str, str] | None:
    if not seed_path.is_file():
        return None
    catalog = ReviewedLifeSeedCatalog.from_yaml(
        path=seed_path, chronology=LocalChronology(timezone_name)
    )
    summaries: dict[str, str] = {}
    for npc in catalog.reviewed_npcs:
        if npc.identity_summary is None:
            continue
        summaries[npc.stable_identity_ref] = npc.identity_summary
        store.put_if_absent(
            StoredLifeContent(
                content_ref=npc.stable_identity_ref,
                content_kind="provisional_npc_introduction",
                content_payload_hash=life_content_payload_hash(npc.identity_summary),
                text=npc.identity_summary,
            )
        )
    return summaries or None


@dataclass(frozen=True)
class ArchiveLedgerReader:
    """Read-only archive handle for epoch migration source closure."""

    _database_path: Path
    _world_id: str


async def compile_seen_at_head(
    *,
    database: Path,
    world_id: str,
    actor_ref: str,
    counterpart_actor_ref: str,
    timezone_name: str,
    seed_path: Path,
    archive_database: Path | None = None,
) -> CompiledSeen:
    started = time.perf_counter()
    ledger = SQLiteWorldLedger(path=database, world_id=world_id)
    archive_path = archive_database or default_epoch_archive_path(live_database=database)
    archive_ledger = (
        ArchiveLedgerReader(_database_path=archive_path.resolve(), _world_id=world_id)
        if archive_path is not None
        else None
    )
    life_store = SQLiteImmutableLifeContentStore(path=str(database), world_id=world_id)
    expression_store = SQLiteImmutableExpressionPayloadStore(
        path=str(database), world_id=world_id
    )
    perception = _ReadOnlyPerceptionReader(database)
    npc_summaries = _seed_npc_summaries(life_store, seed_path, timezone_name)
    biographical_timeline = (
        BiographicalTimelineConfiguredPayload.from_yaml(
            path=seed_path, timezone_name=timezone_name
        )
        if seed_path.is_file()
        else None
    )
    biographical_catalog = (
        BiographicalLifecycleCatalog.from_yaml(path=seed_path, timezone_name=timezone_name)
        if seed_path.is_file() and biographical_timeline is not None
        else None
    )
    capsules = context_capsule_compiler_from_ledger(
        ledger=ledger,
        situation_compiler=SituationCompiler(local_chronology=LocalChronology(timezone_name)),
        policy=_chat_budget(),
        relevance_scope=ContextRelevanceScope(
            actor_ref=actor_ref,
            related_subject_refs=(counterpart_actor_ref,),
        ),
        life_content_store=life_store,
        perception_result_reader=perception,
        expression_payload_store=expression_store,
        biographical_catalog=biographical_catalog,
        biographical_timezone_name=timezone_name if biographical_catalog is not None else None,
        biographical_timeline=biographical_timeline,
        reviewed_npc_identity_summaries=npc_summaries,
        archive_ledger=archive_ledger,
    )
    projector = _LedgerCapsuleInteriorProjection(
        ledger=ledger,
        capsules=capsules,
        companion_actor_ref=actor_ref,
    )
    projection = await asyncio.to_thread(ledger.project)
    trigger_ref = _trigger_ref(projection, world_id)
    if projection.logical_time is None:
        raise RuntimeError("ledger head has no logical_time")
    cursor = ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )
    opportunity = InteriorOpportunity(
        opportunity_ref="opportunity:context-truth-audit",
        inner_turn_ref="inner-turn:context-truth-audit",
        world_id=world_id,
        actor_ref=actor_ref,
        trigger_ref=trigger_ref,
        cursor=cursor,
        logical_time=projection.logical_time,
        purpose="context_truth_audit",
        source_refs=(trigger_ref,),
    )
    snapshot = await projector.project(subject=opportunity)
    view = snapshot.model_view()
    materials = view.get("materials") if isinstance(view.get("materials"), dict) else {}
    compile_ms = (time.perf_counter() - started) * 1000
    return CompiledSeen(
        seen=SeenView.from_model_view(view),
        model_view=view,
        materials=dict(materials),
        snapshot=snapshot,
        cursor=cursor,
        trigger_ref=trigger_ref,
        compile_ms=compile_ms,
        projection=projection,
        ledger=ledger,
        stores_to_close=(perception, life_store, expression_store, ledger),
    )


def compile_seen_at_head_sync(**kwargs: Any) -> CompiledSeen:
    return asyncio.run(compile_seen_at_head(**kwargs))
