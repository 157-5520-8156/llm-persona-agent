"""Read-only cost & token meter for the authenticated World v2 Dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")
# Metered pay-per-use rate for GLM-5.3-Flash on EdgeFn (~0.36 CNY / 1M tokens).
# NOTE: GLM-5.3-Flash is billed per token (NOT free / subscription); field names retain legacy compatibility.
_SUBSCRIPTION_EQUIV_CNY_PER_MILLION_TOKENS = 0.36

PURPOSE_LABELS: dict[str, tuple[str, str]] = {
    "life_development_draft": ("世界推演与远景规划", "后台生活与演化"),
    "life_development_novel_origin_review": ("剧本新事实来源审查", "后台生活与演化"),
    "life_development_source_closure_review": ("世界结果来源闭包审查", "后台生活与演化"),
    "life_development_source_rewrite": ("世界结果来源局部修正", "后台生活与演化"),
    "life_source_review": ("后台生活事实来源审查", "后台生活与演化"),
    "life_development_choice": ("知栀自主日常安排选择", "后台生活与演化"),
    "activity_lifecycle_choice": ("活动状态推进选择", "后台生活与演化"),
    "outcome_selection": ("多分支世界结果落点选择", "后台生活与演化"),
    "character_life_experience": ("亲身经历第一人称沉淀", "后台生活与演化"),
    "experience_memory_retention": ("亲身经历转长期记忆评估", "后台生活与演化"),
    "fact_memory_retention": ("世界事实转长期记忆评估", "后台生活与演化"),
    "memory_consolidation": ("长期记忆周期性温习整合", "后台生活与演化"),
    "memory_withdrawal_review": ("记忆撤回与修正复核", "后台生活与演化"),
    "private_impression_reflection": ("对你的私密印象沉淀", "后台生活与演化"),
    "interaction_fact_draft": ("互动对话事实提取", "后台生活与演化"),
    "npc_actor_decision": ("身边 NPC 独立行动决策", "后台生活与演化"),
    "world_stimulus_appraisal": ("世界事件刺激情绪评估", "后台生活与演化"),
    "external_perception_attention": ("B 站冲浪注意力采样", "感知与视觉媒体"),
    "external_perception_appraisal": ("B 站冲浪内容主观评价", "感知与视觉媒体"),
    "qq_attachment_perception": ("聊天图片与附件多模态识别", "感知与视觉媒体"),
    "media_selection": ("随手拍相册候选挑选", "感知与视觉媒体"),
    "media_candidate_selection": ("随手拍相册候选挑选", "感知与视觉媒体"),
    "media_planning": ("照片画面构图与提示词规划", "感知与视觉媒体"),
    "media_planning_repair": ("照片画面提示词合规修复", "感知与视觉媒体"),
    "media_generation_plan": ("照片画面构图与提示词规划", "感知与视觉媒体"),
    "media_visual_inspection": ("成片画面质量与一致性自检", "感知与视觉媒体"),
    "image_generation": ("AI 图像生成渲染", "感知与视觉媒体"),
    "inbound_turn": ("收到你消息后的即时回复", "前台聊天与表达"),
    "proactive_contact": ("主动找你搭话与分享日常", "前台聊天与表达"),
    "expression_reconsideration": ("发送前气泡重审与微调", "前台聊天与表达"),
    "character_turn": ("聊天回复与主动发起决策", "前台聊天与表达"),
    "visible_source_review": ("可见发言事实来源审查", "前台聊天与表达"),
    "inbound_source_review": ("对话回复事实来源审查", "前台聊天与表达"),
    "source_review": ("发言事实来源闭包审查", "前台聊天与表达"),
    "text_shadow_review": ("影子发言对照评估", "前台聊天与表达"),
    "expression_episode_observer": ("对话节拍与模式观察", "前台聊天与表达"),
    "recall_embedding": ("记忆语义向量检索 Embedding", "后台生活与演化"),
}


def _purpose_meta(purpose: str) -> tuple[str, str]:
    if purpose in PURPOSE_LABELS:
        return PURPOSE_LABELS[purpose]
    if "perception" in purpose or "media" in purpose or "image" in purpose:
        return (purpose, "感知与视觉媒体")
    if (
        "turn" in purpose
        or "visible" in purpose
        or "expression" in purpose
        or "proactive" in purpose
        or "inbound" in purpose
    ):
        return (purpose, "前台聊天与表达")
    return (purpose, "后台生活与演化")


def _to_shanghai_date_and_clock(raw: object) -> tuple[str, str, float]:
    if isinstance(raw, (int, float)):
        ts = float(raw)
        dt = datetime.fromtimestamp(ts, tz=_SHANGHAI)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%m-%d %H:%M:%S"), ts
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_SHANGHAI)
            else:
                dt = dt.astimezone(_SHANGHAI)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%m-%d %H:%M:%S"), dt.timestamp()
        except ValueError:
            return text[:10], text[:19], 0.0
    return "", "", 0.0


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _subscription_db_candidates(db_path: Path) -> tuple[Path, ...]:
    first = Path(str(db_path) + ".subscription.sqlite")
    second = db_path.with_suffix(".subscription.sqlite")
    return (first,) if first == second else (first, second)


def build_dashboard_cost_snapshot(
    database_path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Read SQLite usage + subscription tables and return a structured cost & token breakdown."""

    current = (now or datetime.now(tz=_SHANGHAI)).astimezone(_SHANGHAI)
    today_str = current.strftime("%Y-%m-%d")
    yesterday_str = (current - timedelta(days=1)).strftime("%Y-%m-%d")
    day_7_cutoff = (current - timedelta(days=6)).strftime("%Y-%m-%d")
    day_14_dates = [
        (current - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(13, -1, -1)
    ]
    daily_map: dict[str, dict[str, object]] = {
        d: {
            "date": d,
            "label": d[5:],
            "total_cny": 0.0,
            "api_cny": 0.0,
            "sub_equiv_cny": 0.0,
            "model_cny": 0.0,
            "media_cny": 0.0,
            "embedding_cny": 0.0,
            "background_cny": 0.0,
            "interactive_cny": 0.0,
            "calls": 0,
            "total_tokens": 0,
            "cache_hit_tokens": 0,
        }
        for d in day_14_dates
    }

    purpose_map: dict[str, dict[str, object]] = {}
    model_map: dict[str, dict[str, object]] = {}
    raw_recent: list[tuple[float, dict[str, object]]] = []

    def ensure_purpose(key: str) -> dict[str, object]:
        if key not in purpose_map:
            label, category = _purpose_meta(key)
            purpose_map[key] = {
                "purpose": key,
                "label": label,
                "category": category,
                "today_calls": 0,
                "today_tokens": 0,
                "today_cny": 0.0,
                "yesterday_calls": 0,
                "yesterday_tokens": 0,
                "yesterday_cny": 0.0,
                "days_7_calls": 0,
                "days_7_tokens": 0,
                "days_7_cny": 0.0,
                "all_time_calls": 0,
                "all_time_tokens": 0,
                "all_time_cache_hit_tokens": 0,
                "all_time_prompt_tokens": 0,
                "all_time_cny": 0.0,
            }
        return purpose_map[key]

    def ensure_model(model_name: str, provider: str, billing_mode: str = "api") -> dict[str, object]:
        key = model_name or "unknown"
        if key not in model_map:
            model_map[key] = {
                "model": key,
                "provider": provider or "",
                "billing_mode": billing_mode,
                "today_calls": 0,
                "today_tokens": 0,
                "today_cny": 0.0,
                "yesterday_cny": 0.0,
                "days_7_cny": 0.0,
                "all_time_calls": 0,
                "all_time_tokens": 0,
                "all_time_prompt_tokens": 0,
                "all_time_cache_hit_tokens": 0,
                "all_time_cny": 0.0,
                "latency_sum_ms": 0.0,
                "latency_count": 0,
            }
        return model_map[key]

    today_api_cny = 0.0
    today_sub_equiv_cny = 0.0
    today_model_cny = 0.0
    today_media_cny = 0.0
    today_embedding_cny = 0.0
    today_calls = 0
    today_total_tokens = 0
    today_prompt_tokens = 0
    today_completion_tokens = 0
    today_cache_hit_tokens = 0

    yesterday_total_cny = 0.0
    yesterday_calls = 0
    yesterday_total_tokens = 0

    days_7_api_cny = 0.0
    days_7_sub_equiv_cny = 0.0
    days_7_model_cny = 0.0
    days_7_media_cny = 0.0
    days_7_embedding_cny = 0.0
    days_7_calls = 0
    days_7_total_tokens = 0

    all_time_api_cny = 0.0
    all_time_sub_equiv_cny = 0.0
    all_time_model_cny = 0.0
    all_time_media_cny = 0.0
    all_time_embedding_cny = 0.0
    all_time_calls = 0
    all_time_total_tokens = 0

    db_path = Path(database_path)
    if db_path.exists():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        try:
            if _table_exists(conn, "world_v2_model_usage"):
                cursor = conn.execute(
                    "SELECT recorded_at, purpose, model, provider, status, "
                    "prompt_tokens, completion_tokens, cache_hit_tokens, total_tokens, "
                    "cost_cny, estimated_cny, latency_ms "
                    "FROM world_v2_model_usage ORDER BY id DESC"
                )
                for (
                    recorded_at,
                    purpose,
                    model_name,
                    provider,
                    status,
                    prompt_tokens,
                    completion_tokens,
                    cache_hit_tokens,
                    total_tokens,
                    cost_cny,
                    estimated_cny,
                    latency_ms,
                ) in cursor:
                    day_str, clock_str, ts = _to_shanghai_date_and_clock(recorded_at)
                    p_tok = int(prompt_tokens or 0)
                    c_tok = int(completion_tokens or 0)
                    h_tok = int(cache_hit_tokens or 0)
                    t_tok = int(total_tokens or (p_tok + c_tok))
                    cny = float(
                        cost_cny
                        if cost_cny is not None
                        else (estimated_cny if estimated_cny is not None else 0.0)
                    )
                    purpose_key = str(purpose or "unknown")
                    model_key = str(model_name or "unknown")
                    p_entry = ensure_purpose(purpose_key)
                    m_entry = ensure_model(model_key, str(provider or ""), "api")

                    all_time_api_cny += cny
                    all_time_model_cny += cny
                    all_time_calls += 1
                    all_time_total_tokens += t_tok

                    p_entry["all_time_calls"] = int(p_entry["all_time_calls"]) + 1
                    p_entry["all_time_tokens"] = int(p_entry["all_time_tokens"]) + t_tok
                    p_entry["all_time_prompt_tokens"] = int(p_entry["all_time_prompt_tokens"]) + p_tok
                    p_entry["all_time_cache_hit_tokens"] = int(p_entry["all_time_cache_hit_tokens"]) + h_tok
                    p_entry["all_time_cny"] = float(p_entry["all_time_cny"]) + cny

                    m_entry["all_time_calls"] = int(m_entry["all_time_calls"]) + 1
                    m_entry["all_time_tokens"] = int(m_entry["all_time_tokens"]) + t_tok
                    m_entry["all_time_prompt_tokens"] = int(m_entry["all_time_prompt_tokens"]) + p_tok
                    m_entry["all_time_cache_hit_tokens"] = int(m_entry["all_time_cache_hit_tokens"]) + h_tok
                    m_entry["all_time_cny"] = float(m_entry["all_time_cny"]) + cny
                    if latency_ms is not None and float(latency_ms) > 0:
                        m_entry["latency_sum_ms"] = float(m_entry["latency_sum_ms"]) + float(latency_ms)
                        m_entry["latency_count"] = int(m_entry["latency_count"]) + 1

                    if day_str >= day_7_cutoff:
                        days_7_api_cny += cny
                        days_7_model_cny += cny
                        days_7_calls += 1
                        days_7_total_tokens += t_tok
                        p_entry["days_7_calls"] = int(p_entry["days_7_calls"]) + 1
                        p_entry["days_7_tokens"] = int(p_entry["days_7_tokens"]) + t_tok
                        p_entry["days_7_cny"] = float(p_entry["days_7_cny"]) + cny
                        m_entry["days_7_cny"] = float(m_entry["days_7_cny"]) + cny

                    if day_str == yesterday_str:
                        yesterday_total_cny += cny
                        yesterday_calls += 1
                        yesterday_total_tokens += t_tok
                        p_entry["yesterday_calls"] = int(p_entry["yesterday_calls"]) + 1
                        p_entry["yesterday_tokens"] = int(p_entry["yesterday_tokens"]) + t_tok
                        p_entry["yesterday_cny"] = float(p_entry["yesterday_cny"]) + cny
                        m_entry["yesterday_cny"] = float(m_entry["yesterday_cny"]) + cny

                    if day_str == today_str:
                        today_api_cny += cny
                        today_model_cny += cny
                        today_calls += 1
                        today_total_tokens += t_tok
                        today_prompt_tokens += p_tok
                        today_completion_tokens += c_tok
                        today_cache_hit_tokens += h_tok
                        p_entry["today_calls"] = int(p_entry["today_calls"]) + 1
                        p_entry["today_tokens"] = int(p_entry["today_tokens"]) + t_tok
                        p_entry["today_cny"] = float(p_entry["today_cny"]) + cny
                        m_entry["today_calls"] = int(m_entry["today_calls"]) + 1
                        m_entry["today_tokens"] = int(m_entry["today_tokens"]) + t_tok
                        m_entry["today_cny"] = float(m_entry["today_cny"]) + cny

                    if day_str in daily_map:
                        d_entry = daily_map[day_str]
                        d_entry["api_cny"] = float(d_entry["api_cny"]) + cny
                        d_entry["model_cny"] = float(d_entry["model_cny"]) + cny
                        d_entry["total_cny"] = float(d_entry["total_cny"]) + cny
                        d_entry["calls"] = int(d_entry["calls"]) + 1
                        d_entry["total_tokens"] = int(d_entry["total_tokens"]) + t_tok
                        d_entry["cache_hit_tokens"] = int(d_entry["cache_hit_tokens"]) + h_tok
                        if p_entry["category"] == "前台聊天与表达":
                            d_entry["interactive_cny"] = float(d_entry["interactive_cny"]) + cny
                        else:
                            d_entry["background_cny"] = float(d_entry["background_cny"]) + cny

                    if len(raw_recent) < 40:
                        raw_recent.append(
                            (
                                ts,
                                {
                                    "time": clock_str,
                                    "purpose": purpose_key,
                                    "purpose_label": p_entry["label"],
                                    "category": p_entry["category"],
                                    "model": model_key,
                                    "billing_mode": "api",
                                    "total_tokens": t_tok,
                                    "cache_hit_tokens": h_tok,
                                    "cost_cny": round(cny, 4),
                                    "latency_ms": int(latency_ms or 0),
                                    "status": str(status or "succeeded"),
                                },
                            )
                        )

            if _table_exists(conn, "usage_events"):
                for created_at, kind, estimated_cny, note in conn.execute(
                    "SELECT created_at, kind, estimated_cny, note FROM usage_events ORDER BY id DESC"
                ):
                    day_str, clock_str, ts = _to_shanghai_date_and_clock(created_at)
                    cny = float(estimated_cny or 0.0)
                    kind_key = str(kind or "image_generation")
                    p_entry = ensure_purpose(kind_key)
                    model_label = (
                        str(note or "gpt-image").split(":", 2)[1]
                        if ":" in str(note or "")
                        else str(note or "gpt-image")
                    )
                    m_entry = ensure_model(model_label, "media", "api")

                    all_time_api_cny += cny
                    all_time_media_cny += cny
                    p_entry["all_time_calls"] = int(p_entry["all_time_calls"]) + 1
                    p_entry["all_time_cny"] = float(p_entry["all_time_cny"]) + cny
                    m_entry["all_time_calls"] = int(m_entry["all_time_calls"]) + 1
                    m_entry["all_time_cny"] = float(m_entry["all_time_cny"]) + cny

                    if day_str >= day_7_cutoff:
                        days_7_api_cny += cny
                        days_7_media_cny += cny
                        p_entry["days_7_calls"] = int(p_entry["days_7_calls"]) + 1
                        p_entry["days_7_cny"] = float(p_entry["days_7_cny"]) + cny
                        m_entry["days_7_cny"] = float(m_entry["days_7_cny"]) + cny

                    if day_str == yesterday_str:
                        yesterday_total_cny += cny
                        p_entry["yesterday_calls"] = int(p_entry["yesterday_calls"]) + 1
                        p_entry["yesterday_cny"] = float(p_entry["yesterday_cny"]) + cny
                        m_entry["yesterday_cny"] = float(m_entry["yesterday_cny"]) + cny

                    if day_str == today_str:
                        today_api_cny += cny
                        today_media_cny += cny
                        p_entry["today_calls"] = int(p_entry["today_calls"]) + 1
                        p_entry["today_cny"] = float(p_entry["today_cny"]) + cny
                        m_entry["today_calls"] = int(m_entry["today_calls"]) + 1
                        m_entry["today_cny"] = float(m_entry["today_cny"]) + cny

                    if day_str in daily_map:
                        d_entry = daily_map[day_str]
                        d_entry["api_cny"] = float(d_entry["api_cny"]) + cny
                        d_entry["media_cny"] = float(d_entry["media_cny"]) + cny
                        d_entry["total_cny"] = float(d_entry["total_cny"]) + cny
                        d_entry["background_cny"] = float(d_entry["background_cny"]) + cny

                    if len(raw_recent) < 60:
                        raw_recent.append(
                            (
                                ts,
                                {
                                    "time": clock_str,
                                    "purpose": kind_key,
                                    "purpose_label": p_entry["label"],
                                    "category": p_entry["category"],
                                    "model": model_label,
                                    "billing_mode": "api",
                                    "total_tokens": 0,
                                    "cache_hit_tokens": 0,
                                    "cost_cny": round(cny, 4),
                                    "latency_ms": 0,
                                    "status": "succeeded",
                                },
                            )
                        )

            if _table_exists(conn, "world_recall_embedding_usage_daily"):
                for usage_day, consumed_tokens, estimated_cost_cny, request_count in conn.execute(
                    "SELECT usage_day, consumed_tokens, estimated_cost_cny, request_count "
                    "FROM world_recall_embedding_usage_daily"
                ):
                    day_str = str(usage_day or "")
                    tok = int(consumed_tokens or 0)
                    cny = float(estimated_cost_cny or 0.0)
                    reqs = int(request_count or 0)
                    p_entry = ensure_purpose("recall_embedding")

                    all_time_api_cny += cny
                    all_time_embedding_cny += cny
                    p_entry["all_time_calls"] = int(p_entry["all_time_calls"]) + reqs
                    p_entry["all_time_tokens"] = int(p_entry["all_time_tokens"]) + tok
                    p_entry["all_time_cny"] = float(p_entry["all_time_cny"]) + cny

                    if day_str >= day_7_cutoff:
                        days_7_api_cny += cny
                        days_7_embedding_cny += cny
                        p_entry["days_7_calls"] = int(p_entry["days_7_calls"]) + reqs
                        p_entry["days_7_tokens"] = int(p_entry["days_7_tokens"]) + tok
                        p_entry["days_7_cny"] = float(p_entry["days_7_cny"]) + cny

                    if day_str == yesterday_str:
                        yesterday_total_cny += cny
                        p_entry["yesterday_calls"] = int(p_entry["yesterday_calls"]) + reqs
                        p_entry["yesterday_tokens"] = int(p_entry["yesterday_tokens"]) + tok
                        p_entry["yesterday_cny"] = float(p_entry["yesterday_cny"]) + cny

                    if day_str == today_str:
                        today_api_cny += cny
                        today_embedding_cny += cny
                        p_entry["today_calls"] = int(p_entry["today_calls"]) + reqs
                        p_entry["today_tokens"] = int(p_entry["today_tokens"]) + tok
                        p_entry["today_cny"] = float(p_entry["today_cny"]) + cny

                    if day_str in daily_map:
                        d_entry = daily_map[day_str]
                        d_entry["api_cny"] = float(d_entry["api_cny"]) + cny
                        d_entry["embedding_cny"] = float(d_entry["embedding_cny"]) + cny
                        d_entry["total_cny"] = float(d_entry["total_cny"]) + cny
                        d_entry["background_cny"] = float(d_entry["background_cny"]) + cny
        finally:
            conn.close()

    for sub_path in _subscription_db_candidates(db_path):
        if not sub_path.exists():
            continue
        sub_conn = sqlite3.connect(f"file:{sub_path}?mode=ro", uri=True, timeout=5.0)
        try:
            if _table_exists(sub_conn, "subscription_requests"):
                sub_recent_collected = 0
                for (
                    created_at,
                    purpose,
                    model_name,
                    reserved_tokens,
                    reported_tokens,
                    status,
                    latency_ms,
                ) in sub_conn.execute(
                    "SELECT created_at, purpose, model, reserved_tokens, reported_tokens, status, latency_ms "
                    "FROM subscription_requests ORDER BY rowid DESC"
                ):
                    day_str, clock_str, ts = _to_shanghai_date_and_clock(created_at)
                    t_tok = int(
                        reported_tokens
                        if reported_tokens is not None and int(reported_tokens) > 0
                        else (reserved_tokens or 0)
                    )
                    cny = t_tok * _SUBSCRIPTION_EQUIV_CNY_PER_MILLION_TOKENS / 1_000_000.0
                    purpose_key = str(purpose or "unknown")
                    model_key = str(model_name or "GLM-5.3-Flash")
                    p_entry = ensure_purpose(purpose_key)
                    m_entry = ensure_model(model_key, "subscription", "subscription")

                    all_time_sub_equiv_cny += cny
                    all_time_model_cny += cny
                    all_time_calls += 1
                    all_time_total_tokens += t_tok

                    p_entry["all_time_calls"] = int(p_entry["all_time_calls"]) + 1
                    p_entry["all_time_tokens"] = int(p_entry["all_time_tokens"]) + t_tok
                    p_entry["all_time_cny"] = float(p_entry["all_time_cny"]) + cny

                    m_entry["all_time_calls"] = int(m_entry["all_time_calls"]) + 1
                    m_entry["all_time_tokens"] = int(m_entry["all_time_tokens"]) + t_tok
                    m_entry["all_time_cny"] = float(m_entry["all_time_cny"]) + cny
                    if latency_ms is not None and float(latency_ms) > 0:
                        m_entry["latency_sum_ms"] = float(m_entry["latency_sum_ms"]) + float(latency_ms)
                        m_entry["latency_count"] = int(m_entry["latency_count"]) + 1

                    if day_str >= day_7_cutoff:
                        days_7_sub_equiv_cny += cny
                        days_7_model_cny += cny
                        days_7_calls += 1
                        days_7_total_tokens += t_tok
                        p_entry["days_7_calls"] = int(p_entry["days_7_calls"]) + 1
                        p_entry["days_7_tokens"] = int(p_entry["days_7_tokens"]) + t_tok
                        p_entry["days_7_cny"] = float(p_entry["days_7_cny"]) + cny
                        m_entry["days_7_cny"] = float(m_entry["days_7_cny"]) + cny

                    if day_str == yesterday_str:
                        yesterday_total_cny += cny
                        yesterday_calls += 1
                        yesterday_total_tokens += t_tok
                        p_entry["yesterday_calls"] = int(p_entry["yesterday_calls"]) + 1
                        p_entry["yesterday_tokens"] = int(p_entry["yesterday_tokens"]) + t_tok
                        p_entry["yesterday_cny"] = float(p_entry["yesterday_cny"]) + cny
                        m_entry["yesterday_cny"] = float(m_entry["yesterday_cny"]) + cny

                    if day_str == today_str:
                        today_sub_equiv_cny += cny
                        today_model_cny += cny
                        today_calls += 1
                        today_total_tokens += t_tok
                        p_entry["today_calls"] = int(p_entry["today_calls"]) + 1
                        p_entry["today_tokens"] = int(p_entry["today_tokens"]) + t_tok
                        p_entry["today_cny"] = float(p_entry["today_cny"]) + cny
                        m_entry["today_calls"] = int(m_entry["today_calls"]) + 1
                        m_entry["today_tokens"] = int(m_entry["today_tokens"]) + t_tok
                        m_entry["today_cny"] = float(m_entry["today_cny"]) + cny

                    if day_str in daily_map:
                        d_entry = daily_map[day_str]
                        d_entry["sub_equiv_cny"] = float(d_entry["sub_equiv_cny"]) + cny
                        d_entry["model_cny"] = float(d_entry["model_cny"]) + cny
                        d_entry["total_cny"] = float(d_entry["total_cny"]) + cny
                        d_entry["calls"] = int(d_entry["calls"]) + 1
                        d_entry["total_tokens"] = int(d_entry["total_tokens"]) + t_tok
                        if p_entry["category"] == "前台聊天与表达":
                            d_entry["interactive_cny"] = float(d_entry["interactive_cny"]) + cny
                        else:
                            d_entry["background_cny"] = float(d_entry["background_cny"]) + cny

                    if sub_recent_collected < 24:
                        sub_recent_collected += 1
                        raw_recent.append(
                            (
                                ts,
                                {
                                    "time": clock_str,
                                    "purpose": purpose_key,
                                    "purpose_label": p_entry["label"],
                                    "category": p_entry["category"],
                                    "model": model_key,
                                    "billing_mode": "subscription",
                                    "total_tokens": t_tok,
                                    "cache_hit_tokens": 0,
                                    "cost_cny": round(cny, 4),
                                    "latency_ms": int(latency_ms or 0),
                                    "status": str(status or "succeeded"),
                                },
                            )
                        )
                break
        finally:
            sub_conn.close()

    raw_recent.sort(key=lambda item: item[0], reverse=True)
    recent_records = [item[1] for item in raw_recent[:24]]

    purpose_rows = []
    for item in purpose_map.values():
        all_calls = int(item["all_time_calls"])
        all_tokens = int(item["all_time_tokens"])
        all_prompt = int(item["all_time_prompt_tokens"])
        all_hit = int(item["all_time_cache_hit_tokens"])
        purpose_rows.append(
            {
                "purpose": item["purpose"],
                "label": item["label"],
                "category": item["category"],
                "today_calls": int(item["today_calls"]),
                "today_tokens": int(item["today_tokens"]),
                "today_cny": round(float(item["today_cny"]), 4),
                "yesterday_calls": int(item["yesterday_calls"]),
                "yesterday_tokens": int(item["yesterday_tokens"]),
                "yesterday_cny": round(float(item["yesterday_cny"]), 4),
                "days_7_calls": int(item["days_7_calls"]),
                "days_7_tokens": int(item["days_7_tokens"]),
                "days_7_cny": round(float(item["days_7_cny"]), 4),
                "all_time_calls": all_calls,
                "all_time_tokens": all_tokens,
                "all_time_cny": round(float(item["all_time_cny"]), 4),
                "avg_tokens_per_call": round(all_tokens / all_calls) if all_calls > 0 else 0,
                "cache_hit_rate": round(all_hit / all_prompt, 4) if all_prompt > 0 else 0.0,
            }
        )
    purpose_rows.sort(
        key=lambda r: (r["today_cny"], r["days_7_cny"], r["all_time_cny"]),
        reverse=True,
    )

    model_rows = []
    for item in model_map.values():
        all_prompt = int(item["all_time_prompt_tokens"])
        all_hit = int(item["all_time_cache_hit_tokens"])
        lat_count = int(item["latency_count"])
        model_rows.append(
            {
                "model": item["model"],
                "provider": item["provider"],
                "billing_mode": item["billing_mode"],
                "today_calls": int(item["today_calls"]),
                "today_tokens": int(item["today_tokens"]),
                "today_cny": round(float(item["today_cny"]), 4),
                "yesterday_cny": round(float(item["yesterday_cny"]), 4),
                "days_7_cny": round(float(item["days_7_cny"]), 4),
                "all_time_calls": int(item["all_time_calls"]),
                "all_time_tokens": int(item["all_time_tokens"]),
                "all_time_cny": round(float(item["all_time_cny"]), 4),
                "cache_hit_rate": round(all_hit / all_prompt, 4) if all_prompt > 0 else 0.0,
                "avg_latency_ms": round(float(item["latency_sum_ms"]) / lat_count) if lat_count > 0 else 0,
            }
        )
    model_rows.sort(
        key=lambda r: (r["today_cny"], r["days_7_cny"], r["all_time_cny"]),
        reverse=True,
    )

    daily_series = [
        {
            "date": d["date"],
            "label": d["label"],
            "total_cny": round(float(d["total_cny"]), 4),
            "api_cny": round(float(d["api_cny"]), 4),
            "sub_equiv_cny": round(float(d["sub_equiv_cny"]), 4),
            "model_cny": round(float(d["model_cny"]), 4),
            "media_cny": round(float(d["media_cny"]), 4),
            "embedding_cny": round(float(d["embedding_cny"]), 4),
            "background_cny": round(float(d["background_cny"]), 4),
            "interactive_cny": round(float(d["interactive_cny"]), 4),
            "calls": int(d["calls"]),
            "total_tokens": int(d["total_tokens"]),
            "cache_hit_tokens": int(d["cache_hit_tokens"]),
        }
        for d in daily_map.values()
    ]

    today_total_cny = today_api_cny + today_sub_equiv_cny
    days_7_total_cny = days_7_api_cny + days_7_sub_equiv_cny
    all_time_total_cny = all_time_api_cny + all_time_sub_equiv_cny

    return {
        "contract": "world-v2-dashboard-cost-meter.1",
        "generated_at": current.isoformat(),
        "timezone": "Asia/Shanghai",
        "summary": {
            "today_date": today_str,
            "today_total_cny": round(today_total_cny, 4),
            "today_api_cny": round(today_api_cny, 4),
            "today_sub_equiv_cny": round(today_sub_equiv_cny, 4),
            "today_model_cny": round(today_model_cny, 4),
            "today_media_cny": round(today_media_cny, 4),
            "today_embedding_cny": round(today_embedding_cny, 4),
            "today_calls": today_calls,
            "today_total_tokens": today_total_tokens,
            "today_prompt_tokens": today_prompt_tokens,
            "today_completion_tokens": today_completion_tokens,
            "today_cache_hit_tokens": today_cache_hit_tokens,
            "today_cache_hit_rate": (
                round(today_cache_hit_tokens / today_prompt_tokens, 4)
                if today_prompt_tokens > 0
                else 0.0
            ),
            "yesterday_date": yesterday_str,
            "yesterday_total_cny": round(yesterday_total_cny, 4),
            "yesterday_calls": yesterday_calls,
            "yesterday_total_tokens": yesterday_total_tokens,
            "days_7_total_cny": round(days_7_total_cny, 4),
            "days_7_api_cny": round(days_7_api_cny, 4),
            "days_7_sub_equiv_cny": round(days_7_sub_equiv_cny, 4),
            "days_7_avg_cny": round(days_7_total_cny / 7.0, 4),
            "days_7_calls": days_7_calls,
            "days_7_total_tokens": days_7_total_tokens,
            "all_time_total_cny": round(all_time_total_cny, 4),
            "all_time_api_cny": round(all_time_api_cny, 4),
            "all_time_sub_equiv_cny": round(all_time_sub_equiv_cny, 4),
            "all_time_model_cny": round(all_time_model_cny, 4),
            "all_time_media_cny": round(all_time_media_cny, 4),
            "all_time_embedding_cny": round(all_time_embedding_cny, 4),
            "all_time_calls": all_time_calls,
            "all_time_total_tokens": all_time_total_tokens,
        },
        "daily_series": daily_series,
        "by_purpose": purpose_rows,
        "by_model": model_rows,
        "recent_records": recent_records,
    }
