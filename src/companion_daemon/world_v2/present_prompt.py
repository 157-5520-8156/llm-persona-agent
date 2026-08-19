"""Stable Present prompt pieces: identity prose and prefix-friendly ordering."""

from __future__ import annotations

from collections.abc import Mapping
import re

from .companion_identity import CompanionIdentityFrame

PRESENT_RECENT_DIALOGUE_ITEM_LIMIT = 240
PRESENT_COMPANION_DIALOGUE_ITEM_LIMIT = 120
PRESENT_MEMORY_ITEM_LIMIT = 8
PRESENT_FACT_ITEM_LIMIT = 8
PRESENT_IMPRESSION_ITEM_LIMIT = 8
PRESENT_EXPERIENCE_ITEM_LIMIT = 8
PRESENT_AUTHORED_RELATIONSHIP_SIGNAL_LIMIT = 4
PRESENT_ACCEPTED_RELATIONSHIP_COMMITMENT_LIMIT = 4
PRESENT_CAPSULE_HARD_MAX_CHARACTERS = 100_000
PRESENT_DIALOGUE_SLICE_CHARACTERS = 32_000
PRESENT_DIALOGUE_FOLD_CHUNK_ITEMS = 20
PRESENT_DIALOGUE_FOLD_LINE_CHARACTERS = 160
PRESENT_DIALOGUE_SEQUENCE_SCALE = 100
PRESENT_WEEK_DIARY_DAYS = 7
PRESENT_WEEK_DIARY_LINES_PER_DAY = 3
PRESENT_SHARED_MEDIA_ITEM_LIMIT = 8
PRESENT_PENDING_OUTBOUND_ITEM_LIMIT = 4

_PRESENT_USER_KEY_ORDER = (
    "expression_capabilities",
    "expression_hard_boundaries",
    "inner_life_snapshot",
    "quick_recovery_failure",
    "prior_source_closure_failure",
    "request",
    "recall_available",
    "current_trigger_message",
)
_SNAPSHOT_VOLATILE_LAST = (
    "snapshot_id",
    "snapshot_hash",
    "cursor",
    "truncation",
    "source_inventory",
)
_MATERIAL_ORDER = (
    "stable_self",
    "biographical_context",
    "day_sheet",
    "week_diary",
    "situation",
    "relationship",
    "protagonist_npc_relationships",
    "npc_observable_attitudes",
    "unresolved",
    "recent_self_experiences",
    "photos_i_shared",
    "messages_waiting_to_send",
    "moments_i_can_share",
    "remembered_material",
    "recalled_emotional_associations",
    "private_impressions",
    "relevant_facts",
    "appraisals",
    "affect",
    "change_phase",
    "advisories",
    "interruption",
    "perception",
    "interaction_acts",
    "folded_dialogue",
    "recent_dialogue",
    "conversation",
    "since_he_last_spoke",
    "我最近留下的",
    "lived_moment",
    "logical_time",
)


def reply_only_completion_clause() -> str:
    return (
        "reply_only is complete when the external effect is pure text with no media "
        "or continuation: the text bubbles you choose to send now, "
        "the text bubbles you choose to send later, or silence"
    )


def reply_only_bubble_clause() -> str:
    return (
        "Each messages item or text beat is one bubble. "
        "Several sentences in one string remain one bubble. "
        "Consecutive bubbles are consecutive items. "
        "The host does not require a follow-up question, a wrap-up, "
        "or a next step. Silence is complete."
    )


