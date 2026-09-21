"""Versioned transport for newly authored World.2 candidates, not new authority.

The original messages remain the durable request carrier. Bind the exact tool
wire into those messages before hashing/storing them; recovery continues to use
the original stored messages and the unchanged canonical draft parser.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .character_interior.inbound_tool_contract import (
    _inline_refs,
    deepseek_strict_tool_schema,
)
from .character_interior.single_tool_transport import resolve_single_tool_transport
from .character_interior.local_schema_references import factor_local_schema_references
from .life_development_draft import ORDINARY_LIFE_PHOTO_PRIVACY, LifeDevelopmentNoOpDraft
from .life_development_output_schema import life_possibility_output_schema


CONTRACT = "world-consequence-author-tool.3"
TOOL_NAME = "author_world_consequence_v3"
_CONTRACTS = {
    "world-consequence-author-tool.1": "author_world_consequence_v1",
    "world-consequence-author-tool.2": "author_world_consequence_v2",
    CONTRACT: TOOL_NAME,
}


def world_consequence_author_tool_contract(
    *, provider: object, contract_id: str = CONTRACT,
) -> dict[str, object]:
    if not isinstance(contract_id, str) or contract_id not in _CONTRACTS:
        raise ValueError("unknown World author tool contract")
    tool_name = _CONTRACTS[contract_id]
    propose = life_possibility_output_schema(outcome_contract="world-consequence.2")
    if contract_id == CONTRACT:
        _bind_nonempty_visual_environment(propose)
    # The two branches have disjoint source_kind literals. Keep that exact
    # union using the provider's supported anyOf, before its subset projection
    # removes oneOf. This changes only this new tool's schema carrier.
    binding = propose["$defs"]["AuthorizedAttemptResult"]["properties"]["execution_binding"]
    binding["anyOf"] = binding.pop("oneOf")
    binding.pop("discriminator")
    propose = _inline_refs(propose, propose["$defs"])
    proposals = (
        [propose] if contract_id == "world-consequence-author-tool.1"
        else _located_visual_branches(propose)
    )
    schema = {
        "type": "object",
        "properties": {
            "replacement": {
                "anyOf": [
                    LifeDevelopmentNoOpDraft.model_json_schema(mode="validation"),
                    *proposals,
                ]
            }
        },
        "required": ["replacement"],
        "additionalProperties": False,
    }
    parameters = deepseek_strict_tool_schema(schema)
    if contract_id != "world-consequence-author-tool.1":
        # Keep complete union branches for the native strict parser; reuse only
        # exact repeated children, using the existing lossless $def factoring.
        parameters = factor_local_schema_references(parameters)
    tools = [{
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "Return the complete World Author no_op or propose decision.",
            "strict": True,
            "parameters": parameters,
        },
    }]
    transport = resolve_single_tool_transport(
        provider=getattr(provider, "authority_origin", provider),
        tools=tools,
        tool_choice={"type": "function", "function": {"name": tool_name}},
        identity={"contract_id": contract_id, "tool_name": tool_name},
    )
    return {"tools": tools, "tool_choice": transport.tool_choice}


def _bind_nonempty_visual_environment(propose: dict[str, object]) -> None:
    """Expose the canonical environment invariant in the supported tool dialect.

    An absent environment is already nullable. When an object is supplied,
    at least one of its canonical fields must carry text. Complete anyOf
    branches survive the strict adapter without forcing a particular fact.
    The older tool contracts deliberately retain their exact schema bytes.
    """
    environment = propose["$defs"]["LifeDevelopmentVisualEnvironmentDraft"]
    fields = environment["properties"]
    branches = []
    for name, field in fields.items():
        text = next(item for item in field["anyOf"] if item.get("type") == "string")
        # DeepSeek supports pattern but not minLength; this admits all
        # nonempty text, including newlines, without selecting its meaning.
        text["pattern"] = r"[\s\S]"
        branches.append({"properties": {name: deepcopy(text)}, "required": [name]})
    environment["anyOf"] = branches


def _located_visual_branches(propose: dict[str, object]) -> list[dict[str, object]]:
    """Expose existing parser invariants without choosing location or privacy.

    Location remains the author's choice. A located ordinary outcome requires
    visual facts; withhold forbids them, and an unlocated proposal cannot claim
    visual location. Equality with a chosen location, claim closure and privacy
    ranks still belong to the unchanged canonical validator.
    """

    branches = []
    for located in (False, True):
        branch = deepcopy(propose)
        fields = branch["properties"]
        for field in ("location_ref", "location_capability_ref"):
            fields[field] = (
                next(item for item in fields[field]["anyOf"] if item.get("type") != "null")
                if located else {"type": "null"}
            )
        # These optional canonical fields are explicit nulls in the strict wire.
        # Mark them required before projection, so the located branch cannot be
        # widened back to nullable while completing branch envelopes.
        branch["required"] = list(dict.fromkeys([
            *branch["required"], "location_ref", "location_capability_ref",
        ]))
        original_outcome = fields["outcomes"]["items"]
        outcomes = []
        for ordinary in (True, False):
            outcome = deepcopy(original_outcome)
            properties = outcome["properties"]
            properties["privacy_class"] = {
                "type": "string",
                "enum": sorted(ORDINARY_LIFE_PHOTO_PRIVACY) if ordinary else ["withhold"],
            }
            visual = properties["visual_evidence"]
            if not ordinary:
                properties["visual_evidence"] = {"type": "null"}
            elif located:
                properties["visual_evidence"] = next(
                    item for item in visual["anyOf"] if item.get("type") != "null"
                )
            else:
                for item in visual["anyOf"]:
                    if item.get("type") == "object":
                        item["properties"]["location"] = {"type": "null"}
            outcome["required"] = list(dict.fromkeys([
                *outcome["required"], "visual_evidence",
            ]))
            outcomes.append(outcome)
        fields["outcomes"]["items"] = {"anyOf": outcomes}
        branches.append(branch)
    return branches


def bind_world_consequence_author_tool(
    *, messages: list[dict[str, str]], tool_contract: dict[str, object],
) -> list[dict[str, str]]:
    """Make a new request identity without rewriting any historical compiler."""

    user = json.loads(messages[1]["content"])
    if "world_author_wire" in user:
        raise ValueError("World author wire is already bound")
    user["world_author_wire"] = _wire_identity(tool_contract)
    return [
        messages[0],
        {"role": messages[1]["role"], "content": json.dumps(user, ensure_ascii=False)},
        *messages[2:],
    ]


def recover_world_consequence_author_tool(
    *, messages: list[dict[str, str]], provider: object,
) -> dict[str, object]:
    """Carry the original transport into same-author source correction.

    Old requests without the marker retain JSON transport even when the current
    provider supports tools. A marked request must match this exact tool wire;
    never silently upgrade its schema or selection mode while reusing its pin.
    """

    user = json.loads(messages[1]["content"])
    if "world_author_wire" not in user:
        if user.get("world_author_json_transport") == "json_object":
            origin = getattr(provider, "authority_origin", provider)
            if not all(callable(getattr(model, "complete_json", None)) for model in (provider, origin)):
                raise TypeError("original World author JSON transport is unavailable")
        return {}
    if getattr(provider, "supports_strict_tool_choice", False) is not True:
        raise ValueError("original World author tool transport is unavailable")
    marker = user["world_author_wire"]
    contract_id = marker.get("contract") if isinstance(marker, dict) else None
    if not isinstance(contract_id, str) or contract_id not in _CONTRACTS:
        raise ValueError("original World author tool transport identity changed")
    contract = world_consequence_author_tool_contract(provider=provider, contract_id=contract_id)
    if user["world_author_wire"] != _wire_identity(contract):
        raise ValueError("original World author tool transport identity changed")
    return contract


def _wire_identity(tool_contract: dict[str, object]) -> dict[str, object]:
    tool_name = tool_contract["tools"][0]["function"]["name"]
    contract_id = next((key for key, name in _CONTRACTS.items() if name == tool_name), None)
    if contract_id is None:
        raise ValueError("unknown World author tool transport")
    encoded = json.dumps(tool_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "contract": contract_id,
        "tool_name": tool_name,
        "tool_choice": tool_contract["tool_choice"],
        "tool_contract_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "arguments_contract": "exact_replacement_envelope_of_output_contract",
    }
