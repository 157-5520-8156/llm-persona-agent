"""Deterministic transport-shape repair for model JSON.

This module never invents a role decision. It only recovers the four
observed provider-envelope failures: a Markdown fence, an unescaped
ASCII quote inside a JSON string, a missing wrapper ``}`` / ``]`` after
the last complete field, and trailing Extra data after one complete
object. Truncation inside a string, a non-object root, or a structurally
unrecoverable fragment stay fail-closed.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any


_ObjectPairsHook = Callable[[list[tuple[str, object]]], Any]
_ParseConstant = Callable[[str], Any]


def strip_json_fence(raw: str, *, unclosed_message: str = "JSON fence is unclosed") -> str:
    """Drop a provider's accidental Markdown fence around one JSON value."""

    candidate = raw.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) < 3 or not lines[-1].strip().startswith("```"):
            raise ValueError(unclosed_message)
        candidate = "\n".join(lines[1:-1]).strip()
    return candidate


def escape_unescaped_quotes_in_json_strings(text: str) -> str:
    """Escape ASCII quotes that cannot close a JSON string.

    A quote is a terminator only when the next non-space character is
    ``,``, ``}``, ``]``, ``:``, or the end of the text. Every other quote
    is inner speech and can be escaped losslessly. This does not rewrite
    keys, numbers, or already-escaped quotes.
    """

    repaired: list[str] = []
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                repaired.append(character)
                escaped = False
                continue
            if character == "\\":
                repaired.append(character)
                escaped = True
                continue
            if character == '"':
                rest = text[index + 1 :].lstrip()
                if rest.startswith((",", "}", "]", ":")) or rest == "":
                    in_string = False
                    repaired.append(character)
                else:
                    repaired.append("\\")
                    repaired.append('"')
                continue
            repaired.append(character)
            continue
        repaired.append(character)
        if character == '"':
            in_string = True
            escaped = False
    return "".join(repaired)


def close_unclosed_json_containers(text: str) -> str:
    """Append missing ``}`` / ``]`` after a complete last field.

    DeepSeek often finishes a tool call (``finish_reason=tool_calls``) one
    closing brace short of the forced wrapper. Inner fields are intact; only
    the transport envelope is unclosed. A cut inside a string, an unmatched
    closer, or a already-balanced document is left unchanged so the caller
    stays fail-closed.
    """

    depth_object = 0
    depth_array = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            continue
        if character == "{":
            depth_object += 1
        elif character == "}":
            depth_object -= 1
        elif character == "[":
            depth_array += 1
        elif character == "]":
            depth_array -= 1
        if depth_object < 0 or depth_array < 0:
            return text
    if in_string or escaped or (depth_object == 0 and depth_array == 0):
        return text
    return text + ("]" * depth_array) + ("}" * depth_object)


def loads_one_json_object(
    raw: str,
    *,
    object_pairs_hook: _ObjectPairsHook | None = None,
    parse_constant: _ParseConstant | None = None,
    not_object_message: str = "expected one JSON object",
    invalid_message: str = "did not return one JSON object",
    unclosed_fence_message: str = "JSON fence is unclosed",
) -> dict[str, object]:
    """Parse one object after the shared transport-shape repairs.

    Order is fence strip, inner-quote escape, wrapper close, then Extra
    data takes the first complete object. Semantic validation stays with
    the caller.
    """

    candidate = strip_json_fence(raw, unclosed_message=unclosed_fence_message)
    texts = [candidate]
    repaired = escape_unescaped_quotes_in_json_strings(candidate)
    if repaired != candidate:
        texts.append(repaired)
    for text in list(texts):
        closed = close_unclosed_json_containers(text)
        if closed != text:
            texts.append(closed)
    decode_kwargs: dict[str, object] = {}
    if object_pairs_hook is not None:
        decode_kwargs["object_pairs_hook"] = object_pairs_hook
    if parse_constant is not None:
        decode_kwargs["parse_constant"] = parse_constant
    decoder = json.JSONDecoder(**decode_kwargs)  # type: ignore[arg-type]
    last_error: json.JSONDecodeError | None = None
    for text in texts:
        try:
            value = json.loads(text, **decode_kwargs)  # type: ignore[arg-type]
        except json.JSONDecodeError as exc:
            last_error = exc
            if "Extra data" not in exc.msg:
                continue
            try:
                value, _end = decoder.raw_decode(text)
            except json.JSONDecodeError:
                continue
        if isinstance(value, dict):
            return value
        raise ValueError(f"{not_object_message}: got type={type(value).__name__}")
    raise ValueError(invalid_message) from last_error


__all__ = [
    "close_unclosed_json_containers",
    "escape_unescaped_quotes_in_json_strings",
    "loads_one_json_object",
    "strip_json_fence",
]
