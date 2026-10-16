from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as N

from companion_daemon.world_v2.silence_observation import silence_observation

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def projection():
    incoming = N(
        event_id="user-event",
        event_type="ObservationRecorded",
        world_revision=1,
        payload_hash="user-hash",
        logical_time=NOW - timedelta(days=3),
    )
    refs = [incoming]
    receipts = []
    actions = []
    for index, plan in enumerate(["first", "first", "second"], start=2):
        at = NOW - timedelta(days=2) if plan == "first" else NOW - timedelta(hours=1)
        refs.append(
            N(
                event_id=f"receipt-{index}",
                event_type="ExecutionReceiptRecorded",
                world_revision=index,
                payload_hash="h",
                logical_time=at,
            )
        )
        receipts.append(N(action_id=f"action-{index}", observed_state="delivered"))
        actions.append(
            N(
                action_id=f"action-{index}",
                actor="companion",
                target="user",
                kind="reply",
                expression_plan_id=plan,
            )
        )
    return N(
        logical_time=NOW,
        committed_world_event_refs=refs,
        execution_receipts=receipts,
        actions=actions,
        message_observations=[N(actor="user", world_revision=1, event_payload_hash="user-hash")],
    )


def test_new_message_does_not_reset_total_silence_and_beats_are_not_extra_contacts():
    p = projection()
    view = silence_observation(p, anchor_event_ref="receipt-4", actor_ref="companion")
    assert view["status"] == "available"
    assert view["seconds_since_anchor"] == 3600
    assert view["seconds_since_last_counterpart_message"] == 3 * 86400
    assert view["seconds_since_first_unanswered_expression"] == 2 * 86400
    assert view["unanswered_expression_groups"] == 2
    assert view["counterpart_message_since_anchor"] is False
    assert not {"mood", "emotion", "ignore", "reply_choice"}.intersection(view)


def test_transport_updates_neither_duplicate_nor_reset_an_expression():
    p = projection()
    p.committed_world_event_refs.append(
        N(
            event_id="receipt-5",
            event_type="ExecutionReceiptRecorded",
            world_revision=5,
            payload_hash="h",
            logical_time=NOW,
        )
    )
    p.execution_receipts.append(N(action_id="action-4", observed_state="delivered"))
    view = silence_observation(p, anchor_event_ref="receipt-5", actor_ref="companion")
    assert view["unanswered_expression_groups"] == 2
    assert view["seconds_since_latest_unanswered_expression"] == 3600


def test_new_reply_is_reported_even_when_old_silence_trigger_is_still_pending():
    p = projection()
    p.committed_world_event_refs.append(
        N(
            event_id="new-user",
            event_type="ObservationRecorded",
            world_revision=5,
            payload_hash="new-hash",
            logical_time=NOW - timedelta(minutes=2),
        )
    )
    p.message_observations.append(N(actor="user", world_revision=5, event_payload_hash="new-hash"))
    view = silence_observation(p, anchor_event_ref="receipt-4", actor_ref="companion")
    assert view["counterpart_message_since_anchor"] is True
    assert view["unanswered_expression_groups"] == 0
    assert view["seconds_since_first_unanswered_expression"] is None


def test_reply_between_beats_does_not_turn_the_later_beat_into_another_contact():
    p = projection()
    # The first group's two Beats straddle an incoming message.
    p.committed_world_event_refs[2].world_revision = 4
    p.committed_world_event_refs[3].world_revision = 5
    p.committed_world_event_refs.append(
        N(
            event_id="reply-between",
            event_type="ObservationRecorded",
            world_revision=3,
            payload_hash="between-hash",
            logical_time=NOW - timedelta(days=2),
        )
    )
    p.message_observations.append(
        N(actor="user", world_revision=3, event_payload_hash="between-hash")
    )
    view = silence_observation(p, anchor_event_ref="receipt-4", actor_ref="companion")
    assert view["unanswered_expression_groups"] == 1


def test_other_recipient_or_actor_does_not_count_and_ambiguous_inbound_is_unavailable():
    p = projection()
    p.actions[0].target = "other"
    p.actions[1].actor = "other"
    assert (
        silence_observation(p, anchor_event_ref="receipt-4", actor_ref="companion")[
            "unanswered_expression_groups"
        ]
        == 1
    )
    p.message_observations.append(N(actor="other", world_revision=0, event_payload_hash="h"))
    assert (
        silence_observation(p, anchor_event_ref="receipt-4", actor_ref="companion")["status"]
        == "unavailable"
    )


