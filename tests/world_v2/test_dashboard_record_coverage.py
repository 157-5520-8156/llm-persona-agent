"""No display gaps may masquerade as missing mechanisms or delivered messages."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as N

import pytest

from companion_daemon.world_v2.dashboard_expression_reading import (
    read_dashboard_expression,
    expectation_display_status,
)
from companion_daemon.world_v2.dashboard_mechanism_activity import mechanism_activity
from companion_daemon.world_v2.dashboard_home_snapshot import DashboardOperationsData, _metric

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def expression():
    plan = N(
        plan_id="plan",
        acceptance_id="accepted",
        proposal_id="proposal",
        expression_change_id="change",
    )
    beat = N(
        action=N(action_id="action"),
        text="已经送达的原文。",
        privacy_class="private",
        storage_kind="inline_text",
        content_type="text/plain",
        payload_ref="payload",
        payload_hash="hash",
    )
    manifest = N(
        **vars(plan),
        beats=[beat],
        acceptance_event_ref="event",
        acceptance_event_payload_hash="event-hash",
        recorded_at_world_revision=4,
    )
    event = N(
        event_id="event",
        event_type="AcceptanceRecorded",
        payload_hash="event-hash",
        world_revision=4,
        logical_time=NOW,
    )
    stored = N(
        acceptance_id="accepted",
        proposal_id="proposal",
        payload_ref="payload",
        payload_hash="hash",
        content_type="text/plain",
        text=beat.text,
    )
    projection = N(
        expression_plan_manifests=[manifest],
        committed_world_event_refs=[event],
        actions=[N(action_id="action", state="delivered")],
        stored_message_payloads=[stored],
    )
    return projection, plan


def test_exact_delivered_text_is_read_and_unrelated_store_content_is_ignored():
    projection, plan = expression()
    projection.stored_message_payloads.append(
        N(
            **{
                **vars(projection.stored_message_payloads[0]),
                "payload_ref": "different",
                "text": "OTHER_MESSAGE_CANARY",
            }
        )
    )
    reading = read_dashboard_expression(projection, plan)
    assert reading.status == "read" and reading.text == "已经送达的原文。"
    assert reading.occurred_at == NOW and reading.delivered_count == reading.shown_count == 1


@pytest.mark.parametrize(
    "fault",
    [
        "unaccepted",
        "wrong_plan",
        "hash",
        "type",
        "state",
        "withhold",
        "sidecar",
        "text",
        "missing_payload",
    ],
)
def test_unbound_private_or_undelivered_text_is_not_used_as_body(fault):
    projection, plan = expression()
    if fault == "unaccepted":
        projection.committed_world_event_refs = []
    elif fault == "wrong_plan":
        plan.acceptance_id = "other"
    elif fault == "hash":
        projection.committed_world_event_refs[0].payload_hash = "other"
    elif fault == "type":
        projection.committed_world_event_refs[0].event_type = "ClockAdvanced"
    elif fault == "state":
        projection.actions[0].state = "authorized"
    elif fault == "withhold":
        projection.expression_plan_manifests[0].beats[0].privacy_class = "withhold"
    elif fault == "sidecar":
        projection.expression_plan_manifests[0].beats[0].storage_kind = "sidecar"
    elif fault == "text":
        projection.stored_message_payloads[0].text = "UNBOUND_CANARY"
    else:
        projection.stored_message_payloads = []
    assert read_dashboard_expression(projection, plan).text is None


def test_partial_delivery_is_not_presented_as_the_complete_message():
    projection, plan = expression()
    second = deepcopy(projection.expression_plan_manifests[0].beats[0])
    second.action.action_id = "unsent-action"
    second.text = "UNSENT_CANARY"
    projection.expression_plan_manifests[0].beats.append(second)
    reading = read_dashboard_expression(projection, plan)
    assert reading.status == "partial" and reading.beat_count == 2 and reading.delivered_count == 1
    assert "UNSENT_CANARY" not in reading.text


def test_expectation_uses_matching_assessment_and_window_not_hardcoded_open():
    manifest = N(
        plan_id="plan",
        acceptance_event_ref="accepted",
        response_expectation=N(not_before=NOW, expires_at=NOW + timedelta(hours=1)),
    )
    projection = N(logical_time=NOW + timedelta(days=1), response_expectation_assessments=[])
    assert expectation_display_status(projection, manifest) == "expired"
    assessment = N(
        source_plan_id="plan",
        source_acceptance_event_ref="accepted",
        assessed_at=NOW,
        world_revision=1,
        status="fulfilled",
    )
    projection.response_expectation_assessments = [assessment]
    assert expectation_display_status(projection, manifest) == "fulfilled"
    assessment.source_acceptance_event_ref = "wrong-acceptance"
    assert expectation_display_status(projection, manifest) == "expired"


def test_terminal_process_is_counted_without_claiming_success_or_installation():
    projection = N(
        trigger_processes=[
            N(
                process_kind="silence_appraisal",
                state="terminal",
                attempt_ids=("one", "two"),
                runtime_outcome_ref="technical_failure:PRIVATE_DIAGNOSTIC",
            )
        ]
    )
    rows = mechanism_activity(projection)
    row = next(r for r in rows if r.key == "silence_appraisal")
    assert (row.recorded_count, row.terminal_count, row.attempt_count) == (1, 1, 2)
    assert "PRIVATE_DIAGNOSTIC" not in str(rows)
    zero = next(r for r in rows if r.key == "memory_consolidation_review")
    assert zero.recorded_count == 0 and "未安装" in zero.scope_note
    assert _metric("pending_actions", "待处理", []).count_note.startswith("当前没有待处理")


def test_old_owner_payload_does_not_gain_new_fields_on_hash_roundtrip():
    old = {
        "metrics": [{"key": "actions", "label": "行动", "count": 0}],
        "highlights": [],
        "notices": [],
    }
    assert (
        DashboardOperationsData.model_validate_json(json.dumps(old)).model_dump(mode="json") == old
    )


def test_experience_body_joins_its_exact_settlement_instead_of_latest_event():
    from companion_daemon.world_v2.dashboard_world_occurrence import experience_world_occurrence

    binding = N(
        source_kind="occurrence_settlement",
        occurrence_id="old-occurrence",
        authority_event_ref="settlement",
        authority_payload_hash="h",
        authority_world_revision=5,
        result_id="r",
        result_payload_ref="body",
        result_payload_hash="body-hash",
        occurrence_entity_revision=2,
    )
    occurrence = N(
        occurrence_id="old-occurrence",
        settlement_event_ref="settlement",
        result_id="r",
        result_payload_ref="body",
        result_payload_hash="body-hash",
        entity_revision=2,
    )
    other = N(**{**vars(occurrence), "occurrence_id": "newer-occurrence"})
    projection = N(
        world_occurrences=[other, occurrence],
        committed_world_event_refs=[
            N(
                event_id="settlement",
                event_type="WorldOccurrenceSettled",
                world_revision=5,
                payload_hash="h",
            )
        ],
    )
    experience = N(
        values=N(source_bindings=[N(source_kind="world_life_response", settlement=binding)])
    )
    assert experience_world_occurrence(projection, experience) is occurrence
    binding.result_payload_hash = "different"
    assert experience_world_occurrence(projection, experience) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'missing', 'future', 'audit_hash', 'withhold'])
async def test_pending_reason_uses_exact_accepted_author_chain(fault):
    from test_character_interior_world_stimulus import _RoleModel, _runtime, SOURCE_REF
    from companion_daemon.world_v2.dashboard_pending_reading import read_thread_reason

    reason = '想过一会儿再决定要不要主动联系。'
    model = _RoleModel(decision='no_change', experience_transition={
        'domain': 'thread', 'operation': 'open', 'target_id': None,
        'expected_entity_revision': 0, 'thread_kind': 'reply_reconsideration',
        'importance_bp': 6100, 'due_at': '2026-08-05T18:00:00Z',
        'expires_at': '2026-08-06T18:00:00Z', 'resolution_kind': None,
        'cancellation_reason_code': None, 'source_refs': [SOURCE_REF],
        'reason_summary': reason,
    })
    runtime, ledger, _ = _runtime(model=model)
    assert (await runtime.drain_one()).work_status == 'accepted'
    state = ledger.project()
    thread = state.threads[0]
    if fault == 'withhold':
        thread = thread.model_copy(update={'values': thread.values.model_copy(update={'privacy_class': 'withhold'})})
        state = state.model_copy(update={'threads': (thread,)})
    if fault == 'audit_hash':
        state = state.model_copy(update={'proposal_audits': tuple(
            a.model_copy(update={'event_payload_hash': 'wrong'}) for a in state.proposal_audits
        )})

    def lookup(ref):
        pair = ledger.lookup_event_commit(ref)
        if fault == 'missing':
            return None
        if fault == 'future' and pair:
            event, commit = pair
            return event, N(world_revision=state.world_revision+1, deliberation_revision=0, ledger_sequence=0)
        return pair

    value = read_thread_reason(ledger=N(lookup_event_commit=lookup), projection=state, thread=thread)
    assert value == (reason if fault is None else None)