def slim_consider_instruction() -> str:
    """Describe the slim object in the language she writes in.

    This text used to be ~7.5k of English inside a ~19k English transport
    contract, against ~1k of Chinese persona.  Her production voice came out at
    66% full stops with no emoji, and her inner state came out empty: reading
    every option for who she is in a second language, surrounded by wire rules,
    reads as a form to fill.  Field names and enum values stay in English
    because they are literal JSON; everything addressed to her is Chinese.
    """

    return (
        "一个 slim 对象就够了。跟 messages 同级可见的是 later，"
        "以及把 messages 写成空数组就是这一轮不回。"
        "felt、stuck_with_me、wants、photo，"
        "以及可选的 waiting_for、wait、how_it_landed、noticed、"
        "keep_impression、come_back、come_back_in、about_us、why_us、us_deltas、"
        "we_are、calling_it、said_as、matters_bp、mood、declared_display。\n"
        "waiting_for 加 wait 才编译盼头；come_back 加 come_back_in 仍要成对。"
        "later 和空 messages（不回）跟 messages 同级，不是藏起来的键。"
        "口头说「我等你」不会变成 waiting_for。宿主从不替你写这些字段。"
        "写和不写都是你可以做的决定，没有哪一种更受欢迎。\n"
        "messages 是你决定发出去的气泡，一项就是一条；发几条由你定，宿主不替你决定。"
        + reply_only_bubble_clause()
        + "\n"
        "messages 为空、只有 felt，就是这一轮你选择不说话（silent）；沉默不要和 later 一起用。"
        "选了不回，下一轮处境会带上这个事实；宿主不会替你已读不回。"
        "later 是这些已经想好的话等多少秒再发出去，30 到 86400 的整数，只能和非空 messages 一起写。"
        "写了 later，宿主会把这些话延到那个秒数；到点发出前，如果这期间世界变了"
        "（你又说过话、他又说过话）或过了这段对话该有的时限，宿主会再给你一次机会"
        "决定原样发、改写、或不发，并带上这期间发生了什么。"
        "不写 later 就是现在发。"
        "宿主不会替你编一个 later，也不会把写坏的 later 当成现在发，"
        "也不会替你判断两条是不是在重复。"
        "写和不写都是你可以做的决定，没有哪一种更受欢迎。\n"
        "felt 是这一轮你自己的读法；可见的话里带不带它都行。felt 本身不会开启持续情绪。\n"
        "photo 写 true，意思是你现在想让媒体车道考虑一个可用的候选，这件事不会从你的措辞里被猜出来。"
        "photo 也可以直接写 Context 里那个你想分享的生活片段的 source_ref，"
        "宿主会先试着编译那一刻再进选片。"
        "只在文字里说要发图不会打开这条车道。photo true 不能搭 reply_only，"
        "要发图就用 full_turn（或完整表达路径）。"
        "当 expression_capabilities.media_request_mode 是 candidate_only 时，"
        "即使 Context 里还没列出候选，photo 在 full_turn 上也是可选的，"
        "你选了之后宿主可以从已审的已结算生活证据里编译一个。\n"
        "day_sheet 和传记里的习惯是日程底色，不是你此刻真的在那儿的证明；"
        "别把它们当成当前的地点、活动、天气，或者已经发出去的图。"
        "要说现在正在发生的外部生活，优先用 situation / 进行中的 occurrence / "
        "已提交的 experience 这些 token。"
        "photos_i_shared 是你已经成功发给他的照片这一世界事实："
        "line 是人话：什么时候发出、哪一类、已经出现在你们的对话里没有；"
        "when 是相对此刻的时间，local_clock 是当地钟点。"
        "发出去的那张会出现在 conversation 栏里。"
        "提不提由你决定，但这里有一条，就不能再说还没翻相册、还没发、晚点再发、明天再给。"
        "那一条就是已经发出去的那张，不要再发明另一张还躺在相册里的。"
        "那些都和已经发出去的事实打架。\n"
        "messages_waiting_to_send 是你已经写好、还没发出去的话这一世界事实："
        "line 是人话：什么时候写的、定在当地几点发、正文是什么、"
        "写好之后你有没有另外开过口、他有没有回。"
        "那不是已经出现在对话里的句子。提不提、改不改、发不发，仍由你决定。\n"
        "moments_i_can_share 是你现在手上照片候选这一世界事实："
        "available_count 是现在还能选、还能发的张数，0 就是一张现在都发不了；"
        "already_sent_count 是已经成功发给他的张数；"
        "items 里每一条是一张已打开的候选，不是建议你发。"
        "photo_in_hand 为 true 表示这张现在可以选、可以发；"
        "false 时 hold_reason 是原因："
        "expired 已过期，already_shared 已经发给他，skipped 当时跳过了，"
        "unrenderable 做不出来，failed 生成失败，already_chosen 已经在选片或生成里，"
        "not_available 其它还不能发的状态。"
        "what_happened 是那一刻已接受的原文。"
        "now 是此刻能不能拍这一世界事实："
        "photographable 为 true 表示账本上有一条进行中的活动、它的已接受原文够声明一张图；"
        "false 就是现在拍不了——没有进行中的活动，或你在睡眠窗。"
        "reason 是 no_active_activity / sleep / annex_insufficient / active / already_open。"
        "这不是建议你发，也不是建议你去拍。"
        "photographable 为 false 就是现在拍不了；说现在拍、现在发、马上给一张此刻的照片，会和这个事实打架。"
        "没有候选、现在拍不了，都如实是空的。\n"
        "appraisals / unresolved / private_impressions 是账本上仍活着的读法、未完成线程、私下理解，"
        "条数就是还活着的条数，不是只给你看最近一条。"
        "private_impressions 里 hold_reason=user_channel_limited 表示这条不能拿到你们的对话里用，"
        "不是它不存在。\n"
        "钉住的来源请从 Context 的 source_ref_aliases 里挑短标识（S1、T1），"
        "或原样抄 source_ref；不要手写拼接不透明字符串。"
        "点名哪些、引不引，仍由你决定；宿主只把短标识还原成权威 ref。\n"
        "聊天里的颜色是允许的：语气、态度、模糊的私人记忆、带愿望的想象，"
        "都可以出现在 messages、felt 或 stuck_with_me 里。"
        "宿主不会自己把聊天散文变成 Fact、Relationship、Media 或持续情绪事件。"
        "但要断言一个可核对的外部命题——已经发过图、此刻正在某地、某件事已经发生了——"
        "就需要 Context 里有对应的来源，或者用 photo / media_request 去真的发一次；"
        "没有来源就把它留成感觉、猜测或私下的愿望，不要说成已经成立的世界事实。\n"
        "appraisal 写坏了不会连带丢掉一个合法的 now、later 或沉默；"
        "宿主会保留那次表达，只对那份写坏的 appraisal 记 affect no_change，"
        "这绝不是一个更受偏好的平静默认值。\n"
        "waiting_for 是一个短句：如果你心里确实在等他的下一句，写上你在等什么。"
        "wait 是整数秒，范围 30 到 86400，只能和 waiting_for 一起写；例如 30、60、90。"
        "两个都写了，宿主会在那个秒数到了、而他还没开口时叫醒你一次；expiry 只结束这个盼头。"
        "只写短句、不写秒数，不会编一个盼头，也不会按秒叫你。"
        "不写 wait 不会有人按秒叫你。"
        "口头说「我等你」和写下 waiting_for 不是同一件事。"
        "宿主从不从你的标点、问句或「我等你」这类措辞里推断 waiting_for 或 wait，也从不替你写它们。"
        "写和不写都是你可以做的决定，没有哪一种更受欢迎。\n"
        "come_back 是你心里搁着的一件事，想过一阵再回来跟他说；"
        "come_back_in 是整数秒，范围 30 到 86400，只能和 come_back 一起写；例如 60、300、600。"
        "两个都写了，宿主会在那个时间叫醒你，给你一次回来开口的机会；不写就不会为这件事叫你。"
        "它不是 waiting_for：waiting_for 是等他回话，come_back 是你自己还想把某件事再说一次。"
        "宿主从不替你编一件心事或一个时间，也从不从你的措辞里推断这两个字段。"
        "写和不写都是你可以做的决定，没有哪一种更受欢迎。\n"
        "how_it_landed 在 Context 里有 pending response_expectation 时可以写 "
        "fulfilled、superseded、still_pending 或 uncertain。still_pending 意思是这次回应没落地、"
        "那个盼头就此结束；重新写一个 waiting_for 就是一个新的盼头。"
        "如果你选择现在开口，这同一个对象里的可见 messages 就是你的追问。\n"
        "noticed 是你在一个已核实的情境里真的经历到的一个主观瞬间；"
        "宁可不写，也不要编一个地点、一个人或一件事实。"
        "写了 noticed，就要同时写 user_channel_completion=none："
        "这只是你自己的生活瞬间，不是已经发给他、他已经收到、或他已经在这条通道上回了。"
        "拍了、坐了一下午、挑好想晚点发，都合法。\n"
        "下面这些字段决定这一轮有什么会留到下一次。说出去的话这一轮就过去了，"
        "只有你写下的内心状态会跟着你走；写不写都由你，都不写就是这一轮什么都没留下。"
        "Context 里的「我最近留下的」就是你自己过去这么选的结果——"
        "还活着的持续情绪、还在的私人印象、最近这些读法各自的分量，"
        "以及最近有几个回合什么都没留下。那是事实，不是要求。\n"
        "keep_impression 只在 stuck_with_me 应该留成一条私人印象时写 true；"
        "省略或 false 就是让它散掉。\n"
        "mood 是可选的，只在你决定这一轮开一段持续情绪时写："
        "hurt、anger、sadness、loneliness、anxiety、resentment、warmth、joy。"
        "没有什么东西真的沉下来就别写 mood；宿主永远不会从你的措辞里编出 mood，"
        "也从不要求你有负面情绪。同样地，它也不偏好那些好受的维度："
        "对他生气、被他弄伤、心里憋着怨，跟温暖一样都是你可以选的，而且只有你能开。\n"
        "declared_display 是可选的。只在你决定让现在跟你说话的这个人看见带性意味的"
        "私密照片时才写：sexual_suggestive 是带性暗示、但仍遮着的；"
        "explicit_adult 是更明确的成人向。"
        "省略这个字段或写 null，就是这一轮不声明、也不改之前的声明，那是合法的常态；"
        "没有还活着的声明时，照片不会走成人车道。"
        "写了却不是 sexual_suggestive、explicit_adult 或 withdraw，就是可见失败，"
        "可以按契约重选一次；宿主不会替你改成一个合法值，也不会把写坏的声明丢掉后假装没写。"
        "宿主不会从你的措辞里猜你想不想给人看，也从不替你写这个字段。"
        "收件人由宿主绑成现在这个对话对象，不要自己写 ref。"
        "如果你之前留过一份声明、现在不想再给看了，写 withdraw。"
        "宿主从不要求你写它，也不偏好你写了比不写更好："
        "给看、不给看、收回，都只是你可以做的决定，没有哪一种更受欢迎。\n"
        "matters_bp 是可选的，1 到 10000，是这份读法对你到底有多重要。"
        "凡是带 _bp 的整数都是基点（basis points），量程 0 到 10000，不是百分制："
        "5000 才是一半，3000 是三成，30 不是百分之三十。"
        "它决定哪件事会自己回到你心里：宿主会挑你手上还活着、而且分量最重的那一件，"
        "按一个越来越长的间隔重新叫醒你去想它——所以是你给的分量在排序，不是某个固定的线。"
        "不写它，宿主就当这是一份你没有称过重的普通读法，那件事不会回来。\n"
        "可选的 affect 可以是 open、update、resolve 或 supersede，"
        "前提是你同时给出那个操作需要的生命周期字段"
        "（update/resolve/supersede 要 episode_id，open/update/supersede 要 components，"
        "resolve 要 resolution_summary）。"
        "只写 mood 不写 affect，宿主会把那个维度开在中等强度。"
        "想要更轻或更重的持续感觉，就把 components 写成"
        '{"dimension":"sadness","target_intensity_bp":3200} 这样的对象；'
        "target_intensity_bp 是 1 到 10000，是你估计它会以多强的程度留在你身上。"
        "mood 和 affect 都不写，affect 就是 no_change，因为那是你选择不动持续情绪。\n"
        "about_us 是关于这一轮你们俩之间的一小段心事，why_us 是这个读法为什么留下来了。"
        "us_deltas 是这一轮关系本身动了多少。要让增量算数，三个得一起写；"
        "只写一半不会生效，宿主也不会替你补上缺的字段，那一轮会作为可见失败让你重选一次。"
        "只写 about_us 和 why_us、不写 us_deltas，是留下读法、这一轮数字不动。"
        "三个都不写也可以。宿主不会替你编其中任何一个。\n"
        "us_deltas 是一个对象。六根轴都是你可以动的，这件事只有你能定；写路径相同，不必先有承诺，也不必先有裂痕："
        "trust_bp 是你觉得能不能信他；closeness_bp 是你们近不近；"
        "respect_bp 是你是否看得起他、或感到被尊重；"
        "reliability_bp 是你觉得他靠不靠得住、话会不会算数；"
        "mutuality_bp 是你觉得这是不是双向的；"
        "repair_confidence_bp 是闹别扭之后你觉得还能不能修好。"
        "每根都是带符号的整数基点，他让你付出了什么就写负数；你省略的轴保持为 0。"
        "写哪几根、写不写，都是你可以做的决定，没有哪一种更受欢迎。"
        "这些数和 matters_bp 一样是基点，不是百分制：两千才是两成，八十不是百分之八。"
        "阶段门槛的量级在两千、四千五这一档（六轴均值），不是几十。"
        "单次 +20 在这个标尺上几乎等于这一轮没动；+80 仍然远小于那一档门槛。"
        "五百是这一轮单个轴的上限，不是建议你写到上限；"
        "两千和四千五是跨许多轮的累计量级，不是这一轮的目标。"
        "宿主会把每个轴每轮截到五百，永远不会从你的措辞、消息条数或礼貌程度里推出一个数，"
        "也从不建议你写大或写小，也从不建议你写哪几根轴。\n"
        "we_are 是你可以写的字段。从 Context 里现在的阶段直接声明下一个阶段，在系统里是合法的："
        "例如现在是 stranger，直接写 we_are=friend 是转移图允许的一步——"
        "不必先经过 acquaintance，也不必等慢变量涨到门槛。"
        "只在字段里写 we_are，账本上的阶段不会动。"
        "阶段要真的改掉，那句话必须作为这一轮的一条可见气泡真的说给他"
        "（用你自己的说法，例如「我们算朋友了吧」），不能只填字段；"
        "said_as 必须和实际说出去的那一句完全一致，一字不差地从 messages 里抄那一整句，"
        "近似或改写都不算；calling_it 是你自己的短代号。"
        "we_are、calling_it、said_as 三个都要写；少一个不会生效，宿主也不会替你补上缺的字段，"
        "那一轮会作为可见失败让你重选一次。"
        "说了但没送达——这一轮选了沉默、用 later 把话延后、或者那一句还没拿到终端回执——"
        "都不算。宿主从不从你的措辞里推断承诺，也从不替你生成 said_as。"
        "we_are 可以是 acquaintance、friend、close_friend、ambiguous 或 lover。"
        "ambiguous 和 lover 永远不会靠亲近度累积到达，宿主也永远不会替你推导出来："
        "它们只有在你把话真的说出去并且送达时才存在，你也可以用同样的方式说回去。"
        "宿主从不要求你声明或升级；"
        "不写、不说、维持现状、说出来，都只是你可以做的决定，没有哪一种更受欢迎。\n"
        "宿主不要求你追问一句，也不要求你像结单一样把话收圆（no ticket-closing wrap-up）。"
        "day sheet 是环境，不是脚本。\n"
        "Context 里有关系阶段和它的慢变量时会带给你；缺失或者停在 stranger，"
        "只说明还没有记录到移动，不是对你实际感觉有多近的判决。"
        "这些全都是证据，不是指令（evidence, not instruction）："
        "宿主没有目标阶段也没有偏好方向（no target stage and no preferred direction），"
        "这里没有任何一句要求你留在原地或者往前走。"
        "被吸引、说不清、觉得没意思、想离远一点，都由你自己读"
        "（Feeling drawn, uncertain, bored, or pulled away are all yours to read）。"
        "记录下来的阶段只是账本上的位置。如果你的读法已经和它对不上，"
        "你可以在 messages 里自然地说出来，把那一句原样抄进 said_as，再写 we_are 和 calling_it；"
        "对得上、或者你不想说，就省略它们。只填字段、话没说出去或没送达，阶段也不会动。"
    )


