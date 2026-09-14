"""All memory lanes obey the current candidate image before reading content."""

from dataclasses import dataclass

import pytest

from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalCompiler
from companion_daemon.world_v2.schemas import MemoryCandidateTransitionProjection
from test_memory_retrieval import (
    NOW,
    _MemoryReadLedger,
    _experience_memory_read_fixture,
    _message_fact_read_ledger,
)
import test_memory_candidate_authority as authority


@dataclass
class Fixture:
    ledger: object
    candidate: object
    cursor: object
    store: object = None

    def read(self, candidate=None, *, privacy="private"):
        return MemoryRetrievalCompiler(ledger=self.ledger, life_content_store=self.store).compile(
            cursor=self.cursor,
            candidates=(candidate or self.candidate,),
            viewer_privacy_ceiling=privacy,
        )

    def forbid_content_reads(self, monkeypatch):
        def fail(*args, **kwargs):
            raise AssertionError("ineligible memory reached original content")

        monkeypatch.setattr(self.ledger, "observation_events_at", fail)
        if self.store is not None:
            monkeypatch.setattr(self.store, "read_exact", fail)


@pytest.fixture(params=["fact", "experience"])
def memory(request):
    if request.param == "fact":
        ledger, candidate, cursor = _message_fact_read_ledger()
        return Fixture(ledger, candidate, cursor)
    projection, candidate, cursor, store, event = _experience_memory_read_fixture()
    return Fixture(
        _MemoryReadLedger(projection=projection, event=event, cursor=cursor),
        candidate,
        cursor,
        store,
    )


def replacement(before, *, status="active", summary_hash=None, privacy="private"):
    return authority.candidate(
        before.values.source_bindings[0],
        sources=before.values.source_bindings,
        candidate_id=before.candidate_id,
        revision=before.entity_revision + 1,
        status=status,
        summary_hash=summary_hash or before.values.summary_payload_hash,
        opened_at=before.opened_at,
        updated_at=NOW,
        reviewed_at=NOW,
        forgotten_at=NOW if status == "forgotten" else None,
        privacy_ceiling=privacy,
        accepted_event_ref="event:memory:replacement",
    )


def source_alias(after, *, ref=None, content_hash=None):
    binding = after.values.source_bindings[0]
    values = after.values.model_copy(
        update={
            "summary_ref": ref or f"summary:source:{binding.authority_event_ref}",
            "summary_payload_hash": content_hash or binding.authority_payload_hash,
        }
    )
    result = after.model_copy(
        update={
            "values": values,
            "semantic_fingerprint": authority.memory_candidate_semantic_fingerprint(
                values=values,
                policy_refs=after.origin.policy_refs,
            ),
        }
    )
    return type(result).model_validate_json(result.model_dump_json())


def revise(memory, after, *, kind):
    before = memory.candidate
    transition = MemoryCandidateTransitionProjection(
        transition_id=after.origin.transition_id,
        candidate_id=after.candidate_id,
        entity_revision=after.entity_revision,
        operation="revise",
        values_before=before.values,
        values_after=after.values,
        change_id=after.origin.change_id,
        policy_refs=after.origin.policy_refs,
        accepted_event_ref=after.origin.accepted_event_ref,
        accepted_at=NOW,
        revise_kind=kind,
    )
    memory.ledger._projection = memory.ledger._projection.model_copy(
        update={
            "memory_candidates": (after,),
            "memory_candidate_transitions": (transition,),
        }
    )


@pytest.mark.parametrize("change", ["forgotten", "removed", "privacy", "new_revision"])
def test_old_active_image_cannot_bypass_the_current_memory_state(memory, monkeypatch, change):
    assert memory.read().items
    before_projection = memory.ledger._projection
    after = replacement(
        memory.candidate,
        status="forgotten" if change == "forgotten" else "active",
        privacy="withhold" if change == "privacy" else "private",
    )
    memory.ledger._projection = before_projection.model_copy(
        update={
            "memory_candidates": () if change == "removed" else (after,),
        }
    )
    memory.forbid_content_reads(monkeypatch)
    result = memory.read()
    assert not result.items
    assert result.suppressions[0].reasons == ("source_proof_failed",)
    if change == "forgotten":
        current = memory.read(after)
        assert not current.items and current.suppressions[0].reasons == ("not_active",)
    elif change == "privacy":
        current = memory.read(after)
        assert not current.items and current.suppressions[0].reasons == ("privacy_ceiling",)


@pytest.mark.parametrize("kind", ["compress", "clarify"])
def test_changed_representation_never_falls_back_to_original_fact_or_experience(
    memory, monkeypatch, kind
):
    assert memory.read().items
    after = replacement(memory.candidate, summary_hash="e" * 64)
    revise(memory, after, kind=kind)
    memory.forbid_content_reads(monkeypatch)
    result = memory.read(after)
    assert not result.items
    assert result.suppressions[0].reasons == ("content_unavailable",)


def test_source_alias_updates_still_read_their_exact_existing_source(memory):
    # Correction/source-retirement producers use this exact event alias,
    # not an authored summary. Those readers remain available.
    expected = memory.read().items[0].source_excerpts
    binding = memory.candidate.values.source_bindings[0]
    after = replacement(memory.candidate, summary_hash=binding.authority_payload_hash)
    after = source_alias(after)
    revise(memory, after, kind="clarify")
    assert memory.read(after).items[0].source_excerpts == expected


def test_source_alias_cannot_erase_an_earlier_compression(memory, monkeypatch):
    before = memory.candidate
    compressed = replacement(before, summary_hash="e" * 64)
    revise(memory, compressed, kind="compress")
    compression = memory.ledger._projection.memory_candidate_transitions[0]
    memory.candidate = compressed
    binding = before.values.source_bindings[0]
    after = replacement(compressed, summary_hash=binding.authority_payload_hash)
    after = source_alias(after)
    revise(memory, after, kind="clarify")
    memory.ledger._projection = memory.ledger._projection.model_copy(
        update={
            "memory_candidate_transitions": (
                compression,
                *memory.ledger._projection.memory_candidate_transitions,
            ),
        }
    )
    memory.forbid_content_reads(monkeypatch)
    assert memory.read(after).suppressions[0].reasons == ("content_unavailable",)


def test_unchanged_current_revision_remains_readable(memory):
    before = memory.read()
    after = replacement(memory.candidate)
    memory.ledger._projection = memory.ledger._projection.model_copy(
        update={"memory_candidates": (after,)}
    )
    assert memory.read(after) == before


@pytest.mark.parametrize("changed", ["ref", "hash"])
def test_source_alias_requires_both_exact_event_ref_and_hash(memory, monkeypatch, changed):
    after = source_alias(
        replacement(memory.candidate),
        ref="summary:source:event:unrelated" if changed == "ref" else None,
        content_hash="e" * 64 if changed == "hash" else None,
    )
    revise(memory, after, kind="clarify")
    memory.forbid_content_reads(monkeypatch)
    assert memory.read(after).suppressions[0].reasons == ("content_unavailable",)
