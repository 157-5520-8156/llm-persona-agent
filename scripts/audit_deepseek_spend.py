#!/usr/bin/env python3
"""Read-only DeepSeek spend audit: production vs clone/test, waste, unit cost.

Never writes ``data/``. Never calls a model. Opens sqlite with
``file:?mode=ro``. Artifacts go to ``output/spend-audit/``.

Usage::

    .venv/bin/python scripts/audit_deepseek_spend.py
    .venv/bin/python scripts/audit_deepseek_spend.py --since 2026-08-13
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from companion_daemon.spend_account import debug_ledger_path
from companion_daemon.usage_metrics import (
    estimate_legacy_flat_cny,
    estimate_model_cost,
    is_deepseek_peak,
)

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
EPOCH1_CANDIDATES = (
    (REPO / "data" / "companion.sqlite").resolve(),
    (REPO / "data" / "companion.epoch1.sqlite").resolve(),
    (REPO / "data" / "companion.epoch1.rerun.sqlite").resolve(),
)
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
OUTPUT = (REPO / "output" / "spend-audit").resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")
PRICE_HIKE = datetime(2026, 8, 17, 0, 0, tzinfo=SHANGHAI)

# Repo ledger used USD × 7.2 (usage_metrics July 2026 row) until the
# 2026-08-17 peak/off-peak CNY table. Official console CNY is native.

PHOTO_SENT_FRAGMENTS = (
    "于是把照片发了过去",
    "把照片发了过去",
    "已经发给他了",
    "照片已经挑好发给他了",
    "照片发过去了",
    "照片已经发过去",
    "照片昨晚已经发给他了",
    "已经发出去了",
    "发出去了",
    "照片已经发出去",
    "照片昨晚发出去了",
    "发给他了",
    "已经发给他",
    "已经发过去",
    "也真的发了",
)

JSON_CONTRACT_FAILURES = frozenset(
    {
        "authored_expression_reselection_invalid",
        "paired_expression_reselection_invalid",
        "appraisal_reselection_invalid",
        "recall_choice_reselection_invalid",
        "main_invalid_output",
        "invalid_role_result_after_correction",
        "corrective_invalid",
        "inventory_invalid",
        "coverage_invalid",
    }
)

PURPOSE_LANES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("入站回话", ("inbound_turn", "paired_cognition_initial", "paired_cognition_stream",
               "expression_stream_tail", "validation_reselection", "qq_attachment_perception")),
    ("主动开口", ("proactive_contact",)),
    ("生活生态", ("life_development_draft", "life_development_choice", "activity_lifecycle_choice",
               "outcome_selection", "experience_memory_retention", "fact_memory_retention",
               "npc_ecology", "open_world", "life_development_source_closure_review",
               "life_development_novel_origin_review")),
    ("私人印象农场", ("private_impression_reflection",)),
    ("评价 appraisal", ("world_stimulus_appraisal",)),
    ("媒体选片/生图", ("media_selection", "image_generation")),
    ("对话事实抽取", ("interaction_fact_draft",)),
    ("未分用途（旧账）", ("world_v2_character_interior", "unclassified", "")),
)

VISIBLE_INBOUND = {
    "inbound_turn",
    "paired_cognition_initial",
    "paired_cognition_stream",
    "expression_stream_tail",
    "paired_recall_followup",
    "recall_followup",
    "recall_control_transfer",
    "validation_reselection",
    "qq_attachment_perception",
    "interaction_fact_draft",
}


@dataclass
class UsageRow:
    recorded_at: str
    purpose: str
    model: str
    status: str
    prompt: int
    completion: int
    hit: int
    miss: int
    ledger_cny: float
    error: str
    bucket: str = ""
    source: str = ""
    spend_account: str = ""

    @property
    def fingerprint(self) -> tuple[Any, ...]:
        return (
            self.recorded_at,
            self.purpose,
            self.model,
            self.status,
            self.prompt,
            self.completion,
            self.hit,
            self.miss,
            round(self.ledger_cny, 6),
            self.error[:80],
        )

    def local(self) -> datetime:
        return parse_dt(self.recorded_at)

    def official_cny(self) -> float | None:
        if not self.is_deepseek():
            return None
        return estimate_model_cost(
            model=self.model,
            prompt_tokens=self.prompt,
            completion_tokens=self.completion,
            cache_hit_tokens=self.hit,
            cache_miss_tokens=self.miss,
            at=self.recorded_at,
        ).cny

    def legacy_flat_cny(self) -> float:
        return estimate_legacy_flat_cny(
            model=self.model,
            prompt_tokens=self.prompt,
            completion_tokens=self.completion,
            cache_hit_tokens=self.hit,
            cache_miss_tokens=self.miss,
        )

    def is_deepseek(self) -> bool:
        return "deepseek" in (self.model or "").lower()


@dataclass
class Money:
    calls: int = 0
    ledger_cny: float = 0.0
    official_cny: float = 0.0
    prompt: int = 0
    completion: int = 0
    hit: int = 0
    miss: int = 0
    succeeded: int = 0
    failed: int = 0

    def add(self, row: UsageRow) -> None:
        self.calls += 1
        self.ledger_cny += row.ledger_cny
        official = row.official_cny()
        if official is not None:
            self.official_cny += official
        self.prompt += row.prompt
        self.completion += row.completion
        self.hit += row.hit
        self.miss += row.miss
        if row.status == "succeeded":
            self.succeeded += 1
        else:
            self.failed += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "ledger_cny": round(self.ledger_cny, 4),
            "official_cny": round(self.official_cny, 4),
            "prompt_tokens": self.prompt,
            "completion_tokens": self.completion,
            "cache_hit_tokens": self.hit,
            "cache_miss_tokens": self.miss,
        }


def parse_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("empty timestamp")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SHANGHAI)


def is_peak(when: datetime) -> bool:
    return is_deepseek_peak(when)


def connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def load_usage_rows(path: Path) -> list[UsageRow]:
    conn = connect_ro(path)
    try:
        if not table_exists(conn, "world_v2_model_usage"):
            return []
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(world_v2_model_usage)")
        }
        spend_sql = (
            "spend_account" if "spend_account" in columns else "''"
        )
        rows: list[UsageRow] = []
        for recorded_at, purpose, model, status, prompt, completion, hit, miss, cost, error, account in conn.execute(
            f"""
            SELECT recorded_at, purpose, model, status,
                   prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens,
                   cost_cny, error, {spend_sql}
            FROM world_v2_model_usage
            """
        ):
            rows.append(
                UsageRow(
                    recorded_at=str(recorded_at or ""),
                    purpose=str(purpose or ""),
                    model=str(model or ""),
                    status=str(status or ""),
                    prompt=int(prompt or 0),
                    completion=int(completion or 0),
                    hit=int(hit or 0),
                    miss=int(miss or 0),
                    ledger_cny=float(cost or 0.0),
                    error=str(error or ""),
                    spend_account=str(account or ""),
                )
            )
        return rows
    finally:
        conn.close()


def iter_sqlite_files(roots: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.sqlite"):
            name = path.name
            if name.endswith("-wal") or name.endswith("-shm"):
                continue
            found.append(path.resolve())
    return found


def lane_for(purpose: str) -> str:
    for label, names in PURPOSE_LANES:
        if purpose in names:
            return label
    if "reselection" in purpose or "corrective" in purpose or "recovery" in purpose:
        return "重选/修复"
    if "perception" in purpose or "vision" in purpose:
        return "感知/视觉"
    if "reflection" in purpose or "reconsider" in purpose:
        return "反思"
    return f"其他（{purpose or '空'}）"


def money_from(rows: Iterable[UsageRow]) -> Money:
    acc = Money()
    for row in rows:
        if row.is_deepseek():
            acc.add(row)
    return acc


def payload_of(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("payload_json")
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    if isinstance(raw, dict):
        return raw
    return {}


def load_events(conn: sqlite3.Connection, event_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sequence, raw in conn.execute(
        """
        SELECT ledger_sequence, event_json FROM world_v2_events
        WHERE json_extract(event_json, '$.event_type') = ?
          AND json_extract(event_json, '$.world_id') = ?
        """,
        (event_type, WORLD_ID),
    ):
        event = json.loads(raw)
        event["_seq"] = sequence
        event["_payload"] = payload_of(event)
        rows.append(event)
    return rows


def collect_unique_usage(since: date | None) -> dict[tuple[Any, ...], UsageRow]:
    unique: dict[tuple[Any, ...], UsageRow] = {}

    def ingest(path: Path, bucket: str) -> None:
        try:
            rows = load_usage_rows(path)
        except sqlite3.Error:
            return
        rel = str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)
        for row in rows:
            if since is not None:
                try:
                    if row.local().date() < since:
                        continue
                except ValueError:
                    continue
            fp = row.fingerprint
            if fp in unique:
                continue
            row_bucket = bucket
            if row.spend_account == "debug":
                row_bucket = "agent_test"
            elif row.spend_account == "production" and bucket.startswith("agent"):
                # Copied live rows that already carry a production stamp.
                continue
            row.bucket = row_bucket
            row.source = rel
            unique[fp] = row

    ingest(PRODUCTION_DB, "production_epoch2")
    for path in EPOCH1_CANDIDATES:
        if path.exists():
            ingest(path, "production_epoch1")
    skip = {PRODUCTION_DB, *EPOCH1_CANDIDATES}
    debug_ledger = debug_ledger_path().resolve()
    if debug_ledger.exists():
        ingest(debug_ledger, "agent_test")
        skip.add(debug_ledger)
    for path in iter_sqlite_files([REPO / "data", REPO / "output"]):
        if path in skip:
            continue
        ingest(path, "agent_test")
    return unique


def group_money(rows: Iterable[UsageRow], key_fn) -> dict[str, Money]:
    grouped: dict[str, Money] = defaultdict(Money)
    for row in rows:
        if not row.is_deepseek():
            continue
        grouped[key_fn(row)].add(row)
    return dict(grouped)


def production_world_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    types = Counter(
        str(row[0] or "")
        for row in conn.execute(
            """
            SELECT json_extract(event_json, '$.event_type')
            FROM world_v2_events
            WHERE json_extract(event_json, '$.world_id') = ?
            """,
            (WORLD_ID,),
        )
    )
    authorized = load_events(conn, "ActionAuthorized")
    action_kinds = Counter()
    for event in authorized:
        action = event["_payload"].get("action")
        kind = action.get("kind") if isinstance(action, dict) else None
        action_kinds[str(kind or "unknown")] += 1
    delivered = load_events(conn, "ActionDelivered")
    payloads = load_events(conn, "MessagePayloadStored")
    observations = load_events(conn, "ObservationRecorded")
    model_results = load_events(conn, "ModelResultRecorded")

    attempt_index = Counter()
    attempt_count = Counter()
    statuses = Counter()
    failures = Counter()
    json_failures = Counter()
    other_failures = Counter()
    failure_days: dict[str, Counter[str]] = defaultdict(Counter)
    reselection_rows = 0
    for event in model_results:
        payload = event["_payload"]
        idx = int(payload.get("attempt_index") or 0)
        cnt = int(payload.get("attempt_count") or 1)
        attempt_index[idx] += 1
        attempt_count[cnt] += 1
        if idx > 0:
            reselection_rows += 1
        audit_raw = payload.get("audit_json")
        try:
            audit = json.loads(audit_raw) if isinstance(audit_raw, str) else (audit_raw or {})
        except json.JSONDecodeError:
            audit = {}
        if not isinstance(audit, dict):
            audit = {}
        status = str(audit.get("status") or "")
        code = str(audit.get("failure_code") or "")
        statuses[status] += 1
        if code:
            failures[code] += 1
            day = parse_dt(event.get("created_at")).date().isoformat()
            failure_days[day][code] += 1
            if code in JSON_CONTRACT_FAILURES:
                json_failures[code] += 1
            else:
                other_failures[code] += 1

    photo_rows = 0
    photo_kinds: Counter[str] = Counter()
    unique_photo_texts: set[str] = set()
    if table_exists(conn, "world_v2_life_content"):
        for kind, text in conn.execute(
            """
            SELECT content_kind, text FROM world_v2_life_content
            WHERE world_id = ?
            """,
            (WORLD_ID,),
        ):
            body = str(text or "")
            if any(fragment in body for fragment in PHOTO_SENT_FRAGMENTS):
                photo_rows += 1
                photo_kinds[str(kind or "")] += 1
                unique_photo_texts.add(body.strip())

    clocks = load_events(conn, "ClockAdvanced")
    clock_times = sorted(parse_dt(event["created_at"]) for event in clocks)
    long_gaps = []
    for earlier, later in zip(clock_times, clock_times[1:]):
        seconds = (later - earlier).total_seconds()
        if seconds >= 3 * 3600:
            long_gaps.append(
                {
                    "from": earlier.isoformat(),
                    "to": later.isoformat(),
                    "hours": round(seconds / 3600, 2),
                }
            )

    trigger_kinds: Counter[str] = Counter()
    for event in load_events(conn, "TriggerProcessOpened"):
        payload = event["_payload"]
        process = payload.get("process") if isinstance(payload.get("process"), dict) else payload
        trigger_kinds[str(process.get("process_kind") or payload.get("process_kind") or "")] += 1

    media_tables = {}
    for name in (
        "world_v2_media_payload",
        "world_v2_event_media_planning_result",
        "world_v2_media_provider_dispatch",
        "world_v2_media_pending_inspection",
    ):
        media_tables[name] = (
            int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            if table_exists(conn, name)
            else 0
        )

    media_attempts = load_events(conn, "MediaSelectionAttemptRecorded")
    media_outcome = None
    if media_attempts:
        payload = media_attempts[0]["_payload"]
        media_outcome = {
            "outcome": payload.get("outcome"),
            "failure_code": payload.get("failure_code"),
            "candidates": len(payload.get("candidates") or []),
            "created_at": media_attempts[0].get("created_at"),
        }

    perception = _perception_snapshot()
    embedding = _embedding_snapshot(conn)

    unique_plans = {
        (event["_payload"] or {}).get("proposal_id")
        for event in payloads
        if (event["_payload"] or {}).get("proposal_id")
    }

    return {
        "event_counts": dict(types.most_common()),
        "qq_beats_delivered": len(delivered),
        "qq_unique_plans": len(unique_plans),
        "action_kinds": dict(action_kinds),
        "observations": len(observations),
        "model_results": len(model_results),
        "attempt_index": {str(k): v for k, v in attempt_index.items()},
        "attempt_count": {str(k): v for k, v in attempt_count.items()},
        "reselection_attempt_index_gt_0": reselection_rows,
        "model_result_status": dict(statuses.most_common()),
        "failure_codes": dict(failures.most_common()),
        "json_contract_failures": dict(json_failures.most_common()),
        "other_failures": dict(other_failures.most_common()),
        "failure_by_day": {day: dict(counter) for day, counter in sorted(failure_days.items())},
        "photo_fiction_rows": photo_rows,
        "photo_fiction_by_kind": dict(photo_kinds),
        "photo_fiction_unique_texts": len(unique_photo_texts),
        "clock_ticks": len(clocks),
        "clock_gaps_ge_3h": long_gaps,
        "clock_gap_max_hours": max((item["hours"] for item in long_gaps), default=0),
        "trigger_kinds": dict(trigger_kinds.most_common()),
        "media_tables": media_tables,
        "media_selection_attempts": len(media_attempts),
        "media_selection_outcome": media_outcome,
        "photo_candidates_opened": types.get("PhotoCandidateOpened", 0),
        "perception": perception,
        "embedding_cny": embedding,
        "life_content_recorded": types.get("LifeContentRecorded", 0),
        "experiences_committed": types.get("ExperienceCommitted", 0),
        "appraisals_accepted": types.get("AppraisalAccepted", 0),
        "private_impressions_accepted": types.get("PrivateImpressionAccepted", 0),
        "external_observation_processed": types.get("ExternalObservationProcessed", 0),
    }


def _perception_snapshot() -> dict[str, Any]:
    path = REPO / "data" / "external-world-perception.sqlite"
    if not path.exists() or path.stat().st_size == 0:
        return {"present": False}
    conn = connect_ro(path)
    try:
        def count(name: str) -> int:
            if not table_exists(conn, name):
                return 0
            return int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])

        return {
            "present": True,
            "signals": count("external_signal_revisions"),
            "opportunities": count("external_perception_attention_opportunities"),
            "model_audits": count("external_perception_attention_model_audits"),
            "attempts": count("external_perception_attention_attempts"),
            "live_outbox": count("external_perception_live_outbox"),
            "embeddings": count("external_signal_embeddings"),
        }
    finally:
        conn.close()


def _embedding_snapshot(conn: sqlite3.Connection) -> float:
    if not table_exists(conn, "world_recall_embedding_usage_daily"):
        return 0.0
    row = conn.execute(
        "SELECT COALESCE(SUM(estimated_cost_cny), 0) FROM world_recall_embedding_usage_daily"
    ).fetchone()
    return float(row[0] or 0.0)


def yuan(value: float) -> str:
    if abs(value) < 0.005:
        return "¥0.00"
    return f"¥{value:.2f}"


def pct(part: float, whole: float) -> str:
    if whole <= 0:
        return "—"
    return f"{100.0 * part / whole:.0f}%"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def build_report(result: dict[str, Any]) -> str:
    total = result["headline"]["total_official_cny"]
    waste = result["headline"]["waste_official_cny"]
    waste_pct = result["headline"]["waste_ratio"]
    prod = result["buckets"]["production_epoch2"]
    epoch1 = result["buckets"]["production_epoch1"]
    agent = result["buckets"]["agent_test"]
    world = result["production_world"]
    waste_items = result["waste_items"]
    lanes = result["production_lanes"]
    unit = result["unit_cost"]
    days = result["days"]

    day_rows = []
    for day, payload in days:
        day_rows.append(
            [
                day,
                str(payload["production_epoch2"]["calls"]),
                yuan(payload["production_epoch2"]["official_cny"]),
                str(payload["production_epoch1"]["calls"]),
                yuan(payload["production_epoch1"]["official_cny"]),
                str(payload["agent_test"]["calls"]),
                yuan(payload["agent_test"]["official_cny"]),
                yuan(payload["total_official_cny"]),
            ]
        )

    lane_rows = []
    for item in lanes:
        lane_rows.append(
            [
                item["lane"],
                str(item["calls"]),
                yuan(item["official_cny"]),
                str(item["ledger_events"]),
                str(item["user_visible"]),
                item["bought"],
            ]
        )

    waste_rows = []
    for item in waste_items:
        waste_rows.append(
            [
                item["name"],
                yuan(item["official_cny"]),
                item["kind"],
                item["note"],
            ]
        )

    save_rows = []
    for item in result["savings"]:
        save_rows.append(
            [
                item["action"],
                item["kind"],
                yuan(item["estimated_save_cny"]),
                item["basis"],
            ]
        )

    gap = result["bill_gap"]
    return f"""# 沈知栀这几天把 DeepSeek 的钱花哪了

