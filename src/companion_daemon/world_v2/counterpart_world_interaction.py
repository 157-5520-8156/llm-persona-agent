"""Bidirectional counterpart world interaction & studio snapshot for World v2.

Provides:
1. A durable counterpart interaction station (`world_v2_counterpart_interactions`)
   covering:
   - ``instant_delivery`` (user -> companion takeaway / drinks / errand)
   - ``counterpart_parcel`` (user -> companion mailed parcel / gift)
   - ``companion_parcel`` (companion -> user mailed parcel / letter, synced from
     committed ledger experiences and ready for user unboxing)
2. Native World v2 Event Machine integration via ``OccurrenceContentCoordinator``
   and ``WorldOccurrenceActivatedPayload`` (`occurrence:life-aftermath:*` with
   weighted ``world_contingency`` environmental candidates), settled by
   ``LifeAftermathRuntime`` with full source closure and zero scripted character
   reactions.
3. The Studio snapshot (`build_dashboard_studio_snapshot`) powering the C-position
   Inner Drama Studio (`private_turn_state` side-by-side with user message and
   delivered bubbles, live affect episodes, appraisals, private impressions,
   active revisit/response intentions) and the C-position Cost Mini-Gauge.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from .accepted_ledger_batch import AcceptedLedgerBatchIssuer
from .dashboard_cost_meter import build_dashboard_cost_snapshot
from .event_identity import domain_idempotency_key
from .life_content_store import (
    SQLiteImmutableLifeContentStore,
    life_content_payload_hash,
)
from .life_events import WorldOccurrenceActivatedPayload
from .occurrence_content_coordinator import (
    OccurrenceContentCommitRequest,
    OccurrenceContentCoordinator,
    OutcomeCandidateContent,
)
from .schemas import (
    DueWindow,
    EvidenceRef,
    ProjectionCursor,
    WorldEvent,
    WorldOccurrenceProjection,
)
from .sqlite_ledger import SQLiteWorldLedger

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

_AFFECT_LABELS: dict[str, str] = {
    "warmth": "暖意",
    "joy": "开心",
    "sadness": "低落",
    "hurt": "委屈",
    "anger": "生气",
    "loneliness": "孤单",
    "anxiety": "挂心",
    "resentment": "别扭",
}

_REVISIT_KIND_LABELS: dict[str, str] = {
    "topic_open": "话还没聊透",
    "question_pending": "等着你的回答",
    "repair_open": "想把刚才的话说圆",
    "external_result_pending": "等着外面的结果",
    "coordination_pending": "等着和你对一下时间",
    "reply_reconsideration": "还在想要不要再补一句",
}

_INTERACTION_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "preset_id": "takeaway_bazirou",
        "kind": "instant_delivery",
        "label": "🍱 东北菜 · 把子肉盖饭 + 热汤",
        "title": "东北菜 · 把子肉盖饭 + 青菜豆腐热汤",
        "detail": "肥瘦相间的把子肉盖饭一份，配卤蛋与热乎的青菜豆腐汤，骑手送到宿舍楼下外卖柜",
        "sender_note": "说了请你就请你，别净吃青菜豆腐了，趁热吃",
        "target_place": "华东师大宿舍楼下",
        "eta_minutes": 15,
    },
    {
        "preset_id": "drink_osmanthus_latte",
        "kind": "instant_delivery",
        "label": "☕ 热桂花燕麦拿铁 + 小点心",
        "title": "热桂花燕麦拿铁（去冰微甜）+ 栗子小蛋糕",
        "detail": "保温袋装的热桂花燕麦拿铁一杯，配一块栗子奶油小方，骑手送到图书馆/宿舍楼下",
        "sender_note": "看书或者改稿子累了垫垫肚子",
        "target_place": "华东师大校园配送点",
        "eta_minutes": 15,
    },
    {
        "preset_id": "parcel_care_box",
        "kind": "counterpart_parcel",
        "label": "📮 从成都寄出的随手礼盒",
        "title": "从成都寄往华东师大的小纸箱",
        "detail": "一盒成都特产小零食与手写便签，寄到华东师大校园快递驿站",
        "sender_note": "礼尚往来，到了驿站记得去取",
        "target_place": "华东师大校园快递驿站",
        "eta_minutes": 30,
    },
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _format_cn_time(value: str | None) -> str:
    dt = _parse_iso(value)
    if dt is None:
        return value or "—"
    local = dt.astimezone(_SHANGHAI_TZ)
    return local.strftime("%m-%d %H:%M")


def _digest(parts: list[str]) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS world_v2_counterpart_interactions (
            interaction_id TEXT PRIMARY KEY,
            world_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            direction TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT NOT NULL,
            sender_note TEXT NOT NULL DEFAULT '',
            receiver_note TEXT NOT NULL DEFAULT '',
            target_place TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            eta_at TEXT NOT NULL,
            settled_at TEXT,
            occurrence_id TEXT,
            source_event_ref TEXT,
            outcome_text TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_world_v2_counterpart_interactions_status
        ON world_v2_counterpart_interactions(world_id, status, created_at DESC)
        """
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _detect_world_id(conn: sqlite3.Connection) -> str:
    if _table_exists(conn, "world_v2_heads"):
        row = conn.execute(
            "SELECT world_id FROM world_v2_heads ORDER BY ledger_sequence DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    if _table_exists(conn, "world_v2_events"):
        row = conn.execute(
            "SELECT world_id FROM world_v2_events ORDER BY ledger_sequence DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    return "world:companion-v2:qq-c2c:user"


def _build_candidate_outcomes(
    *,
    suffix: str,
    kind: str,
    title: str,
    detail: str,
    sender_note: str,
    receiver_note: str,
    target_place: str,
) -> tuple[OutcomeCandidateContent, ...]:
    place_label = (target_place or "华东师大宿舍楼下").strip()
    clean_title = title.strip()
    clean_detail = detail.strip()
    detail_clause = f"（{clean_detail}）" if clean_detail and clean_detail != clean_title else ""
    note_clause = f"，附言写着「{sender_note.strip()}」" if sender_note.strip() else ""

    if kind == "companion_parcel":
        feedback_clause = (
            f"，附有签收留言：「{receiver_note.strip()}」"
            if receiver_note.strip()
            else ""
        )
        raw_candidates: tuple[tuple[str, str, int], ...] = (
            (
                "signed_unboxed",
                f"手机快递物流弹出签收提醒：此前寄出的包裹「{clean_title}」已在{place_label}由收件人本人签收并拆封纸箱{feedback_clause}。",
                100,
            ),
        )
    elif kind == "counterpart_parcel":
        raw_candidates = (
            (
                "station_intact",
                f"校园快递驿站发来取件码通知，在{place_label}收到对方寄来的包裹「{clean_title}」{detail_clause}{note_clause}，纸箱封口严实完好。",
                80,
            ),
            (
                "station_heavy",
                f"收到校园快递驿站取件通知，在{place_label}领回对方寄来的包裹「{clean_title}」{detail_clause}{note_clause}，纸箱抱在手里比预想的更有分量。",
                14,
            ),
            (
                "station_dent",
                f"从{place_label}取到对方寄来的包裹「{clean_title}」{detail_clause}{note_clause}；长途运输让纸箱外角压出一道折痕，但里层缓冲包装完好。",
                6,
            ),
        )
    else:
        raw_candidates = (
            (
                "timely_warm",
                f"骑手打来电话通知外卖已送到{place_label}，保温袋里装着对方点过来的「{clean_title}」{detail_clause}{note_clause}，包装完好，拿在手里还带着刚出锅的热乎气。",
                78,
            ),
            (
                "slight_rush",
                f"骑手一路小跑把外卖送到{place_label}，袋子里是对方点过来的「{clean_title}」{detail_clause}{note_clause}；路上绕了一小段稍微晚了几分钟，但餐盒依旧温热。",
                15,
            ),
            (
                "minor_splash",
                f"外卖送到{place_label}，里面是对方点过来的「{clean_title}」{detail_clause}{note_clause}；路上颠簸让外层纸袋边角沾了一点汤汁，不过里面的主食与餐盒封口完好。",
                7,
            ),
        )

    return tuple(
        OutcomeCandidateContent(
            candidate_result_ref=f"candidate:life-aftermath:{suffix}:{cid}",
            result_id=f"result:life-aftermath:{suffix}:{cid}",
            result_payload_ref=f"content:occurrence-result:{suffix}:{cid}",
            result_payload_hash=life_content_payload_hash(
                json.dumps(
                    {
                        "contract": "world-consequence.2",
                        "environment_text": text[:12000],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
            privacy_class="shareable",
            content_ref=f"content:outcome-candidate:{suffix}:{cid}",
            text=json.dumps(
                {
                    "contract": "world-consequence.2",
                    "environment_text": text[:12000],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            result_contract="world-consequence.2",
            causal_authority="world_contingency",
            relative_plausibility_weight=weight,
        )
        for cid, text, weight in raw_candidates
    )


def _open_ledger_and_coordinator(
    db_path: str | Path,
    *,
    world_id: str,
    ledger: Any = None,
    occurrence_content: Any = None,
) -> tuple[Any, Any, bool]:
    if ledger is not None and occurrence_content is not None:
        return ledger, occurrence_content, False
    issuer = AcceptedLedgerBatchIssuer()
    owned_ledger = SQLiteWorldLedger(
        path=Path(db_path),
        world_id=world_id,
        accepted_batch_issuer=issuer,
    )
    life_store = SQLiteImmutableLifeContentStore(path=str(db_path), world_id=world_id)
    owned_coord = OccurrenceContentCoordinator(ledger=owned_ledger, store=life_store)
    return owned_ledger, owned_coord, True


def _commit_and_activate_interaction_occurrence(
    db_path: str | Path,
    *,
    interaction_id: str,
    world_id: str,
    kind: str,
    title: str,
    detail: str,
    sender_note: str,
    receiver_note: str,
    target_place: str,
    ledger: Any = None,
    occurrence_content: Any = None,
) -> str | None:
    suffix = "counterpart-ix-" + _digest([interaction_id, kind, title])
    occurrence_id = "occurrence:life-aftermath:" + suffix

    active_ledger, coord, owns_ledger = _open_ledger_and_coordinator(
        db_path,
        world_id=world_id,
        ledger=ledger,
        occurrence_content=occurrence_content,
    )
    try:
        projected = active_ledger.project()
        if not projected.committed_world_event_refs:
            return None
        existing = next(
            (item for item in projected.world_occurrences if item.occurrence_id == occurrence_id),
            None,
        )
        logical_time = projected.logical_time or _utc_now()
        wake_ref = projected.committed_world_event_refs[-1]
        wake_evidence = EvidenceRef(
            ref_id=wake_ref.event_id,
            evidence_type="committed_world_event",
            claim_purpose="life_transition",
            source_world_revision=wake_ref.world_revision,
            immutable_hash=wake_ref.payload_hash,
        )
        location_ref = "place:ecnu-dorm"
        for loc in getattr(projected, "locations", ()):
            if getattr(loc, "actor_ref", None) in {"agent:companion", "npc:companion"} and getattr(loc, "location_ref", None):
                location_ref = loc.location_ref
                break

        candidate_contents = _build_candidate_outcomes(
            suffix=suffix,
            kind=kind,
            title=title,
            detail=detail,
            sender_note=sender_note,
            receiver_note=receiver_note,
            target_place=target_place,
        )
        trace_id = f"trace:counterpart-interaction:{suffix}"
        correlation_id = f"counterpart-interaction:{interaction_id}"

        if existing is None:
            occurrence = WorldOccurrenceProjection(
                occurrence_id=occurrence_id,
                entity_revision=1,
                trigger_ref=wake_ref.event_id,
                participant_refs=("agent:companion", "user:geoff"),
                location_ref=location_ref,
                time_window=DueWindow(
                    opens_at=logical_time - timedelta(seconds=10),
                    closes_at=logical_time + timedelta(seconds=1),
                ),
                candidate_outcome_refs=tuple(
                    item.candidate_result_ref for item in candidate_contents
                ),
                visibility="shareable",
                status="committed",
            )
            coord.commit(
                OccurrenceContentCommitRequest(
                    world_id=active_ledger.world_id,
                    occurrence=occurrence,
                    candidate_contents=candidate_contents,
                    change_id="change:life-aftermath:occurrence:" + suffix,
                    transition_id="transition:life-aftermath:occurrence:" + suffix,
                    evidence_refs=(wake_evidence,),
                    policy_refs=("policy:life-aftermath.1",),
                    logical_time=logical_time,
                    created_at=logical_time,
                    actor="worker:life-aftermath",
                    source="world-v2:life-aftermath",
                    trace_id=trace_id,
                    causation_id=wake_ref.event_id,
                    correlation_id=correlation_id,
                )
            )
            projected = active_ledger.project()
            existing = next(
                (item for item in projected.world_occurrences if item.occurrence_id == occurrence_id),
                None,
            )

        if existing is not None and existing.status == "committed":
            wake_ref = projected.committed_world_event_refs[-1]
            wake_evidence = EvidenceRef(
                ref_id=wake_ref.event_id,
                evidence_type="committed_world_event",
                claim_purpose="life_transition",
                source_world_revision=wake_ref.world_revision,
                immutable_hash=wake_ref.payload_hash,
            )
            payload = WorldOccurrenceActivatedPayload(
                change_id="change:life-aftermath:activate:" + suffix,
                transition_id="transition:life-aftermath:activate:" + suffix,
                expected_entity_revision=existing.entity_revision,
                evidence_refs=(wake_evidence,),
                policy_refs=("policy:life-aftermath.1",),
                occurrence_id=occurrence_id,
                activated_at=logical_time - timedelta(seconds=5),
                satisfied_precondition_refs=(),
            ).model_dump(mode="json")
            activate_event = WorldEvent.from_payload(
                schema_version="world-v2.1",
                event_id="event:life-aftermath:activate:" + suffix,
                world_id=active_ledger.world_id,
                event_type="WorldOccurrenceActivated",
                logical_time=logical_time,
                created_at=logical_time,
                actor="worker:life-aftermath",
                source="world-v2:life-aftermath",
                trace_id=trace_id,
                causation_id=wake_ref.event_id,
                correlation_id=correlation_id,
                idempotency_key=domain_idempotency_key(
                    event_type="WorldOccurrenceActivated",
                    world_id=active_ledger.world_id,
                    payload=payload,
                ),
                payload=payload,
            )
            active_ledger.commit_at_cursor(
                (activate_event,),
                expected_cursor=ProjectionCursor(
                    world_revision=projected.world_revision,
                    deliberation_revision=projected.deliberation_revision,
                    ledger_sequence=projected.ledger_sequence,
                ),
                commit_id="commit:life-aftermath:activate:" + suffix,
            )
        return occurrence_id
    finally:
        if owns_ledger:
            close_fn = getattr(active_ledger, "close", None)
            if callable(close_fn):
                close_fn()


def sync_interactions_with_ledger(
    db_path: str | Path,
    *,
    ledger: Any = None,
    occurrence_content: Any = None,
) -> list[dict[str, Any]]:
    """Sync companion-sent parcels, dispatch due in-transit orders, and reconcile settled outcomes."""

    path = Path(db_path)
    if not path.exists():
        return []

    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        world_id = _detect_world_id(conn)

        # 1. Seed companion -> user parcels from committed recall documents / experiences
        if _table_exists(conn, "world_v2_recall_documents"):
            docs = conn.execute(
                """
                SELECT document_id, document_json, occurred_from
                FROM world_v2_recall_documents
                WHERE memory_kind = 'experience'
                  AND document_json LIKE '%下单寄出%'
                ORDER BY occurred_from ASC
                """
            ).fetchall()
            for doc in docs:
                try:
                    dj = json.loads(doc["document_json"])
                except Exception:
                    dj = {}
                text = str(dj.get("summary") or dj.get("text") or doc["document_json"] or "")
                source_ref = str(dj.get("source_event_ref") or doc["document_id"] or "")
                occurred_at = str(doc["occurred_from"] or _iso_utc(_utc_now()))
                if "抱着封好的纸箱走到校园快递点" in text:
                    ix_id = "interaction:companion-parcel:ecnu-book-box-20261101"
                    title = "从华东师大寄往成都的封箱旧书"
                    detail = (
                        "沈知栀在旧书市场收尾摊挑中、回宿舍亲手封好纸箱并去校园快递点过秤寄出的书"
                        "（她说书名先留个悬念，等着看你拆箱时的反应）"
                    )
                    sender_note = "书我挑好了，寄到成都你自己拆，别到时候又只说一句太御了"
                    target_place = "成都 · 你的收件驿站"
                else:
                    ix_id = "interaction:companion-parcel:" + _digest([source_ref, text])
                    title = "沈知栀从校园快递点寄出的包裹"
                    detail = text
                    sender_note = "寄出去了，到了记得拆"
                    target_place = "你的收件驿站"

                exists = conn.execute(
                    "SELECT 1 FROM world_v2_counterpart_interactions WHERE interaction_id = ?",
                    (ix_id,),
                ).fetchone()
                if not exists:
                    created_dt = _parse_iso(occurred_at) or _utc_now()
                    eta_dt = created_dt + timedelta(hours=30)
                    conn.execute(
                        """
                        INSERT INTO world_v2_counterpart_interactions (
                            interaction_id, world_id, kind, direction, title, detail,
                            sender_note, receiver_note, target_place, status,
                            created_at, eta_at, settled_at, occurrence_id, source_event_ref, outcome_text
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ix_id,
                            world_id,
                            "companion_parcel",
                            "companion_to_user",
                            title,
                            detail,
                            sender_note,
                            "",
                            target_place,
                            "ready_for_pickup",
                            _iso_utc(created_dt),
                            _iso_utc(eta_dt),
                            None,
                            None,
                            source_ref,
                            None,
                        ),
                    )
            conn.commit()

        # 2. Check due in_transit interactions that have not yet committed a WorldOccurrence
        now_dt = _utc_now()
        due_rows = conn.execute(
            """
            SELECT * FROM world_v2_counterpart_interactions
            WHERE status = 'in_transit' AND (occurrence_id IS NULL OR occurrence_id = '')
            """
        ).fetchall()
        conn.close()

        for row in due_rows:
            eta_dt = _parse_iso(row["eta_at"])
            if eta_dt is not None and eta_dt <= now_dt:
                try:
                    occ_id = _commit_and_activate_interaction_occurrence(
                        path,
                        interaction_id=str(row["interaction_id"]),
                        world_id=str(row["world_id"] or world_id),
                        kind=str(row["kind"]),
                        title=str(row["title"]),
                        detail=str(row["detail"]),
                        sender_note=str(row["sender_note"] or ""),
                        receiver_note=str(row["receiver_note"] or ""),
                        target_place=str(row["target_place"] or ""),
                        ledger=ledger,
                        occurrence_content=occurrence_content,
                    )
                except Exception:
                    occ_id = None
                if occ_id:
                    with sqlite3.connect(str(path), timeout=10.0) as wconn:
                        wconn.execute(
                            """
                            UPDATE world_v2_counterpart_interactions
                            SET occurrence_id = ?
                            WHERE interaction_id = ?
                            """,
                            (occ_id, str(row["interaction_id"])),
                        )
                        wconn.commit()

        # 3. Reconcile occurrences that have been settled by LifeAftermathRuntime
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        occurrences_by_id: dict[str, dict[str, Any]] = {}
        if _table_exists(conn, "world_v2_head_state_items"):
            occ_rows = conn.execute(
                """
                SELECT item_json FROM world_v2_head_state_items
                WHERE field = 'world_occurrences'
                """
            ).fetchall()
            for orow in occ_rows:
                try:
                    item = json.loads(orow["item_json"])
                    if isinstance(item, dict) and item.get("occurrence_id"):
                        occurrences_by_id[str(item["occurrence_id"])] = item
                except Exception:
                    continue

        pending_occ_rows = conn.execute(
            """
            SELECT * FROM world_v2_counterpart_interactions
            WHERE occurrence_id IS NOT NULL AND occurrence_id != ''
              AND (outcome_text IS NULL OR outcome_text = '' OR status = 'in_transit')
            """
        ).fetchall()
        for row in pending_occ_rows:
            occ_id = str(row["occurrence_id"])
            occ = occurrences_by_id.get(occ_id)
            if not occ or occ.get("status") != "settled":
                continue
            result_payload_ref = occ.get("result_payload_ref")
            outcome_text = None
            for content_table in ("world_v2_life_content", "world_v2_immutable_life_content"):
                if not _table_exists(conn, content_table):
                    continue
                if result_payload_ref and not outcome_text:
                    crow = conn.execute(
                        f"SELECT text FROM {content_table} WHERE content_ref = ? LIMIT 1",
                        (str(result_payload_ref),),
                    ).fetchone()
                    if crow and crow["text"]:
                        outcome_text = str(crow["text"])
                if not outcome_text:
                    suffix = occ_id.removeprefix("occurrence:life-aftermath:")
                    crow = conn.execute(
                        f"SELECT text FROM {content_table} WHERE content_ref LIKE ? LIMIT 1",
                        (f"content:outcome-candidate:{suffix}:%",),
                    ).fetchone()
                    if crow and crow["text"]:
                        outcome_text = str(crow["text"])
            if outcome_text and outcome_text.strip().startswith("{"):
                try:
                    parsed_outcome = json.loads(outcome_text)
                    if isinstance(parsed_outcome, dict) and parsed_outcome.get("environment_text"):
                        outcome_text = str(parsed_outcome["environment_text"])
                except Exception:
                    pass
            new_status = "received" if row["kind"] == "companion_parcel" else "delivered"
            settled_at = str(occ.get("settled_at") or row["settled_at"] or _iso_utc(_utc_now()))
            conn.execute(
                """
                UPDATE world_v2_counterpart_interactions
                SET status = ?, settled_at = ?, outcome_text = COALESCE(?, outcome_text)
                WHERE interaction_id = ?
                """,
                (new_status, settled_at, outcome_text, str(row["interaction_id"])),
            )
        conn.commit()

        all_rows = conn.execute(
            """
            SELECT * FROM world_v2_counterpart_interactions
            ORDER BY
                CASE status
                    WHEN 'ready_for_pickup' THEN 0
                    WHEN 'in_transit' THEN 1
                    ELSE 2
                END ASC,
                created_at DESC
            """
        ).fetchall()
        now_iso = _iso_utc(_utc_now())
        items: list[dict[str, Any]] = []
        for row in all_rows:
            created_dt = _parse_iso(row["created_at"])
            eta_dt = _parse_iso(row["eta_at"])
            remaining_seconds = 0
            progress_pct = 100
            if row["status"] == "in_transit" and created_dt and eta_dt:
                total_sec = max(1.0, (eta_dt - created_dt).total_seconds())
                elapsed_sec = max(0.0, (_utc_now() - created_dt).total_seconds())
                remaining_seconds = max(0, int((eta_dt - _utc_now()).total_seconds()))
                progress_pct = min(98, max(5, int(round((elapsed_sec / total_sec) * 100))))
            elif row["status"] == "ready_for_pickup":
                progress_pct = 90
            items.append(
                {
                    "interaction_id": str(row["interaction_id"]),
                    "world_id": str(row["world_id"]),
                    "kind": str(row["kind"]),
                    "kind_label": {
                        "instant_delivery": "即时外卖 / 跑腿",
                        "counterpart_parcel": "寄给她的快递",
                        "companion_parcel": "她寄给你的包裹",
                    }.get(str(row["kind"]), str(row["kind"])),
                    "direction": str(row["direction"]),
                    "title": str(row["title"]),
                    "detail": str(row["detail"]),
                    "sender_note": str(row["sender_note"] or ""),
                    "receiver_note": str(row["receiver_note"] or ""),
                    "target_place": str(row["target_place"] or ""),
                    "status": str(row["status"]),
                    "status_label": {
                        "ready_for_pickup": "已到驿站 · 待你签收拆箱",
                        "in_transit": "配送在途 · 事件机计时中",
                        "delivered": "已送达 · 已记入世界账本",
                        "received": "已签收拆箱 · 已回传世界账本",
                    }.get(str(row["status"]), str(row["status"])),
                    "created_at": str(row["created_at"]),
                    "created_at_ formatted": _format_cn_time(row["created_at"]),
                    "created_at_display": _format_cn_time(row["created_at"]),
                    "eta_at": str(row["eta_at"]),
                    "eta_at_display": _format_cn_time(row["eta_at"]),
                    "settled_at": str(row["settled_at"]) if row["settled_at"] else None,
                    "settled_at_display": _format_cn_time(row["settled_at"]) if row["settled_at"] else None,
                    "occurrence_id": str(row["occurrence_id"]) if row["occurrence_id"] else None,
                    "source_event_ref": str(row["source_event_ref"]) if row["source_event_ref"] else None,
                    "outcome_text": str(row["outcome_text"]) if row["outcome_text"] else None,
                    "remaining_seconds": remaining_seconds,
                    "progress_pct": progress_pct,
                    "captured_at": now_iso,
                }
            )
        return items
    finally:
        try:
            conn.close()
        except Exception:
            pass


def create_counterpart_interaction(
    db_path: str | Path,
    *,
    kind: str,
    title: str,
    detail: str = "",
    sender_note: str = "",
    target_place: str = "",
    eta_minutes: int = 15,
    ledger: Any = None,
    occurrence_content: Any = None,
) -> dict[str, Any]:
    """Create a user -> companion delivery or parcel and optionally dispatch immediately."""

    normalized_kind = (kind or "instant_delivery").strip()
    if normalized_kind not in {"instant_delivery", "counterpart_parcel"}:
        raise ValueError(f"unsupported counterpart interaction kind: {normalized_kind}")
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("interaction title is required")
    clean_detail = (detail or clean_title).strip()
    clean_note = (sender_note or "").strip()
    clean_place = (
        (target_place or "").strip()
        or ("华东师大校园快递驿站" if normalized_kind == "counterpart_parcel" else "华东师大宿舍楼下")
    )
    bounded_eta = max(0, min(2880, int(eta_minutes)))

    now_dt = _utc_now()
    eta_dt = now_dt + timedelta(minutes=bounded_eta)
    interaction_id = f"interaction:{normalized_kind}:{_digest([clean_title, clean_detail, _iso_utc(now_dt)])}"

    path = Path(db_path)
    with sqlite3.connect(str(path), timeout=10.0) as conn:
        _ensure_schema(conn)
        world_id = _detect_world_id(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO world_v2_counterpart_interactions (
                interaction_id, world_id, kind, direction, title, detail,
                sender_note, receiver_note, target_place, status,
                created_at, eta_at, settled_at, occurrence_id, source_event_ref, outcome_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                interaction_id,
                world_id,
                normalized_kind,
                "user_to_companion",
                clean_title,
                clean_detail,
                clean_note,
                "",
                clean_place,
                "in_transit",
                _iso_utc(now_dt),
                _iso_utc(eta_dt),
                None,
                None,
                None,
                None,
            ),
        )
        conn.commit()

    if bounded_eta == 0:
        occ_id = _commit_and_activate_interaction_occurrence(
            path,
            interaction_id=interaction_id,
            world_id=world_id,
            kind=normalized_kind,
            title=clean_title,
            detail=clean_detail,
            sender_note=clean_note,
            receiver_note="",
            target_place=clean_place,
            ledger=ledger,
            occurrence_content=occurrence_content,
        )
        if occ_id:
            with sqlite3.connect(str(path), timeout=10.0) as conn:
                conn.execute(
                    "UPDATE world_v2_counterpart_interactions SET occurrence_id = ? WHERE interaction_id = ?",
                    (occ_id, interaction_id),
                )
                conn.commit()

    items = sync_interactions_with_ledger(path, ledger=ledger, occurrence_content=occurrence_content)
    return next((item for item in items if item["interaction_id"] == interaction_id), items[0] if items else {})


def settle_counterpart_interaction_now(
    db_path: str | Path,
    *,
    interaction_id: str,
    receiver_note: str = "",
    ledger: Any = None,
    occurrence_content: Any = None,
) -> dict[str, Any]:
    """Immediately unbox a companion parcel or fast-forward an in-transit delivery into the World Ledger."""

    path = Path(db_path)
    with sqlite3.connect(str(path), timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM world_v2_counterpart_interactions WHERE interaction_id = ?",
            (interaction_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"interaction not found: {interaction_id}")
        world_id = str(row["world_id"] or _detect_world_id(conn))
        kind = str(row["kind"])
        title = str(row["title"])
        detail = str(row["detail"])
        sender_note = str(row["sender_note"] or "")
        clean_receiver_note = (receiver_note or row["receiver_note"] or "").strip()
        target_place = str(row["target_place"] or "")
        existing_occ_id = str(row["occurrence_id"] or "")

    now_iso = _iso_utc(_utc_now())
    occ_id = existing_occ_id
    if not occ_id:
        occ_id = _commit_and_activate_interaction_occurrence(
            path,
            interaction_id=interaction_id,
            world_id=world_id,
            kind=kind,
            title=title,
            detail=detail,
            sender_note=sender_note,
            receiver_note=clean_receiver_note,
            target_place=target_place,
            ledger=ledger,
            occurrence_content=occurrence_content,
        ) or ""

    with sqlite3.connect(str(path), timeout=10.0) as conn:
        if kind == "companion_parcel":
            feedback_clause = f"，附有签收留言：「{clean_receiver_note}」" if clean_receiver_note else ""
            default_outcome = (
                f"手机快递物流弹出签收提醒：此前寄出的包裹「{title}」已在{target_place or '收件驿站'}"
                f"由收件人本人签收并拆封纸箱{feedback_clause}。"
            )
            conn.execute(
                """
                UPDATE world_v2_counterpart_interactions
                SET status = 'received',
                    receiver_note = ?,
                    eta_at = ?,
                    settled_at = ?,
                    occurrence_id = COALESCE(NULLIF(?, ''), occurrence_id),
                    outcome_text = COALESCE(outcome_text, ?)
                WHERE interaction_id = ?
                """,
                (clean_receiver_note, now_iso, now_iso, occ_id, default_outcome, interaction_id),
            )
        else:
            conn.execute(
                """
                UPDATE world_v2_counterpart_interactions
                SET eta_at = ?,
                    occurrence_id = COALESCE(NULLIF(?, ''), occurrence_id)
                WHERE interaction_id = ?
                """,
                (now_iso, occ_id, interaction_id),
            )
        conn.commit()

    items = sync_interactions_with_ledger(path, ledger=ledger, occurrence_content=occurrence_content)
    return next((item for item in items if item["interaction_id"] == interaction_id), {})


def _extract_inner_drama(conn: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(conn, "world_v2_head_state_items"):
        return {
            "turns": [],
            "active_affect": [],
            "appraisals": [],
            "private_impressions": [],
            "intentions": [],
        }

    # 1. Load expression_plan_manifests for sent bubbles + revisit/expectation intentions
    manifests_by_proposal: dict[str, dict[str, Any]] = {}
    intentions: list[dict[str, Any]] = []
    manifest_rows = conn.execute(
        """
        SELECT idx, item_json FROM world_v2_head_state_items
        WHERE field = 'expression_plan_manifests' AND idx >= 0
        ORDER BY idx DESC LIMIT 40
        """
    ).fetchall()
    for mrow in manifest_rows:
        try:
            m = json.loads(mrow["item_json"])
        except Exception:
            continue
        if not isinstance(m, dict):
            continue
        prop_id = str(m.get("proposal_id") or "")
        if prop_id:
            manifests_by_proposal[prop_id] = m
        revisit = m.get("revisit_intention")
        if isinstance(revisit, dict) and len(intentions) < 6:
            kind_code = str(revisit.get("kind") or "topic_open")
            intentions.append(
                {
                    "type": "revisit_intention",
                    "type_label": "她记着的后续念头 (Revisit Intention)",
                    "kind_label": _REVISIT_KIND_LABELS.get(kind_code, kind_code),
                    "reason": str(revisit.get("reason") or ""),
                    "not_before_display": _format_cn_time(revisit.get("not_before")),
                    "expires_at_display": _format_cn_time(revisit.get("expires_at")),
                    "Invalidated": False,
                }
            )
        expect = m.get("response_expectation")
        if isinstance(expect, dict) and len(intentions) < 6:
            intentions.append(
                {
                    "type": "response_expectation",
                    "type_label": "她在等你的回应 (Response Expectation)",
                    "kind_label": f"在意度 {round(int(expect.get('importance_bp') or 0) / 100)}%",
                    "reason": str(expect.get("hoped_response") or ""),
                    "not_before_display": _format_cn_time(expect.get("not_before")),
                    "expires_at_display": _format_cn_time(expect.get("expires_at")),
                }
            )

    # 2. Load recent user ingress batches for user message text lookup
    ingress_text_by_suffix: dict[str, str] = {}
    if _table_exists(conn, "world_v2_qq_ingress_batches"):
        brows = conn.execute(
            """
            SELECT batch_id, batch_json FROM world_v2_qq_ingress_batches
            ORDER BY rowid DESC LIMIT 60
            """
        ).fetchall()
        for brow in brows:
            bid = str(brow["batch_id"] or "")
            suffix = bid.split(":")[-1] if ":" in bid else bid
            try:
                bj = json.loads(brow["batch_json"])
                text = str(bj.get("text") or "").strip()
                if suffix and text:
                    ingress_text_by_suffix[suffix] = text
            except Exception:
                continue

    # 3. Load latest turns with private_turn_state from proposal_audits
    turns: list[dict[str, Any]] = []
    audit_rows = conn.execute(
        """
        SELECT idx, item_json FROM world_v2_head_state_items
        WHERE field = 'proposal_audits'
          AND item_json LIKE '%inner_state_summary%'
        ORDER BY idx DESC LIMIT 12
        """
    ).fetchall()
    for arow in audit_rows:
        try:
            audit = json.loads(arow["item_json"])
            proposal_raw = audit.get("proposal_json")
            proposal = (
                json.loads(proposal_raw)
                if isinstance(proposal_raw, str)
                else (proposal_raw or {})
            )
            pts = proposal.get("private_turn_state")
            if not isinstance(pts, dict):
                continue
        except Exception:
            continue

        proposal_id = str(audit.get("proposal_id") or "")
        trigger_ref = str(audit.get("trigger_ref") or "")
        is_proactive = proposal_id.startswith("proposal:proactive:") or ":proactive:" in proposal_id
        trigger_suffix = trigger_ref.split(":")[-1] if ":" in trigger_ref else trigger_ref
        user_text = ingress_text_by_suffix.get(trigger_suffix, "")
        if not user_text:
            if is_proactive:
                user_text = "【主动找你 / 生活事件与心绪触发】"
            else:
                user_text = "【收到你的消息触发】"

        manifest = manifests_by_proposal.get(proposal_id, {})
        beats = manifest.get("beats") if isinstance(manifest, dict) else None
        sent_bubbles: list[str] = []
        created_at_str = None
        if isinstance(beats, list):
            for beat in beats:
                if not isinstance(beat, dict):
                    continue
                btext = str(beat.get("text") or "").strip()
                if btext:
                    sent_bubbles.append(btext)
                if not created_at_str and isinstance(beat.get("action"), dict):
                    created_at_str = beat["action"].get("created_at")

        if not sent_bubbles:
            # Check inline proposal intents if silence/hold or unaccepted
            intents = proposal.get("intents") or proposal.get("beats") or []
            if isinstance(intents, list):
                for item in intents:
                    if isinstance(item, dict) and item.get("text"):
                        sent_bubbles.append(str(item["text"]).strip())

        raw_noticed = pts.get("noticed")
        if isinstance(raw_noticed, str):
            noticed_list = [raw_noticed.strip()] if raw_noticed.strip() else []
        elif isinstance(raw_noticed, (list, tuple)):
            noticed_list = [str(x).strip() for x in raw_noticed if str(x).strip()]
        else:
            noticed_list = []

        raw_stuck = pts.get("stuck_with_me")
        if isinstance(raw_stuck, str):
            stuck_list = [raw_stuck.strip()] if raw_stuck.strip() else []
        elif isinstance(raw_stuck, (list, tuple)):
            stuck_list = [str(x).strip() for x in raw_stuck if str(x).strip()]
        else:
            stuck_list = []

        turns.append(
            {
                "turn_index": int(arow["idx"]),
                "proposal_id": proposal_id,
                "evaluated_world_revision": audit.get("evaluated_world_revision"),
                "mode": "proactive" if is_proactive else "reply",
                "mode_label": "她主动找你" if is_proactive else "回你的消息",
                "time_display": _format_cn_time(created_at_str),
                "user_text": user_text,
                "sent_bubbles": sent_bubbles,
                "inner_state_summary": str(pts.get("inner_state_summary") or ""),
                "noticed": noticed_list,
                "about_us": str(pts.get("about_us") or ""),
                "why_us": str(pts.get("why_us") or ""),
                "stuck_with_me": stuck_list,
            }
        )

    # 4. Load active affect_episodes
    active_affect: list[dict[str, Any]] = []
    affect_rows = conn.execute(
        """
        SELECT idx, item_json FROM world_v2_head_state_items
        WHERE field = 'affect_episodes' AND idx >= 0
        ORDER BY idx DESC LIMIT 12
        """
    ).fetchall()
    for frow in affect_rows:
        try:
            ep = json.loads(frow["item_json"])
        except Exception:
            continue
        if not isinstance(ep, dict):
            continue
        status = str(ep.get("status") or "")
        if status not in {"active", "open"}:
            continue
        components = ep.get("components") or []
        if isinstance(components, list) and components:
            for comp in components:
                if not isinstance(comp, dict):
                    continue
                kind_code = str(comp.get("dimension") or comp.get("emotion_kind") or "warmth")
                intensity_bp = int(comp.get("intensity_bp") or 0)
                residual_bp = int(comp.get("residue_bp") or comp.get("residual_floor_bp") or 0)
                active_affect.append(
                    {
                        "episode_id": str(ep.get("episode_id") or ""),
                        "emotion_kind": kind_code,
                        "emotion_label": _AFFECT_LABELS.get(kind_code, kind_code),
                        "intensity_bp": intensity_bp,
                        "intensity_pct": round(intensity_bp / 100, 1),
                        "residual_pct": round(residual_bp / 100, 1),
                        "status": status,
                        "opened_at_display": _format_cn_time(comp.get("opened_at") or ep.get("opened_at")),
                        "expires_at_display": _format_cn_time(comp.get("last_updated_at") or ep.get("updated_at")),
                    }
                )
        else:
            values = ep.get("values") or {}
            kind_code = str(values.get("emotion_kind") or ep.get("emotion_kind") or "warmth")
            intensity_bp = int(values.get("intensity_bp") or ep.get("intensity_bp") or 0)
            residual_bp = int(values.get("residual_floor_bp") or 0)
            active_affect.append(
                {
                    "episode_id": str(ep.get("episode_id") or ""),
                    "emotion_kind": kind_code,
                    "emotion_label": _AFFECT_LABELS.get(kind_code, kind_code),
                    "intensity_bp": intensity_bp,
                    "intensity_pct": round(intensity_bp / 100, 1),
                    "residual_pct": round(residual_bp / 100, 1),
                    "status": status,
                    "opened_at_display": _format_cn_time(ep.get("opened_at") or (ep.get("origin") or {}).get("accepted_at")),
                    "expires_at_display": _format_cn_time(values.get("expires_at") or ep.get("updated_at")),
                }
            )

    # 5. Load recent accepted appraisals
    appraisals: list[dict[str, Any]] = []
    app_rows = conn.execute(
        """
        SELECT idx, item_json FROM world_v2_head_state_items
        WHERE field = 'appraisals' AND idx >= 0
        ORDER BY idx DESC LIMIT 10
        """
    ).fetchall()
    for prow in app_rows:
        try:
            ap = json.loads(prow["item_json"])
        except Exception:
            continue
        if not isinstance(ap, dict):
            continue
        values = ap.get("values") or {}
        origin = ap.get("origin") or {}
        hyps = ap.get("hypotheses") or values.get("hypotheses") or []
        parsed_hyps = []
        for h in hyps:
            if isinstance(h, dict) and h.get("meaning"):
                parsed_hyps.append(
                    {
                        "meaning": str(h.get("meaning") or ""),
                        "attribution": str(h.get("attribution") or ""),
                        "severity": str(h.get("severity") or ""),
                        "weight_pct": round(int(h.get("weight_bp") or 0) / 100),
                    }
                )
        if not parsed_hyps:
            continue
        conf_bp = int(ap.get("confidence_bp") or origin.get("confidence_bp") or 0)
        acc_at = ap.get("accepted_at") or origin.get("accepted_at")
        appraisals.append(
            {
                "idx": int(prow["idx"]),
                "appraisal_id": str(ap.get("appraisal_id") or ""),
                "status": str(ap.get("status") or ""),
                "confidence_pct": round(conf_bp / 100),
                "accepted_at_display": _format_cn_time(acc_at),
                "hypotheses": parsed_hyps,
            }
        )

    # 6. Load recent private_impressions
    impressions: list[dict[str, Any]] = []
    imp_rows = conn.execute(
        """
        SELECT idx, item_json FROM world_v2_head_state_items
        WHERE field = 'private_impressions' AND idx >= 0
        ORDER BY idx DESC LIMIT 8
        """
    ).fetchall()
    for irow in imp_rows:
        try:
            imp = json.loads(irow["item_json"])
        except Exception:
            continue
        if not isinstance(imp, dict):
            continue
        values = imp.get("values") or {}
        origin = imp.get("origin") or {}
        summary = str(imp.get("reflection_summary") or values.get("reflection_summary") or "").strip()
        if not summary:
            continue
        conf_bp = int(imp.get("confidence_bp") or origin.get("confidence_bp") or 0)
        acc_at = imp.get("last_supported") or imp.get("first_seen") or origin.get("accepted_at")
        impressions.append(
            {
                "idx": int(irow["idx"]),
                "impression_id": str(imp.get("impression_id") or ""),
                "status": str(imp.get("status") or ""),
                "reflection_summary": summary,
                "confidence_pct": round(conf_bp / 100),
                "accepted_at_display": _format_cn_time(acc_at),
            }
        )

    return {
        "turns": turns,
        "active_affect": active_affect,
        "appraisals": appraisals,
        "private_impressions": impressions,
        "intentions": intentions,
    }


def build_dashboard_studio_snapshot(
    db_path: str | Path,
    *,
    ledger: Any = None,
    occurrence_content: Any = None,
) -> dict[str, Any]:
    """Build the unified payload for the Inner Drama Studio, Cost Mini-Gauge, and Interaction Station."""

    path = Path(db_path)
    interactions = sync_interactions_with_ledger(
        path,
        ledger=ledger,
        occurrence_content=occurrence_content,
    )
    cost_snapshot = build_dashboard_cost_snapshot(path)

    inner_drama: dict[str, Any] = {
        "turns": [],
        "active_affect": [],
        "appraisals": [],
        "private_impressions": [],
        "intentions": [],
    }
    if path.exists():
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            inner_drama = _extract_inner_drama(conn)
        finally:
            conn.close()

    pending_pickup_count = sum(1 for item in interactions if item["status"] == "ready_for_pickup")
    in_transit_count = sum(1 for item in interactions if item["status"] == "in_transit")
    completed_count = sum(
        1 for item in interactions if item["status"] in {"delivered", "received"}
    )

    return {
        "status": "ready",
        "captured_at": _iso_utc(_utc_now()),
        "inner_drama": inner_drama,
        "cost_mini": {
            "summary": cost_snapshot.get("summary", {}),
            "categories": (cost_snapshot.get("categories") or [])[:5],
            "models": (cost_snapshot.get("models") or [])[:4],
        },
        "interaction_station": {
            "summary": {
                "pending_pickup_count": pending_pickup_count,
                "in_transit_count": in_transit_count,
                "completed_count": completed_count,
                "total_count": len(interactions),
            },
            "presets": list(_INTERACTION_PRESETS),
            "items": interactions,
        },
    }


__all__ = [
    "build_dashboard_studio_snapshot",
    "create_counterpart_interaction",
    "settle_counterpart_interaction_now",
    "sync_interactions_with_ledger",
]