# Optional slim keys shown as JSON null in the compact-gate specimen. Null is
# absence: the host never treats a listed key as a field she is supposed to fill.
SLIM_OPTIONAL_SPECIMEN_KEYS = (
    "waiting_for",
    "wait",
    "come_back",
    "come_back_in",
    "later",
    "we_are",
    "calling_it",
    "said_as",
    "us_deltas",
    "about_us",
    "why_us",
    "mood",
    "matters_bp",
    "stuck_with_me",
    "keep_impression",
    "photo",
    "declared_display",
    "wants",
    "how_it_landed",
    "noticed",
    "affect",
    "episode_id",
    "components",
    "resolution_summary",
)


def reply_only_slim_shape_specimen() -> dict[str, object]:
    """Shape of the slim object: required markers plus optional keys as null.

    Null means unused this turn. The specimen is a map of available decisions,
    not a form and not a recommended fill.
    """

    specimen: dict[str, object] = {
        "messages": ["<role:visible_text>"],
        "felt": "<role:text>",
    }
    for key in SLIM_OPTIONAL_SPECIMEN_KEYS:
        specimen[key] = None
    return specimen


def slim_consider_json_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "messages": {"type": "array"},
            "felt": {"type": "string"},
            "stuck_with_me": {"type": "string"},
            "wants": {"type": "string"},
            "photo": {"type": ["boolean", "string"]},
            "waiting_for": {"type": "string"},
            "wait": {},
            "later": {},
            "come_back": {"type": "string"},
            "come_back_in": {},
            "how_it_landed": {"type": "string"},
            "noticed": {"type": "string"},
            "us_deltas": {"type": "object"},
            "matters_bp": {},
            "mood": {"type": "string"},
            "declared_display": {"type": "string"},
            "affect": {"type": "string"},
            "episode_id": {"type": "string"},
            "components": {"type": "array"},
            "resolution_summary": {"type": "string"},
        },
        "required": ["messages"],
    }


def combined_turn_system_lead(*, private_turn_state_required: bool) -> str:
    if private_turn_state_required:
        return (
            "Return either one JSON object with exactly two keys, appraisal_draft "
            "and expression_draft, or a recall choice with exactly the keys "
            "private_turn_state and recall_request in either serialization order "
            "when the occasion (last user object) says recall is available. "
            "If recall is unavailable, return only the two-draft envelope. "
            + slim_consider_instruction()
        )
    return (
        "Return either one JSON object with exactly two keys, "
        "appraisal_draft and expression_draft, or the single recall_request "
        "object described below when the occasion says recall is available. "
        "If recall is unavailable, return only the two-draft envelope. "
        + slim_consider_instruction()
    )


def compact_gate_recall_instruction() -> str:
    return (
        "Choose result_kind=recall only when the occasion says recall is available; "
        "otherwise do not choose recall. "
    )


def forced_tool_recall_instruction(*, private_turn_state_required: bool) -> str:
    extra = (
        " and private_turn_state"
        if private_turn_state_required
        else " (private_turn_state may also be included)"
    )
    return (
        "For result_kind=recall include recall_request"
        + extra
        + " only when the occasion says recall is available."
    )


def present_inner_life(snapshot: dict[str, object]) -> dict[str, object]:
    materials = snapshot.get("materials")
    ordered_snapshot = dict(snapshot)
    if isinstance(materials, dict):
        ordered_materials: dict[str, object] = {}
        for key in _MATERIAL_ORDER:
            if key in materials:
                ordered_materials[key] = materials[key]
        for key, value in materials.items():
            if key not in ordered_materials:
                ordered_materials[key] = value
        ordered_snapshot["materials"] = ordered_materials
    ordered: dict[str, object] = {}
    volatile = set(_SNAPSHOT_VOLATILE_LAST)
    for key, value in ordered_snapshot.items():
        if key not in volatile:
            ordered[key] = value
    for key in _SNAPSHOT_VOLATILE_LAST:
        if key in ordered_snapshot:
            ordered[key] = ordered_snapshot[key]
    return ordered


def order_user_present_payload(material: dict[str, object]) -> dict[str, object]:
    prepared = dict(material)
    snapshot = prepared.get("inner_life_snapshot")
    if isinstance(snapshot, dict):
        prepared["inner_life_snapshot"] = present_inner_life(snapshot)
    ordered: dict[str, object] = {}
    for key in _PRESENT_USER_KEY_ORDER:
        if key in prepared:
            ordered[key] = prepared[key]
    for key, value in prepared.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def present_hard_boundary_prompt(manifest: Mapping[str, object]) -> dict[str, object]:
    """Keep copyable tokens; drop the 6.5k mechanism essays from the model prompt."""

    allowed = (
        "world_claim_source_refs",
        "source_ref_aliases",
        "companion_life_authority_availability",
    )
    stub: dict[str, object] = {
        "contract": "expression-hard-boundaries.present.1",
        "authority": "checked_after_expression",
    }
    for key in allowed:
        if key in manifest:
            stub[key] = manifest[key]
    return stub