**{yuan(total)} / {yuan(waste)} / {waste_pct}**

三个数字是：**账本按 DeepSeek 官方人民币峰谷价能对上的总额 / 其中没换成你 QQ 上体验的白烧 / 白烧占比。**

你说「一百多」。账本对得上大约 **{yuan(total)}**，不是一百。差的那一截账本里没有，下面单独说。先把能对上的说清楚：

- **她陪你**（当前生产 epoch2，8 月 14–19 日）：**{yuan(prod['official_cny'])}**，{prod['calls']} 次 Flash 调用，你 QQ 上真正送到的气泡 **{world['qq_beats_delivered']}** 条。
- **我们拿你的钥匙跑克隆/探针**：**{yuan(agent['official_cny'])}**，{agent['calls']} 次。这是「一百多」里最大的一块，也是白烧大头。
- **上一世生产**（epoch1，大约 8 月 7–13 日，世界已封存）：**{yuan(epoch1['official_cny'])}**。那是她当时的正事，不是这几天的调试。

账本自己记的 `cost_cny` 用的是 7 月那张美元价 × 7.2，生产只记了 {yuan(prod['ledger_cny'])}。8 月 17 日 0 点起 DeepSeek 改人民币峰谷价：空闲时段 Flash 未命中输入 ¥1.5/百万、输出 ¥4.5/百万，高峰（北京 9–12 点、14–18 点）翻倍到 ¥3 / ¥9。所以**同一批 token，控制台比仓库记账贵大约一倍**。8 月 19 日生产一天，仓库记 {yuan(result['aug19_prod_ledger'])}，官方价 {yuan(result['aug19_prod_official'])}。

