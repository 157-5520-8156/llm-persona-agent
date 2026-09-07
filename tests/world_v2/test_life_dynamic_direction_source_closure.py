"""Durable world effects must reach the already installed focused critic.

Supplied model verdicts test transport, coordinates and acceptance, not the
semantic accuracy of a real critic.
"""

import json
from itertools import groupby
from pathlib import Path

import pytest

from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore,
    StoredLifeContent,
)
from companion_daemon.world_v2.schemas import WorldEvent
from companion_daemon.world_v2.life_review_identity import current_novel_origin_review_subject_hash
from companion_daemon.world_v2.batch_invariants import validate_commit_batch
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentProposalReader
from companion_daemon.world_v2.life_development_draft import parse_world_author_draft
from companion_daemon.world_v2.life_development_source_closure import (
    LifeDevelopmentSourceClosureError,
    life_development_novel_origin_messages,
    parse_life_development_novel_origin_review,
)
from test_life_development_runtime import (
    NOW,
    WORLD_ID,
    _SequenceModel,
    _novel_book_exchange_draft,
    _runtime,
    _seed_clock,
    _manifest,
    _projection_cursor,
    _location_capability,
    _location_bound_world_draft,
    _replace_event_payload,
    _novel_origin_review,
    _hash_json,
)


def _direction():
    return {
        "summary": "Her recent manuscripts now supply the workshop's weekly readings.",
        "context_tags": ["writing:weekly_workshop"],
        "supersedes_context_tag_prefixes": [],
        "narrative_tags": ["narrative:weekly_reading"],
        "duration_days": 30,
        "privacy_class": "shareable",
    }


def _review(*, path="outcomes.0.dynamic_life_direction.summary", fragment="Her recent manuscripts"):
    return json.dumps(
        {
            "review": {
                "decision": "unsupported",
                "unsupported_dynamic_life_directions": [
                    {
                        "prose_path": path,
                        "violation_kinds": ["imported_current_or_prior_prerequisite"],
                        "exact_fragments": [fragment],
                    }
                ],
                "reason": "The durable context imports manuscripts absent from the pinned evidence.",
            }
        }
    )


@pytest.mark.asyncio
async def test_durable_direction_reaches_focused_review_and_rejection_prevents_world_effect():
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    value = _novel_book_exchange_draft(wake=wake)
    value["outcomes"][0]["dynamic_life_direction"] = _direction()
    author = _SequenceModel(model="world-author", outputs=(json.dumps(value),))

    class InspectingCritic(_SequenceModel):
        async def complete(self, messages, *, temperature=0.2):
            packet = json.loads(messages[-1]["content"])
            assert (
                packet["reviewed_surface"]["outcomes"][0]["dynamic_life_direction"] == _direction()
            )
            assert (
                "outcomes.0.dynamic_life_direction.summary"
                in packet["parser_coordinate_catalog"]["dynamic_life_direction_paths"]
            )
            return await super().complete(messages, temperature=temperature)

    critic = InspectingCritic(model="focused-critic", outputs=(_review(),))
    character = _SequenceModel(model="character", outputs=())
    runtime, _ = _runtime(
        ledger=ledger,
        wake=wake,
        world_author=author,
        character_interior=character,
        novel_origin_critic=critic,
    )
    for _ in range(2):
        result = await runtime.advance_once(
            wake_event_ref=wake.event_id,
            trace_id="trace:direction-origin",
            correlation_id="correlation:direction-origin",
        )
        assert result.status == "technical_failure"
        assert result.reason_code == "life_development.source_closure_rejected"
    assert author.calls == critic.calls == 1
    assert character.consider_calls == 0
    assert not ledger.project().plans
    assert not ledger.project().world_occurrences


