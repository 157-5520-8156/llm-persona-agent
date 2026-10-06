"""Ordered native carrier for v25, preserving its original validation rules.

Array position supplies serialization order, never a semantic classification.
Historical JSON responses and their immutable prepared requests remain intact.
"""

from typing import Literal
from functools import lru_cache
from copy import deepcopy

from pydantic import Field, create_model

from .schema_core import FrozenModel

LEGACY_CONTRACT = "grounded-review-ordered-wire.1"
V2_CONTRACT = "grounded-review-ordered-wire.2"
V3_CONTRACT = "grounded-review-ordered-wire.3"
CONTRACT = "grounded-review-ordered-wire.4"
REASONED_CONTRACT = "grounded-review-ordered-wire.5"
LEGACY_INSTRUCTION = (
    "输出使用单个按原文顺序排列的 segments 数组，不再输出 facts、non_record_expressions 或编号。"
    "每段由你判断 kind=fact 或 non_record：fact 保留事实命题、来源和支持判断；non_record 保留理由。"
    "同一段不要重复归类。按数组顺序拼接 text 必须完整覆盖该 Beat 的字词和标点。"
    "保留原文空格；若仅段落边界空白被省略，宿主会从原气泡无损绑定它们，不会补字或补标点。"
    "例如一段同时含事实与当前回应时，在适当位置切分，不能先把整句算一次、再把其中的回应算一次。"
    "仍需完整阅读语境并核对每一项事实。通过指定工具提交一次完整结果。"
)


INSTRUCTION = LEGACY_INSTRUCTION + (
    "根对象只输出 beat_decisions；请求中的 wire_carrier 是宿主标签，不要把它或 contract 加进工具参数。"
    "此刻发生也不豁免外部感知、位置和身体行为。看过实际场景、走到某处、取回物品等仍是需要来源的事实；"
    "对这些事的即时评价或想象才可能属于 non_record。不能把动作和感知整体称为当前主观感受来豁免。"
    "意愿中的目标不等于它已存在、可用或已实现：想听窗外的声音不证明已经听见某种声音。"
    "不要把未决意愿补成环境事实；独立断言的场景和已发生的感知仍要核对来源。"
)

REASONED_INSTRUCTION = INSTRUCTION + (
    "本版本每段先写text和rationale，忠实说明原话的意义与证据义务，再在assessment选择kind和对应字段；"
    "不要先选fact再为它找理由。text和rationale不在assessment里重复。review_complete放在全部segments之后。"
    "审核不是尽可能找出可疑命题。角色此刻表达自己的感受、态度、解释、猜测、选择和今后想做什么，"
    "不需要已有同一情绪或计划记录；这类内容归入non_record。accepted_intention只核验关于已有意图记录的陈述，"
    "不是角色现在形成和表达新意愿的准入条件。新意愿可以有明确目标和未来时间，但不证明实施、成功或获准执行。"
    "普通评价、修辞和概括不自动承担新的场景、位置、统计频率、过去心理状态或他人动机；"
    "不得先加强或补出原句没有的命题再以缺少证据拒绝。"
    "当前作者权限仅覆盖主观感受、态度、解释和选择，不覆盖物理世界。"
    "现在进行的动作、实际位置、物品状态和正在发生的外部感知，与过去动作一样需要来源。"
    "混合原句中主观部分保留，独立的物理断言另列为事实核验，不能整句豁免。"
    "具体过去感受、意图、已执行动作、通信以及他人行为和独立未来事件仍需相应来源；"
    "不能把已经发生的事改成打算去做，不许借作者权限证明外部事实或遗漏原文。"
)


