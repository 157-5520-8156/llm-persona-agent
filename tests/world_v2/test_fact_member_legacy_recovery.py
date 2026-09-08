"""Current Fact runtime rejoins durable .3 choices without changing their audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.fact_draft_adapter import FactObservationProposalAdapter
from companion_daemon.world_v2.fact_trigger import (
    interaction_fact_decision_event_id,
    interaction_fact_trigger_event,
)
from companion_daemon.world_v2.fact_v2_acceptance_runtime import FactV2AcceptanceRuntime
from companion_daemon.world_v2.interaction_fact_trigger_runtime import InteractionFactTriggerRuntime
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

from test_interaction_fact_trigger_runtime import (
    NOW,
    WORLD_ID,
    _home_observation,
    _NoChangeChat,
    _WithdrawalChat,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class _LegacyAdapter(FactObservationProposalAdapter):
    VERSION = "fact-observation-draft.3"


class _LegacyRuntime(InteractionFactTriggerRuntime):
    """Produce the original single-Fact context and request identity from 788d9e7b.

    Only historical fixture construction uses these overrides. Recovery uses
    the current runtime and adapter without overrides.
    """

    _fact_authority_context = staticmethod(
        InteractionFactTriggerRuntime._single_fact_authority_context
    )

    async def _record_decision(self, **kwargs):  # type: ignore[no-untyped-def]
        assert not kwargs.get("current_set_fact_sources")
        source = kwargs["source_event"]
        kwargs["request_hash"] = _digest(
            {
                "adapter_version": _LegacyAdapter.VERSION,
                "source_event_ref": source.event_id,
                "source_payload_hash": source.payload_hash,
                "evaluated_cursor": kwargs["evaluated_cursor"].model_dump(mode="json"),
                "current_single_fact_sources": kwargs["current_single_fact_sources"],
                "fact_context_hash": kwargs["fact_context_hash"],
            }
        )
        return await super()._record_decision(**kwargs)


class _UnusedCurrentModel:
    model = "test-current-model-must-not-replace-legacy-author"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, _messages, *, temperature=0.2):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("a durable legacy decision must not call the current model")


def _runtime(ledger, issuer, model, *, legacy=False):  # type: ignore[no-untyped-def]
    runtime_type = _LegacyRuntime if legacy else InteractionFactTriggerRuntime
    adapter_type = _LegacyAdapter if legacy else FactObservationProposalAdapter
    return runtime_type(
        ledger=ledger,
        acceptance=FactV2AcceptanceRuntime.compose(ledger=ledger, batch_issuer=issuer),
        adapter=adapter_type(model=model),
        owner_id="worker:legacy-fact-recovery",
    )


def _record_observation(ledger, *, index, text):  # type: ignore[no-untyped-def]
    observation, event = _home_observation(index, text)
    projection = ledger.project()
    ledger.commit(
        (
            event,
            interaction_fact_trigger_event(observation=observation, observation_event=event),
        ),
        expected_world_revision=projection.world_revision,
        expected_deliberation_revision=projection.deliberation_revision,
    )
    return observation


def _decision_audit_bytes(ledger: SQLiteWorldLedger) -> tuple[bytes, ...]:
    return tuple(
        event.model_dump_json().encode()
        for event in ledger.recent_events_by_type(
            event_types=frozenset({"InteractionFactDecisionRecorded"}),
            since=NOW,
            limit=20,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_kind", ("withdraw", "no_change"))
async def test_current_runtime_rejoins_legacy_single_fact_decision_after_cold_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision_kind: str,
) -> None:
    path = tmp_path / f"legacy-single-{decision_kind}.sqlite3"
    issuer = AcceptedLedgerBatchIssuer()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    try:
        _record_observation(ledger, index=1, text="我住在杭州。")
        seeded = await _runtime(ledger, issuer, _WithdrawalChat(), legacy=True).drain_one()
        assert seeded.work_status == "accepted"
        (original_fact,) = ledger.project().facts
        assert original_fact.values.cardinality == "single"

        observation = _record_observation(
            ledger,
            index=2,
            text="我已经不住杭州了。" if decision_kind == "withdraw" else "最近没什么变化。",
        )
        model = _WithdrawalChat() if decision_kind == "withdraw" else _NoChangeChat()
        original_commit = ledger.commit_at_cursor

        def crash_after_decision(events, *, expected_cursor, commit_id=None):
            result = original_commit(
                events,
                expected_cursor=expected_cursor,
                commit_id=commit_id,
            )
            if any(event.event_type == "InteractionFactDecisionRecorded" for event in events):
                raise RuntimeError("process lost after durable legacy decision")
            return result

        with monkeypatch.context() as crash:
            crash.setattr(ledger, "commit_at_cursor", crash_after_decision)
            with pytest.raises(RuntimeError, match="durable legacy decision"):
                await _runtime(ledger, issuer, model, legacy=True).drain_one()
        assert model.calls == 1
        before = ledger.project()
        assert before.facts == (original_fact,)
        (decision,) = (
            item
            for item in before.interaction_fact_decisions
            if item.source_observation_ref == observation.observation_id
        )
        assert decision.adapter_version == "fact-observation-draft.3"
        assert decision.model_id == model.model
        assert decision.batch_size == 1
        assert decision.decision_kind == decision_kind
        assert decision.fact_context_hash == _digest([original_fact.model_dump(mode="json")])
        assert set(json.loads(decision.decision_json)) == (
            {"predicate_code", "assertion_source_ref", "confidence_bp", "brief_rationale"}
            if decision_kind == "withdraw"
            else {"decision"}
        )
        event_id = interaction_fact_decision_event_id(
            trigger_id=decision.trigger_id,
            fact_context_hash=decision.fact_context_hash,
        )
        recorded = ledger.lookup_event_commit(event_id)
        assert recorded is not None
        original_event_bytes = recorded[0].model_dump_json().encode()
        original_audits = _decision_audit_bytes(ledger)
        assert len(original_audits) == 2
        ledger.close()

        issuer = AcceptedLedgerBatchIssuer()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
        current_model = _UnusedCurrentModel()
        assert FactObservationProposalAdapter.VERSION == "fact-observation-draft.4"
        runtime = _runtime(ledger, issuer, current_model)
        result = await runtime.drain_one()

        assert result.work_status == ("accepted" if decision_kind == "withdraw" else "no_change")
        assert current_model.calls == 0
        after = ledger.project()
        # Terminal processes leave the recovery index; their original audit
        # events must remain byte-identical in the durable history.
        assert _decision_audit_bytes(ledger) == original_audits
        restored = ledger.lookup_event_commit(event_id)
        assert restored is not None
        assert restored[0].model_dump_json().encode() == original_event_bytes
        (fact,) = after.facts
        if decision_kind == "withdraw":
            assert fact.fact_id == original_fact.fact_id
            assert fact.entity_revision == original_fact.entity_revision + 1
            assert fact.values.status == "withdrawn"
            assert fact.values.value_hash == original_fact.values.value_hash
            assert sum(item.operation == "withdraw" for item in after.fact_transitions) == 1
        else:
            assert fact == original_fact
        assert all(item.state == "terminal" for item in after.trigger_processes)
        assert (await runtime.drain_one()).status == "idle"
        assert current_model.calls == 0
        assert ledger.project().semantic_hash == after.semantic_hash
    finally:
        ledger.close()
