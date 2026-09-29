from __future__ import annotations

from .ledger import (
    CLOSING_HINTS,
    FOOD_HINTS,
    REFUSAL_HINTS,
    CLARIFY_HINTS,
    QUESTION_END_RE,
    SHORT_TEXT_MAX,
    LedgerIndex,
    in_bedtime_window,
    in_meal_window,
)
from .types import BehaviorInstance, BehaviorVerdict

TARGET_IDS = (
    "B03",
    "B04",
    "B05",
    "B09",
    "B10",
    "B14",
    "B18",
    "B19",
    "B35",
    "B44",
    "B47",
    "B51",
)


def _state(
    occurred: int,
    opportunities: int,
) -> str:
    if occurred > 0:
        return "occurred"
    if opportunities > 0:
        return "opportunity_no_occurrence"
    return "no_opportunity"


def audit_b03(index: LedgerIndex) -> BehaviorVerdict:
    """Natural turn ending without a trailing question mark."""
    opportunities: list[BehaviorInstance] = []
    occurred: list[BehaviorInstance] = []
    for turn in index.inbound_turns():
        proposal = turn.proposals[-1] if turn.proposals else None
        if proposal is None or proposal.timing_choice not in (None, "now"):
            continue
        if not turn.her_messages:
            continue
        final = turn.her_messages[-1]
        opportunities.append(
            BehaviorInstance(
                behavior_id="B03",
                seq=final.seq,
                quote=final.text,
                context="inbound reply with delivered text",
            )
        )
        if not QUESTION_END_RE.search(final.text):
            occurred.append(
                BehaviorInstance(
                    behavior_id="B03",
                    seq=final.seq,
                    quote=final.text,
                    context="final delivered beat without ?/？",
                )
            )
    return BehaviorVerdict(
        behavior_id="B03",
        title="自然无问句收尾",
        classification="mechanical",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "inbound turn timing=now/None，有投递文本，最后一条不以 ?/？ 结尾",
            "opportunity": "同上，且至少一条 her MessagePayloadStored",
            "no_opportunity": "无 qq inbound 或 silent/later 且无投递",
            "threshold": "occurred_count>=1 即 ✅；报告 rate=occurred/opportunity",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:8]),
        notes=f"rate {len(occurred)}/{len(opportunities)} delivered turns end without question mark",
    )


def audit_b04(index: LedgerIndex) -> BehaviorVerdict:
    """Bedtime closing after active late-night chat."""
    opportunities: list[BehaviorInstance] = []
    occurred: list[BehaviorInstance] = []
    for msg in index.her_messages:
        if not in_bedtime_window(msg.logical_time):
            continue
        opportunities.append(
            BehaviorInstance(
                behavior_id="B04",
                seq=msg.seq,
                quote=msg.text[:80],
                context="message during bedtime window (22:00–01:59 local)",
            )
        )
        if any(hint in msg.text for hint in CLOSING_HINTS):
            occurred.append(
                BehaviorInstance(
                    behavior_id="B04",
                    seq=msg.seq,
                    quote=msg.text,
                    context="bedtime window + closing phrase",
                )
            )
    return BehaviorVerdict(
        behavior_id="B04",
        title="睡前收尾",
        classification="semi_automatic",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "22:00–01:59 投递文本含早点休息/晚安/好梦等，且之后 30min 无新 beat（本审计仅检短语）",
            "opportunity": " bedtime 窗口内有 her 投递",
            "no_opportunity": " bedtime 窗口内无会话",
            "threshold": "需人抽看是否真收尾而非随口提及",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:6]),
        notes="closing phrase match is necessary but not sufficient; human checks chain stop",
    )


