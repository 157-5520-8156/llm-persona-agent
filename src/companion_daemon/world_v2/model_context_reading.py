"""Shared provider reading limits; neither evidence nor a behavior policy.

This packet is attached before request hashing. It never modifies source item
schemas, expands permissions, or reconstructs an old provider request.
"""
from collections.abc import Mapping


def present_context_evidence(packet: dict[str, object]) -> dict[str, object]:
    """Type the derived transcript as reported speech, keeping all raw evidence.

    Only the current author presenter calls this. Canonical snapshots, legacy
    carriers and the source-reader's recent_dialogue records remain untouched.
    """
    snapshot = packet.get('inner_life_snapshot')
    materials = snapshot.get('materials') if isinstance(snapshot, dict) else None
    lines = materials.get('conversation') if isinstance(materials, dict) else None
    if not isinstance(materials, dict):
        return packet
    presented = dict(materials)
    dialogue = materials.get('recent_dialogue')
    if isinstance(dialogue, dict) and isinstance(dialogue.get('stable_turns'), list):
        presented['recent_dialogue'] = {
            'record_scope': 'who_said_what_not_proof_of_described_events_or_current_activity',
            **dialogue,
        }
    if isinstance(lines, list) and all(isinstance(line, str) for line in lines):
        conversation = {'contract': 'dialogue-report-view.1',
            'authority': 'recorded_utterance_only',
            'event_scope': 'said_at_recorded_time_not_proof_of_action_or_current_world',
            'lines': list(lines)}
        links = _dialogue_display_links(snapshot, materials, lines)
        if links is not None:
            conversation = {'contract': 'dialogue-report-view.2',
                'authority': 'recorded_utterance_only',
                'read_from': 'recent_dialogue',
                'event_scope': 'same_utterances_not_independent_evidence_of_execution',
                'entries': links}
        presented['conversation'] = conversation
    if snapshot.get('contract') == 'inner-life-snapshot.1':
        # These are known derived native views, never additional witnesses.
        # Keep the registered source shapes and canonical snapshot intact.
        appraisals = materials.get('appraisals')
        if isinstance(appraisals, dict) and 'columns' in appraisals and 'rows' in appraisals:
            presented['appraisals'] = {**appraisals,
                'authority': 'character_interpretation_not_world_execution'}
        experiences = materials.get('recent_self_experiences')
        if isinstance(experiences, dict) and 'items' in experiences:
            presented['recent_self_experiences'] = {**experiences,
                'authority': 'field_scoped_world_records_not_automatic_personal_experience'}
        sources = [key for key in ('week_diary', 'appraisals', 'private_impressions') if materials.get(key)]
        if not sources and isinstance(materials.get('lived_moment'), str):
            sources = [
                key
                for key in ('biographical_context', 'affect', 'world_planning')
                if materials.get(key)
            ]
        if isinstance(materials.get('lived_moment'), str) and sources:
            walking_clauses = [
                clause.strip()
                for part in materials['lived_moment'].split('。')
                for clause in part.split('；')
                if clause.strip()
                and clause.strip().startswith(
                    (
                        '此刻正在：',
                        '手头正忙：',
                        '刚经历：',
                        '当下环境：',
                        '眼前环境余光：',
                        '近期身上悬着的处境：',
                        '此刻情绪余波：',
                        '今日近期课表/日程：',
                        '当前状态负荷：',
                        '已连续清醒',
                    )
                )
            ]
            lived_nav: dict[str, object] = {
                'contract': 'lived-material-links.1',
                'authority': 'navigation_only_not_additional_evidence',
                'source_fields': sources,
            }
            if walking_clauses:
                lived_nav['current_moment'] = '；'.join(walking_clauses)
            presented['lived_moment'] = lived_nav
    if presented == materials:
        return packet
    return {**packet, 'inner_life_snapshot': {**snapshot, 'materials': presented}}


def _dialogue_display_links(snapshot, materials, lines):
    """Match the native renderer exactly; unmatched display lines stay literal.

    This is lossless presentation deduplication, not interpretation of speech.
    Separate dialogue ids remain separate utterances even if their text agrees.
    """
    from collections import defaultdict, deque
    from .character_interior.contracts import _instant, _rendered_conversation

    dialogue = materials.get('recent_dialogue')
    turns = dialogue.get('stable_turns') if isinstance(dialogue, dict) else dialogue
    if not isinstance(turns, list):
        return None
    cursor = snapshot.get('cursor', {})
    at = _instant(cursor.get('logical_time')) if isinstance(cursor, dict) else None
    by_line = defaultdict(deque)
    for turn in turns:
        if not isinstance(turn, dict) or not isinstance(turn.get('dialogue_id'), str):
            continue
        rendered = _rendered_conversation({'recent_dialogue': [turn]}, at)
        if rendered:
            body = turn['text'].strip()
            by_line[rendered[0]].append({'source_ref': turn['dialogue_id'],
                'display_prefix': rendered[0][:-len(body)]})
    found = False
    entries = []
    for line in lines:
        if by_line[line]:
            entries.append(by_line[line].popleft())
            found = True
        else:
            entries.append({'line': line})
    return entries if found else None


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
