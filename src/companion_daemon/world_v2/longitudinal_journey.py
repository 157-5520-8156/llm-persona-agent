"""Accelerated, capture-only journeys through the installed QQ host.

This is an offline evaluation adapter. It owns simulated external time, never
World events or character choices. Transport deadlines and cost accounting keep
their real clocks; only presentation waits and registered scheduler timers use
the supplied clock. Real provider time cannot be accelerated.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import traceback
from typing import Awaitable, Callable

from .longitudinal_review import context_evidence_visibility
from .replay_evaluator import ReplayEvaluator
from .sqlite_ledger import SQLiteWorldLedger


CONTRACT = "longitudinal-journey.1"
RECIPIENT = "longitudinal-capture-user"


class JourneyDeadlineExceeded(TimeoutError):
    """The experiment stopped admitting work; not a provider timeout."""


@dataclass(frozen=True)
class Journey:
    scenario_id: str
    started_at: datetime
    duration_minutes: int
    turns: tuple[dict, ...]
    restart_minutes: tuple[int, ...]

    @classmethod
    def parse(cls, data: dict) -> Journey:
        start = datetime.fromisoformat(data["started_at"])
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("started_at must have a timezone")
        duration = data["duration_minutes"]
        if type(duration) is not int or not 1 <= duration <= 31 * 1440:
            raise ValueError("duration_minutes must be an integer from 1 to 44640")
        turns = data.get("turns", [])
        last = -1
        ids: set[str] = set()
        for turn in turns:
            minute, identifier = turn["at_minutes"], turn["id"]
            if type(minute) is not int or not last <= minute <= duration or minute < 0:
                raise ValueError("turns must be ordered and inside the journey")
            if not isinstance(identifier, str) or not identifier or identifier in ids:
                raise ValueError("turn IDs must be nonempty and unique")
            if not isinstance(turn["text"], str) or not turn["text"].strip():
                raise ValueError("each turn requires text")
            if set(turn) - {"id", "at_minutes", "text"}:
                raise ValueError("scenario turns may supply inputs, not character decisions")
            ids.add(identifier)
            last = minute
        restarts = data.get("restart_minutes", [])
        if any(type(value) is not int or not 0 < value < duration for value in restarts):
            raise ValueError("restart_minutes must be strictly inside the journey")
        if sorted(set(restarts)) != restarts:
            raise ValueError("restart_minutes must be ordered and unique")
        return cls(
            str(data["scenario_id"]),
            start.astimezone(UTC),
            duration,
            tuple(dict(turn) for turn in turns),
            tuple(restarts),
        )


@dataclass(frozen=True)
class JourneyLimits:
    heartbeat_seconds: float = 300
    max_steps: int = 5000
    max_wall_seconds: float = 1800
    drain_passes: int = 8
    background_units: int = 4

    def __post_init__(self) -> None:
        if not math.isfinite(self.heartbeat_seconds) or not 1 <= self.heartbeat_seconds <= 900:
            raise ValueError("heartbeat_seconds must be between 1 and 900")
        if not math.isfinite(self.max_wall_seconds) or not 1 <= self.max_wall_seconds <= 3600:
            raise ValueError("max_wall_seconds must be between 1 and 3600")
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 100000:
            raise ValueError("max_steps must be between 1 and 100000")
        if type(self.drain_passes) is not int or not 1 <= self.drain_passes <= 64:
            raise ValueError("drain_passes must be between 1 and 64")
        if type(self.background_units) is not int or not 1 <= self.background_units <= 64:
            raise ValueError("background_units must be between 1 and 64")


class JourneyClock:
    """The runner advances calendar time; background timers can only wait."""

    def __init__(self, at: datetime):
        self.current = at
        self._presentation_current = at
        self._waiters: list[tuple[datetime, asyncio.Future]] = []

    def now(self) -> datetime:
        return self.current

    def presentation_now(self) -> datetime:
        return self._presentation_current

    async def presentation_sleep(self, seconds: float) -> None:
        # Sender rhythm waits are simulated explicitly, never real sleeps.
        self._presentation_current += timedelta(seconds=max(0, seconds))
        await asyncio.sleep(0)

    async def timer_sleep(self, seconds: float) -> None:
        future = asyncio.get_running_loop().create_future()
        waiter = (self.current + timedelta(seconds=max(0, seconds)), future)
        self._waiters.append(waiter)
        try:
            await future
        finally:
            self._waiters.remove(waiter)

    def next_timer(self) -> datetime | None:
        return min((at for at, future in self._waiters if not future.done()), default=None)

    def advance(self, at: datetime) -> None:
        if at < self.current:
            raise ValueError("journey clock cannot move backward")
        self.current = at
        self._presentation_current = max(self._presentation_current, at)
        for due, future in tuple(self._waiters):
            if due <= at and not future.done():
                future.set_result(None)


class CaptureDelivery:
    """Local delivery receipts, with typing distinct from visible expression."""

    def __init__(self, clock: JourneyClock):
        self.clock = clock
        self.records: list[dict] = []

    def _capture(self, kind: str, text: str) -> dict:
        identifier = f"journey-message-{len(self.records) + 1}"
        self.records.append(
            {
                "kind": kind,
                "text": text,
                "message_id": identifier,
                "virtual_at": self.clock.now().isoformat(),
            }
        )
        return {"status": "ok", "data": {"message_id": identifier}}

    async def send_text(self, recipient_id: str, text: str) -> dict:
        return self._capture("text", text)

    async def send_typing(self, recipient_id: str, *, state: str) -> dict:
        return self._capture("typing", state)

    async def send_reaction(self, recipient_id: str, *, message_id: str, reaction_id: str) -> dict:
        return self._capture("reaction", f"{message_id}:{reaction_id}")

    async def send_sticker(self, recipient_id: str, *, sticker_id: str) -> dict:
        return self._capture("sticker", sticker_id)

    async def get_message(self, recipient_id: str, *, message_id: str) -> dict:
        record = next((item for item in self.records if item["message_id"] == message_id), None)
        if record is None:
            return {"status": "failed", "retcode": 1}
        return {
            "status": "ok",
            "retcode": 0,
            "data": {"message_id": message_id, "message": record["text"]},
        }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def read_events(database: Path, after: int = 0) -> list[dict]:
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_v2_events'"
            ).fetchone()
            is None
        ):
            return []
        rows = connection.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE ledger_sequence > ? ORDER BY ledger_sequence",
            (after,),
        ).fetchall()
    return [{**json.loads(raw), "ledger_sequence": sequence} for sequence, raw in rows]


def terminal_evidence(events: list[dict]) -> list[dict]:
    """Retain exact lifecycle outcomes, including several decisions in one step."""
    result = []
    for event in events:
        if event.get("event_type") != "TriggerProcessCompleted":
            continue
        payload = json.loads(event["payload_json"])
        outcome = payload.get("runtime_outcome_ref")
        if not isinstance(outcome, str):
            continue
        result.append(
            {
                "event_ref": event["event_id"],
                "outcome_ref": outcome,
                "terminal_outcome": {
                    "expression-episode:model-silent": "character_silent",
                    "expression-episode:action_authorized": "action_authorized",
                }.get(outcome, "unknown"),
            }
        )
    return result


def model_failures(events: list[dict]) -> list[dict]:
    failures = []
    for event in events:
        if event.get("event_type") != "ModelResultRecorded":
            continue
        payload = json.loads(event["payload_json"])
        audit = json.loads(payload["audit_json"])
        if audit.get("failure_code"):
            failures.append(
                {
                    "event_ref": event["event_id"],
                    "failure_code": audit["failure_code"],
                    "status": audit["status"],
                }
            )
    return failures


def read_provider_usage_evidence(database: Path) -> dict:
    """Snapshot the isolated primary ledger without migrating or repricing it.

    A successful World ModelResult may follow an earlier failed provider call.
    These records are independent evidence, not inferred links to those events
    or a claim that every row represents an emitted HTTP request. The raw rows
    remain private artifacts; only status counts enter the review summary.
    """
    result = {"contract": "longitudinal-provider-usage.1"}
    scope = "world_v2_model_usage_and_reservations"
    unavailable = {"status": "unavailable", "scope": scope, "world_event_linkage": "unverified"}
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not {"world_v2_model_usage", "world_v2_model_reservations"} <= tables:
                return {**result, "summary": {**unavailable, "reason": "missing_usage_tables"}}
            usage = [dict(row) for row in connection.execute(
                "SELECT * FROM world_v2_model_usage ORDER BY id"
            )]
            reservations = [dict(row) for row in connection.execute(
                "SELECT * FROM world_v2_model_reservations ORDER BY reservation_id"
            )]
        finally:
            connection.close()
    except sqlite3.Error:
        return {**result, "summary": {**unavailable, "reason": "usage_database_unreadable"}}

    def counts(rows: list[dict], key: str) -> dict:
        # Older rows can predate billing metadata. Missing values are unknown,
        # never a synthetic known/not_billed or a successful provider attempt.
        return dict(sorted(Counter(str(row.get(key) or "unavailable") for row in rows).items()))

    return {
        **result,
        "summary": {
            "status": "captured",
            "scope": scope,
            "usage_record_count": len(usage),
            "usage_status_counts": counts(usage, "status"),
            "billing_state_counts": counts(usage, "billing_state"),
            "reservation_record_count": len(reservations),
            "reservation_status_counts": counts(reservations, "status"),
            "world_event_linkage": "unverified",
        },
        "usage_records": usage,
        "reservation_records": reservations,
    }


def budget_was_denied(database: Path) -> bool:
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='world_v2_model_usage'"
            ).fetchone()
            is None
        ):
            return False
        return (
            connection.execute(
                "SELECT 1 FROM world_v2_model_usage WHERE status='budget_denied' LIMIT 1"
            ).fetchone()
            is not None
        )


def cold_evidence(database: Path):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        worlds = connection.execute("SELECT world_id FROM world_v2_heads").fetchall()
    if len(worlds) != 1:
        raise ValueError("a journey must contain exactly one isolated world")
    ledger = SQLiteWorldLedger(path=database, world_id=worlds[0][0])
    try:
        return ledger.export_replay_evidence()
    finally:
        ledger.close()


async def run_journey(
    *,
    journey: Journey,
    output: Path,
    host_factory: Callable,
    synthetic: bool,
    limits: JourneyLimits = JourneyLimits(),
    provenance: dict | None = None,
    model_input_capture=None,
    close_resources: Callable | None = None,
    next_command: Callable[[dict], Awaitable[dict | None]] | None = None,
) -> dict:
    """Create a fresh world and retain reviewable evidence even on early stop.

    host_factory(database, clock, delivery) must construct the installed host.
    A nonempty/existing output is never removed or reused. The production DB
    cannot enter this interface. Limits stop the experiment, not the character.
    next_command observes settled steps and supplies a user turn, a future
    wait_until_minutes checkpoint, or None to stop. It cannot author World state.
    """
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    database = output / "world.sqlite"
    clock = JourneyClock(journey.started_at)
    delivery = CaptureDelivery(clock)
    host = None
    shutdown_task: asyncio.Task | None = None
    wall_started = time.monotonic()
    billing_day = datetime.now(UTC).date()
    timeline: list[dict] = []
    evidence: list[dict] = []
    restarts: list[dict] = []
    end = journey.started_at + timedelta(minutes=journey.duration_minutes)
    sequence = 0
    delivery_offset = 0
    capture_offset = 0
    turn_index = restart_index = 0
    turns = list(journey.turns)
    observation_offset = 0
    checkpoint: datetime | None = None
    stop_reason = "completed"
    replay: dict = {}
    usage: dict = {}
    final_logical_time: datetime | None = None
    final_wake_snapshot = None
    due_snapshot_sequence_before: int | None = None
    due_snapshot_sequence_after: int | None = None
    row: dict = {}

    async def shutdown() -> None:
        nonlocal shutdown_task

        async def finish_owned_resources():
            failure = None
            operations = []
            if host is not None:
                operations.extend((host.aclose, host.wait_for_shutdown_quiescence))
            if close_resources is not None:
                operations.append(close_resources)
            for operation in operations:
                try:
                    await operation()
                except BaseException as exc:
                    failure = failure or exc
            if failure is not None:
                raise failure

        if shutdown_task is None:
            shutdown_task = asyncio.create_task(finish_owned_resources())
        cancelled = False
        while not shutdown_task.done():
            try:
                await asyncio.shield(shutdown_task)
            except asyncio.CancelledError:
                cancelled = True
        shutdown_task.result()
        if cancelled:
            raise asyncio.CancelledError()

    async def bounded(awaitable):
        remaining = limits.max_wall_seconds - (time.monotonic() - wall_started)
        if remaining <= 0:
            awaitable.close()
            raise JourneyDeadlineExceeded("journey admission deadline")
        deadline = asyncio.timeout(remaining)
        try:
            async with deadline:
                return await awaitable
        except TimeoutError as exc:
            if deadline.expired():
                raise JourneyDeadlineExceeded("journey admission deadline") from exc
            raise

    def capture(row: dict) -> None:
        nonlocal sequence, delivery_offset, capture_offset
        new_events = read_events(database, sequence)
        row.update(
            {
                "step_id": f"step-{len(timeline) + 1:05}",
                "virtual_at": clock.now().isoformat(),
                "ledger_start_sequence": sequence + 1,
                "ledger_end_sequence": new_events[-1]["ledger_sequence"]
                if new_events
                else sequence,
                "deliveries": delivery.records[delivery_offset:],
            }
        )
        row["terminal_outcomes"] = terminal_evidence(new_events)
        row["model_failures"] = model_failures(new_events)
        row["context_visibility"] = "unverified"
        if model_input_capture is not None:
            capture_offset, records = model_input_capture.read_since(capture_offset)
            requests = [record for record in records if record.get("kind") == "request"]
            row["context_evidence"] = {
                "requests": requests,
                "transport_results": [
                    record for record in records if record.get("kind") == "transport_result"
                ],
                "association": "capture_interval_only_not_pinned_turn_proof",
            }
            row["context_visibility"] = context_evidence_visibility(row["context_evidence"])
        delivery_offset = len(delivery.records)
        if new_events:
            sequence = new_events[-1]["ledger_sequence"]
            with (output / "evidence.jsonl").open("a") as stream:
                stream.writelines(_json(event) + "\n" for event in new_events)
            evidence.extend(new_events)
        timeline.append(row)
        with (output / "timeline.jsonl").open("a") as stream:
            stream.write(_json(row) + "\n")

    def operator_observation() -> dict:
        return {
            "virtual_at": clock.now().isoformat(),
            "elapsed_minutes": (clock.now() - journey.started_at).total_seconds() / 60,
            "steps": [
                {
                    key: value
                    for key, value in step.items()
                    if key
                    in {
                        "step_id",
                        "kind",
                        "status",
                        "virtual_at",
                        "user_text",
                        "turn_id",
                        "deliveries",
                        "terminal_outcomes",
                        "model_failures",
                        "errors",
                    }
                }
                for step in timeline[observation_offset:]
            ],
        }

    async def drain() -> tuple[list[str], bool]:
        statuses: list[str] = []
        for _ in range(limits.drain_passes):
            result = await bounded(
                host.drain(max_action_units=8, max_background_units=limits.background_units)
            )
            current = [*result.action_statuses, *result.background_statuses]
            statuses.extend(current)
            if not current or all(status == "idle" for status in current):
                return statuses, True
            if all(status in {"idle", "not_due", "owned_elsewhere"} for status in current):
                snapshot = await bounded(host.scheduler_wake_snapshot())
                if any(item.due_at > clock.now() for item in snapshot.dues):
                    # A durable lease or scheduled Action cannot progress at
                    # this instant. Preserve that status and visit its due;
                    # repeated drain calls cannot make logical time elapse.
                    return statuses, True
            await asyncio.sleep(0)
        return statuses, False

    try:
        host = host_factory(database, clock, delivery)
        capture({"kind": "bootstrap", "status": "ready", "errors": []})
        while clock.now() < end or turn_index < len(turns):
            if len(timeline) >= limits.max_steps:
                stop_reason = "step_limit"
                break
            if time.monotonic() - wall_started >= limits.max_wall_seconds:
                stop_reason = "wall_time_limit"
                break
            if datetime.now(UTC).date() != billing_day:
                stop_reason = "billing_day_boundary"
                break
            if next_command is not None and turn_index == len(turns) and checkpoint is None:
                observation = operator_observation()
                # Give the operator a detached view, never mutable ledger/timeline objects.
                command = await bounded(next_command(json.loads(_json(observation))))
                observation_offset = len(timeline)
                # Operator thinking time is real wall time.  A day/month reset
                # must not admit a new provider call against a fresh allowance.
                if datetime.now(UTC).date() != billing_day:
                    stop_reason = "billing_day_boundary"
                    break
                if command is not None:
                    if not isinstance(command, dict):
                        raise ValueError(
                            "operator command must be a turn, wait checkpoint, or null"
                        )
                    if set(command) == {"wait_until_minutes"}:
                        minute = command["wait_until_minutes"]
                        if type(minute) is not int or not (
                            observation["elapsed_minutes"] < minute <= journey.duration_minutes
                        ):
                            raise ValueError(
                                "wait checkpoint must be future and inside the journey"
                            )
                        checkpoint = journey.started_at + timedelta(minutes=minute)
                    else:
                        validated = Journey.parse(
                            {
                                "scenario_id": journey.scenario_id,
                                "started_at": journey.started_at.isoformat(),
                                "duration_minutes": journey.duration_minutes,
                                "turns": [*turns, command],
                            }
                        )
                        if command["at_minutes"] < observation["elapsed_minutes"]:
                            raise ValueError("operator input cannot move time backward")
                        turns = list(validated.turns)
                with (output / "operator-commands.jsonl").open("a") as stream:
                    stream.write(
                        _json({"virtual_at": clock.now().isoformat(), "command": command}) + "\n"
                    )
                if command is None:
                    stop_reason = "operator_stopped"
                    break
            row = {"kind": "scheduler", "errors": []}
            started = time.monotonic()
            # Re-read accepted due instants after every turn and settlement.
            snapshot = await bounded(host.scheduler_wake_snapshot())
            future = [item.due_at for item in snapshot.dues if item.due_at > clock.now()]
            timer = clock.next_timer()
            if timer is not None and timer > clock.now():
                future.append(timer)
            target = min(end, clock.now() + timedelta(seconds=limits.heartbeat_seconds), *future)
            next_turn = (
                journey.started_at + timedelta(minutes=turns[turn_index]["at_minutes"])
                if turn_index < len(turns)
                else None
            )
            next_restart = (
                journey.started_at + timedelta(minutes=journey.restart_minutes[restart_index])
                if restart_index < len(journey.restart_minutes)
                else None
            )
            target = min(target, next_turn or end, next_restart or end, checkpoint or end)
            clock.advance(max(clock.now(), target))
            await asyncio.sleep(0)
            # Environment deadlines at the same instant are settled before
            # the next external input reads its Context. Inbound itself does
            # not advance the authoritative logical clock.
            result = await bounded(
                host.scheduler_once(
                    observed_at=clock.now(),
                    max_action_units=8,
                    max_background_units=limits.background_units,
                )
            )
            row["scheduler_statuses"] = [*result.action_statuses, *result.background_statuses]
            pre_statuses, pre_idle = await drain()
            row["pre_input_drain_statuses"] = pre_statuses
            if not pre_idle:
                row.update(status="incomplete", errors=["drain_limit_reached"])
                capture(row)
                stop_reason = "drain_limit_reached"
                break
            if next_restart is not None and next_restart <= clock.now():
                row["kind"] = "restart"
                await shutdown()
                before = cold_evidence(database)
                delivered_before = len(delivery.records)
                checkpoint_path = (
                    output / f"checkpoint-{journey.restart_minutes[restart_index]}.sqlite"
                )
                with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as source:
                    with sqlite3.connect(checkpoint_path) as destination:
                        source.backup(destination)
                shutdown_task = None
                host = host_factory(database, clock, delivery)
                after = host.export_replay_evidence()
                restart = {
                    "virtual_at": clock.now().isoformat(),
                    "before_hash": before.projection.semantic_hash,
                    "after_hash": after.projection.semantic_hash,
                    "same_state": before.projection.semantic_hash == after.projection.semantic_hash,
                    "checkpoint": checkpoint_path.name,
                    "checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
                    "construction_delivery_delta": len(delivery.records) - delivered_before,
                    "duplicate_recovery_status": "unverified",
                }
                restarts.append(restart)
                restart_index += 1
                row["status"] = "reopened"
                if not restart["same_state"] or restart["construction_delivery_delta"]:
                    row["errors"].append("restart_continuity_mismatch")
            elif next_turn is not None and next_turn <= clock.now():
                turn = turns[turn_index]
                row.update(kind="inbound", user_text=turn["text"], turn_id=turn["id"])
                result = await bounded(
                    host.inbound_text(
                        message_id=f"journey-{turn['id']}",
                        recipient_id=RECIPIENT,
                        text=turn["text"],
                        observed_at=clock.now(),
                    )
                )
                row["status"] = result.status
                turn_index += 1
            else:
                row["status"] = "scheduled"
            statuses, exhausted = await drain()
            row["drain_statuses"] = statuses
            row["queue_quiescent"] = exhausted
            row["drained_to_idle"] = exhausted and not any(
                status in {"not_due", "owned_elsewhere"} for status in (*pre_statuses, *statuses)
            )
            if not exhausted:
                row["errors"].append("drain_limit_reached")
            row["wall_seconds"] = time.monotonic() - started
            capture(row)
            if budget_was_denied(database):
                stop_reason = "budget_admission_denied"
                break
            if row["errors"]:
                stop_reason = row["errors"][0]
                break
            if checkpoint is not None and clock.now() >= checkpoint:
                checkpoint = None
        due_snapshot_sequence_before = sequence + len(read_events(database, sequence))
        final_wake_snapshot = await bounded(host.scheduler_wake_snapshot())
        due_snapshot_sequence_after = sequence + len(read_events(database, sequence))
    except Exception as exc:
        traceback.print_exc()
        stop_reason = (
            "wall_time_limit"
            if isinstance(exc, JourneyDeadlineExceeded)
            else f"technical_failure:{type(exc).__name__}"
        )
        # The exception type is safe for the overview; detailed provider data
        # remains in the local ledger, never copied from arbitrary exceptions.
        if database.exists():
            capture(
                {**row, "kind": "failure", "status": "technical_failure", "errors": [stop_reason]}
            )
    finally:
        try:
            await shutdown()
        except Exception as exc:
            stop_reason = f"technical_failure:shutdown:{type(exc).__name__}"
        if host is not None:
            try:
                exported = cold_evidence(database)
                final_logical_time = exported.projection.logical_time
                evaluation = ReplayEvaluator().evaluate(evidence=exported)
                replay = asdict(evaluation)
                if stop_reason == "completed":
                    if not evaluation.passed or not evaluation.replay_hash_matches:
                        stop_reason = "replay_failure"
                    elif time.monotonic() - wall_started > limits.max_wall_seconds:
                        stop_reason = "wall_time_limit"
                    elif final_logical_time is None or final_wake_snapshot is None:
                        stop_reason = "logical_time_did_not_reach_end"
                    elif due_snapshot_sequence_before != due_snapshot_sequence_after or (
                        due_snapshot_sequence_after
                        != sequence + len(read_events(database, sequence))
                    ):
                        stop_reason = "completion_unverified_after_state_change"
                    elif any(item.due_at <= end for item in final_wake_snapshot.dues):
                        stop_reason = "unprocessed_due_before_end"
                usage = host.usage_budget_health()
                capture({"kind": "final", "status": stop_reason, "errors": []})
            except Exception as exc:
                stop_reason = f"technical_failure:final_evidence:{type(exc).__name__}"

    provider_usage_evidence = read_provider_usage_evidence(database)
    (output / "provider-usage.json").write_text(_json(provider_usage_evidence) + "\n")
    manifest = {
        "contract": CONTRACT,
        "scenario_id": journey.scenario_id,
        "synthetic": synthetic,
        "completed": stop_reason == "completed",
        "human_likeness_status": "unassessed",
        "qualification_status": "manual_only",
        "requested_virtual_days": journey.duration_minutes / 1440,
        "elapsed_virtual_seconds": (clock.now() - journey.started_at).total_seconds(),
        "final_logical_time": final_logical_time.isoformat() if final_logical_time else None,
        "elapsed_logical_seconds": (
            (final_logical_time - journey.started_at).total_seconds() if final_logical_time else 0
        ),
        "clock_coverage": "registered_due_with_bounded_heartbeat",
        "quiet_tail_seconds": (
            max(0, (end - final_logical_time).total_seconds()) if final_logical_time else None
        ),
        "model_failures": model_failures(evidence),
        "provider_usage_evidence": provider_usage_evidence["summary"],
        "due_snapshot_sequence_before": due_snapshot_sequence_before,
        "due_snapshot_sequence_after": due_snapshot_sequence_after,
        "final_ledger_sequence": sequence,
        "wall_seconds": time.monotonic() - wall_started,
        "stop_reason": stop_reason,
        "turns_consumed": turn_index,
        "turns_requested": len(turns),
        "interaction_mode": "adaptive" if next_command is not None else "scripted",
        "limits": asdict(limits),
        "restarts": restarts,
        "replay": replay,
        "usage": usage,
        "model_input_capture": (
            model_input_capture.health()
            if model_input_capture is not None
            else {"status": "unverified"}
        ),
        "provenance": provenance or {},
        "profile_differences": (provenance or {}).get("profile_differences", []),
        "life_source_review": (provenance or {}).get(
            "life_source_review", {"status": "unverified"}
        ),
        "safety": {"real_qq": False, "fresh_database": True, "external_world_feeds": False},
        "exclusions": [
            "real QQ receipts",
            "real-time endurance",
            "future real-world news",
            "automatic human-likeness verdict",
            "cross-database monthly account total",
        ],
        "artifacts": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in (
                "timeline.jsonl",
                "evidence.jsonl",
                "model-inputs.jsonl",
                "operator-commands.jsonl",
                "provider-usage.json",
            )
            if (output / name).exists()
        },
    }
    if next_command is not None:
        # Final settlement/shutdown can deliver after the last operator read.
        # Expose that unread tail without opening another command or model call.
        manifest["operator_final_observation"] = operator_observation()
    (output / "manifest.json").write_text(_json(manifest) + "\n")
    from .longitudinal_review import build_review_packet, render_longitudinal_report

    packet = build_review_packet(timeline=timeline, evidence=evidence, run_manifest=manifest)
    (output / "review.json").write_text(_json(packet) + "\n")
    (output / "report.md").write_text(
        render_longitudinal_report(manifest=manifest, timeline=timeline, evidence=evidence)
    )
    return manifest