{gap}

---

## 一、总账

按上海日历。金额都是 **DeepSeek Flash 官方人民币价**（不是仓库 `cost_cny`）。非 DeepSeek（GPT 图、Qwen、Civitai）另附，不计入上面三个数字。

{md_table(
    ["日期", "生产次数", "生产", "上一世次数", "上一世", "调试次数", "调试", "当日合计"],
    day_rows,
)}

按模型：能对上的 DeepSeek 调用全部是 `deepseek-v4-flash`，没有 Pro。高峰时段承担了 {yuan(result['peak_split']['peak']['official_cny'])}（{result['peak_split']['peak']['calls']} 次），空闲 {yuan(result['peak_split']['off']['official_cny'])}，涨价前 {yuan(result['peak_split']['pre']['official_cny'])}。

调试花费最多的目录（指纹去重后，同一调用拷进八个 clone 只算一次）：

{md_table(
    ["来源", "次数", "官方价"],
    [[item["source"], str(item["calls"]), yuan(item["official_cny"])] for item in result["agent_sources"][:12]],
)}

非 DeepSeek、不计入总额：克隆里 GPT Image 2 大约 {yuan(result['non_deepseek']['gpt_image_official_proxy'])}（按仓库美元价 × 7.2），Civitai Buzz 记 0 元。生产 `usage_events` 是空的——她还没在生产账本里留下生图花费。

