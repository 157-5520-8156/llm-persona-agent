"""Small typed semantic core, with the existing optional-capability dialect.

The host decodes representation only; it never supplies an omitted feeling,
interpretation, message, claim or capability choice.
"""

import json

CORE_FIELDS = ("messages", "meaning_of_this", "my_state", "world_claims")
CORE_KEYS = frozenset(CORE_FIELDS)


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def parameters(*, recall_allowed):
    text = {"type": "string"}
    claim = _object({"claim_text": text, "scope": {"type": "string", "enum": [
        "current_world", "past_world", "counterpart_history", "shared_history",
        "stable_identity", "subjective_or_hypothetical",
    ]}, "source_refs": {"type": "array", "items": text,
                       "description": "At most 8 exact supporting source refs per claim; choose relevant refs, never copy the whole catalog. Different factual propositions use separate claims."}})
    decision = _object({"result_kind": {"type": "string", "enum": ["decision"]},
        "messages": {"type": "array", "items": text}, "meaning_of_this": text,
        "my_state": text, "world_claims": {"type": "array", "items": claim},
        "extras_json": {"type": "string", "description": "One JSON object of selected optional slim capabilities; {} when none. Never repeat the native core fields."}})
    branches = [decision]
    if recall_allowed:
        branches.append(_object({"result_kind": {"type": "string", "enum": ["recall"]},
                                "payload_json": text}))
    return _object({"result": {"anyOf": branches} if recall_allowed else decision})


def decode(value, *, recall_allowed):
    # Observed provider bug: authored fields appear beside result instead of
    # inside it. Move only explicit, non-overlapping core/optional-container
    # fields; never manufacture a missing semantic value or accept unknowns.
    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        outer = set(value) - {"result"}
        if outer and outer <= (CORE_KEYS | {"extras_json"}) and not outer.intersection(value["result"]):
            value = {"result": {**value["result"], **{key: value[key] for key in outer}}}
    if not isinstance(value, dict) or set(value) != {"result"} or not isinstance(value["result"], dict):
        raise ValueError("native slim core requires exactly one result object")
    result = value["result"]
    if result.get("result_kind") == "recall":
        if not recall_allowed or set(result) != {"result_kind", "payload_json"}:
            raise ValueError("native slim recall is unavailable or ambiguous")
        return result
    if result.get("result_kind") != "decision" or set(result) not in (
        {*CORE_FIELDS, "result_kind", "extras_json"}, {*CORE_FIELDS, "result_kind"},
    ):
        raise ValueError("native slim decision requires all four authored core fields")
    if (not isinstance(result["messages"], list) or any(not isinstance(v, str) for v in result["messages"])
        or not isinstance(result["world_claims"], list)
        or any(not isinstance(result[k], str) or not result[k].strip() for k in ("meaning_of_this", "my_state"))):
        raise ValueError("native slim authored semantic core is invalid")
    # No optional fields were selected in a complete bare core. This is the
    # same legal omission as the original slim dialect, not a role fallback.
    extras_json = result.get("extras_json", "{}")
    if not isinstance(extras_json, str):
        raise ValueError("native slim extras must be one JSON object string")
    from .inbound_tool_contract import _loads_compact_gate_payload_object

    extras = _loads_compact_gate_payload_object(extras_json)
    if not isinstance(extras, dict) or (CORE_KEYS | {"result", "result_kind", "payload_json", "extras_json"}) & set(extras):
        raise ValueError("native slim extras cannot replace core fields or transport authority")
    payload = {**{key: result[key] for key in CORE_FIELDS}, **extras}
    return {"result_kind": "decision", "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}


def prompt(original):
    from .inbound_prompt import _SLIM_TRANSPORT

    if "本轮使用 native slim core：" in original:
        return original
    updated = original.replace(_SLIM_TRANSPORT, "")
    updated = updated.replace("character-inbound-prompt.6-slim\n", "character-inbound-prompt.7-slim-core\n", 1)
    updated = updated.replace("SLIM PAYLOAD_JSON 形状", "完整内部决定的字段示意")
    return updated + (
        "\n本轮使用 native slim core：函数参数只有 result 对象。decision分支直接写 "
        "result_kind、messages、meaning_of_this、my_state、world_claims；这四项必须由你自己填写，"
        "不能放入字符串或用null代替。其余你选择的可选能力只放extras_json这个JSON对象字符串，"
        "未选可选能力时写\"{}\"，不重复四项核心字段。recall分支的result只含result_kind和"
        "payload_json，后者沿用上方独立Recall结构。两分支不可混写。"
        "消息数量、内容、感受和是否Recall仍由你选择；messages=[]仍是你的沉默。"
        "收到role_result_correction时按同一证据和精确原因重新选择完整结果。"
    )
