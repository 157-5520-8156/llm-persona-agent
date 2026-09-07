"""Runner orchestration checks with a fake host and in-memory evidence.

These tests do not qualify production character decisions, provider behavior,
SQLite replay correctness, or real QQ delivery. They exercise run_journey's
ordering and reporting at its host/clock/evidence boundaries.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.declared_due import DeclaredDueTarget, SchedulerWakeSnapshot
import companion_daemon.world_v2.longitudinal_journey as journey_module
from companion_daemon.world_v2.longitudinal_journey import Journey, JourneyLimits, run_journey
from companion_daemon.world_v2.replay_evaluator import ReplayEvaluation, ReplayFinding


NOW = datetime(2026, 9, 7, tzinfo=UTC)


class _BoundaryHost:
    def __init__(self, fixture: _RunnerFixture, database: Path) -> None:
        database.touch()
        self.fixture = fixture
        self.logical_time = NOW
        self.pending_settlement = False
        self.environment_settled = False
        self.closed = False
        self.quiescent = False

    async def scheduler_wake_snapshot(self) -> SchedulerWakeSnapshot:
        due = self.fixture.due_at
        return SchedulerWakeSnapshot(
            logical_time=self.logical_time,
            dues=(DeclaredDueTarget("life.ecology", due, "fixture:environment"),)
            if due is not None and not self.environment_settled
            else (),
        )

    async def scheduler_once(self, *, observed_at: datetime, **_kwargs: object) -> SimpleNamespace:
        self.logical_time = observed_at
        if self.fixture.scheduler_delay:
            await asyncio.sleep(self.fixture.scheduler_delay)
        if self.fixture.due_at is not None and observed_at >= self.fixture.due_at:
            self.pending_settlement = True
        return SimpleNamespace(action_statuses=(), background_statuses=())

    async def inbound_text(self, **_kwargs: object) -> SimpleNamespace:
        # Like production ingress, receiving a timestamp cannot advance the
        # World Clock. The input sees the environment already settled here.
        self.fixture.inbound_contexts.append((self.logical_time, self.environment_settled))
        return SimpleNamespace(status="observed_only")

    async def drain(self, **_kwargs: object) -> SimpleNamespace:
        if self.pending_settlement:
            self.pending_settlement = False
            self.environment_settled = True
            return SimpleNamespace(action_statuses=(), background_statuses=("settled",))
        return SimpleNamespace(action_statuses=(), background_statuses=())

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            if self.fixture.close_tail:
                self.fixture.append_event("event:close-tail")

    async def wait_for_shutdown_quiescence(self) -> None:
        if not self.quiescent:
            self.quiescent = True
            if self.fixture.close_tail:
                self.fixture.append_event("event:quiescence-tail")

    def export_replay_evidence(self) -> SimpleNamespace:
        return SimpleNamespace(
            projection=SimpleNamespace(
                logical_time=self.logical_time,
                world_revision=len(self.fixture.events),
                semantic_hash="fixture:projection",
            )
        )

    def usage_budget_health(self) -> dict[str, object]:
        return {"status": "fixture_only"}


class _RunnerFixture:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.due_at: datetime | None = None
        self.scheduler_delay = 0.0
        self.replay_error = False
        self.close_tail = False
        self.events: list[dict[str, object]] = []
        self.inbound_contexts: list[tuple[datetime, bool]] = []
        self.hosts: list[_BoundaryHost] = []
        monkeypatch.setattr(journey_module, "read_events", self.read_events)
        monkeypatch.setattr(journey_module, "cold_evidence", self.cold_evidence)

        def evaluate(_evaluator: object, *, evidence: SimpleNamespace) -> ReplayEvaluation:
            return ReplayEvaluation(
                evaluator_version="fixture:runner-boundary",
                world_id="fixture:world",
                world_revision=evidence.projection.world_revision,
                replay_hash_matches=not self.replay_error,
                mechanism_checks=(),
                findings=(ReplayFinding("replay_hash_mismatch", "error", "fixture"),)
                if self.replay_error
                else (),
            )

        monkeypatch.setattr(journey_module.ReplayEvaluator, "evaluate", evaluate)

    def factory(self, database: Path, _clock: object, _delivery: object) -> _BoundaryHost:
        host = _BoundaryHost(self, database)
        self.hosts.append(host)
        return host

    def read_events(self, _database: Path, after: int = 0) -> list[dict[str, object]]:
        return [dict(event) for event in self.events if event["ledger_sequence"] > after]

    def cold_evidence(self, _database: Path) -> SimpleNamespace:
        return self.hosts[-1].export_replay_evidence()

    def append_event(self, event_id: str) -> None:
        payload_json = json.dumps({"runtime_outcome_ref": "expression-episode:model-silent"})
        self.events.append(
            {
                "event_id": event_id,
                "event_type": "TriggerProcessCompleted",
                "logical_time": self.hosts[-1].logical_time.isoformat(),
                "ledger_sequence": len(self.events) + 1,
                "payload_json": payload_json,
                "payload_hash": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
            }
        )


def _journey(*, with_due_input: bool = False) -> Journey:
    return Journey.parse(
        {
            "scenario_id": "runner-boundaries",
            "started_at": NOW.isoformat(),
            "duration_minutes": 3 if with_due_input else 1,
            "turns": [{"id": "at-due", "at_minutes": 2, "text": "fixture input"}]
            if with_due_input
            else [],
        }
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_injected_provider_clients_close_after_host_quiescence_at_each_restart(
    tmp_path, monkeypatch
):
    fixture = _RunnerFixture(monkeypatch)
    closes = []

    async def close_resources():
        assert fixture.hosts[-1].closed
        assert fixture.hosts[-1].quiescent
        closes.append(len(fixture.hosts))

    scenario = Journey.parse(
        {
            "scenario_id": "provider-lifecycle",
            "started_at": NOW.isoformat(),
            "duration_minutes": 3,
            "restart_minutes": [1],
        }
    )
    result = await run_journey(
        journey=scenario,
        output=tmp_path / "run",
        host_factory=fixture.factory,
        synthetic=True,
        close_resources=close_resources,
    )
    assert result["completed"]
    assert closes == [1, 2]


@pytest.mark.asyncio
async def test_partial_factory_failure_closes_caller_owned_provider_clients(tmp_path, monkeypatch):
    fixture = _RunnerFixture(monkeypatch)
    closed = []

    def broken_factory(database, clock, delivery):
        fixture.factory(database, clock, delivery)
        raise ValueError("fixture construction failure")

    async def close_resources():
        closed.append(True)

    result = await run_journey(
        journey=_journey(),
        output=tmp_path / "run",
        host_factory=broken_factory,
        synthetic=True,
        close_resources=close_resources,
    )
    assert result["stop_reason"] == "technical_failure:ValueError"
    assert closed == [True]


@pytest.mark.asyncio
async def test_host_close_error_still_waits_for_quiescence_and_closes_clients(
    tmp_path, monkeypatch
):
    fixture = _RunnerFixture(monkeypatch)
    closed = []

    def factory(database, clock, delivery):
        host = fixture.factory(database, clock, delivery)

        async def broken_close():
            raise OSError("fixture close error")

        host.aclose = broken_close
        return host

    async def close_resources():
        assert fixture.hosts[-1].quiescent
        closed.append(True)

    result = await run_journey(
        journey=_journey(),
        output=tmp_path / "run",
        host_factory=factory,
        synthetic=True,
        close_resources=close_resources,
    )
    assert result["stop_reason"] == "technical_failure:shutdown:OSError"
    assert closed == [True]


@pytest.mark.asyncio
async def test_caller_cancel_cannot_close_clients_before_shutdown_quiescence(tmp_path, monkeypatch):
    fixture = _RunnerFixture(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    closed = []

    def factory(database, clock, delivery):
        host = fixture.factory(database, clock, delivery)

        async def wait_for_quiescence():
            entered.set()
            await release.wait()
            host.quiescent = True

        host.wait_for_shutdown_quiescence = wait_for_quiescence
        return host

    async def close_resources():
        assert fixture.hosts[-1].quiescent
        closed.append(True)

    task = asyncio.create_task(
        run_journey(
            journey=_journey(),
            output=tmp_path / "run",
            host_factory=factory,
            synthetic=True,
            close_resources=close_resources,
        )
    )
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not closed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]


@pytest.mark.asyncio
async def test_same_time_input_observes_due_environment_after_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _RunnerFixture(monkeypatch)
    fixture.due_at = NOW + timedelta(minutes=2)
    manifest = await run_journey(
        journey=_journey(with_due_input=True),
        output=tmp_path / "journey",
        host_factory=fixture.factory,
        synthetic=True,
        limits=JourneyLimits(heartbeat_seconds=120),
    )
    assert fixture.inbound_contexts == [(NOW + timedelta(minutes=2), True)]
    assert manifest["turns_consumed"] == 1


@pytest.mark.asyncio
async def test_replay_error_cannot_be_reported_as_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _RunnerFixture(monkeypatch)
    fixture.replay_error = True
    output = tmp_path / "journey"
    manifest = await run_journey(
        journey=_journey(),
        output=output,
        host_factory=fixture.factory,
        synthetic=True,
    )
    assert manifest["replay"]["replay_hash_matches"] is False
    assert manifest["completed"] is False
    assert manifest["stop_reason"] != "completed"
    final = [row for row in _read_jsonl(output / "timeline.jsonl") if row["kind"] == "final"]
    assert final[-1]["status"] == manifest["stop_reason"]


@pytest.mark.asyncio
async def test_final_step_exceeding_wall_deadline_cannot_report_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _RunnerFixture(monkeypatch)
    fixture.scheduler_delay = 1.1
    manifest = await run_journey(
        journey=_journey(),
        output=tmp_path / "journey",
        host_factory=fixture.factory,
        synthetic=True,
        limits=JourneyLimits(max_wall_seconds=1),
    )
    assert manifest["completed"] is False
    assert manifest["stop_reason"] == "wall_time_limit"


@pytest.mark.asyncio
async def test_provider_timeout_is_not_an_experiment_deadline(tmp_path, monkeypatch):
    fixture = _RunnerFixture(monkeypatch)

    async def provider_timeout(self):
        raise TimeoutError("fixture transport timed out")

    monkeypatch.setattr(_BoundaryHost, "scheduler_wake_snapshot", provider_timeout)
    manifest = await run_journey(
        journey=_journey(),
        output=tmp_path / "journey",
        host_factory=fixture.factory,
        synthetic=True,
        limits=JourneyLimits(max_wall_seconds=30),
    )
    assert manifest["completed"] is False
    assert manifest["stop_reason"] == "technical_failure:TimeoutError"


@pytest.mark.asyncio
@pytest.mark.parametrize("unprocessed_due", [False, True])
async def test_quiet_tail_needs_no_empty_clock_event_but_cannot_skip_due(
    tmp_path,
    monkeypatch,
    unprocessed_due,
):
    fixture = _RunnerFixture(monkeypatch)
    if unprocessed_due:
        fixture.due_at = NOW + timedelta(seconds=30)

    async def quiet_scheduler(self, **kwargs):
        return SimpleNamespace(action_statuses=(), background_statuses=())

    monkeypatch.setattr(_BoundaryHost, "scheduler_once", quiet_scheduler)
    manifest = await run_journey(
        journey=_journey(),
        output=tmp_path / "journey",
        host_factory=fixture.factory,
        synthetic=True,
    )
    assert manifest["completed"] is not unprocessed_due
    assert manifest["quiet_tail_seconds"] == 60
    assert manifest["stop_reason"] == (
        "unprocessed_due_before_end" if unprocessed_due else "completed"
    )


@pytest.mark.asyncio
async def test_clock_at_end_does_not_prove_overdue_work_was_processed(tmp_path, monkeypatch):
    fixture = _RunnerFixture(monkeypatch)
    fixture.due_at = NOW + timedelta(seconds=30)

    async def skip_environment(self, *, observed_at, **kwargs):
        self.logical_time = observed_at
        return SimpleNamespace(action_statuses=(), background_statuses=())

    monkeypatch.setattr(_BoundaryHost, "scheduler_once", skip_environment)
    manifest = await run_journey(
        journey=_journey(),
        output=tmp_path / "journey",
        host_factory=fixture.factory,
        synthetic=True,
    )
    assert manifest["elapsed_logical_seconds"] == 60
    assert manifest["completed"] is False
    assert manifest["stop_reason"] == "unprocessed_due_before_end"


@pytest.mark.asyncio
async def test_existing_output_is_preserved_without_constructing_a_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _RunnerFixture(monkeypatch)
    output = tmp_path / "existing"
    output.mkdir()
    retained = output / "world.sqlite"
    retained.write_bytes(b"preserve existing evidence")
    with pytest.raises(FileExistsError):
        await run_journey(
            journey=_journey(),
            output=output,
            host_factory=fixture.factory,
            synthetic=True,
        )
    assert retained.read_bytes() == b"preserve existing evidence"
    assert list(output.iterdir()) == [retained]
    assert fixture.hosts == []


@pytest.mark.asyncio
async def test_final_artifacts_include_close_and_quiescence_tail_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _RunnerFixture(monkeypatch)
    fixture.close_tail = True
    output = tmp_path / "journey"
    manifest = await run_journey(
        journey=_journey(),
        output=output,
        host_factory=fixture.factory,
        synthetic=True,
    )
    assert [event["event_id"] for event in _read_jsonl(output / "evidence.jsonl")] == [
        "event:close-tail",
        "event:quiescence-tail",
    ]
    final = [row for row in _read_jsonl(output / "timeline.jsonl") if row["kind"] == "final"]
    assert final[-1]["ledger_end_sequence"] == 2
    assert manifest["replay"]["world_revision"] == 2
    assert manifest["completed"] is False
    assert manifest["stop_reason"] == "completion_unverified_after_state_change"
