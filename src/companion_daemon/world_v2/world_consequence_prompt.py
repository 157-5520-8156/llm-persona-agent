"""The current World Author wire, separate from historical action-candidate prose."""

from __future__ import annotations

from copy import deepcopy
import json

from .life_development_output_schema import life_possibility_output_schema
from .life_development_draft import LifeDevelopmentDraftError, LifeDevelopmentPossibilityDraft
from .life_content_store import MAX_LIFE_CONTENT_CHARACTERS
from .world_consequence_contract import WorldConsequenceAuthority
from .world_consequence_execution_context import WorldConsequenceExecutionMaterial


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


def _compliant_propose_example(*, manifest: dict[str, object]) -> dict[str, object] | None:
    """One exact current-contract shape using only refs offered in this request.

    The example is generated per request so it never contains an invented
    placeholder anchor.  It intentionally omits location fields: a location is
    optional and, when used, must copy one exact offered capability pair and
    one exact offered window.
    """

    anchors = manifest.get("anchor_refs")
    anchor = next(
        (item for item in anchors if isinstance(item, str) and item),
        None,
    ) if isinstance(anchors, (list, tuple)) else None
    if anchor is None:
        return None
    owner = manifest.get("owner_actor_ref")
    if not isinstance(owner, str) or not owner:
        return None
    return {
        "decision": "propose",
        "authored_subject_ref": owner,
        "causal_authority": "character_choice",
        "outcome_resolution_authority": "character_choice",
        "premise_scope": "external_opportunity",
        "premise": (
            "One concrete external development is available in the current "
            "environment as a candidate opportunity."
        ),
        "premise_claim_refs": ["local:claim:candidate-development"],
        "claim_declarations": [
            {
                "claim_id": "local:claim:candidate-development",
                "summary": (
                    "A new proposal-scoped external development and its candidate "
                    "environmental consequences are offered; no prior relationship, "
                    "shared history, or completed character experience is asserted."
                ),
                "scope": "novel_world_generation",
                "subject_scope": "world_environment",
                "source_refs": [],
            }
        ],
        "timing": {"mode": "now", "duration_minutes": 30},
        "anchor_refs": [anchor],
        "entity_refs": [],
        "privacy_class": "shareable",
        "outcomes": [
            {
                "experienced_by_ref": owner,
                "world_consequence": {
                    "contract": "world-consequence.2",
                    "environment_text": (
                        "A concrete, observable change in the external situation."
                    ),
                },
                "user_channel_completion": "none",
                "privacy_class": "shareable",
                "relative_plausibility_weight": 6000,
                "claim_refs": ["local:claim:candidate-development"],
            },
            {
                "experienced_by_ref": owner,
                "world_consequence": {
                    "contract": "world-consequence.2",
                    "environment_text": (
                        "A different concrete, observable external situation."
                    ),
                },
                "user_channel_completion": "none",
                "privacy_class": "shareable",
                "relative_plausibility_weight": 4000,
                "claim_refs": ["local:claim:candidate-development"],
            },
        ],
    }


