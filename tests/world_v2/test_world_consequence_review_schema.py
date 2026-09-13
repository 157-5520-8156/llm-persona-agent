"""Model-visible focused schema agrees with the existing parser, not its semantics."""

import json
from itertools import combinations

import httpx
import pytest
from jsonschema import Draft202012Validator

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.life_development_draft import parse_world_author_draft
from companion_daemon.world_v2.life_development_model_adapter import (
    RoleBoundLifeDevelopmentModelAdapter,
)
from companion_daemon.world_v2.life_development_source_closure import (
    LifeDevelopmentSourceClosureError,
    life_development_novel_origin_correction_message,
    life_development_novel_origin_messages,
    parse_life_development_novel_origin_review,
)
from companion_daemon.world_v2.world_consequence_contract import ActivityExecutionBinding
from test_life_development_runtime import NOW
from test_world_consequence_source_closure import (
    _consequence_material,
    _legacy_material,
    _review_authority,
)


# Exact field/fragment/classification shape from the rejected focused response
# at real canary ledger 217. This fixture does not endorse its semantic verdict.
ATTEMPT_FRAGMENT = (
    "the reading spot by the window is being used for catching up on last week's "
    "course reading, with a quiet stretch in the middle and no message-checking"
)


def _attempt_case():
    manifest, _ = _legacy_material()
    binding = ActivityExecutionBinding(
        actor_ref=manifest.owner_actor_ref,
        source_event_ref="event:fixture:activity-started",
        source_world_revision=1,
        source_payload_hash="a" * 64,
        privacy_class="shareable",
        source_event_type="ActivityStarted",
        plan_id="plan:fixture:reading",
        activity_id="activity:fixture:reading",
        plan_entity_revision=2,
    ).model_dump(mode="json")
    manifest, draft = _consequence_material()
    value = draft.model_dump(mode="json")
    for outcome in value["outcomes"]:
        outcome["world_consequence"]["authorized_attempt_result"] = {
            "text": ATTEMPT_FRAGMENT, "execution_binding": binding,
        }
    draft = parse_world_author_draft(raw=json.dumps(value), manifest=manifest, logical_time=NOW)
    messages = life_development_novel_origin_messages(
        context={}, manifest=manifest, draft=draft,
        execution_authority=_review_authority(manifest, (binding,)),
    )
    return draft, messages


def _review(kinds):
    return {
        "review": {
            "decision": "unsupported",
            "reason": "The reviewer classifies the exact candidate attempt-result fragment.",
            "unsupported_claims": [],
            "unsupported_provisional_npcs": [],
            "unsupported_provisional_places": [],
            "unsupported_outcome_prerequisites": [
                {
                    "prose_path": f"outcomes.{index}.world_consequence.authorized_attempt_result.text",
                    "violation_kinds": kinds,
                    "exact_fragments": [ATTEMPT_FRAGMENT],
                }
                for index in (0, 1)
            ],
            "unsupported_objective_transitions": [],
            "unsupported_dynamic_life_directions": [],
            "undeclared_premise_fragments": [],
        }
    }


@pytest.mark.asyncio
async def test_focused_adapter_schema_rejects_real_completed_experience_only_shape():
    draft, messages = _attempt_case()
    raw_response = json.dumps(_review(["completed_character_experience"]))
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": raw_response}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100},
        })

    provider = DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(respond),
    )
    try:
        adapter = RoleBoundLifeDevelopmentModelAdapter(
            model=provider, role="world_author_source_reviewer",
        )
        raw = await adapter.complete(messages)
    finally:
        await provider.aclose()
    assert raw == raw_response
    assert len(requests) == 1
    assert requests[0]["messages"] == messages
    with pytest.raises(LifeDevelopmentSourceClosureError) as caught:
        parse_life_development_novel_origin_review(raw=raw, draft=draft)
    assert caught.value.code == "invalid_novel_origin_shape"
    assert {item["path"] for item in caught.value.violations} == {
        "unsupported_outcome_prerequisites.0", "unsupported_outcome_prerequisites.1",
    }
    assert "outside the current proposal branch or an actor-authority violation" in caught.value.detail
    schema = json.loads(requests[0]["messages"][-1]["content"])["output_contract"]["review_schema"]
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(json.loads(raw)["review"])), (
        "The model-visible schema admits the exact combination the runtime rejects"
    )