def test_frozen_packet4_history_cold_replays_without_gaining_new_coverage():
    """Fixture is an actual public runtime result from 233249ed, not new output relabelled old."""
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/life_origin_packet4_replay.json").read_text()
    )
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    store = InMemoryImmutableLifeContentStore()
    for value in fixture["content"]:
        store.put_if_absent(StoredLifeContent(**value))
    for commit_id, rows in groupby(fixture["events"], key=lambda row: row["commit_id"]):
        ledger.commit_at_cursor(
            tuple(WorldEvent.model_validate_json(json.dumps(row["event"])) for row in rows),
            expected_cursor=_projection_cursor(ledger),
            commit_id=commit_id,
        )
    assert ledger.project() == ledger.rebuild()
    occurrence = ledger.project().world_occurrences[0]
    material = LifeDevelopmentProposalReader(
        ledger=ledger, content_store=store
    ).read_for_occurrence(occurrence=occurrence)
    assert material is not None
    proposal_event, commit = ledger.lookup_event_commit(material.proposal_event_ref)
    proposal = proposal_event.payload()
    assert "world_author_novel_origin_evidence_packet_contract" not in proposal
    assert "unsupported_dynamic_life_directions" not in proposal["world_author_novel_origin_review"]
    # Explicitly relabelling that same old subject as today's full coverage fails.
    upgraded = proposal | {
        "world_author_novel_origin_evidence_packet_contract": "life-development-novel-origin-review-evidence-packet.5"
    }
    upgraded_event = _replace_event_payload(proposal_event, payload=upgraded)
    events = tuple(ledger.lookup_event_commit(ref)[0] for ref in commit.event_ids)
    with pytest.raises(ValueError, match="novel-origin critic reviewed another subject"):
        validate_commit_batch(
            tuple(
                upgraded_event if event.event_id == upgraded_event.event_id else event
                for event in events
            ),
            expected_world_revision=proposal["evaluated_world_revision"],
            accepted_manifest_v3_authorized=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("repaired", [False, True])
async def test_bad_direction_coordinate_gets_only_existing_critic_wire_correction(repaired):
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    value = _novel_book_exchange_draft(wake=wake)
    value["outcomes"][0]["dynamic_life_direction"] = _direction()
    author = _SequenceModel(model="world-author", outputs=(json.dumps(value),))
    bad = _review(fragment="not present in this direction")
    critic = _SequenceModel(model="critic", outputs=(bad, _review() if repaired else bad))
    character = _SequenceModel(model="character", outputs=())
    runtime, _ = _runtime(
        ledger=ledger,
        wake=wake,
        world_author=author,
        character_interior=character,
        novel_origin_critic=critic,
    )
    result = await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:direction-wire",
        correlation_id="correlation:direction-wire",
    )
    assert result.status == "technical_failure"
    assert result.reason_code == (
        "life_development.source_closure_rejected"
        if repaired
        else "life_development.novel_origin_critic_invalid_contract"
    )
    assert author.calls == 1
    assert critic.calls == 2
    assert critic.messages[1][:-2] == critic.messages[0]
    correction = json.loads(critic.messages[1][-1]["content"])
    assert correction["validation_failure"]["code"] == "unknown_dynamic_life_direction_fragment"
    assert (
        "outcomes.0.dynamic_life_direction.summary"
        in correction["parser_coordinate_catalog"]["dynamic_life_direction_paths"]
    )
    assert character.consider_calls == 0
    assert not ledger.project().world_occurrences


@pytest.mark.parametrize(
    "field,fragment",
    [
        ("summary", "Her recent manuscripts"),
        ("context_tags.0", "writing:weekly_workshop"),
        ("narrative_tags.0", "narrative:weekly_reading"),
        ("supersedes_context_tag_prefixes.0", "writing:"),
    ],
)
def test_direction_findings_bind_the_exact_field_and_packet(field, fragment):
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger))
    value = _novel_book_exchange_draft(wake=wake)
    direction = _direction()
    direction["supersedes_context_tag_prefixes"] = ["writing:"]
    value["outcomes"][0]["dynamic_life_direction"] = direction
    draft = parse_world_author_draft(raw=json.dumps(value), manifest=manifest, logical_time=NOW)
    path = "outcomes.0.dynamic_life_direction." + field
    review = parse_life_development_novel_origin_review(
        raw=_review(path=path, fragment=fragment), draft=draft
    )
    assert review.unsupported_dynamic_life_directions[0].prose_path == path
    packet = json.loads(
        life_development_novel_origin_messages(context={}, manifest=manifest, draft=draft)[-1][
            "content"
        ]
    )
    assert packet["reviewed_surface"]["outcomes"][0]["dynamic_life_direction"] == direction
    changed_direction = {"summary": direction["summary"] + " altered"}
    changed = draft.model_copy(
        update={
            "outcomes": (
                draft.outcomes[0].model_copy(
                    update={
                        "dynamic_life_direction": draft.outcomes[
                            0
                        ].dynamic_life_direction.model_copy(update=changed_direction)
                    }
                ),
                draft.outcomes[1],
            )
        }
    )
    changed_packet = json.loads(
        life_development_novel_origin_messages(context={}, manifest=manifest, draft=changed)[-1][
            "content"
        ]
    )
    assert changed_packet["pinned_authority"] == packet["pinned_authority"]
    assert changed_packet["evidence_packet_binding"] != packet["evidence_packet_binding"]
    with pytest.raises(
        LifeDevelopmentSourceClosureError, match="unknown_dynamic_life_direction_path"
    ):
        parse_life_development_novel_origin_review(
            raw=_review(path=path.replace("outcomes.0", "outcomes.1"), fragment=fragment),
            draft=draft,
        )
    with pytest.raises(
        LifeDevelopmentSourceClosureError, match="unknown_dynamic_life_direction_fragment"
    ):
        parse_life_development_novel_origin_review(
            raw=_review(path=path, fragment="foreign fragment"), draft=draft
        )