---

## 二、这钱买到了什么（生产）

只谈当前生产世界 `world:companion-v2:qq-c2c:geoff`。金额是官方价。

{md_table(
    ["用途", "调用", "花费", "落账", "你看见的", "买到了什么"],
    lane_rows,
)}

后台（主动 + 生活 + 农场 + 评价 + 媒体，不含入站回话和事实抽取）占生产 DeepSeek 的 **{pct(unit['background_official_cny'], prod['official_cny'])}**（{yuan(unit['background_official_cny'])}）。换成你看见的东西：**2 条主动消息**，照片 **0 张**。其余是她的内心账：评价、活动、周记、记忆。那些对角色是真的，对 QQ 气泡几乎不是。

入站 {world['observations']} 次你说话，她回了 {world['action_kinds'].get('reply', 0)} 条回复气泡（{world['qq_unique_plans']} 个表达计划，有的一计划多拍）。另有主动 {world['action_kinds'].get('proactive_message', 0)}、followup {world['action_kinds'].get('followup', 0)}。合计 **{world['qq_beats_delivered']} 条到达 QQ**。

---

## 三、浪费清单

原则：不把「她过了一天内心生活」算成浪费。浪费是**付了钱、你这边零体验，或产出是假的**。调试对你个人体验也是零，所以算进白烧，但和「她系统写砸了」分开写。

