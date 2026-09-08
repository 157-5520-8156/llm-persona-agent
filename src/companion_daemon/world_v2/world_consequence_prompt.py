"""The current World Author wire, separate from historical action-candidate prose."""

from __future__ import annotations

from copy import deepcopy
import json

from .life_development_output_schema import life_possibility_output_schema
from .world_consequence_contract import WorldConsequenceAuthority
from .world_consequence_execution_context import WorldConsequenceExecutionMaterial


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
                "output_contract and cross_field_authority govern the complete object. "
                "Return exactly one JSON object."
            ),
        },
        {"role": "user", "content": json.dumps(value, ensure_ascii=False, separators=(",", ":"))},
    ]
