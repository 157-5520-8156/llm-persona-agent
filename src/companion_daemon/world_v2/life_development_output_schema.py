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


def life_possibility_output_schema(*, outcome_contract: str | None = None) -> dict[str, object]:
    schema = LifeDevelopmentPossibilityDraft.model_json_schema(mode="validation")
    definitions = schema["$defs"]
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
    return schema
