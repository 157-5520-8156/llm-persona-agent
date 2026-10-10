"""Shared provider reading limits; neither evidence nor a behavior policy.

This packet is attached before request hashing. It never modifies source item
schemas, expands permissions, or reconstructs an old provider request.
"""
from collections.abc import Mapping


def present_dialogue_reports(packet: dict[str, object]) -> dict[str, object]:
    """Type the derived transcript as reported speech, keeping all raw evidence.

    Only the current author presenter calls this. Canonical snapshots, legacy
    carriers and the source-reader's recent_dialogue records remain untouched.
    """
    snapshot = packet.get('inner_life_snapshot')
    materials = snapshot.get('materials') if isinstance(snapshot, dict) else None
    lines = materials.get('conversation') if isinstance(materials, dict) else None
    if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
        return packet
    return {**packet, 'inner_life_snapshot': {**snapshot, 'materials': {**materials,
        'conversation': {'contract': 'dialogue-report-view.1',
            'authority': 'recorded_utterance_only',
            'event_scope': 'said_at_recorded_time_not_proof_of_action_or_current_world',
            'lines': list(lines)},
    }}}


def context_reading_scope(snapshot: Mapping[str, object] | None = None) -> dict[str, object]:
    materials = snapshot.get("materials") if snapshot is not None else None
    selected = materials.get("selected_recall") if isinstance(materials, Mapping) else None
    recall_presentation = "presented" if isinstance(selected, Mapping) else "not_presented"
    if isinstance(materials, Mapping) and materials.get("contract") == "shared-string-view.1":
        # This transport is opaque until its existing decoder runs. Do not
        # mistake encoded material keys for absence of a recalled result.
        recall_presentation = "encoded_view"
    return {
        "contract": "model-context-reading.2",
        "history_coverage": "partial",
        "dialogue_authority": "recorded_utterances_only_not_world_execution",
        "selected_recall": recall_presentation,
        "rule": (
            "上下文是本次已提供的来源，不是完整人生；未提供、未检索到或片段未覆盖，不代表事情没发生。"
            "事实、过去主观记录、此刻回顾性理解分别按原来源和时间读取；"
            "角色可以自由形成现在的感受和理解，但它们不能自动成为记得的过去感受或新增事实。"
            "片段的概述、关联或下一页指针不等于已读到那些细节。"
            "旧对话能证明当时说过什么，不能直接证明动作已完成或今天又发生；"
            "行为结果按personal_action_evidence及有明确执行权限的来源读取。"
            "复述可自由换措辞；未记录的过去动作、共同反应或过程细节，不能用现在的感受补成往事。"
            "是否回忆、如何表达和如何选择仍由角色决定；这份说明不是事实来源或额外权限。"
        ),
    }
