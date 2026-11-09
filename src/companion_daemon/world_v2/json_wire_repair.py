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


_VALID_JSON_VALUE_STARTERS = (
    '"',
    "{",
    "[",
    "-",
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "t",
    "f",
    "n",
    "]",
)


def _starts_with_object_key(after_comma: str) -> bool:
    if not after_comma.startswith('"'):
        return False
    escaped = False
    for idx in range(1, len(after_comma)):
        ch = after_comma[idx]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            return after_comma[idx + 1 :].lstrip().startswith(":")
    return False


def escape_unescaped_quotes_in_json_strings(text: str) -> str:
    """Escape ASCII quotes and raw control characters that cannot close a JSON string.

    An object-key string closes only before ``:`` (or end of text). A value
    string closes only before its enclosing container closer (``}`` or ``]``),
    end of text, or ``,`` followed by a valid next member/element token.
    Every other quote is inner speech and can be escaped losslessly.
    """

    repaired: list[str] = []
    in_string = False
    in_key_string = False
    escaped = False
    container_stack: list[str] = []
    last_nonspace_outside = ""
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if in_string:
            if escaped:
                repaired.append(character)
                escaped = False
                index += 1
                continue
            if character == "\\":
                repaired.append(character)
                escaped = True
                index += 1
                continue
            if character == "\n":
                repaired.append("\\n")
                index += 1
                continue
            if character == "\r":
                repaired.append("\\r")
                index += 1
                continue
            if character == "\t":
                repaired.append("\\t")
                index += 1
                continue
            if character == '"':
                rest = text[index + 1 :].lstrip()
                enclosing = container_stack[-1] if container_stack else ""
                if in_key_string:
                    is_closer = rest.startswith(":") or rest == ""
                else:
                    expected_closer = "}" if enclosing == "{" else ("]" if enclosing == "[" else "")
                    if rest == "" or (expected_closer and rest.startswith(expected_closer)):
                        is_closer = True
                    elif not expected_closer and rest.startswith(("}", "]")):
                        is_closer = True
                    elif enclosing == "{" and _starts_with_object_key(rest):
                        is_closer = True
                    elif rest.startswith(","):
                        after_comma = rest[1:].lstrip()
                        if after_comma == "":
                            is_closer = True
                        elif enclosing == "{":
                            is_closer = after_comma.startswith("}") or _starts_with_object_key(
                                after_comma
                            )
                        else:
                            is_closer = after_comma.startswith(_VALID_JSON_VALUE_STARTERS)
                    else:
                        is_closer = False
                if is_closer:
                    in_string = False
                    in_key_string = False
                    last_nonspace_outside = '"'
                    repaired.append(character)
                else:
                    repaired.append("\\")
                    repaired.append('"')
                index += 1
                continue
            repaired.append(character)
            index += 1
            continue
        if (
            character == '"'
            and container_stack
            and container_stack[-1] == "{"
            and last_nonspace_outside in ("{", ",")
            and text[index : index + 3] in ('"}"', '"]"')
            and not text[index + 3 :].lstrip().startswith(":")
        ):
            closer_char = text[index + 1]
            if not text[index + 3 :].lstrip().startswith(closer_char):
                repaired.append(closer_char)
                container_stack.pop()
                last_nonspace_outside = closer_char
            index += 3
            continue
        repaired.append(character)
        if character == '"':
            in_string = True
            in_key_string = bool(
                container_stack
                and container_stack[-1] == "{"
                and last_nonspace_outside in ("{", ",")
            )
            escaped = False
        elif character in ("{", "["):
            container_stack.append(character)
            last_nonspace_outside = character
        elif character in ("}", "]"):
            if container_stack:
                container_stack.pop()
            last_nonspace_outside = character
        elif not character.isspace():
            last_nonspace_outside = character
        index += 1
    return "".join(repaired)


def merge_split_object_arrays(text: str) -> str:
    """Merge adjacent ``["a"], ["b"]`` array fragments on an object property.

    DeepSeek occasionally serializes a multi-bubble ``messages`` array inside
    ``payload_json`` as ``"messages":["a"],["b"]``—closing the array after the
    first item and writing the next item as a bare ``[...]`` after a comma at
    object depth.  In JSON grammar an object member after ``,`` at
    ``depth_array == 0`` must begin with a quoted key, never ``[``, so merging
    ``], [`` at ``depth_object > 0`` and ``depth_array == 0`` is lossless and
    never alters nested arrays or string contents.
    """

    repaired: list[str] = []
    depth_object = 0
    depth_array = 0
    in_string = False
    escaped = False
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if in_string:
            repaired.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            repaired.append(character)
            index += 1
            continue
        if character == "{":
            depth_object += 1
        elif character == "}":
            depth_object = max(0, depth_object - 1)
        elif character == "[":
            depth_array += 1
        elif character == "]":
            if depth_array == 1 and depth_object > 0:
                look = index + 1
                while look < length and text[look].isspace():
                    look += 1
                if look < length and text[look] == ",":
                    look += 1
                    while look < length and text[look].isspace():
                        look += 1
                    if look < length and text[look] == "[":
                        repaired.append(", ")
                        index = look + 1
                        continue
                    if look < length and text[look] == "{":
                        repaired.append(", ")
                        index = look
                        continue
            depth_array = max(0, depth_array - 1)
        repaired.append(character)
        index += 1
    return "".join(repaired)


def close_unclosed_json_containers(text: str) -> str:
    """Append missing ``}`` / ``]`` after a complete last field.

    DeepSeek often finishes a tool call (``finish_reason=tool_calls``) one
    closing brace short of the forced wrapper. Inner fields are intact; only
    the transport envelope is unclosed. A cut inside a string, an unmatched
    closer, or a already-balanced document is left unchanged so the caller
    stays fail-closed.
    """

    open_stack: list[str] = []
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
            open_stack.append("}")
        elif character == "[":
            open_stack.append("]")
        elif character in ("}", "]"):
            if not open_stack or open_stack[-1] != character:
                return text
            open_stack.pop()
    if in_string or escaped or not open_stack:
        return text
    return text + "".join(reversed(open_stack))


def repair_commas_outside_strings(text: str) -> str:
    """Remove trailing commas before ``}``/``]`` and insert missing commas before ``"``."""

    repaired: list[str] = []
    in_string = False
    escaped = False
    last_nonspace_outside = ""
    for index, character in enumerate(text):
        if in_string:
            repaired.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
                last_nonspace_outside = '"'
            continue
        if character == ",":
            rest = text[index + 1 :].lstrip()
            if rest.startswith(("}", "]")):
                continue
            repaired.append(character)
            last_nonspace_outside = ","
            continue
        if character == '"':
            if last_nonspace_outside in ('"', "}", "]") or (
                last_nonspace_outside and last_nonspace_outside.isalnum()
            ):
                repaired.append(",")
            in_string = True
            escaped = False
            repaired.append(character)
            continue
        repaired.append(character)
        if not character.isspace():
            last_nonspace_outside = character
    return "".join(repaired)


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
        comma_fixed = repair_commas_outside_strings(text)
        if comma_fixed != text:
            texts.append(comma_fixed)
    for text in list(texts):
        merged = merge_split_object_arrays(text)
        if merged != text:
            texts.append(merged)
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
