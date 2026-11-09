"""Compile a read-only 3-tier memory hierarchy and provenance/evolution graph for visualization.

Aligns the companion's memory architecture with:
1. Conway's Self-Memory System (SMS) 3-Tier Hierarchy:
   - ``working_self``: Character Core + Stage-level understandings (living habits, interpersonal posture, inner realizations).
   - ``semantic_facts``: Bi-temporal world & counterpart facts (durable traits vs. point-in-time situations).
   - ``episodic_impressions``: Short-term private impressions, memory candidates, and lived experiences.
2. PGMem Provenance & Dynamic Evolution Graph:
   - ``support`` edges (⊕): Connect episodic/fact memory candidates to the stage understandings they ground.
   - ``evolution`` edges (⇝): Connect predecessor understandings to revised, merged, or released understandings.
"""

from __future__ import annotations

from datetime import datetime
from types import MappingProxyType
from typing import Literal, Mapping
from zoneinfo import ZoneInfo

from pydantic import Field

from .ledger import WorldLedger
from .life_content_store import ImmutableLifeContentStore
from .memory_understanding import MemoryUnderstandingReader, UnderstandingSnapshot
from .schema_core import FrozenModel
from .schemas import LedgerProjection, ProjectionCursor

_DEFAULT_LOCAL_TZ = ZoneInfo("Asia/Shanghai")

_TRANSIENT_FACT_PREDICATES = frozenset(
    {
        "situation.recent",
        "activity.current",
        "location.current",
        "schedule.commitment",
        "health.condition",
    }
)

_FACT_PREDICATE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "activity.current": "正在做的事",
        "health.condition": "身体情况",
        "interest.activity": "平时喜欢做的事",
        "location.current": "现在在哪",
        "location.home": "平时住哪",
        "location.hometown": "老家",
        "possession.item": "身边的东西",
        "preference.dislikes": "不喜欢什么",
        "preference.likes": "喜欢什么",
        "profile.display_name": "怎么称呼",
        "profile.education": "读书情况",
        "profile.occupation": "平时忙什么",
        "profile.timezone": "作息时区",
        "relationship.affiliation": "所在学校或地方",
        "relationship.person": "身边重要的人",
        "routine.habit": "日常习惯",
        "schedule.commitment": "记着的安排",
        "situation.recent": "最近的情况",
    }
)

_FACET_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "private_self": "阶段生活习惯与日常步调",
        "subjective_relationship": "待人脾气与关系分寸",
        "expression_stance": "说话心气与相处边界",
        "aspirations_conflicts": "近期悬着的心事与处境",
        "selective_memory": "常想起的旧事脉络",
        "emotional_continuity": "近期情绪底色",
        "appraisal_affect": "对处境的直觉体会",
        "autonomous_impulses": "自己想做的念头",
    }
)

_CUE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "anniversary": "纪念日",
        "commitment": "约定",
        "conflict": "疙瘩",
        "episodic_highlight": "难忘瞬间",
        "habit": "习惯",
        "identity": "自我认知",
        "milestone": "重要节点",
        "open_loop": "未完的事",
        "preference": "喜好",
        "relationship": "关系变化",
        "shared_joke": "共同默契",
        "vulnerability": "心事",
    }
)

_STATUS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "active": "当前生效",
        "superseded": "已演化更新",
        "released": "已翻篇放下",
        "withdrawn": "已撤回",
        "forgotten": "已淡忘",
        "archived": "已归档",
        "resolved": "已解开",
    }
)

MemoryTierId = Literal["working_self", "semantic_facts", "episodic_impressions"]
MemoryNodeKind = Literal[
    "character_core",
    "memory_understanding",
    "fact",
    "private_impression",
    "memory_candidate",
    "experience",
    "appraisal",
]
MemoryEdgeKind = Literal["support", "evolution", "fact_evidence", "impression_appraisal"]


def _format_occurred_age(occurred_at: datetime | None, logical_time: datetime | None) -> str | None:
    if occurred_at is None or logical_time is None:
        return None
    if occurred_at.tzinfo is None or logical_time.tzinfo is None:
        return None
    now_date = logical_time.astimezone(_DEFAULT_LOCAL_TZ).date()
    occ_date = occurred_at.astimezone(_DEFAULT_LOCAL_TZ).date()
    day_diff = (now_date - occ_date).days
    if day_diff <= 0:
        return "今天"
    if day_diff == 1:
        return "昨天"
    if day_diff < 30:
        return f"{day_diff}天前"
    return f"{day_diff // 30}个月前（{day_diff}天前）"