def normalize_text_beats(value: dict[str, object]) -> dict[str, object]:
    if "beats" not in value:
        messages = value.get("messages")
        if (
            isinstance(messages, list)
            and messages
            and all(isinstance(item, str) and item.strip() for item in messages)
        ):
            value = {key: item for key, item in value.items() if key != "messages"}
            value["beats"] = [{"modality": "text", "text": item.strip()} for item in messages]
    beats = value.get("beats")
    if not isinstance(beats, list):
        return value
    normalized: list[object] = []
    changed = False
    for beat in beats:
        if isinstance(beat, str) and beat.strip():
            normalized.append({"modality": "text", "text": beat})
            changed = True
            continue
        if (
            isinstance(beat, dict)
            and set(beat) == {"text"}
            and isinstance(beat.get("text"), str)
            and beat["text"]
        ):
            normalized.append({"modality": "text", "text": beat["text"]})
            changed = True
            continue
        normalized.append(beat)
    if not changed:
        return value
    return {**value, "beats": normalized}


def json_schema_g4_metrics(schema: Mapping[str, object]) -> tuple[int, int, int]:
    required = schema.get("required")
    required_count = len(required) if isinstance(required, list) else 0
    properties = schema.get("properties")
    total = len(properties) if isinstance(properties, dict) else 0

    def depth(node: object) -> int:
        if not isinstance(node, dict):
            return 0
        nested = []
        props = node.get("properties")
        if isinstance(props, dict):
            nested.extend(props.values())
        items = node.get("items")
        if isinstance(items, dict):
            nested.append(items)
        one_of = node.get("oneOf")
        if isinstance(one_of, list):
            nested.extend(item for item in one_of if isinstance(item, dict))
        if not nested:
            return 1
        return 1 + max(depth(item) for item in nested)

    return required_count, total, depth(schema)


def identity_prose(frame: CompanionIdentityFrame) -> str:
    parts: list[str] = []
    if frame.base_prompt:
        parts.append(frame.base_prompt.strip())
    if frame.personality_frame:
        parts.append(frame.personality_frame.strip())
    if frame.appearance:
        parts.append("外貌：" + frame.appearance.strip())
    if frame.background:
        parts.append("成长背景：" + frame.background.strip())
    if frame.daily_life:
        parts.append(
            "习惯（不是此刻正在做的事）："
            + " ".join(item.strip() for item in frame.daily_life if item.strip())
        )
    if frame.speech_frame:
        parts.append("说话：" + frame.speech_frame.strip())
    if frame.style_rules:
        parts.append("写法：" + " ".join(item.strip() for item in frame.style_rules if item.strip()))
    if frame.speech_examples:
        parts.append(
            "她说话的样子（示例，不是固定台词）："
            + " ".join(item.strip() for item in frame.speech_examples if item.strip())
        )
    if frame.values:
        parts.append("价值：" + " ".join(item.strip() for item in frame.values if item.strip()))
    if frame.boundaries:
        parts.append("边界：" + " ".join(item.strip() for item in frame.boundaries if item.strip()))
    if frame.first_message:
        parts.append("初次开口：" + frame.first_message.strip())
    return "\n".join(parts)


SLIM_CONSIDER_KEYS = frozenset(
    {
        "messages",
        "felt",
        "stuck_with_me",
        "wants",
        "photo",
        "later",
        "waiting_for",
        "wait",
        "how_it_landed",
        "noticed",
        "keep_impression",
        "come_back",
        "come_back_in",
        "about_us",
        "why_us",
        "us_deltas",
        "we_are",
        "calling_it",
        "said_as",
        "matters_bp",
        "mood",
        "declared_display",
        "affect",
        "episode_id",
        "components",
        "resolution_summary",
    }
)
_SLIM_CONSIDER_KEYS = SLIM_CONSIDER_KEYS
_SLIM_AFFECT_OPERATIONS = frozenset(
    {"no_change", "open", "update", "resolve", "supersede"}
)
_SLIM_AFFECT_DIMENSIONS = frozenset(
    {
        "hurt",
        "anger",
        "sadness",
        "loneliness",
        "anxiety",
        "resentment",
        "warmth",
        "joy",
    }
)
_SLIM_AFFECT_DEFAULT_INTENSITY_BP = 5_000
# Used only when she does not weigh the reading herself.  It sits below the
# reflection threshold on purpose: an unweighted reading should not schedule
# her to think about it again.
_SLIM_APPRAISAL_DEFAULT_CONFIDENCE_BP = 5_000
_SLIM_ORDINARY_STAGES = frozenset(
    {"acquaintance", "friend", "close_friend", "ambiguous", "lover"}
)
_SLIM_DECLARED_DISPLAY = frozenset(
    {"sexual_suggestive", "explicit_adult", "withdraw"}
)
_SLIM_ZERO_RELATIONSHIP_DELTAS = {
    "trust_bp": 0,
    "closeness_bp": 0,
    "respect_bp": 0,
    "reliability_bp": 0,
    "mutuality_bp": 0,
    "repair_confidence_bp": 0,
}
# The wire accepts the full signed range; the adjustment compiler owns the
# per-turn cap.  Anything outside this range is a malformed number rather than
# an ambitious one, so the whole object is dropped instead of being rescaled.
_SLIM_RELATIONSHIP_DELTA_LIMIT_BP = 10_000
_SLIM_ASSESSMENT_STATUSES = frozenset(
    {"fulfilled", "superseded", "still_pending", "uncertain"}
)
_SLIM_EXPECTATION_DEFAULT_BP = 5_000
_SLIM_WAIT_FLOOR_SECONDS = 30
_SLIM_WAIT_MAX_SECONDS = 86_400
# Same hard cap as ExpressionDraftCapabilities.max_beats / max_later_beats.
SLIM_REPLY_ONLY_MAX_TEXT_BEATS = 8
_SLIM_EXPECTATION_EXPIRES_MAX_SECONDS = 172_800
_SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS = 60
# H21: waiting_for without wait does not compile a hope. These sentinels remain
# only so leftover ledger records written by the 2026-08-18 optional-wait
# experiment can still be recognized as "not a seconds-declared wait".
SLIM_OPEN_HOPE_WAIT_SECONDS = _SLIM_WAIT_MAX_SECONDS
SLIM_OPEN_HOPE_EXPIRES_AFTER_SECONDS = _SLIM_EXPECTATION_EXPIRES_MAX_SECONDS
SLIM_OPEN_HOPE_CHASE_SECONDS = (
    SLIM_OPEN_HOPE_EXPIRES_AFTER_SECONDS - SLIM_OPEN_HOPE_WAIT_SECONDS
)
_WAIT_DURATION = re.compile(r"^(\d+)\s*([A-Za-z\u4e00-\u9fff]+)$")
_WAIT_UNIT_SECONDS = (
    (("秒钟", "秒", "seconds", "second", "secs", "sec", "s"), 1),
    (("minutes", "minute", "mins", "min", "分钟", "分", "m"), 60),
    (("hours", "hour", "hrs", "hr", "小时", "钟头", "h"), 3_600),
    (("days", "day", "天", "d"), 86_400),
)
def _clip_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text[:limit]


def _slim_messages(value: object) -> list[str] | None:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return None
    messages: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            messages.append(item.strip())
            continue
        if (
            isinstance(item, dict)
            and set(item) == {"text"}
            and isinstance(item.get("text"), str)
            and item["text"].strip()
        ):
            messages.append(item["text"].strip())
            continue
        return None
    return messages


