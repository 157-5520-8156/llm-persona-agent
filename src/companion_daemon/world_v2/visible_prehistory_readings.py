"""Exact historical narrative authority, separate from provenance metadata.

Callers first verify the original source binding. These field permissions are
ceilings only: the semantic reviewer must still match the named participant,
time, action and polarity against the complete retained text.
"""

from .visible_source_closure_protocol import _historical_memory, _historical_recall

CONTRACT = "retained-prehistory-field-authority.1"
AUTHORITY = "retained_character_prehistory_exact_excerpt_only"
INSTRUCTION = (
    "人生记忆只有目录中保留的原文可作为直接证据。记录ID、归档时间、hash、实体标签和"
    "关联记录仅限定来源身份与范围，不能补出原文未记载的经历。记忆所有者不等于每个命题的"
    "施事：companion只指记忆所有者，other只允许该条原文明确描述且participant_refs声明的"
    "历史人物或群体；必须按原文逐一核对其身份、动作、对象、时间和否定。历史人物不是当前"
    "用户或正在活动的NPC，历史原文不证明今天发生了同一事件。external_fact只支持原文中的"
    "历史事件，utterance_record只支持原文明确记录的过去发言；原文存在不等于命题成立。"
)


def prehistory_field_permissions(row: dict) -> dict[str, list[list[str]]] | None:
    """None means another source family; empty means no usable historical text."""
    material = row["review_material"]
    if material.get("authority") != AUTHORITY:
        return None
    value = material.get("item", {}).get("value")
    fields = {}
    if material.get("lane") == "recalled_prehistory":
        document = _historical_recall(value)
        if document is not None and document.actor_ref == document.prehistory.actor_ref:
            fields["/item/value/text"] = document.prehistory
    elif material.get("lane") == "active_memory_candidates":
        memory = _historical_memory(value)
        if memory is not None:
            fields = {
                f"/item/value/source_excerpts/{index}/text": source.prehistory
                for index, source in enumerate(memory.source_excerpts)
                if source.text and not source.truncated
            }
    permissions = {}
    for pointer, history in fields.items():
        if row.get("support_subject_role") != "companion" or row.get("support_subject_ref") != history.actor_ref:
            continue
        participants = {entity.entity_ref for entity in history.entities if entity.kind in {"person", "group"}}
        roles = ["companion"]
        if history.participant_refs and set(history.participant_refs) <= participants:
            roles.append("other")
        permissions[pointer] = [[scope, role] for scope in ("utterance_record", "external_fact") for role in roles]
    return permissions
