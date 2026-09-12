from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime

import pytest

from companion_daemon.world_v2.deliberation import (
    Deliberation,
    ModelOutput,
    ModelUsageProvenance,
    ValidationTechnicalFailure,
)
from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
from companion_daemon.world_v2.proposal_audit import ProposalAuditRecorder
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_proposal_audit import WORLD, _context, _started, _world_change
from test_deliberation import (
    _ManualClock,
    _Router,
    _SameAuthorHedgePort,
    _StreamingPrimary,
    _capsule,
    _hedge_budget,
)


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda item: item.isoformat() if isinstance(item, datetime) else item,
        ).encode()
    ).hexdigest()


class _Primary(_StreamingPrimary):
    async def propose(self, request):
        return await self.propose_stream_head(request)

    async def propose_stream_head(self, request):
        output = await super().propose_stream_head(request)
        return output.model_copy(update={"input_tokens": 14_000, "output_tokens": 300})


class _MeteredHedge(_SameAuthorHedgePort):
    output: ModelOutput | None = None

    def __init__(self, *, terminal_failure=None, **kwargs):
        super().__init__(**kwargs)
        self.terminal_failure = terminal_failure
        self.completed = asyncio.Event()

    async def propose_hedge(self, request):
        output = await super().propose_hedge(request)
        material = {
            "usage_contract": "model-usage.1",
            "route_class": "expressive",
            "input_tokens": 12_500,
            "output_tokens": 200,
            "thinking_tokens": 0,
            "token_provenance": "offline_estimated",
            "transport": "offline_fixture",
            "provider": "test-same-role",
            "provider_usage_ref": "usage:hedge:fixture",
        }
        self.output = output.model_copy(
            update={
                "input_tokens": 12_500,
                "output_tokens": 200,
                "usage": ModelUsageProvenance(**material, provider_usage_hash=_hash(material)),
            }
        )
        self.completed.set()
        if self.terminal_failure is not None:
            raise ValidationTechnicalFailure(
                self.terminal_failure,
                attempted_model_id="hedge",
                attempted_model_version="v1",
                usage=self.output.usage,
                failure_detail="bounded review failure fixture",
            )
        return self.output


class _ObservedValidation(Deliberation):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.hedge_rejected = asyncio.Event()
        self.primary_rejected = asyncio.Event()
        self.validated = []

    def _validated_proposal(self, output, *args, **kwargs):
        try:
            result = super()._validated_proposal(output, *args, **kwargs)
            self.validated.append(output.model_id)
            return result
        except (TypeError, ValueError):
            if output.model_id == "hedge":
                self.hedge_rejected.set()
            elif output.model_id == "stream-head":
                self.primary_rejected.set()
            raise


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "stream"])
async def test_completed_invalid_hedge_keeps_response_and_usage_when_primary_wins(mode, tmp_path):
    clock = _ManualClock()
    primary = _Primary(block_head=True)
    hedge = _MeteredHedge(raw={"not": "a decision"})
    deliberation = _ObservedValidation(
        router=_Router(),
        main_model=primary,
        quick_recovery=hedge,
        expression_episode_mode=mode,
    )
    turn = asyncio.create_task(
        deliberation.deliberate(
            _capsule(), attempt_id="attempt:completed-invalid-hedge", budget=_hedge_budget(clock)
        )
    )
    try:
        await asyncio.wait_for(primary.head_started.wait(), timeout=1)
        await clock.advance(1.5)
        await asyncio.wait_for(deliberation.hedge_rejected.wait(), timeout=1)
        primary.release_tail.set()
        primary.release_head.set()
        result = await asyncio.wait_for(turn, timeout=1)

        assert result.proposal is not None
        assert result.audit.slot == "primary"
        assert len(result.attempt_audits) == 2
        loser, winner = result.attempt_audits
        assert winner.outcome == "winner"
        assert loser.outcome == "invalid"
        assert loser.failure_code == "backup_invalid"
        assert loser.failure_detail and "invalid_output" in loser.failure_detail
        assert loser.model_id == "hedge"
        assert loser.response_hash == _hash(hedge.raw)
        assert (loser.input_tokens, loser.output_tokens) == (12_500, 200)
        assert loser.usage == hedge.output.usage
        recorded = RecordedModelResultAudit.model_validate_json(loser.model_dump_json())
        assert recorded.input_tokens == 12_500
        assert recorded.response_hash == loser.response_hash
        assert recorded.failure_detail == loser.failure_detail
        assert len({item.model_call_id for item in result.attempt_audits}) == 2
        assert not hedge.cancelled.is_set()
        _assert_recorded_and_replayed(result, tmp_path)
        for change in (
            {"slot": "primary"},
            {"failure_code": "backup_invented"},
            {"outcome": "winner"},
            {"status": "proposal_validated"},
            {"model_call_id": winner.model_call_id},
        ):
            with pytest.raises(ValueError):
                Deliberation._result(
                    _capsule().capsule,
                    proposal=result.proposal,
                    audit=winner,
                    attempt_audits=(loser.model_copy(update=change), winner),
                )
    finally:
        primary.release_head.set()
        primary.release_tail.set()
        hedge.release.set()
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await deliberation.aclose()


