"""Advertise canonical action-specific shapes; keep stored draft parsing unchanged."""

from copy import deepcopy
from datetime import timedelta
from zoneinfo import ZoneInfo
from .world_outlook import WorldOutlookDraft


def outlook_author_schema(*, basis_refs=None, npc_privacy_floors=(), prior_opportunities=(), now=None,
                          rolling_planning=False, prior_threads=(), identity=None, preparation=None, history_offer=None,
                          new_opportunity_ids=None, story_planning=False, world_changes=()):
    schema = WorldOutlookDraft.model_json_schema()
    if story_planning:
        schema["properties"]["threads"]["minItems"] = 2
        thread = schema["$defs"]["OutlookThread"]
        thread["required"].append("developments")
        thread["properties"]["developments"]["minItems"] = 2
        stage = schema["$defs"]["OutlookStage"]
        stage["required"].append("thread_refs")
        stage["properties"]["thread_refs"]["minItems"] = 1
        schema["$defs"]["OutlookChangeResponse"]["allOf"] = [{
            "if": {"properties": {"decision": {"const": "retain"}}},
            "then": {"properties": {"planning_keys": {"maxItems": 0}}},
            "else": {"properties": {"thread_refs": {"minItems": 1}}},
        }]
        prior_threads = tuple(t for t in prior_threads if t.status == "closed" or len(t.developments) >= 2)
        if world_changes:
            schema["required"].append("change_responses")
            schema["properties"]["change_responses"].update(minItems=len(world_changes), maxItems=len(world_changes))
            schema["$defs"]["OutlookChangeResponse"]["properties"]["source_ref"] = {
                "type": "string", "enum": [change["source_ref"] for change in world_changes]}
        else:
            schema["properties"]["change_responses"]["maxItems"] = 0
    else:
        schema["properties"].pop("change_responses")
        schema["$defs"].pop("OutlookChangeResponse")
        schema["$defs"]["OutlookThread"]["properties"].pop("developments")
        for field in ("thread_refs", "conditions"):
            schema["$defs"]["OutlookStage"]["properties"].pop(field)
    if rolling_planning:
        schema["required"].append("long_term")
        schema["properties"]["long_term"]["minItems"] = 1
        planning = schema["$defs"]["WorldPlanningDraft"]
        planning["properties"]["contract"] = {"const": "world-planning.3", "type": "string"}
        planning["required"].extend(("contract", "detailed_through", "review_at", "phases"))
        for field in ("detailed_through", "review_at"):
            planning["properties"][field] = next(s for s in planning["properties"][field]["anyOf"] if s.get("type") != "null")
        slots = schema["$defs"]["PlanningSlot"]
        slots["required"].extend(("event_text", "prepare_details"))
        slots["properties"]["event_text"] = next(s for s in slots["properties"]["event_text"]["anyOf"] if s.get("type") != "null")
        slots["properties"]["prepare_details"] = {"type": "boolean", "const": True}
        slots["properties"]["duration_minutes"] = {"type": "integer", "minimum": 2, "maximum": 720}
        exception = schema["$defs"]["PlanningException"]
        exception["properties"]["duration_minutes"] = {
            "anyOf": [{"type": "integer", "minimum": 2, "maximum": 720}, {"type": "null"}],
        }
        if now is not None:
            local_day = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
            review = f"After {now.isoformat()} and no later than {(now + timedelta(days=7)).isoformat()}. This is the next background review, not the end of the term."
            planning["properties"]["review_at"]["description"] = review
            schema["properties"]["review_at"]["description"] = review
            planning["properties"]["detailed_through"]["description"] = f"At least {(local_day + timedelta(days=7)).isoformat()}, or ends_on for a finite arrangement ending sooner."
            schema["properties"]["long_term"]["description"] = f"Cover continuously from {local_day.isoformat()} for at least one year through {(local_day + timedelta(days=365)).isoformat()} (365 future days, not the preceding date); explicitly describe uncertain intervals."
        if history_offer is not None:
            offered = {item.anchor: item.privacy_class for item in history_offer.items}
            prior_threads = tuple(t for t in prior_threads if all(a in offered for a in t.history_anchors))
            threads = {t.thread_id: t for t in prior_threads}
            ranks = {v: i for i, v in enumerate(("public", "shareable", "personal", "private", "withhold"))}
            prior_opportunities = tuple(n for n in prior_opportunities if all(t in threads for t in n.thread_refs)
                and all(ranks[n.execution.privacy_class] >= ranks[offered[a]] for t in n.thread_refs for a in threads[t].history_anchors))
            refs = [a.source_ref for a in offered]
            schema["$defs"]["StoryAnchor"] = ({"type": "object", "additionalProperties": False,
                "properties": {"history_anchor_ref": {"type": "string", "enum": refs}},
                "required": ["history_anchor_ref"]} if refs else {"not": {}})
        if identity is not None:
            schema["required"].append("parent_revision")
            for key in ("world_id", "actor_ref", "parent_revision"):
                schema["properties"][key] = {"const": identity[key]}
            schema["properties"]["horizon_end"] = {"type": "string", "const": (now + timedelta(days=30)).isoformat()}
        if preparation is not None and preparation["due"]:
            schema["required"].append("opportunities")
            schema["properties"]["opportunities"]["allOf"] = [
                {"contains": {"type": "object", "required": ["execution", "opens_at"], "properties": {
                    "opens_at": {"const": now.isoformat()}, "execution": {
                        "type": "object", "required": ["kind", "planning"], "properties": {
                            "kind": {"const": "planning"}, "planning": {
                                "type": "object", "required": ["planning_key", "supersedes_occurrence_ref"],
                                "properties": {key: {"const": due[key]} for key in ("planning_key", "supersedes_occurrence_ref")}}}}}}}
                for due in preparation["due"]]
    else:
        # Old pinned dialects must not advertise capabilities they never offered,
        # or pay the input cost of the rolling schema.
        schema["properties"].pop("long_term")
        schema["$defs"].pop("OutlookStage")
        schema["$defs"].pop("PlanningPhase")
        planning = schema["$defs"]["WorldPlanningDraft"]["properties"]
        for key in ("phases", "detailed_through", "review_at", "continues"):
            planning.pop(key)
        planning["contract"] = {"type": "string", "const": "world-planning.1"}
        for key in ("event_text", "prepare_details"):
            schema["$defs"]["PlanningSlot"]["properties"].pop(key)
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
        if rolling_planning and kind == "planning":
            properties["duration_minutes"] = {"type": "integer", "const": 1,
                "description": "The publication step lasts one minute; scheduled events have their own durations."}
            properties["privacy_class"] = {"type": "string", "enum": ["public", "shareable", "personal", "private"]}
            variants[-1]["required"].append("duration_minutes")
    schema["$defs"]["OutlookExecution"] = {"oneOf": variants}
    opportunity = schema["$defs"]["OutlookOpportunity"]
    if new_opportunity_ids is not None:
        opportunity["properties"]["opportunity_id"]["enum"] = list(new_opportunity_ids)
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
            ({"type": "object", "additionalProperties": False,
              "properties": {"retain_opportunity_id": {"enum": [n.opportunity_id for n in prior_opportunities]}},
              "required": ["retain_opportunity_id"]} if rolling_planning else
             {"enum": [node.model_dump(mode="json") for node in prior_opportunities]}),
        ]}
    if rolling_planning and prior_threads:
        schema["$defs"]["OutlookThread"] = {"anyOf": [schema["$defs"]["OutlookThread"],
            {"type": "object", "additionalProperties": False,
             "properties": {"retain_thread_id": {"enum": [t.thread_id for t in prior_threads]}},
             "required": ["retain_thread_id"]}]}
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


