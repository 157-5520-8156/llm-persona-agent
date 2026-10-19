"""The current World Author wire, separate from historical action-candidate prose."""

from __future__ import annotations

from copy import deepcopy
import json

from .life_development_output_schema import life_possibility_output_schema
from .life_development_draft import LifeDevelopmentDraftError, LifeDevelopmentPossibilityDraft
from .life_content_store import MAX_LIFE_CONTENT_CHARACTERS
from .world_consequence_contract import WorldConsequenceAuthority
from .world_consequence_execution_context import WorldConsequenceExecutionMaterial
from .world_environment_attention import (
    environment_attention, independent_scene_context,
)


def _recent_consequence_view(texture: object) -> object:
    """Expose the actual history before its proof metadata, without new facts.

    The full pinned source rows remain in pinned_world_context. This second
    view is an index for the author, not another source or an authority upgrade.
    Unknown/legacy row formats stay verbatim; only typed settled consequences
    have a compact rendering. No prose classification or topic quota is used.
    """
    if not isinstance(texture, dict) or texture.get("contract") != "recent-life-texture.1":
        return texture
    rows = []
    for item in texture.get("items", []):
        value = item.get("value") if isinstance(item, dict) else None
        if not isinstance(value, dict):
            rows.append(deepcopy(item))
            continue
        if value.get("context_kind") == "biographical_context":
            continue  # Background identity is not an event in recent life.
        content = value.get("content")
        if not isinstance(content, dict) or not isinstance(content.get("world_consequence"), dict):
            rows.append(deepcopy(item))
            continue
        rows.append({
            "source_ref": item["source_ref"], "lane": item["lane"],
            "settled_at": value.get("settled_at"),
            "privacy_class": value.get("privacy_class"),
            "location_ref": value.get("location_ref"),
            "content_ref": content.get("content_ref"),
            "truncated": content.get("truncated"),
            "world_consequence": deepcopy(content["world_consequence"]),
            **({"character_response": deepcopy(content["character_response"])}
               if content.get("character_response") is not None else {}),
        })
    return {
        **deepcopy(texture), "contract": "recent-life-texture.2", "items": rows,
        "reading_scope": "historical_consequences_not_current_presence_or_new_character_actions",
        "source_details": "same_source_ref_in_pinned_world_context",
    }


def validate_world_consequence_offered_bindings(*, draft, messages) -> None:
    if not isinstance(draft, LifeDevelopmentPossibilityDraft):
        return
    declarations = []
    for message in messages:
        if message.get("role") != "user":
            continue
        try:
            value = json.loads(message["content"])
        except (ValueError, KeyError):
            continue
        if isinstance(value, dict) and "execution_authority" in value:
            declarations.append(value["execution_authority"])
    if len(declarations) != 1:
        raise ValueError("world consequence request lacks unique execution authority")
    authority = WorldConsequenceAuthority.model_validate_json(json.dumps(declarations[0]))
    for index, outcome in enumerate(draft.outcomes):
        consequence = outcome.world_consequence
        if consequence is not None and consequence.contract != "world-consequence.2":
            raise ValueError("this author capability cannot publish objective planning")
        if consequence is None:
            raise ValueError("world consequence request cannot accept historical outcome text")
        attempt = consequence.authorized_attempt_result
        if attempt is not None and attempt.execution_binding not in authority.execution_bindings:
            raise LifeDevelopmentDraftError(
                "execution_binding_not_offered",
                "The attempt result must copy one exact execution binding from the original request.",
                violations=({
                    "path": f"outcomes.{index}.world_consequence.authorized_attempt_result.execution_binding",
                    "type": "value_error",
                    "message": "exact_offered_execution_binding",
                },),
            )
        if len(outcome.content_text) > MAX_LIFE_CONTENT_CHARACTERS:
            raise LifeDevelopmentDraftError(
                "consequence_content_too_large",
                "Each complete serialized world consequence must fit the 12000-character content limit.",
                violations=({
                    "path": f"outcomes.{index}.world_consequence",
                    "type": "value_error", "message": "complete_content_size_limit",
                },),
            )