class DashboardMemoryNode(FrozenModel):
    node_id: str = Field(min_length=1, max_length=256)
    tier: MemoryTierId
    node_kind: MemoryNodeKind
    kind_label: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=512)
    summary: str | None = Field(default=None, max_length=4000)
    facet: str | None = Field(default=None, max_length=128)
    facet_label: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=64)
    status_label: str | None = Field(default=None, max_length=64)
    temporal_scope: (
        Literal[
            "durable_trait_or_identity",
            "point_in_time_situation",
            "stage_posture",
            "transient_impression",
            "episodic_event",
        ]
        | None
    ) = None
    occurred_at: datetime | None = None
    occurred_age: str | None = Field(default=None, max_length=64)
    confidence_bp: int | None = Field(default=None, ge=0, le=10000)
    strength_bp: int | None = Field(default=None, ge=0, le=10000)
    privacy_class: str | None = Field(default=None, max_length=64)


class DashboardMemoryEdge(FrozenModel):
    edge_id: str = Field(min_length=1, max_length=512)
    edge_kind: MemoryEdgeKind
    edge_label: str = Field(min_length=1, max_length=64)
    source_node_id: str = Field(min_length=1, max_length=256)
    target_node_id: str = Field(min_length=1, max_length=256)
    operation: str | None = Field(default=None, max_length=64)


class DashboardMemoryTierSummary(FrozenModel):
    tier_id: MemoryTierId
    title: str = Field(min_length=1, max_length=128)
    subtitle: str = Field(min_length=1, max_length=256)
    active_count: int = Field(default=0, ge=0)
    historical_count: int = Field(default=0, ge=0)
    node_ids: tuple[str, ...] = ()


class DashboardMemoryHierarchy(FrozenModel):
    contract: Literal["dashboard-memory-hierarchy.1"] = "dashboard-memory-hierarchy.1"
    world_id: str = Field(min_length=1, max_length=128)
    cursor: ProjectionCursor
    logical_time: datetime
    tiers: tuple[DashboardMemoryTierSummary, ...]
    nodes: tuple[DashboardMemoryNode, ...] = ()
    edges: tuple[DashboardMemoryEdge, ...] = ()
    pending_consolidation_count: int = Field(default=0, ge=0)