def outlook_result_tool(draft_schema, *, allow_keep=True):
    """Native object-root carrier for the same two canonical result shapes."""
    schema = deepcopy(draft_schema)
    definitions = schema.pop("$defs")
    tool = {"type": "function", "function": {"name": "world_outlook_v6",
        "description": "Submit one private rolling outlook candidate per response. Rejected candidates need a corrected function call.",
        "parameters": {"type": "object", "additionalProperties": False, "$defs": definitions,
            "properties": {"decision": {"enum": ["keep", "revise"] if allow_keep else ["revise"]}, "draft": schema,
                           "review_at": {"type": "string", "format": "date-time"}, "reason": {"type": "string"}},
            "required": ["decision"], "allOf": [{
                "if": {"properties": {"decision": {"const": "keep"}}},
                "then": {"required": ["review_at", "reason"], "not": {"required": ["draft"]}},
                "else": {"required": ["draft"], "not": {"anyOf": [{"required": ["review_at"]}, {"required": ["reason"]}]}}}]}}}
    if not allow_keep:
        # Missing coverage already requires a revision. Do not advertise the
        # keep-only fields and rely on a conditional schema to forbid them.
        parameters = tool["function"]["parameters"]
        parameters["properties"].pop("review_at")
        parameters["properties"].pop("reason")
        parameters["required"] = ["decision", "draft"]
        parameters.pop("allOf")
    return tool
