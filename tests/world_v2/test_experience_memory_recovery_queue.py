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
from companion_daemon.world_v2.life_content_events import LifeContentRecordedPayload
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

    def __init__(self, *, failures: dict[str, int], retain: tuple[str, ...] = ()) -> None:
        self.failures = dict(failures)
        self.retain = retain
        self.sources: list[str] = []

    async def consider(self, opportunity):  # type: ignore[no-untyped-def]
        source = opportunity.trigger_ref
        self.sources.append(source)
        if self.failures.get(source, 0):
            self.failures[source] -= 1
            raise FactMemoryDraftTechnicalFailure("provider_exception")
        manifest = opportunity.capability_manifest
        assert manifest is not None
        choice = {"retain": False}
        if source in self.retain:
            choice = {
                "retain": True,
                "cue_kind": "world_continuity",
                "retention_rationales": ["world_continuity"],
                "salience": {
                    "autobiographical_relevance_bp": 7000,
                    "relationship_relevance_bp": 2000,
                    "emotional_residue_bp": 2000,
                    "unfinished_business_bp": 1000,
                    "recurrence_bp": 3000,
                    "novelty_bp": 6000,
                    "future_utility_bp": 5000,
                    "world_continuity_bp": 9000,
                },
            }
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
                    **choice,
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


def _record_content_descriptor(ledger, experience):  # type: ignore[no-untyped-def]
    projection = ledger.project()
    source = next(
        ref for ref in projection.committed_world_event_refs
        if ref.event_id == experience.origin.accepted_event_ref
    )
    payload = LifeContentRecordedPayload(
        content_id=f"life-content:{experience.experience_id}",
        content_kind="experience_summary",
        content_ref=experience.values.summary_ref,
        content_payload_hash=experience.values.summary_payload_hash,
        privacy_class=experience.values.privacy_class,
        source_kind="experience",
        source_event_ref=source.event_id,
        source_world_revision=source.world_revision,
        source_payload_hash=source.payload_hash,
        source_entity_id=experience.experience_id,
        source_entity_revision=experience.entity_revision,
    )
    ledger.commit(
        (authority.event(
            f"event:content:{experience.experience_id}", "LifeContentRecorded",
            payload.model_dump(mode="json"), at=projection.logical_time,
        ),),
        expected_world_revision=projection.world_revision,
        expected_deliberation_revision=projection.deliberation_revision,
    )


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

        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=authority.WORLD)
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


@pytest.mark.asyncio
async def test_exhausted_source_stays_failed_without_starving_another_experience(tmp_path: Path) -> None:
    path = tmp_path / "exhausted-memory-queue.sqlite"
    ledger, store = _seed_backlog(path)
    character = _CharacterMemoryChoice(failures={FIRST: 8})
    wake = "clock:start"
    try:
        for attempt in range(8):
            with pytest.raises(FactMemoryDraftTechnicalFailure, match="provider_exception"):
                await _advance(_runtime(ledger, store, character), wake)
            retry = ledger.project().contextual_life_retries[0]
            assert retry.retry_ordinal == attempt + 1
            wake = _clock(ledger, at=retry.next_retry_at, name=f"clock:memory-failure:{attempt}")
        assert character.sources == [FIRST] * 8
        original_failure = ledger.lookup_event_commit(retry.failure_event_ref)
        original_experiences = ledger.project().experiences
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=authority.WORLD)

        result = await _advance(_runtime(ledger, store, character), wake)
        assert [item["experience_id"] for item in _decisions(ledger)] == ["experience:second"]
        assert _decisions(ledger)[0]["decision_kind"] == "no_change"
        assert result.status != "recovered_memory"
        assert character.sources == [FIRST] * 8 + [SECOND]
        for day in range(3):
            ledger.close()
            ledger = SQLiteWorldLedger(path=path, world_id=authority.WORLD)
            wake = _clock(
                ledger,
                at=ledger.project().logical_time + timedelta(days=1),
                name=f"clock:exhausted-memory-day:{day}",
            )
            await _advance(_runtime(ledger, store, character), wake)
        assert character.sources == [FIRST] * 8 + [SECOND]
        assert ledger.project().memory_candidates == ()
        assert ledger.project().experiences == original_experiences
        assert ledger.project().contextual_life_retries == (retry,)
        assert ledger.lookup_event_commit(retry.failure_event_ref) == original_failure
        assert len(_decisions(ledger)) == 1
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_one_eligible_experience_per_pass_keeps_the_character_retention_choice(tmp_path: Path) -> None:
    ledger, store = _seed_backlog(tmp_path / "one-memory-per-pass.sqlite")
    character = _CharacterMemoryChoice(failures={}, retain=(FIRST,))
    try:
        first = ledger.project().experiences[0]
        _record_content_descriptor(ledger, first)
        result = await _advance(_runtime(ledger, store, character))
        assert result.status == "recovered_memory"
        assert result.experience_id == first.experience_id
        assert character.sources == [FIRST]
        candidate, = ledger.project().memory_candidates
        assert candidate.values.status == "active"
        assert candidate.values.source_bindings[0].source_id == first.experience_id
        assert candidate.values.privacy_ceiling == "private"
        assert [item["decision_kind"] for item in _decisions(ledger)] == ["retain"]

        await _advance(_runtime(ledger, store, character))
        assert character.sources == [FIRST, SECOND]
        assert [item["decision_kind"] for item in _decisions(ledger)] == ["retain", "no_change"]
        assert ledger.project().memory_candidates == (candidate,)
    finally:
        ledger.close()


class _UnavailableContent:
    """An immutable sidecar read fails after the author consumed its bytes."""

    def __init__(self, store) -> None:  # type: ignore[no-untyped-def]
        self.store = store
        self.reads = 0

    def read_exact(self, *, content_ref):  # type: ignore[no-untyped-def]
        self.reads += 1
        if self.reads > 1:
            raise OSError("sidecar temporarily unavailable")
        return self.store.read_exact(content_ref=content_ref)


@pytest.mark.asyncio
async def test_cold_recovery_accepts_the_durable_retention_choice_without_reasking(tmp_path: Path) -> None:
    path = tmp_path / "authored-retention-recovery.sqlite"
    ledger, store = _seed_backlog(path)
    character = _CharacterMemoryChoice(failures={}, retain=(FIRST,))
    first = ledger.project().experiences[0]
    _record_content_descriptor(ledger, first)
    with pytest.raises(OSError, match="sidecar temporarily unavailable"):
        await _advance(_runtime(ledger, _UnavailableContent(store), character))
    assert ledger.project().memory_candidates == ()
    assert [item["decision_kind"] for item in _decisions(ledger)] == ["retain"]
    original_decision = _decisions(ledger)[0]
    ledger.close()

    ledger = SQLiteWorldLedger(path=path, world_id=authority.WORLD)
    try:
        result = await _advance(_runtime(ledger, store, character))
        assert result.status == "recovered_memory"
        assert result.experience_id == first.experience_id
        assert character.sources == [FIRST]
        assert _decisions(ledger) == [original_decision]
        candidate, = ledger.project().memory_candidates
        assert candidate.values.status == "active"
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=authority.WORLD)
        await _advance(_runtime(ledger, store, character))
        await _advance(_runtime(ledger, store, character))
        assert character.sources == [FIRST, SECOND]
        assert ledger.project().memory_candidates == (candidate,)
    finally:
        ledger.close()