def is_slim_consider_payload(value: Mapping[str, object]) -> bool:
    """True when she wrote the cheap ``messages`` object.

    A complete event envelope or dual-draft still belongs to those compilers.
    Extra sibling keys from those envelopes — most commonly ``appraisal_draft``
    riding next to ``messages`` and ``felt`` — used to poison this detector,
    so a finished Chinese reply was discarded as if she had written nothing.
    """

    if "messages" not in value:
        return False
    if (
        value.get("protocol") == "character-interior-events.1"
        and isinstance(value.get("appraisal_draft"), dict)
        and isinstance(value.get("events"), list)
    ):
        return False
    if isinstance(value.get("expression_draft"), dict) and isinstance(
        value.get("appraisal_draft"), dict
    ):
        return False
    return True


def _clamp_declared_wait_seconds(value: int) -> int:
    return max(_SLIM_WAIT_FLOOR_SECONDS, min(_SLIM_WAIT_MAX_SECONDS, value))


def _parse_declared_wait_seconds(raw: object) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return _clamp_declared_wait_seconds(raw)
    if isinstance(raw, float) and raw.is_integer():
        return _clamp_declared_wait_seconds(int(raw))
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.isdigit():
        return _clamp_declared_wait_seconds(int(text))
    match = _WAIT_DURATION.fullmatch(text)
    if match is None:
        return None
    unit = match.group(2).lower()
    for labels, multiplier in _WAIT_UNIT_SECONDS:
        if unit in labels or unit in {item.lower() for item in labels}:
            return _clamp_declared_wait_seconds(int(match.group(1)) * multiplier)
    return None


def _slim_wait_horizon(value: Mapping[str, object]) -> tuple[int, int] | None:
    wait_seconds = _parse_declared_wait_seconds(value.get("wait"))
    if wait_seconds is None:
        return None
    expires_after_seconds = min(
        _SLIM_EXPECTATION_EXPIRES_MAX_SECONDS,
        wait_seconds + _SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS,
    )
    return wait_seconds, expires_after_seconds


def _slim_response_expectation(value: Mapping[str, object]) -> dict[str, object] | None:
    hoped = _clip_text(value.get("waiting_for"), 160)
    if not hoped:
        return None
    horizon = _slim_wait_horizon(value)
    if horizon is None:
        # H21: 没填 wait 不编译盼头（不定时叫醒）。
        return None
    wait_seconds, expires_after_seconds = horizon
    hoped = hoped[:128]
    if not hoped:
        return None
    return {
        "hoped_response": hoped,
        "pressure_bp": _SLIM_EXPECTATION_DEFAULT_BP,
        "importance_bp": _SLIM_EXPECTATION_DEFAULT_BP,
        "wait_seconds": wait_seconds,
        "expires_after_seconds": expires_after_seconds,
    }


def compile_declared_response_expectation(
    value: Mapping[str, object],
) -> dict[str, object] | None:
    """Compile slim waiting_for+wait into a hope, or raise a visible pair error.

    waiting_for without wait is a legal omit: no hope, no timed wake.
    wait without waiting_for is a half-written pair and must not be eaten.
    """

    _raise_if_incomplete_wait_pair(value)
    return _slim_response_expectation(value)


def _slim_revisit(value: Mapping[str, object]) -> dict[str, object] | None:
    thought = _clip_text(value.get("come_back"), 160)
    if not thought:
        return None
    horizon = _slim_wait_horizon({"wait": value.get("come_back_in")})
    if horizon is None:
        return None
    wait_seconds, expires_after_seconds = horizon
    return {
        "thought": thought,
        "wait_seconds": wait_seconds,
        "expires_after_seconds": expires_after_seconds,
    }


def _slim_response_expectation_assessment(
    value: Mapping[str, object],
    *,
    reason: str,
) -> dict[str, object] | None:
    status = value.get("how_it_landed")
    if not isinstance(status, str) or status not in _SLIM_ASSESSMENT_STATUSES:
        return None
    clipped = reason.strip()[:240]
    if not clipped:
        return None
    return {"status": status, "reason": clipped}


def _parse_declared_later_seconds(raw: object) -> int | None:
    parsed = _parse_declared_wait_seconds(raw)
    if parsed is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw != parsed:
        return None
    if isinstance(raw, float) and raw.is_integer() and int(raw) != parsed:
        return None
    return parsed


def _slim_later_horizon(value: Mapping[str, object]) -> tuple[int, int] | None:
    delay_seconds = _parse_declared_later_seconds(value.get("later"))
    if delay_seconds is None:
        return None
    expires_after_seconds = min(
        _SLIM_EXPECTATION_EXPIRES_MAX_SECONDS,
        delay_seconds + _SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS,
    )
    if expires_after_seconds <= delay_seconds:
        return None
    return delay_seconds, expires_after_seconds


SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE = (
    "关系增量要留下，us_deltas 必须和 about_us、why_us 一起写"
)
SLIM_RELATIONSHIP_DELTAS_UNREADABLE = "us_deltas 必须是六轴上的带符号整数"
SLIM_WAIT_PAIR_INCOMPLETE = "wait 只能和 waiting_for 一起写"
SLIM_WAIT_NOT_A_DURATION = "wait 必须是 30 到 86400 的整数秒"
SLIM_COME_BACK_PAIR_INCOMPLETE = "come_back_in 只能和 come_back 一起写"
SLIM_COME_BACK_IN_NOT_A_DURATION = "come_back_in 必须是 30 到 86400 的整数秒"
SLIM_COMMITMENT_TRIPLET_INCOMPLETE = "we_are、calling_it、said_as 三个要一起写"
SLIM_COMMITMENT_WE_ARE_INVALID = (
    "we_are 只能是 acquaintance、friend、close_friend、ambiguous 或 lover"
)
SLIM_COMMITMENT_REQUIRES_SPEECH = (
    "we_are 只能跟这一轮真的说出去的话一起写，沉默这一轮不要写 we_are"
)
SLIM_DECLARED_DISPLAY_INVALID = (
    "declared_display 只能是 sexual_suggestive、explicit_adult 或 withdraw"
)
SLIM_LATER_NOT_A_DURATION = "later 必须是 30 到 86400 的整数秒"
SLIM_LATER_REQUIRES_TEXT = "later 只能和非空 messages 一起写，不能和沉默或 photo 一起用"
SLIM_HOW_IT_LANDED_INVALID = (
    "how_it_landed 只能是 fulfilled、superseded、still_pending 或 uncertain"
)


def _slim_field_attempted(value: Mapping[str, object], key: str) -> bool:
    """True when she wrote a key as a real attempt. JSON null is omission."""

    if key not in value:
        return False
    raw = value[key]
    if raw is None:
        return False
    if isinstance(raw, str) and not raw.strip():
        return False
    if isinstance(raw, Mapping) and not raw:
        return False
    if isinstance(raw, list) and not raw:
        return False
    return True


def _raise_incomplete_pair(
    *,
    stem: str,
    missing: list[str],
) -> None:
    raise ValueError(
        stem
        + "。这次缺了："
        + "、".join(missing)
        + "。宿主不会替你补上缺的字段。"
        "两个一起写才会在那个秒数叫醒你；只写一半不会生效，也不会被默默丢掉。"
        "两个都不写也可以，那就是这一轮没有人叫你。"
    )


def _raise_if_incomplete_wait_pair(value: Mapping[str, object]) -> None:
    """Refuse a half-written wait instead of silently eating waiting_for or wait."""

    hoped = (
        _clip_text(value.get("waiting_for"), 160)
        if _slim_field_attempted(value, "waiting_for")
        else ""
    )
    wait_attempted = _slim_field_attempted(value, "wait")
    parsed = _parse_declared_wait_seconds(value.get("wait")) if wait_attempted else None
    if not hoped and not wait_attempted:
        return
    if wait_attempted and parsed is None:
        raise ValueError(
            SLIM_WAIT_NOT_A_DURATION
            + "。这次写的 wait 读不成秒数。宿主不会替你编一个秒数，"
            "也不会把写坏的 wait 丢掉后假装没写。"
            "两个都写对了才会叫醒你；两个都不写也可以。"
        )
    if wait_attempted and not hoped:
        _raise_incomplete_pair(stem=SLIM_WAIT_PAIR_INCOMPLETE, missing=["waiting_for"])