_CORE_GUIDANCE = """You are the World Author. Return exactly one JSON object: no_op, or one source-bound
possibility with 2-4 world_consequence.2 alternatives under output_contract.
Author external circumstances, never a new protagonist/user action, decision, perception,
feeling, memory or response. CharacterInterior owns those choices. An outcome token grants
no character authorship; user-channel completion requires authorized Action and receipt.
Existing-world claims need exact entailing pinned sources. Novel world generation may
create current proposal-scoped external facts, not earlier friendships, shared history or
completed experience. Claims must cover every field that relies on them.
Reference namespaces are distinct: premise_claim_refs, outcome.claim_refs and detail.claim_refs
use claim_declarations[*].claim_id, never source event IDs. Original evidence belongs in
claim_declarations[*].source_refs. Their used claim IDs must close over the declarations.
Each visual_evidence.objects entry requires its own local_ref as well as its description.
A Clock proves time only; a schedule, residence, personality or intention proves neither presence nor success.
Use the exact capabilities and privacy rules in capability_manifest and cross_field_authority.
anchor_refs must copy offered anchor_refs. A location is optional: omit both location fields
or copy an offered location_ref/capability_ref pair. timing_coordinates.location_capability_coordinates provides derived
windows; capability_manifest retains the complete original location schedules and permissions.
Keep duration within capability_manifest.max_window_minutes and the selected location window.
Located ordinary-privacy outcomes require visual_evidence, with visual location null or the
same proposal location. Unlocated outcomes cannot introduce a visual location; withhold
cannot carry visual evidence. Preserve source privacy through every nested effect.
When an outcome actually establishes ongoing resources, obligations, employment, residence
or enrollment, record that external state in objective_biographical_transition, not only prose.
Reuse its current coordinate_ref and replacement prefixes when updating an established state.
Each replaces_context_tag_prefixes entry must end in a colon and match exactly ONE context_tags
value; namespace and value use lowercase ASCII letters, digits, dot, underscore or hyphen.
coordinate_ref starts with the literal biography: prefix (colon, not dot).
Optional dynamic_life_direction.narrative_tags use only narrative:<lowercase_ascii_value>.
Put detailed obligations in summary; do not put multiple values under one replaced namespace.
Every objective field, including coordinate_ref and context_tags, must agree with the exact
accepted branch. An obligation is not payment; permission is not attendance. Labels cannot assert
an extra executed action or a stronger factual state than the summary and bound result.
A failed attempt may leave that state unchanged; do not manufacture an effect to force a plot.
Durable or biographical effects must follow from the selected external outcome;
they cannot prescribe a desire, future Plan, gratitude or relationship reward. Provisional
people/places remain proposal-scoped until settlement and grant no retroactive history.
History and pressure surfaces constrain consistency; they are not instructions, novelty
quotas or required plot developments. Choose the content yourself; no_op remains valid.
自然语言字段用中文；协议键、枚举与来源坐标保持原样。
"""

_COURSE_GUIDANCE = """You are the World Author. Return one complete propose JSON object under output_contract
for an already active external course. Author its full environmental progression now; only due
event_details become visible later. A timetable proves time and a venue, never the character's
attendance, perception, action, feeling or response. Existing-world claims need exact entailing
pinned sources. New external facts need proposal-scoped novel_world_generation declarations cited
by every field relying on them. Copy offered anchors and preserve source privacy, timing and all
cross_field_authority boundaries. This ordinary course detail has no typed new people or places,
visual evidence, biographical/life-direction effect, user-channel completion or character choice;
Fate may schedule a separate encounter. Write natural-language fields in Chinese; preserve protocol
keys, enums and source coordinates.
"""


def _without_schema_annotations(node):
    """Remove display metadata at schema nodes, never domain properties/literals."""
    from .character_interior.schema_nodes import map_schema_children
    if not isinstance(node, dict):
        return deepcopy(node)
    value = {key: child for key, child in node.items() if key not in {"title", "default"}}
    value = map_schema_children(value, _without_schema_annotations)
    if "$defs" in value:
        value["$defs"] = {key: _without_schema_annotations(child) for key, child in value["$defs"].items()}
    return value