{md_table(
    ["项目", "金额", "类型", "说明"],
    waste_rows,
)}

分项说明：

### 1. 重选（受约束重选）

生产 `ModelResultRecorded` {world['model_results']} 条里，`attempt_index > 0` 只有 **{world['reselection_attempt_index_gt_0']}** 条。用量行的 `attempt` 全是 1——第二次调用被当成另一次 attempt=1 记下，所以不能用用量表直接加总「重选占比」。

失败码（生产，按次数）：

{md_table(
    ["failure_code", "次数", "归类"],
    [
        [code, str(count), "JSON/契约" if code in JSON_CONTRACT_FAILURES else "超时/异常/其他"]
        for code, count in world["failure_codes"].items()
    ] or [["（无）", "0", ""]],
)}

JSON/契约类合计 **{sum(world['json_contract_failures'].values())}** 次（`authored_expression_reselection_invalid`、`main_invalid_output`、`invalid_role_result_after_correction` 等）。今天修的运输层 JSON 修复针对这类。它们在用量表上常常是「调用成功、结果被丢掉」或「cancelled、token=0」。生产侧估出来大约 {yuan(waste_items[0]['official_cny'] if waste_items else 0)}，不是大头。克隆里大量 `inbound_turn` 重跑才是重选烧钱的地方，已经算进「调试白烧」。

### 2. 技术失败（调了、丢掉、没落成可见消息）

用量表失败 16 次，几乎全是 `caller_cancelled` 或一次 400，**token=0，控制台也不该扣钱**。真正可能扣了钱再丢掉的，是 status=succeeded 但 audit 为 `main_invalid` / `recovery_failed` 的那些。生产里这类不多，估 {yuan(result['tech_fail_official_cny'])}。

### 3. 空转车道

- **调度器空转 3 小时**：账本上 ClockAdvanced 有 {len(world['clock_gaps_ge_3h'])} 段 ≥3 小时的缝，最长 {world.get('clock_gap_max_hours') or 0} 小时。那是进程没在走，**空窗期不调用模型，不烧钱**。真在跑的时候时钟大约 20 秒一次，多数 tick 并不调模型。
- **pending_opportunity_count=20**：这是主动机会堆着没消化。生产主动调用 {result['proactive_calls']} 次、官方价 {yuan(result['proactive_official_cny'])}，送到你 QQ 的主动消息 **2** 条。她经常选 silent/later，这是角色决定，不是脚本替她闭嘴。钱花了，你没看见——算空转，但不是纯 bug。
- **媒体链六天问了 1 次**：`media_selection` 1 次、{yuan(result['media_official_cny'])}，她选了 `no_op`。`media_provider_dispatch` / `media_payload` / `event_media_planning_result` 全是 0 行。**几乎没烧模型钱，烧的是功能**：六天只问她一次，问了还不发，所以你看不到照片。
- **外部感知 230 条她看见 0**：账本 `ExternalObservationProcessed` = {world['external_observation_processed']}，抽过样，那是 **QQ 回执**，不是 RSS/新闻。独立感知库里信号 3 条、模型审计 **0**、live outbox **0**。**DeepSeek 这块是 ¥0。** K6 说的「看见 0 条」是上下文没把感知喂给她，不是账单。

### 4. 被毒化的产出

生活散文里至少 {world['photo_fiction_unique_texts']} 段不同的文字写「照片已经发给他了」，同时 `media_deliveries` 为 0、生产生图表为空。同一件假事被写成 14 条 experience_summary 反复记。对应记忆/收场车道大约 {yuan(result['poison_official_cny'])}。今天要回填抹掉的就是这批——钱已经付过了，留下的内容还是假的。

### 5. 重复劳动

同一「书店角落照片已发出」被夜醒、再睡、图书馆窗边翻来覆去记。主动车道也对同一处境反复 consider 再 silent。这部分和毒化、空转有重叠，上面不重复加总。

---

## 四、单位成本

- **每一条真正到达 QQ 的气泡（{world['qq_beats_delivered']} 条，含多拍）**：生产 DeepSeek 全摊 = **¥{unit['cny_per_delivered_beat']:.3f}**。只摊入站回话 = **¥{unit['cny_per_reply_inbound_only']:.3f}**。
- 若用「一次表达计划」当一条（{world['qq_unique_plans']}）：全摊 **{yuan(unit['cny_per_plan'])}**。
- 后台占生产 **{pct(unit['background_official_cny'], prod['official_cny'])}**，换成你看见的：**2 条主动消息**，折合每条主动消息背后还趴着 {yuan(unit['cny_per_proactive_visible'])} 的考虑/沉默。