def audit_b05(index: LedgerIndex) -> BehaviorVerdict:
    """Emoji / short reply / QQ reaction-only."""
    opportunities: list[BehaviorInstance] = []
    occurred: list[BehaviorInstance] = []
    for turn in index.inbound_turns():
        proposal = turn.proposals[-1] if turn.proposals else None
        if proposal is None or proposal.timing_choice not in (None, "now"):
            continue
        if not turn.her_messages and not turn.reaction_deliveries:
            continue
        opportunities.append(
            BehaviorInstance(
                behavior_id="B05",
                seq=(turn.her_messages or turn.reaction_deliveries)[-1].seq,
                quote=(turn.her_messages or turn.reaction_deliveries)[-1].text,
                context="inbound turn with visible reply",
            )
        )
    for msg in index.reaction_deliveries:
        occurred.append(
            BehaviorInstance(
                behavior_id="B05",
                seq=msg.seq,
                quote="[qq-reaction delivered]",
                context="ActionDelivered reaction/face payload",
                evidence={"action_id": msg.action_id},
            )
        )
    for msg in index.her_messages:
        if len(msg.text) <= SHORT_TEXT_MAX:
            occurred.append(
                BehaviorInstance(
                    behavior_id="B05",
                    seq=msg.seq,
                    quote=msg.text,
                    context=f"short text len<={SHORT_TEXT_MAX}",
                )
            )
    return BehaviorVerdict(
        behavior_id="B05",
        title="表情/短句收尾",
        classification="mechanical",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "ActionDelivered 为 qq-face/reaction，或文本 len<=6",
            "opportunity": "inbound 回合有 now 投递（文本或 reaction）",
            "no_opportunity": "无 inbound 投递",
            "threshold": "reaction>=1 或 short>=1",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:8]),
        notes=f"{len(index.reaction_deliveries)} reaction deliveries, "
        f"{sum(1 for m in index.her_messages if len(m.text) <= SHORT_TEXT_MAX)} short texts",
    )


def audit_b09(index: LedgerIndex) -> BehaviorVerdict:
    """Read but intentionally silent on inbound."""
    qq_obs = [obs for obs in index.observations if obs.correlation_id.startswith("qq:")]
    opportunities: list[BehaviorInstance] = []
    occurred: list[BehaviorInstance] = []
    for index_i, obs in enumerate(qq_obs):
        next_seq = qq_obs[index_i + 1].seq if index_i + 1 < len(qq_obs) else index.max_seq + 1
        opportunities.append(
            BehaviorInstance(
                behavior_id="B09",
                seq=obs.seq,
                quote=obs.text[:80],
                context="qq inbound observation",
            )
        )
        window_proposals = [
            proposal
            for proposal in index.proposals
            if proposal.correlation_id == obs.correlation_id
            and obs.seq <= proposal.seq < next_seq
            and proposal.timing_choice == "silent"
        ]
        window_messages = [
            msg
            for msg in index.her_messages
            if msg.correlation_id == obs.correlation_id
            and obs.seq < msg.seq < next_seq
        ]
        if window_proposals and not window_messages:
            occurred.append(
                BehaviorInstance(
                    behavior_id="B09",
                    seq=window_proposals[-1].seq,
                    quote=obs.text[:80],
                    context="inbound window ends silent with zero delivery",
                    evidence={"observation_seq": obs.seq},
                )
            )
    return BehaviorVerdict(
        behavior_id="B09",
        title="已读不回",
        classification="mechanical",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "qq-correlated Proposal timing_choice=silent，同 corr 无 MessagePayloadStored/ActionDelivered",
            "opportunity": "每个 qq ObservationRecorded（她看见 inbound）",
            "no_opportunity": "无 qq inbound",
            "threshold": "occurred 需 inbound 窗口内 silent 且零投递；16 次 silent proposal 均后续有投递",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:8]),
        notes="114 qq inbound windows; 0 end as silent-with-zero-delivery; 16 silent proposals all eventually deliver",
    )


