from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.declared_display_contract import (
    DECLARED_DISPLAY_HITCH_CAS_ATTEMPTS,
    DECLARED_DISPLAY_HITCH_RETRYABLE_REASONS,
    DECLARED_DISPLAY_HITCH_TERMINAL_REASONS,
    DECLARED_DISPLAY_RECORDED,
)
from companion_daemon.world_v2.declared_display_runtime import (
    classify_declared_display_hitch_failure,
    validate_declared_display_hitch_terminal,
)
from companion_daemon.world_v2.errors import ConcurrencyConflict, IdempotencyConflict
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.private_turn_state import PrivateTurnState
from companion_daemon.world_v2.runtime import WorldRuntime
from companion_daemon.world_v2.schemas import Observation, WorldEvent


NOW = datetime(2026, 8, 18, 10, tzinfo=UTC)
WORLD = "world:declared-display-hitch"


def _event(
    event_id: str,
    event_type: str,
    payload: dict[str, object],
    *,
    actor: str = "system:test",
) -> WorldEvent:
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=event_id,
        event_type=event_type,
        world_id=WORLD,
        logical_time=NOW,
        created_at=NOW,
        actor=actor,
        source="test:declared-display-hitch",
        trace_id="trace:declared-display-hitch",
        causation_id="cause:" + event_id,
        correlation_id="correlation:declared-display-hitch",
        idempotency_key="idempotency:" + event_id,
        payload=payload,
    )


def _observation(*, actor: str = "user:1") -> Observation:
    return Observation(
        schema_version="world-v2.1",
        observation_id="observation:hitch-1",
        world_id=WORLD,
        logical_time=NOW,
        created_at=NOW,
        trace_id="trace:declared-display-hitch",
        causation_id="inbound:hitch-1",
        correlation_id="correlation:declared-display-hitch",
        source="test",
        source_event_id="message:hitch-1",
        actor=actor,
        channel="test",
        payload_ref="payload:hitch",
        payload_hash="a" * 64,
        text="给我看看你现在的样子",
        received_at=NOW,
        reply_context={"target": actor},
    )


def _world_with_observation(*, actor: str = "user:1"):
    ledger = WorldLedger.in_memory(world_id=WORLD)
    ledger.commit(
        (_event("event:start", "WorldStarted", {}),),
        expected_world_revision=0,
        expected_deliberation_revision=0,
    )
    observation = _observation(actor=actor)
    payload = observation.model_dump(mode="json")
    event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:observation:hitch-1",
        event_type="ObservationRecorded",
        world_id=observation.world_id,
        logical_time=observation.logical_time,
        created_at=observation.created_at,
        actor=observation.actor,
        source=observation.source,
        trace_id=observation.trace_id,
        causation_id=observation.causation_id,
        correlation_id=observation.correlation_id,
        idempotency_key=domain_idempotency_key(
            event_type="ObservationRecorded",
            world_id=WORLD,
            payload=payload,
        )
        or f"observation:{observation.source}:{observation.source_event_id}",
        payload=payload,
    )
    projection = ledger.project()
    ledger.commit(
        (event,),
        expected_world_revision=projection.world_revision,
        expected_deliberation_revision=projection.deliberation_revision,
    )
    runtime = WorldRuntime(world_id=WORLD, ledger=ledger)
    return runtime, ledger, observation, event


class _ConflictThenCommit:
    def __init__(self, inner: WorldLedger, *, conflicts: int) -> None:
        self._inner = inner
        self._remaining = conflicts
        self.calls = 0

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def commit_at_cursor(self, events, *, expected_cursor, commit_id):
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise ConcurrencyConflict("stale world revision")
        return self._inner.commit_at_cursor(
            events, expected_cursor=expected_cursor, commit_id=commit_id
        )


def _display_state(intent: str = "sexual_suggestive") -> PrivateTurnState:
    return PrivateTurnState(
        inner_state_summary="想让他看见这一面，但只到这里",
        declared_display=intent,  # type: ignore[arg-type]
    )


def _display_events(ledger: WorldLedger) -> tuple[str, ...]:
    return tuple(
        item.event_type
        for item in ledger.project().committed_world_event_refs
        if item.event_type
        in {DECLARED_DISPLAY_RECORDED, "DeclaredDisplayWithdrawn", "AdvisoryAcceptanceRejected"}
    )


def test_classify_keeps_cas_retryable_and_out_of_the_terminal_allowlist() -> None:
    reason, retryable = classify_declared_display_hitch_failure(
        ConcurrencyConflict("stale")
    )
    assert reason == "concurrency_conflict"
    assert retryable is True
    assert reason in DECLARED_DISPLAY_HITCH_RETRYABLE_REASONS
    assert reason not in DECLARED_DISPLAY_HITCH_TERMINAL_REASONS
    reason, retryable = classify_declared_display_hitch_failure(
        IdempotencyConflict("reuse")
    )
    assert reason == "idempotency_conflict" and retryable is True
    with pytest.raises(ValueError, match="not a terminal"):
        validate_declared_display_hitch_terminal(
            reason_code="concurrency_conflict",
            ledger=SimpleNamespace(project=lambda: SimpleNamespace(), lookup_event_commit=lambda _: None),
            source_event_ref="event:missing",
            observation_actor="user:1",
        )