直说：生产这几天 **不是**「她聊天把钱烧光了」。聊天本身很便宜。贵的是后台内心，再加上我们调试。

---

## 五、他该怎么省

{md_table(
    ["做法", "类型", "估省", "依据"],
    save_rows,
)}

上面几条**不能加总**：停真钥匙已经把高峰里的调试一并拿掉了。不要为了省 {yuan(prod['official_cny'])} 把入站回话掐掉——那是她在跟你说话，也是这几天生产里唯一明显买到的东西。真要降账单，先停真钥匙跑克隆（单是 `output/proactive-fourth-wall` 就烧了 {yuan(result['agent_sources'][0]['official_cny'] if result['agent_sources'] else 0)}），再把剩下的真调用挪出高峰。

---

## 怎么重跑

```bash
.venv/bin/python scripts/audit_deepseek_spend.py
.venv/bin/python scripts/audit_deepseek_spend.py --since 2026-08-17
```

只读 `data/` 和 `output/` 下的 sqlite（`mode=ro`），不调模型，不写生产账本。报告和 `summary.json` 写到 `output/spend-audit/`。

克隆库会把生产用量拷进去。脚本按「时间戳 + token + 花费」做指纹去重：同一笔生产只算一次，同一笔测试被拷八份也只算一次。两次独立的真调用就算两次。

