"""Choose a provider-compatible carrier before request identity is compiled.

The schema and its decoder continue to bind one exact named result. Auto does
not permit text. A separately configured JSON-object text carrier is versioned
in request identity; it never repairs fields or bypasses host validation.
Unconfigured providers keep their original wire/identity.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Mapping, Sequence


@dataclass(frozen=True)
class SingleToolTransport:
    tool_choice: object
    identity: Mapping[str, str]


def resolve_single_tool_transport(
    *,
    provider: object,
    tools: Sequence[dict[str, object]],
    tool_choice: object,
    identity: Mapping[str, str],
) -> SingleToolTransport:
    mode = getattr(provider, "single_tool_selection_mode", "forced")
    carrier = getattr(provider, "single_tool_response_carrier", "native")
    if carrier != "native":
        if carrier != "native_or_json_object" or mode != "forced" or len(tools) != 1:
            raise ValueError("unsupported single-schema JSON text carrier")
        function = tools[0].get("function", {})
        expected = tool_choice.get("function", {}) if isinstance(tool_choice, dict) else {}
        name = function.get("name")
        if (not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is None
                or tools[0].get("type") != "function" or expected.get("name") != name
                or tool_choice.get("type") != "function" or identity.get("tool_name") != name):
            raise ValueError("JSON text carrier cannot change the expected schema")
        material = {
            "contract_id":"single-schema-json-text-carrier", "version":"1",
            "selection_mode":mode, "response_carrier":carrier, "expected_tool_name":name,
            "schema_contract_identity_json":json.dumps(dict(identity),sort_keys=True,separators=(",",":")),
        }
        material["contract_sha256"] = hashlib.sha256(
            json.dumps(material,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        return SingleToolTransport(tool_choice=tool_choice,identity=material)
    if mode == "forced":
        return SingleToolTransport(tool_choice=tool_choice, identity=identity)
    if mode != "auto":
        raise ValueError("unknown single-tool provider selection mode")
    if len(tools) != 1:
        raise ValueError("auto transport requires one declared function")
    tool = tools[0]
    function = tool.get("function") if tool.get("type") == "function" else None
    name = function.get("name") if isinstance(function, dict) else None
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is None:
        raise ValueError("auto transport requires one valid declared function name")
    expected = tool_choice.get("function") if isinstance(tool_choice, dict) else None
    if (
        not isinstance(expected, dict)
        or tool_choice.get("type") != "function"
        or expected.get("name") != name
        or identity.get("tool_name") != name
    ):
        raise ValueError("auto transport cannot change the schema's expected function")
    # The wrapped identity is explicitly a schema/decoder reference, not a
    # claim that the provider enforces a forced-function selection.
    schema_identity = json.dumps(dict(identity), sort_keys=True, separators=(",", ":"))
    auto_identity = {
        "contract_id": "single-tool-auto-transport",
        "version": "1",
        "selection_mode": "auto",
        "expected_tool_name": name,
        "schema_contract_identity_json": schema_identity,
    }
    auto_identity["contract_sha256"] = hashlib.sha256(
        json.dumps(auto_identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return SingleToolTransport(tool_choice="auto", identity=auto_identity)