def compile_dashboard_memory_hierarchy(
    *,
    projection: LedgerProjection,
    cursor: ProjectionCursor | None = None,
    ledger: WorldLedger | None = None,
    life_content_store: ImmutableLifeContentStore | None = None,
    understanding_snapshot: UnderstandingSnapshot | None = None,
    actor_ref: str = "agent:companion",
) -> DashboardMemoryHierarchy:
    """Compile the 3-tier SMS memory hierarchy and PGMem provenance/evolution graph."""
    resolved_cursor = cursor or ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )
    logical_time = projection.logical_time
    if understanding_snapshot is None and ledger is not None and life_content_store is not None:
        understanding_snapshot = MemoryUnderstandingReader(
            ledger=ledger,
            content_store=life_content_store,
            actor_ref=actor_ref,
        ).read(projection)

    nodes: list[DashboardMemoryNode] = []
    edges: list[DashboardMemoryEdge] = []
    seen_node_ids: set[str] = set()
    seen_edge_ids: set[str] = set()

    def _add_node(node: DashboardMemoryNode) -> None:
        if node.node_id not in seen_node_ids:
            seen_node_ids.add(node.node_id)
            nodes.append(node)

    def _add_edge(edge: DashboardMemoryEdge) -> None:
        if edge.edge_id not in seen_edge_ids:
            seen_edge_ids.add(edge.edge_id)
            edges.append(edge)

    # 1. Tier 1: Working Self (Character Core + Stage Understandings)
    working_self_ids: list[str] = []
    working_self_active = 0
    working_self_historical = 0

    if projection.character_core is not None:
        core = projection.character_core
        _add_node(
            DashboardMemoryNode(
                node_id=core.core_id,
                tier="working_self",
                node_kind="character_core",
                kind_label="角色长期底色",
                title="角色核心设定与底色",
                status="active",
                status_label="当前生效",
                temporal_scope="durable_trait_or_identity",
                occurred_at=core.updated_at,
                occurred_age=_format_occurred_age(core.updated_at, logical_time),
                privacy_class=core.values.privacy_class,
            )
        )
        working_self_ids.append(core.core_id)
        working_self_active += 1

    if understanding_snapshot is not None:
        ordered_understandings = sorted(
            understanding_snapshot.recallable or understanding_snapshot.versions,
            key=lambda v: (v.read_status == "active", v.selected_at, v.version_ref),
            reverse=True,
        )
        for version in ordered_understandings:
            f_label = _FACET_LABELS.get(version.facet, version.facet or "阶段体悟")
            st_label = _STATUS_LABELS.get(version.read_status, version.read_status)
            short_title = (
                version.summary[:40] + ("…" if len(version.summary) > 40 else "")
                if version.summary
                else f_label
            )
            _add_node(
                DashboardMemoryNode(
                    node_id=version.version_ref,
                    tier="working_self",
                    node_kind="memory_understanding",
                    kind_label="阶段活法与心事",
                    title=f"{f_label}：{short_title}",
                    summary=version.summary,
                    facet=version.facet,
                    facet_label=f_label,
                    status=version.read_status,
                    status_label=st_label,
                    temporal_scope="stage_posture",
                    occurred_at=version.selected_at,
                    occurred_age=_format_occurred_age(version.selected_at, logical_time),
                    confidence_bp=version.confidence_bp,
                    privacy_class="private",
                )
            )
            working_self_ids.append(version.version_ref)
            if version.read_status == "active":
                working_self_active += 1
            else:
                working_self_historical += 1

            for pred_ref in version.predecessor_refs:
                op_label = {
                    "revise": "阶段修订 (⇝)",
                    "merge": "归并沉淀 (⇝)",
                    "release": "释怀放下 (⇝)",
                }.get(version.operation, "阶段演化 (⇝)")
                _add_edge(
                    DashboardMemoryEdge(
                        edge_id=f"evolution:{pred_ref}->{version.version_ref}",
                        edge_kind="evolution",
                        edge_label=op_label,
                        source_node_id=pred_ref,
                        target_node_id=version.version_ref,
                        operation=version.operation,
                    )
                )
            for cand in version.sources:
                _add_edge(
                    DashboardMemoryEdge(
                        edge_id=f"support:{cand.candidate_id}->{version.version_ref}",
                        edge_kind="support",
                        edge_label="记忆支撑 (⊕)",
                        source_node_id=cand.candidate_id,
                        target_node_id=version.version_ref,
                        operation=version.operation,
                    )
                )

    # 2. Tier 2: Semantic Facts (Bi-Temporal Durable vs. Point-in-Time Facts)
    semantic_fact_ids: list[str] = []
    semantic_active = 0
    semantic_historical = 0

    for fact in projection.facts:
        f_val = fact.values
        pred_label = _FACT_PREDICATE_LABELS.get(f_val.predicate_code, f_val.predicate_code)
        is_transient = f_val.predicate_code in _TRANSIENT_FACT_PREDICATES
        occ_at = fact.updated_at or fact.committed_at
        _add_node(
            DashboardMemoryNode(
                node_id=fact.fact_id,
                tier="semantic_facts",
                node_kind="fact",
                kind_label="阶段动态事实" if is_transient else "长期稳定事实",
                title=pred_label,
                facet=f_val.predicate_code,
                facet_label=pred_label,
                status=f_val.status,
                status_label=_STATUS_LABELS.get(f_val.status, f_val.status),
                temporal_scope=(
                    "point_in_time_situation" if is_transient else "durable_trait_or_identity"
                ),
                occurred_at=occ_at,
                occurred_age=_format_occurred_age(occ_at, logical_time),
                confidence_bp=f_val.confidence_bp,
                privacy_class=f_val.privacy_class,
            )
        )
        semantic_fact_ids.append(fact.fact_id)
        if f_val.status == "active":
            semantic_active += 1
        else:
            semantic_historical += 1

    # 3. Tier 3: Episodic & Impressions (Private Impressions, Memory Candidates, Experiences)
    episodic_ids: list[str] = []
    episodic_active = 0
    episodic_historical = 0

    for imp in projection.private_impressions:
        imp_val = getattr(imp, "values", imp)
        occ_at = (
            getattr(imp, "updated_at", None)
            or getattr(imp, "committed_at", None)
            or getattr(imp_val, "last_supported", None)
            or getattr(imp_val, "first_seen", None)
        )
        hold_or_expiry = (
            getattr(imp_val, "hold_reason", None)
            or getattr(imp_val, "expiry_condition", None)
            or "impression"
        )
        refl_summary = getattr(imp_val, "reflection_summary", None)
        imp_summary = (
            refl_summary.strip()[:3900]
            if isinstance(refl_summary, str) and refl_summary.strip()
            else None
        )
        imp_title = (
            imp_summary[:96] + ("…" if len(imp_summary) > 96 else "")
            if imp_summary
            else f"对 {imp_val.subject_ref} 的印象（{hold_or_expiry}）"
        )
        _add_node(
            DashboardMemoryNode(
                node_id=imp.impression_id,
                tier="episodic_impressions",
                node_kind="private_impression",
                kind_label="短期私人印象",
                title=imp_title,
                summary=imp_summary,
                status=imp_val.status,
                status_label=_STATUS_LABELS.get(imp_val.status, imp_val.status),
                temporal_scope="transient_impression",
                occurred_at=occ_at,
                occurred_age=_format_occurred_age(occ_at, logical_time),
                confidence_bp=imp_val.confidence_bp,
                privacy_class=getattr(imp_val, "privacy_class", "private"),
            )
        )
        episodic_ids.append(imp.impression_id)
        if imp_val.status == "active":
            episodic_active += 1
        else:
            episodic_historical += 1
        for interp_ref in getattr(imp_val, "interpretation_refs", ()):
            _add_edge(
                DashboardMemoryEdge(
                    edge_id=f"impression_appraisal:{interp_ref}->{imp.impression_id}",
                    edge_kind="impression_appraisal",
                    edge_label="评估触发",
                    source_node_id=interp_ref,
                    target_node_id=imp.impression_id,
                )
            )

    for cand in projection.memory_candidates:
        c_val = cand.values
        cue_label = _CUE_LABELS.get(c_val.cue_kind, c_val.cue_kind)
        _add_node(
            DashboardMemoryNode(
                node_id=cand.candidate_id,
                tier="episodic_impressions",
                node_kind="memory_candidate",
                kind_label="长期记忆候选",
                title=f"记忆候选：{cue_label}",
                facet=c_val.cue_kind,
                facet_label=cue_label,
                status=c_val.status,
                status_label=_STATUS_LABELS.get(c_val.status, c_val.status),
                temporal_scope="episodic_event",
                occurred_at=cand.updated_at,
                occurred_age=_format_occurred_age(cand.updated_at, logical_time),
                strength_bp=c_val.retrieval_strength_bp,
                privacy_class=c_val.privacy_ceiling,
            )
        )
        episodic_ids.append(cand.candidate_id)
        if c_val.status == "active":
            episodic_active += 1
        else:
            episodic_historical += 1
        for binding in getattr(c_val, "source_bindings", ()):
            src_id = getattr(binding, "source_id", None)
            if isinstance(src_id, str) and src_id:
                _add_edge(
                    DashboardMemoryEdge(
                        edge_id=f"fact_evidence:{src_id}->{cand.candidate_id}",
                        edge_kind="fact_evidence",
                        edge_label="来源证据",
                        source_node_id=src_id,
                        target_node_id=cand.candidate_id,
                    )
                )

    for exp in projection.experiences[-16:]:
        exp_val = getattr(exp, "values", exp)
        occ_at = getattr(exp_val, "occurred_to", None) or getattr(exp, "occurred_at", None)
        exp_summary: str | None = None
        for b in getattr(exp_val, "source_bindings", ()):
            resp_obj = getattr(b, "response", None)
            resp_txt = getattr(resp_obj, "response_text", None) if resp_obj is not None else None
            if isinstance(resp_txt, str) and resp_txt.strip():
                exp_summary = resp_txt.strip()[:3900]
                break
        exp_title = (
            exp_summary[:96] + ("…" if len(exp_summary) > 96 else "")
            if exp_summary
            else f"亲历片段（{exp.experience_id[:28]}）"
        )
        _add_node(
            DashboardMemoryNode(
                node_id=exp.experience_id,
                tier="episodic_impressions",
                node_kind="experience",
                kind_label="亲历片段",
                title=exp_title,
                summary=exp_summary,
                status=getattr(exp, "status", "active"),
                status_label="已记录",
                temporal_scope="episodic_event",
                occurred_at=occ_at,
                occurred_age=_format_occurred_age(occ_at, logical_time),
                privacy_class=getattr(exp_val, "privacy_class", None),
            )
        )
        episodic_ids.append(exp.experience_id)
        episodic_active += 1

    tiers = (
        DashboardMemoryTierSummary(
            tier_id="working_self",
            title="阶段自我与活法层（Working Self）",
            subtitle="由近期经历沉淀出的阶段生活习惯、待人脾气边界与交心体悟",
            active_count=working_self_active,
            historical_count=working_self_historical,
            node_ids=tuple(working_self_ids),
        ),
        DashboardMemoryTierSummary(
            tier_id="semantic_facts",
            title="双时态语义事实层（Bi-Temporal Facts）",
            subtitle="区分长期稳定身份/偏好与某天当时的短期处境动态",
            active_count=semantic_active,
            historical_count=semantic_historical,
            node_ids=tuple(semantic_fact_ids),
        ),
        DashboardMemoryTierSummary(
            tier_id="episodic_impressions",
            title="情景经历与短期印象层（Episodic & Impressions）",
            subtitle="具体亲历片段、短期私人印象与待巩固的记忆候选",
            active_count=episodic_active,
            historical_count=episodic_historical,
            node_ids=tuple(episodic_ids),
        ),
    )

    return DashboardMemoryHierarchy(
        world_id=projection.world_id,
        cursor=resolved_cursor,
        logical_time=logical_time,
        tiers=tiers,
        nodes=tuple(nodes),
        edges=tuple(edges),
        pending_consolidation_count=(
            understanding_snapshot.pending_count if understanding_snapshot is not None else 0
        ),
    )
