"""Explicit live output wires without silently widening historical requests."""

from .life_development_draft import LifeDevelopmentPossibilityDraft


_HISTORICAL_OUTCOME_DESCRIPTION = (
    "One objective candidate branch, not companion interior or a channel Action.\n\n"
    "``text`` may propose objective actions, photographs, NPC talk and world\n"
    "consequences. New companion feelings, motives, thoughts, intentions and\n"
    "subjective reactions belong to the Character Model. Exact source-bound\n"
    "historical interior is context only, not a new response. It cannot make a\n"
    "completed user-channel act true: sending him a message or photo, his\n"
    "receiving it, or his reply through that channel. Those facts exist only as\n"
    "authorized Action and receipt events. ``user_channel_completion`` is the\n"
    "structural acknowledgement; this author has no Action authority, so the\n"
    "only legal value is ``none``."
)


def life_possibility_output_schema(*, outcome_contract: str | None = None,
                                  authorized_attempt_claims: bool = False, event_details: bool = False,
                                  required_event_details: bool = False,
                                  closing_offset_seconds: int | None = None,
                                  ordinary_course_detail: bool = False,
                                  event_detail_privacy_class: str | None = None) -> dict[str, object]:
    schema = LifeDevelopmentPossibilityDraft.model_json_schema(mode="validation")
    definitions = schema["$defs"]
    if event_details:
        for field in ("causal_authority", "outcome_resolution_authority"):
            schema["properties"][field] = {"type": "string", "const": "world_contingency"}
        definitions["LifeDevelopmentTimingDraft"]["properties"]["mode"] = {"type": "string", "const": "now"}
        if required_event_details:
            if closing_offset_seconds is None or closing_offset_seconds < 60:
                raise ValueError("required event detail needs its exact final release offset")
            schema["required"].append("event_details")
            steps = schema["properties"]["event_details"]
            steps["minItems"] = 2
            steps["allOf"] = [{"contains": {"required": ["after_seconds"],
                "properties": {"after_seconds": {"const": offset}}}} for offset in (0, closing_offset_seconds)]
    if not event_details:
        schema["properties"].pop("event_details", None)
        definitions.pop("EventDetailStep", None)
    # This author's existing capability does not publish objective planning.
    consequence = definitions["WorldConsequenceV2"]["properties"]
    consequence.pop("world_planning", None)
    consequence["contract"] = {"const": "world-consequence.2", "default": "world-consequence.2", "title": "Contract", "type": "string"}
    for key in ("WorldPlanningDraft", "PlanningSlot", "PlanningException", "PlanningPhase"):
        definitions.pop(key, None)
    if not authorized_attempt_claims:
        # Frozen legacy/v1-v4 wire: adding a new live declaration must not
        # change the schema or request hash of already-recorded author calls.
        allowed = definitions["LifeDevelopmentClaimDeclaration"]["properties"]["subject_scope"]["enum"]
        allowed.remove("authorized_attempt_result")
    elif outcome_contract != "world-consequence.2":
        raise ValueError("attempt-result claims require the bounded World.2 contract")
    outcome = definitions["LifeDevelopmentOutcomeDraft"]
    if outcome_contract == "world-consequence.2":
        outcome["properties"].pop("text")
        outcome["properties"]["world_consequence"] = {"$ref": "#/$defs/WorldConsequenceV2"}
        outcome["required"] = [
            key for key in outcome.get("required", []) if key != "text"
        ] + ["world_consequence"]
    elif outcome_contract is None:
        for key in (
            "WorldConsequenceV2", "AuthorizedAttemptResult", "ActivityExecutionBinding",
            "ReceiptExecutionBinding",
        ):
            definitions.pop(key)
        outcome["properties"].pop("world_consequence")
        outcome["properties"]["text"] = {
            "maxLength": 12_000, "minLength": 1, "title": "Text", "type": "string",
        }
        outcome["required"].insert(1, "text")
        outcome["description"] = _HISTORICAL_OUTCOME_DESCRIPTION
    else:
        raise ValueError("unknown life possibility output contract")
    if ordinary_course_detail:
        if not required_event_details or event_detail_privacy_class not in {
            "public", "shareable", "personal", "private"
        }:
            raise ValueError("ordinary course detail requires a role-visible source privacy")
        properties = schema["properties"]
        for key in ("location_ref", "location_capability_ref"):
            properties[key] = {"type": "null"}
        properties["entity_refs"] = {"type": "array", "maxItems": 0, "items": {"type": "string"}}
        properties["privacy_class"] = {"type": "string", "const": event_detail_privacy_class}
        properties["outcomes"]["minItems"] = properties["outcomes"]["maxItems"] = 2
        outcome_properties = definitions["LifeDevelopmentOutcomeDraft"]["properties"]
        outcome_properties["privacy_class"] = {"type": "string", "const": event_detail_privacy_class}
        for key in ("provisional_npcs", "provisional_places"):
            outcome_properties[key] = {"type": "array", "maxItems": 0,
                                       "items": outcome_properties[key]["items"]}
        for key in ("objective_biographical_transition", "dynamic_life_direction", "visual_evidence"):
            outcome_properties[key] = {"type": "null"}
    return schema