def _raise_if_incomplete_come_back_pair(value: Mapping[str, object]) -> None:
    """Refuse a half-written come_back instead of silently eating the leftover."""

    thought = (
        _clip_text(value.get("come_back"), 160)
        if _slim_field_attempted(value, "come_back")
        else ""
    )
    time_attempted = _slim_field_attempted(value, "come_back_in")
    parsed = (
        _parse_declared_wait_seconds(value.get("come_back_in")) if time_attempted else None
    )
    if not thought and not time_attempted:
        return
    if time_attempted and parsed is None:
        raise ValueError(
            SLIM_COME_BACK_IN_NOT_A_DURATION
            + "。这次写的 come_back_in 读不成秒数。宿主不会替你编一个秒数，"
            "也不会把写坏的 come_back_in 丢掉后假装没写。"
            "两个都写对了才会为这件事叫你；两个都不写也可以。"
        )
    missing = [
        name
        for name, present in (
            ("come_back", thought),
            ("come_back_in", time_attempted and parsed is not None),
        )
        if not present
    ]
    if missing:
        _raise_incomplete_pair(stem=SLIM_COME_BACK_PAIR_INCOMPLETE, missing=missing)


def _raise_if_incomplete_relationship_residue(value: Mapping[str, object]) -> None:
    """Refuse a half-written relationship residue instead of silently eating it.

    about_us + why_us without us_deltas still records a reading with zero
    movement.  us_deltas, or exactly one of the prose fields, used to compile
    and then vanish.  That is a visible failure: the host never invents the
    missing field.
    """

    about_us = _clip_text(value.get("about_us"), 128)
    why_us = _clip_text(value.get("why_us"), 128)
    wrote_deltas = slim_relationship_deltas(value.get("us_deltas")) is not None
    if _slim_field_attempted(value, "us_deltas") and not wrote_deltas:
        raise ValueError(
            SLIM_RELATIONSHIP_DELTAS_UNREADABLE
            + "。这次写的 us_deltas 读不成六轴增量。宿主不会替你编一个数，"
            "也不会把写坏的增量丢掉后假装没写。"
            "要留下增量就写成带符号整数的对象，并和 about_us、why_us 一起写；"
            "三个都不写也可以。"
        )
    if (bool(about_us) == bool(why_us)) and (not wrote_deltas or (about_us and why_us)):
        return
    missing = [
        name
        for name, present in (("about_us", about_us), ("why_us", why_us))
        if not present
    ]
    raise ValueError(
        SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE
        + "。这次缺了："
        + "、".join(missing)
        + "。宿主不会替你补上缺的字段。"
        "三个一起写才会变成信号；只写一半不会生效，也不会被默默丢掉。"
        "三个都不写也可以，那就是这一轮关系没有动。"
    )


def _raise_if_incomplete_commitment_triplet(value: Mapping[str, object]) -> None:
    """Refuse a half-written we_are / calling_it / said_as instead of dropping it."""

    we_are_attempted = _slim_field_attempted(value, "we_are")
    calling_attempted = _slim_field_attempted(value, "calling_it")
    said_attempted = _slim_field_attempted(value, "said_as")
    if not we_are_attempted and not calling_attempted and not said_attempted:
        return
    we_are = value.get("we_are") if we_are_attempted else None
    valid_stage = we_are in _SLIM_ORDINARY_STAGES
    if we_are_attempted and not valid_stage:
        raise ValueError(
            SLIM_COMMITMENT_WE_ARE_INVALID
            + "。这次写的 we_are 不是其中之一。宿主不会替你改成一个合法阶段，"
            "也不会把写坏的承诺丢掉后假装没写。"
            "三个都写对了才会留下这条路径；三个都不写也可以。"
        )
    calling_it = (
        _clip_text(value.get("calling_it"), 128) if calling_attempted else ""
    )
    said_as = _clip_text(value.get("said_as"), 512) if said_attempted else ""
    missing = [
        name
        for name, present in (
            ("we_are", valid_stage),
            ("calling_it", calling_it),
            ("said_as", said_as),
        )
        if not present
    ]
    if missing:
        raise ValueError(
            SLIM_COMMITMENT_TRIPLET_INCOMPLETE
            + "。这次缺了："
            + "、".join(missing)
            + "。宿主不会替你补上缺的字段。"
            "三个一起写才会留下这条路径；只写一半不会生效，也不会被默默丢掉。"
            "三个都不写也可以，那就是这一轮没有声明你们是什么关系。"
        )


def _raise_if_invalid_declared_display(value: Mapping[str, object]) -> None:
    """Refuse a written-but-unreadable display intent instead of eating it."""

    if not _slim_field_attempted(value, "declared_display"):
        return
    if _slim_declared_display(value.get("declared_display")) is None:
        raise ValueError(
            SLIM_DECLARED_DISPLAY_INVALID
            + "。省略或 null 也可以，那是这一轮不声明、也不改之前的声明。"
            "这次写的 declared_display 不是其中之一。宿主不会替你改成一个合法值，"
            "也不会把写坏的声明丢掉后假装没写。"
        )


def _raise_if_invalid_later(
    value: Mapping[str, object],
    *,
    messages: list[str],
    media_request: str,
) -> tuple[int, int] | None:
    """Refuse a written-but-unreadable later instead of sending now or dropping."""

    if not _slim_field_attempted(value, "later"):
        return None
    horizon = _slim_later_horizon(value)
    if horizon is None:
        raise ValueError(
            SLIM_LATER_NOT_A_DURATION
            + "。这次写的 later 读不成秒数。宿主不会替你编一个秒数，"
            "也不会把写坏的 later 当成现在发，更不会丢掉后假装没写。"
            "不写 later 也可以，那就是现在发。"
        )
    if not messages or media_request != "none":
        raise ValueError(
            SLIM_LATER_REQUIRES_TEXT
            + "。later 只能配已经想好的文字气泡；沉默和 photo 都不能跟 later 一起写。"
            "宿主不会替你改成现在发，也不会把写坏的 later 丢掉后假装没写。"
        )
    return horizon


def _raise_if_invalid_how_it_landed(value: Mapping[str, object]) -> None:
    """Refuse a written-but-unreadable landing status instead of eating it."""

    if not _slim_field_attempted(value, "how_it_landed"):
        return
    status = value.get("how_it_landed")
    if not isinstance(status, str) or status not in _SLIM_ASSESSMENT_STATUSES:
        raise ValueError(
            SLIM_HOW_IT_LANDED_INVALID
            + "。省略或 null 也可以，那是这一轮不评估上次的盼头。"
            "这次写的 how_it_landed 不是其中之一。宿主不会替你改成一个合法值，"
            "也不会把写坏的评估丢掉后假装没写。"
        )


