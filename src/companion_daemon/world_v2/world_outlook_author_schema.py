"""Advertise canonical action-specific shapes; keep stored draft parsing unchanged."""

from .world_outlook import WorldOutlookDraft


def outlook_author_schema(*, basis_refs=None, npc_privacy_floors=(), prior_opportunities=(), now=None):
    schema = WorldOutlookDraft.model_json_schema()
    fields = schema["$defs"]["OutlookExecution"]["properties"]
    common = ("text", "privacy_class", "duration_minutes")
    variants = []
    for kind, extra, required in (
        ("environment", ("prepare_details",), ()),
        ("planning", ("planning",), ("planning",)),
        ("npc_statement", ("prepare_details",), ()),
        ("npc_activity", ("activity_kind", "location_ref", "participant_npc_refs"), ("activity_kind",)),
        ("npc_cancel_activity", ("target_plan_ref",), ("target_plan_ref",)),
        ("npc_status", ("target_status",), ("target_status",)),
    ):
        properties = {"kind": {"const": kind, "type": "string"},
                      **{key: fields[key] for key in (*common, *extra)}}
        for key in required:
            # Required type-specific values cannot be null. Old persisted
            # models keep their existing null/default compatibility rules.
            definition = properties[key]
            properties[key] = next(option for option in definition["anyOf"] if option.get("type") != "null")
        if "participant_npc_refs" in properties:
            properties["participant_npc_refs"] = {**properties["participant_npc_refs"], "uniqueItems": True}
        variants.append({"type": "object", "additionalProperties": False,
                         "properties": properties, "required": ["kind", "text", *required]})
    schema["$defs"]["OutlookExecution"] = {"oneOf": variants}
    opportunity = schema["$defs"]["OutlookOpportunity"]
    if basis_refs is not None:
        offered = list(dict.fromkeys(basis_refs))
        opportunity["properties"]["basis_refs"]["items"] = (
            {"type": "string", "enum": offered} if offered else {"not": {}}
        )
    opportunity["allOf"] = [{
        "if": {"properties": {"lane": {"const": "npc"}}},
        "then": {"required": ["npc_ref"], "properties": {
            "npc_ref": {"type": "string"},
            "execution": {"properties": {"kind": {"enum": ["npc_statement", "npc_activity", "npc_cancel_activity", "npc_status"]}}},
        }},
        "else": {"properties": {
            "npc_ref": {"type": "null"},
            "execution": {"properties": {"kind": {"enum": ["environment", "planning"]}}},
        }},
    }]
    privacy_order = ("public", "shareable", "personal", "private", "withhold")
    for floor in npc_privacy_floors:
        opportunity["allOf"].append({
            "if": {"required": ["npc_ref"], "properties": {"npc_ref": {"const": floor["npc_ref"]}}},
            "then": {"properties": {"execution": {
                "required": ["privacy_class"],
                "properties": {"privacy_class": {"enum": list(privacy_order[privacy_order.index(floor["privacy_class"]):])}},
            }}},
        })
    prior_opportunities = tuple(n for n in prior_opportunities if now is None or n.closes_at > now)
    if prior_opportunities:
        # Old basis is legal only for an entire unchanged prior node. Do not
        # grant its historical authority to new or edited opportunities.
        schema["$defs"]["OutlookOpportunity"] = {"anyOf": [
            opportunity,
            {"enum": [node.model_dump(mode="json") for node in prior_opportunities]},
        ]}
    schema["$defs"]["OutlookDependency"]["anyOf"] = [
        {"required": ["ready_statuses"], "properties": {"ready_statuses": {"minItems": 1}}},
        {"required": ["unchanged_token"], "properties": {"unchanged_token": {"type": "string"}}},
    ]
    # Pydantic's display titles/defaults are annotation only. Keeping the
    # actual fields, types and constraints makes the richer schema affordable.
    def without_annotations(value):
        if isinstance(value, dict):
            # Enum/const contain literal authored data, not schema nodes.
            # In particular, a prior timetable's title is a required value.
            return {key: child if key in {"enum", "const"} else without_annotations(child) for key, child in value.items()
                    if not (key == "default" or key == "title" and isinstance(child, str))}
        if isinstance(value, list):
            return [without_annotations(child) for child in value]
        return value

    return without_annotations(schema)
