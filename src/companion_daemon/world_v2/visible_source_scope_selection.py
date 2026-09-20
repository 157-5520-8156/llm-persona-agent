"""Narrow presentation by proved source permissions, never by candidate words.

The legacy selector omits only verified unused subjective history. The opt-in
context selector also admits other verified unused cards while preserving
dialogue and situation. Every usable reading remains whole. The original source
table stays pinned; this is neither a memory policy nor a semantic verdict.
"""
from .visible_meaning_source_review import _eligible_readings
from .visible_lifecycle_readings import eligible_readings
from .visible_source_closure_protocol import _eligible_reference
from .visible_subjective_source import AUTHORITY

CONTRACT = 'visible-subjective-permission-selection.1'
INSTRUCTION = (
    'source_selection_contract 描述按已验证字段权限选择的材料。只省略对所有 fixed_facts '
    '都没有支持权限的角色主观历史；不是删除记忆，也不是声称没有这些历史。'
    '所有固定命题原有的合格来源都保留，其他种类材料完整保留。'
    '如果完整原句中还有 fixed_facts 未覆盖的既往感受/经历或其他记录依赖前提，'
    '必须列在 unaccounted_record_bound_assertions，不能因材料未展示而把该前提改成无需记录。'
    '不能自行改换固定命题类别来调用未展示的来源。'
)

CONTEXT_CONTRACT = 'visible-permission-context-selection.1'
CONTEXT_INSTRUCTION = (
    'source_selection_contract 描述权限范围选择，不是语义判断。保留完整对话、当前处境和所有 '
    'fixed_facts 可使用的原始材料；其他已验证且没有支持权限的材料可能省略。'
    '未展示不表示不存在。逐句检查全部原文，发现 fixed_facts 未覆盖的记录依赖断言或预设时，'
    '必须列在 unaccounted_record_bound_assertions；不能因材料未展示而改判无需记录，'
    '也不能改换固定命题的类别、主体或时间来取得支持。歧义阻止判断时明确报告。'
)


def select_permission_context(*, witness_pin, catalog, facts, lifecycle_scope=False):
    """Keep discourse context and every permitted support, without ranking prose.

    Original source proofs remain pinned by the caller. Cards with unknown or
    invalid eligibility are retained. No candidate is approved by this selector:
    full-text model coverage and source authorization still run afterwards.
    """
    if type(lifecycle_scope) is not bool:
        raise TypeError('lifecycle scope must be boolean')
    def permitted(fact, readings):
        return eligible_readings(fact, readings, lifecycle_scope=lifecycle_scope)
    sources, indexes = witness_pin['sources'], witness_pin['material_indexes']
    shown = witness_pin['shown_materials']
    if len(sources) != len(indexes):
        raise ValueError('source selection requires complete original material mapping')
    needed = {reading for fact in facts for reading in permitted(fact, catalog)}
    omitted = []
    for index, material in enumerate(shown):
        if material.get('lane') in {'recent_dialogue', 'current_situation'}:
            continue
        refs = [row for row, position in zip(sources, indexes, strict=True) if position == index]
        eligible = [row for row in refs if row.get('support_eligibility') == 'eligible']
        readings = [r for r in catalog if r['material_index'] == index]
        if (eligible and readings and all(_eligible_reference(row) for row in eligible)
                and not any(r['reading_id'] in needed for r in readings)):
            omitted.append(index)
    retained = [i for i in range(len(shown)) if i not in omitted]
    selected = [r for r in catalog if r['material_index'] in retained]
    if any(permitted(f, selected) != permitted(f, catalog) for f in facts):
        raise ValueError('source selection changed a fixed fact permission set')
    return retained, selected, {
        'contract': 'visible-permission-context-selection.2' if lifecycle_scope else CONTEXT_CONTRACT, 'omitted_material_indexes': omitted,
        'retained_material_indexes': retained,
        'omission_basis': 'verified_no_fixed_fact_permission_outside_discourse_context',
        'fixed_fact_permissions_unchanged': True,
        'new_record_bound_assertions_require_reselection': True,
    }


def select_subjective_history(*, witness_pin, catalog, facts):
    """Return original material positions, a narrowed catalog and host proof."""
    sources = witness_pin['sources']
    indexes = witness_pin['material_indexes']
    shown = witness_pin['shown_materials']
    if len(sources) != len(indexes):
        raise ValueError('source selection requires complete original material mapping')
    needed = {reading for fact in facts for reading in _eligible_readings(fact, catalog)}
    omitted = []
    for index, material in enumerate(shown):
        refs = [row for row, position in zip(sources, indexes, strict=True) if position == index]
        readings = [r for r in catalog if r['material_index'] == index]
        eligible_refs = [row for row in refs if row.get('support_eligibility') == 'eligible']
        if (eligible_refs and readings and material.get('authority') == AUTHORITY
                and all(row['review_material'].get('authority') == AUTHORITY for row in refs)
                and all('subjective_history_support' in row and _eligible_reference(row) for row in eligible_refs)
                and all(r['permissions'] == [['subjective_history', 'companion']] for r in readings)
                and not any(r['reading_id'] in needed for r in readings)):
            omitted.append(index)
    retained = [i for i in range(len(shown)) if i not in omitted]
    selected = [r for r in catalog if r['material_index'] in retained]
    if any(_eligible_readings(f, selected) != _eligible_readings(f, catalog) for f in facts):
        raise ValueError('source selection changed a fixed fact permission set')
    return retained, selected, {
        'contract': CONTRACT, 'omitted_material_indexes': omitted, 'retained_material_indexes': retained,
        'omission_basis': 'validated_subjective_history_without_any_fixed_fact_permission',
        'fixed_fact_permissions_unchanged': True,
        'new_record_bound_assertions_require_reselection': True,
    }