AUTHORITY_KINDS = (
    "retroactive_relationship_or_shared_history",
    "existing_entity_or_fact_masquerading_as_novel",
    "imported_current_or_prior_prerequisite",
    "completed_user_channel_act",
    "character_interior_authorship",
)
SUPPLEMENTARY_KINDS = (
    "completed_character_experience", "objective_transition_not_entailed_by_candidate",
)
KIND_COMBINATIONS = tuple(
    combination
    for length in range(5)
    for combination in combinations(AUTHORITY_KINDS + SUPPLEMENTARY_KINDS, length)
) + (
    AUTHORITY_KINDS,
    ("character_interior_authorship", "character_interior_authorship"),
    ("character_interior_authorship", "invented_violation"),
)


@pytest.fixture(scope="module")
def attempt_case():
    return _attempt_case()


@pytest.mark.parametrize("kinds", KIND_COMBINATIONS)
def test_current_schema_preserves_every_existing_classification_combination(attempt_case, kinds):
    draft, messages = attempt_case
    review = _review(list(kinds))
    schema = json.loads(messages[-1]["content"])["output_contract"]["review_schema"]
    expected = (
        1 <= len(kinds) <= 4
        and len(kinds) == len(set(kinds))
        and set(kinds).issubset(AUTHORITY_KINDS + SUPPLEMENTARY_KINDS)
        and bool(set(kinds).intersection(AUTHORITY_KINDS))
    )
    assert Draft202012Validator(schema).is_valid(review["review"]) == expected
    raw = json.dumps(review)
    if expected:
        parsed = parse_life_development_novel_origin_review(raw=raw, draft=draft)
        assert parsed.decision == "unsupported"
        assert all(
            finding.violation_kinds == tuple(sorted(kinds))
            for finding in parsed.unsupported_outcome_prerequisites
        )
    else:
        with pytest.raises(LifeDevelopmentSourceClosureError) as caught:
            parse_life_development_novel_origin_review(raw=raw, draft=draft)
        assert caught.value.code == "invalid_novel_origin_shape"


def test_correction_transports_the_same_schema_without_reclassifying_failed_review(attempt_case):
    draft, messages = attempt_case
    original = _review(["completed_character_experience"])
    raw = json.dumps(original)
    with pytest.raises(LifeDevelopmentSourceClosureError) as caught:
        parse_life_development_novel_origin_review(raw=raw, draft=draft)
    correction = life_development_novel_origin_correction_message(error=caught.value, draft=draft)
    initial_packet = json.loads(messages[-1]["content"])
    corrected_packet = json.loads(correction["content"])
    assert corrected_packet["output_contract"] == initial_packet["output_contract"]
    assert corrected_packet["parser_coordinate_catalog"] == initial_packet["parser_coordinate_catalog"]
    assert corrected_packet["validation_failure"]["code"] == "invalid_novel_origin_shape"
    assert original == json.loads(raw)
    assert "review" not in corrected_packet


def test_supported_without_rejection_remains_valid(attempt_case):
    draft, messages = attempt_case
    response = {"review": {"decision": "supported", "reason": "No finding in this fixture."}}
    schema = json.loads(messages[-1]["content"])["output_contract"]["review_schema"]
    assert Draft202012Validator(schema).is_valid(response["review"])
    parsed = parse_life_development_novel_origin_review(raw=json.dumps(response), draft=draft)
    assert parsed.decision == "supported"
    assert parsed.unsupported_outcome_prerequisites == ()


@pytest.mark.parametrize(
    "mutation, code",
    [
        ("absent_fragment", "unknown_novel_origin_outcome_fragment"),
        ("absent_path", "unknown_novel_origin_outcome_path"),
        ("invented_history_claim", "unknown_novel_origin_claim"),
    ],
)
def test_schema_conformance_does_not_grant_missing_source_or_coordinate_authority(
    attempt_case, mutation, code,
):
    draft, messages = attempt_case
    response = _review(["imported_current_or_prior_prerequisite"])
    finding = response["review"]["unsupported_outcome_prerequisites"][0]
    if mutation == "absent_fragment":
        finding["exact_fragments"] = ["A prior experience absent from the candidate."]
    elif mutation == "absent_path":
        finding["prose_path"] = "outcomes.99.world_consequence.authorized_attempt_result.text"
    else:
        response["review"]["unsupported_claims"] = [{
            "claim_id": "local:claim:unoffered-history",
            "violation_kinds": ["completed_character_experience"],
            "exact_fragments": ["A prior experience absent from the candidate."],
        }]
    schema = json.loads(messages[-1]["content"])["output_contract"]["review_schema"]
    assert Draft202012Validator(schema).is_valid(response["review"])
    with pytest.raises(LifeDevelopmentSourceClosureError) as caught:
        parse_life_development_novel_origin_review(raw=json.dumps(response), draft=draft)
    assert caught.value.code == code
