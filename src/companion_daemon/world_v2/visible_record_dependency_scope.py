"""Model-owned record dependency, separate from grammatical assertion.

Only the new contextual contract uses this vocabulary. No local inspection of
the character's words or reviewer explanation assigns the dependency class.
"""

INSTRUCTION = (
    '对每个 fact_id 判断 record_dependency：record_bound 表示完整语境实际断言或预设了'
    '需要既有 World、记忆或通信记录支持的命题，包括外部事件、实际行为、独立先前的内心、'
    '持续状态、习惯以及过去言语。not_record_bound 表示这个命题不需要既有记录：'
    '它可能是本次形成的感受、思考、意愿或对当前发言本身的说明，也可能只是假设、'
    '可能性或未作断言的引语。uncertain 表示无法确定记录依赖范围。'
    '不要因为没有来源而选择 not_record_bound；这由原句含义决定。语法上肯定或否定地'
    '陈述一件事，不等于它需要先前记录。当前表达也不能把它所指的独立事件变成当前创作。'
    '引语及当前元话语的豁免不证明引语内容为真，也不证明过去曾经说过或送达过它。'
    '若复合命题中夹带记录依赖前提，不能整条免审；保持原命题坐标并在逐气泡检查中'
    '单独列出未覆盖前提。记录要求和引用权限是两个问题：not_record_bound 不产生'
    '新的 World 事实、记忆或引用权限。'
)


def configure_record_dependency(*, instruction, fact_item):
    """Compile a new wire contract; old assertion contracts remain unchanged."""
    old_scope = (
        '对每个 fact_id 判断 assertion_status：asserted 表示原句确实断言或预设这个命题；'
        'not_asserted 表示整条命题只在假设、可能性、未知问题答案或当下自主感受/意愿中出现，原句没有断言其发生；'
        'uncertain 表示无法确定范围。不要因为来源缺失而把 asserted 降成 not_asserted。'
    )
    if instruction.count(old_scope) != 1:
        raise ValueError('record dependency compiler requires its original scope paragraph')
    instruction = instruction.replace(old_scope, INSTRUCTION)
    instruction = instruction.replace('not_asserted', 'not_record_bound').replace('asserted', 'record_bound')
    instruction = instruction.replace(
        'not_record_bound 必须解释为何原句不承担这个事实承诺。',
        'not_record_bound 必须解释为何该命题无需既有记录，不要求否认它正在表达一个当前想法或元话语。',
    ).replace(
        '若分类本身错误又不能确认为不承担事实承诺，返回 uncertain，不能靠变更权限放行。',
        '若不能确定是否依赖既有记录，返回 uncertain，不能靠变更权限放行。',
    )
    properties = fact_item['properties']
    del properties['assertion_status']
    properties['record_dependency'] = {
        'type': 'string', 'enum': ['record_bound', 'not_record_bound', 'uncertain'],
        'description': '按完整原句判断该命题是否需要既有记录；当前表达本身与其中独立的历史或外部前提分别检查。',
    }
    fact_item['required'] = list(properties)
    return instruction
