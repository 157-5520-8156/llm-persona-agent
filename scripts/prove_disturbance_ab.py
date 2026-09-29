#!/usr/bin/env python3
"""Force-disturbance vs ordinary Life Development draft A/B on a production clone.

Never writes ``data/``. Artifacts live in ``output/disturbance-ab/``.
Budget hard cap enforced at runtime (default ¥1).
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Literal

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
from companion_daemon.config import Settings
from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.aspiration_view import active_aspiration_advisories
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.life_context import compile_life_decision_context
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_capability import (
    ProjectionLifeCapabilityManifestCompiler,
)
from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentNoOpDraft,
    LifeDevelopmentPossibilityDraft,
)
from companion_daemon.world_v2.life_development_model_adapter import (
    RoleBoundLifeDevelopmentModelAdapter,
)
from companion_daemon.world_v2.life_development_runtime import (
    LifeDevelopmentRuntime,
    compile_pressure_surfaces,
    outcome_has_durable_world_consequence,
    validate_disturbance_consequence_closure,
)
from companion_daemon.world_v2.ledger_context_resolver import (
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
from companion_daemon.world_v2.schemas import WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

OUTPUT = (REPO / "output" / "disturbance-ab").resolve()
WORLD_ID = drive.WORLD_ID
ACTOR = "agent:companion"
SEED_PATH = REPO / "configs" / "world_seed.yaml"
SAMPLES_PER_GROUP = 4
BUDGET_CNY = 0.6

THEME_MARKERS = (
    "照片",
    "书店",
    "安静",
    "好滴",
    "睡",
    "图书馆",
    "边城",
    "徐青禾",
    "分享",
    "雨",
    "电影",
    "实习",
    "论文",
    "母亲",
    "父亲",
)
NEGATIVE_MARKERS = ("冲突", "争执", "吵", "失败", "落空", "拒绝", "放鸽子", "不来", "烦", "累", "打断")
EXCITEMENT_MARKERS = ("兴奋", "雀跃", "太好了", "开心得", "激动", "惊喜")


def _cost_cny(usage_db: Path) -> float:
    if not usage_db.exists():
        return 0.0
    return float((drive.cost_report(usage_db, since_id=0) or {}).get("cost_cny") or 0)


def _serialize_outcome(outcome) -> dict[str, Any]:
    return {
        "text": outcome.text,
        "privacy_class": outcome.privacy_class,
        "relative_plausibility_weight": outcome.relative_plausibility_weight,
        "has_durable_consequence": outcome_has_durable_world_consequence(outcome),
        "dynamic_life_direction": (
            outcome.dynamic_life_direction.model_dump(mode="json")
            if outcome.dynamic_life_direction is not None
            else None
        ),
        "objective_biographical_transition": (
            outcome.objective_biographical_transition.model_dump(mode="json")
            if outcome.objective_biographical_transition is not None
            else None
        ),
        "provisional_npcs": [
            item.model_dump(mode="json") for item in outcome.provisional_npcs
        ],
        "provisional_places": [
            item.model_dump(mode="json") for item in outcome.provisional_places
        ],
    }


def _serialize_sample(parsed, *, occasion_mode: str, index: int) -> dict[str, Any]:
    row: dict[str, Any] = {
        "index": index,
        "occasion_mode": occasion_mode,
        "decision": getattr(parsed, "decision", None),
    }
    if isinstance(parsed, LifeDevelopmentNoOpDraft):
        row["kind"] = "no_op"
        row["text"] = "(no_op — World Author chose nothing this occasion)"
        return row
    if not isinstance(parsed, LifeDevelopmentPossibilityDraft):
        row["kind"] = "invalid"
        return row
    row["kind"] = "propose"
    row["premise"] = parsed.premise
    row["causal_authority"] = parsed.causal_authority
    row["outcome_resolution_authority"] = parsed.outcome_resolution_authority
    row["outcomes"] = [_serialize_outcome(item) for item in parsed.outcomes]
    row["full_text"] = parsed.premise + "\n\n" + "\n\n".join(
        f"[{idx + 1}] {item.text}" for idx, item in enumerate(parsed.outcomes)
    )
    row["theme_hits"] = _theme_hits(row["full_text"])
    row["negative_hits"] = [m for m in NEGATIVE_MARKERS if m in row["full_text"]]
    row["excitement_hits"] = [m for m in EXCITEMENT_MARKERS if m in row["full_text"]]
    row["schema_durable"] = any(item["has_durable_consequence"] for item in row["outcomes"])
    if occasion_mode == "disturbance":
        try:
            validate_disturbance_consequence_closure(parsed)
            row["passes_disturbance_gate"] = True
        except Exception as exc:
            row["passes_disturbance_gate"] = False
            row["disturbance_gate_reason"] = str(exc)
        row["consequence_audit"] = _audit_consequences(parsed, row)
    return row


def _theme_hits(text: str) -> dict[str, bool]:
    return {marker: marker in text for marker in THEME_MARKERS}


def _dominant_theme_chain(text: str) -> str:
    if "照片" in text and ("书店" in text or "安静" in text or "好滴" in text):
        return "书店-照片-安静-被惦记"
    if "照片" in text:
        return "照片余波"
    if "书店" in text or "边城" in text:
        return "书店/阅读"
    if "图书馆" in text:
        return "图书馆"
    if "徐青禾" in text:
        return "朋友徐青禾"
    if "雨" in text or "天气" in text:
        return "天气"
    if "电影" in text:
        return "露天电影"
    if "实习" in text or "论文" in text:
        return "学业/工作"
    if "母亲" in text or "父亲" in text:
        return "父母"
    return "其他"


def _audit_consequences(parsed: LifeDevelopmentPossibilityDraft, row: dict[str, Any]) -> dict[str, Any]:
    blob = row["full_text"]
    categories: list[str] = []
    if row["schema_durable"]:
        if any(item.get("dynamic_life_direction") for item in row["outcomes"]):
            categories.append("dynamic_life_direction")
        if any(item.get("objective_biographical_transition") for item in row["outcomes"]):
            categories.append("objective_biographical_transition")
        if any(item.get("provisional_npcs") for item in row["outcomes"]):
            categories.append("provisional_npc")
        if any(item.get("provisional_places") for item in row["outcomes"]):
            categories.append("provisional_place")
    plan_tokens = ("计划", "取消", "放弃", "推迟", "暂停", "改期", "来不及")
    if any(token in blob for token in plan_tokens):
        categories.append("plan_language")
    npc_tokens = ("徐青禾", "母亲", "父亲", "范予安", "林晚")
    if parsed.causal_authority == "world_contingency" and any(t in blob for t in npc_tokens):
        categories.append("npc_world_moves")
    constraint_tokens = ("关店", "没空", "闭馆", "下雨", "堵车", "截止", "来不及", "冲突")
    if any(token in blob for token in constraint_tokens):
        categories.append("resource_or_time_constraint")
    round_back = ("不过", "反而", "最后还是", "也算", "心里也", "也挺好", "慢慢就")
    atmosphere_only = (
        not row["schema_durable"]
        or (
            row["schema_durable"]
            and sum(1 for token in round_back if token in blob) >= 2
            and not categories
        )
    )
    if not row["schema_durable"]:
        landing = "prose_only"
    elif atmosphere_only and len(categories) <= 1 and categories == ["dynamic_life_direction"]:
        landing = "schema_but_atmosphere"
    elif row["passes_disturbance_gate"] and len(categories) >= 1:
        landing = "real_consequence"
    elif row["passes_disturbance_gate"]:
        landing = "schema_minimal"
    else:
        landing = "prose_only"
    return {
        "categories": categories,
        "landing": landing,
        "causal_authority": parsed.causal_authority,
    }


def _group_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    proposes = [item for item in samples if item.get("kind") == "propose"]
    chains = Counter(_dominant_theme_chain(item.get("full_text") or "") for item in proposes)
    dominant_count = chains.most_common(1)[0][1] if chains else 0
    return {
        "n_total": len(samples),
        "n_propose": len(proposes),
        "n_no_op": sum(1 for item in samples if item.get("kind") == "no_op"),
        "theme_chain_counts": dict(chains),
        "dominant_chain_share": (
            round(dominant_count / len(proposes), 3) if proposes else None
        ),
        "baseline_compare_23_of_24": (
            round(dominant_count / len(proposes), 3) if proposes else None
        ),
        "photo_hits": sum(1 for item in proposes if item.get("theme_hits", {}).get("照片")),
        "bookstore_hits": sum(1 for item in proposes if item.get("theme_hits", {}).get("书店")),
        "negative_events": sum(1 for item in proposes if item.get("negative_hits")),
        "excitement_events": sum(1 for item in proposes if item.get("excitement_hits")),
        "negative_marker_total": sum(len(item.get("negative_hits") or []) for item in proposes),
        "excitement_marker_total": sum(len(item.get("excitement_hits") or []) for item in proposes),
    }


def _consequence_rates(samples: list[dict[str, Any]]) -> dict[str, Any]:
    proposes = [item for item in samples if item.get("kind") == "propose"]
    landings = Counter(
        (item.get("consequence_audit") or {}).get("landing", "unknown") for item in proposes
    )
    real = landings.get("real_consequence", 0) + landings.get("schema_minimal", 0)
    prose = landings.get("prose_only", 0) + landings.get("schema_but_atmosphere", 0)
    gate_pass = sum(1 for item in proposes if item.get("passes_disturbance_gate"))
    return {
        "n_propose": len(proposes),
        "passes_disturbance_gate": gate_pass,
        "landing_counts": dict(landings),
        "real_consequence_rate": round(real / len(proposes), 3) if proposes else None,
        "prose_only_rate": round(prose / len(proposes), 3) if proposes else None,
    }


def compile_pinned(*, database: Path) -> tuple[dict[str, object], object, WorldEvent, dict[str, object]]:
    if drive._is_production_path(database):
        raise SystemExit(f"refusing production ledger: {database}")
    ledger = SQLiteWorldLedger(path=database, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(database), world_id=WORLD_ID)
    try:
        projection = ledger.project()
        compiler = context_capsule_compiler_from_ledger(ledger=ledger, life_content_store=store)
        clock_ref = next(
            (
                item.event_id
                for item in reversed(projection.committed_world_event_refs)
                if item.event_type == "ClockAdvanced"
            ),
            None,
        )
        if clock_ref is None:
            raise SystemExit("clone has no ClockAdvanced wake")
        located = ledger.lookup_event_commit(clock_ref)
        if located is None:
            raise SystemExit(f"could not resolve wake event {clock_ref}")
        wake_event = located[0]
        query = query_from_projection(projection, actor_ref=ACTOR, trigger_ref=wake_event.event_id)
        handle = compiler.compile_for_deliberation(query)
        context = compile_life_decision_context(handle.capsule)
        advisories = active_aspiration_advisories(projection)
        if advisories:
            context = {
                **context,
                "active_aspirations": [item.model_dump(mode="json") for item in advisories],
            }
        catalog = ReviewedLifeSeedCatalog.from_yaml(
            path=SEED_PATH,
            chronology=LocalChronology("Asia/Shanghai"),
        )
        manifest_compiler = ProjectionLifeCapabilityManifestCompiler(
            owner_actor_ref=ACTOR,
            catalog=catalog,
            content_store=store,
        )
        manifest = manifest_compiler.compile(
            projection=projection,
            wake=wake_event,
            capsule=handle.capsule,
        )
        pressure = compile_pressure_surfaces(
            manifest=manifest,
            context=context,
            projection=projection,
            logical_time=projection.logical_time,
            owner_actor_ref=ACTOR,
            content_store=store,
        )
        meta = {
            "logical_time": projection.logical_time.isoformat() if projection.logical_time else None,
            "world_revision": projection.world_revision,
            "ledger_sequence": projection.ledger_sequence,
            "wake_event_ref": wake_event.event_id,
            "pressure_surfaces": pressure,
            "location_capability_count": len(manifest.location_capabilities),
            "npc_capability_count": len(manifest.npc_capabilities),
        }
        return context, manifest, wake_event, meta
    finally:
        store.close()


async def _run_group(
    *,
    runtime: LifeDevelopmentRuntime,
    context: dict[str, object],
    manifest,
    wake_event: WorldEvent,
    base_time: datetime,
    occasion_mode: Literal["ordinary", "disturbance"],
    n: int,
    usage_db: Path,
    cost_cap: float,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    attempt = 0
    while len(samples) < n:
        if _cost_cny(usage_db) >= cost_cap:
            break
        attempt += 1
        logical_time = base_time + timedelta(minutes=attempt * 23 + (7 if occasion_mode == "disturbance" else 0))
        run = await runtime._world_author_draft(
            context=context,
            logical_time=logical_time,
            manifest=manifest,
            wake_event_ref=f"{wake_event.event_id}:ab:{occasion_mode}:{attempt}",
            occasion_mode=occasion_mode,
        )
        parsed = run.parsed
        if parsed is None:
            samples.append(
                {
                    "index": len(samples) + 1,
                    "occasion_mode": occasion_mode,
                    "kind": "technical_failure",
                    "text": f"(technical failure: {run.attempts[-1].failure_code if run.attempts else 'unknown'})",
                }
            )
            continue
        row = _serialize_sample(parsed, occasion_mode=occasion_mode, index=len(samples) + 1)
        row["attempt"] = attempt
        row["cost_cny_after"] = _cost_cny(usage_db)
        samples.append(row)
    return samples


def _landing_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    proposes = [item for item in samples if item.get("kind") == "propose"]
    gate_pass = sum(1 for item in proposes if item.get("passes_disturbance_gate"))
    durable = sum(1 for item in proposes if item.get("schema_durable"))
    go_stay = sum(
        1
        for item in proposes
        if item.get("causal_authority") == "character_choice"
        and item.get("outcome_resolution_authority") == "character_choice"
    )
    return {
        "n_propose": len(proposes),
        "passes_disturbance_gate": gate_pass,
        "schema_durable_rate": round(durable / len(proposes), 3) if proposes else None,
        "go_stay_shape_rate": round(go_stay / len(proposes), 3) if proposes else None,
    }


async def run_experiment(*, cost_cap: float) -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    usage_db = OUTPUT / "usage.sqlite"
    usage_db.unlink(missing_ok=True)
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    context, manifest, wake_event, meta = compile_pinned(database=clone)
    base_time = datetime.fromisoformat(meta["logical_time"].replace("Z", "+00:00"))

    settings = Settings()
    if not settings.deepseek_api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required")
    usage_store = WorldV2UsageStore(path=str(usage_db))
    inner = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking_enabled=False,
        max_completion_tokens=4_096,
        usage_observer=usage_store.record,
    )
    author = RoleBoundLifeDevelopmentModelAdapter(model=inner, role="world_author")
    ledger = SQLiteWorldLedger(path=clone, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(clone), world_id=WORLD_ID)
    catalog = ReviewedLifeSeedCatalog.from_yaml(
        path=SEED_PATH,
        chronology=LocalChronology("Asia/Shanghai"),
    )
    runtime = LifeDevelopmentRuntime(
        ledger=ledger,
        content_store=store,
        world_author=author,
        character_interior=SimpleNamespace(consider=None),
        capsule_compiler=SimpleNamespace(compile_for_deliberation=lambda query: None),
        capability_manifest_compiler=ProjectionLifeCapabilityManifestCompiler(
            owner_actor_ref=ACTOR,
            catalog=catalog,
            content_store=store,
        ),
        owner_actor_ref=ACTOR,
    )
    try:
        group_a: list[dict[str, Any]] = []
        group_b: list[dict[str, Any]] = []
        for round_index in range(SAMPLES_PER_GROUP):
            if _cost_cny(usage_db) >= cost_cap:
                break
            group_a.extend(
                await _run_group(
                    runtime=runtime,
                    context=context,
                    manifest=manifest,
                    wake_event=wake_event,
                    base_time=base_time + timedelta(hours=round_index),
                    occasion_mode="disturbance",
                    n=1,
                    usage_db=usage_db,
                    cost_cap=cost_cap,
                )
            )
            if _cost_cny(usage_db) >= cost_cap:
                break
            group_b.extend(
                await _run_group(
                    runtime=runtime,
                    context=context,
                    manifest=manifest,
                    wake_event=wake_event,
                    base_time=base_time + timedelta(hours=round_index, minutes=30),
                    occasion_mode="ordinary",
                    n=1,
                    usage_db=usage_db,
                    cost_cap=cost_cap,
                )
            )
    finally:
        store.close()

    cost = drive.cost_report(usage_db, since_id=0)
    report = {
        "meta": meta,
        "cost_cap_cny": cost_cap,
        "cost": cost,
        "group_a_disturbance": group_a,
        "group_b_ordinary": group_b,
        "metrics_a": _group_metrics(group_a),
        "metrics_b": _group_metrics(group_b),
        "consequence_a": _consequence_rates(group_a),
        "landing_a": _landing_metrics(group_a),
        "landing_b": _landing_metrics(group_b),
        "pressure_surfaces_delivered": meta.get("pressure_surfaces"),
    }
    (OUTPUT / "evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    write_report(report)
    return report


def write_report(report: dict[str, Any]) -> None:
    ma = report["metrics_a"]
    mb = report["metrics_b"]
    ca = report["consequence_a"]
    cost = report["cost"]
    lines = [
        "# Life Development disturbance A/B",
        "",
        "Forced-branch sample on production clone (`companion.epoch2`). "
        "No scheduler time advance; only `life_development_draft` calls.",
        "",
        f"Pinned wake: `{report['meta']['wake_event_ref']}` at {report['meta']['logical_time']}. "
        f"Pressure surfaces active plans={len(report['meta']['pressure_surfaces'].get('active_plans') or [])}, "
        f"NPC caps={report['meta']['npc_capability_count']}, "
        f"location caps={report['meta']['location_capability_count']}.",
        "",
        "## 一、多样性",
        "",
        "### 基线（flat-world epoch2 settled occurrences）",
        "",
        "- 24 条 settlement 中 **23/24** 同一主题链（书店/照片/安静/被惦记的暖）",
        "- 冲突/失败/被拒绝/明显兴奋：**0/24**",
        "",
        "### A 组 — 强制 disturbance（n={})".format(ma["n_total"]),
        "",
        f"- propose/no_op: {ma['n_propose']}/{ma['n_no_op']}",
        f"- 主题链分布: `{ma['theme_chain_counts']}`",
        f"- 最大链占比: **{ma['dominant_chain_share']}**（基线 23/24 ≈ 0.958）",
        f"- 含「照片」: {ma['photo_hits']}/{ma['n_propose']}；含「书店」: {ma['bookstore_hits']}/{ma['n_propose']}",
        f"- 带负面标记样本: {ma['negative_events']}/{ma['n_propose']}；兴奋标记: {ma['excitement_events']}/{ma['n_propose']}",
        "",
        "### B 组 — 强制 ordinary（n={})".format(mb["n_total"]),
        "",
        f"- propose/no_op: {mb['n_propose']}/{mb['n_no_op']}",
        f"- 主题链分布: `{mb['theme_chain_counts']}`",
        f"- 最大链占比: **{mb['dominant_chain_share']}**",
        f"- 含「照片」: {mb['photo_hits']}/{mb['n_propose']}；含「书店」: {mb['bookstore_hits']}/{mb['n_propose']}",
        f"- 带负面标记样本: {mb['negative_events']}/{mb['n_propose']}；兴奋标记: {mb['excitement_events']}/{mb['n_propose']}",
        "",
        "### 对照结论",
        "",
    ]
    a_dom = ma.get("dominant_chain_share")
    b_dom = mb.get("dominant_chain_share")
    if a_dom is not None and b_dom is not None:
        if abs(a_dom - b_dom) < 0.15 and ma["photo_hits"] == mb["photo_hits"]:
            lines.append(
                "**A 组与 B 组在主题分布上几乎看不出差别** —— 扰动 prompt/pressure_surfaces "
                "未改变 World Author 吸引子；不是概率问题，机制未生效或需更深输入面。"
            )
        elif a_dom < 0.7 or ma["photo_hits"] < mb["photo_hits"]:
            lines.append(
                "A 组主题重复度低于 B 组/基线，disturbance 分支对多样性有可见改善。"
            )
        else:
            lines.append("A 组多样性改善有限，仍高度黏着最近生活纹理。")
    lines.extend(["", "## 二、后果落地（A 组）", ""])
    lines.append(
        f"- 通过 disturbance 结构门（至少一条 outcome 带 durable consequence）: "
        f"**{ca['passes_disturbance_gate']}/{ca['n_propose']}**"
    )
    lines.append(f"- 落地分类: `{ca['landing_counts']}`")
    lines.append(
        f"- **真有后果**（real+schema_minimal）: {ca['real_consequence_rate']}；"
        f"**只是散文**（prose+atmosphere）: {ca['prose_only_rate']}"
    )
    lines.extend(["", "### 逐条 A 组", ""])
    for item in report["group_a_disturbance"]:
        lines.append(f"#### A-{item.get('index', '?')} ({item.get('kind')})")
        lines.append("")
        if item.get("kind") == "propose":
            audit = item.get("consequence_audit") or {}
            lines.append(
                f"- gate={item.get('passes_disturbance_gate')} landing={audit.get('landing')} "
                f"categories={audit.get('categories')} causal={item.get('causal_authority')}"
            )
            lines.append("")
            lines.append("**premise**")
            lines.append("")
            lines.append(item.get("premise") or "")
            lines.append("")
            for idx, outcome in enumerate(item.get("outcomes") or (), start=1):
                lines.append(f"**outcome {idx}** (durable={outcome.get('has_durable_consequence')})")
                lines.append("")
                lines.append(outcome.get("text") or "")
                lines.append("")
        else:
            lines.append(item.get("text") or str(item))
            lines.append("")
    lines.extend(["", "## A 组全部原文（紧凑）", ""])
    for item in report["group_a_disturbance"]:
        if item.get("full_text"):
            lines.append(f"### A-{item['index']}")
            lines.append("")
            lines.append(item["full_text"])
            lines.append("")
    lines.extend(["", "## B 组全部原文", ""])
    for item in report["group_b_ordinary"]:
        lines.append(f"### B-{item.get('index', '?')} ({item.get('kind')})")
        lines.append("")
        if item.get("full_text"):
            lines.append(item["full_text"])
        else:
            lines.append(item.get("text") or str(item))
        lines.append("")
    lines.extend(["", "## 三、概率建议", ""])
    gate = ca.get("passes_disturbance_gate") or 0
    n_prop = ca.get("n_propose") or 0
    real_rate = ca.get("real_consequence_rate") or 0
    if n_prop == 0:
        lines.append("样本不足，暂不建议调概率。")
    elif gate < max(2, n_prop // 3) or real_rate < 0.3:
        lines.append(
            "当前 **600/10000（≈6%）不宜上调**。A 组多数未通过 consequence 门或落地为散文；"
            "先修 World Author 输入/约束，再谈频率。"
        )
    elif a_dom is not None and b_dom is not None and a_dom < b_dom - 0.2 and real_rate >= 0.5:
        lines.append(
            "A 组质量可接受：多样性优于 B 组且落地率 ≥50%。可维持 **600/10000** 作稀疏起点；"
            "若克隆 7 日审计仍 flat，再试 **900–1200/10000**，需附带 consequence 落地率门禁。"
        )
    else:
        lines.append(
            "多样性有轻微改善但 consequence 落地不稳：保持 **600/10000**，"
            "优先修 pressure_surfaces 与 acceptance，不增概率。"
        )
    lines.extend(
        [
            "",
            "## 四、实际花费",
            "",
            f"- 总花费：**¥{cost.get('cost_cny', 0)}**",
            f"- 调用次数：{cost.get('calls', 0)} `{cost.get('by_purpose')}`",
            f"- 预算上限：¥{report['cost_cap_cny']}",
            "",
        ]
    )
    (OUTPUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost-cap", type=float, default=BUDGET_CNY)
    args = parser.parse_args()
    report = asyncio.run(run_experiment(cost_cap=args.cost_cap))
    print(json.dumps({"cost": report["cost"], "metrics_a": report["metrics_a"], "metrics_b": report["metrics_b"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
