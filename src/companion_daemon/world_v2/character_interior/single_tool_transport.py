"""Choose a provider-compatible carrier before request identity is compiled.

The schema and its decoder continue to require one exact named tool. Auto is
only the provider's selection mode, not permission to accept plain text or a
different function. Nonthinking providers keep their original wire/identity.
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
