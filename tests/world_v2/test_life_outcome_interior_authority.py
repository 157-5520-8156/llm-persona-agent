"""World Author permission at public request, parser and Life acceptance seams.

Mock HTTP verdicts prove the mechanical contract, not real critic accuracy.
"""

import json
from itertools import groupby
from pathlib import Path

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore,
    StoredLifeContent,
)
from companion_daemon.world_v2.life_development_draft import parse_world_author_draft
from companion_daemon.world_v2.life_development_model_adapter import (
    RoleBoundLifeDevelopmentModelAdapter,
)
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentProposalReader
from companion_daemon.world_v2.life_development_source_closure import (
    LifeDevelopmentSourceClosureError,
    parse_life_development_novel_origin_review,
)
from companion_daemon.world_v2.schemas import WorldEvent
from test_life_development_runtime import (
    NOW,
    WORLD_ID,
    _SequenceModel,
    _location_bound_world_draft,
    _location_capability,
    _manifest,
    _novel_book_exchange_draft,
    _novel_origin_review,
    _projection_cursor,
    _runtime,
    _seed_clock,
)


INTERIOR = "She feels quiet and reassured."


def _rejection(fragment=INTERIOR):
    return json.dumps(
        {
            "review": {
                "decision": "unsupported",
                "unsupported_outcome_prerequisites": [
                    {
                        "prose_path": "outcomes.0.text",
                        "violation_kinds": ["character_interior_authorship"],
                        "exact_fragments": [fragment],
                    }
                ],
                "reason": "The author assigns the companion a new subjective reaction.",
            }
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "objective,causal",
    [
        (False, "character_choice"),
        (False, "world_contingency"),
        (True, "world_contingency"),
    ],
)
async def test_http_review_keeps_objective_candidates_but_rejects_authored_interior(
    objective, causal, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    location = _location_capability()
    value = json.loads(
        _location_bound_world_draft(
            wake=wake,
            capability=location,
            timing={"mode": "now", "duration_minutes": 30},
            privacy_class="shareable",
            causal_authority=causal,
            outcome_resolution_authority=causal,
        )
    )
    value["outcomes"][0]["text"] = "The light shifts over the courtyard."
    if not objective:
        value["outcomes"][0]["text"] += " " + INTERIOR
    requests = []

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        raw = (
            json.dumps(value)
            if body["model"] == "deepseek-v4-pro"
            else _novel_origin_review(decision="supported")
            if objective
            else _rejection()
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": raw}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            },
        )

    providers = [
        DeepSeekChatModel(
            "offline-fixture",
            "https://fixture.invalid",
            name,
            thinking_enabled=False,
            transport=httpx.MockTransport(respond),
        )
        for name in ("deepseek-v4-pro", "deepseek-v4-flash")
    ]
    character = _SequenceModel(model="offline-character", outputs=())
    runtime, _ = _runtime(
        ledger=ledger,
        wake=wake,
        world_author=RoleBoundLifeDevelopmentModelAdapter(model=providers[0], role="world_author"),
        novel_origin_critic=RoleBoundLifeDevelopmentModelAdapter(
            model=providers[1], role="world_author_source_reviewer"
        ),
        character_interior=character,
        location_capability=location,
    )
    try:
        result = await runtime.advance_once(
            wake_event_ref=wake.event_id,
            trace_id="trace:outcome-authority",
            correlation_id="correlation:outcome-authority",
        )
        if not objective:
            assert result.status == "technical_failure"
            assert result.reason_code == "life_development.source_closure_rejected"
            again = await runtime.advance_once(
                wake_event_ref=wake.event_id,
                trace_id="trace:outcome-authority",
                correlation_id="correlation:outcome-authority",
            )
            assert again.reason_code == result.reason_code
            assert not ledger.project().plans
            assert not ledger.project().world_occurrences
        else:
            assert result.status == "occurrence_committed"
            assert len(ledger.project().world_occurrences) == 1
        assert character.consider_calls == 0
        assert len(requests) == 2  # Valid unsupported is not a malformed-wire retry.
        author_request, critic_request = requests
        author_text = json.dumps(author_request["messages"], ensure_ascii=False)
        assert "what_she_thought" not in author_text
        assert "what she thought" not in author_text
        assert "must_not_author_companion_interior" in author_text
        packet = json.loads(critic_request["messages"][-1]["content"])
        assert packet["review_contract"] == "life-development-novel-origin-review.5"
        assert (
            "character_interior_authorship"
            in packet["parser_coordinate_catalog"]["outcome_prerequisite_violation_kinds"]
        )
        assert packet["reviewed_surface"]["outcomes"][0]["text"] == value["outcomes"][0]["text"]
        assert (
            packet["review_dimensions"]["outcome_prerequisites"]["character_interior"]
            == "cannot_author_new_state_or_reaction"
        )
        assert ledger.rebuild() == ledger.project()
    finally:
        for provider in providers:
            await provider.aclose()


def test_interior_rejection_uses_exact_outcome_fragment_without_becoming_malformed():
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    value = _novel_book_exchange_draft(wake=wake)
    value["outcomes"][0]["text"] = INTERIOR
    draft = parse_world_author_draft(
        raw=json.dumps(value),
        manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        logical_time=NOW,
    )
    review = parse_life_development_novel_origin_review(raw=_rejection(), draft=draft)
    assert review.decision == "unsupported"
    assert review.unsupported_outcome_prerequisites[0].exact_fragments == (INTERIOR,)
    with pytest.raises(
        LifeDevelopmentSourceClosureError, match="unknown_novel_origin_outcome_fragment"
    ):
        parse_life_development_novel_origin_review(raw=_rejection("foreign fragment"), draft=draft)


@pytest.mark.asyncio
@pytest.mark.parametrize("repaired", [False, True])
async def test_foreign_fragment_gets_one_critic_wire_correction_without_author_rewrite(repaired):
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    value = _novel_book_exchange_draft(wake=wake)
    value["outcomes"][0]["text"] = INTERIOR
    bad = _rejection("foreign fragment")
    author = _SequenceModel(model="offline-world-author", outputs=(json.dumps(value),))
    critic = _SequenceModel(
        model="offline-focused-critic", outputs=(bad, _rejection() if repaired else bad)
    )
    character = _SequenceModel(model="offline-character", outputs=())
    runtime, _ = _runtime(
        ledger=ledger,
        wake=wake,
        world_author=author,
        novel_origin_critic=critic,
        character_interior=character,
    )
    result = await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:outcome-wire",
        correlation_id="correlation:outcome-wire",
    )
    assert result.reason_code == (
        "life_development.source_closure_rejected"
        if repaired
        else "life_development.novel_origin_critic_invalid_contract"
    )
    assert author.calls == 1
    assert critic.calls == 2
    assert critic.messages[1][:-2] == critic.messages[0]
    correction = json.loads(critic.messages[1][-1]["content"])
    assert correction["validation_failure"]["code"] == "unknown_novel_origin_outcome_fragment"
    assert (
        "character_interior_authorship"
        in correction["parser_coordinate_catalog"]["outcome_prerequisite_violation_kinds"]
    )
    assert character.consider_calls == 0
    assert not ledger.project().plans
    assert not ledger.project().world_occurrences


def test_frozen_packet5_history_cold_replays_with_original_outcome_bytes():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/life_origin_packet5_replay.json").read_text()
    )
    assert fixture["generated_from_commit"] == "803f15a5"
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
    material = LifeDevelopmentProposalReader(
        ledger=ledger, content_store=store
    ).read_for_occurrence(occurrence=ledger.project().world_occurrences[0])
    assert material is not None
    event = ledger.lookup_event_commit(material.proposal_event_ref)[0]
    assert event.payload()["world_author_novel_origin_evidence_packet_contract"] == (
        "life-development-novel-origin-review-evidence-packet.5"
    )
    assert any(INTERIOR in value["text"] for value in fixture["content"])
    assert [
        row.event.model_dump(mode="json") for row in ledger.export_replay_evidence().events
    ] == [row["event"] for row in fixture["events"]]