def compile_world_consequence_messages(
    *, user_context: dict[str, object], authority: WorldConsequenceAuthority,
    execution_materials: tuple[WorldConsequenceExecutionMaterial, ...],
) -> list[dict[str, str]]:
    """Render already-pinned evidence; do not select events or interpret prose."""
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
    # The historical specimen and boundary explicitly permit authoring new
    # protagonist actions. Neither is an example of the current protocol.
    value.pop("disturbance_consequence_usage_specimen", None)
    boundary = value.get("cross_field_authority")
    if not isinstance(boundary, dict):
        raise ValueError("world consequence prompt lacks hard boundaries")
    boundary.pop("outcome_text", None)
    boundary["world_consequence"] = {
        "contract": "world-consequence.2",
        "environment_text": "external_world_consequences_only",
        "authorized_attempt_result": {
            "optional": True,
            "execution_binding": "copy_one_exact_offered_execution_binding",
            "text": "objective_result_of_only_that_original_authorized_attempt",
            "no_execution_binding": "omit_this_field",
            "future_character_plan": "omit_this_field; an existing attempt cannot supply execution for a new Plan",
        },
        "character_actions_and_inner_response": "require_character_authorship",
        "selection_of_an_outcome_token": "cannot_author_a_character_action_or_response",
        "prior_attempt": "not_evidence_of_success_or_of_embedded_historical_assertions",
        "user_channel_completion": "none",
    }
    schema = life_possibility_output_schema(outcome_contract="world-consequence.2")
    value["output_contract"] = {"no_op": {"decision": "no_op"}, "propose": schema}
    value["execution_authority"] = authority.model_dump(mode="json")
    value["execution_materials"] = [item.model_dump(mode="json") for item in execution_materials]
    if value.get("occasion_mode") == "disturbance" or value.get("pressure_surfaces"):
        disturbance_guidance = (
            "This is a disturbance occasion. At least one outcome must carry a durable "
            "world consequence: include dynamic_life_direction with a summary of the "
            "same external change, a nonempty context_tags array, duration_days, and "
            "the same privacy_class as that outcome (or a stronger one). The claim "
            "declarations must cover that durable material. Compliant shape: "
            + json.dumps(
                {
                    "dynamic_life_direction": {
                        "summary": "The same durable external change the outcome describes.",
                        "context_tags": ["constraint:example-duration"],
                        "duration_days": 3,
                        "privacy_class": "personal",
                    }
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    else:
        disturbance_guidance = ""
    example = _compliant_propose_example(manifest=manifest)
    completion = manifest.get("completed_activity_consequence")
    if completion is not None:
        boundary["completed_activity_consequence"] = {
            "contract": "completed-activity-consequence.1",
            "terminal_evidence": "lifecycle_ended_only_not_success_location_or_embedded_history",
            "allowed_decision": "no_op_or_now_world_contingency_for_the_exact_offered_attempt",
            "each_outcome": "requires_the_same_offered_execution_binding",
            "character_response": "separate_character_authorship_after_settlement",
        }
        example_guidance = (
            "This request concerns only the aftermath of the completed activity bound in "
            "capability_manifest.completed_activity_consequence. Its terminal event proves "
            "the lifecycle ended; it does not prove intention fulfillment, location presence, "
            "embedded history, or successful action. Its original Started/Resumed source "
            "authorizes the same bounded attempt. Choose no_op or propose objective candidate "
            "consequences of that attempt as world_contingency with timing.mode now. Each "
            "outcome must include authorized_attempt_result with the exact offered binding. "
            "Do not restart or extend her activity, generate another Plan, rewrite an earlier "
            "settled result, or invent how she feels or reacts. New outcome uncertainty stays "
            "in your alternatives until the existing settlement, and her response remains "
            "a separate character decision.\n"
        )
    elif example is None:
        example_guidance = (
            "capability_manifest.anchor_refs is empty, so no propose decision is "
            "authorized; return the no_op object. "
        )
    else:
        example_guidance = (
            "Here is one exact compliant propose example for this request; mirror "
            "its field names and structure, but do not copy its premise, claim "
            "summary, anchor choice, or timestamps: "
            + json.dumps(example, ensure_ascii=False)
            + "\n"
        )
    return [
        {
            "role": "system",
            "content": (
                "You are the World Author. From this pinned world and capability manifest, "
                "author no_op or one source-bound life possibility. The premise describes "
                "the external environment and evidenced existing circumstances. Each of "
                "its 2-4 alternatives must use world_consequence contract world-consequence.2. "
                "Do not return the historical outcome text field.\n"
                "environment_text may author changes in the world, including ordinary, "
                "pleasant, difficult, adverse, brief, or durable circumstances. It must "
                "not invent what the protagonist or user does, decides, notices, feels, "
                "thinks, wants, remembers, or says. This boundary applies to every prose "
                "field and annex. The Character Model owns its new actions and reactions. "
                "The world can settle without her approval; an outcome token does not "
                "author a response for her. No personality trait proves a past action.\n"
                "When execution_authority offers an exact binding and execution_materials "
                "shows the original accepted intention, you may optionally author an "
                "objective candidate result of only that already-started attempt in "
                "authorized_attempt_result. Copy the binding exactly. Do not add another "
                "action or extend the scope of the attempt. Started is evidence of an "
                "attempt, not of prior success; the intention's embedded historical or "
                "external assertions gain no proof. If no binding is offered, omit the "
                "attempt result. A Plan, Clock, or outcome selection is not execution.\n"
                "Use world_contingency when the external world moves first; its resolution "
                "belongs to a recorded world draw or external observation. Use "
                "character_choice for an opportunity she can choose to undertake later. "
                "Its future Plan has not executed: omit authorized_attempt_result from "
                "those candidates. An existing attempt result belongs to its original "
                "activity and may be proposed separately as world_contingency. "
                "Do not narrate her acceptance or independently prescribe a life direction. "
                "Optional durable effects must follow from that exact candidate, not a "
                "hope, motive, or future Plan.\n"
                "Declare existing facts with exact entailing pinned sources. Declare new "
                "proposal-scoped world material as novel_world_generation with empty "
                "source_refs. Claims must cover every relying field; do not relabel prior "
                "relationships, shared history, or completed experiences as novel. A Clock "
                "proves time only. Residence or a schedule does not prove current presence. "
                "Use only listed existing refs, exact authorized location/time windows, "
                "and the coupled privacy ranks and visual-evidence rules in "
                "cross_field_authority. Do not invent a new author permission from an "
                "example or a claim declaration.\n"
                "recent_life_texture and any pressure_surfaces are evidence and opportunities, "
                "not novelty targets, plot menus, or behavior instructions. The full "
                "output_contract and cross_field_authority govern the complete object.\n"
                "anchor_refs is required and must copy exact members of "
                "capability_manifest.anchor_refs; never invent an anchor ref. "
                "location_ref and location_capability_ref are optional and must be "
                "supplied together. When you use a location, copy one exact offered pair "
                "from timing_coordinates.location_capability_coordinates and choose an "
                "opens_at/closes_at inside that location's offered near_term_later_interval "
                "when timing.mode is later, keeping closes_at - opens_at at or below "
                "capability_manifest.max_window_minutes; when timing.mode is now, keep "
                "duration_minutes at or below that capability's maximum_now_duration_minutes "
                "and inside its offered schedule; "
                "never use a placeholder location ref. When the possibility does not "
                "need a location, omit both location fields and every visual location. "
                "If a proposal location is present and an outcome has ordinary-life "
                "privacy, that outcome must include visual_evidence; within it use "
                "location null or exactly the proposal location_ref, never a different "
                "place.\n"
                + example_guidance
                + disturbance_guidance
                + "Return exactly one JSON object."
            ),
        },
        {"role": "user", "content": json.dumps(value, ensure_ascii=False, separators=(",", ":"))},
    ]
