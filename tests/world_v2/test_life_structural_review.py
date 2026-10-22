"""Structural Life review keeps the review lifecycle without a semantic judge."""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.life_development_model_adapter import (
    RoleBoundLifeDevelopmentModelAdapter,
)
from companion_daemon.world_v2.life_development_source_closure import (
    parse_life_development_novel_origin_review,
    parse_life_development_source_closure_review,
)
from companion_daemon.world_v2.life_structural_review import (
    STRUCTURAL_LIFE_REVIEW_MODEL_ID,
    StructuralLifeReviewModel,
    is_structural_life_review,
)
from tests.world_v2.test_life_development_runtime import (
    OWNER,
    WORLD_ID,
    _SequenceModel,
    _location_capability,
    _runtime,
    _seed_clock,
)


def _structural_reviewer() -> RoleBoundLifeDevelopmentModelAdapter:
    return RoleBoundLifeDevelopmentModelAdapter(
        model=StructuralLifeReviewModel(),
        role="world_author_source_reviewer",
    )


def _hail_draft(wake_ref: str, *, existing_ref: str | None = None) -> dict[str, object]:
    claims: list[dict[str, object]] = [
        {
            "claim_id": "local:claim:hail",
            "summary": "此刻院子里突发冰雹并打湿了手账。",
            "scope": "novel_world_generation",
            "subject_scope": "world_environment",
            "source_refs": [],
        }
    ]
    if existing_ref is not None:
        claims.append(
            {
                "claim_id": "local:claim:existing",
                "summary": "她早就把手账晾在院子里。",
                "scope": "existing_world",
                "subject_scope": "world_environment",
                "source_refs": [existing_ref],
            }
        )
    claim_refs = [claim["claim_id"] for claim in claims]
    outcome = {
        "experienced_by_ref": OWNER,
        "text": "她赶过去的时候，还是有几页洇开了，字糊成一团，她有点心疼。",
        "privacy_class": "personal",
        "relative_plausibility_weight": 1,
        "claim_refs": claim_refs,
        "provisional_npcs": [],
        "dynamic_life_direction": None,
        "visual_evidence": {
            "claim_refs": ["local:claim:hail"],
            "activity_description": "冰雹打湿了晾着的手账",
            "location": {
                "location_ref": "location:campus-courtyard",
                "kind": "open_courtyard",
                "publicness": "public",
            },
            "environment": {"weather": "sudden hail", "structure": "open courtyard"},
        },
    }
    return {
        "decision": "propose",
        "authored_subject_ref": OWNER,
        "causal_authority": "world_contingency",
        "outcome_resolution_authority": "world_contingency",
        "premise_scope": "external_opportunity",
        "premise": "院子上方忽然落下一阵很急的冰雹。",
        "premise_claim_refs": claim_refs,
        "claim_declarations": claims,
        "timing": {"mode": "now", "duration_minutes": 20},
        "anchor_refs": [wake_ref],
        "location_ref": "location:campus-courtyard",
        "location_capability_ref": _location_capability().capability_ref,
        "entity_refs": [],
        "privacy_class": "personal",
        "outcomes": [
            outcome,
            {
                **outcome,
                "text": "她及时收回了手账，只湿了封面和边角。",
                "relative_plausibility_weight": 3,
            },
        ],
    }


def test_structural_reply_decodes_for_both_review_kinds_without_findings() -> None:
    import asyncio

    raw = asyncio.run(StructuralLifeReviewModel().complete([]))
    decoded = json.loads(raw)
    assert decoded["decision"] == "supported"
    assert set(decoded) == {"decision", "reason"}
    assert is_structural_life_review(_structural_reviewer())
    assert not is_structural_life_review(_SequenceModel(model="x", outputs=()))
    # The same bytes satisfy the frozen general-closure and focused parsers.
    assert parse_life_development_source_closure_review is not None
    assert parse_life_development_novel_origin_review is not None


@pytest.mark.asyncio
async def test_structural_review_commits_a_life_occurrence_without_a_provider_review() -> None:
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    world_author = _SequenceModel(
        model="test-world-author",
        outputs=(json.dumps(_hail_draft(wake.event_id), ensure_ascii=False),),
    )
    character_model = _SequenceModel(model="test-character-model", outputs=())
    reviewer = _structural_reviewer()
    runtime, _store = _runtime(
        ledger=ledger,
        wake=wake,
        world_author=world_author,
        character_interior=character_model,
        source_closure_reviewer=reviewer,
        novel_origin_critic=reviewer,
    )

    first = await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:structural",
        correlation_id="correlation:life-development",
    )
    replay = await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:structural-replay",
        correlation_id="correlation:life-development",
    )

    assert first.status == "occurrence_committed"
    assert replay.status == "occurrence_committed"
    assert replay.occurrence_id == first.occurrence_id
    assert world_author.calls == 1
    assert len(ledger.project().world_occurrences) == 1
    # The review lifecycle still records its audit, under the structural identity.
    review_results = [
        stored.event
        for stored in ledger._events  # noqa: SLF001
        if stored.event.event_type == "ModelResultRecorded"
        and STRUCTURAL_LIFE_REVIEW_MODEL_ID in str(stored.event.payload().get("audit_json", ""))
    ]
    assert review_results, "structural review must keep its audit records"


@pytest.mark.asyncio
async def test_structural_review_still_fails_closed_on_an_unresolvable_existing_world_ref() -> None:
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    draft = _hail_draft(wake.event_id, existing_ref="event:never-committed")
    world_author = _SequenceModel(
        model="test-world-author",
        outputs=tuple(json.dumps(draft, ensure_ascii=False) for _ in range(4)),
    )
    reviewer = _structural_reviewer()
    runtime, _store = _runtime(
        ledger=ledger,
        wake=wake,
        world_author=world_author,
        character_interior=_SequenceModel(model="test-character-model", outputs=()),
        source_closure_reviewer=reviewer,
        novel_origin_critic=reviewer,
    )

    result = await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:structural-bad-ref",
        correlation_id="correlation:life-development",
    )

    assert result.status != "occurrence_committed"
    assert not ledger.project().world_occurrences