def _reference_duplicate_capabilities(boundary, manifest):
    """Keep the exact source once; unknown or unequal material is never removed."""
    locations = manifest.get("location_capabilities")
    location = boundary.get("location_binding")
    if isinstance(location, dict) and locations is not None and location.get("available_capabilities") == locations:
        del location["available_capabilities"]
        location["capabilities_source"] = "capability_manifest.location_capabilities"
    privacy = boundary.get("privacy_lattice")
    if not isinstance(privacy, dict):
        return
    floors = manifest.get("npc_privacy_floors")
    if floors is not None and privacy.get("npc_privacy_floors") == floors:
        del privacy["npc_privacy_floors"]
        if floors:
            privacy["npc_privacy_source"] = "capability_manifest.npc_privacy_floors"
    allowed = privacy.get("allowed_outcome_privacy_by_proposal_privacy", {})
    visual = privacy.get("allowed_visual_outcome_privacy_by_proposal_privacy", {})
    if locations is not None and all(c.get("privacy_class") in allowed and c.get("privacy_class") in visual for c in locations):
        expected = [{"capability_ref": c["capability_ref"], "location_ref": c["location_ref"],
                     "privacy_floor": c["privacy_class"], "allowed_proposal_privacy": allowed[c["privacy_class"]],
                     "allowed_recipient_unbound_visual_proposal_privacy": visual[c["privacy_class"]]} for c in locations]
        if privacy.get("location_capability_privacy_envelopes") == expected:
            del privacy["location_capability_privacy_envelopes"]
            privacy["location_privacy_source"] = "capability_manifest.location_capabilities[].privacy_class"