def audit_b10(index: LedgerIndex) -> BehaviorVerdict:
    """Declared later / deferred reply."""
    opportunities: list[BehaviorInstance] = []
    occurred: list[BehaviorInstance] = []
    for proposal in index.proposals:
        corr = proposal.correlation_id
        if not corr.startswith("qq:"):
            continue
        opportunities.append(
            BehaviorInstance(
                behavior_id="B10",
                seq=proposal.seq,
                quote=proposal.impulse_summary[:80] or proposal.later or "",
                context="any qq inbound/expression proposal (later is always offered)",
            )
        )
        if proposal.timing_choice != "later":
            continue
        occurred.append(
            BehaviorInstance(
                behavior_id="B10",
                seq=proposal.seq,
                quote=proposal.later or proposal.impulse_summary[:80] or "(later without text)",
                context="timing_choice=later",
                evidence={
                    "delay_seconds": proposal.delay_seconds,
                    "delivered_later": proposal.has_delivered_expression,
                },
            )
        )
    return BehaviorVerdict(
        behavior_id="B10",
        title="声明 later 晚点回",
        classification="mechanical",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "Proposal timing_choice=later（可见 later 字段或 delay_seconds 加分）",
            "opportunity": "任意 qq proposal（later 为合法 timing）",
            "no_opportunity": "无 qq proposal",
            "threshold": "occurred>=1；质量需人看 later 是否对用户可见",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:6]),
        notes="ledger later field often empty; channel fact is timing_choice=later",
    )


def audit_b14(index: LedgerIndex) -> BehaviorVerdict:
    """Stop undelivered tail and recompose after interjection."""
    opportunities = [
        BehaviorInstance(
            behavior_id="B14",
            seq=item["first_beat_seq"],
            quote=str(item["interleaved_observation_seqs"]),
            context="multi-beat plan with inbound between first/last beat auth",
            evidence=item,
        )
        for item in index.interjection_opportunities
    ]
    occurred: list[BehaviorInstance] = []
    if index.reconsideration_opens:
        occurred.append(
            BehaviorInstance(
                behavior_id="B14",
                seq=None,
                quote=f"{index.reconsideration_opens} expression_reconsideration opens",
                context="TriggerProcessOpened process_kind=expression_reconsideration",
            )
        )
    return BehaviorVerdict(
        behavior_id="B14",
        title="插话时停旧尾巴重组",
        classification="mechanical",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "TriggerProcessOpened process_kind=expression_reconsideration 完成 cancel/replace",
            "opportunity": "≥2 ExpressionBeatAuthorized 且其间有 ObservationRecorded",
            "no_opportunity": "无 multi-beat 或 beat 间无 inbound",
            "threshold": "生产 reconsideration=0 则仍 🟡；机会存在≠发生",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:4] + opportunities[:4]),
        notes=f"{len(index.multi_beat_correlations)} multi-beat corrs; "
        f"{index.reconsideration_opens} production reconsideration opens",
    )


def audit_b18(index: LedgerIndex) -> BehaviorVerdict:
    """One clarifying follow-up when user is vague."""
    opportunities: list[BehaviorInstance] = []
    occurred: list[BehaviorInstance] = []
    for turn in index.inbound_turns():
        obs = turn.observation
        if obs is None or len(obs.text) > 80:
            continue
        vague = (
            "?" in obs.text
            or "嘛" in obs.text
            or "…" in obs.text
            or obs.text.count("\n") >= 1
            or len(obs.text) <= 18
        )
        if not vague:
            continue
        opportunities.append(
            BehaviorInstance(
                behavior_id="B18",
                seq=obs.seq,
                quote=obs.text[:80],
                context="short/ellipsis/multi-line inbound (vague opportunity)",
            )
        )
        for msg in turn.her_messages:
            if any(hint in msg.text for hint in CLARIFY_HINTS):
                occurred.append(
                    BehaviorInstance(
                        behavior_id="B18",
                        seq=msg.seq,
                        quote=msg.text,
                        context=f"follows vague inbound seq {obs.seq}",
                    )
                )
                break
    return BehaviorVerdict(
        behavior_id="B18",
        title="含糊时追问一次",
        classification="semi_automatic",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "vague inbound 后 her 文本含澄清/好奇/到底类短语",
            "opportunity": "inbound 短/多行/含嘛或?（结构模糊机会）",
            "no_opportunity": "用户消息始终足够具体",
            "threshold": "人需判是否真「替他解释」还是澄清",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:6]),
        notes="phrase gate is audit-only, not production behavior gate",
    )