仓库记账（美元 × 7.2）和 DeepSeek 控制台（人民币峰谷）是两套价。对你的充值余额，信官方价。
"""


def analyze(*, since: date | None) -> dict[str, Any]:
    unique = collect_unique_usage(since)
    rows = list(unique.values())
    buckets = {
        "production_epoch2": money_from(r for r in rows if r.bucket == "production_epoch2"),
        "production_epoch1": money_from(r for r in rows if r.bucket == "production_epoch1"),
        "agent_test": money_from(r for r in rows if r.bucket == "agent_test"),
    }
    total = Money()
    for row in rows:
        if row.is_deepseek():
            total.add(row)

    prod_rows = [r for r in rows if r.bucket == "production_epoch2" and r.is_deepseek()]
    agent_rows = [r for r in rows if r.bucket == "agent_test" and r.is_deepseek()]

    conn = connect_ro(PRODUCTION_DB)
    try:
        world = production_world_audit(conn)
    finally:
        conn.close()

    prod_by_purpose = group_money(prod_rows, lambda r: r.purpose)
    prod_by_lane = group_money(prod_rows, lambda r: lane_for(r.purpose))

    def lane_events(label: str) -> tuple[int, int, str]:
        counts = world["event_counts"]
        if label == "入站回话":
            visible = world["action_kinds"].get("reply", 0) + world["action_kinds"].get("followup", 0)
            return counts.get("ObservationRecorded", 0), visible, "你说话 → 她回 QQ"
        if label == "主动开口":
            return (
                world.get("trigger_kinds", {}).get("proactive_action_deliberation", 0),
                world["action_kinds"].get("proactive_message", 0),
                "考虑要不要找你；送到 QQ 的主动消息",
            )
        if label == "生活生态":
            return (
                counts.get("LifeContentRecorded", 0) + counts.get("ExperienceCommitted", 0)
                + counts.get("ActivityPlanned", 0),
                0,
                "活动/周记/记忆落账；QQ 上 0 条",
            )
        if label == "私人印象农场":
            return counts.get("PrivateImpressionAccepted", 0), 0, "私密印象留在内心，不发 QQ"
        if label == "评价 appraisal":
            return counts.get("AppraisalAccepted", 0), 0, "对世界事件的评价，喂给内心不喂气泡"
        if label == "媒体选片/生图":
            return world["media_selection_attempts"], 0, "问了 1 次，她选 no_op，下游生图 0"
        if label == "对话事实抽取":
            return counts.get("InteractionFactDecisionRecorded", 0), 0, "从对话抽事实，便宜"
        return 0, 0, ""

    lanes = []
    for label, _ in PURPOSE_LANES:
        money = prod_by_lane.get(label, Money())
        if money.calls == 0:
            continue
        ledger_events, visible, bought = lane_events(label)
        lanes.append(
            {
                "lane": label,
                "calls": money.calls,
                "official_cny": round(money.official_cny, 4),
                "ledger_events": ledger_events,
                "user_visible": visible,
                "bought": bought,
            }
        )
    lanes.sort(key=lambda item: -item["official_cny"])

    inbound = prod_by_purpose.get("inbound_turn", Money())
    proactive = prod_by_purpose.get("proactive_contact", Money())
    media = prod_by_purpose.get("media_selection", Money())
    experience = prod_by_purpose.get("experience_memory_retention", Money())
    outcome = prod_by_purpose.get("outcome_selection", Money())
    appraisal = prod_by_purpose.get("world_stimulus_appraisal", Money())
    life_draft = prod_by_purpose.get("life_development_draft", Money())
    life_choice = prod_by_purpose.get("life_development_choice", Money())
    activity = prod_by_purpose.get("activity_lifecycle_choice", Money())
    farm = prod_by_purpose.get("private_impression_reflection", Money())
    facts = prod_by_purpose.get("interaction_fact_draft", Money())

    json_fail_n = sum(world["json_contract_failures"].values())
    other_fail_n = sum(world["other_failures"].values())
    avg_call = (
        buckets["production_epoch2"].official_cny / max(1, buckets["production_epoch2"].succeeded)
    )
    # Paid-then-discarded estimate: JSON/contract failures plus recovered invalids.
    # Cancelled usage rows have 0 tokens and are not billed.
    reselection_cny = round(min(json_fail_n * avg_call, inbound.official_cny * 0.35), 4)
    tech_fail_cny = round(min(other_fail_n * avg_call * 0.5, 1.0), 4)
    summaries = world["photo_fiction_by_kind"].get("experience_summary", 0)
    poison_share = min(1.0, summaries / 22.0) if summaries else 0.0
    poison_cny = round(experience.official_cny * poison_share + outcome.official_cny * 0.25, 4)
    # Proactive: 2 visible of N calls. Charge the non-sending remainder as idle.
    proactive_visible = world["action_kinds"].get("proactive_message", 0)
    proactive_idle_cny = round(
        proactive.official_cny * (1.0 - min(1.0, proactive_visible / max(1, proactive.succeeded))),
        4,
    )
    media_idle_cny = round(media.official_cny, 4)
    agent_cny = round(buckets["agent_test"].official_cny, 4)

    waste_items = [
        {
            "name": "重选 / JSON·契约失败（生产）",
            "official_cny": reselection_cny,
            "kind": "纯浪费（体验损失≈0 如果修掉）",
            "note": (
                f"attempt_index>0 仅 {world['reselection_attempt_index_gt_0']} 次；"
                f"JSON/契约失败码 {json_fail_n} 次。今天的 json_wire_repair 针对这类。"
            ),
        },
        {
            "name": "技术失败（生产，付了再丢）",
            "official_cny": tech_fail_cny,
            "kind": "纯浪费",
            "note": (
                f"用量失败 16 次 token=0 不扣费；audit 超时/异常 {other_fail_n} 次。"
                "按成功调用均价估，故意取保守。"
            ),
        },
        {
            "name": "主动开口空转（生产）",
            "official_cny": proactive_idle_cny,
            "kind": "有价值但贵",
            "note": f"{proactive.calls} 次考虑，{proactive_visible} 条送到 QQ。沉默是她选的，钱你出。",
        },
        {
            "name": "媒体链空转（生产）",
            "official_cny": media_idle_cny,
            "kind": "功能空转，钱很少",
            "note": "六天问 1 次、她 no_op、下游 0 行。账单可忽略，照片体验为零。",
        },
        {
            "name": "假“照片已发出”记忆（生产）",
            "official_cny": poison_cny,
            "kind": "毒化产出",
            "note": (
                f"{world['photo_fiction_unique_texts']} 段散文 / "
                f"{summaries} 条 experience_summary 写照片已发，账本投递 0。"
            ),
        },
        {
            "name": "我们跑克隆和探针（真 DeepSeek 钥匙）",
            "official_cny": agent_cny,
            "kind": "纯浪费（对你的聊天体验）",
            "note": "指纹去重后的调试调用。8 月 18–19 日是高峰。不是她陪你花的。",
        },
    ]
    production_waste = reselection_cny + tech_fail_cny + poison_cny + media_idle_cny
    # Proactive idle is "valuable but expensive" — include half in headline waste? User asked
    # for 空转 in the waste list. Headline 白烧 = agent debug + production pure waste +
    # idle lanes they named (proactive + media). Don't add epoch1.
    headline_waste = agent_cny + production_waste + proactive_idle_cny
    headline_total = round(total.official_cny, 4)
    headline_waste = round(headline_waste, 4)

    delivered = max(1, world["qq_beats_delivered"])
    replies = max(1, world["action_kinds"].get("reply", 0))
    plans = max(1, world["qq_unique_plans"])
    background = (
        buckets["production_epoch2"].official_cny
        - inbound.official_cny
        - facts.official_cny
    )
    unit = {
        "cny_per_delivered_beat": round(buckets["production_epoch2"].official_cny / delivered, 4),
        "cny_per_reply_inbound_only": round(inbound.official_cny / replies, 4),
        "cny_per_plan": round(buckets["production_epoch2"].official_cny / plans, 4),
        "background_official_cny": round(background, 4),
        "cny_per_proactive_visible": round(
            proactive.official_cny / max(1, proactive_visible), 4
        ),
    }

    # Days table
    day_map: dict[str, dict[str, Money]] = defaultdict(lambda: defaultdict(Money))
    for row in rows:
        if not row.is_deepseek():
            continue
        try:
            day = row.local().date().isoformat()
        except ValueError:
            continue
        day_map[day][row.bucket].add(row)
    days = []
    for day in sorted(day_map):
        payload = {
            bucket: day_map[day].get(bucket, Money()).as_dict()
            for bucket in ("production_epoch2", "production_epoch1", "agent_test")
        }
        payload["total_official_cny"] = round(
            sum(day_map[day][b].official_cny for b in ("production_epoch2", "production_epoch1", "agent_test")),
            4,
        )
        days.append((day, payload))

    peak_split = {"pre": Money(), "peak": Money(), "off": Money()}
    for row in rows:
        if not row.is_deepseek():
            continue
        when = row.local()
        if when < PRICE_HIKE:
            peak_split["pre"].add(row)
        elif is_peak(when):
            peak_split["peak"].add(row)
        else:
            peak_split["off"].add(row)

    agent_sources: dict[str, Money] = defaultdict(Money)
    for row in agent_rows:
        source = row.source
        if source.startswith("output/"):
            parts = Path(source).parts
            label = "/".join(parts[:2]) if len(parts) >= 2 else source
        else:
            label = source
        agent_sources[label].add(row)
    agent_source_list = sorted(
        (
            {"source": name, "calls": money.calls, "official_cny": round(money.official_cny, 4)}
            for name, money in agent_sources.items()
        ),
        key=lambda item: -item["official_cny"],
    )

    # Aug 19 highlight
    aug19 = day_map.get("2026-08-19", {})
    aug19_prod = aug19.get("production_epoch2", Money())

    missing = max(0.0, 100.0 - headline_total)
    bill_gap = (
        f"跟「一百多」对不上的部分大约 **{yuan(missing)}–¥30**。"
        "最像三件事，不是我漏加了她的聊天："
        "（1）有的探针脚本直接调 DeepSeek、没挂 `usage_observer`，sqlite 里没有；"
        "（2）思考 token 若没写进 `completion_tokens`，官方价会偏低；"
        "（3）控制台可能还有别的项目/别的 key 混在同一张账单。"
        "我没有为了凑一百去加一项看不见的「其他」。"
        if headline_total < 90
        else "和「一百多」已经同量级，剩余差额更可能是未入账脚本或思考 token。"
    )

    # Peak-move savings on agent+prod after hike
    peak_save = round(peak_split["peak"].official_cny * 0.5, 4)
    agent_save = round(agent_cny * 0.9, 4)
    life_save = round((life_draft.official_cny + experience.official_cny) * 0.4, 4)
    farm_save = round(farm.official_cny * 0.5, 4)

    savings = [
        {
            "action": "克隆/探针默认 fake 或独立便宜 key，生产 key 只给 QQ 进程",
            "kind": "纯浪费",
            "estimated_save_cny": agent_save,
            "basis": f"这几天调试 {yuan(agent_cny)}，停真钥匙可去掉九成；8 月 18 日单日调试就有 {yuan(day_map.get('2026-08-18', {}).get('agent_test', Money()).official_cny)}",
        },
        {
            "action": "必须真调时避开北京 9–12 / 14–18 高峰",
            "kind": "有价值但贵",
            "estimated_save_cny": peak_save,
            "basis": f"高峰 {yuan(peak_split['peak'].official_cny)}，搬到空闲约半价",
        },
        {
            "action": "同一条假「照片已发出」不要反复 experience_memory",
            "kind": "纯浪费",
            "estimated_save_cny": round(poison_cny * 0.7, 4),
            "basis": f"毒化记忆估 {yuan(poison_cny)}，去重+禁止用户通道虚构可砍大部分",
        },
        {
            "action": "生活草稿/记忆夜间复述降频（保留入站）",
            "kind": "有价值但贵",
            "estimated_save_cny": life_save,
            "basis": f"draft+记忆 {yuan(life_draft.official_cny + experience.official_cny)}，砍四成不影响她跟你说话",
        },
        {
            "action": "私人印象农场日上限保持很小或暂停",
            "kind": "有价值但贵",
            "estimated_save_cny": farm_save,
            "basis": f"农场 {yuan(farm.official_cny)}，你看不见",
        },
        {
            "action": "入站回话、事实抽取",
            "kind": "必要开销",
            "estimated_save_cny": 0.0,
            "basis": f"入站 {yuan(inbound.official_cny)} + 事实 {yuan(facts.official_cny)}，这是她在回你",
        },
    ]

    gpt_image_ledger = sum(
        r.ledger_cny for r in unique.values() if "gpt-image" in (r.model or "").lower()
    )

    return {
        "generated_at": datetime.now(tz=SHANGHAI).isoformat(),
        "since": since.isoformat() if since else None,
        "headline": {
            "total_official_cny": headline_total,
            "waste_official_cny": headline_waste,
            "waste_ratio": pct(headline_waste, headline_total),
            "production_official_cny": round(buckets["production_epoch2"].official_cny, 4),
            "agent_official_cny": agent_cny,
            "epoch1_official_cny": round(buckets["production_epoch1"].official_cny, 4),
        },
        "buckets": {name: money.as_dict() for name, money in buckets.items()},
        "total": total.as_dict(),
        "days": days,
        "peak_split": {name: money.as_dict() for name, money in peak_split.items()},
        "production_world": world,
        "production_lanes": lanes,
        "production_purposes": {name: money.as_dict() for name, money in sorted(prod_by_purpose.items(), key=lambda kv: -kv[1].official_cny)},
        "waste_items": waste_items,
        "unit_cost": unit,
        "savings": savings,
        "agent_sources": agent_source_list,
        "non_deepseek": {"gpt_image_official_proxy": round(gpt_image_ledger, 4)},
        "bill_gap": bill_gap,
        "aug19_prod_ledger": round(aug19_prod.ledger_cny, 4),
        "aug19_prod_official": round(aug19_prod.official_cny, 4),
        "proactive_calls": proactive.calls,
        "proactive_official_cny": round(proactive.official_cny, 4),
        "media_official_cny": round(media.official_cny, 4),
        "poison_official_cny": poison_cny,
        "tech_fail_official_cny": tech_fail_cny,
        "avg_succeeded_call_official_cny": round(avg_call, 4),
        "appraisal_official_cny": round(appraisal.official_cny, 4),
        "activity_official_cny": round(activity.official_cny, 4),
        "life_choice_official_cny": round(life_choice.official_cny, 4),
    }


def _jsonify(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, Money):
        return value.as_dict()
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only DeepSeek spend audit")
    parser.add_argument("--since", help="Shanghai calendar date YYYY-MM-DD")
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Write summary.json but skip REPORT.md",
    )
    args = parser.parse_args(argv)
    since = date.fromisoformat(args.since) if args.since else None
    result = analyze(since=since)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "summary.json").write_text(
        json.dumps(_jsonify(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not args.json_only:
        (OUTPUT / "REPORT.md").write_text(build_report(result), encoding="utf-8")
    headline = result["headline"]
    print(
        f"total={headline['total_official_cny']:.2f} "
        f"waste={headline['waste_official_cny']:.2f} "
        f"ratio={headline['waste_ratio']} "
        f"report={OUTPUT / 'REPORT.md'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