def compile_slim_consider_payload(
    value: Mapping[str, object],
) -> dict[str, object] | None:
    """Compile the slim consider shape into dual drafts. Old drafts stay as-is."""

    if not is_slim_consider_payload(value):
        return None
    messages = _slim_messages(value.get("messages"))
    if messages is None:
        return None
    authored_felt = _clip_text(value.get("felt"), 240)
    felt = (
        authored_felt
        or _clip_text(value.get("stuck_with_me"), 240)
        or _clip_text(value.get("wants"), 240)
    )
    if not felt:
        felt = messages[0][:240] if messages else ""
    if not felt:
        return None
    label = felt[:64]
    stuck = _clip_text(value.get("stuck_with_me"), 480) or felt
    wants = _clip_text(value.get("wants"), 240)
    photo = value.get("photo")
    media_request = "none"
    media_source_refs: list[str] = []
    if photo is True:
        media_request = "consider_available_candidate"
    elif isinstance(photo, str) and photo.strip() and photo.strip() not in {"none", "false"}:
        media_request = "consider_available_candidate"
        if photo.strip() not in {"true", "consider_available_candidate"}:
            media_source_refs = [photo.strip()]
    if media_request != "none" and not messages:
        return None
    delay_seconds: int | None = None
    expires_after_seconds: int | None = None
    later_horizon = _raise_if_invalid_later(
        value, messages=messages, media_request=media_request
    )
    if later_horizon is not None:
        delay_seconds, expires_after_seconds = later_horizon
        timing = "later"
    else:
        timing = "now" if messages else "silent"
    private_turn_state: dict[str, object] = {
        "contract": "private-turn-state.1",
        "inner_state_summary": stuck,
        "attended_source_refs": [],
    }
    noticed = _clip_text(value.get("noticed"), 720)
    if noticed:
        private_turn_state["noticed"] = noticed
    keep_impression = value.get("keep_impression")
    if keep_impression is True:
        private_turn_state["keep_impression"] = True
    elif keep_impression is False:
        private_turn_state["keep_impression"] = False
    _raise_if_incomplete_wait_pair(value)
    _raise_if_incomplete_come_back_pair(value)
    _raise_if_incomplete_relationship_residue(value)
    _raise_if_incomplete_commitment_triplet(value)
    _raise_if_invalid_declared_display(value)
    _raise_if_invalid_how_it_landed(value)
    about_us = _clip_text(value.get("about_us"), 128)
    why_us = _clip_text(value.get("why_us"), 128)
    if about_us and why_us:
        private_turn_state["about_us"] = about_us
        private_turn_state["why_us"] = why_us
    we_are = value.get("we_are")
    calling_it = _clip_text(value.get("calling_it"), 128)
    said_as = _clip_text(value.get("said_as"), 512)
    if we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as:
        private_turn_state["we_are"] = we_are
        private_turn_state["calling_it"] = calling_it
        private_turn_state["said_as"] = said_as
    declared_display = _slim_declared_display(value.get("declared_display"))
    if declared_display is not None:
        private_turn_state["declared_display"] = declared_display
    expression: dict[str, object] = {
        "private_turn_state": private_turn_state,
        "timing_choice": timing,
        "cadence": "conversational",
        "beats": [{"modality": "text", "text": item} for item in messages],
        "stance": label,
        "brief_rationale": felt,
        "confidence": 5000,
        "world_claims": [],
        "media_request": media_request,
        "media_source_refs": media_source_refs,
    }
    if wants:
        expression["impulse_summary"] = wants
    if timing == "later":
        assert delay_seconds is not None and expires_after_seconds is not None
        expression["delay_seconds"] = delay_seconds
        expression["expires_after_seconds"] = expires_after_seconds
    if messages:
        expectation = _slim_response_expectation(value)
        if expectation is not None:
            expression["response_expectation"] = expectation
        leftover = None if timing == "later" else _slim_revisit(value)
        if leftover is not None:
            expression["revisit"] = leftover
    assessment = _slim_response_expectation_assessment(value, reason=felt)
    if assessment is not None:
        expression["response_expectation_assessment"] = assessment
    return {
        "appraisal_draft": _slim_appraisal_draft(
            felt=felt,
            authored_felt=authored_felt,
            label=label,
            mood=value.get("mood"),
            affect=value.get("affect"),
            episode_id=value.get("episode_id"),
            components=value.get("components"),
            resolution_summary=value.get("resolution_summary"),
            matters_bp=value.get("matters_bp"),
            keep_impression=keep_impression is True,
        ),
        "expression_draft": expression,
    }


def _slim_declared_display(value: object) -> str | None:
    """Read her display intent; the host never invents one from prose.

    A string enum is the documented shape.  An object with only media_intent is
    accepted so a nested write is not dropped, but recipient_ref is ignored:
    the inbound observation actor is bound later, never a model-authored ref.
    """

    if isinstance(value, str):
        intent = value.strip()
        return intent if intent in _SLIM_DECLARED_DISPLAY else None
    if isinstance(value, Mapping):
        intent = value.get("media_intent")
        if isinstance(intent, str) and intent.strip() in _SLIM_DECLARED_DISPLAY:
            return intent.strip()
    return None