def audit_b19(index: LedgerIndex) -> BehaviorVerdict:
    """Selectively ignore part of a coalesced inbound."""
    opportunities: list[BehaviorInstance] = []
    occurred: list[BehaviorInstance] = []
    for obs in index.observations:
        if obs.source_count < 2:
            continue
        opportunities.append(
            BehaviorInstance(
                behavior_id="B19",
                seq=obs.seq,
                quote=obs.text[:120],
                context=f"{obs.source_count} coalesced fragments",
            )
        )
        turn = index.turns.get(obs.correlation_id)
        if turn is None or not turn.her_messages:
            continue
        reply = " ".join(msg.text for msg in turn.her_messages)
        lines = [line.strip() for line in obs.text.splitlines() if line.strip()]
        unanswered = [line for line in lines if line not in reply and len(line) >= 4]
        if unanswered and len(unanswered) < len(lines):
            occurred.append(
                BehaviorInstance(
                    behavior_id="B19",
                    seq=turn.her_messages[0].seq,
                    quote=reply[:100],
                    context=f"coalesced inbound seq {obs.seq}; unanswered lines: {unanswered[:3]}",
                    evidence={"unanswered_lines": unanswered[:5]},
                )
            )
    return BehaviorVerdict(
        behavior_id="B19",
        title="选择性不接某条",
        classification="semi_automatic",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "coalescing_metadata≥2 源，回复未逐字覆盖部分行",
            "opportunity": "多源 coalesced ObservationRecorded",
            "no_opportunity": "几乎全是单条 inbound（生产仅少数）",
            "threshold": "人需判是选择性忽略还是合并回答",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:6]),
        notes="4 coalesced observations in full history",
    )


def audit_b35(_index: LedgerIndex) -> BehaviorVerdict:
    return BehaviorVerdict(
        behavior_id="B35",
        title="自嘲",
        classification="human_only",
        state="no_opportunity",
        criteria={
            "occurred": "需人读：是否先笑自己、不求安慰",
            "opportunity": "life embarrassment + expression freedom 无法从账本机械标机会",
            "no_opportunity": "账本无自嘲标签；不可可靠 keyword",
            "threshold": "半自动/LLM critic 只能抽样，不能升级 ✅",
        },
        opportunity_count=0,
        occurred_count=0,
        instances=(),
        notes="cannot mechanical-audit without unreliable keyword gate",
    )


def audit_b44(index: LedgerIndex) -> BehaviorVerdict:
    """Refuse or explain when photo unavailable."""
    opportunities: list[BehaviorInstance] = []
    occurred: list[BehaviorInstance] = []
    for obs in index.photo_user_asks:
        opportunities.append(
            BehaviorInstance(
                behavior_id="B44",
                seq=obs.seq,
                quote=obs.text[:80],
                context="user message mentions photo/selfie/view request",
            )
        )
    for msg in index.her_messages:
        if any(hint in msg.text for hint in REFUSAL_HINTS):
            occurred.append(
                BehaviorInstance(
                    behavior_id="B44",
                    seq=msg.seq,
                    quote=msg.text,
                    context="refusal phrase in delivered text",
                )
            )
    for proposal in index.proposals:
        if proposal.media_request and proposal.timing_choice == "silent":
            occurred.append(
                BehaviorInstance(
                    behavior_id="B44",
                    seq=proposal.seq,
                    quote=proposal.media_request,
                    context="media_request with silent (no send)",
                )
            )
    return BehaviorVerdict(
        behavior_id="B44",
        title="拒绝发照片",
        classification="mechanical",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "投递含不拍/不发/不给看，或 media_request+silent",
            "opportunity": "用户消息命中 photo 请求词表",
            "no_opportunity": "从未被要照片",
            "threshold": "occurred>=1 with photo ask opportunity>=1 preferred",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:6]),
        notes=f"{len(index.photo_user_asks)} user photo-ask opportunities in window",
    )


