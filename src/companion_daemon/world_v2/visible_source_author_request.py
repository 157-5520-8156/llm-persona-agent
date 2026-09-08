"""Recover aliases from the original atomic author's logical provider request.

The caller must obtain expected_request_hash from the immutable author audit.
This carrier proves neither provider completion nor source-review acceptance.
It covers the two-message whole-author contract, including Core-owned Recall
follow-ups, not the older local Recall prose/continuation transport.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import math

from companion_daemon.llm import provider_invocation_request_hash


AUTHOR_REQUEST_CONTRACT = "visible-source-author-request.1"
_MAX_BYTES = 512_000
_PARAMETER_KEYS = {"messages", "temperature", "tools", "tool_choice", "identity_extras"}


def _canonical(value: object) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(raw.encode("utf-8")) > _MAX_BYTES:
            raise ValueError("visible author request exceeds its byte bound")
        return raw
    except (TypeError, RecursionError, UnicodeError) as exc:
        raise ValueError("visible author request is not bounded JSON") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("visible author request contains duplicate JSON members")
        value[key] = item
    return value


def _check_parameters(value: dict[str, object]) -> None:
    messages = value["messages"]
    if (
        type(messages) is not list
        or len(messages) != 2
        or any(
            type(message) is not dict
            or set(message) != {"role", "content"}
            or not isinstance(message["content"], str)
            for message in messages
        )
        or [message["role"] for message in messages] != ["system", "user"]
    ):
        raise ValueError("visible author request requires the atomic system and JSON user pair")
    temperature = value["temperature"]
    if type(temperature) not in {int, float} or not math.isfinite(temperature):
        raise ValueError("visible author request temperature is invalid")
    tools = value["tools"]
    if tools is not None and (
        type(tools) is not list or any(type(tool) is not dict for tool in tools)
    ):
        raise ValueError("visible author request tools are invalid")
    # The original hash excludes tool_choice when tools is None. Do not retain
    # a value that the purported immutable request hash never authenticated.
    if tools is None and value["tool_choice"] is not None:
        raise ValueError("visible author request has an unhashed tool choice")
    extras = value["identity_extras"]
    if extras is not None and type(extras) is not dict:
        raise ValueError("visible author request identity extras are invalid")


def prepare_visible_source_author_request(
    *,
    messages: list[dict[str, str]],
    temperature: float,
    tools: list[dict[str, object]] | None,
    tool_choice: object | None,
    identity_extras: Mapping[str, object] | None,
    expected_request_hash: str,
) -> str:
    """Freeze actual prepared parameters; never take a separate alias table."""
    if identity_extras is not None and not isinstance(identity_extras, Mapping):
        raise ValueError("visible author request identity extras are invalid")
    parameters = {
        "messages": messages,
        "temperature": temperature,
        "tools": tools,
        "tool_choice": tool_choice,
        "identity_extras": dict(identity_extras) if identity_extras is not None else None,
    }
    _check_parameters(parameters)
    raw = _canonical({"contract": AUTHOR_REQUEST_CONTRACT, **parameters})
    verify_visible_source_author_request(raw, expected_request_hash=expected_request_hash)
    return raw


def verify_visible_source_author_request(
    raw: str,
    *,
    expected_request_hash: str,
) -> dict[str, str]:
    """Recompute the provider formula, then extract the authenticated aliases.

    provider_invocation_request_hash hashes messages and temperature, adds both
    tools and tool_choice only when tools is not None, and merges every extras
    entry at the top level while rejecting collisions with those parameters.
    """
    if (
        not isinstance(expected_request_hash, str)
        or len(expected_request_hash) != 64
        or any(char not in "0123456789abcdef" for char in expected_request_hash)
    ):
        raise ValueError("visible author request hash must be an independent lowercase SHA-256")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("visible author request is unavailable or exceeds its byte bound")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (TypeError, RecursionError) as exc:
        raise ValueError("visible author request is not bounded JSON") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"contract", *_PARAMETER_KEYS}
        or value["contract"] != AUTHOR_REQUEST_CONTRACT
        or _canonical(value) != raw
    ):
        raise ValueError("visible author request carrier contract is invalid")
    _check_parameters(value)
    actual = provider_invocation_request_hash(**{key: value[key] for key in _PARAMETER_KEYS})
    if actual != expected_request_hash:
        raise ValueError("visible author request hash differs from the original author audit")
    try:
        user = json.loads(value["messages"][1]["content"], object_pairs_hook=_unique_object)
    except (TypeError, RecursionError) as exc:
        raise ValueError("visible author request user material is not JSON") from exc
    boundaries = user.get("expression_hard_boundaries") if isinstance(user, dict) else None
    aliases = boundaries.get("source_ref_aliases") if isinstance(boundaries, dict) else None
    if not isinstance(aliases, dict) or any(
        not isinstance(alias, str) or not alias or not isinstance(ref, str) or not ref
        for alias, ref in aliases.items()
    ):
        raise ValueError("visible author request lacks its original source_ref_aliases field")
    return dict(aliases)