def _assert_recorded_and_replayed(result, tmp_path):
    path = tmp_path / "hedge-audit.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    _started(ledger)
    revision = result.proposal.evaluated_world_revision
    for previous_revision in range(1, revision):
        ledger.commit(
            [_world_change(f"event:fixture-world-change:{previous_revision}")],
            expected_world_revision=previous_revision,
            expected_deliberation_revision=0,
        )
    context = _context(commit_world_revision=revision).model_copy(
        update={
            "trigger_ref": result.proposal.trigger_ref,
            "evaluated_world_revision": result.proposal.evaluated_world_revision,
        }
    )
    recorder = ProposalAuditRecorder(ledger=ledger)
    recorder.record(result, context)
    recorder.record(result, context)  # The same attempt remains effect-once.
    before = ledger.project()
    assert len(before.model_result_audits) == 2
    assert len(before.proposal_audits) == 1
    original = [item.audit_json for item in before.model_result_audits]
    ledger.close()
    reopened = SQLiteWorldLedger(path=path, world_id=WORLD)
    try:
        rebuilt = reopened.rebuild()
        assert rebuilt.semantic_hash == before.semantic_hash
        assert [item.audit_json for item in rebuilt.model_result_audits] == original
        recorded = {
            item.model_call_id: RecordedModelResultAudit.model_validate_json(item.audit_json)
            for item in rebuilt.model_result_audits
        }
        for audit in result.attempt_audits:
            actual = recorded[audit.model_call_id]
            assert actual.response_hash == audit.response_hash
            assert actual.input_tokens == audit.input_tokens
            assert actual.output_tokens == audit.output_tokens
            assert actual.failure_code == audit.failure_code
            assert actual.failure_detail == audit.failure_detail
            assert actual.outcome == audit.outcome
    finally:
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "stream"])
@pytest.mark.parametrize("already_complete", [False, True])
async def test_primary_winner_distinguishes_pending_hedge_from_simultaneous_valid_result(
    mode,
    already_complete,
    tmp_path,
):
    clock = _ManualClock()
    primary = _Primary(block_head=True)
    hedge = _MeteredHedge(block=True)
    deliberation = _ObservedValidation(
        router=_Router(),
        main_model=primary,
        quick_recovery=hedge,
        expression_episode_mode=mode,
    )
    turn = asyncio.create_task(
        deliberation.deliberate(
            _capsule(), attempt_id="attempt:hedge-race-terminal-state", budget=_hedge_budget(clock)
        )
    )
    try:
        await asyncio.wait_for(primary.head_started.wait(), timeout=1)
        await clock.advance(1.5)
        await asyncio.wait_for(hedge.started.wait(), timeout=1)
        primary.release_tail.set()
        primary.release_head.set()
        if already_complete:
            # Release both provider outputs in one event-loop turn. The main
            # tie-break remains unchanged; the other valid result is evidence.
            hedge.release.set()
        result = await asyncio.wait_for(turn, timeout=1)
        assert result.audit.slot == "primary"
        assert result.proposal is not None
        assert len(result.attempt_audits) == 2
        loser, winner = result.attempt_audits
        assert len({item.model_call_id for item in result.attempt_audits}) == 2
        assert winner.outcome == "winner"
        if already_complete:
            assert "hedge" in deliberation.validated
            assert loser.status == "candidate_returned"
            assert loser.outcome == "returned"
            assert loser.failure_code is None
            assert loser.usage == hedge.output.usage
            assert loser.response_hash == _hash(hedge.raw)
            assert not hedge.cancelled.is_set()
        else:
            assert loser.outcome == "hedge_cancelled"
            assert loser.response_hash is None
            assert loser.usage is None
            assert hedge.cancelled.is_set()
        _assert_recorded_and_replayed(result, tmp_path)
    finally:
        primary.release_head.set()
        primary.release_tail.set()
        hedge.release.set()
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await deliberation.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "stream"])
async def test_valid_hedge_winner_keeps_completed_invalid_primary_audit(mode):
    clock = _ManualClock()
    primary = _Primary(raw={"not": "a decision"}, block_head=True)
    hedge = _MeteredHedge(block=True)
    deliberation = _ObservedValidation(
        router=_Router(),
        main_model=primary,
        quick_recovery=hedge,
        expression_episode_mode=mode,
    )
    turn = asyncio.create_task(
        deliberation.deliberate(
            _capsule(), attempt_id="attempt:completed-invalid-primary", budget=_hedge_budget(clock)
        )
    )
    try:
        await asyncio.wait_for(primary.head_started.wait(), timeout=1)
        await clock.advance(1.5)
        await asyncio.wait_for(hedge.started.wait(), timeout=1)
        primary.release_head.set()
        await asyncio.wait_for(deliberation.primary_rejected.wait(), timeout=1)
        hedge.release.set()
        result = await asyncio.wait_for(turn, timeout=1)
        assert result.proposal is not None
        assert result.audit.slot == "backup"
        assert len(result.attempt_audits) == 2
        loser, winner = result.attempt_audits
        assert loser.outcome == "invalid"
        assert loser.input_tokens == 14_000
        assert loser.response_hash == _hash(primary.raw)
        assert winner.outcome == "winner"
        assert winner.usage == hedge.output.usage
    finally:
        primary.release_head.set()
        primary.release_tail.set()
        hedge.release.set()
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await deliberation.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,outcome",
    [
        ("source_review_exception", "exception"),
        ("source_review_timeout", "timeout"),
        ("inventory_invalid", "invalid"),
    ],
)
async def test_completed_technical_hedge_failure_keeps_its_usage_and_precise_reason(
    failure,
    outcome,
    tmp_path,
):
    clock = _ManualClock()
    primary = _Primary(block_head=True)
    hedge = _MeteredHedge(terminal_failure=failure)
    deliberation = Deliberation(
        router=_Router(),
        main_model=primary,
        quick_recovery=hedge,
    )
    turn = asyncio.create_task(
        deliberation.deliberate(
            _capsule(), attempt_id="attempt:terminal-review-hedge", budget=_hedge_budget(clock)
        )
    )
    try:
        await asyncio.wait_for(primary.head_started.wait(), timeout=1)
        await clock.advance(1.5)
        await asyncio.wait_for(hedge.completed.wait(), timeout=1)
        primary.release_head.set()
        result = await asyncio.wait_for(turn, timeout=1)
        loser, winner = result.attempt_audits
        assert winner.outcome == "winner"
        assert loser.outcome == outcome
        assert loser.failure_code == f"backup_{failure}"
        assert loser.failure_detail == "bounded review failure fixture"
        assert loser.attempted_model_id == "hedge"
        assert loser.usage == hedge.output.usage
        _assert_recorded_and_replayed(result, tmp_path)
    finally:
        primary.release_head.set()
        primary.release_tail.set()
        hedge.release.set()
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await deliberation.aclose()