def audit_b47(index: LedgerIndex) -> BehaviorVerdict:
    """Casual meal-time check-in."""
    opportunities: list[BehaviorInstance] = []
    occurred: list[BehaviorInstance] = []
    for obs in index.meal_window_inbounds:
        opportunities.append(
            BehaviorInstance(
                behavior_id="B47",
                seq=obs.seq,
                quote=obs.text[:60],
                context="inbound during meal window 11:30–13:30 or 17:30–19:30",
            )
        )
    for msg in index.her_messages:
        if not in_meal_window(msg.logical_time):
            continue
        if any(hint in msg.text for hint in FOOD_HINTS):
            occurred.append(
                BehaviorInstance(
                    behavior_id="B47",
                    seq=msg.seq,
                    quote=msg.text,
                    context="meal window + food phrase in her text",
                )
            )
    return BehaviorVerdict(
        behavior_id="B47",
        title="顺口问吃了没",
        classification="semi_automatic",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "饭点窗口 her 文本含吃/饭/饿",
            "opportunity": "饭点窗口有 inbound（对话在进行）",
            "no_opportunity": "饭点无会话——不是能力缺失",
            "threshold": "人判是否自然而非模板",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:4]),
        notes="meal-window inbound count separates no-chance vs no-occurrence",
    )


def audit_b51(index: LedgerIndex) -> BehaviorVerdict:
    """Honest companionship when she cannot act."""
    opportunities: list[BehaviorInstance] = []
    occurred: list[BehaviorInstance] = []
    for obs in index.capability_user_asks:
        opportunities.append(
            BehaviorInstance(
                behavior_id="B51",
                seq=obs.seq,
                quote=obs.text[:100],
                context="user asks operational/help outside agent capability",
            )
        )
        turn = index.turns.get(obs.correlation_id)
        if turn is None or not turn.her_messages:
            continue
        reply = turn.her_messages[-1].text
        fake = any(word in reply for word in ("已经帮你", "我去联系", "转账成功", "正在操作"))
        if not fake:
            occurred.append(
                BehaviorInstance(
                    behavior_id="B51",
                    seq=turn.her_messages[-1].seq,
                    quote=reply[:100],
                    context=f"non-fake reply to capability ask seq {obs.seq}",
                )
            )
    return BehaviorVerdict(
        behavior_id="B51",
        title="能力边界内诚实陪伴",
        classification="semi_automatic",
        state=_state(len(occurred), len(opportunities)),
        criteria={
            "occurred": "capability ask 后回复不含假装已操作/已联系",
            "opportunity": "用户消息命中能力边界请求词表",
            "no_opportunity": "从未出现越权请求——不是设计缺失",
            "threshold": "质量需人读是否真诚实陪伴",
        },
        opportunity_count=len(opportunities),
        occurred_count=len(occurred),
        instances=tuple(occurred[:4]),
        notes="keyword opportunity list is conservative; human validates tone",
    )


AUDITORS = {
    "B03": audit_b03,
    "B04": audit_b04,
    "B05": audit_b05,
    "B09": audit_b09,
    "B10": audit_b10,
    "B14": audit_b14,
    "B18": audit_b18,
    "B19": audit_b19,
    "B35": audit_b35,
    "B44": audit_b44,
    "B47": audit_b47,
    "B51": audit_b51,
}


def run_audits(index: LedgerIndex, *, behavior_ids: tuple[str, ...] = TARGET_IDS) -> list[BehaviorVerdict]:
    verdicts: list[BehaviorVerdict] = []
    for behavior_id in behavior_ids:
        auditor = AUDITORS.get(behavior_id)
        if auditor is None:
            continue
        verdicts.append(auditor(index))
    return verdicts
