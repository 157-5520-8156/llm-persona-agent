"""An index of already-present typed result fields, not new fact authority."""
from ..shared_string_view import unpack_shared_strings


def personal_action_evidence(snapshot):
    if not isinstance(snapshot, dict):
        return None
    materials = snapshot.get('materials', {})
    if isinstance(materials, dict) and materials.get('contract') == 'shared-string-view.1':
        materials = unpack_shared_strings(materials)
    if not isinstance(materials, dict):
        return None
    found, seen = [], set()

    def visit(value, source_ref=None):
        if isinstance(value, list):
            for item in value:
                visit(item, source_ref)
        elif isinstance(value, dict):
            ref = value.get('source_ref', source_ref)
            consequence = value.get('world_consequence')
            result = consequence.get('authorized_attempt_result') if isinstance(consequence, dict) else None
            if isinstance(result, dict):
                text, binding = result.get('text'), result.get('execution_binding', {})
                if isinstance(text, str) and text and binding.get('actor_ref') == snapshot.get('actor_ref'):
                    key = (ref, text)
                    if key not in seen:
                        seen.add(key)
                        found.append({'source_ref': ref, 'result_text': text})
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item, ref)

    for key in ('recent_self_experiences', 'week_diary', 'remembered_material'):
        visit(materials.get(key))
    return {
        'contract': 'personal-attempt-result-index.1',
        'scope': 'index_of_visible_result_fields_only_not_complete_life_history',
        'results': found,
        'instruction': '这是当前材料里可定位的个人行动结果。按结果原意使用，失败或未证实不等于成功。'
            '回顾自己实际做过什么时，环境、原计划、活动结束和过去说过的话都不能替代执行结果。'
            '其他有明确权限的事实、生平记录仍可用；列表为空不证明什么都没发生，也不授权补写经历。'
            '对自己现在的感受和想法可直接表达，不必为了接话补一个刚做过的动作。',
    }
