"""Chronology for a silence opportunity; no interpretation of another mind."""
from .response_expectation_view import answerable_receipt_history
from .silence_appraisal_trigger import _VISIBLE_MESSAGE_ACTION_KINDS

SILENCE_CONTEXT_NOTE = (
    "This opportunity concerns the continuing reply gap at the CURRENT World time. "
    "The receipt is its historical delivery anchor, not new feedback from the counterpart. "
    "Read silence_observation for the total gap and intervening expression groups, not only "
    "the latest message's age. Delivery does not establish reading or motive. "
    "The character owns the present interpretation, feeling and next choice, including no change."
)


def silence_observation(projection, *, anchor_event_ref, actor_ref):
    base = {
        'contract': 'silence-observation.1',
        'scope': 'pinned_single_counterpart_channel_not_read_status_or_motive',
        'instruction': '这次机会关注发送后的回应空缺；旧送达回执不是对方的新回应。'
                       '未观察到回复不证明对方已读、故意忽略或不在意。'
                       '这些时间和记录只提供处境，感受、理解和后续行动仍由你决定。',
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
    groups = {}
    for action_id, (first, _last) in answerable_receipt_history(projection).items():
        sent = actions.get(action_id)
        if (sent is None or sent.actor != actor_ref or sent.target != action.target
            or sent.kind not in _VISIBLE_MESSAGE_ACTION_KINDS
            or first.logical_time > now):
            continue
        # Several Beats and repeated transport acknowledgements are one
        # authored expression, never several separate attempts at contact.
        key = sent.expression_plan_id or sent.action_id
        previous = groups.get(key)
        if previous is None or first.world_revision < previous.world_revision:
            groups[key] = first
    # A delayed Beat from an expression begun before his reply is not a new
    # contact after that reply. Group before applying the inbound boundary.
    groups = {key: first for key, first in groups.items()
              if first.world_revision > latest.world_revision}
    first = min(groups.values(), key=lambda e: e.world_revision, default=None)
    last = max(groups.values(), key=lambda e: e.world_revision, default=None)
    return {
        **base, 'status': 'available', 'observed_at': now.isoformat(),
        'anchor_receipt_at': anchor.logical_time.isoformat(),
        'anchor_delivery_state': receipt.observed_state,
        'seconds_since_anchor': int((now - anchor.logical_time).total_seconds()),
        'counterpart_message_since_anchor': latest.world_revision > anchor.world_revision,
        'seconds_since_last_counterpart_message': int((now - incoming.logical_time).total_seconds()),
        'unanswered_expression_groups': len(groups),
        'seconds_since_first_unanswered_expression': int((now - first.logical_time).total_seconds()) if first else None,
        'seconds_since_latest_unanswered_expression': int((now - last.logical_time).total_seconds()) if last else None,
        'source_event_refs': list(dict.fromkeys([anchor_event_ref, incoming.event_id,
                              *([first.event_id, last.event_id] if first else [])])),
        'count_scope': 'distinct_expression_plans_provider_accepted_or_delivered_not_individual_beats',
    }
