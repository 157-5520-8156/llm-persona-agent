from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from companion_daemon.world_v2.life_aftermath_runtime import LifeAftermathRuntime
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore,
    StoredLifeContent,
)
from companion_daemon.world_v2.life_events import ActivityPlannedPayload, ActivityTransitionPayload
from companion_daemon.world_v2.occurrence_content_coordinator import OccurrenceContentCoordinator
from companion_daemon.world_v2.schemas import DueWindow, EvidenceRef
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_biographical_lifecycle import (
    _CapturingLongLivedOutcomeModel,
    _commit_active_long_lived_occurrence,
    _commit_event,
    _event,
)
from test_life_ecology_activity import _plan


SHANGHAI = ZoneInfo("Asia/Shanghai")
START = datetime(2026, 9, 7, 9, 12, tzinfo=SHANGHAI)
EARLY = datetime(2026, 9, 7, 9, 27, tzinfo=SHANGHAI)
END = datetime(2026, 9, 7, 12, 0, tzinfo=SHANGHAI)


def _advance_clock(ledger, at):  # type: ignore[no-untyped-def]
    prior = ledger.project().logical_time or START.replace(hour=9, minute=0)
    wake = _event(
        world_id=ledger.world_id,
        event_id=f"clock:aftermath:{at.isoformat()}",
        event_type="ClockAdvanced",
        logical_at=at,
        payload={"logical_time_from": prior.isoformat(), "logical_time_to": at.isoformat()},
    )
    _commit_event(ledger, wake)
    return wake


def _active_aftermath(tmp_path: Path, *, plan_id: str | None = None):  # type: ignore[no-untyped-def]
    ledger = SQLiteWorldLedger(path=tmp_path / "aftermath.sqlite", world_id="world:aftermath")
    first = _advance_clock(ledger, START)
    if plan_id is not None:
        evidence = EvidenceRef(
            ref_id=first.event_id,
            evidence_type="committed_world_event",
            claim_purpose="future_plan",
            source_world_revision=1,
            immutable_hash=first.payload_hash,
        )
        planned = ActivityPlannedPayload(
            change_id=f"change:{plan_id}:plan",
            transition_id=f"transition:{plan_id}:plan",
            expected_entity_revision=0,
            evidence_refs=(evidence,),
            plan=_plan(
                plan_id, scheduled_window=DueWindow(opens_at=START, closes_at=END)
            ).model_copy(
                update={"evidence_refs": (evidence,)},
            ),
        )
        _commit_event(
            ledger,
            _event(
                world_id=ledger.world_id,
                event_id=f"event:{plan_id}:plan",
                event_type="ActivityPlanned",
                logical_at=START,
                payload=planned.model_dump(mode="json"),
            ),
        )
        _activity_transition(ledger, plan_id, "ActivityStarted", START)
    occurrence, texts = _commit_active_long_lived_occurrence(
        ledger=ledger,
        clock=first,
        include_reviewed_life_arc_effect=False,
        time_window=DueWindow(opens_at=START, closes_at=END),
    )
    store = InMemoryImmutableLifeContentStore()
    for candidate, text in zip(occurrence.candidate_outcomes, texts, strict=True):
        store.put_if_absent(
            StoredLifeContent(
                content_ref=candidate.content_ref,
                content_kind="outcome_candidate",
                content_payload_hash=candidate.content_payload_hash,
                text=text,
            )
        )
    interior = _CapturingLongLivedOutcomeModel(
        selected_ref=occurrence.candidate_outcomes[1].candidate_result_ref,
    )
    runtime = LifeAftermathRuntime(
        ledger=ledger,
        catalog=SimpleNamespace(),
        occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=store),
        content_store=store,
        owner_actor_ref="actor:companion",
        character_interior=interior,
    )
    return ledger, runtime, interior, occurrence


