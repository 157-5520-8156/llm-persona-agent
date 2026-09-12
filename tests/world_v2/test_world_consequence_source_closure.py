"""Versioned World consequence review at public compiler and parser seams.

Provider fixtures verify transported evidence and exact rejection coordinates;
they do not establish a real critic's semantic accuracy.
"""

import hashlib
import json

import pytest
import httpx

from companion_daemon.llm import DeepSeekChatModel

from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.life_development_draft import parse_world_author_draft
from companion_daemon.world_v2.life_development_model_adapter import (
    RoleBoundLifeDevelopmentModelAdapter,
)
from companion_daemon.world_v2.life_development_source_closure import (
    LifeDevelopmentSourceClosureError,
    life_development_novel_origin_correction_message,
    life_development_novel_origin_messages,
    life_development_source_closure_messages,
    life_development_review_packet_identity,
    parse_life_development_novel_origin_review,
    parse_life_development_source_closure_review,
    resolve_cited_pinned_material,
)
from companion_daemon.world_v2.world_consequence_contract import (
    ActivityExecutionBinding,
    WorldConsequenceAuthority,
)
from test_life_development_runtime import (
    NOW,
    WORLD_ID,
    _location_bound_world_draft,
    _location_capability,
    _manifest,
    _projection_cursor,
    _seed_clock,
)


def _legacy_material():
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    capability = _location_capability()
    manifest = _manifest(
        wake, pinned_cursor=_projection_cursor(ledger), location_capability=capability
    )
    raw = _location_bound_world_draft(
        wake=wake,
        capability=capability,
        timing={"mode": "now", "duration_minutes": 30},
        privacy_class="shareable",
        causal_authority="world_contingency",
        outcome_resolution_authority="world_contingency",
    )
    return manifest, parse_world_author_draft(raw=raw, manifest=manifest, logical_time=NOW)


def _wire_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def test_legacy_reviews_keep_frozen_wire_without_gaining_execution_authority():
    """Hashes were captured from public compilers at untouched 4fed62c8."""
    manifest, draft = _legacy_material()
    arguments = dict(context={}, manifest=manifest, draft=draft, execution_authority={})
    general = life_development_source_closure_messages(**arguments, cited_events=())
    focused = life_development_novel_origin_messages(**arguments)
    correction = life_development_novel_origin_correction_message(
        error=LifeDevelopmentSourceClosureError(
            "unknown_novel_origin_outcome_fragment", "absent fragment"
        ),
        draft=draft,
    )
    assert _wire_hash(general) == "c2c607689615778e3bd890e272e19e26f4c85c6ac617e6f424ef03ccfe0d49d3"
    assert _wire_hash(focused) == "b955e861e9c353fe8835c47a8c42655ffcce96b3cbf5707c4b99cc86e656da00"
    assert (
        _wire_hash(correction) == "6525d099721dd9983a234766a23e1ce6212e0dc1da446d94a94484fbe640e479"
    )


ENVIRONMENT = "Hail covers the open courtyard."
UNAUTHORED = "She retrieves the journal."


def _consequence_material(*, attempt=None):
    manifest, legacy = _legacy_material()
    manifest = type(manifest).model_validate_json(
        json.dumps(
            manifest.model_dump(mode="json", exclude_computed_fields=True)
            | {"outcome_contract": "world-consequence.2"}
        )
    )
    value = legacy.model_dump(mode="json")
    for outcome in value["outcomes"]:
        del outcome["text"]
        outcome["world_consequence"] = {
            "contract": "world-consequence.2",
            "environment_text": ENVIRONMENT + " " + UNAUTHORED,
        }
    if attempt is not None:
        value["outcomes"][0]["world_consequence"]["authorized_attempt_result"] = attempt
    draft = parse_world_author_draft(raw=json.dumps(value), manifest=manifest, logical_time=NOW)
    return manifest, draft


def _review_authority(manifest, bindings=()):
    authority = WorldConsequenceAuthority.model_validate_json(
        json.dumps(
            {
                "world_id": WORLD_ID,
                "actor_ref": manifest.owner_actor_ref,
                "evaluated_cursor": manifest.pinned_cursor.model_dump(mode="json"),
                "execution_bindings": list(bindings),
            }
        )
    ).model_dump(mode="json")
    return {"authority": authority, "execution_materials": []}


def _reject(path, fragment):
    return json.dumps(
        {
            "review": {
                "decision": "unsupported",
                "unsupported_outcome_prerequisites": [
                    {
                        "prose_path": path,
                        "violation_kinds": ["character_interior_authorship"],
                        "exact_fragments": [fragment],
                    }
                ],
                "reason": "No role execution source authorizes this new companion action.",
            }
        }
    )


