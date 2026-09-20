"""Versioned transport for newly authored World.2 candidates, not new authority.

The original messages remain the durable request carrier. Bind the exact tool
wire into those messages before hashing/storing them; recovery continues to use
the original stored messages and the unchanged canonical draft parser.
"""

from __future__ import annotations

import hashlib
import json

from .character_interior.inbound_tool_contract import (
    _inline_refs,
    deepseek_strict_tool_schema,
)
from .character_interior.single_tool_transport import resolve_single_tool_transport
from .life_development_draft import LifeDevelopmentNoOpDraft
from .life_development_output_schema import life_possibility_output_schema


CONTRACT = "world-consequence-author-tool.1"
TOOL_NAME = "author_world_consequence_v1"


def world_consequence_author_tool_contract(*, provider: object) -> dict[str, object]:
    propose = life_possibility_output_schema(outcome_contract="world-consequence.2")
    # The two branches have disjoint source_kind literals. Keep that exact
    # union using the provider's supported anyOf, before its subset projection
    # removes oneOf. This changes only this new tool's schema carrier.
    binding = propose["$defs"]["AuthorizedAttemptResult"]["properties"]["execution_binding"]
    binding["anyOf"] = binding.pop("oneOf")
    binding.pop("discriminator")
    schema = {
        "type": "object",
        "properties": {
            "replacement": {
                "anyOf": [
                    LifeDevelopmentNoOpDraft.model_json_schema(mode="validation"),
                    _inline_refs(propose, propose["$defs"]),
                ]
            }
        },
        "required": ["replacement"],
        "additionalProperties": False,
    }
    tools = [{
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Return the complete World Author no_op or propose decision.",
            "strict": True,
            "parameters": deepseek_strict_tool_schema(schema),
        },
    }]
    transport = resolve_single_tool_transport(
        provider=getattr(provider, "authority_origin", provider),
        tools=tools,
        tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
        identity={"contract_id": CONTRACT, "tool_name": TOOL_NAME},
    )
    return {"tools": tools, "tool_choice": transport.tool_choice}


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
        return {}
    if getattr(provider, "supports_strict_tool_choice", False) is not True:
        raise ValueError("original World author tool transport is unavailable")
    contract = world_consequence_author_tool_contract(provider=provider)
    if user["world_author_wire"] != _wire_identity(contract):
        raise ValueError("original World author tool transport identity changed")
    return contract


def _wire_identity(tool_contract: dict[str, object]) -> dict[str, object]:
    encoded = json.dumps(tool_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "contract": CONTRACT,
        "tool_name": TOOL_NAME,
        "tool_choice": tool_contract["tool_choice"],
        "tool_contract_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "arguments_contract": "exact_replacement_envelope_of_output_contract",
    }
