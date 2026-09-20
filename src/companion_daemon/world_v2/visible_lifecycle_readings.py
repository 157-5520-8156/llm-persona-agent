"""Narrow lifecycle evidence for factual readings, without outcome authority.

Only the explicit lifecycle source contract enables this permission. No prose
classification or conclusion is made here; the reviewer must still establish
that the complete proposition describes the selected lifecycle field.
"""
from .visible_meaning_source_review import _eligible_readings as legacy_eligible_readings

CONTRACT = "visible-lifecycle-field-permission.1"
LIFECYCLE_FIELDS = frozenset({
    "/item/value/status", "/item/value/started_at", "/item/value/ended_at",
})
INSTRUCTION = (
    'activity_lifecycle 的合格 reading 只证明同一 plan 的已记录状态或开始/结束时间。'
    'actual_event_or_state 包括纯生命周期陈述，所以可以用这些窄字段核对“活动开始了/结束了”。'
    '必须同时核对材料里的 owner、plan_id、原意图与时间，防止把另一活动的结束接到本命题。'
    '原意图只用于辨认这是哪项活动，不证明其中行动、地点、成功标准或细节已经实现。'
    '状态 completed 只说明该活动阶段结束，不证明完成整圈、已经回到某处、洗过澡、休息过，'
    '也不证明当时的情绪或动机；这些独立命题仍需各自有权支持的实际记录。'
    '不得把多个 lifecycle 字段组合成更广的执行结果，或把 accepted_intention 的正文当作结果。'
    '仍按完整原句审查所有嵌入事实与遗漏；权限合格不等于完整命题被支持。'
)


def eligible_readings(fact, catalog, *, lifecycle_scope=False):
    if type(lifecycle_scope) is not bool:
        raise TypeError("lifecycle scope must be boolean")
    choices = legacy_eligible_readings(fact, catalog)
    if lifecycle_scope and fact["mode"] == "actual_event_or_state":
        for reading in catalog:
            if (reading.get("pointer") in LIFECYCLE_FIELDS
                    and ["activity_lifecycle", fact["subject_role"]] in reading["permissions"]):
                choices[reading["reading_id"]] = "activity_lifecycle"
    return choices