def expand_response_references(value, bindings):
    """Decode only identifier slots, never prose or an exact quoted value."""
    from copy import deepcopy
    from .reference_wire import expand_reference_values

    result = deepcopy(value)
    slots = []

    def collect(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in {"reading_ids", "reading_id", "subject_ref"}:
                    slots.append((node, key, child))
                else:
                    collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(result)
    # One validation of the original request-local dictionary for all slots.
    values = expand_reference_values([slot[2] for slot in slots], bindings)
    for (node, key, _), expanded in zip(slots, values, strict=True):
        node[key] = expanded
    return result


@lru_cache(maxsize=5)
def response_model(carrier=CONTRACT):
    # Lazy import avoids coupling the frozen v24/v25 schemas to the new wire.
    from .visible_grounded_review import _Fact, _NonRecordExpression

    class FactSegment(_Fact):
        kind: Literal["fact"]

    class NonRecordSegment(_NonRecordExpression):
        kind: Literal["non_record"]

    class Beat(FrozenModel):
        beat_index: int = Field(ge=0, le=15)
        review_complete: bool
        segments: tuple[FactSegment | NonRecordSegment, ...] = Field(min_length=1, max_length=32)
        unresolved_details: tuple[str, ...] = Field(max_length=16)

    if carrier == REASONED_CONTRACT:
        FactAssessment = create_model(
            "GroundedFactAssessment", __base__=FrozenModel,
            kind=(Literal["fact"], ...),
            **{name: (field.annotation, deepcopy(field)) for name, field in _Fact.model_fields.items()
               if name not in {"text", "rationale"}},
        )
        class NonRecordAssessment(FrozenModel):
            kind: Literal["non_record"]
        class ReasonedSegment(FrozenModel):
            text: str = Field(min_length=1, max_length=4096)
            rationale: str = Field(min_length=1, max_length=1024)
            assessment: FactAssessment | NonRecordAssessment
        class ReasonedBeat(FrozenModel):
            beat_index: int = Field(ge=0, le=15)
            segments: tuple[ReasonedSegment, ...] = Field(min_length=1, max_length=32)
            review_complete: bool
        class Response(FrozenModel):
            beat_decisions: tuple[ReasonedBeat, ...] = Field(min_length=1, max_length=16)
    elif carrier == LEGACY_CONTRACT:
        class Response(FrozenModel):
            contract: Literal["grounded-review-ordered-wire.1"]
            beat_decisions: tuple[Beat, ...] = Field(min_length=1, max_length=16)
    elif carrier == V2_CONTRACT:
        class Response(FrozenModel):
            beat_decisions: tuple[Beat, ...] = Field(min_length=1, max_length=16)
    elif carrier in {V3_CONTRACT, CONTRACT}:
        class CompactBeat(Beat):
            # .3 uses the explicit complete flag as its exhaustive verdict.
            # If an older-shaped diagnostic is supplied it still fails closed.
            unresolved_details: tuple[str, ...] = Field(default=(), max_length=16)
        class Response(FrozenModel):
            beat_decisions: tuple[CompactBeat, ...] = Field(min_length=1, max_length=16)
    else:
        raise ValueError("unsupported grounded review wire carrier")
    return Response


def tool_request(carrier=CONTRACT):
    from .character_interior.inbound_tool_contract import _inline_refs, deepseek_strict_tool_schema

    schema = response_model(carrier).model_json_schema()
    schema = _inline_refs(schema, schema.get("$defs", {}))
    # Choose the union branch before generating its fields, rather than asking
    # an autoregressive provider to decide the branch after the fact payload.
    segment_schema = schema["properties"]["beat_decisions"]["items"]["properties"]["segments"]["items"]
    choices = segment_schema["properties"]["assessment"] if carrier == REASONED_CONTRACT else segment_schema
    for branch in choices["anyOf"]:
        properties = branch["properties"]
        branch["properties"] = {"kind": properties["kind"], **{k: v for k, v in properties.items() if k != "kind"}}
        branch["required"] = list(branch["properties"])
    if carrier in {V3_CONTRACT, CONTRACT}:
        beat = schema["properties"]["beat_decisions"]["items"]
        beat["properties"].pop("unresolved_details")
    tool_name = {LEGACY_CONTRACT: "review_grounded_segments_v1", V2_CONTRACT: "review_grounded_segments_v2",
                 V3_CONTRACT: "review_grounded_segments_v3", CONTRACT: "review_grounded_segments_v4",
                 REASONED_CONTRACT: "review_grounded_segments_v5"}[carrier]
    return {
        "tools": [{"type": "function", "function": {
            "name": tool_name, "description": "Review every original Beat with ordered source-bound segments.",
            "strict": True, "parameters": deepseek_strict_tool_schema(schema),
        }}],
        "tool_choice": {"type": "function", "function": {"name": tool_name}},
    }


def decode(raw, *, beats=None, carrier=CONTRACT):
    """Lossless carrier decoding; all v25 semantic/coverage checks run afterward."""
    value = response_model(carrier).model_validate_json(raw, strict=True)
    originals = {item["beat_index"]: item["text"] for item in beats} if beats is not None else None
    beats = []
    for beat in value.beat_decisions:
        facts, non_record = [], []
        cursor = 0
        original = originals[beat.beat_index] if originals is not None else None
        parts = []
        for index, segment in enumerate(beat.segments):
            if carrier == REASONED_CONTRACT:
                kind = segment.assessment.kind
                part = {"text": segment.text, "rationale": segment.rationale,
                        **segment.assessment.model_dump(mode="json", exclude={"kind"})}
            else:
                kind = segment.kind
                part = segment.model_dump(mode="json", exclude={"kind"})
            if carrier in {CONTRACT, REASONED_CONTRACT} and kind == "fact":
                # A full value selection already names this reading. Repeating
                # its ID in the generic list adds no evidence. Keep the full
                # selection so exact quote/owner/scope validation still runs;
                # never turn a plain ID into a value selection or collapse
                # conflicting/duplicate full selections. Raw bytes stay audited.
                value_ids = {selection["reading_id"] for selection in part["fact_value_selections"]}
                part["reading_ids"] = [ref for ref in part["reading_ids"] if ref not in value_ids]
            part["segment_index"] = index
            if original is not None:
                start = cursor
                # Source-bound lossless whitespace carrier, not a fuzzy match:
                # no word, punctuation, reordering or overlap may be repaired.
                while not original.startswith(part["text"], cursor) and cursor < len(original) and original[cursor] in " \t\r\n\u3000":
                    cursor += 1
                if not original.startswith(part["text"], cursor):
                    raise ValueError(f"ordered segment does not match Beat {beat.beat_index} at character {cursor}")
                cursor += len(part["text"])
                part["text"] = original[start:cursor]
            parts.append(part)
            (facts if kind == "fact" else non_record).append(part)
        if original is not None and cursor < len(original):
            tail = original[cursor:]
            if not tail or any(char not in " \t\r\n\u3000" for char in tail):
                raise ValueError("ordered review omits original Beat content")
            parts[-1]["text"] += tail
        beats.append({"beat_index": beat.beat_index, "review_complete": beat.review_complete,
                      "facts": facts, "non_record_expressions": non_record,
                      "unresolved_details": list(getattr(beat, "unresolved_details", ()))})
    return {"contract": "visible-grounded-review.2", "beat_decisions": beats}