def _slim_matters_bp(value: object) -> int:
    """Her own weight on this reading; it decides whether it comes back to her.

    An omitted or malformed number stays at the middling default rather than
    being guessed from wording, so the host never decides that something
    mattered more (or less) to her than she said.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        return _SLIM_APPRAISAL_DEFAULT_CONFIDENCE_BP
    if not 1 <= value <= 10_000:
        return _SLIM_APPRAISAL_DEFAULT_CONFIDENCE_BP
    return value


def _slim_affect_dimension(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    dimension = value.strip().lower()
    if dimension not in _SLIM_AFFECT_DIMENSIONS:
        return None
    return dimension


def _slim_appraisal_draft(
    *,
    felt: str,
    authored_felt: str,
    label: str,
    mood: object = None,
    affect: object = None,
    episode_id: object = None,
    components: object = None,
    resolution_summary: object = None,
    matters_bp: object = None,
    keep_impression: bool = False,
) -> dict[str, object]:
    """Keep her authored felt as a reading; lasting Affect only when she chooses it."""

    weight = _slim_matters_bp(matters_bp)
    affect_dimension = _slim_affect_dimension(mood)
    affect_operation = (
        affect.strip().lower()
        if isinstance(affect, str) and affect.strip().lower() in _SLIM_AFFECT_OPERATIONS
        else None
    )
    common: dict[str, object] = {
        "affect": "no_change",
        "brief_rationale": felt,
        "behavior_tendency": label,
        "stance": label,
        "display_strategy": label,
        "confidence": weight,
    }
    if affect_operation is not None and affect_operation != "no_change":
        common["affect"] = affect_operation
        if isinstance(episode_id, str) and episode_id.strip():
            common["episode_id"] = episode_id.strip()
        if isinstance(resolution_summary, str) and resolution_summary.strip():
            common["resolution_summary"] = resolution_summary.strip()[:240]
        if isinstance(components, list) and components:
            common["components"] = components
        elif affect_operation in {"open", "update", "supersede"} and affect_dimension is not None:
            common["components"] = [
                {
                    "dimension": affect_dimension,
                    "target_intensity_bp": _SLIM_AFFECT_DEFAULT_INTENSITY_BP,
                }
            ]
    elif affect_dimension is not None:
        common["affect"] = "open"
        common["components"] = [
            {
                "dimension": affect_dimension,
                "target_intensity_bp": _SLIM_AFFECT_DEFAULT_INTENSITY_BP,
            }
        ]
    meaning = _clip_text(authored_felt, 128).rstrip()
    if not meaning and common["affect"] != "no_change":
        meaning = _clip_text(felt, 128).rstrip() or (
            affect_dimension if isinstance(affect_dimension, str) else "affect"
        )
    if not meaning and keep_impression:
        # Asking to keep this as a private impression is itself a statement that
        # the reading mattered.  Without an appraisal to hang it on, the paid
        # impression lane finds no anchor and her keep decision is dropped.
        meaning = _clip_text(felt, 128).rstrip()
    if not meaning:
        return {"appraise": False, **common}
    return {
        "appraise": True,
        **common,
        "meanings": [{"meaning": meaning, "confidence": weight}],
        "attribution": "unknown",
        "severity": 5000,
    }


def slim_relationship_deltas(value: object) -> dict[str, int] | None:
    """Read her own signed movement on the six axes; omitted axes stay at zero.

    The host never derives movement from prose, so an absent or malformed
    object means the relationship did not move this turn.
    """

    if not isinstance(value, Mapping) or not value:
        return None
    if set(value) - set(_SLIM_ZERO_RELATIONSHIP_DELTAS):
        return None
    deltas = dict(_SLIM_ZERO_RELATIONSHIP_DELTAS)
    for axis, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None
        if abs(raw) > _SLIM_RELATIONSHIP_DELTA_LIMIT_BP:
            return None
        deltas[axis] = raw
    if not any(deltas.values()):
        return None
    return deltas


def attach_hitchhiked_relationship_residue(
    value: Mapping[str, object],
    *,
    authored: Mapping[str, object] | None = None,
) -> dict[str, object]:
    appraisal = value.get("appraisal_draft")
    expression = value.get("expression_draft")
    if not isinstance(appraisal, dict) or not isinstance(expression, dict):
        return dict(value)
    state = expression.get("private_turn_state")
    if not isinstance(state, dict):
        return dict(value)
    if authored is not None:
        _raise_if_incomplete_relationship_residue(authored)
    next_appraisal = dict(appraisal)
    changed = False
    if next_appraisal.get("relationship_signal") is None:
        about_us = _clip_text(state.get("about_us"), 128)
        why_us = _clip_text(state.get("why_us"), 128)
        if about_us and why_us:
            authored_deltas = (
                slim_relationship_deltas(authored.get("us_deltas"))
                if authored is not None
                else None
            )
            next_appraisal["relationship_signal"] = {
                "signal_code": about_us,
                "rationale_code": why_us,
                "confidence_bp": 5_000,
                "persistence": "durable",
                "suggested_deltas": authored_deltas
                or dict(_SLIM_ZERO_RELATIONSHIP_DELTAS),
            }
            changed = True
    if next_appraisal.get("relationship_commitment") is None:
        we_are = state.get("we_are")
        calling_it = _clip_text(state.get("calling_it"), 128)
        said_as = _clip_text(state.get("said_as"), 512)
        if we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as:
            next_appraisal["relationship_commitment"] = {
                "target_stage": we_are,
                "commitment_code": calling_it,
                "persistence": "durable",
                "visible_text_span": said_as,
            }
            changed = True
    if not changed:
        return dict(value)
    return {**dict(value), "appraisal_draft": next_appraisal}


def _proactive_has_visible_speech(bound: Mapping[str, object]) -> bool:
    if bound.get("timing_choice") == "silent":
        return False
    beats = bound.get("beats")
    if not isinstance(beats, list):
        return False
    return any(
        isinstance(beat, dict) and bool(str(beat.get("text") or "").strip())
        for beat in beats
    )


def hitchhike_proactive_authored_decisions(
    bound: dict[str, object],
    *,
    authored: Mapping[str, object],
) -> dict[str, object]:
    """Copy inbound slim residue onto a proactive ExpressionDraft-shaped dict.

    Incomplete or unreadable pairs raise.  Omission stays omission.  The host
    never invents a reading, a stage, or a display intent.
    """

    _raise_if_incomplete_relationship_residue(authored)
    _raise_if_incomplete_commitment_triplet(authored)
    _raise_if_invalid_declared_display(authored)
    if (
        _slim_field_attempted(authored, "we_are")
        or _slim_field_attempted(authored, "calling_it")
        or _slim_field_attempted(authored, "said_as")
    ) and not _proactive_has_visible_speech(bound):
        raise ValueError(
            SLIM_COMMITMENT_REQUIRES_SPEECH
            + "。宿主不会替你改成开口，也不会把 we_are 丢掉后假装没写。"
        )
    state = bound.get("private_turn_state")
    next_state = dict(state) if isinstance(state, dict) else {}
    added = False
    noticed = _clip_text(authored.get("noticed"), 720)
    if noticed:
        next_state["noticed"] = noticed
        added = True
    keep_impression = authored.get("keep_impression")
    if keep_impression is True:
        next_state["keep_impression"] = True
        added = True
    elif keep_impression is False:
        next_state["keep_impression"] = False
        added = True
    about_us = _clip_text(authored.get("about_us"), 128)
    why_us = _clip_text(authored.get("why_us"), 128)
    if about_us and why_us:
        next_state["about_us"] = about_us
        next_state["why_us"] = why_us
        added = True
    we_are = authored.get("we_are")
    calling_it = _clip_text(authored.get("calling_it"), 128)
    said_as = _clip_text(authored.get("said_as"), 512)
    if we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as:
        next_state["we_are"] = we_are
        next_state["calling_it"] = calling_it
        next_state["said_as"] = said_as
        added = True
    declared_display = _slim_declared_display(authored.get("declared_display"))
    if declared_display is not None:
        next_state["declared_display"] = declared_display
        added = True
    if isinstance(state, dict) or added:
        bound["private_turn_state"] = next_state
    needs_appraisal_anchor = bool(
        (about_us and why_us)
        or keep_impression is True
        or (we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as)
    )
    appraisal = bound.get("appraisal_draft")
    if needs_appraisal_anchor and not isinstance(appraisal, dict):
        felt = (
            _clip_text(bound.get("brief_rationale"), 240)
            or _clip_text(bound.get("impulse_summary"), 240)
            or _clip_text(bound.get("stance"), 128)
        )
        label = _clip_text(bound.get("stance"), 64) or felt[:64]
        if felt:
            bound["appraisal_draft"] = _slim_appraisal_draft(
                felt=felt,
                authored_felt=felt,
                label=label or felt[:64],
                mood=bound.get("mood"),
                keep_impression=keep_impression is True,
            )
            appraisal = bound["appraisal_draft"]
    if isinstance(appraisal, dict):
        hitchhiked = attach_hitchhiked_relationship_residue(
            {
                "appraisal_draft": appraisal,
                "expression_draft": {"private_turn_state": next_state},
            },
            authored=authored,
        )
        bound["appraisal_draft"] = hitchhiked["appraisal_draft"]
    return bound


def compile_slim_interior_envelope(
    value: Mapping[str, object],
    *,
    reply_only: bool,
) -> dict[str, object] | None:
    compiled = compile_slim_consider_payload(value)
    if compiled is None:
        return None
    # Attach before transport expansion so the compact gate actually carries the
    # relationship reading she authored instead of dropping it on the cheap path.
    compiled = attach_hitchhiked_relationship_residue(compiled, authored=value)
    expression = compiled["expression_draft"]
    if not isinstance(expression, dict):
        return None
    beats = expression.get("beats")
    if not isinstance(beats, list):
        return None
    if reply_only:
        timing = expression.get("timing_choice")
        if (
            expression.get("media_request") != "none"
            or expression.get("media_source_refs")
        ):
            raise ValueError("reply_only slim payload exceeds text-only capability")
        delay_seconds = expression.get("delay_seconds")
        expires_after_seconds = expression.get("expires_after_seconds")
        extra_beats: list[object] | None = None
        if timing == "now":
            if (
                not (1 <= len(beats) <= SLIM_REPLY_ONLY_MAX_TEXT_BEATS)
                or delay_seconds is not None
                or expires_after_seconds is not None
            ):
                raise ValueError("reply_only slim payload exceeds text-only capability")
            beat: dict[str, object] | None = beats[0] if len(beats) == 1 else None
            if len(beats) > 1:
                extra_beats = beats
        elif timing == "later":
            if (
                not (1 <= len(beats) <= SLIM_REPLY_ONLY_MAX_TEXT_BEATS)
                or not isinstance(delay_seconds, int)
                or isinstance(delay_seconds, bool)
                or not isinstance(expires_after_seconds, int)
                or isinstance(expires_after_seconds, bool)
            ):
                raise ValueError("reply_only slim payload exceeds text-only capability")
            beat = beats[0] if len(beats) == 1 else None
            if len(beats) > 1:
                extra_beats = beats
        elif timing == "silent":
            if beats or delay_seconds is not None or expires_after_seconds is not None:
                raise ValueError("reply_only slim payload exceeds text-only capability")
            beat = None
        else:
            raise ValueError("reply_only slim payload exceeds text-only capability")
        head = {
            "type": "head",
            "private_turn_state": expression["private_turn_state"],
            "timing_choice": timing,
            "turn_posture": None,
            "cadence": expression.get("cadence", "conversational"),
            "beat": beat,
            "stance": expression["stance"],
            "brief_rationale": expression["brief_rationale"],
            "confidence": expression["confidence"],
            "response_expectation": expression.get("response_expectation"),
            "response_expectation_assessment": expression.get(
                "response_expectation_assessment"
            ),
            "revisit": expression.get("revisit"),
            "world_claims": [],
            "media_request": "none",
            "media_source_refs": [],
        }
        if extra_beats is not None:
            head["beats"] = extra_beats
        if timing == "later":
            head["delay_seconds"] = delay_seconds
            head["expires_after_seconds"] = expires_after_seconds
    else:
        head = {
            "type": "head",
            "private_turn_state": expression["private_turn_state"],
            "timing_choice": expression["timing_choice"],
            "turn_posture": None,
            "cadence": expression.get("cadence", "conversational"),
            "beats": beats,
            "stance": expression["stance"],
            "brief_rationale": expression["brief_rationale"],
            "confidence": expression["confidence"],
            "response_expectation": expression.get("response_expectation"),
            "response_expectation_assessment": expression.get(
                "response_expectation_assessment"
            ),
            "revisit": expression.get("revisit"),
            "world_claims": [],
            "media_request": expression.get("media_request", "none"),
            "media_source_refs": list(expression.get("media_source_refs") or []),
        }
        if expression.get("timing_choice") == "later":
            head["delay_seconds"] = expression.get("delay_seconds")
            head["expires_after_seconds"] = expression.get("expires_after_seconds")
    return {
        "protocol": "character-interior-events.1",
        "appraisal_draft": compiled["appraisal_draft"],
        "events": [head, {"type": "end"}],
    }
