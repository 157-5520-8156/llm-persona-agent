"""Pinned process counts, explicitly not installation or success claims."""
from pydantic import Field

from .schema_core import FrozenModel


_LABELS = {
    'life_ecology': '生活生态调度',
    'npc_world_appraisal': '生活事件认知',
    'interaction_appraisal': '对话情境评估',
    'interaction_fact': '对话事实提取',
    'life_reflection': '生活反思',
    'private_impression_deliberation': '私人印象形成',
    'memory_candidate_review': '记忆来源撤回复核',
    'memory_consolidation_review': '周期记忆复核',
    'proactive_action_deliberation': '主动联系考虑',
    'silence_appraisal': '未回复后的评估',
    'expression_reconsideration': '表达重新考虑',
    'perception_deliberation': '外部感知请求',
    'perception_result_deliberation': '外部感知结果处理',
    'media_continuation': '媒体后续处理',
    'relationship_adjustment': '关系调整',
}


class DashboardMechanismActivity(FrozenModel):
    key: str
    label: str
    recorded_count: int = Field(ge=0)
    open_count: int = Field(ge=0)
    claimed_count: int = Field(ge=0)
    terminal_count: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    scope_note: str


def mechanism_activity(projection):
    rows = []
    for kind, label in _LABELS.items():
        processes = [p for p in projection.trigger_processes if p.process_kind == kind]
        rows.append(DashboardMechanismActivity(
            key=kind, label=label, recorded_count=len(processes),
            open_count=sum(p.state == 'open' for p in processes),
            claimed_count=sum(p.state == 'claimed' for p in processes),
            terminal_count=sum(p.state == 'terminal' for p in processes),
            attempt_count=sum(len(p.attempt_ids) for p in processes),
            scope_note=('仅计入账本的流程；调度侧保存的空转、等待与租约不在此表。'
                        if kind == 'life_ecology' else '仅统计本类别的持久流程；未记录不等于未安装。'),
        ))
    return tuple(rows)


ZERO_NOTES = {
    'locations': '没有独立位置状态记录；已有场景或计划地点不计入此项。',
    'resources': '没有独立资源状态记录，不能据此推断身体或精力正常。',
    'goals': '没有独立目标记录；生活计划与愿望分别计数。',
    'attentions': '没有独立注意力状态记录，不等于模型没有注意到上下文。',
    'prehistory_archives': '本世界尚无启动前人生档案记录。',
    'prehistory_records': '本世界尚无启动前历史片段记录。',
    'interaction_fact_decisions': '此项只计当前结构的提取决定；历史事实需同时看事实条目。',
    'pending_actions': '当前没有待处理行动，不表示从未发送过消息。',
    'failed_actions': '当前行动状态中没有失败项；历史失败另看失败回执。',
}
