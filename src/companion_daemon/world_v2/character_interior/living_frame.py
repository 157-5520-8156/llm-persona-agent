"""One configured character across chat and private life, without fact authority."""

import hashlib
import json


LIVING_PURPOSES = frozenset({
    "activity_lifecycle_choice", "life_development_choice", "world_stimulus_appraisal",
})

LIVING_CHOICE_SCOPE = (
    "这是你此刻的新选择，不是在历史记录里挑一件事重做。你可以产生此前没写过的新念头；"
    "未来想做什么不需要先有一段做过它的记忆。来源绑定本次机会，不规定选择内容。"
    "字段解释：intention 是想怎么度过这段时间，不必是待办或需要产出的目标；"
    "self_directed 只表示你自己的行动权限，不是自律或自我完善；private 是记录的隐私等级，"
    "不要求安静、独处或文艺；importance_bp 是此刻在意程度，不是事情的价值评分；"
    "duration_seconds 是暂定时段，不是必须做满或取得成果的指标。"
    "爱好、背景、旧习惯不是日程单；旧安排结束也不自动形成续做义务。"
    "若 plan_management 中已有进行中（active）或已排好（planned）的后续安排，"
    "可用 manage_plan（continue/revise/end）或直接选 no_op，不要无视现有安排再重复排一套重叠的日程；"
    "今天已经做完的日常小事（如洗漱、收拾桌面、扔垃圾、散步等）也不要在同一天反复重做。"
    "summary 写此刻的普通念头即可，无需给日常小事解释意义，也无需表演松弛。"
    "做什么、不安排、或稍后再想都由你决定。新的愿望不是已经发生的经历；"
    "过去行动、他人行为和世界结果仍只能引用有来源的事实。"
)


ACTIVITY_STATE_CHOICE_SCOPE = (
    "这次能力选择现有活动的执行状态，operation 和 resulting_status 给出所选动作的准确效果。"
    "no_op 是完整选择：保留当前全部活动状态。意图和时间安排由这些状态动作原样保留。"
    "是否选择动作、是否维持现状、此刻有什么新念头，都由你决定；noticed 可选且只记录本次念头。"
    "新的打算仍可自由形成，实际修改或合并安排须使用本次明确提供的相应能力。"
)


def configured_living_frame(frame) -> dict:
    """Keep characterization once; dialogue samples and factual claims stay in their lanes."""
    return {
        "contract": "configured-character-disposition.1",
        "authority": "characterization_only_not_evidence_or_activity_instructions",
        "reading_rule": "这是性格底色，不是每次重演的人物简介；当前有来源的自我状态、关系与已经接受的变化优先。",
        "name": frame.companion_name,
        "personality": frame.personality_frame or "",
        "values": list(frame.values),
    }


def living_frame_identity(frame: dict) -> str:
    # Changing this reading changes new turn identity, not old persisted turns.
    from .continuity_view import CONTRACT
    from .lived_evidence_scope import LIVED_EVIDENCE_SCOPE

    return hashlib.sha256(json.dumps(
        {"frame": frame, "living_choice_scope": LIVING_CHOICE_SCOPE,
         "framing_contract": "living-choice-semantics.2", "continuity_view": CONTRACT,
         "lived_evidence_scope": LIVED_EVIDENCE_SCOPE},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