def _activity_transition(ledger, plan_id, event_type, at):  # type: ignore[no-untyped-def]
    plan = next(item for item in ledger.project().plans if item.plan_id == plan_id)
    payload = ActivityTransitionPayload(
        change_id=f"change:{plan_id}:{event_type}",
        transition_id=f"transition:{plan_id}:{event_type}",
        expected_entity_revision=plan.entity_revision,
        evidence_refs=plan.evidence_refs,
        plan_id=plan_id,
        transitioned_at=at,
        reason_ref=plan.evidence_refs[0].ref_id,
    )
    event = _event(
        world_id=ledger.world_id,
        event_id=f"event:{plan_id}:{event_type}",
        event_type=event_type,
        logical_at=at,
        payload=payload.model_dump(mode="json"),
    )
    _commit_event(ledger, event)
    return event


@pytest.mark.asyncio
async def test_complete_outcome_waits_for_accepted_window_end(tmp_path: Path) -> None:
    ledger, runtime, interior, occurrence = _active_aftermath(tmp_path)
    early = _advance_clock(ledger, EARLY)
    await runtime.advance_once(
        wake_event_ref=early.event_id,
        trace_id="trace:early",
        correlation_id="correlation:timing",
    )

    assert ledger.project().world_occurrences[0].status == "active"
    assert ledger.project().experiences == ()
    assert interior.calls == 0

    end = _advance_clock(ledger, END)
    settled = await runtime.advance_once(
        wake_event_ref=end.event_id,
        trace_id="trace:end",
        correlation_id="correlation:timing",
    )

    assert settled.status == "settled"
    assert interior.calls == 1
    projection = ledger.project()
    assert projection.world_occurrences[0].settled_outcome_ref == (
        occurrence.candidate_outcomes[1].candidate_result_ref
    )
    experience = projection.experiences[0]
    assert experience.values.occurred_from == START
    assert experience.values.occurred_to == END


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["ActivityCompleted", "ActivityAbandoned"])
@pytest.mark.parametrize("own_plan", [True, False])
async def test_activity_terminal_does_not_realize_complete_outcome_early(
    tmp_path: Path,
    event_type: str,
    own_plan: bool,
) -> None:
    plan_id = "plan:consequential" if own_plan else "plan:another"
    ledger, runtime, interior, _ = _active_aftermath(tmp_path, plan_id=plan_id)
    _advance_clock(ledger, EARLY)
    terminal = _activity_transition(ledger, plan_id, event_type, EARLY)

    await runtime.advance_once(
        wake_event_ref=terminal.event_id,
        trace_id="trace:early",
        correlation_id="correlation:terminal",
    )

    assert ledger.project().plans[0].status == (
        "completed" if event_type == "ActivityCompleted" else "abandoned"
    )
    assert ledger.project().world_occurrences[0].status == "active"
    assert ledger.project().experiences == ()
    assert interior.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["ActivityCompleted", "ActivityAbandoned"])
async def test_other_activity_terminal_is_not_this_occurrence_settlement_evidence(
    tmp_path: Path,
    event_type: str,
) -> None:
    ledger, runtime, interior, _ = _active_aftermath(tmp_path, plan_id="plan:another")
    end = _advance_clock(ledger, END)
    terminal = _activity_transition(ledger, "plan:another", event_type, END)

    await runtime.advance_once(
        wake_event_ref=terminal.event_id,
        trace_id="trace:other",
        correlation_id="correlation:terminal",
    )

    assert ledger.project().world_occurrences[0].status == "active"
    assert ledger.project().experiences == ()
    assert interior.calls == 0
    settled = await runtime.advance_once(
        wake_event_ref=end.event_id,
        trace_id="trace:end",
        correlation_id="correlation:terminal",
    )
    assert settled.status == "settled"


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["ActivityCompleted", "ActivityAbandoned"])
async def test_own_activity_terminal_can_wake_settlement_at_window_end(
    tmp_path: Path,
    event_type: str,
) -> None:
    ledger, runtime, interior, _ = _active_aftermath(tmp_path, plan_id="plan:consequential")
    _advance_clock(ledger, END)
    terminal = _activity_transition(ledger, "plan:consequential", event_type, END)

    result = await runtime.advance_once(
        wake_event_ref=terminal.event_id,
        trace_id="trace:own",
        correlation_id="correlation:terminal",
    )

    assert result.status == "settled"
    assert interior.calls == 1
    assert ledger.project().experiences[0].values.occurred_to == END
