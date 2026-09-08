from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from character_interior import canonical_inner_decision
from companion_daemon.world_v2.experience_memory_candidate_lifecycle import (
    ExperienceMemoryCandidateLifecycle,
)
from companion_daemon.world_v2.fact_memory_draft import FactMemoryDraftTechnicalFailure
from companion_daemon.world_v2.life_aftermath_runtime import LifeAftermathRuntime
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore,
    StoredLifeContent,
    life_content_payload_hash,
)
from companion_daemon.world_v2.occurrence_content_coordinator import (
    OccurrenceContentCoordinator,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

import test_experience_authority as authority


FIRST = "event:experience:commit"
SECOND = "event:experience:second"


class _CharacterMemoryChoice:
    """Explicit character-port decisions; no provider or background author."""

    def __init__(self, *, failures: dict[str, int]) -> None:
        self.failures = dict(failures)
        self.sources: list[str] = []

    async def consider(self, opportunity):  # type: ignore[no-untyped-def]
        source = opportunity.trigger_ref
        self.sources.append(source)
        if self.failures.get(source, 0):
            self.failures[source] -= 1
            raise FactMemoryDraftTechnicalFailure("provider_exception")
        manifest = opportunity.capability_manifest
        assert manifest is not None
        return canonical_inner_decision(
            opportunity,
            identity=f"memory-choice:{source}:{len(self.sources)}",
            decision={
                "contract": "character-interior-purpose-decision.1",
                "purpose": "experience_memory_retention",
                "source_refs": list(opportunity.source_refs),
                "capability_ref": manifest.capability_ref,
                "capability_payload_hash": manifest.payload_hash,
                "payload": {
                    "contract": "character-interior-experience-memory-retention.1",
                    "retain": False,
                },
            },
        )


def _seed_backlog(path: Path):  # type: ignore[no-untyped-def]
    ledger = authority.initialized(
        kind=lambda *, world_id: SQLiteWorldLedger(path=path, world_id=world_id)
    )
    store = InMemoryImmutableLifeContentStore()
    text = "The authorized external action was cancelled."
    content_hash = life_content_payload_hash(text)
    first = authority.experience(summary_payload_hash=content_hash)
    store.put_if_absent(
        StoredLifeContent(
            content_ref=first.values.summary_ref,
            content_kind="experience_summary",
            content_payload_hash=content_hash,
            text=text,
        )
    )
    authority.record_accept_mutate(
        ledger,
        authority.mutation(
            first,
            proposal_id="proposal:memory-first",
            evaluated_world_revision=ledger.project().world_revision,
        ),
    )
    second_action, second_receipt = authority.seed_second_receipt_authority(ledger)
    second_binding = authority.binding(
        receipt_id=second_receipt.receipt_id,
        authority=second_receipt,
        action_authority=second_action,
    )
    second = authority.experience(
        source_bindings=(second_binding,),
        experience_id="experience:second",
        transition_id="transition:second",
        accepted_event_ref=SECOND,
        summary_payload_hash=content_hash,
    )
    second = second.model_copy(
        update={"origin": second.origin.model_copy(update={"change_id": "change:second"})}
    )
    authority.record_accept_mutate(
        ledger,
        authority.mutation(
            second,
            proposal_id="proposal:memory-second",
            evaluated_world_revision=ledger.project().world_revision,
            evidence_refs=(authority.evidence(second_binding),),
        ),
    )
    return ledger, store


def _runtime(ledger, store, character):  # type: ignore[no-untyped-def]
    return LifeAftermathRuntime(
        ledger=ledger,
        catalog=SimpleNamespace(),
        occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=store),
        content_store=store,
        owner_actor_ref="actor:companion",
        character_interior=character,
        experience_memory_lifecycle=ExperienceMemoryCandidateLifecycle(
            ledger=ledger, actor="worker:test-memory", source="test:memory", content_store=store
        ),
    )


async def _advance(runtime, wake="clock:start"):  # type: ignore[no-untyped-def]
    return await runtime.advance_once(
        wake_event_ref=wake, trace_id="trace:memory-queue", correlation_id="correlation:memory-queue"
    )


def _clock(ledger, *, at, name):  # type: ignore[no-untyped-def]
    projection = ledger.project()
    event = authority.event(
        name,
        "ClockAdvanced",
        {"logical_time_from": projection.logical_time.isoformat(), "logical_time_to": at.isoformat()},
        at=at,
    )
    ledger.commit(
        (event,),
        expected_world_revision=projection.world_revision,
        expected_deliberation_revision=projection.deliberation_revision,
    )
    return event.event_id


def _decisions(ledger):  # type: ignore[no-untyped-def]
    return [
        event.payload()
        for event in ledger.recent_events_by_type(
            event_types=frozenset({"ExperienceMemoryDecisionRecorded"}),
            since=authority.NOW,
            limit=20,
        )
    ]


@pytest.mark.asyncio
async def test_retry_wait_does_not_block_another_experience_after_cold_restart(tmp_path: Path) -> None:
    path = tmp_path / "memory-queue.sqlite"
    ledger, store = _seed_backlog(path)
    character = _CharacterMemoryChoice(failures={FIRST: 1})
    with pytest.raises(FactMemoryDraftTechnicalFailure, match="provider_exception"):
        await _advance(_runtime(ledger, store, character))
    original_retry = ledger.project().contextual_life_retries[0]
    assert original_retry.next_retry_at == authority.NOW + timedelta(minutes=10)
    ledger.close()

    ledger = SQLiteWorldLedger(path=path, world_id=authority.WORLD)
    try:
        result = await _advance(_runtime(ledger, store, character))
        assert [item["experience_id"] for item in _decisions(ledger)] == ["experience:second"]
        assert _decisions(ledger)[0]["decision_kind"] == "no_change"
        assert result.status != "recovered_memory"
        assert ledger.project().memory_candidates == ()
        assert ledger.project().contextual_life_retries == (original_retry,)
        assert character.sources == [FIRST, SECOND]

        await _advance(_runtime(ledger, store, character))
        assert character.sources == [FIRST, SECOND]
        wake = _clock(ledger, at=original_retry.next_retry_at, name="clock:memory-retry-due")
        await _advance(_runtime(ledger, store, character), wake)
        assert character.sources == [FIRST, SECOND, FIRST]
        assert {item["experience_id"] for item in _decisions(ledger)} == {
            "experience:receipt", "experience:second"
        }
        assert ledger.project().contextual_life_retries == ()
        assert ledger.lookup_event_commit(original_retry.failure_event_ref) is not None
    finally:
        ledger.close()