def _opportunity_projection():
    p = projection()
    p.world_id = 'world:silence'
    p.trigger_processes = []
    for i, receipt in enumerate(p.execution_receipts, start=2):
        receipt.receipt_id = f'provider:{i}'
    return p


def test_later_verification_does_not_reopen_an_already_appraised_expression():
    from companion_daemon.world_v2.silence_appraisal_trigger import silence_appraisal_opportunity
    from companion_daemon.world_v2.batch_invariants import silence_appraisal_trigger_identity
    p = _opportunity_projection()
    first = silence_appraisal_opportunity(p, idle_seconds_threshold=60)
    p.trigger_processes.append(N(trigger_id=first.trigger_id, process_kind='silence_appraisal',
                                 source_evidence_ref=first.source_evidence_ref))
    p.committed_world_event_refs.append(N(event_id='late-verification', event_type='ExecutionReceiptRecorded',
                                         world_revision=6, logical_time=NOW - timedelta(minutes=2)))
    p.execution_receipts.append(N(action_id='action-4', observed_state='delivered', receipt_id='late'))
    assert silence_appraisal_opportunity(p, idle_seconds_threshold=60) is None
    # An old deployment may already have appraised the later receipt instead.
    p.trigger_processes[:] = [N(trigger_id=silence_appraisal_trigger_identity(p.world_id, 'late-verification'),
                                process_kind='silence_appraisal', source_evidence_ref='late-verification')]
    assert silence_appraisal_opportunity(p, idle_seconds_threshold=60) is None


def test_failed_or_unknown_send_is_not_evidence_of_unanswered_contact():
    from companion_daemon.world_v2.silence_appraisal_trigger import silence_appraisal_opportunity
    for status in ('failed', 'unknown'):
        p = _opportunity_projection()
        for i in range(2, 5):
            p.committed_world_event_refs.append(N(event_id=f'failure-{i}', event_type='ExecutionReceiptRecorded',
                                                 world_revision=6+i, logical_time=NOW - timedelta(minutes=2)))
            p.execution_receipts.append(N(action_id=f'action-{i}', observed_state=status, receipt_id=f'failure-{i}'))
        assert silence_appraisal_opportunity(p, idle_seconds_threshold=60) is None


def test_first_failed_beat_cannot_move_expression_start_past_a_reply():
    from companion_daemon.world_v2.silence_appraisal_trigger import silence_appraisal_opportunity
    p = _opportunity_projection()
    p.actions = p.actions[:2]
    p.execution_receipts = p.execution_receipts[:2]
    p.committed_world_event_refs = p.committed_world_event_refs[:3]
    p.committed_world_event_refs[2].world_revision = 4
    p.committed_world_event_refs.append(N(event_id='answer-between', event_type='ObservationRecorded',
                                         world_revision=3, payload_hash='answer-hash', logical_time=NOW-timedelta(days=2)))
    p.message_observations.append(N(actor='user', world_revision=3, event_payload_hash='answer-hash'))
    p.committed_world_event_refs.append(N(event_id='unknown-first', event_type='ExecutionReceiptRecorded',
                                         world_revision=5, logical_time=NOW-timedelta(hours=1)))
    p.execution_receipts.append(N(action_id='action-2', observed_state='unknown', receipt_id='unknown'))
    assert silence_appraisal_opportunity(p, idle_seconds_threshold=60) is None
    gap = silence_observation(p, anchor_event_ref='receipt-3', actor_ref='companion')
    assert gap['unanswered_expression_groups'] == 0


def test_proactive_gap_requires_the_current_actor_and_conversation():
    from companion_daemon.world_v2.proactive_action import compile_proactive_self_history_advisories
    p = _opportunity_projection()
    for a in p.actions:
        a.actor = 'another-actor'
        a.target = 'another-target'
    assert compile_proactive_self_history_advisories(p) == ()
    assert compile_proactive_self_history_advisories(p, actor_ref='companion', target='user') == ()
    from companion_daemon.world_v2.silence_appraisal_trigger import silence_appraisal_opportunity
    assert silence_appraisal_opportunity(p, idle_seconds_threshold=60, actor_ref='companion', target='user') is None
