"""Chronology for a silence opportunity; no interpretation of another mind."""
from .silence_appraisal_trigger import _VISIBLE_MESSAGE_ACTION_KINDS, answerable_expression_anchors

SILENCE_CONTEXT_NOTE = (
    "This opportunity concerns the continuing reply gap at the CURRENT World time. "
    "The receipt is its historical delivery anchor, not new feedback from the counterpart. "
    "Read silence_observation for the total gap and intervening expression groups, not only "
    "the latest message's age. Delivery does not establish reading or offline facts, "
    "nor does it oblige the character to excuse the silence; she calibrates her present "
    "interpretation, feeling and next choice (including no change) by what was at stake, "
    "how long the gap has lasted, and how many expression groups went unanswered."
)


def _format_duration_cn(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "未知"
    if seconds < 60:
        return f"{seconds}秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{sec}秒" if sec else f"{minutes}分钟"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}小时{mins}分" if mins else f"{hours}小时"
    days, rem_hours = divmod(hours, 24)
    return f"{days}天{rem_hours}小时" if rem_hours else f"{days}天"


def silence_observation(projection, *, anchor_event_ref, actor_ref):
    base_instruction = (
        '这次机会关注发送后的回应空缺；旧送达回执仅代表消息已送达且尚无新回复（不将猜测的线下状态当作客观世界事实）。'
        '怎么感受和理解这段空缺，按刚才的话是否已自然聊完、你投入的心意与盼头分量、累计未回时长及连续未回轮数（unanswered_expression_groups）由你决定：'
        '普通闲聊空档无需小题大做（可维持 no_change），但若挂着在意的盼头或已连续多轮/跨天未回，不必无条件替对方找『在忙/在睡』的借口，也不要被早先1小时刚发完时写下的『不急/随他』旧印象绑架，可如实更新你的情绪（affect）与私人印象。'
    )
    base = {
        'contract': 'silence-observation.1',
        'scope': 'pinned_single_counterpart_channel_not_read_status_or_motive',
        'instruction': base_instruction,
    }
    now = projection.logical_time
    refs = {e.event_id: e for e in projection.committed_world_event_refs}
    anchor = refs.get(anchor_event_ref)
    receipt_refs = [e for e in projection.committed_world_event_refs if e.event_type == 'ExecutionReceiptRecorded']
    if now is None or anchor is None or len(receipt_refs) != len(projection.execution_receipts):
        return {**base, 'status': 'unavailable'}
    receipts = {e.event_id: r for e, r in zip(receipt_refs, projection.execution_receipts, strict=True)}
    receipt = receipts.get(anchor_event_ref)
    actions = {a.action_id: a for a in projection.actions}
    action = actions.get(receipt.action_id) if receipt is not None else None
    if (action is None or action.actor != actor_ref or action.kind not in _VISIBLE_MESSAGE_ACTION_KINDS
        or anchor.logical_time > now):
        return {**base, 'status': 'unavailable'}
    observations = projection.message_observations
    actors = {o.actor for o in observations}
    # This lane currently belongs to a private one-counterpart World. Never
    # merge unrelated people or incomplete legacy identities into silence.
    if len(actors) != 1 or None in actors:
        return {**base, 'status': 'unavailable'}
    latest = max(observations, key=lambda o: o.world_revision)
    incoming = next((e for e in refs.values() if e.event_type == 'ObservationRecorded'
                     and e.world_revision == latest.world_revision
                     and e.payload_hash == latest.event_payload_hash), None)
    if incoming is None or incoming.logical_time > now:
        return {**base, 'status': 'unavailable'}
    groups = answerable_expression_anchors(projection, actor_ref=actor_ref, target=action.target)
    # A delayed Beat from an expression begun before his reply is not a new
    # contact after that reply. Group before applying the inbound boundary.
    groups = {key: first for key, first in groups.items()
              if first.world_revision > latest.world_revision}
    first = min(groups.values(), key=lambda e: e.world_revision, default=None)
    last = max(groups.values(), key=lambda e: e.world_revision, default=None)
    sec_last_cp = int((now - incoming.logical_time).total_seconds())
    sec_first_unans = int((now - first.logical_time).total_seconds()) if first else None
    sec_latest_unans = int((now - last.logical_time).total_seconds()) if last else None
    n_groups = len(groups)
    live_summary = (
        f"【当前空档实况：距离对方上一次说话已过去 {_format_duration_cn(sec_last_cp)}；"
        f"这期间你已发出 {n_groups} 轮消息未获回复，"
        f"距第一条未回消息已过去 {_format_duration_cn(sec_first_unans)}，"
        f"距最近一条已过去 {_format_duration_cn(sec_latest_unans)}】"
    )
    return {
        **base,
        'instruction': live_summary + base_instruction,
        'status': 'available', 'observed_at': now.isoformat(),
        'anchor_receipt_at': anchor.logical_time.isoformat(),
        'anchor_delivery_state': receipt.observed_state,
        'seconds_since_anchor': int((now - anchor.logical_time).total_seconds()),
        'counterpart_message_since_anchor': latest.world_revision > anchor.world_revision,
        'seconds_since_last_counterpart_message': sec_last_cp,
        'unanswered_expression_groups': n_groups,
        'seconds_since_first_unanswered_expression': sec_first_unans,
        'seconds_since_latest_unanswered_expression': sec_latest_unans,
        'source_event_refs': list(dict.fromkeys([anchor_event_ref, incoming.event_id,
                              *([first.event_id, last.event_id] if first else [])])),
        'count_scope': 'distinct_expression_plans_provider_accepted_or_delivered_not_individual_beats',
    }