def test_hitch_lands_a_full_turn_private_turn_state_declaration() -> None:
    runtime, ledger, observation, event = _world_with_observation()
    result = runtime._hitch_paid_inbound_declared_display(
        state=_display_state(),
        observation=observation,
        observation_event=event,
        external_effect_landed=True,
    )
    assert result.outcome == "landed"
    assert result.attempts == 1
    assert DECLARED_DISPLAY_RECORDED in _display_events(ledger)


def test_hitch_retries_cas_and_does_not_drop_the_declaration() -> None:
    runtime, ledger, observation, event = _world_with_observation()
    wrapped = _ConflictThenCommit(ledger, conflicts=1)
    runtime._ledger = wrapped  # type: ignore[method-assign]
    result = runtime._hitch_paid_inbound_declared_display(
        state=_display_state(),
        observation=observation,
        observation_event=event,
        external_effect_landed=True,
    )
    assert result.outcome == "landed"
    assert result.attempts == 2
    assert wrapped.calls >= 2
    assert DECLARED_DISPLAY_RECORDED in _display_events(ledger)
    assert "AdvisoryAcceptanceRejected" not in _display_events(ledger)


def test_hitch_exhausted_cas_is_visible_retryable_not_a_terminal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime, ledger, observation, event = _world_with_observation()
    wrapped = _ConflictThenCommit(ledger, conflicts=DECLARED_DISPLAY_HITCH_CAS_ATTEMPTS + 4)
    runtime._ledger = wrapped  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR):
        result = runtime._hitch_paid_inbound_declared_display(
            state=_display_state(),
            observation=observation,
            observation_event=event,
            external_effect_landed=True,
        )
    assert result.outcome == "retryable"
    assert result.reason_code == "concurrency_conflict"
    assert result.attempts == DECLARED_DISPLAY_HITCH_CAS_ATTEMPTS
    assert result.event_id is None
    assert DECLARED_DISPLAY_RECORDED not in _display_events(ledger)
    assert "concurrency_conflict" in caplog.text
    assert "exhausted" in caplog.text


def test_hitch_missing_logical_clock_is_visible_and_not_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = WorldRuntime.in_memory(world_id=WORLD)
    observation = _observation()
    event = _event(
        "event:observation:hitch-1",
        "ObservationRecorded",
        observation.model_dump(mode="json"),
        actor="user:1",
    )
    with caplog.at_level(logging.ERROR):
        result = runtime._hitch_paid_inbound_declared_display(
            state=_display_state(),
            observation=observation,
            observation_event=event,
            external_effect_landed=True,
        )
    assert result.outcome == "retryable"
    assert result.reason_code == "logical_clock_unavailable"
    assert "logical_clock_unavailable" in caplog.text
    assert runtime._ledger.project().logical_time is None


def test_hitch_does_not_land_a_superseded_turn() -> None:
    runtime, ledger, observation, event = _world_with_observation()
    result = runtime._hitch_paid_inbound_declared_display(
        state=_display_state("explicit_adult"),
        observation=observation,
        observation_event=event,
        external_effect_landed=False,
    )
    assert result.outcome == "not_delivered"
    assert result.reason_code == "external_effect_not_landed"
    assert _display_events(ledger) == ()


@pytest.mark.asyncio
async def test_side_effects_bind_declared_display_to_external_effect_landed() -> None:
    runtime, ledger, observation, event = _world_with_observation()
    proposal = SimpleNamespace(
        private_turn_state=_display_state(),
        response_expectation_assessment=None,
    )
    audited = SimpleNamespace(model_result_ref="model:hitch", proposal_id="proposal:hitch")
    await runtime._hitch_paid_inbound_side_effects(
        proposal=proposal,  # type: ignore[arg-type]
        audited=audited,  # type: ignore[arg-type]
        observation=observation,
        observation_event=event,
        external_effect_landed=False,
    )
    assert _display_events(ledger) == ()
    await runtime._hitch_paid_inbound_side_effects(
        proposal=proposal,  # type: ignore[arg-type]
        audited=audited,  # type: ignore[arg-type]
        observation=observation,
        observation_event=event,
        external_effect_landed=True,
    )
    assert DECLARED_DISPLAY_RECORDED in _display_events(ledger)


def test_hitch_non_user_actor_is_a_reproved_terminal() -> None:
    runtime, ledger, observation, event = _world_with_observation(actor="npc:bookshop")
    result = runtime._hitch_paid_inbound_declared_display(
        state=_display_state(),
        observation=observation,
        observation_event=event,
        external_effect_landed=True,
    )
    assert result.outcome == "rejected"
    assert result.reason_code == "recipient_not_user_bound"
    assert DECLARED_DISPLAY_RECORDED not in _display_events(ledger)
    assert result.event_id is not None
    located = ledger.lookup_event_commit(result.event_id)
    assert located is not None
    terminal, _commit = located
    assert terminal.event_type == "AdvisoryAcceptanceRejected"
    assert terminal.payload()["reason_code"] == "recipient_not_user_bound"
    assert terminal.payload()["advisory_kind"] == "declared_display"
    validate_declared_display_hitch_terminal(
        reason_code="recipient_not_user_bound",
        ledger=ledger,
        source_event_ref=event.event_id,
        observation_actor=observation.actor,
    )