def compile_world_consequence_messages(
    *, user_context: dict[str, object], authority: WorldConsequenceAuthority,
    execution_materials: tuple[WorldConsequenceExecutionMaterial, ...],
    cache_stable_prefix: bool = False,
    temporal_publication: dict[str, object] | None = None,
) -> list[dict[str, str]]:
    """Present the existing task and sources; parsing/admission stay authoritative."""
    value = deepcopy(user_context)
    manifest = value.get("capability_manifest")
    if not isinstance(manifest, dict) or any((
        manifest.get("outcome_contract") != "world-consequence.2",
        manifest.get("pinned_cursor") != authority.evaluated_cursor.model_dump(mode="json"),
        manifest.get("owner_actor_ref") != authority.actor_ref,
        any(item.status != "available" for item in execution_materials),
        tuple(item.execution_binding for item in execution_materials) != authority.execution_bindings,
    )):
        raise ValueError("world consequence prompt lacks exact execution material")
    boundary = value.get("cross_field_authority")
    if not isinstance(boundary, dict):
        raise ValueError("world consequence prompt lacks hard boundaries")
    if manifest.get("semantic_source_review_version") == "2":
        if temporal_publication is None:
            raise ValueError("World author requires its exact publication-time boundary")
        value["temporal_publication"] = temporal_publication
    value.pop("disturbance_consequence_usage_specimen", None)
    boundary.pop("outcome_text", None)
    scope = next((key for key in ("completed_activity_consequence", "active_attempt_consequence", "event_detail_capability")
                  if manifest.get(key) is not None), None)
    has_details = manifest.get("event_detail_capability") is not None
    detail_capability = manifest.get("event_detail_capability") or {}
    required_details = bool(detail_capability.get("required_program"))
    ordinary_course_detail = required_details and detail_capability.get("contract") == "event-detail-capability.2"
    closing_offset = detail_capability.get("closing_offset_seconds")
    bound_attempt = scope in {"completed_activity_consequence", "active_attempt_consequence"}
    value["world_author_task"] = scope or "open_world_possibility"
    value["world_author_prompt_contract"] = (
        "world-consequence-prompt.8" if temporal_publication is not None
        else "world-consequence-prompt.4" if ordinary_course_detail else "world-consequence-prompt.7"
    )
    boundary["world_consequence"] = {
        "contract": "world-consequence.2", "environment_text": "external_world_consequences_only",
        "authorized_attempt_result": {
            "presence": "required_in_every_outcome" if bound_attempt else "optional_with_offered_binding" if authority.execution_bindings else "omit_or_null",
            "execution_binding_source": "execution_authority.execution_bindings",
            "text": "objective_result_of_only_that_original_authorized_attempt",
        },
        "character_actions_and_inner_response": "require_character_authorship",
        "selection_of_an_outcome_token": "cannot_author_a_character_action_or_response",
        "prior_attempt": "not_evidence_of_success_or_of_embedded_historical_assertions",
        "user_channel_completion": "none",
    }
    if bound_attempt:
        boundary[scope] = {
            "contract": "completed-activity-consequence.1" if scope == "completed_activity_consequence" else "active-attempt-consequence.1",
            **({"terminal_evidence": "lifecycle_ended_only_not_success_location_or_embedded_history"}
               if scope == "completed_activity_consequence" else
               {"lifecycle_evidence": "active_attempt_and_elapsed_time_only_not_success_or_presence"}),
            "allowed_decision": "no_op_or_now_world_contingency_for_the_exact_offered_attempt",
            "each_outcome": "requires_the_same_offered_execution_binding",
            "character_response": "separate_character_authorship_after_settlement",
        }
    if authority.execution_bindings:
        # The parser already permits this narrow claim kind. Legacy generic
        # descriptions must not contradict the actual offered result capability.
        scopes = boundary.get("claim_declarations", {}).get("novel_world_generation", {}).get("allowed_subject_scopes")
        classification = value.get("claim_classification_contract", {}).get("proposal_scoped_novel_world", {}).get("requirements", {}).get("subject_scope")
        for allowed in (scopes, classification):
            if isinstance(allowed, list) and "authorized_attempt_result" not in allowed:
                allowed.append("authorized_attempt_result")
    _reference_duplicate_capabilities(boundary, manifest)
    schema = life_possibility_output_schema(outcome_contract="world-consequence.2",
        authorized_attempt_claims=bool(authority.execution_bindings), event_details=has_details,
        required_event_details=required_details, closing_offset_seconds=closing_offset,
        ordinary_course_detail=ordinary_course_detail,
        event_detail_privacy_class=detail_capability.get("privacy_class") if ordinary_course_detail else None)
    if not ordinary_course_detail:
        # Advertise the existing canonical validators, including constraints
        # Pydantic after-validators cannot express in generated JSON Schema.
        for name, prefix_field in (("ObjectiveBiographicalTransitionDraft", "replaces_context_tag_prefixes"),
                                   ("DynamicLifeDirectionDraft", "supersedes_context_tag_prefixes")):
            properties = schema["$defs"][name]["properties"]
            properties["context_tags"]["items"] = {"type": "string", "pattern": r"^[a-z][a-z0-9._-]*:[a-z0-9._-]+$"}
            properties[prefix_field]["items"] = {"type": "string", "pattern": r"^[a-z][a-z0-9._-]*:$"}
            properties[prefix_field]["description"] = "Each prefix replaces exactly one matching context_tags value. Protected narrative/season/relationship/privacy/safety/direction namespaces are unavailable."
        schema["$defs"]["ObjectiveBiographicalTransitionDraft"]["properties"]["coordinate_ref"]["description"] = "Literal biography: prefix, then lowercase ASCII path; for example biography:community.membership, never biography.community.membership. This is syntax, not a required subject."
        schema["$defs"]["DynamicLifeDirectionDraft"]["properties"]["narrative_tags"]["items"] = {
            "type": "string", "pattern": r"^narrative:[a-z0-9._-]+$"}
    if scope is not None:
        resolutions = ["world_contingency"]
        if not has_details and manifest.get("allow_external_observation_outcomes"):
            resolutions.append("external_observation")
        boundary["authority_pairings"] = {"world_contingency": {"outcome_resolution_authority": resolutions}}
        schema["properties"]["causal_authority"] = {"type": "string", "const": "world_contingency"}
        schema["properties"]["outcome_resolution_authority"] = {"type": "string", "enum": resolutions}
        timing_schema = schema["$defs"]["LifeDevelopmentTimingDraft"]
        timing_schema["properties"]["mode"] = {"type": "string", "const": "now"}
        timing_schema["properties"]["duration_minutes"] = next(
            option for option in timing_schema["properties"]["duration_minutes"]["anyOf"] if option.get("type") != "null"
        )
        for field in ("opens_at", "closes_at"):
            timing_schema["properties"][field] = {"type": "null"}
        timing_schema["required"] = [*timing_schema["required"], "duration_minutes"]
    consequence = schema["$defs"]["WorldConsequenceV2"]
    if not authority.execution_bindings:
        consequence["properties"]["authorized_attempt_result"] = {"type": "null"}
        for key in ("AuthorizedAttemptResult", "ActivityExecutionBinding", "ReceiptExecutionBinding"):
            schema["$defs"].pop(key, None)
    elif bound_attempt:
        consequence["required"] = [*consequence["required"], "authorized_attempt_result"]
        consequence["properties"]["authorized_attempt_result"] = {"$ref": "#/$defs/AuthorizedAttemptResult"}
    value["output_contract"] = {**({} if required_details else {"no_op": {"decision": "no_op"}}),
                                "propose": _without_schema_annotations(schema)}
    value["execution_authority"] = authority.model_dump(mode="json")
    value["execution_materials"] = [item.model_dump(mode="json") for item in execution_materials]
    if "recent_life_texture" in value:
        value["recent_life_texture"] = _recent_consequence_view(value["recent_life_texture"])

    # These rows are derived conveniences. Keep full permissions in the manifest
    # and remove only fields that repeat them exactly or are illegal in this task.
    timing = value.get("timing_coordinates", {})
    if scope is not None and isinstance(timing.get("timing_modes"), dict):
        timing["timing_modes"].pop("later", None)
    locations = {c.get("capability_ref"): c for c in manifest.get("location_capabilities", [])}
    for row in timing.get("location_capability_coordinates", []):
        source = locations.get(row.get("capability_ref"), {})
        if row.get("schedule_formula") == {"local_windows": source.get("local_windows"), "weekdays": source.get("weekdays")}:
            row.pop("schedule_formula")
        if scope is not None:
            row.pop("near_term_later_interval", None)
    attention = environment_attention(value)
    value.pop("environment_attention", None)
    if attention is not None:
        value["environment_attention"] = attention
        value["pinned_world_context"] = independent_scene_context(value.get("pinned_world_context"))

    if bound_attempt:
        guidance = (
            f"This request concerns only capability_manifest.{scope}, not a new opportunity. "
            "Choose no_op or now/world_contingency consequences inside that exact original attempt's "
            "authorized scope and elapsed time. A lifecycle start/end proves neither success, presence "
            "nor the intention's embedded history. Do not extend or finish the lifecycle, add a new "
            "action or Plan, or rewrite a settled result. Every outcome requires the same offered "
            "execution binding. Describe a concrete full, partial or unsuccessful result; a disclaimer "
            "about missing evidence is not itself an activity result.\n"
        )
    elif required_details:
        guidance = (
            "Continue the already active external event in event_detail_capability.source_event_ref. "
            "This request requires now/world_contingency and a complete event_details programme; "
            "no_op and an empty programme are invalid. Keep its sourced setting and do not invent a "
            "new opportunity or the protagonist's participation.\n"
        )
    elif has_details:
        guidance = (
            "Continue only the active external event identified by event_detail_capability.source_event_ref. "
            "Choose no_op or now/world_contingency consequences for that event, resolved by world_contingency. "
            "Keep its sourced situation; do not sample a different setting, a new opportunity or a new disturbance.\n"
        )
    elif not manifest.get("anchor_refs"):
        guidance = "capability_manifest.anchor_refs is empty: no propose is authorized; return no_op.\n"
    else:
        guidance = (
            "Use world_contingency when the external world moves first; an outcome cannot select her response. "
            "Use character_choice for a future opportunity she may accept, never as evidence that its Plan "
            "executed. Later timing must fit an offered near_term_later_interval and the original schedule.\n"
        )
    if authority.execution_bindings:
        guidance += (
            "Execution material grants narrow result creation, not just reading known results: declare the "
            "new result as novel_world_generation/authorized_attempt_result, used only by its bound result "
            "text. Keep premise and environment on separate declarations. An offered attempt supplies no "
            "execution for a new future Plan.\n"
        )
    if has_details:
        guidance += (
            "event_details are private preparations, not current facts or character knowledge. after_seconds "
            "is measured from starts_at; use increasing distinct offsets and world_environment claim_refs. "
            "Independent steps must hold under every outcome; otherwise bind outcome_index and wait for "
            "that selected settlement. Never prewrite protagonist actions, perception, feelings, gratitude "
            "or relationship changes. Unreleased details stop on pause, departure or source change. Each "
            "step must leave 60 seconds before ends_at; all step texts together are limited to 8000 characters.\n"
        )
        if required_details:
            guidance += (
                f"Author the opening at after_seconds=0 and the final-minute scene at "
                f"after_seconds={closing_offset}, with any useful intermediate steps. "
                "Author the complete external progression now; only its scheduled steps become visible later. "
                + ("For this ordinary class, write exactly two external outcomes and the timed steps. "
                   "Set proposal and outcome privacy_class exactly to event_detail_capability.privacy_class. "
                   "Keep location fields null, entity_refs and provisional people/places empty, and "
                   "visual, biographical and life-direction effects null; scene prose belongs in "
                   "event_details and world_consequence.environment_text. "
                   "The separate Fate lane may schedule significant encounters. "
                   if ordinary_course_detail else "")
                + (
                "If visual evidence is included, declare and reference each current visual fact, "
                "including weather, light, objects and activity, or omit optional assertions; "
                "every visual_evidence.claim_refs entry must also appear in that outcome.claim_refs; "
                "the course schedule alone proves none of those scene details. "
                "If introducing a provisional person or place, narrative_tags use "
                "narrative: followed by lowercase letters, digits, dots, underscores or hyphens. "
                if not ordinary_course_detail else "")
                + "\n"
            )
    if value.get("occasion_mode") == "disturbance":
        guidance += (
            "This is a disturbance occasion; no_op remains valid. If proposing, at least one outcome "
            "must carry a durable external consequence through dynamic_life_direction, "
            "objective_biographical_transition or a provisional NPC/place. Use the selected field's "
            "complete schema, source declarations and privacy floor. The content and outcome remain yours.\n"
        )
    elif attention is not None:
        guidance += (
            "For this independent ordinary opening, environment_attention selects an offered external "
            "setting, not protagonist presence, participation or observation. A quiet, unremarkable scene "
            "is valid; it need not advance the last story, solve her agenda or create a problem.\n"
        )
    if temporal_publication is not None:
        guidance += (
            "temporal_publication separates premise activation, outcome settlement and each private "
            "detail step. A completed fact must not occur after the time its field becomes available. "
            "Keep a later future scene only in its timed event_details step, not in an earlier outcome "
            "or premise. A known future plan may be stated as a plan, never as already completed. "
            "An attempt result also stays inside its original elapsed execution interval.\n"
        )
    if cache_stable_prefix:
        stable_manifest_fields = (
            "version", "owner_actor_ref", "location_capabilities", "entity_refs", "npc_capabilities",
            "npc_privacy_floors", "biographical_context_tags", "allow_external_observation_outcomes",
            "outcome_contract", "execution_intention_sources_version", "pinned_source_materials_version",
            "semantic_source_review_version", "max_future_days", "max_window_minutes",
        )
        value["capability_manifest"] = {
            **{key: manifest[key] for key in stable_manifest_fields if key in manifest},
            **{key: item for key, item in manifest.items() if key not in stable_manifest_fields},
        }
        stable_fields = (
            "output_contract", "claim_classification_contract", "authored_subject", "world_author_wire",
            "cross_field_authority", "capability_manifest", "pinned_world_context", "recent_life_texture",
        )
        value = {**{key: value[key] for key in stable_fields if key in value},
                 **{key: item for key, item in value.items() if key not in stable_fields}}
    elif scope == "completed_activity_consequence":
        stable_fields = ("output_contract", "claim_classification_contract", "authored_subject", "world_author_wire")
        value = {**{key: value[key] for key in stable_fields if key in value},
                 **{key: item for key, item in value.items() if key not in stable_fields}}
    core = _COURSE_GUIDANCE if ordinary_course_detail else _CORE_GUIDANCE
    if required_details and not ordinary_course_detail:
        core = core.replace("no_op, or one source-bound", "one source-bound").replace(
            "Choose the content yourself; no_op remains valid.",
            "Choose the external content for this already active event; a complete detail programme is required.")
    messages = [
        {"role": "system", "content": core + (
            "The following system message carries this request's exact task/binding guidance.\n" if cache_stable_prefix else guidance
        )},
        {"role": "user", "content": json.dumps(value, ensure_ascii=False, separators=(",", ":"))},
    ]
    if cache_stable_prefix:
        messages.append({"role": "system", "content": guidance})
    return messages
