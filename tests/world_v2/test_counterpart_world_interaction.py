from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.counterpart_world_interaction import (
    build_dashboard_studio_snapshot,
    create_counterpart_interaction,
    settle_counterpart_interaction_now,
    sync_interactions_with_ledger,
)
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.occurrence_content_coordinator import OccurrenceContentCoordinator
from companion_daemon.world_v2.runtime import WorldRuntime
from companion_daemon.world_v2.schemas import ClockObservation
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_v2_dashboard_ui import DASHBOARD_HTML


def _seed_clock_event(db_path: Path, world_id: str) -> tuple[SQLiteWorldLedger, OccurrenceContentCoordinator]:
    issuer = AcceptedLedgerBatchIssuer()
    ledger = SQLiteWorldLedger(path=db_path, world_id=world_id, accepted_batch_issuer=issuer)
    store = SQLiteImmutableLifeContentStore(path=str(db_path), world_id=world_id)
    coord = OccurrenceContentCoordinator(ledger=ledger, store=store)
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    asyncio.run(
        WorldRuntime(world_id=world_id, ledger=ledger).advance(
            ClockObservation(
                schema_version="world-v2.1",
                tick_id="tick:test:1",
                world_id=world_id,
                logical_time=now,
                created_at=now,
                trace_id="trace:test:1",
                causation_id="scheduler:test:1",
                correlation_id="clock:test",
                logical_time_from=now - timedelta(seconds=10),
                logical_time_to=now,
                reason="test_init",
            )
        )
    )
    return ledger, coord


def test_create_and_fast_settle_instant_delivery_commits_active_occurrence(tmp_path: Path) -> None:
    db_path = tmp_path / "interaction.sqlite"
    world_id = "world:companion-v2:qq-c2c:geoff"
    ledger, coord = _seed_clock_event(db_path, world_id)
    try:
        created = create_counterpart_interaction(
            db_path,
            kind="instant_delivery",
            title="东北菜 · 把子肉盖饭 + 青菜豆腐热汤",
            detail="肥瘦相间把子肉盖饭一份，配卤蛋与热汤",
            sender_note="说了请你就请你，趁热吃",
            target_place="华东师大宿舍楼下",
            eta_minutes=15,
            ledger=ledger,
            occurrence_content=coord,
        )
        assert created["status"] == "in_transit"
        assert created["occurrence_id"] is None

        settled = settle_counterpart_interaction_now(
            db_path,
            interaction_id=created["interaction_id"],
            ledger=ledger,
            occurrence_content=coord,
        )
        assert settled["occurrence_id"] is not None
        assert settled["occurrence_id"].startswith("occurrence:life-aftermath:")

        projected = ledger.project()
        occ = next(
            item
            for item in projected.world_occurrences
            if item.occurrence_id == settled["occurrence_id"]
        )
        assert occ.status == "active"
        assert len(occ.candidate_outcomes) == 3
        assert all(c.causal_authority == "world_contingency" for c in occ.candidate_outcomes)
    finally:
        ledger.close()


def test_sync_companion_parcel_and_unbox_into_world_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "parcel.sqlite"
    world_id = "world:companion-v2:qq-c2c:geoff"
    ledger, coord = _seed_clock_event(db_path, world_id)
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_v2_recall_documents (
                    world_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    memory_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    privacy_rank INTEGER NOT NULL,
                    occurred_from TEXT NOT NULL,
                    occurred_to TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO world_v2_recall_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    world_id,
                    "doc:parcel:1",
                    '{"summary":"洗漱收拾完，抱着封好的纸箱走到校园快递点，排队十来分钟轮到，过秤称重后顺利下单寄出。","source_event_ref":"event:life-aftermath:parcel:1"}',
                    "[]",
                    "experience",
                    "active",
                    1,
                    "2026-11-01T02:27:04Z",
                    "2026-11-01T02:40:00Z",
                ),
            )
            conn.commit()

        items = sync_interactions_with_ledger(db_path, ledger=ledger, occurrence_content=coord)
        parcel = next(
            item
            for item in items
            if item["interaction_id"] == "interaction:companion-parcel:ecnu-book-box-20261101"
        )
        assert parcel["status"] == "ready_for_pickup"

        unboxed = settle_counterpart_interaction_now(
            db_path,
            interaction_id=parcel["interaction_id"],
            receiver_note="拆开看到扉页的字了，选书眼光可以啊",
            ledger=ledger,
            occurrence_content=coord,
        )
        assert unboxed["status"] == "received"
        assert unboxed["occurrence_id"] is not None
        assert "拆开看到扉页的字了" in (unboxed["outcome_text"] or "")

        snap = build_dashboard_studio_snapshot(db_path, ledger=ledger, occurrence_content=coord)
        assert snap["status"] == "ready"
        assert snap["interaction_station"]["summary"]["completed_count"] == 1
    finally:
        ledger.close()


def test_dashboard_html_exposes_inner_drama_cost_panel_and_interaction_station() -> None:
    assert 'id="innerDramaStudio"' in DASHBOARD_HTML
    assert 'id="costMiniGaugePanel"' in DASHBOARD_HTML
    assert 'id="interactionStationPanel"' in DASHBOARD_HTML
    assert 'id="interactionDispatchForm"' in DASHBOARD_HTML
    # Operator-only surfaces stay hidden while presenting.
    assert "body.presenting .operator-only{display:none!important}" in DASHBOARD_HTML


def test_counterpart_interaction_emits_world_consequence_v2_and_consideration_id(
    tmp_path: Path,
) -> None:
    from companion_daemon.world_v2.social_initiative import (
        counterpart_interaction_consideration_id,
        is_shared_outreach_consideration_id,
    )

    db_path = tmp_path / "companion_consequence.sqlite"
    ledger, coord = _seed_clock_event(db_path, "world:companion-v2:qq-c2c:geoff")
    try:
        created = create_counterpart_interaction(
            db_path,
            kind="instant_delivery",
            title="把子肉盖饭 + 青菜豆腐热汤",
            detail="东北菜热汤",
            sender_note="趁热吃",
            target_place="华东师大宿舍楼下",
            eta_minutes=10,
            ledger=ledger,
            occurrence_content=coord,
        )
        record = settle_counterpart_interaction_now(
            db_path,
            interaction_id=created["interaction_id"],
            ledger=ledger,
            occurrence_content=coord,
        )
        occ_id = record["occurrence_id"]
        assert occ_id is not None
        proj = ledger.project()
        occ = next(item for item in proj.world_occurrences if item.occurrence_id == occ_id)
        assert all(c.result_contract == "world-consequence.2" for c in occ.candidate_outcomes)
        cid = counterpart_interaction_consideration_id(
            settlement_event_id="event:life-aftermath:settlement:counterpart-ix-test"
        )
        assert not is_shared_outreach_consideration_id(cid)
    finally:
        ledger.close()

