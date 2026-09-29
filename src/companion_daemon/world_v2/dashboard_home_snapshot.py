"""Typed, read-only owner Dashboard snapshot compiled from one pinned World view.

The module is deliberately a compiler, not another state owner.  Every ledger
section is derived from one :class:`LedgerProjection`; runtime-only health can
only enter through the small typed observation contract below.  Raw proposal
or audit JSON, provider payloads, prompts, secrets, evidence hashes, and the
ledger semantic hash never cross this boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
from types import MappingProxyType
from typing import Literal, TypeAlias
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .audited_change_terminal import (
    audited_change_terminal_event_id,
    terminal_relationship_commitment_payload,
)
from .audited_proposal_settlement import (
    AuditedChangeTerminalSettlement,
    find_terminal_audited_change,
)
from .dashboard_mechanism_activity import DashboardMechanismActivity, mechanism_activity, ZERO_NOTES
from .dashboard_projection_adapter import DashboardRoomRouteCatalog, DashboardSceneRoute
from .dashboard_life_intention import DashboardLifeIntention, read_dashboard_life_intention
from .dashboard_world_occurrence import DashboardWorldOccurrenceReading, read_dashboard_world_occurrence, experience_world_occurrence
from .ledger import LedgerPort
from .life_content_store import ImmutableLifeContentStore
from .proposal_audit_schemas import ProposalAuditProjection
from .proposal_envelope import DecisionProposal, TypedChange, validate_proposal_envelope
from .room_projection import RoomProjectionMaterializer
from .schema_core import FrozenModel
from .schemas import ExperienceWorldLifeResponseBinding, LedgerProjection, ProjectionCursor


_DETAIL_LIMIT = 48
_KIND_KEEP_DEFAULT = 2
_KIND_KEEP: Mapping[str, int] = MappingProxyType(
    {
        "plan": 3,
        "location": 2,
        "resource": 6,
        "attention": 2,
        "affect_episode": 3,
        "appraisal": 2,
        "relationship_state": 2,
        "response_expectation": 2,
        "revisit_intention": 2,
        "expectation_assessment": 2,
        "interaction_bid": 2,
        "action": 5,
        "expression_plan": 3,
        "npc": 8,
        "thread": 2,
        "life_ecology_schedule": 1,
        "life_retry": 3,
        "execution_receipt": 3,
        "trigger_process": 0,
        "expression_beat": 0,
        "affect_baseline": 0,
        "fact": 4,
        "memory_candidate": 4,
        "character_core": 1,
        "budget_account": 3,
        "budget_reservation": 2,
        "budget_settlement": 2,
        "action_reconciliation": 2,
    }
)
_NOW_KINDS: tuple[str, ...] = (
    "plan",
    "location",
    "resource",
    "attention",
    "affect_episode",
    "relationship_state",
    "response_expectation",
    "revisit_intention",
    "interaction_bid",
    "appraisal",
    "action",
    "expression_plan",
    "npc",
    "thread",
    "life_ecology_schedule",
)
_TERMINAL_RETURN_LIMIT = 64
_ROOM_VISIBLE_PRIVACY = frozenset({"public", "shareable"})
_HOME_LOCATION_REF = "location:ecnu-dorm-room"


DashboardSectionState: TypeAlias = Literal["ready", "empty", "unavailable"]
DashboardRuntimeState: TypeAlias = Literal[
    "ready", "degraded", "warming", "busy", "unavailable", "disabled"
]
DashboardRuntimeSignalKey: TypeAlias = Literal[
    "scheduler",
    "character_interior",
    "local_provider_capacity",
    "text_endpoint",
    "proactive_source_authority",
    "life_source_authority",
    "external_perception_upstream",
    "model_usage_budget",
    "process_latency",
    "storage",
    "expression_episode",
    "semantic_recall",
]
DashboardRuntimeReasonCode: TypeAlias = Literal[
    "primary_timeout",
    "not_configured",
    "capacity_exhausted",
    "source_unavailable",
    "budget_exhausted",
    "latency_gate_exceeded",
    "storage_unavailable",
    "composition_unavailable",
    "runtime_probe_failed",
]


_ACTIVITY_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "routine.morning_settle": "早上收拾洗漱",
        "sleep.prepare_for_bed": "睡前收拾，准备休息",
        "sleep.late_wind_down": "深夜收心，准备睡了",
        "sleep.early_morning_wake": "清晨早醒，还没起",
        "study.focused_reading": "专注读书",
        "meal.make_drink": "弄点吃的喝的",
        "creative.edit_photo_notes": "整理照片和随手笔记",
        "commute.short_walk": "出门走一小段",
        "household.tidy_small_things": "收拾屋里的小东西",
        "recovery.quiet_rest": "安静歇一会儿",
        "leisure.digital_browse": "窝着刷手机",
        "social.literature_reading_list": "忙文学社书单的事",
        "social.literature_club_meetup": "和范予安约了文学社碰头",
        "commute.lakeside_walk": "去丽娃河边走一段",
        "creative.photo_batch_organize": "集中整理一批照片",
        "study.reading_notes": "写读书笔记",
        "study.attend_class": "去教学楼上课",
        "study.essay_writing": "赶论文",
        "study.evening_self_study": "晚上在图书馆自习",
        "study.seminar_room_session": "预约了研讨间整理思路",
        "creative.write_essay": "写随笔",
        "creative.film_scan_sort": "翻扫整理胶片",
        "creative.write_diary": "写日记",
        "creative.bund_night_shoot": "去外滩拍夜景",
        "household.do_laundry": "洗衣服",
        "errand.pick_up_parcel": "去驿站取快递",
        "errand.buy_fruit": "买水果和零嘴",
        "errand.print_shop": "去打印店",
        "meal.canteen_meal": "去食堂吃饭",
        "meal.dorm_cooking": "在宿舍煮饭试新菜",
        "recovery.evening_stretch": "睡前拉伸",
        "recovery.window_daydream": "靠窗发呆",
        "sleep.afternoon_nap": "午睡",
        "leisure.podcast_listen": "听播客",
        "leisure.browse_book_stall": "逛旧书摊",
        "leisure.book_market_hunt": "去二手书市淘书",
        "social.family_call": "给家里打电话",
        "social.roommate_chat": "和林晚闲聊",
        "social.literature_club_admin": "处理文学社事务",
        "social.exhibition_outing": "和范予安去看展",
        "family.bookstore_help": "回嘉兴帮家里看店",
        "shared.movie_call": "和你连麦一起看电影",
        "focused_work": "在看资料",
        "relax": "放松一下",
    }
)

# This catalog is renderer composition, not character policy.  Every mapping
# is exact: no unknown activity is guessed into a local animation.
_ACTIVITY_ROUTES: Mapping[str, str] = MappingProxyType(
    {
        "routine.morning_settle": "tidy",
        "sleep.prepare_for_bed": "sleep",
        "sleep.late_wind_down": "sleep",
        "sleep.early_morning_wake": "sleep",
        "study.focused_reading": "study",
        "meal.make_drink": "cook",
        "creative.edit_photo_notes": "study",
        "household.tidy_small_things": "tidy",
        "recovery.quiet_rest": "relax",
        "leisure.digital_browse": "phone",
        "social.literature_reading_list": "study",
        "creative.photo_batch_organize": "study",
        "study.reading_notes": "study",
        "study.essay_writing": "study",
        "creative.write_essay": "study",
        "creative.film_scan_sort": "study",
        "creative.write_diary": "study",
        "household.do_laundry": "tidy",
        "meal.dorm_cooking": "cook",
        "recovery.evening_stretch": "relax",
        "recovery.window_daydream": "relax",
        "sleep.afternoon_nap": "sleep",
        "leisure.podcast_listen": "relax",
        "social.family_call": "phone",
        "social.roommate_chat": "relax",
        "social.literature_club_admin": "study",
        "shared.movie_call": "phone",
        "focused_work": "study",
        "relax": "relax",
    }
)
_ROOM_ROUTES = DashboardRoomRouteCatalog(
    location_routes={_HOME_LOCATION_REF: "zhizhi-home"},
    activity_routes=_ACTIVITY_ROUTES,
)


_STATUS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "planned": "已计划",
        "window_missed": "窗口过了还没开始",
        "committed": "已记下",
        "active": "进行中",
        "paused": "暂停",
        "completed": "已完成",
        "abandoned": "已放弃",
        "available": "可用",
        "occupied": "忙碌",
        "deep_focus": "专注中",
        "do_not_disturb": "请勿打扰",
        "recovering_attention": "恢复注意力",
        "open": "进行中",
        "resolved": "已解决",
        "superseded": "已替代",
        "released": "已搁下",
        "accepted": "已接受",
        "rejected": "已拒绝",
        "stale": "已过期",
        "authorized": "已授权",
        "scheduled": "已排程",
        "claimed": "处理中",
        "dispatch_started": "发送中",
        "provider_accepted": "外部已接收",
        "delivered": "已送达",
        "failed": "失败",
        "unknown": "状态未知",
        "cancelled": "已取消",
        "expired": "已失效",
        "pending": "待处理",
        "settled": "已结算",
        "active_reply": "回复处理中",
        "committed_world_event": "已经落进世界的事",
        "terminal": "已结束",
        "depleted": "快没了",
        "low": "偏低",
        "moderate": "还行",
        "high": "挺充足",
        "full": "满的",
        "private": "自己待着",
        "shareable": "可以给人看",
        "public": "在外面",
        "dormant": "暂时不在",
        "departed": "离开了",
        "retired": "已经退场",
        "contradicted": "已经对不上",
        "fulfilled": "她觉得你应了",
        "still_pending": "她觉得还没等到",
        "uncertain": "她还拿不准",
        "topic_open": "还没说完",
        "question_pending": "还等一个回答",
        "repair_open": "还想把话说圆",
        "external_result_pending": "还等外面的结果",
        "coordination_pending": "还在对一下",
        "reply_reconsideration": "还在想要不要再回",
        "reply": "回你的消息",
        "proactive_message": "主动找你",
        "followup": "又补了一句",
        "ack": "回执",
        "provider_result": "外部结果",
        "physical_energy": "体力",
        "cognitive_capacity": "脑子清不清",
        "social_capacity": "社交力气",
        "warmth": "暖",
        "joy": "开心",
        "sadness": "难过",
        "hurt": "委屈",
        "anger": "生气",
        "loneliness": "孤单",
        "anxiety": "担心",
        "resentment": "怨气",
    }
)
_STAGE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "stranger": "陌生",
        "acquaintance": "认识了",
        "friend": "朋友",
        "close_friend": "很熟的朋友",
        "ambiguous": "有点暧昧",
        "lover": "恋人",
    }
)
_CUE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "identity": "身份",
        "relationship": "关系",
        "boundary": "边界",
        "unfinished_business": "未完成的事",
        "repeated_pattern": "重复的模式",
        "future_utility": "以后有用",
        "emotional_residue": "情绪残留",
        "world_continuity": "生活连续性",
    }
)
_MEMORY_STATUS_LABELS = MappingProxyType({
    "pending": "待复核", "active": "已保留", "rejected": "未保留", "forgotten": "已遗忘",
})
_FACT_PREDICATE_LABELS = MappingProxyType({
    "location.current": "当前位置", "profile.display_name": "姓名与称呼",
    "profile.timezone": "时区", "preference.likes": "喜欢的事物",
    "preference.dislikes": "不喜欢的事物", "relationship.affiliation": "所属组织",
    "profile.occupation": "工作与职业", "profile.education": "学业情况",
    "location.home": "居住地", "location.hometown": "家乡",
    "schedule.commitment": "计划与约定", "situation.recent": "近期处境",
    "activity.current": "正在做的事", "relationship.person": "身边的人",
    "health.condition": "身体情况", "routine.habit": "习惯与作息",
    "interest.activity": "兴趣活动", "possession.item": "拥有的物品或宠物",
})
_FACT_STATUS_LABELS = MappingProxyType({"active": "有效记录", "withdrawn": "已撤回"})
_APPRAISAL_STATUS_LABELS = MappingProxyType({
    "active": "当前理解", "contradicted": "已有矛盾证据",
    "expired": "已失效", "superseded": "已有新的理解",
})
_NPC_STATUS_LABELS = MappingProxyType({
    "active": "现有人物", "dormant": "暂时不活跃",
    "departed": "已离开", "retired": "已退场",
})
_RETENTION_LABELS = MappingProxyType({
    "identity_relevance": "与身份有关", "relationship_continuity": "关系连续性",
    "boundary_relevance": "与边界有关", "unfinished_business": "未完成的事",
    "repeated_pattern": "重复的模式", "future_utility": "以后有用",
    "emotional_salience": "情绪意义", "world_continuity": "生活连续性",
})
_MEMORY_SOURCE_LABELS = MappingProxyType({
    "fact": "已接受事实", "experience": "已提交经历", "terminal_thread": "已结束事项",
})
_MEANING_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "ordinary": "普通往来",
        "care": "被关心",
        "support": "被支持",
        "shared_joy": "共同的开心",
        "goal_progress": "事情有进展",
        "uncertainty": "不确定",
        "misunderstanding": "误会",
        "disappointment": "失望",
        "dismissal": "被敷衍",
        "boundary_violation": "越界",
        "dehumanization": "不被当人",
        "coercion": "被强迫",
        "control_pressure": "被控制",
        "betrayal": "被辜负",
        "loss": "失去",
        "user_withdrawing": "对方在退开",
        "user_confused": "对方困惑",
        "repair_attempt": "想修复",
        "npc_conflict": "与人摩擦",
    }
)
_NPC_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "literature-fan": "范予安",
        "roommate-lin": "林晚",
        "roommate-qiao": "乔宁",
        "mother-shen": "沈岚",
        "father-shen": "陈远",
        "photography-zhou": "周栩",
        "hometown-xu": "徐青禾",
    }
)
_LOCATION_LABELS: Mapping[str, str] = MappingProxyType(
    {
        _HOME_LOCATION_REF: "华东师大宿舍",
    }
)
_RUNTIME_SIGNAL_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "scheduler": "时钟还在走",
        "character_interior": "她自己的判断",
        "local_provider_capacity": "本地小模型",
        "text_endpoint": "对话入口",
        "proactive_source_authority": "主动找你这条路",
        "life_source_authority": "生活事件这条路",
        "external_perception_upstream": "外面的新闻天气",
        "model_usage_budget": "模型预算",
        "process_latency": "这一下快不快",
        "storage": "账本存储",
        "expression_episode": "说话节奏",
        "semantic_recall": "记得的事",
    }
)
_RUNTIME_STATE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "ready": "正常",
        "degraded": "降级",
        "warming": "预热中",
        "busy": "忙碌",
        "unavailable": "不可用",
        "disabled": "未启用",
    }
)
_RUNTIME_REASON_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "primary_timeout": "主文本请求超时",
        "not_configured": "未配置",
        "capacity_exhausted": "容量已用尽",
        "source_unavailable": "来源不可用",
        "budget_exhausted": "预算已用尽",
        "latency_gate_exceeded": "延迟超过门槛",
        "storage_unavailable": "存储不可用",
        "composition_unavailable": "组合尚不可用",
        "runtime_probe_failed": "运行态探针不可用",
    }
)

_FAILURE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "provider_pending_deadline_elapsed": "外部接收后超过等待期限",
        "provider_accepted_without_terminal_receipt": "外部已接收但缺少终态回执",
        "action_deadline_elapsed_before_dispatch": "行动在发送前超过期限",
        "local_preflight_authorization_rejected": "本地授权预检拒绝",
        "local_preflight_payload_unavailable": "本地预检找不到载荷",
        "local_preflight_action_unsupported": "本地预检不支持该行动",
        "local_preflight_payload_binding_mismatch": "本地预检载荷绑定不匹配",
        "local_preflight_payload_content_type_rejected": "本地预检拒绝载荷类型",
        "local_preflight_payload_hash_mismatch": "本地预检载荷校验失败",
        "local_preflight_payload_semantics_rejected": "本地预检拒绝载荷语义",
        "provider_result_unavailable": "外部结果不可用",
        "provider_rejected": "外部服务拒绝",
        "repair_route_not_composed": "媒体修复通道未组合",
        "unexpected_render_payload": "渲染结果结构不合法",
        "invalid_frozen_plan": "冻结的媒体计划无效",
        "render_failed": "媒体渲染失败",
        "unexpected_inspection_payload": "媒体检查结果结构不合法",
        "inspection_record_unavailable": "媒体检查记录不可用",
        "http_capture_capability_unavailable": "HTTP capture 能力不可用",
        "provider_timeout": "模型或外部服务超时",
        "provider_failure": "模型或外部服务失败",
        "media_render_failure": "媒体渲染失败",
        "media_inspection_failure": "媒体检查失败",
        "activity_lifecycle_failure": "活动生命周期处理失败",
        "memory_failure": "记忆后处理失败",
        "aftermath_failure": "生活事件后续处理失败",
        "life_development_failure": "生活发展处理失败",
        "npc_ecology_failure": "人物生态处理失败",
        "life_media_failure": "生活媒体处理失败",
        "unclassified_receipt_failure": "未分类的执行回执失败",
        "unclassified_life_failure": "未分类的生活处理失败",
    }
)


class DashboardLedgerFieldPolicy(FrozenModel):
    section: Literal[
        "metadata",
        "overview_life",
        "facts_memory_inner",
        "relationship_lifecycle",
        "operations",
        "perception_media",
        "authority_privacy",
        "ledger_qualification",
        "withheld",
    ]
    exposure: Literal[
        "metadata", "typed_summary", "count_only", "intentionally_withheld"
    ]
    reason: str


_LEDGER_FIELDS_BY_SECTION: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "metadata": (
            "schema_version",
            "reducer_bundle_version",
            "world_id",
            "world_revision",
            "deliberation_revision",
            "ledger_sequence",
            "logical_time",
        ),
        "authority_privacy": (
            "actor_authorities",
            "actor_authority_transitions",
            "consumed_actor_root_nonces",
            "capability_grants",
            "capability_transitions",
            "consent_grants",
            "consent_transitions",
            "privacy_policies",
            "privacy_transitions",
            "provider_media_grants",
            "consumed_authorization_root_nonces",
            "consumed_authorization_challenge_ids",
            "consumed_authorization_source_ids",
        ),
        "ledger_qualification": (
            "observation_refs",
            "message_observations",
            "operator_observations",
            "committed_world_event_refs",
            "clock_transition_history",
            "proposal_ids",
            "proposal_revisions",
            "model_result_audits",
            "proposal_audits",
            "interaction_fact_decisions",
            "acceptance_manifests_v2",
            "fact_commit_proposal_audits_v2",
            "acceptance_manifests_v3",
            "expression_plan_manifests",
        ),
        "overview_life": (
            "goals",
            "goal_transitions",
            "goal_proposals",
            "goal_proposal_ids",
            "locations",
            "location_transitions",
            "location_proposals",
            "location_proposal_ids",
            "resources",
            "resource_transitions",
            "resource_proposals",
            "resource_proposal_ids",
            "attentions",
            "attention_transitions",
            "attention_proposals",
            "attention_proposal_ids",
            "world_places",
            "biographical_coordinates",
            "life_arcs",
            "aspirations",
            "plans",
            "world_occurrences",
            "outcome_observations",
            "experiences",
            "experience_transitions",
            "experience_proposals",
            "experience_proposal_ids",
        ),
        "operations": (
            "actions",
            "pending_actions",
            "read_only_tool_requests",
            "tool_results",
            "budget_accounts",
            "budget_reservations",
            "trigger_processes",
            "life_ecology_schedule",
            "pending_contextual_life_sources",
            "contextual_life_retries",
            "pending_biographical_settlements",
            "pending_external_observations",
            "execution_receipts",
            "budget_settlements",
            "reconciliations",
            "completed_trigger_ids",
            "minimal_reply_manifests",
            "response_expectation_assessments",
            "stored_message_payloads",
            "expression_payload_descriptors",
            "life_content_descriptors",
            "expression_plans",
            "expression_beats",
            "acceptance_decisions",
            "outcome_proposals",
        ),
        "perception_media": (
            "perception_requests",
            "perception_results",
            "external_signal_snapshots",
            "external_perceptions",
            "external_perception_acceptance_manifests",
            "appearance_states",
            "visible_physical_states",
            "photo_candidates",
            "media_declined_candidate_revisions",
            "media_opportunities",
            "media_plans",
            "media_unrenderable_opportunity_ids",
            "media_artifacts",
            "media_inspections",
            "media_previews",
            "media_failed_plan_ids",
            "media_delivery_approvals",
            "media_deliveries",
        ),
        "relationship_lifecycle": (
            "interaction_bids",
            "interaction_bid_proposals",
            "media_thread_proposals",
            "npcs",
            "relationship_signals",
            "relationship_commitments",
            "interaction_acts",
            "interaction_act_transitions",
            "interaction_act_proposals",
            "relationship_adjustments",
            "relationship_states",
            "boundaries",
            "relationship_proposals",
            "relationship_proposal_ids",
            "private_impressions",
            "private_impression_proposals",
            "private_impression_proposal_ids",
            "threads",
            "thread_transitions",
            "thread_proposals",
            "thread_proposal_ids",
            "commitments",
            "commitment_transitions",
            "commitment_proposals",
            "commitment_proposal_ids",
        ),
        "facts_memory_inner": (
            "prehistory_archives",
            "prehistory_records",
            "memory_candidates",
            "memory_candidate_transitions",
            "memory_candidate_proposals",
            "memory_candidate_proposal_ids",
            "character_core",
            "character_core_transitions",
            "character_core_proposals",
            "character_core_proposal_ids",
            "appraisals",
            "affect_baselines",
            "affect_episodes",
            "appraisal_proposals",
            "appraisal_proposal_ids",
            "affect_proposals",
            "affect_proposal_ids",
            "facts",
            "fact_transitions",
            "fact_proposals",
            "fact_proposal_ids",
        ),
        "withheld": ("semantic_hash", "chat_life_intent_failures", "chat_life_plan_considerations"),
    }
)
_TYPED_SUMMARY_FIELDS = frozenset(
    {
        "actor_authorities",
        "capability_grants",
        "consent_grants",
        "privacy_policies",
        "provider_media_grants",
        "goals",
        "locations",
        "resources",
        "attentions",
        "world_places",
        "biographical_coordinates",
        "life_arcs",
        "aspirations",
        "plans",
        "world_occurrences",
        "outcome_observations",
        "experiences",
        "actions",
        "read_only_tool_requests",
        "tool_results",
        "budget_accounts",
        "budget_reservations",
        "trigger_processes",
        "life_ecology_schedule",
        "contextual_life_retries",
        "execution_receipts",
        "budget_settlements",
        "reconciliations",
        "expression_plans",
        "expression_beats",
        "perception_requests",
        "perception_results",
        "external_signal_snapshots",
        "external_perceptions",
        "photo_candidates",
        "media_opportunities",
        "media_plans",
        "media_inspections",
        "media_previews",
        "media_deliveries",
        "interaction_bids",
        "npcs",
        "relationship_signals",
        "relationship_commitments",
        "interaction_acts",
        "relationship_adjustments",
        "relationship_states",
        "boundaries",
        "threads",
        "commitments",
        "memory_candidates",
        "character_core",
        "appraisals",
        "affect_baselines",
        "affect_episodes",
        "facts",
        "response_expectation_assessments",
    }
)


def _build_field_policy() -> Mapping[str, DashboardLedgerFieldPolicy]:
    configured = tuple(
        field_name
        for fields in _LEDGER_FIELDS_BY_SECTION.values()
        for field_name in fields
    )
    if len(configured) != len(set(configured)):
        raise RuntimeError("dashboard ledger field policy contains duplicate fields")
    expected = set(LedgerProjection.model_fields)
    if set(configured) != expected:
        missing = sorted(expected - set(configured))
        extra = sorted(set(configured) - expected)
        raise RuntimeError(
            f"dashboard ledger field policy drifted (missing={missing}, extra={extra})"
        )
    policy: dict[str, DashboardLedgerFieldPolicy] = {}
    for section, fields in _LEDGER_FIELDS_BY_SECTION.items():
        for field_name in fields:
            if section == "metadata":
                exposure = "metadata"
                reason = "stable snapshot metadata"
            elif section == "withheld":
                exposure = "intentionally_withheld"
                reason = "internal integrity value; full semantic hashes are never displayed"
            elif field_name in _TYPED_SUMMARY_FIELDS:
                exposure = "typed_summary"
                reason = "whitelisted typed summary plus exact aggregate count"
            else:
                exposure = "count_only"
                reason = "exact aggregate count; raw records can contain private or audit material"
            policy[field_name] = DashboardLedgerFieldPolicy(
                section=section,
                exposure=exposure,
                reason=reason,
            )
    return MappingProxyType(policy)


DASHBOARD_LEDGER_FIELD_POLICY = _build_field_policy()


class DashboardOwnerIdentity(FrozenModel):
    deployment_id: str
    boot_id: str


class DashboardCoverage(FrozenModel):
    known_count: int = Field(ge=0)
    included_count: int = Field(ge=0)
    truncated: bool

    @model_validator(mode="after")
    def included_does_not_exceed_known(self) -> DashboardCoverage:
        if self.included_count > self.known_count:
            raise ValueError("dashboard coverage included_count exceeds known_count")
        if self.truncated != (self.included_count < self.known_count):
            raise ValueError("dashboard coverage truncation flag is inconsistent")
        return self


class DashboardMetric(FrozenModel):
    key: str
    label: str
    count: int = Field(ge=0)
    count_note: str | None = Field(default=None, exclude_if=lambda value: value is None)


class DashboardLabeledValue(FrozenModel):
    key: str
    label: str
    value: str | int | bool | None
    value_label: str | None = None


class DashboardEntitySummary(FrozenModel):
    kind: str
    kind_label: str
    title: str
    status_code: str | None = None
    status_label: str | None = None
    detail: str | None = None
    privacy_class: str | None = None
    occurred_at: datetime | None = None
    values: tuple[DashboardLabeledValue, ...] = ()


class DashboardNotice(FrozenModel):
    kind: Literal["action_failure", "receipt_failure", "media_failure", "life_retry"]
    label: str
    severity: Literal["warning", "error"]
    reason_code: str
    reason_label: str
    occurred_at: datetime | None = None


class DashboardRoomRenderState(FrozenModel):
    protocol: Literal["pixel-home-state.2"] = "pixel-home-state.2"
    route: DashboardSceneRoute
    logical_time: datetime | None = None


class DashboardRoomSection(FrozenModel):
    label: Literal["房间"] = "房间"
    state: DashboardSectionState
    source: Literal["ledger"] = "ledger"
    observed_at: datetime | None = None
    cursor: ProjectionCursor
    coverage: DashboardCoverage
    reason_code: str | None = None
    render_state: DashboardRoomRenderState


class DashboardOverviewLifeData(FrozenModel):
    metrics: tuple[DashboardMetric, ...]
    highlights: tuple[DashboardEntitySummary, ...] = ()


class DashboardFactsMemoryInnerData(FrozenModel):
    metrics: tuple[DashboardMetric, ...]
    highlights: tuple[DashboardEntitySummary, ...] = ()


class DashboardTypedChangeTerminal(FrozenModel):
    status: Literal["rejected", "stale"]
    status_label: str
    reason_code: str
    effect_applied: Literal[False] = False
    target_stage: str
    target_stage_label: str
    commitment_code: str
    occurred_at: datetime
    cursor: ProjectionCursor


class DashboardRelationshipLifecycleData(FrozenModel):
    relationship_state_count: int = Field(ge=0)
    commitment_count: int = Field(ge=0)
    interaction_act_count: int = Field(ge=0)
    metrics: tuple[DashboardMetric, ...]
    highlights: tuple[DashboardEntitySummary, ...] = ()
    typed_change_candidate_count: int = Field(ge=0)
    typed_change_lookup_count: int = Field(ge=0)
    typed_change_terminal_count: int = Field(ge=0)
    typed_change_rejected_count: int = Field(ge=0)
    typed_change_stale_count: int = Field(ge=0)
    typed_change_unsettled_count: int = Field(ge=0)
    typed_change_terminals: tuple[DashboardTypedChangeTerminal, ...] = ()


class DashboardOperationsData(FrozenModel):
    metrics: tuple[DashboardMetric, ...]
    mechanisms: tuple[DashboardMechanismActivity, ...] = Field(default=(), exclude_if=lambda value: not value)
    highlights: tuple[DashboardEntitySummary, ...] = ()
    notices: tuple[DashboardNotice, ...] = ()


class DashboardPerceptionMediaData(FrozenModel):
    metrics: tuple[DashboardMetric, ...]
    highlights: tuple[DashboardEntitySummary, ...] = ()


class DashboardAuthorityPrivacyData(FrozenModel):
    metrics: tuple[DashboardMetric, ...]
    highlights: tuple[DashboardEntitySummary, ...] = ()


class DashboardLedgerQualificationData(FrozenModel):
    ledger_schema_version: str
    reducer_bundle_version: str
    qualification_status: Literal["not_asserted"] = "not_asserted"
    qualification_label: Literal[
        "仅展示账本证据，不代表生产资格通过"
    ] = "仅展示账本证据，不代表生产资格通过"
    field_policy_count: int = Field(ge=0)
    typed_summary_field_count: int = Field(ge=0)
    count_only_field_count: int = Field(ge=0)
    metadata_field_count: int = Field(ge=0)
    intentionally_withheld_field_count: int = Field(ge=0)
    metrics: tuple[DashboardMetric, ...]


class DashboardLedgerSectionBase(FrozenModel):
    state: DashboardSectionState
    source: Literal["ledger"] = "ledger"
    observed_at: datetime | None = None
    cursor: ProjectionCursor
    coverage: DashboardCoverage
    reason_code: str | None = None


class DashboardOverviewLifeSection(DashboardLedgerSectionBase):
    label: Literal["生活概览"] = "生活概览"
    data: DashboardOverviewLifeData


class DashboardFactsMemoryInnerSection(DashboardLedgerSectionBase):
    label: Literal["事实、记忆与内在"] = "事实、记忆与内在"
    data: DashboardFactsMemoryInnerData


class DashboardRelationshipLifecycleSection(DashboardLedgerSectionBase):
    label: Literal["关系与人物生命周期"] = "关系与人物生命周期"
    data: DashboardRelationshipLifecycleData


class DashboardOperationsSection(DashboardLedgerSectionBase):
    label: Literal["行动、表达与回执"] = "行动、表达与回执"
    data: DashboardOperationsData


class DashboardPerceptionMediaSection(DashboardLedgerSectionBase):
    label: Literal["外部感知与媒体"] = "外部感知与媒体"
    data: DashboardPerceptionMediaData


class DashboardAuthorityPrivacySection(DashboardLedgerSectionBase):
    label: Literal["能力、同意与隐私"] = "能力、同意与隐私"
    data: DashboardAuthorityPrivacyData


class DashboardLedgerQualificationSection(DashboardLedgerSectionBase):
    label: Literal["账本与资格证据"] = "账本与资格证据"
    data: DashboardLedgerQualificationData


class DashboardRuntimeReason(FrozenModel):
    signal: DashboardRuntimeSignalKey
    reason_code: DashboardRuntimeReasonCode


class DashboardRuntimeObservation(FrozenModel):
    observed_at: datetime | None = None
    scheduler_state: DashboardRuntimeState
    character_interior_state: DashboardRuntimeState
    local_provider_capacity_state: DashboardRuntimeState
    text_endpoint_state: DashboardRuntimeState
    proactive_source_authority_state: DashboardRuntimeState
    life_source_authority_state: DashboardRuntimeState
    external_perception_upstream_state: DashboardRuntimeState
    model_usage_budget_state: DashboardRuntimeState
    process_latency_state: DashboardRuntimeState
    storage_state: DashboardRuntimeState
    expression_episode_state: DashboardRuntimeState
    expression_episode_mode: Literal["off", "shadow", "stream"]
    semantic_recall_state: DashboardRuntimeState
    semantic_embedding_enabled: bool
    reasons: tuple[DashboardRuntimeReason, ...] = ()

    @model_validator(mode="after")
    def observation_is_coherent(self) -> DashboardRuntimeObservation:
        if (
            self.observed_at is not None
            and (self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None)
        ):
            raise ValueError("dashboard runtime observed_at must be timezone-aware")
        pairs = {(reason.signal, reason.reason_code) for reason in self.reasons}
        if len(pairs) != len(self.reasons):
            raise ValueError("dashboard runtime reasons must be unique")
        states = _runtime_state_values(self)
        for reason in self.reasons:
            if states[reason.signal] == "ready":
                raise ValueError("dashboard runtime reason cannot annotate a ready signal")
        return self


class DashboardRuntimeSignal(FrozenModel):
    key: DashboardRuntimeSignalKey
    label: str
    state: DashboardRuntimeState
    state_label: str


class DashboardRuntimeNotice(FrozenModel):
    signal: DashboardRuntimeSignalKey
    signal_label: str
    reason_code: str
    reason_label: str


class DashboardExpressionEpisodeRuntime(FrozenModel):
    mode: Literal["off", "shadow", "stream"]
    mode_label: str


class DashboardSemanticRecallRuntime(FrozenModel):
    semantic_embedding_enabled: bool
    semantic_embedding_label: str


class DashboardRuntimeOperationsData(FrozenModel):
    signals: tuple[DashboardRuntimeSignal, ...] = ()
    notice_count: int = Field(ge=0)
    notices: tuple[DashboardRuntimeNotice, ...] = ()
    expression_episode: DashboardExpressionEpisodeRuntime | None = None
    semantic_recall: DashboardSemanticRecallRuntime | None = None


class DashboardRuntimeOperationsSection(FrozenModel):
    label: Literal["运行时状态"] = "运行时状态"
    state: DashboardSectionState
    source: Literal["runtime"] = "runtime"
    observed_at: datetime | None = None
    cursor: ProjectionCursor
    coverage: DashboardCoverage
    reason_code: str | None = None
    data: DashboardRuntimeOperationsData


class DashboardHomeSections(FrozenModel):
    room: DashboardRoomSection
    overview_life: DashboardOverviewLifeSection
    facts_memory_inner: DashboardFactsMemoryInnerSection
    relationship_lifecycle: DashboardRelationshipLifecycleSection
    operations: DashboardOperationsSection
    perception_media: DashboardPerceptionMediaSection
    authority_privacy: DashboardAuthorityPrivacySection
    ledger_qualification: DashboardLedgerQualificationSection
    runtime_operations: DashboardRuntimeOperationsSection


class DashboardHomeSnapshot(FrozenModel):
    schema_version: Literal["world-v2-dashboard-home.1"] = "world-v2-dashboard-home.1"
    policy_version: Literal["dashboard-owner-policy.1"] = "dashboard-owner-policy.1"
    snapshot_hash: str
    owner: DashboardOwnerIdentity
    world_id: str
    generated_at: datetime
    cursor: ProjectionCursor
    logical_time: datetime | None = None
    sections: DashboardHomeSections

    @model_validator(mode="after")
    def hash_matches_visible_payload(self) -> DashboardHomeSnapshot:
        expected = _snapshot_hash(self.model_dump(mode="json", exclude={"snapshot_hash"}))
        if self.snapshot_hash != expected:
            raise ValueError("dashboard snapshot hash does not match its visible payload")
        return self

    def to_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class DashboardHomeSnapshotModule:
    """Compile a stable owner view without advancing or interpreting the World."""

    def __init__(
        self,
        *,
        ledger: LedgerPort,
        deployment_id: str,
        boot_id: str,
        clock: Callable[[], datetime] | None = None,
        life_content_store: ImmutableLifeContentStore | None = None,
        life_actor_ref: str | None = None,
        life_privacy_ceiling: Literal["public", "shareable", "personal", "private"] = "shareable",
    ) -> None:
        if not deployment_id or not boot_id:
            raise ValueError("dashboard owner identity must be complete")
        self._ledger = ledger
        self._life_content_store = life_content_store
        self._life_actor_ref = life_actor_ref
        self._life_privacy_ceiling = life_privacy_ceiling
        self._owner = DashboardOwnerIdentity(
            deployment_id=deployment_id,
            boot_id=boot_id,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._capture_lock = asyncio.Lock()
        self._cached_key: tuple[
            str,
            ProjectionCursor,
            str,
            str,
            str,
            str | None,
        ] | None = None
        self._cached_snapshot: DashboardHomeSnapshot | None = None

    async def capture(
        self,
        runtime_observation: DashboardRuntimeObservation | None = None,
    ) -> DashboardHomeSnapshot:
        """Capture exactly one projection; blocking compile/lookup stays off-loop."""

        async with self._capture_lock:
            if self._ledger.blocks_event_loop:
                return await asyncio.to_thread(self._capture_sync, runtime_observation)
            return self._capture_sync(runtime_observation)

    def _capture_sync(
        self,
        runtime_observation: DashboardRuntimeObservation | None,
    ) -> DashboardHomeSnapshot:
        projection = self._ledger.project()
        cursor = ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
        runtime_key = (
            _snapshot_hash(runtime_observation.model_dump(mode="json"))
            if runtime_observation is not None
            else None
        )
        cache_key = (
            projection.world_id,
            cursor,
            projection.schema_version,
            projection.reducer_bundle_version,
            projection.semantic_hash,
            runtime_key,
        )
        if self._cached_key == cache_key and self._cached_snapshot is not None:
            return self._cached_snapshot

        generated_at = self._clock()
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("dashboard clock must be timezone-aware")

        sections = DashboardHomeSections(
            room=self._room(projection=projection, cursor=cursor),
            overview_life=self._overview_life(projection=projection, cursor=cursor),
            facts_memory_inner=self._facts_memory_inner(
                projection=projection,
                cursor=cursor,
            ),
            relationship_lifecycle=self._relationship_lifecycle(
                projection=projection,
                cursor=cursor,
            ),
            operations=self._operations(projection=projection, cursor=cursor),
            perception_media=self._perception_media(
                projection=projection,
                cursor=cursor,
            ),
            authority_privacy=self._authority_privacy(
                projection=projection,
                cursor=cursor,
            ),
            ledger_qualification=self._ledger_qualification(
                projection=projection,
                cursor=cursor,
            ),
            runtime_operations=self._runtime_operations(
                cursor=cursor,
                observation=runtime_observation,
            ),
        )
        payload_without_hash = {
            "schema_version": "world-v2-dashboard-home.1",
            "policy_version": "dashboard-owner-policy.1",
            "owner": self._owner.model_dump(mode="json"),
            "world_id": projection.world_id,
            "generated_at": _json_datetime(generated_at),
            "cursor": cursor.model_dump(mode="json"),
            "logical_time": _json_datetime(projection.logical_time),
            "sections": sections.model_dump(mode="json"),
        }
        snapshot = DashboardHomeSnapshot(
            snapshot_hash=_snapshot_hash(payload_without_hash),
            owner=self._owner,
            world_id=projection.world_id,
            generated_at=generated_at,
            cursor=cursor,
            logical_time=projection.logical_time,
            sections=sections,
        )
        self._cached_key = cache_key
        self._cached_snapshot = snapshot
        return snapshot

    @staticmethod
    def _room(
        *,
        projection: LedgerProjection,
        cursor: ProjectionCursor,
    ) -> DashboardRoomSection:
        unavailable = DashboardSceneRoute(
            scene_id="unavailable",
            action_id="idle",
            availability="unavailable",
        )
        actor_ref = _companion_actor_ref(projection)
        route = unavailable
        if actor_ref is not None:
            locations = tuple(
                item for item in projection.locations if item.actor_ref == actor_ref
            )
            active_plans = tuple(
                item
                for item in projection.plans
                if item.owner_actor_ref == actor_ref and item.status == "active"
            )
            if (
                len(locations) == 1
                and locations[0].values.privacy_class in _ROOM_VISIBLE_PRIVACY
                and locations[0].values.scene_visibility in _ROOM_VISIBLE_PRIVACY
                and locations[0].values.location_ref == _HOME_LOCATION_REF
                and len(active_plans) == 1
                and active_plans[0].privacy_class in _ROOM_VISIBLE_PRIVACY
                and active_plans[0].location_ref == _HOME_LOCATION_REF
                and active_plans[0].activity_kind in _ACTIVITY_ROUTES
            ):
                public_view = RoomProjectionMaterializer.materialize(projection)
                candidate = _ROOM_ROUTES.route(public_view)
                if candidate.availability != "unavailable":
                    route = candidate

        ready = route.availability != "unavailable"
        reason_code: str | None
        if ready:
            reason_code = None
        elif projection.logical_time is None and cursor.ledger_sequence == 0:
            reason_code = "world_not_initialized"
        else:
            reason_code = "renderer_route_unavailable"
        return DashboardRoomSection(
            state="ready" if ready else "unavailable",
            observed_at=projection.logical_time,
            cursor=cursor,
            coverage=_coverage(1 if ready else 0, 1 if ready else 0),
            reason_code=reason_code,
            render_state=DashboardRoomRenderState(
                route=route,
                logical_time=projection.logical_time,
            ),
        )

    def _overview_life(
        self,
        *,
        projection: LedgerProjection,
        cursor: ProjectionCursor,
    ) -> DashboardOverviewLifeSection:
        metrics = _overview_life_metrics(projection)
        # Only the bounded display candidates need additional immutable reads.
        plans = sorted(projection.plans, key=lambda item: (
            item.last_transitioned_at or datetime.min.replace(tzinfo=UTC), item.plan_id,
        ))[-_KIND_KEEP["plan"]:]
        intentions = {
            plan.plan_id: material for plan in plans
            if (material := read_dashboard_life_intention(
                ledger=self._ledger, projection=projection, plan=plan,
            )) is not None
        }
        occurrences = sorted(projection.world_occurrences, key=lambda item: (
            item.settled_at or item.activated_at or datetime.min.replace(tzinfo=UTC), item.occurrence_id,
        ))[-_KIND_KEEP_DEFAULT:]
        # Experiences can refer to an older settlement than the latest World
        # cards. Read only the additional exact sources of displayed experiences.
        experiences = sorted(projection.experiences, key=lambda item: (
            getattr(getattr(item, 'values', item), 'occurred_to', None) or datetime.min.replace(tzinfo=UTC),
            item.experience_id,
        ))[-_KIND_KEEP_DEFAULT:]
        occurrence_map = {item.occurrence_id: item for item in occurrences}
        for experience in experiences:
            source = experience_world_occurrence(projection, experience)
            if source is not None:
                occurrence_map[source.occurrence_id] = source
        world_readings = {
            item.occurrence_id: read_dashboard_world_occurrence(
                ledger=self._ledger, store=self._life_content_store, projection=projection,
                cursor=cursor, occurrence=item, actor_ref=self._life_actor_ref,
                viewer_privacy_ceiling=self._life_privacy_ceiling,
            ) for item in occurrence_map.values()
        } if self._life_content_store is not None and self._life_actor_ref else {}
        all_highlights = _overview_life_summaries(
            projection, intentions=intentions, plan_display_candidates=plans,
            world_readings=world_readings,
        )
        highlights = _bounded_summaries(all_highlights)
        return DashboardOverviewLifeSection(
            state=_state_from_metrics(metrics),
            observed_at=projection.logical_time,
            cursor=cursor,
            coverage=_coverage(
                len(all_highlights) + len(projection.plans) - len(plans), len(highlights),
            ),
            data=DashboardOverviewLifeData(metrics=metrics, highlights=highlights),
        )

    @staticmethod
    def _facts_memory_inner(
        *,
        projection: LedgerProjection,
        cursor: ProjectionCursor,
    ) -> DashboardFactsMemoryInnerSection:
        metrics = _facts_memory_inner_metrics(projection)
        all_highlights = _facts_memory_inner_summaries(projection)
        highlights = _bounded_summaries(all_highlights)
        return DashboardFactsMemoryInnerSection(
            state=_state_from_metrics(metrics),
            observed_at=projection.logical_time,
            cursor=cursor,
            coverage=_coverage(len(all_highlights), len(highlights)),
            data=DashboardFactsMemoryInnerData(metrics=metrics, highlights=highlights),
        )

    def _relationship_lifecycle(
        self,
        *,
        projection: LedgerProjection,
        cursor: ProjectionCursor,
    ) -> DashboardRelationshipLifecycleSection:
        metrics = _relationship_metrics(projection)
        from .dashboard_pending_reading import read_thread_reason
        visible_threads = sorted(projection.threads, key=lambda item: (item.updated_at, item.thread_id))[-_KIND_KEEP["thread"]:]
        thread_reasons = {item.thread_id: read_thread_reason(
            ledger=self._ledger, projection=projection, thread=item) for item in visible_threads}
        all_highlights = _relationship_summaries(projection, thread_reasons=thread_reasons)
        highlights = _bounded_summaries(all_highlights)
        candidates = _relationship_terminal_candidates(projection)
        located: list[
            tuple[
                int,
                ProposalAuditProjection,
                TypedChange,
                AuditedChangeTerminalSettlement,
                ProjectionCursor,
            ]
        ] = []
        unsettled_count = 0
        for audit, change in candidates:
            settlement = find_terminal_audited_change(
                ledger=self._ledger,
                audit=audit,
                change=change,
            )
            if settlement is None:
                unsettled_count += 1
                continue
            terminal_cursor = ProjectionCursor(
                world_revision=settlement.commit.world_revision,
                deliberation_revision=settlement.commit.deliberation_revision,
                ledger_sequence=settlement.commit.ledger_sequence,
            )
            if _cursor_after(terminal_cursor, cursor):
                unsettled_count += 1
                continue
            located.append(
                (
                    terminal_cursor.ledger_sequence,
                    audit,
                    change,
                    settlement,
                    terminal_cursor,
                )
            )

        located.sort(key=lambda item: item[0])
        terminals: list[DashboardTypedChangeTerminal] = []
        for _sequence, audit, change, settlement, terminal_cursor in located[
            -_TERMINAL_RETURN_LIMIT:
        ]:
            event_ref = audited_change_terminal_event_id(audit=audit, change=change)
            event_commit = self._ledger.lookup_event_commit(event_ref)
            if event_commit is None:
                raise ValueError("dashboard terminal event is unavailable")
            event, event_commit_result = event_commit
            if event_commit_result != settlement.commit:
                raise ValueError("dashboard terminal event commit changed during capture")
            authored = terminal_relationship_commitment_payload(change)
            terminals.append(
                DashboardTypedChangeTerminal(
                    status=settlement.status,
                    status_label=_label(settlement.status, _STATUS_LABELS),
                    reason_code=settlement.reason_code,
                    target_stage=authored.target_stage,
                    target_stage_label=_label(authored.target_stage, _STAGE_LABELS),
                    commitment_code=authored.commitment_code,
                    occurred_at=event.logical_time,
                    cursor=terminal_cursor,
                )
            )
        terminal_count = len(located)
        rejected_count = sum(
            settlement.status == "rejected"
            for _sequence, _audit, _change, settlement, _cursor in located
        )
        stale_count = terminal_count - rejected_count
        known_count = len(all_highlights) + terminal_count
        included_count = len(highlights) + len(terminals)
        data = DashboardRelationshipLifecycleData(
            relationship_state_count=len(projection.relationship_states),
            commitment_count=len(projection.relationship_commitments),
            interaction_act_count=len(projection.interaction_acts),
            metrics=metrics,
            highlights=highlights,
            typed_change_candidate_count=len(candidates),
            typed_change_lookup_count=len(candidates),
            typed_change_terminal_count=terminal_count,
            typed_change_rejected_count=rejected_count,
            typed_change_stale_count=stale_count,
            typed_change_unsettled_count=unsettled_count,
            typed_change_terminals=tuple(terminals),
        )
        return DashboardRelationshipLifecycleSection(
            state=(
                "ready"
                if any(metric.count for metric in metrics) or candidates
                else "empty"
            ),
            observed_at=projection.logical_time,
            cursor=cursor,
            coverage=_coverage(known_count, included_count),
            data=data,
        )

    @staticmethod
    def _operations(
        *,
        projection: LedgerProjection,
        cursor: ProjectionCursor,
    ) -> DashboardOperationsSection:
        metrics = _operations_metrics(projection)
        all_highlights = _operations_summaries(projection)
        highlights = _bounded_summaries(all_highlights)
        notices = _operation_notices(projection)
        return DashboardOperationsSection(
            state=_state_from_metrics(metrics),
            observed_at=projection.logical_time,
            cursor=cursor,
            coverage=_coverage(len(all_highlights), len(highlights)),
            data=DashboardOperationsData(
                metrics=metrics,
                mechanisms=mechanism_activity(projection),
                highlights=highlights,
                notices=notices,
            ),
        )

    @staticmethod
    def _perception_media(
        *,
        projection: LedgerProjection,
        cursor: ProjectionCursor,
    ) -> DashboardPerceptionMediaSection:
        metrics = _perception_media_metrics(projection)
        all_highlights = _perception_media_summaries(projection)
        highlights = _bounded_summaries(all_highlights)
        return DashboardPerceptionMediaSection(
            state=_state_from_metrics(metrics),
            observed_at=projection.logical_time,
            cursor=cursor,
            coverage=_coverage(len(all_highlights), len(highlights)),
            data=DashboardPerceptionMediaData(metrics=metrics, highlights=highlights),
        )

    @staticmethod
    def _authority_privacy(
        *,
        projection: LedgerProjection,
        cursor: ProjectionCursor,
    ) -> DashboardAuthorityPrivacySection:
        metrics = _authority_privacy_metrics(projection)
        all_highlights = _authority_privacy_summaries(projection)
        highlights = _bounded_summaries(all_highlights)
        return DashboardAuthorityPrivacySection(
            state=_state_from_metrics(metrics),
            observed_at=projection.logical_time,
            cursor=cursor,
            coverage=_coverage(len(all_highlights), len(highlights)),
            data=DashboardAuthorityPrivacyData(metrics=metrics, highlights=highlights),
        )

    @staticmethod
    def _ledger_qualification(
        *,
        projection: LedgerProjection,
        cursor: ProjectionCursor,
    ) -> DashboardLedgerQualificationSection:
        metrics = _ledger_qualification_metrics(projection)
        exposures = tuple(item.exposure for item in DASHBOARD_LEDGER_FIELD_POLICY.values())
        return DashboardLedgerQualificationSection(
            state=_state_from_metrics(metrics),
            observed_at=projection.logical_time,
            cursor=cursor,
            coverage=_coverage(0, 0),
            data=DashboardLedgerQualificationData(
                ledger_schema_version=projection.schema_version,
                reducer_bundle_version=projection.reducer_bundle_version,
                field_policy_count=len(DASHBOARD_LEDGER_FIELD_POLICY),
                typed_summary_field_count=exposures.count("typed_summary"),
                count_only_field_count=exposures.count("count_only"),
                metadata_field_count=exposures.count("metadata"),
                intentionally_withheld_field_count=exposures.count(
                    "intentionally_withheld"
                ),
                metrics=metrics,
            ),
        )

    @staticmethod
    def _runtime_operations(
        *,
        cursor: ProjectionCursor,
        observation: DashboardRuntimeObservation | None,
    ) -> DashboardRuntimeOperationsSection:
        if observation is None:
            return DashboardRuntimeOperationsSection(
                state="unavailable",
                cursor=cursor,
                coverage=_coverage(0, 0),
                reason_code="runtime_observation_not_supplied",
                data=DashboardRuntimeOperationsData(notice_count=0),
            )
        states = _runtime_state_values(observation)
        signals = tuple(
            DashboardRuntimeSignal(
                key=key,
                label=_RUNTIME_SIGNAL_LABELS[key],
                state=state,
                state_label=_RUNTIME_STATE_LABELS[state],
            )
            for key, state in states.items()
        )
        notices = tuple(
            DashboardRuntimeNotice(
                signal=reason.signal,
                signal_label=_RUNTIME_SIGNAL_LABELS[reason.signal],
                reason_code=reason.reason_code,
                reason_label=_label(reason.reason_code, _RUNTIME_REASON_LABELS),
            )
            for reason in observation.reasons
        )
        return DashboardRuntimeOperationsSection(
            state="ready",
            observed_at=observation.observed_at,
            cursor=cursor,
            coverage=_coverage(len(signals), len(signals)),
            data=DashboardRuntimeOperationsData(
                signals=signals,
                notice_count=len(notices),
                notices=notices,
                expression_episode=DashboardExpressionEpisodeRuntime(
                    mode=observation.expression_episode_mode,
                    mode_label={
                        "off": "说话节奏未启用",
                        "shadow": "只在旁边看着",
                        "stream": "按语义往外说",
                    }[observation.expression_episode_mode],
                ),
                semantic_recall=DashboardSemanticRecallRuntime(
                    semantic_embedding_enabled=observation.semantic_embedding_enabled,
                    semantic_embedding_label=(
                        "语义嵌入已启用"
                        if observation.semantic_embedding_enabled
                        else "语义嵌入未启用"
                    ),
                ),
            ),
        )


def _metric(key: str, label: str, value: Sequence[object] | int | None) -> DashboardMetric:
    if isinstance(value, int):
        count = value
    elif value is None:
        count = 0
    else:
        count = len(value)
    return DashboardMetric(key=key, label=label, count=count,
        count_note=ZERO_NOTES.get(key, '本世界当前投影没有此类记录；单凭零值无法判断是否启用。') if count == 0 else None)


def _overview_life_metrics(projection: LedgerProjection) -> tuple[DashboardMetric, ...]:
    return (
        _metric("goals", "目标", projection.goals),
        _metric("goal_transitions", "目标变更", projection.goal_transitions),
        _metric("goal_proposals", "目标提议", projection.goal_proposals),
        _metric("goal_proposal_ids", "目标提议标识", projection.goal_proposal_ids),
        _metric("locations", "位置状态", projection.locations),
        _metric("location_transitions", "位置变更", projection.location_transitions),
        _metric("location_proposals", "位置提议", projection.location_proposals),
        _metric("location_proposal_ids", "位置提议标识", projection.location_proposal_ids),
        _metric("resources", "资源状态", projection.resources),
        _metric("resource_transitions", "资源变更", projection.resource_transitions),
        _metric("resource_proposals", "资源提议", projection.resource_proposals),
        _metric("resource_proposal_ids", "资源提议标识", projection.resource_proposal_ids),
        _metric("attentions", "注意力状态", projection.attentions),
        _metric("attention_transitions", "注意力变更", projection.attention_transitions),
        _metric("attention_proposals", "注意力提议", projection.attention_proposals),
        _metric("attention_proposal_ids", "注意力提议标识", projection.attention_proposal_ids),
        _metric("world_places", "世界地点", projection.world_places),
        _metric("biographical_coordinates", "生平坐标", projection.biographical_coordinates),
        _metric("life_arcs", "生活线", projection.life_arcs),
        _metric("aspirations", "愿望", projection.aspirations),
        _metric("plans", "计划与活动", projection.plans),
        _metric("world_occurrences", "世界事件", projection.world_occurrences),
        _metric("outcome_observations", "结果观察", projection.outcome_observations),
        _metric("experiences", "经历", projection.experiences),
        _metric("experience_transitions", "经历变更", projection.experience_transitions),
        _metric("experience_proposals", "经历提议", projection.experience_proposals),
        _metric("experience_proposal_ids", "经历提议标识", projection.experience_proposal_ids),
    )


def _facts_memory_inner_metrics(
    projection: LedgerProjection,
) -> tuple[DashboardMetric, ...]:
    return (
        _metric("facts", "事实", projection.facts),
        _metric("fact_transitions", "事实变更", projection.fact_transitions),
        _metric("fact_proposals", "事实提议", projection.fact_proposals),
        _metric("fact_proposal_ids", "事实提议标识", projection.fact_proposal_ids),
        _metric("prehistory_archives", "启动前人生档案", projection.prehistory_archives),
        _metric("prehistory_records", "启动前历史片段", projection.prehistory_records),
        _metric("memory_candidates", "记忆候选", projection.memory_candidates),
        _metric("memory_candidate_transitions", "记忆变更", projection.memory_candidate_transitions),
        _metric("memory_candidate_proposals", "记忆提议", projection.memory_candidate_proposals),
        _metric("memory_candidate_proposal_ids", "记忆提议标识", projection.memory_candidate_proposal_ids),
        _metric("character_core", "角色核心", 1 if projection.character_core is not None else 0),
        _metric("character_core_transitions", "角色核心变更", projection.character_core_transitions),
        _metric("character_core_proposals", "角色核心提议", projection.character_core_proposals),
        _metric("character_core_proposal_ids", "角色核心提议标识", projection.character_core_proposal_ids),
        _metric("appraisals", "情境评估", projection.appraisals),
        _metric("affect_baselines", "情感基线", projection.affect_baselines),
        _metric("affect_episodes", "心情", projection.affect_episodes),
        _metric("appraisal_proposals", "评估提议", projection.appraisal_proposals),
        _metric("appraisal_proposal_ids", "评估提议标识", projection.appraisal_proposal_ids),
        _metric("affect_proposals", "情感提议", projection.affect_proposals),
        _metric("affect_proposal_ids", "情感提议标识", projection.affect_proposal_ids),
    )


def _relationship_metrics(projection: LedgerProjection) -> tuple[DashboardMetric, ...]:
    return (
        _metric("npcs", "人物", projection.npcs),
        _metric("relationship_signals", "关系信号", projection.relationship_signals),
        _metric("relationship_commitments", "关系承诺", projection.relationship_commitments),
        _metric("interaction_acts", "互动行为", projection.interaction_acts),
        _metric("interaction_act_transitions", "互动行为变更", projection.interaction_act_transitions),
        _metric("interaction_act_proposals", "互动行为提议", projection.interaction_act_proposals),
        _metric("relationship_adjustments", "关系调整", projection.relationship_adjustments),
        _metric("relationship_states", "关系状态", projection.relationship_states),
        _metric("boundaries", "边界", projection.boundaries),
        _metric("relationship_proposals", "关系提议", projection.relationship_proposals),
        _metric("relationship_proposal_ids", "关系提议标识", projection.relationship_proposal_ids),
        _metric("private_impressions", "私人印象", projection.private_impressions),
        _metric("private_impression_proposals", "私人印象提议", projection.private_impression_proposals),
        _metric("private_impression_proposal_ids", "私人印象提议标识", projection.private_impression_proposal_ids),
        _metric("threads", "关系线程", projection.threads),
        _metric("thread_transitions", "线程变更", projection.thread_transitions),
        _metric("thread_proposals", "线程提议", projection.thread_proposals),
        _metric("thread_proposal_ids", "线程提议标识", projection.thread_proposal_ids),
        _metric("commitments", "一般承诺", projection.commitments),
        _metric("commitment_transitions", "承诺变更", projection.commitment_transitions),
        _metric("commitment_proposals", "承诺提议", projection.commitment_proposals),
        _metric("commitment_proposal_ids", "承诺提议标识", projection.commitment_proposal_ids),
        _metric("interaction_bids", "互动期待", projection.interaction_bids),
        _metric("interaction_bid_proposals", "互动期待提议", projection.interaction_bid_proposals),
        _metric("media_thread_proposals", "媒体线程提议", projection.media_thread_proposals),
    )


def _operations_metrics(projection: LedgerProjection) -> tuple[DashboardMetric, ...]:
    failed_actions = sum(item.state == "failed" for item in projection.actions)
    proactive_actions = sum(
        item.kind in {"proactive_message", "followup"} for item in projection.actions
    )
    failed_receipts = sum(item.error_class is not None for item in projection.execution_receipts)
    return (
        _metric("actions", "行动", projection.actions),
        _metric("pending_actions", "待处理行动", projection.pending_actions),
        _metric("failed_actions", "失败行动", failed_actions),
        _metric("proactive_actions", "主动与跟进行动", proactive_actions),
        _metric("read_only_tool_requests", "只读工具请求", projection.read_only_tool_requests),
        _metric("tool_results", "工具结果", projection.tool_results),
        _metric("budget_accounts", "预算账户", projection.budget_accounts),
        _metric("budget_reservations", "预算预留", projection.budget_reservations),
        _metric("trigger_processes", "触发流程", projection.trigger_processes),
        _metric("life_ecology_schedule", "生活生态调度", 1 if projection.life_ecology_schedule is not None else 0),
        _metric("pending_contextual_life_sources", "退役生活来源索引", projection.pending_contextual_life_sources),
        _metric("contextual_life_retries", "生活来源重试", projection.contextual_life_retries),
        _metric("pending_biographical_settlements", "待结算生平变化", projection.pending_biographical_settlements),
        _metric("pending_external_observations", "待接受外部观察", projection.pending_external_observations),
        _metric("execution_receipts", "执行回执", projection.execution_receipts),
        _metric("failed_receipts", "失败回执", failed_receipts),
        _metric("budget_settlements", "预算结算", projection.budget_settlements),
        _metric("reconciliations", "行动对账", projection.reconciliations),
        _metric("completed_trigger_ids", "已完成触发", projection.completed_trigger_ids),
        _metric("minimal_reply_manifests", "最小回复清单", projection.minimal_reply_manifests),
        _metric("response_expectation_assessments", "她有没有等到", projection.response_expectation_assessments),
        _metric("stored_message_payloads", "已存消息", projection.stored_message_payloads),
        _metric("expression_payload_descriptors", "表达载荷描述", projection.expression_payload_descriptors),
        _metric("life_content_descriptors", "生活内容描述", projection.life_content_descriptors),
        _metric("expression_plans", "表达计划", projection.expression_plans),
        _metric("expression_beats", "表达节拍", projection.expression_beats),
        _metric("acceptance_decisions", "接受决定", projection.acceptance_decisions),
        _metric("outcome_proposals", "结果提议", projection.outcome_proposals),
    )


def _perception_media_metrics(projection: LedgerProjection) -> tuple[DashboardMetric, ...]:
    return (
        _metric("perception_requests", "感知请求", projection.perception_requests),
        _metric("perception_results", "感知结果", projection.perception_results),
        _metric("external_signal_snapshots", "外部信号快照", projection.external_signal_snapshots),
        _metric("external_perceptions", "已注意的外部感知", projection.external_perceptions),
        _metric("external_perception_acceptance_manifests", "外部感知接受清单", projection.external_perception_acceptance_manifests),
        _metric("appearance_states", "外观状态", projection.appearance_states),
        _metric("visible_physical_states", "可见身体状态", projection.visible_physical_states),
        _metric("photo_candidates", "照片候选", projection.photo_candidates),
        _metric("media_declined_candidate_revisions", "未选择媒体候选", projection.media_declined_candidate_revisions),
        _metric("media_opportunities", "媒体机会", projection.media_opportunities),
        _metric("media_plans", "媒体计划", projection.media_plans),
        _metric("media_unrenderable_opportunity_ids", "不可渲染媒体机会", projection.media_unrenderable_opportunity_ids),
        _metric("media_artifacts", "媒体产物", projection.media_artifacts),
        _metric("media_inspections", "媒体检查", projection.media_inspections),
        _metric("media_previews", "媒体预览", projection.media_previews),
        _metric("media_failed_plan_ids", "失败媒体计划", projection.media_failed_plan_ids),
        _metric("media_delivery_approvals", "媒体送达批准", projection.media_delivery_approvals),
        _metric("media_deliveries", "媒体送达", projection.media_deliveries),
    )


def _authority_privacy_metrics(projection: LedgerProjection) -> tuple[DashboardMetric, ...]:
    return (
        _metric("actor_authorities", "角色权限", projection.actor_authorities),
        _metric("actor_authority_transitions", "角色权限变更", projection.actor_authority_transitions),
        _metric("consumed_actor_root_nonces", "已消费角色根随机数", projection.consumed_actor_root_nonces),
        _metric("capability_grants", "能力授权", projection.capability_grants),
        _metric("capability_transitions", "能力授权变更", projection.capability_transitions),
        _metric("consent_grants", "同意授权", projection.consent_grants),
        _metric("consent_transitions", "同意授权变更", projection.consent_transitions),
        _metric("privacy_policies", "隐私策略", projection.privacy_policies),
        _metric("privacy_transitions", "隐私策略变更", projection.privacy_transitions),
        _metric("provider_media_grants", "媒体供应授权", projection.provider_media_grants),
        _metric("consumed_authorization_root_nonces", "已消费授权根随机数", projection.consumed_authorization_root_nonces),
        _metric("consumed_authorization_challenge_ids", "已消费授权挑战", projection.consumed_authorization_challenge_ids),
        _metric("consumed_authorization_source_ids", "已消费授权来源", projection.consumed_authorization_source_ids),
    )


def _ledger_qualification_metrics(
    projection: LedgerProjection,
) -> tuple[DashboardMetric, ...]:
    terminal_receipts = sum(item.is_terminal for item in projection.execution_receipts)
    return (
        _metric("observation_refs", "观察引用", projection.observation_refs),
        _metric("message_observations", "消息观察", projection.message_observations),
        _metric("operator_observations", "操作员观察", projection.operator_observations),
        _metric("committed_world_event_refs", "已提交世界事件", projection.committed_world_event_refs),
        _metric("clock_transition_history", "世界时钟变更", projection.clock_transition_history),
        _metric("proposal_ids", "决策提议", projection.proposal_ids),
        _metric("proposal_revisions", "提议修订", projection.proposal_revisions),
        _metric("model_result_audits", "模型结果审计", projection.model_result_audits),
        _metric("proposal_audits", "提议审计", projection.proposal_audits),
        _metric("interaction_fact_decisions", "互动事实决定", projection.interaction_fact_decisions),
        _metric("acceptance_manifests_v2", "接受清单 v2", projection.acceptance_manifests_v2),
        _metric("fact_commit_proposal_audits_v2", "事实提交提议审计", projection.fact_commit_proposal_audits_v2),
        _metric("acceptance_manifests_v3", "接受清单 v3", projection.acceptance_manifests_v3),
        _metric("expression_plan_manifests", "表达计划清单", projection.expression_plan_manifests),
        _metric("terminal_execution_receipts", "终态执行回执", terminal_receipts),
    )


def _overview_life_summaries(
    projection: LedgerProjection,
    *, intentions: Mapping[str, DashboardLifeIntention] | None = None,
    world_readings: Mapping[str, DashboardWorldOccurrenceReading] | None = None,
    plan_display_candidates: Sequence[object] | None = None,
) -> tuple[DashboardEntitySummary, ...]:
    summaries: list[DashboardEntitySummary] = []
    for item in projection.goals:
        values = item.values
        summaries.append(
            _summary(
                kind="goal",
                kind_label="目标",
                entity_id=item.goal_id,
                title="目标状态",
                status=values.status,
                occurred_at=item.updated_at,
                privacy_class=values.privacy_class,
                values=(
                    _value("progress_bp", "进度", values.progress_bp),
                    _value("importance_bp", "重要度", values.importance_bp),
                ),
            )
        )
    for item in projection.locations:
        summaries.append(
            _summary(
                kind="location",
                kind_label="位置",
                entity_id=item.actor_ref,
                title=_place_title(item.values.location_ref),
                status=item.values.scene_visibility,
                status_mapping=_STATUS_LABELS,
                occurred_at=item.updated_at,
                privacy_class=item.values.privacy_class,
            )
        )
    for item in projection.resources:
        summaries.append(
            _summary(
                kind="resource",
                kind_label="资源",
                entity_id=f"{item.actor_ref}:{item.resource_kind}",
                title=_label(item.resource_kind, _STATUS_LABELS),
                status=item.values.derived_band,
                occurred_at=item.updated_at,
                privacy_class=item.values.privacy_class,
                values=(_bp_value("value_bp", "现在", item.values.value_bp),),
            )
        )
    for item in projection.attentions:
        summaries.append(
            _summary(
                kind="attention",
                kind_label="注意力",
                entity_id=item.actor_ref,
                title=_label(item.values.mode, _STATUS_LABELS),
                status=item.values.mode,
                occurred_at=item.updated_at,
                privacy_class=item.values.privacy_class,
                values=(
                    _value("allocation_bp", "投入", item.values.allocation_bp),
                    _value("interruptibility_bp", "可打断度", item.values.interruptibility_bp),
                ),
            )
        )
    for item in projection.world_places:
        summaries.append(
            _summary(
                kind="world_place",
                kind_label="世界地点",
                entity_id=None,
                title="地点资料已接受",
                status=item.access_assurance,
                occurred_at=item.accepted_at,
                privacy_class=item.privacy_class,
            )
        )
    for item in projection.biographical_coordinates:
        summaries.append(
            _summary(
                kind="biographical_coordinate",
                kind_label="生平坐标",
                entity_id=item.coordinate_ref,
                title="生平坐标",
                detail=_short_text(item.summary),
                occurred_at=item.settled_at,
                privacy_class=item.privacy_class,
            )
        )
    for item in projection.life_arcs:
        summaries.append(
            _summary(
                kind="life_arc",
                kind_label="生活线",
                entity_id=item.arc_id,
                title=_label(item.arc_kind, {}),
                status=item.status,
                occurred_at=item.closed_at or item.started_at,
                privacy_class=item.privacy_class,
            )
        )
    for item in projection.aspirations:
        summaries.append(
            _summary(
                kind="aspiration",
                kind_label="愿望",
                entity_id=item.aspiration_id,
                title=_short_text(item.text) or "愿望",
                status=item.status,
                detail=_short_text(item.tension_summary),
                occurred_at=item.last_revised_at or item.planted_at,
                privacy_class=item.privacy_class,
                values=(
                    _value("reinforcement_count", "被想起次数", item.reinforcement_count),
                ),
            )
        )
    for item in projection.plans if plan_display_candidates is None else plan_display_candidates:
        intention = (intentions or {}).get(item.plan_id)
        plan_values = [_value("importance_bp", "重要度", item.importance_bp)]
        window = getattr(item, "scheduled_window", None)
        if window is not None:
            plan_values.extend((
                _value("scheduled_start", "计划开始", window.opens_at.isoformat()),
                _value("scheduled_end", "计划结束", window.closes_at.isoformat()),
            ))
        if intention is not None:
            plan_values.extend((
                _value("intention", "原意图", intention.intention),
                _value("intent_source", "意图来源", intention.source_kind, {
                    "day_open": "当日自主安排", "chat": "同次对话中的自主选择",
                    "world": "对已结算事件的自主回应",
                }[intention.source_kind]),
                _value("execution_scope", "活动范围", "self_directed", "自己进行的活动"),
                _value("selected_at", "选择时间", intention.selected_at.isoformat()),
            ))
        summaries.append(
            _summary(
                kind="plan",
                kind_label="计划与活动",
                entity_id=item.plan_id,
                title=(_short_text(intention.intention, limit=80) if intention else None)
                or _activity_title(item.activity_kind),
                detail=intention.intention if intention else None,
                status=_plan_display_status(item, projection.logical_time),
                occurred_at=item.last_transitioned_at,
                privacy_class=item.privacy_class,
                values=tuple(plan_values),
            )
        )
    for item in projection.world_occurrences:
        reading = (world_readings or {}).get(item.occurrence_id)
        content_status = reading.status if reading else "not_read" if item.status == "settled" else "not_settled"
        summaries.append(
            _summary(
                kind="world_occurrence",
                kind_label="世界事件",
                entity_id=item.occurrence_id,
                title="世界事件",
                status=item.status,
                detail=reading.text if reading else None,
                occurred_at=item.settled_at or item.activated_at,
                privacy_class=getattr(item, "visibility", "withhold"),
                values=(
                    _value("candidate_outcome_count", "候选结果", len(item.candidate_outcomes)),
                    _value("observation_count", "观察", len(item.observation_refs)),
                    _value("world_environment_status", "环境结果正文",
                           content_status, {"read": "已读取", "not_read": "未读取",
                                            "not_settled": "尚未结算", "unavailable": "来源或正文不可用",
                                            "withheld": "受可见范围限制"}[content_status]),
                    *((_value("world_environment_truncated", "正文范围", True, "已节选"),)
                      if reading and reading.truncated else ()),
                ),
            )
        )
    for item in projection.outcome_observations:
        summaries.append(
            _summary(
                kind="outcome_observation",
                kind_label="结果观察",
                entity_id=item.observation_id,
                title=_label(item.source_kind, _STATUS_LABELS),
                occurred_at=item.observed_at,
                values=(_value("confidence_bp", "置信度", item.confidence_bp),),
            )
        )
    for item in projection.experiences:
        source = experience_world_occurrence(projection, item)
        reading = (world_readings or {}).get(source.occurrence_id) if source is not None else None
        values = getattr(item, "values", None)
        if values is None:
            occurred_at = item.occurred_to
            privacy_class = item.privacy_class
            participant_refs = item.participant_refs
        else:
            occurred_at = values.occurred_to
            privacy_class = values.privacy_class
            participant_refs = values.participant_refs
        summaries.append(
            _summary(
                kind="experience",
                kind_label="经历",
                entity_id=item.experience_id,
                title="一段经历",
                detail=reading.text if reading is not None else None,
                status=getattr(item, "status", None),
                occurred_at=occurred_at,
                privacy_class=privacy_class,
                values=(
                    _value(
                        "participant_count",
                        "参与者",
                        len(participant_refs),
                    ),
                    *_experience_source_values(values, reading=reading),
                ),
            )
        )
    return tuple(summaries)


def _experience_source_values(values, *, reading=None) -> tuple[DashboardLabeledValue, ...]:
    bindings = getattr(values, "source_bindings", ())
    if len(bindings) != 1:
        return ()
    source = bindings[0]
    labels = {
        "occurrence_settlement": "已结算世界事件",
        "execution_receipt": "执行回执",
        "world_life_response": "已结算事件与独立角色回应",
    }
    if source.source_kind not in labels:
        return ()
    result = [_value("source_kind", "经历来源", source.source_kind, labels[source.source_kind])]
    if source.source_kind in {"occurrence_settlement", "world_life_response"}:
        status = reading.status if reading is not None else "not_read"
        result.append(_value("world_environment_status", "环境结果正文", status, {
            "read": "已读取", "not_read": "本次摘要未接入正文",
            "not_settled": "尚未结算", "unavailable": "来源或正文不可用",
            "withheld": "受可见范围限制",
        }[status]))
        if reading is not None and reading.truncated:
            result.append(_value("world_environment_truncated", "正文范围", True, "已节选"))
    # Only this composite contains a response to this exact settlement. Nearby
    # appraisals or activities cannot fill in a missing response, including null.
    if isinstance(source, ExperienceWorldLifeResponseBinding):
        explicit_none = source.response.response_text is None
        result.append(_value(
            "character_response_status", "角色回应",
            "explicit_none" if explicit_none else "recorded_private",
            "明确无回应文本" if explicit_none else "已记录私态（正文不展示）",
        ))
    return tuple(result)


def _facts_memory_inner_summaries(
    projection: LedgerProjection,
) -> tuple[DashboardEntitySummary, ...]:
    summaries: list[DashboardEntitySummary] = []
    for item in projection.facts:
        values = item.values
        summaries.append(
            _summary(
                kind="fact",
                kind_label="事实",
                entity_id=item.fact_id,
                title=_FACT_PREDICATE_LABELS.get(values.predicate_code, "已记录事实"),
                status=values.status,
                status_mapping=_FACT_STATUS_LABELS,
                occurred_at=item.updated_at or item.committed_at,
                privacy_class=values.privacy_class,
                values=(_value("confidence_bp", "置信度", values.confidence_bp),),
            )
        )
    for item in projection.memory_candidates:
        values = item.values
        memory_values = [
            _value("retrieval_strength_bp", "提取强度", values.retrieval_strength_bp),
            _value("reinforcement_count", "强化次数", values.reinforcement_count),
        ]
        rationales = tuple(getattr(values, "retention_rationales", ()))
        if rationales:
            memory_values.append(_value("retention_rationales", "保留依据", "、".join(
                _RETENTION_LABELS.get(reason, "其他保留依据") for reason in rationales
            )))
        kinds = tuple(dict.fromkeys(
            source.source_kind for source in getattr(values, "source_bindings", ())
            if source.source_kind in _MEMORY_SOURCE_LABELS
        ))
        if kinds:
            memory_values.append(_value("source_kind", "来源类型", ",".join(kinds), "、".join(
                _MEMORY_SOURCE_LABELS[kind] for kind in kinds
            )))
        for key, label in (("review_due_at", "下次复核"), ("reviewed_at", "已复核"),
                           ("forgotten_at", "遗忘时间")):
            at = getattr(values, key, None)
            if at is not None:
                memory_values.append(_value(key, label, at.isoformat()))
        summaries.append(
            _summary(
                kind="memory_candidate",
                kind_label="记忆候选",
                entity_id=item.candidate_id,
                title=_label(values.cue_kind, _CUE_LABELS),
                status=values.status,
                status_mapping=_MEMORY_STATUS_LABELS,
                occurred_at=item.updated_at,
                privacy_class=values.privacy_ceiling,
                values=tuple(memory_values),
            )
        )
    if projection.character_core is not None:
        item = projection.character_core
        summaries.append(
            _summary(
                kind="character_core",
                kind_label="角色核心",
                entity_id=item.core_id,
                title="她这个人已经立住了",
                occurred_at=item.updated_at,
                privacy_class=item.values.privacy_class,
            )
        )
    for item in projection.appraisals:
        meanings = "、".join(
            _label(hypothesis.meaning, _MEANING_LABELS)
            for hypothesis in item.hypotheses
        )
        summaries.append(
            _summary(
                kind="appraisal",
                kind_label="情境评估",
                entity_id=item.appraisal_id,
                title=meanings or "情境评估",
                status=item.status,
                status_mapping=_APPRAISAL_STATUS_LABELS,
                occurred_at=item.accepted_at,
                values=(_value("confidence_bp", "置信度", item.confidence_bp),),
            )
        )
    for item in projection.affect_baselines:
        summaries.append(
            _summary(
                kind="affect_baseline",
                kind_label="情感基线",
                entity_id=item.dimension,
                title=_label(item.dimension, {}),
                occurred_at=item.last_calibrated_at,
                values=(_value("baseline_bp", "基线", item.baseline_bp),),
            )
        )
    affect_items = tuple(
        item for item in projection.affect_episodes if item.status == "active"
    )
    if not affect_items and projection.affect_episodes:
        affect_items = projection.affect_episodes[-1:]
    for item in affect_items:
        pieces = tuple(
            f"{_label(component.dimension, _STATUS_LABELS)} {round(component.intensity_bp / 100)}%"
            for component in sorted(
                item.components,
                key=lambda component: component.intensity_bp,
                reverse=True,
            )
        )
        summaries.append(
            _summary(
                kind="affect_episode",
                kind_label="心情",
                entity_id=item.episode_id,
                title="、".join(pieces) or "心情",
                status=item.status,
                occurred_at=item.updated_at,
                privacy_class=item.privacy_class,
                values=(
                    _value("component_count", "几种心情", len(item.components)),
                    _bp_value(
                        "peak_intensity_bp",
                        "最浓的一下",
                        max((component.intensity_bp for component in item.components), default=0),
                    ),
                ),
            )
        )
    return tuple(summaries)


def _relationship_summaries(
    projection: LedgerProjection, *, thread_reasons=None,
) -> tuple[DashboardEntitySummary, ...]:
    summaries: list[DashboardEntitySummary] = []
    for item in projection.npcs:
        summaries.append(
            _summary(
                kind="npc",
                kind_label="人物",
                entity_id=item.npc_id,
                title=_NPC_LABELS.get(item.npc_id, "人物"),
                status=item.status,
                status_mapping=_NPC_STATUS_LABELS,
                privacy_class=item.privacy_class,
                occurred_at=(
                    item.subjective_state.evolved_at
                    if item.subjective_state is not None
                    else None
                ),
                values=(_value("known_trait_count", "已知特征", len(item.known_trait_refs)),),
            )
        )
    for item in projection.relationship_signals:
        summaries.append(
            _summary(
                kind="relationship_signal",
                kind_label="关系信号",
                entity_id=item.signal_id,
                title=_label(item.signal_code, {}),
                detail=_short_text(item.rationale_code),
                occurred_at=item.accepted_at,
                values=(_value("confidence_bp", "置信度", item.confidence_bp),),
            )
        )
    for item in projection.relationship_commitments:
        summaries.append(
            _summary(
                kind="relationship_commitment",
                kind_label="关系承诺",
                entity_id=item.commitment_id,
                title=_label(item.commitment_code, {}),
                status=item.status,
                detail=_short_text(item.visible_text_span),
                occurred_at=item.committed_at,
                values=(
                    _value(
                        "committed_stage",
                        "承诺阶段",
                        item.committed_stage,
                        _label(item.committed_stage, _STAGE_LABELS),
                    ),
                ),
            )
        )
    for item in projection.interaction_acts:
        summaries.append(
            _summary(
                kind="interaction_act",
                kind_label="互动行为",
                entity_id=item.interaction_act_id,
                title=_label(item.act_kind, {}),
                status=item.external_outcome,
                occurred_at=item.updated_at,
                privacy_class=item.privacy_class,
                values=(_value("counterparty_count", "对方", len(item.counterparty_refs)),),
            )
        )
    for item in projection.relationship_adjustments:
        summaries.append(
            _summary(
                kind="relationship_adjustment",
                kind_label="关系调整",
                entity_id=item.adjustment_id,
                title=_label(item.rationale_code, {}),
                status=item.operation,
                occurred_at=item.adjusted_at,
                values=(
                    _value(
                        "stage_after",
                        "调整后阶段",
                        item.stage_after,
                        _label(item.stage_after, _STAGE_LABELS),
                    ),
                    _value("confidence_bp", "置信度", item.confidence_bp),
                ),
            )
        )
    for item in projection.relationship_states:
        variables = item.variables
        summaries.append(
            _summary(
                kind="relationship_state",
                kind_label="关系状态",
                entity_id=item.relationship_id,
                title="和你",
                status=item.stage,
                status_mapping=_STAGE_LABELS,
                occurred_at=item.last_adjusted_at,
                values=(
                    _bp_value("trust_bp", "信任", variables.trust_bp),
                    _bp_value("closeness_bp", "亲近", variables.closeness_bp),
                    _bp_value("respect_bp", "尊重", variables.respect_bp),
                    _bp_value("reliability_bp", "可靠", variables.reliability_bp),
                    _bp_value("mutuality_bp", "相互", variables.mutuality_bp),
                    _bp_value("repair_confidence_bp", "修复信心", variables.repair_confidence_bp),
                ),
            )
        )
    for item in projection.boundaries:
        summaries.append(
            _summary(
                kind="boundary",
                kind_label="边界",
                entity_id=item.boundary_id,
                title="边界",
                status=item.status,
                occurred_at=item.updated_at,
                values=(_value("strength_bp", "强度", item.strength_bp),),
            )
        )
    for item in projection.threads:
        values = item.values
        summaries.append(
            _summary(
                kind="thread",
                kind_label="关系线程",
                entity_id=item.thread_id,
                title=_label(values.kind, _STATUS_LABELS),
                detail=(thread_reasons or {}).get(item.thread_id),
                status=values.status,
                occurred_at=item.updated_at,
                privacy_class=values.privacy_class,
                values=(
                    _value("importance_bp", "重要度", values.importance_bp),
                    _value("description_status", "事项说明", "read" if (thread_reasons or {}).get(item.thread_id) else "unavailable",
                           "已读取角色留下此事项的说明" if (thread_reasons or {}).get(item.thread_id) else "未找到可核对的事项说明"),
                    *((_value("due_at", "待处理时间", values.due_window.closes_at.isoformat(), _clock_label(values.due_window.closes_at)),) if values.due_window else ()),
                    *((_value("expires_at", "有效期至", values.expires_at.isoformat(), _clock_label(values.expires_at)),) if values.expires_at else ()),
                ),
            )
        )
    for item in projection.commitments:
        values = item.values
        summaries.append(
            _summary(
                kind="commitment",
                kind_label="承诺",
                entity_id=item.commitment_id,
                title="承诺",
                status=values.status,
                occurred_at=item.updated_at,
                privacy_class=values.privacy_class,
                values=(_value("importance_bp", "重要度", values.importance_bp),),
            )
        )
    for item in projection.interaction_bids:
        summaries.append(
            _summary(
                kind="interaction_bid",
                kind_label="互动期待",
                entity_id=item.bid_id,
                title=_short_text(item.goal) or "互动期待",
                status=item.status,
                detail=_short_text(item.hoped_response),
                occurred_at=item.opened_at,
                values=(_bp_value("pressure_bp", "在意", item.pressure_bp),),
            )
        )
    return tuple(summaries)


def _operations_summaries(
    projection: LedgerProjection,
) -> tuple[DashboardEntitySummary, ...]:
    summaries: list[DashboardEntitySummary] = []
    for item in projection.actions:
        summaries.append(
            _summary(
                kind="action",
                kind_label="行动",
                entity_id=item.action_id,
                title=_label(item.kind, _STATUS_LABELS),
                status=item.state,
                occurred_at=item.created_at,
                values=(
                    _value(
                        "layer",
                        "是不是发出去了",
                        item.layer,
                        "已经对着你" if item.layer == "external_action" else _label(item.layer, {}),
                    ),
                    _value("dispatch_pending", "还在等发送", item.dispatch_pending),
                ),
            )
        )
    for item in projection.read_only_tool_requests:
        summaries.append(
            _summary(
                kind="tool_request",
                kind_label="只读工具请求",
                entity_id=item.request_id,
                title=item.tool_name,
            )
        )
    for item in projection.tool_results:
        summaries.append(
            _summary(
                kind="tool_result",
                kind_label="工具结果",
                entity_id=item.result_id,
                title="工具结果已接受",
                occurred_at=item.accepted_at,
            )
        )
    for item in projection.execution_receipts:
        _failure_code, failure_label = _dashboard_failure(
            item.error_class,
            family="receipt",
        )
        summaries.append(
            _summary(
                kind="execution_receipt",
                kind_label="执行回执",
                entity_id=item.receipt_id,
                title=_label(item.receipt_kind, _STATUS_LABELS),
                status=item.observed_state,
                detail=failure_label if item.error_class is not None else None,
                occurred_at=item.received_at,
                values=(_value("is_terminal", "终态", item.is_terminal),),
            )
        )
    for item in projection.budget_accounts:
        summaries.append(
            _summary(
                kind="budget_account",
                kind_label="预算账户",
                entity_id=item.account_id,
                title=_label(item.category, {}),
                status="failed" if item.overrun else "available",
                values=(
                    _value("limit", "限额", item.limit),
                    _value("reserved", "已预留", item.reserved),
                    _value("spent", "已使用", item.spent),
                    _value("overrun", "超额", item.overrun),
                ),
            )
        )
    for item in projection.budget_reservations:
        summaries.append(
            _summary(
                kind="budget_reservation",
                kind_label="预算预留",
                entity_id=item.reservation_id,
                title=_label(item.category, {}),
                status=item.state,
                values=(
                    _value("amount_limit", "预留上限", item.amount_limit),
                    _value("settled_cost", "已结算成本", item.settled_cost),
                ),
            )
        )
    for item in projection.budget_settlements:
        summaries.append(
            _summary(
                kind="budget_settlement",
                kind_label="预算结算",
                entity_id=item.settlement_id,
                title=_label(item.settlement_kind, {}),
                status=item.state,
                values=(
                    _value("previous_cost", "原成本", item.previous_cost),
                    _value("cost_actual", "实际成本", item.cost_actual),
                    _value("cost_delta", "成本变化", item.cost_delta),
                ),
            )
        )
    for item in projection.reconciliations:
        summaries.append(
            _summary(
                kind="action_reconciliation",
                kind_label="行动对账",
                entity_id=item.reconciliation_id,
                title=_label(item.reason, {}),
                status=item.observed_state,
                values=(
                    _value(
                        "existing_state",
                        "原状态",
                        item.existing_state,
                        (
                            _label(item.existing_state, _STATUS_LABELS)
                            if item.existing_state is not None
                            else None
                        ),
                    ),
                ),
            )
        )
    if projection.life_ecology_schedule is not None:
        item = projection.life_ecology_schedule
        _failure_code, failure_label = _dashboard_failure(
            item.last_failure_code,
            family="life",
        )
        summaries.append(
            _summary(
                kind="life_ecology_schedule",
                kind_label="她自己的日子",
                entity_id=None,
                title="生活还在往下过" if item.last_failure_code is None else "生活这条线卡住了",
                status=("failed" if item.last_failure_code else "ready"),
                detail=failure_label if item.last_failure_code is not None else None,
                occurred_at=item.last_completed_at,
                values=(
                    _value("consecutive_failures", "连续卡住", item.consecutive_failures),
                ),
            )
        )
    for item in projection.contextual_life_retries:
        _failure_code, failure_label = _dashboard_failure(
            item.failure_code,
            family="life",
        )
        summaries.append(
            _summary(
                kind="life_retry",
                kind_label="生活来源重试",
                entity_id=item.source_event_ref,
                title=_label(item.lane, {}),
                status="failed",
                detail=failure_label,
                occurred_at=item.failed_at,
                values=(
                    _value("retry_ordinal", "重试序号", item.retry_ordinal),
                    _value(
                        "consecutive_technical_failures",
                        "连续技术失败",
                        item.consecutive_technical_failures,
                    ),
                ),
            )
        )
    from .dashboard_expression_reading import read_dashboard_expression, expectation_display_status, window_display_status
    for item in projection.expression_plans:
        reading = read_dashboard_expression(projection, item)
        summaries.append(
            _summary(
                kind="expression_plan",
                kind_label="表达计划",
                entity_id=item.plan_id,
                title="一组表达",
                status=item.state,
                detail=reading.text,
                occurred_at=reading.occurred_at,
                values=(
                    _value("expression_body_status", "正文状态", reading.status, {
                        "read": "已读取送达正文", "partial": "部分已送达正文",
                        "not_delivered": "尚未送达，不展示待发表正文",
                        "not_displayable": "受可见范围或载荷类型限制",
                        "unavailable": "缺少匹配的表达来源",
                    }[reading.status]),
                    _value("beat_count", "表达段数", reading.beat_count),
                    _value("delivered_count", "已送达段数", reading.delivered_count),
                    _value("shown_count", "可展示段数", reading.shown_count),
                ),
            )
        )
    for item in projection.expression_plan_manifests:
        expectation = item.response_expectation
        if expectation is not None:
            summaries.append(
                _summary(
                    kind="response_expectation",
                    kind_label="回应期待",
                    entity_id=item.plan_id,
                    title=_short_text(expectation.hoped_response) or "等你回一句",
                    status=expectation_display_status(projection, item),
                    occurred_at=expectation.not_before,
                    values=(
                        _value(
                            "not_before",
                            "她说等到",
                            expectation.not_before.isoformat(),
                            _clock_label(expectation.not_before),
                        ),
                        _value(
                            "expires_at",
                            "这份盼头看到",
                            expectation.expires_at.isoformat(),
                            _clock_label(expectation.expires_at),
                        ),
                        _bp_value("pressure_bp", "在意", expectation.pressure_bp),
                    ),
                )
            )
        leftover = item.revisit
        if leftover is not None:
            summaries.append(
                _summary(
                    kind="revisit_intention",
                    kind_label="再次考虑安排",
                    entity_id=item.plan_id,
                    title=_short_text(leftover.thought) or "还想再回来想这件事",
                    status=window_display_status(projection.logical_time, leftover),
                    occurred_at=leftover.not_before,
                    values=(
                        _value(
                            "not_before",
                            "她说再想",
                            leftover.not_before.isoformat(),
                            _clock_label(leftover.not_before),
                        ),
                        _value(
                            "expires_at",
                            "这份惦记看到",
                            leftover.expires_at.isoformat(),
                            _clock_label(leftover.expires_at),
                        ),
                    ),
                )
            )
    for item in projection.response_expectation_assessments:
        summaries.append(
            _summary(
                kind="expectation_assessment",
                kind_label="等到了没有",
                entity_id=item.assessment_id,
                title=_label(item.status, _STATUS_LABELS),
                status=item.status,
                detail=_short_text(item.reason),
                occurred_at=item.assessed_at,
            )
        )
    return tuple(summaries)


def _operation_notices(projection: LedgerProjection) -> tuple[DashboardNotice, ...]:
    notices: list[DashboardNotice] = []
    for item in projection.actions:
        if item.state != "failed":
            continue
        notices.append(
            DashboardNotice(
                kind="action_failure",
                label="行动失败",
                severity="error",
                reason_code="action_failed",
                reason_label="行动进入失败终态",
                occurred_at=item.created_at,
            )
        )
    for item in projection.execution_receipts:
        if item.error_class is None:
            continue
        failure_code, failure_label = _dashboard_failure(
            item.error_class,
            family="receipt",
        )
        notices.append(
            DashboardNotice(
                kind="receipt_failure",
                label="回执失败",
                severity="error",
                reason_code=failure_code,
                reason_label=failure_label,
                occurred_at=item.received_at,
            )
        )
    for _plan_id in projection.media_failed_plan_ids:
        notices.append(
            DashboardNotice(
                kind="media_failure",
                label="媒体计划失败",
                severity="warning",
                reason_code="media_plan_failed",
                reason_label="媒体计划未完成",
            )
        )
    for item in projection.contextual_life_retries:
        failure_code, failure_label = _dashboard_failure(
            item.failure_code,
            family="life",
        )
        notices.append(
            DashboardNotice(
                kind="life_retry",
                label="生活来源技术重试",
                severity="warning",
                reason_code=failure_code,
                reason_label=failure_label,
                occurred_at=item.failed_at,
            )
        )
    return tuple(notices[-_DETAIL_LIMIT:])


def _perception_media_summaries(
    projection: LedgerProjection,
) -> tuple[DashboardEntitySummary, ...]:
    summaries: list[DashboardEntitySummary] = []
    for item in projection.perception_requests:
        summaries.append(
            _summary(
                kind="perception_request",
                kind_label="感知请求",
                entity_id=item.request_id,
                title=_label(item.analysis_kind, {}),
                privacy_class=item.content_privacy_class,
            )
        )
    for item in projection.perception_results:
        summaries.append(
            _summary(
                kind="perception_result",
                kind_label="感知结果",
                entity_id=item.result_id,
                title=_label(item.analysis_kind, {}),
                occurred_at=item.accepted_at,
                privacy_class=item.content_privacy_class,
            )
        )
    for item in projection.external_signal_snapshots:
        summaries.append(
            _summary(
                kind="external_signal",
                kind_label="外部信号",
                entity_id=item.snapshot_ref,
                title=_short_text(item.headline) or "外部信号",
                detail=_short_text(item.licensed_summary),
                occurred_at=item.occurred_at or item.published_at or item.observed_at,
                values=(
                    _value("may_quote", "允许引用", item.may_quote),
                    _value(
                        "may_expose_to_character_model",
                        "可供角色模型查看",
                        item.may_expose_to_character_model,
                    ),
                ),
            )
        )
    for item in projection.external_perceptions:
        summaries.append(
            _summary(
                kind="external_perception",
                kind_label="已注意的外部感知",
                entity_id=item.perception_id,
                title=_label(item.channel, {}),
                detail=_short_text(item.subjective_summary),
                occurred_at=item.encountered_world_time,
                privacy_class=item.privacy_class,
            )
        )
    for item in projection.photo_candidates:
        summaries.append(
            _summary(
                kind="photo_candidate",
                kind_label="照片候选",
                entity_id=item.candidate_id,
                title=_label(item.family, {}),
                status=item.status,
                occurred_at=item.opened_at,
                privacy_class=item.privacy_ceiling,
            )
        )
    for item in projection.media_opportunities:
        summaries.append(
            _summary(
                kind="media_opportunity",
                kind_label="媒体机会",
                entity_id=item.opportunity_id,
                title=_label(item.family, {}),
                status=item.delivery_mode,
                occurred_at=item.ecology_observed_at,
                privacy_class=item.privacy_ceiling,
            )
        )
    for item in projection.media_plans:
        summaries.append(
            _summary(
                kind="media_plan",
                kind_label="媒体计划",
                entity_id=item.plan_id,
                title=_label(item.family, {}),
                status=item.media_lane,
                occurred_at=item.frozen_at,
            )
        )
    for item in projection.media_inspections:
        _failure_code, failure_label = _dashboard_failure(
            item.reason_code,
            family="media",
        )
        summaries.append(
            _summary(
                kind="media_inspection",
                kind_label="媒体检查",
                entity_id=item.inspection_id,
                title="媒体检查",
                status="accepted" if item.passed else "rejected",
                detail=failure_label,
                values=(_value("repairable", "可修复", item.repairable),),
            )
        )
    for item in projection.media_previews:
        summaries.append(
            _summary(
                kind="media_preview",
                kind_label="媒体预览",
                entity_id=item.preview_id,
                title="媒体预览已生成",
                status=item.delivery_mode,
            )
        )
    for item in projection.media_deliveries:
        summaries.append(
            _summary(
                kind="media_delivery",
                kind_label="媒体送达",
                entity_id=item.delivery_id,
                title="媒体已送达",
                status="delivered",
            )
        )
    return tuple(summaries)


def _authority_privacy_summaries(
    projection: LedgerProjection,
) -> tuple[DashboardEntitySummary, ...]:
    summaries: list[DashboardEntitySummary] = []
    for item in projection.actor_authorities:
        summaries.append(
            _summary(
                kind="actor_authority",
                kind_label="角色权限",
                entity_id=item.authority_id,
                title=_label(item.values.principal_kind, {}),
                status=item.values.status,
                occurred_at=item.updated_at,
                values=(
                    _value(
                        "allowed_operation_count",
                        "允许操作",
                        len(item.values.allowed_operations),
                    ),
                ),
            )
        )
    for item in projection.capability_grants:
        summaries.append(
            _summary(
                kind="capability_grant",
                kind_label="能力授权",
                entity_id=item.grant_id,
                title=_label(item.values.capability_kind, {}),
                status=item.values.state,
                occurred_at=item.updated_at,
                values=(
                    _value(
                        "target_scope_count",
                        "目标范围",
                        len(item.values.target_scope_refs),
                    ),
                ),
            )
        )
    for item in projection.consent_grants:
        summaries.append(
            _summary(
                kind="consent_grant",
                kind_label="同意授权",
                entity_id=item.consent_id,
                title="同意授权",
                status=item.values.status,
                occurred_at=item.updated_at,
                values=(
                    _value("action_scope_count", "行动范围", len(item.values.action_scope_refs)),
                    _value("data_scope_count", "数据范围", len(item.values.data_scope_refs)),
                    _value("revocable", "可撤销", item.values.revocable),
                ),
            )
        )
    for item in projection.privacy_policies:
        summaries.append(
            _summary(
                kind="privacy_policy",
                kind_label="隐私策略",
                entity_id=item.policy_id,
                title="隐私策略",
                status=item.values.status,
                occurred_at=item.updated_at,
                values=(
                    _value("data_class_count", "数据类别", len(item.values.data_class_refs)),
                    _value("viewer_rule_count", "查看规则", len(item.values.viewer_rule_refs)),
                    _value("media_rule_count", "媒体规则", len(item.values.media_rule_refs)),
                ),
            )
        )
    for item in projection.provider_media_grants:
        summaries.append(
            _summary(
                kind="provider_media_grant",
                kind_label="媒体供应授权",
                entity_id=item.grant_id,
                title=_label(item.capability_kind, {}),
                status="active",
                occurred_at=item.issued_at,
            )
        )
    return tuple(summaries)


def _relationship_terminal_candidates(
    projection: LedgerProjection,
) -> tuple[tuple[ProposalAuditProjection, TypedChange], ...]:
    candidates: list[tuple[ProposalAuditProjection, TypedChange]] = []
    for audit in projection.proposal_audits:
        try:
            proposal = validate_proposal_envelope(json.loads(audit.proposal_json))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("dashboard encountered an invalid proposal audit") from exc
        if not isinstance(proposal, DecisionProposal):
            continue
        candidates.extend(
            (audit, change)
            for change in proposal.proposed_changes
            if change.kind == "relationship_commitment" and change.transition == "commit"
        )
    return tuple(candidates)


def _runtime_state_values(
    observation: DashboardRuntimeObservation,
) -> dict[DashboardRuntimeSignalKey, DashboardRuntimeState]:
    return {
        "scheduler": observation.scheduler_state,
        "character_interior": observation.character_interior_state,
        "local_provider_capacity": observation.local_provider_capacity_state,
        "text_endpoint": observation.text_endpoint_state,
        "proactive_source_authority": observation.proactive_source_authority_state,
        "life_source_authority": observation.life_source_authority_state,
        "external_perception_upstream": observation.external_perception_upstream_state,
        "model_usage_budget": observation.model_usage_budget_state,
        "process_latency": observation.process_latency_state,
        "storage": observation.storage_state,
        "expression_episode": observation.expression_episode_state,
        "semantic_recall": observation.semantic_recall_state,
    }


def _companion_actor_ref(projection: LedgerProjection) -> str | None:
    if projection.character_core is not None:
        return projection.character_core.actor_ref
    companion = "actor:companion"
    known_actors = {
        *(item.actor_ref for item in projection.locations),
        *(item.actor_ref for item in projection.attentions),
        *(item.owner_actor_ref for item in projection.plans if item.owner_actor_ref),
    }
    return companion if companion in known_actors else None


def _summary(
    *,
    kind: str,
    kind_label: str,
    entity_id: str | None,
    title: str,
    status: str | None = None,
    status_mapping: Mapping[str, str] = _STATUS_LABELS,
    detail: str | None = None,
    privacy_class: str | None = None,
    occurred_at: datetime | None = None,
    values: tuple[DashboardLabeledValue, ...] = (),
) -> DashboardEntitySummary:
    # Internal entity identities may embed authority hashes or private refs.
    # The owner renderer gets typed state and labels, never those identifiers.
    del entity_id
    withheld = privacy_class == "withhold"
    return DashboardEntitySummary(
        kind=kind,
        kind_label=kind_label,
        title=kind_label if withheld else (_short_text(title) or kind_label),
        status_code=None if withheld else status,
        status_label=(
            None
            if withheld or status is None
            else _label(status, status_mapping)
        ),
        detail=None if withheld else _short_text(detail),
        privacy_class=privacy_class,
        occurred_at=occurred_at,
        values=() if withheld else values,
    )


def _value(
    key: str,
    label: str,
    value: str | int | bool | None,
    value_label: str | None = None,
) -> DashboardLabeledValue:
    return DashboardLabeledValue(
        key=key,
        label=label,
        value=value,
        value_label=value_label,
    )


def _bp_value(key: str, label: str, value: int) -> DashboardLabeledValue:
    return _value(key, label, value, f"{round(value / 100)}%")


def _clock_label(value: datetime | None) -> str | None:
    if value is None:
        return None
    local = value.astimezone(ZoneInfo("Asia/Shanghai"))
    return f"{local.month}月{local.day}日 {local.hour:02d}:{local.minute:02d}"


def _plan_display_status(item: object, logical_time: datetime | None) -> str | None:
    status = getattr(item, "status", None)
    if not isinstance(status, str):
        return None
    window = getattr(item, "scheduled_window", None)
    closes_at = getattr(window, "closes_at", None) if window is not None else None
    if (
        status == "planned"
        and isinstance(closes_at, datetime)
        and logical_time is not None
        and logical_time >= closes_at
    ):
        return "window_missed"
    return status


def _activity_title(activity_kind: str | None) -> str:
    if not activity_kind:
        return "一件还没说明的事"
    labeled = _ACTIVITY_LABELS.get(activity_kind)
    if labeled:
        return labeled
    if activity_kind.startswith("open_life."):
        return "自主活动"
    if activity_kind.startswith("npc_initiative."):
        return "人物相关活动"
    return "一项活动"


def _place_title(location_ref: str | None) -> str:
    if not location_ref:
        return "位置还没具体到哪"
    return _LOCATION_LABELS.get(location_ref, "一个记下的地方")


def _short_text(value: object, *, limit: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def _label(value: str | None, catalog: Mapping[str, str]) -> str:
    if value is None:
        return "未知"
    return catalog.get(value, value)


def _dashboard_failure(
    value: object,
    *,
    family: Literal["receipt", "life", "media"],
) -> tuple[str, str]:
    """Reduce open diagnostic strings to a closed, renderer-safe category."""

    if isinstance(value, str) and value in _FAILURE_LABELS:
        return value, _FAILURE_LABELS[value]
    normalized = value.lower() if isinstance(value, str) else ""
    categories: tuple[tuple[tuple[str, ...], str], ...] = (
        (("render_", "render:"), "media_render_failure"),
        (("inspection_", "inspection:"), "media_inspection_failure"),
        (("activity_lifecycle.",), "activity_lifecycle_failure"),
        (("memory.",), "memory_failure"),
        (("aftermath.",), "aftermath_failure"),
        (("life_development.",), "life_development_failure"),
        (("npc_ecology.",), "npc_ecology_failure"),
        (("media.",), "life_media_failure"),
        (("primary_timeout", "main_timeout", "provider_timeout", "timeout"), "provider_timeout"),
        (("provider_", "provider:", "model:", "main_", "backup_"), "provider_failure"),
    )
    for prefixes, code in categories:
        if normalized.startswith(prefixes):
            return code, _FAILURE_LABELS[code]
    fallback = {
        "receipt": "unclassified_receipt_failure",
        "life": "unclassified_life_failure",
        "media": "media_inspection_failure",
    }[family]
    return fallback, _FAILURE_LABELS[fallback]


def _bounded_summaries(
    summaries: Iterable[DashboardEntitySummary],
) -> tuple[DashboardEntitySummary, ...]:
    grouped: dict[str, list[DashboardEntitySummary]] = {}
    for item in summaries:
        grouped.setdefault(item.kind, []).append(item)

    def _latest(items: Sequence[DashboardEntitySummary], limit: int) -> list[DashboardEntitySummary]:
        ordered = sorted(
            items,
            key=lambda item: (
                item.occurred_at or datetime.min.replace(tzinfo=UTC),
                item.kind,
                item.title,
            ),
        )
        return list(ordered[-limit:]) if limit > 0 else []

    selected: list[DashboardEntitySummary] = []
    now_kinds = set(_NOW_KINDS)
    for kind in _NOW_KINDS:
        selected.extend(_latest(grouped.pop(kind, ()), _KIND_KEEP.get(kind, _KIND_KEEP_DEFAULT)))
    for kind, items in grouped.items():
        selected.extend(_latest(items, _KIND_KEEP.get(kind, _KIND_KEEP_DEFAULT)))
    if len(selected) <= _DETAIL_LIMIT:
        return tuple(selected)
    now_items = [item for item in selected if item.kind in now_kinds]
    other_items = [item for item in selected if item.kind not in now_kinds]
    room = max(0, _DETAIL_LIMIT - len(now_items))
    return tuple((*now_items, *other_items[-room:]))


def _state_from_metrics(metrics: Sequence[DashboardMetric]) -> Literal["ready", "empty"]:
    return "ready" if any(item.count for item in metrics) else "empty"


def _coverage(known_count: int, included_count: int) -> DashboardCoverage:
    return DashboardCoverage(
        known_count=known_count,
        included_count=included_count,
        truncated=included_count < known_count,
    )


def _cursor_after(candidate: ProjectionCursor, captured: ProjectionCursor) -> bool:
    return (
        candidate.world_revision,
        candidate.deliberation_revision,
        candidate.ledger_sequence,
    ) > (
        captured.world_revision,
        captured.deliberation_revision,
        captured.ledger_sequence,
    )


def _json_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _snapshot_hash(payload_without_hash: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload_without_hash,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "DASHBOARD_LEDGER_FIELD_POLICY",
    "DashboardAuthorityPrivacyData",
    "DashboardAuthorityPrivacySection",
    "DashboardCoverage",
    "DashboardEntitySummary",
    "DashboardFactsMemoryInnerData",
    "DashboardFactsMemoryInnerSection",
    "DashboardHomeSections",
    "DashboardHomeSnapshot",
    "DashboardHomeSnapshotModule",
    "DashboardLedgerFieldPolicy",
    "DashboardLedgerQualificationData",
    "DashboardLedgerQualificationSection",
    "DashboardMetric",
    "DashboardOperationsData",
    "DashboardOperationsSection",
    "DashboardOwnerIdentity",
    "DashboardPerceptionMediaData",
    "DashboardPerceptionMediaSection",
    "DashboardRelationshipLifecycleData",
    "DashboardRelationshipLifecycleSection",
    "DashboardRoomRenderState",
    "DashboardRoomSection",
    "DashboardRuntimeObservation",
    "DashboardRuntimeOperationsSection",
    "DashboardRuntimeReason",
    "DashboardTypedChangeTerminal",
]