@pytest.mark.asyncio
async def test_supported_durable_context_is_readable_and_cannot_downgrade_its_audit():
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    capability = _location_capability()
    direction = _direction() | {
        "summary": "The courtyard closes for repairs for thirty days.",
        "context_tags": ["access:courtyard_closed"],
        "narrative_tags": ["narrative:courtyard_repairs"],
    }
    value = json.loads(
        _location_bound_world_draft(
            wake=wake,
            capability=capability,
            timing={"mode": "now", "duration_minutes": 30},
            privacy_class="shareable",
            causal_authority="world_contingency",
            outcome_resolution_authority="world_contingency",
            dynamic_direction=direction,
        )
    )
    value["outcomes"][0]["text"] = "Workers close the courtyard for thirty days of repairs."
    author = _SequenceModel(model="world-author", outputs=(json.dumps(value),))
    critic = _SequenceModel(model="critic", outputs=(_novel_origin_review(decision="supported"),))
    runtime, store = _runtime(
        ledger=ledger,
        wake=wake,
        world_author=author,
        character_interior=_SequenceModel(model="character", outputs=()),
        novel_origin_critic=critic,
        location_capability=capability,
    )
    result = await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:direction-supported",
        correlation_id="correlation:direction-supported",
    )
    assert result.status == "occurrence_committed"
    proposal_event, commit = ledger.lookup_event_commit(result.proposal_event_ref)
    proposal = proposal_event.payload()
    assert (
        proposal["world_author_novel_origin_evidence_packet_contract"]
        == "life-development-novel-origin-review-evidence-packet.5"
    )
    assert "unsupported_dynamic_life_directions" not in proposal["world_author_novel_origin_review"]
    occurrence = ledger.project().world_occurrences[0]
    effect = occurrence.candidate_outcomes[0].dynamic_life_arc_context
    assert store.read_exact(content_ref=effect.summary_content_ref).text == direction["summary"]
    reader = LifeDevelopmentProposalReader(ledger=ledger, content_store=store)
    assert reader.read_for_occurrence(occurrence=occurrence) is not None
    assert ledger.rebuild() == ledger.project()
    events = tuple(ledger.lookup_event_commit(ref)[0] for ref in commit.event_ids)
    for downgrade in [None, "life-development-novel-origin-review-evidence-packet.4"]:
        forged = dict(proposal)
        if downgrade is None:
            del forged["world_author_novel_origin_evidence_packet_contract"]
        else:
            forged["world_author_novel_origin_evidence_packet_contract"] = downgrade
        bad_event = _replace_event_payload(proposal_event, payload=forged)
        with pytest.raises(ValueError, match="novel-origin critic reviewed another subject"):
            validate_commit_batch(
                tuple(
                    bad_event if event.event_id == bad_event.event_id else event for event in events
                ),
                expected_world_revision=proposal["evaluated_world_revision"],
                accepted_manifest_v3_authorized=True,
            )
    # A writer cannot relabel both the Proposal and its copied subject while
    # leaving the actual ModelResult audit on the new packet.
    forged = json.loads(json.dumps(proposal))
    forged.pop("world_author_novel_origin_evidence_packet_contract")
    copied_review = forged["world_author_novel_origin_deliberation"]
    copied_review["decision_subject_hash"] = current_novel_origin_review_subject_hash(
        evidence_packet_contract="life-development-novel-origin-review-evidence-packet.4",
        review_request_hashes=tuple(copied_review["request_hashes"]),
        world_author_raw_output_hash=forged["world_author_raw_output_hash"],
        capability_manifest_hash=forged["capability_manifest_hash"],
        context_cursor=copied_review["context_cursor"],
        wake_event_ref=forged["trigger_id"],
        wake_world_id=proposal_event.world_id,
        wake_logical_time=proposal_event.logical_time.isoformat(),
    )
    forged["world_author_novel_origin_deliberation_hash"] = _hash_json(copied_review)
    bad_event = _replace_event_payload(proposal_event, payload=forged)
    cold = WorldLedger.in_memory(world_id=WORLD_ID)
    for commit_id, rows in groupby(
        ledger.export_replay_evidence().events, key=lambda row: row.commit_id
    ):
        batch = tuple(row.event for row in rows)
        if any(event.event_id == proposal_event.event_id for event in batch):
            with pytest.raises(ValueError, match="(subject|audit|lineage)"):
                cold.commit_at_cursor(
                    tuple(
                        bad_event if event.event_id == bad_event.event_id else event
                        for event in batch
                    ),
                    expected_cursor=_projection_cursor(cold),
                    commit_id=commit_id,
                )
            break
        cold.commit_at_cursor(batch, expected_cursor=_projection_cursor(cold), commit_id=commit_id)
    else:
        pytest.fail("accepted domain batch was missing from exported history")
    assert not cold.project().world_occurrences
    assert author.calls == critic.calls == 1