def test_new_environment_has_exact_focused_coordinates_and_full_execution_authority():
    manifest, draft = _consequence_material()
    authority = _review_authority(manifest)
    messages = life_development_novel_origin_messages(
        context={}, manifest=manifest, draft=draft, execution_authority=authority
    )
    request = json.loads(messages[-1]["content"])
    surface = request["reviewed_surface"]["outcomes"][0]
    assert "text" not in surface
    assert surface["world_consequence"]["environment_text"] == ENVIRONMENT + " " + UNAUTHORED
    assert request["pinned_authority"]["execution_authority"] == authority
    path = "outcomes.0.world_consequence.environment_text"
    assert path in request["parser_coordinate_catalog"]["outcome_prerequisite_paths"]
    assert (
        "outcomes.0.text" not in request["parser_coordinate_catalog"]["outcome_prerequisite_paths"]
    )
    assert request["review_contract"] == "life-development-novel-origin-review.6"
    assert life_development_review_packet_identity(messages)[0] == (
        "life-development-novel-origin-review-evidence-packet.7"
    )
    verdict = parse_life_development_novel_origin_review(raw=_reject(path, UNAUTHORED), draft=draft)
    assert verdict.unsupported_outcome_prerequisites[0].prose_path == path
    with pytest.raises(LifeDevelopmentSourceClosureError):
        parse_life_development_novel_origin_review(
            raw=_reject("outcomes.0.text", UNAUTHORED), draft=draft
        )


def test_attempt_field_stays_separate_and_correction_preserves_new_actor_boundary():
    manifest, _ = _legacy_material()
    binding = ActivityExecutionBinding(
        actor_ref=manifest.owner_actor_ref,
        source_event_ref="event:fixture:activity-started",
        source_world_revision=1,
        source_payload_hash="a" * 64,
        privacy_class="shareable",
        source_event_type="ActivityStarted",
        plan_id="plan:fixture:journal",
        activity_id="activity:fixture:journal",
        plan_entity_revision=2,
    ).model_dump(mode="json")
    attempt = {"text": "The journal's cover is wet.", "execution_binding": binding}
    manifest, draft = _consequence_material(attempt=attempt)
    messages = life_development_novel_origin_messages(
        context={},
        manifest=manifest,
        draft=draft,
        execution_authority=_review_authority(manifest, (binding,)),
    )
    request = json.loads(messages[-1]["content"])
    assert (
        request["reviewed_surface"]["outcomes"][0]["world_consequence"]["authorized_attempt_result"]
        == attempt
    )
    path = "outcomes.0.world_consequence.authorized_attempt_result.text"
    assert path in request["parser_coordinate_catalog"]["outcome_prerequisite_paths"]
    with pytest.raises(LifeDevelopmentSourceClosureError) as error:
        parse_life_development_novel_origin_review(raw=_reject(path, UNAUTHORED), draft=draft)
    assert error.value.code == "unknown_novel_origin_outcome_fragment"
    correction = life_development_novel_origin_correction_message(error=error.value, draft=draft)
    corrected = json.loads(correction["content"])
    assert corrected["review_contract"] == "life-development-novel-origin-review.6"
    assert corrected["output_contract"] == request["output_contract"]
    assert corrected["parser_coordinate_catalog"] == request["parser_coordinate_catalog"]
    assert "outcomes.N.text" not in messages[0]["content"] + corrected["instruction"]
    assert "Objective candidate actions" not in messages[0]["content"] + corrected["instruction"]
    assert "cannot authorize a new companion action" in messages[0]["content"]
    assert "cannot authorize a new companion action" in corrected["instruction"]


def test_general_review_keeps_new_prose_only_in_typed_location_scope():
    manifest, draft = _consequence_material()
    messages = life_development_source_closure_messages(
        context={},
        manifest=manifest,
        draft=draft,
        cited_events=(),
        execution_authority=_review_authority(manifest),
    )
    packet = json.loads(messages[-1]["content"])
    assert life_development_review_packet_identity(messages)[0] == (
        "life-development-general-source-review-evidence-packet.4"
    )
    path = "outcomes.0.world_consequence.environment_text"
    assert (
        packet["reviewed_surface"]["outcomes"][0]["world_consequence"]["environment_text"]
        == ENVIRONMENT + " " + UNAUTHORED
    )
    verdict = {
        "decision": "unsupported",
        "typed_location_conflicts": [
            {
                "typed_location_ref": draft.location_ref,
                "prose_path": path,
                "conflicting_fragment": ENVIRONMENT,
            }
        ],
        "reason": "Fixture verdict checks the exact typed-location path only.",
    }
    parsed = parse_life_development_source_closure_review(raw=json.dumps(verdict), draft=draft)
    assert parsed.typed_location_conflicts[0].prose_path == path
    with pytest.raises(LifeDevelopmentSourceClosureError):
        parse_life_development_source_closure_review(
            raw=json.dumps(
                {
                    "decision": "unsupported",
                    "undeclared_fact_paths": [path],
                    "reason": "General review has no outcome coverage.",
                }
            ),
            draft=draft,
        )


@pytest.mark.parametrize("focused", [False, True])
def test_new_review_requires_and_hashes_original_execution_material(focused):
    manifest, draft = _consequence_material()
    compile_messages = (
        life_development_novel_origin_messages
        if focused
        else life_development_source_closure_messages
    )
    arguments = dict(context={}, manifest=manifest, draft=draft)
    if not focused:
        arguments["cited_events"] = ()
    with pytest.raises(ValueError, match="original execution_authority"):
        compile_messages(**arguments)
    authority = _review_authority(manifest)
    authority["execution_materials"] = [{"text": "The actor started checking a journal."}]
    first = compile_messages(**arguments, execution_authority=authority)
    changed = json.loads(json.dumps(authority))
    changed["execution_materials"][0]["text"] = "The actor started reading a book."
    second = compile_messages(**arguments, execution_authority=changed)
    assert _wire_hash(first) != _wire_hash(second)
    assert (
        life_development_review_packet_identity(first)[1]
        != (life_development_review_packet_identity(second)[1])
    )
    packet = json.loads(first[-1]["content"])
    pinned = packet["pinned_authority" if focused else "pinned_source_evidence"]
    assert pinned["execution_authority"] == authority


@pytest.mark.asyncio
async def test_actual_provider_request_carries_new_fields_and_rejection_stays_valid(monkeypatch):
    """Actual adapter/HTTP/parser boundary; no runtime retry or semantic-accuracy claim."""
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    manifest, draft = _consequence_material()
    authority = _review_authority(manifest)
    messages = life_development_novel_origin_messages(
        context={}, manifest=manifest, draft=draft, execution_authority=authority
    )
    requests = []
    path = "outcomes.0.world_consequence.environment_text"

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["messages"] == messages
        packet = json.loads(body["messages"][-1]["content"])
        assert packet["pinned_authority"]["execution_authority"] == authority
        assert (
            packet["reviewed_surface"]["outcomes"][0]["world_consequence"]["environment_text"]
            == ENVIRONMENT + " " + UNAUTHORED
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": _reject(path, UNAUTHORED)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            },
        )

    provider = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(respond),
    )
    try:
        adapter = RoleBoundLifeDevelopmentModelAdapter(
            model=provider, role="world_author_source_reviewer"
        )
        raw = await adapter.complete(messages)
        verdict = parse_life_development_novel_origin_review(raw=raw, draft=draft)
        assert verdict.decision == "unsupported"
        assert verdict.unsupported_outcome_prerequisites[0].exact_fragments == (UNAUTHORED,)
        assert len(requests) == 1
    finally:
        await provider.aclose()

def _existing_world_material_for_ref(manifest, legacy, ref):
    manifest = type(manifest).model_validate_json(
        json.dumps(
            manifest.model_dump(mode="json", exclude_computed_fields=True)
            | {"grounding_refs": sorted(set(manifest.grounding_refs) | {ref})}
        )
    )
    value = legacy.model_dump(mode="json")
    value["claim_declarations"][0].update(
        scope="existing_world",
        subject_scope="existing_entity",
        source_refs=[ref],
    )
    draft = parse_world_author_draft(
        raw=json.dumps(value),
        manifest=manifest,
        logical_time=NOW,
    )
    return manifest, draft


def test_cited_pinned_materials_resolve_exact_context_and_location_policy():
    manifest, _legacy = _legacy_material()
    ref = "biography:" + "a" * 64
    item = {
        "item_ref": ref,
        "privacy_class": "personal",
        "value": {"biography_id": ref, "age": 21, "academic_year": 3},
    }
    context = {"slices": {"world_life": {"items": [item]}}}

    material = resolve_cited_pinned_material(context=context, manifest=manifest, ref=ref)

    assert material is not None
    assert material["authority_kind"] == "pinned_context_item"
    assert material["materials"] == [{"slice": "world_life", "item": item}]

    policy_ref = manifest.location_capabilities[0].authority_refs[0]
    policy = resolve_cited_pinned_material(context={}, manifest=manifest, ref=policy_ref)

    assert policy is not None
    assert policy["authority_kind"] == "reviewed_location_catalog_policy"
    assert policy["materials"][0]["location_ref"] == manifest.location_capabilities[0].location_ref
    assert resolve_cited_pinned_material(context={}, manifest=manifest, ref="fact:" + "b" * 64) is None


def test_general_review_packet_carries_exact_cited_pinned_materials():
    manifest, legacy = _legacy_material()
    ref = "biography:" + "a" * 64
    manifest, draft = _existing_world_material_for_ref(manifest, legacy, ref)
    item = {
        "item_ref": ref,
        "privacy_class": "personal",
        "value": {"biography_id": ref, "age": 21, "academic_year": 3},
    }
    context = {"slices": {"world_life": {"items": [item]}}}
    material = resolve_cited_pinned_material(context=context, manifest=manifest, ref=ref)
    assert material is not None

    messages = life_development_source_closure_messages(
        context=context,
        manifest=manifest,
        draft=draft,
        cited_events=(),
        cited_pinned_materials=(material,),
    )
    packet = json.loads(messages[-1]["content"])

    assert packet["pinned_source_evidence"]["cited_pinned_materials"] == [material]
    assert packet["pinned_source_evidence"]["cited_committed_events"] == []
    assert "cited_pinned_materials" in messages[0]["content"]
    with pytest.raises(ValueError, match="exactly close existing-world claim refs"):
        life_development_source_closure_messages(
            context=context,
            manifest=manifest,
            draft=draft,
            cited_events=(),
        )

