"""Canonical forced-tool transport for the inbound character call.

This module owns provider-visible structure only.  It neither makes a role
choice nor materializes proposals: the role selects a capability branch through
``result_kind``, and callers receive the pre-existing wire after that envelope
has been removed.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
from os import getenv
from pathlib import Path
from typing import Literal

from ..expression_draft import (
    ExpressionDraft,
    ExpressionDraftCapabilities,
    required_authored_expression_fields,
)
from ..json_wire_repair import loads_one_json_object
from ..private_turn_state import PrivateTurnState
from ..recall_audit import CharacterRecallRequest
from .inbound_appraisal_wire import AppraisalDraftWire
from ..present_prompt import (
    SLIM_CONSIDER_KEYS,
    compact_gate_recall_instruction,
    compile_slim_interior_envelope,
    reply_only_bubble_clause,
    reply_only_completion_clause,
)


InboundToolPhase = Literal["gate", "initial", "after_recall", "final"]
InboundToolTransport = Literal["atomic", "stream"]
InboundToolSchemaDialect = Literal["standard", "deepseek-strict"]
_CONTRACT_VERSION = "1"
_COMPACT_GATE_CONTRACT_VERSION = "2"
_REPLY_ONLY_APPRAISAL_FIELDS = (
    "appraise",
    "affect",
    "brief_rationale",
    "behavior_tendency",
    "stance",
    "display_strategy",
    "confidence",
)
_REPLY_ONLY_HEAD_FIELDS = (
    "type",
    "private_turn_state",
    "timing_choice",
    "turn_posture",
    "cadence",
    "beat",
    "stance",
    "brief_rationale",
    "confidence",
    "response_expectation",
    "response_expectation_assessment",
    "revisit",
    "world_claims",
    "media_request",
    "media_source_refs",
)

# DeepSeek's strict tool dialect intentionally has a smaller JSON-Schema
# vocabulary than the canonical Pydantic wire.  The provider also requires
# every property of every object to be present; optional semantic fields are
# therefore transported as explicit ``null`` and retain their meaning in the
# existing canonical materializer.  This projection is kept here, rather than
# at call sites, so the standard provider path and the strict provider path
# cannot drift apart.
_LOG = logging.getLogger(__name__)
_REJECTED_CARRIER_EXCERPT_CHARS = 800
_DEEPSEEK_STRICT_UNSUPPORTED_KEYS = frozenset(
    {
        "default",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "title",
        "uniqueItems",
    }
)
_DEEPSEEK_STRICT_FORMATS = frozenset({"email", "hostname", "ipv4", "ipv6", "uuid"})


def _rewrite_object_type_array(schema: dict[str, object]) -> dict[str, object]:
    """Turn ``type: [..., "object", ...]`` into the anyOf shape DeepSeek accepts.

    DeepSeek strict tools accept a scalar ``type: "object"`` and
    ``anyOf: [{type: object, ...}, {type: null}]``. They reject a type array
    that includes ``object`` (``type: ["object", "null"]``), including after
    that array is wrapped by ``_nullable_strict_schema``.
    """

    type_value = schema.get("type")
    if not isinstance(type_value, list) or "object" not in type_value:
        return schema
    types: list[object] = []
    for item in type_value:
        if item not in types:
            types.append(item)
    object_schema = {key: value for key, value in schema.items() if key != "type"}
    object_schema["type"] = "object"
    variants: list[object] = []
    for item in types:
        if item == "object":
            variants.append(object_schema)
        else:
            variants.append({"type": item})
    if len(variants) == 1:
        first = variants[0]
        return first if isinstance(first, dict) else schema
    return {"anyOf": variants}


def _nullable_strict_schema(schema: object) -> object:
    if isinstance(schema, dict):
        schema = _rewrite_object_type_array(schema)
        if schema.get("type") == "null":
            return schema
        variants = schema.get("anyOf")
        if isinstance(variants, list) and any(
            isinstance(item, dict) and item.get("type") == "null" for item in variants
        ):
            return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _enum_json_type(value: object) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _deepseek_strict_schema(value: object) -> object:
    """Project one canonical schema into DeepSeek's strict tool dialect.

    The function is deliberately a pure schema adapter.  It never changes
    the canonical decoder or adds a semantic default; omitted optional values
    become explicit JSON nulls and are still validated by the host afterward.
    """

    if isinstance(value, list):
        return [_deepseek_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    value = _rewrite_object_type_array(value)
    projected: dict[str, object] = {}
    for key, item in value.items():
        if key in _DEEPSEEK_STRICT_UNSUPPORTED_KEYS:
            continue
        if key == "format" and item not in _DEEPSEEK_STRICT_FORMATS:
            continue
        if key == "const":
            projected["enum"] = [item]
            continue
        projected[key] = _deepseek_strict_schema(item)

    properties = projected.get("properties")
    if not isinstance(properties, dict):
        return projected

    original_required = set(projected.get("required", ()))
    properties = {key: _deepseek_strict_schema(item) for key, item in properties.items()}
    projected["properties"] = properties

    # Branches such as affect lifecycle, timing, and the outer recall/decision
    # union are object schemas without a full property inventory.  Expand each
    # branch to the same required-null envelope, preserving non-null fields
    # that the canonical object already required.
    branches = projected.get("anyOf")
    if isinstance(branches, list):
        projected_branches: list[object] = []
        for branch in branches:
            if not isinstance(branch, dict):
                projected_branches.append(branch)
                continue
            branch = dict(branch)
            branch.setdefault("type", "object")
            branch_properties = branch.get("properties")
            if not isinstance(branch_properties, dict):
                branch_properties = {}
            branch_properties = {
                key: _deepseek_strict_schema(item) for key, item in branch_properties.items()
            }
            for key, schema in properties.items():
                if key not in branch_properties:
                    branch_properties[key] = (
                        schema if key in original_required else _nullable_strict_schema(schema)
                    )
            branch["properties"] = branch_properties
            branch["required"] = list(properties)
            branch["additionalProperties"] = False
            projected_branches.append(branch)
        projected["anyOf"] = projected_branches

    projected["required"] = list(properties)
    projected["additionalProperties"] = False
    return projected


def _deepseek_documented_schema_subset(value: object) -> object:
    """Keep only the strict JSON-Schema subset documented by DeepSeek."""

    if isinstance(value, list):
        return [_deepseek_documented_schema_subset(item) for item in value]
    if not isinstance(value, dict):
        return value
    projected = {
        key: _deepseek_documented_schema_subset(item)
        for key, item in value.items()
        if key not in {"allOf", "not", "oneOf", "prefixItems"}
    }
    enum = projected.get("enum")
    if "type" not in projected and isinstance(enum, list) and enum:
        typed_values: dict[str, list[object]] = {}
        for item in enum:
            item_type = _enum_json_type(item)
            if item_type is None:
                break
            typed_values.setdefault(item_type, []).append(item)
        else:
            if len(typed_values) == 1:
                projected["type"] = next(iter(typed_values))
            else:
                projected.pop("enum", None)
                projected["anyOf"] = [
                    {"type": item_type, "enum": items} for item_type, items in typed_values.items()
                ]
    return projected


def deepseek_strict_tool_schema(value: object) -> object:
    """Project a background role tool into DeepSeek's documented strict subset.

    The interactive inbound contract retains its already qualified request
    identity. New background strict tools additionally remove unsupported
    composition keywords and type every ``anyOf`` branch before provider use.
    """

    return _deepseek_documented_schema_subset(_deepseek_strict_schema(value))


def _deepseek_strict_union_padding_is_empty(field: str, value: object) -> bool:
    """Recognize only content-free placeholders for an unselected branch.

    DeepSeek strict tools require every root property, but the provider may
    emit an empty value instead of JSON null for an unselected union sibling.
    Each value below is invalid for that sibling's own canonical branch, so
    dropping it cannot turn authored semantics into another branch choice.
    """

    if value is None:
        return True
    empty_padding: dict[str, object] = {
        "protocol": "",
        "appraisal_draft": {},
        "events": [],
        "full_turn_json": "",
        "expression_draft": {},
        "private_turn_state": {},
        "recall_request": {},
    }
    return field in empty_padding and value == empty_padding[field]


def _unique_compact_gate_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject duplicate carrier keys before branch authority is selected."""

    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("compact gate carrier has a duplicate field")
        value[key] = item
    return value


def _record_rejected_compact_gate_carrier(
    reason: str,
    value: dict[str, object],
) -> None:
    """Keep the refused carrier inspectable without inventing a second author."""

    extra_keys = sorted(set(value) - {"result_kind", "payload_json"})
    payload = value.get("payload_json")
    try:
        excerpt = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        excerpt = str(value)
    if len(excerpt) > _REJECTED_CARRIER_EXCERPT_CHARS:
        excerpt = excerpt[:_REJECTED_CARRIER_EXCERPT_CHARS]
    _LOG.warning(
        "compact gate carrier rejected reason=%s extra_keys=%s payload_json_type=%s excerpt=%s",
        reason,
        ",".join(extra_keys) or "-",
        type(payload).__name__,
        excerpt,
    )
    dump_path = getenv("WORLD_V2_COMPACT_GATE_REJECT_LOG")
    if dump_path:
        path = Path(dump_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"reason": reason, "carrier": value},
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )


_COMPACT_GATE_TRANSPORT_KEYS = frozenset({"result_kind", "payload_json"})
_COMPACT_GATE_ENVELOPE_KEYS = frozenset({"protocol", "appraisal_draft", "events"})
_COMPACT_GATE_FOREIGN_CAPABILITY_KEYS = frozenset(
    {
        "recall_request",
        "full_turn_json",
        "expression_draft",
        "media_request",
        "media_source_refs",
        "leading_typing_beat",
    }
)


def _compact_gate_non_padding_items(value: dict[str, object]) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    for key, item in value.items():
        if key == "result_kind":
            cleaned[key] = item
            continue
        if item is None:
            continue
        if _deepseek_strict_union_padding_is_empty(key, item):
            continue
        if key == "media_request" and item == "none":
            continue
        if key == "media_source_refs" and item == []:
            continue
        cleaned[key] = item
    return cleaned


def _loads_compact_gate_payload_object(payload_json: str) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("compact gate carrier payload has a duplicate field")
            result[key] = item
        return result

    def reject_constant(_value: str) -> object:
        raise ValueError("compact gate carrier payload has a non-JSON constant")

    try:
        payload = json.loads(
            payload_json,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError:
        payload = None
    else:
        if not isinstance(payload, dict):
            raise ValueError("compact gate carrier payload must be one JSON object")
        return payload
    try:
        payload = loads_one_json_object(
            payload_json,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            not_object_message="compact gate carrier payload must be one JSON object",
            invalid_message="compact gate carrier payload_json is invalid",
        )
    except ValueError as exc:
        detail = str(exc)
        if "duplicate field" in detail or "non-JSON constant" in detail:
            raise
        raise ValueError("compact gate carrier payload_json is invalid") from exc
    _LOG.info("compact gate carrier payload_json accepted after transport-shape repair")
    return payload


def _expand_compact_gate_payload(value: dict[str, object]) -> dict[str, object]:
    """Expand the compact provider carrier into the existing typed branches."""

    if "payload_json" not in value or (
        value.get("payload_json") is None and set(value) != {"result_kind", "payload_json"}
    ):
        return value
    cleaned = _compact_gate_non_padding_items(value)
    extras = set(cleaned) - _COMPACT_GATE_TRANSPORT_KEYS
    foreign = tuple(sorted(extras & _COMPACT_GATE_FOREIGN_CAPABILITY_KEYS))
    if foreign:
        _record_rejected_compact_gate_carrier(
            "compact gate carrier has cross-branch fields",
            value,
        )
        raise ValueError(
            "compact gate carrier has cross-branch fields keys=" + ",".join(foreign)
        )
    kind = cleaned.get("result_kind")
    payload_json = cleaned.get("payload_json")
    if kind not in {"reply_only", "full_turn", "recall"}:
        _record_rejected_compact_gate_carrier(
            "compact gate carrier result_kind is invalid",
            value,
        )
        raise ValueError("compact gate carrier result_kind is invalid")
    slim_extras = {
        key: cleaned[key] for key in extras if key in SLIM_CONSIDER_KEYS
    }
    envelope_extras = {
        key: cleaned[key] for key in extras if key in _COMPACT_GATE_ENVELOPE_KEYS
    }
    payload: object | None = None
    parse_error: ValueError | None = None
    if isinstance(payload_json, dict):
        payload = payload_json
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 131_072:
            raise ValueError("compact gate carrier payload_json exceeds its byte limit")
    elif isinstance(payload_json, str) and payload_json:
        try:
            payload_bytes = payload_json.encode("utf-8")
        except UnicodeEncodeError as exc:
            _record_rejected_compact_gate_carrier(
                "compact gate carrier payload_json is invalid",
                value,
            )
            raise ValueError("compact gate carrier payload_json is invalid") from exc
        if len(payload_bytes) > 131_072:
            raise ValueError("compact gate carrier payload_json exceeds its byte limit")
        try:
            payload = _loads_compact_gate_payload_object(payload_json)
        except ValueError as exc:
            if "duplicate field" in str(exc) or "non-JSON constant" in str(exc):
                raise
            parse_error = exc
    elif payload_json is not None:
        parse_error = ValueError("compact gate carrier payload_json is invalid")
    if payload is None:
        if set(envelope_extras) == _COMPACT_GATE_ENVELOPE_KEYS:
            payload = {
                key: envelope_extras[key] for key in ("protocol", "appraisal_draft", "events")
            }
        elif "messages" in slim_extras:
            payload = dict(slim_extras)
        else:
            _record_rejected_compact_gate_carrier(
                "compact gate carrier payload_json is invalid",
                value,
            )
            if parse_error is not None:
                raise ValueError("compact gate carrier payload_json is invalid") from parse_error
            raise ValueError("compact gate carrier payload_json is invalid")
    if not isinstance(payload, dict):
        raise ValueError("compact gate carrier payload must be one JSON object")
    payload = dict(payload)
    for key, item in slim_extras.items():
        payload.setdefault(key, item)

    node_count = 0

    def validate_bounds(item: object, *, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > 8_192 or depth > 32:
            raise ValueError("compact gate carrier payload exceeds structural bounds")
        if isinstance(item, dict):
            for child in item.values():
                validate_bounds(child, depth=depth + 1)
        elif isinstance(item, list):
            for child in item:
                validate_bounds(child, depth=depth + 1)

    validate_bounds(payload, depth=0)
    if "result_kind" in payload or "payload_json" in payload:
        raise ValueError("compact gate inner payload cannot own transport authority")
    slim_envelope = compile_slim_interior_envelope(payload, reply_only=(kind == "reply_only"))
    if slim_envelope is not None:
        payload = slim_envelope
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if kind in {"reply_only", "full_turn"}:
        if (
            set(payload) != {"protocol", "appraisal_draft", "events"}
            or payload.get("protocol") != "character-interior-events.1"
            or not isinstance(payload.get("appraisal_draft"), dict)
            or not isinstance(payload.get("events"), list)
        ):
            raise ValueError(
                f"compact gate {kind} carrier requires the exact event envelope "
                "keys=appraisal_draft,events,protocol "
                f"got={','.join(sorted(payload))}"
            )
    elif set(payload) != {"private_turn_state", "recall_request"}:
        raise ValueError("compact gate carrier requires the exact Recall envelope")
    if kind == "full_turn":
        if not isinstance(payload_json, str) or not payload_json:
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return {"result_kind": kind, "full_turn_json": payload_json}
    return {**payload, "result_kind": kind}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _inline_refs(schema: object, definitions: dict[str, object]) -> object:
    """Remove local Pydantic refs without copying field inventories by hand."""

    if isinstance(schema, list):
        return [_inline_refs(item, definitions) for item in schema]
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = definitions.get(ref.removeprefix("#/$defs/"))
        if target is None:
            raise ValueError(f"unresolved canonical schema ref: {ref}")
        return _inline_refs(target, definitions)
    return {
        key: _inline_refs(value, definitions)
        for key, value in schema.items()
        if key not in {"$defs", "title", "default"}
    }


def _provider_schema(model_type: object) -> dict[str, object]:
    model_json_schema = getattr(model_type, "model_json_schema")
    generated = model_json_schema(mode="validation")
    if not isinstance(generated, dict):
        raise TypeError("canonical wire schema must be an object")
    definitions = generated.get("$defs")
    if not isinstance(definitions, dict):
        definitions = {}
    converted = _inline_refs(generated, definitions)
    if not isinstance(converted, dict):
        raise TypeError("provider wire schema must be an object")
    return converted


def _non_null_schema(schema: object, *, field_name: str) -> dict[str, object]:
    """Return the canonical non-null branch of one optional model field."""

    if not isinstance(schema, dict):
        raise ValueError(f"{field_name} canonical schema is not an object")
    variants = schema.get("anyOf")
    if not isinstance(variants, list):
        if schema.get("type") == "null":
            raise ValueError(f"{field_name} canonical schema has no non-null branch")
        return deepcopy(schema)
    non_null = [
        deepcopy(item) for item in variants if isinstance(item, dict) and item.get("type") != "null"
    ]
    if len(non_null) != 1:
        raise ValueError(f"{field_name} canonical schema has an ambiguous non-null branch")
    return non_null[0]


def _reply_only_appraisal_schema() -> dict[str, object]:
    """Project canonical appraisal/affect without cross-turn social effects.

    Relationship movement reaches production through the slim object's
    about_us/why_us/us_deltas instead of these strict properties: a strict
    provider schema must mark every property required, so listing the
    relationship objects here would force a null envelope onto every pure-text
    reply and grow the contract area the compact gate exists to shrink.
    """

    canonical = _provider_schema(AppraisalDraftWire)
    canonical_properties = canonical.get("properties")
    if not isinstance(canonical_properties, dict):
        raise ValueError("AppraisalDraft canonical schema has no properties")
    forbidden = {
        "relationship_signal",
        "relationship_commitment",
        "interaction_act",
    }
    properties = {
        field: deepcopy(schema)
        for field, schema in canonical_properties.items()
        if field not in forbidden
    }
    # Keep the gate projection small without changing the canonical parser.
    # JSON integers satisfy the JSON-Schema number type used by meaning
    # confidence, affect component IDs are host-derived when omitted, and the
    # canonical parser below remains the fail-closed owner of the attribution
    # and affect-dimension vocabularies.
    meanings = properties.get("meanings")
    meaning_variants = meanings.get("anyOf") if isinstance(meanings, dict) else None
    meaning_array = (
        next(
            (
                item
                for item in meaning_variants
                if isinstance(item, dict) and item.get("type") == "array"
            ),
            None,
        )
        if isinstance(meaning_variants, list)
        else None
    )
    meaning_items = meaning_array.get("items") if isinstance(meaning_array, dict) else None
    meaning_properties = (
        meaning_items.get("properties") if isinstance(meaning_items, dict) else None
    )
    if isinstance(meaning_properties, dict):
        meaning_properties["confidence"] = {"type": "number"}
    attribution = properties.get("attribution")
    attribution_variants = attribution.get("anyOf") if isinstance(attribution, dict) else None
    if isinstance(attribution_variants, list):
        for item in attribution_variants:
            if isinstance(item, dict) and item.get("type") == "string":
                item.pop("enum", None)
    components = properties.get("components")
    component_variants = components.get("anyOf") if isinstance(components, dict) else None
    component_array = (
        next(
            (
                item
                for item in component_variants
                if isinstance(item, dict) and item.get("type") == "array"
            ),
            None,
        )
        if isinstance(component_variants, list)
        else None
    )
    component_items = component_array.get("items") if isinstance(component_array, dict) else None
    component_properties = (
        component_items.get("properties") if isinstance(component_items, dict) else None
    )
    component_required = (
        component_items.get("required") if isinstance(component_items, dict) else None
    )
    if isinstance(component_properties, dict):
        component_properties.pop("component_id", None)
        component_properties["dimension"] = {"type": "string"}
    if isinstance(component_required, list):
        component_items["required"] = [
            field for field in component_required if field != "component_id"
        ]
    return {
        "type": "object",
        "properties": properties,
        "required": list(_REPLY_ONLY_APPRAISAL_FIELDS),
        "additionalProperties": False,
    }


def _reply_only_stream_events_schema(
    *,
    response_expectation_assessment_required: bool,
) -> dict[str, object]:
    """Project one immediate text head plus an exact terminal frame.

    The role chooses this branch inside the same physical request as the full
    decision branch.  Its smaller output surface has no delayed, silent,
    reaction, sticker, typing, media-selection, multi-beat, or persistent
    relationship or interaction capability. Its compact carrier leaves the
    canonical appraisal and affect lifecycle, plus same-turn stance, behavior,
    display, rationale, and confidence to the role. The ordinary decision
    branch remains available whenever the character wants any richer effect.
    """

    expression_schema = _provider_schema(ExpressionDraft)
    expression_properties = expression_schema.get("properties")
    if not isinstance(expression_properties, dict):
        raise ValueError("ExpressionDraft canonical schema has no properties")

    def expression_field(name: str) -> dict[str, object]:
        schema = expression_properties.get(name)
        if not isinstance(schema, dict):
            raise ValueError(f"ExpressionDraft canonical schema has no {name}")
        return deepcopy(schema)

    private_turn_state = _non_null_schema(
        expression_field("private_turn_state"),
        field_name="private_turn_state",
    )
    assessment = expression_field("response_expectation_assessment")
    if response_expectation_assessment_required:
        assessment = _non_null_schema(
            assessment,
            field_name="response_expectation_assessment",
        )
    beats_schema = expression_properties.get("beats")
    beat_items = beats_schema.get("items") if isinstance(beats_schema, dict) else None
    beat_properties = beat_items.get("properties") if isinstance(beat_items, dict) else None
    beat_text = beat_properties.get("text") if isinstance(beat_properties, dict) else None

    media_source_refs = expression_field("media_source_refs")
    media_source_refs["maxItems"] = 0
    properties: dict[str, object] = {
        "type": {"type": "string", "enum": ["head"]},
        "private_turn_state": private_turn_state,
        "timing_choice": {"type": "string", "enum": ["now"]},
        "turn_posture": {
            "enum": [None, "continue", "interject"],
        },
        "cadence": expression_field("cadence"),
        # A dedicated beat projection keeps strict-mode callers from having to
        # send null reaction/sticker siblings that can never be selected here.
        "beat": {
            "type": "object",
            "properties": {
                "modality": {"type": "string", "enum": ["text"]},
                "text": _non_null_schema(
                    beat_text,
                    field_name="beat.text",
                ),
            },
            "required": ["modality", "text"],
            "additionalProperties": False,
        },
        "stance": expression_field("stance"),
        "brief_rationale": expression_field("brief_rationale"),
        "confidence": expression_field("confidence"),
        "response_expectation": expression_field("response_expectation"),
        "response_expectation_assessment": assessment,
        "revisit": expression_field("revisit"),
        "world_claims": expression_field("world_claims"),
        "media_request": {"type": "string", "enum": ["none"]},
        "media_source_refs": media_source_refs,
    }
    if tuple(properties) != _REPLY_ONLY_HEAD_FIELDS:
        raise AssertionError("reply-only head field inventory drifted")
    head = {
        "type": "object",
        "properties": properties,
        "required": list(_REPLY_ONLY_HEAD_FIELDS),
        "additionalProperties": False,
    }
    end = {
        "type": "object",
        "properties": {"type": {"type": "string", "enum": ["end"]}},
        "required": ["type"],
        "additionalProperties": False,
    }
    return {
        "type": "array",
        "minItems": 2,
        "maxItems": 2,
        "items": {"anyOf": [head, end]},
    }


def _capability_expression_schema(
    capabilities: ExpressionDraftCapabilities,
    *,
    require_turn_posture: bool,
) -> dict[str, object]:
    """Specialize the authoritative ExpressionDraft schema to deployment facts."""

    schema = _provider_schema(ExpressionDraft)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("ExpressionDraft canonical schema has no properties")
    beats = properties.get("beats")
    if not isinstance(beats, dict) or not isinstance(beats.get("items"), dict):
        raise ValueError("ExpressionDraft canonical schema has no beats items")
    beats["maxItems"] = capabilities.max_beats
    beat_properties = beats["items"].get("properties")
    if not isinstance(beat_properties, dict):
        raise ValueError("ExpressionDraft beat schema has no properties")
    modality = beat_properties.get("modality")
    if not isinstance(modality, dict):
        raise ValueError("ExpressionDraft beat schema has no modality")
    modality["enum"] = list(capabilities.modalities)
    media_request = properties.get("media_request")
    if not isinstance(media_request, dict):
        raise ValueError("ExpressionDraft canonical schema has no media_request")
    media_request["enum"] = (
        ["none", "consider_available_candidate"]
        if capabilities.media_request_mode == "candidate_only"
        else ["none"]
    )

    def constrain_option_ids(field: object, option_ids: list[str]) -> None:
        if not isinstance(field, dict) or not option_ids:
            return
        variants = field.get("anyOf")
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict) and variant.get("type") == "string":
                    variant["enum"] = option_ids
                    return
        if field.get("type") == "string":
            field["enum"] = option_ids

    constrain_option_ids(
        beat_properties.get("reaction_id"),
        [item.option_id for item in capabilities.reaction_options],
    )
    constrain_option_ids(
        beat_properties.get("sticker_id"),
        [item.option_id for item in capabilities.sticker_options],
    )
    later_beats = deepcopy(beats)
    later_beats["maxItems"] = capabilities.max_later_beats
    later_properties = later_beats["items"].get("properties")
    if not isinstance(later_properties, dict) or not isinstance(
        later_properties.get("modality"), dict
    ):
        raise ValueError("ExpressionDraft later beat schema has no modality")
    later_properties["modality"]["enum"] = ["text"]
    required = required_authored_expression_fields(
        capabilities=capabilities,
        require_turn_posture=require_turn_posture,
    )
    # Canonical replay may inherit the safe empty default, but a strict-tool
    # provider cannot omit object properties.  Requiring the live wire to say
    # ``world_claims=[]`` keeps each timing branch non-null and prevents a
    # schema-valid JSON null from consuming the role's one correction.
    required = required | {"world_claims"}
    if capabilities.private_turn_state_mode == "required":
        required = required | {"private_turn_state"}
    schema["required"] = sorted(required)
    # Provider JSON-schema support has a dependable ``anyOf`` subset.  Compile
    # every timing/posture invariant that the provider dialect can express so
    # an ordinary ``now + yield`` or ``later + interject`` choice is rejected
    # before it spends the one bounded host correction.  The canonical model
    # remains authoritative for relative comparisons such as expiry > delay.
    no_due_window = {
        "delay_seconds": {"type": "null"},
        "expires_after_seconds": {"type": "null"},
    }
    schema["anyOf"] = [
        {
            "properties": {
                "timing_choice": {"enum": ["now"]},
                "beats": beats,
                "turn_posture": {"enum": [None, "continue", "interject", "supersede"]},
                **no_due_window,
            }
        },
        {
            "properties": {
                "timing_choice": {"enum": ["later"]},
                "beats": later_beats,
                "turn_posture": {"enum": [None, "yield", "continue", "supersede"]},
                "revisit": {"type": "null"},
            }
        },
        {
            "properties": {
                "timing_choice": {"enum": ["silent"]},
                "beats": {**deepcopy(beats), "maxItems": 0},
                "turn_posture": {"enum": [None, "yield", "continue", "supersede"]},
                "response_expectation": {"type": "null"},
                "revisit": {"type": "null"},
                **no_due_window,
            }
        },
    ]
    return schema


@dataclass(frozen=True)
class InboundToolContractIdentity:
    contract_id: str
    phase: InboundToolPhase
    transport: InboundToolTransport
    schema_dialect: InboundToolSchemaDialect
    tool_name: str
    version: str
    schema_sha256: str
    capabilities_sha256: str
    contract_sha256: str

    def request_identity_material(self) -> dict[str, str]:
        """Local-only contract coordinates bound into the provider audit hash."""

        return {
            "contract_id": self.contract_id,
            "phase": self.phase,
            "transport": self.transport,
            "schema_dialect": self.schema_dialect,
            "tool_name": self.tool_name,
            "version": self.version,
            "schema_sha256": self.schema_sha256,
            "capabilities_sha256": self.capabilities_sha256,
            "contract_sha256": self.contract_sha256,
        }


@dataclass(frozen=True)
class InboundToolContract:
    """One provider-standard function and lossless decoder for one phase."""

    phase: InboundToolPhase
    transport: InboundToolTransport
    capabilities: ExpressionDraftCapabilities
    recall_allowed: bool
    require_turn_posture: bool
    provider_tools: tuple[dict[str, object], ...]
    provider_tool_choice: dict[str, object]
    identity: InboundToolContractIdentity

    def unwrap(self, raw_arguments: str) -> str:
        """Validate and remove only the exact forced-tool transport wrapper."""

        try:
            value = json.loads(raw_arguments)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("forced transport must be one JSON object") from exc
        if not isinstance(value, dict):
            raise ValueError("forced transport must be one JSON object")
        kind = value.get("result_kind")
        if self.identity.schema_dialect == "deepseek-strict":
            # DeepSeek strict mode requires every property of the outer
            # object to be present.  The branch that was not selected is
            # represented by explicit nulls; remove only those transport-null
            # siblings before applying the ordinary exact-envelope rules.
            parameters = self.provider_tools[0]["function"].get("parameters")
            properties = parameters.get("properties") if isinstance(parameters, dict) else None
            if not isinstance(properties, dict) or set(value) != set(properties):
                raise ValueError("DeepSeek strict transport envelope is incomplete")
            value = {
                key: item
                for key, item in value.items()
                if key == "result_kind" or not _deepseek_strict_union_padding_is_empty(key, item)
            }
        if kind in {"decision", "reply_only"}:
            if kind == "reply_only" and self.transport != "stream":
                raise ValueError("forced reply-only transport is unavailable")
            expected = (
                {"result_kind", "appraisal_draft", "expression_draft"}
                if self.transport == "atomic"
                else {"result_kind", "protocol", "appraisal_draft", "events"}
            )
            if set(value) != expected:
                raise ValueError("forced character transport envelope is ambiguous")
        elif kind == "recall":
            if not self.recall_allowed:
                raise ValueError("forced recall transport is unavailable")
            allowed = {
                frozenset({"result_kind", "recall_request"}),
                frozenset({"result_kind", "private_turn_state", "recall_request"}),
            }
            if self.capabilities.private_turn_state_mode == "required":
                allowed = {frozenset({"result_kind", "private_turn_state", "recall_request"})}
            if frozenset(value) not in allowed:
                raise ValueError("forced recall transport envelope is ambiguous")
        else:
            raise ValueError("forced transport result_kind is missing or invalid")
        return json.dumps(
            {key: item for key, item in value.items() if key != "result_kind"},
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class InboundGateToolContract:
    """Compact role-owned choice between reply, full capability, and Recall."""

    capabilities: ExpressionDraftCapabilities
    recall_allowed: bool
    provider_tools: tuple[dict[str, object], ...]
    provider_tool_choice: dict[str, object]
    identity: InboundToolContractIdentity

    def decode(self, raw_arguments: str) -> dict[str, object]:
        """Validate and normalize one exact compact-gate tool result."""

        try:
            value = json.loads(
                raw_arguments,
                object_pairs_hook=_unique_compact_gate_object,
            )
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("compact gate transport must be one JSON object") from exc
        if not isinstance(value, dict):
            raise ValueError("compact gate transport must be one JSON object")
        value = _expand_compact_gate_payload(value)
        kind = value.get("result_kind")
        expected = {
            "reply_only": {
                "result_kind",
                "protocol",
                "appraisal_draft",
                "events",
            },
            "full_turn": {"result_kind", "full_turn_json"},
            "recall": {
                "result_kind",
                "private_turn_state",
                "recall_request",
            },
        }.get(str(kind))
        if expected is None or (kind == "recall" and not self.recall_allowed):
            raise ValueError("compact gate result_kind is unavailable")
        if set(value) != expected:
            raise ValueError("compact gate transport envelope is ambiguous")
        return value


class InboundToolContracts:
    """Deep module: all inbound forced-tool schema/version knowledge in one seam."""

    def compact_gate_for(
        self,
        *,
        capabilities: ExpressionDraftCapabilities,
        recall_allowed: bool,
        response_expectation_assessment_required: bool = False,
        schema_dialect: InboundToolSchemaDialect = "standard",
    ) -> InboundGateToolContract:
        """Return the small initial gate without embedding the full decision schema."""

        if schema_dialect not in {"standard", "deepseek-strict"}:
            raise ValueError("unsupported inbound tool schema dialect")
        if "text" not in capabilities.modalities or capabilities.max_beats < 1:
            raise ValueError("compact inbound gate requires one available text beat")

        result_kinds = ["reply_only", "full_turn", "recall"]
        parameters: dict[str, object] = {
            "type": "object",
            "properties": {
                "result_kind": {"type": "string", "enum": result_kinds},
                # Every branch uses the same required carrier. DeepSeek
                # otherwise emits empty placeholders for an unselected strict
                # union sibling. The host parses and validates this complete
                # inner object before any Action can be authorized.
                "payload_json": {"type": "string"},
            },
            "required": ["result_kind", "payload_json"],
            "additionalProperties": False,
        }

        tool_name = f"character_inbound_compact_gate_v{_COMPACT_GATE_CONTRACT_VERSION}"
        function: dict[str, object] = {
            "name": tool_name,
            "description": (
                "Choose the minimum sufficient branch that losslessly represents the external "
                "effect you choose. "
                + reply_only_completion_clause()
                + ". "
                + reply_only_bubble_clause()
                + " It supports a canonical "
                "appraisal and affect lifecycle: "
                "brief_rationale, behavior_tendency, stance, display_strategy, and confidence; "
                "appraise and affect are your choices. On the slim object, optional mood opens a "
                "lasting Affect component and optional about_us/why_us/us_deltas record how the "
                "relationship itself moved, both without leaving reply_only. It excludes "
                "interaction protocol updates, media, typing/reaction, turn supersession, "
                "continuation, and more text beats than the installed beat limit. "
                "If appraisal or affect is incomplete, keep a legal now, later, or silent "
                "head; the host records affect no_change only for that broken appraisal rather "
                "than inventing later or discarding silence. "
                "Choose full_turn only when the external effect you choose actually requires a "
                "capability reply_only excludes. Put the complete chosen branch object as a "
                "JSON string in payload_json: the slim object "
                "for reply_only, the full character-interior-events.1 envelope for full_turn, "
                "or private_turn_state plus recall_request for recall. "
                + compact_gate_recall_instruction()
                + "The host does not classify by topic, length, complexity, or keywords and "
                "does not choose the branch."
            ),
            "parameters": parameters,
        }
        if schema_dialect == "deepseek-strict":
            function["strict"] = True
        provider_tools = ({"type": "function", "function": function},)
        schema_digest = "sha256:" + sha256(_canonical_json(parameters).encode("utf-8")).hexdigest()
        capabilities_digest = (
            "sha256:"
            + sha256(
                _canonical_json(capabilities.model_dump(mode="json")).encode("utf-8")
            ).hexdigest()
        )
        contract_digest = (
            "sha256:"
            + sha256(
                _canonical_json(
                    {
                        "phase": "gate",
                        "transport": "stream",
                        "schema_dialect": schema_dialect,
                        "recall_allowed": recall_allowed,
                        "response_expectation_assessment_required": (
                            response_expectation_assessment_required
                        ),
                        "schema_sha256": schema_digest,
                        "capabilities_sha256": capabilities_digest,
                        "tool_name": tool_name,
                    }
                ).encode("utf-8")
            ).hexdigest()
        )
        identity = InboundToolContractIdentity(
            contract_id="character-inbound-compact-gate",
            phase="gate",
            transport="stream",
            schema_dialect=schema_dialect,
            tool_name=tool_name,
            version=_COMPACT_GATE_CONTRACT_VERSION,
            schema_sha256=schema_digest,
            capabilities_sha256=capabilities_digest,
            contract_sha256=contract_digest,
        )
        return InboundGateToolContract(
            capabilities=capabilities,
            recall_allowed=recall_allowed,
            provider_tools=provider_tools,
            provider_tool_choice={
                "type": "function",
                "function": {"name": tool_name},
            },
            identity=identity,
        )

    def contract_for(
        self,
        *,
        phase: InboundToolPhase,
        transport: InboundToolTransport = "atomic",
        capabilities: ExpressionDraftCapabilities,
        recall_allowed: bool,
        require_turn_posture: bool = False,
        response_expectation_assessment_required: bool = False,
        schema_dialect: InboundToolSchemaDialect = "standard",
    ) -> InboundToolContract:
        if phase not in {"initial", "after_recall", "final"}:
            raise ValueError("unsupported inbound tool phase")
        if transport not in {"atomic", "stream"}:
            raise ValueError("unsupported inbound tool transport")
        if schema_dialect not in {"standard", "deepseek-strict"}:
            raise ValueError("unsupported inbound tool schema dialect")
        recall_allowed = phase == "initial" and recall_allowed
        schema_includes_recall = phase == "initial"
        tool_name = (
            f"character_inbound_{phase}_v{_CONTRACT_VERSION}"
            if transport == "atomic" and phase in {"initial", "after_recall"}
            else f"character_inbound_{phase}_{transport}_v{_CONTRACT_VERSION}"
        )
        appraisal_schema = _provider_schema(AppraisalDraftWire)
        appraisal_required = appraisal_schema.get("required")
        if not isinstance(appraisal_required, list):
            raise ValueError("AppraisalDraft canonical schema has no required fields")
        appraisal_schema["required"] = sorted({*appraisal_required, "affect"})
        appraisal_schema["anyOf"] = AppraisalDraftWire.provider_lifecycle_branches()
        expression_schema = _capability_expression_schema(
            capabilities,
            require_turn_posture=require_turn_posture,
        )
        decision_properties: dict[str, object] = {
            "result_kind": {"type": "string", "enum": ["decision"]},
            "appraisal_draft": appraisal_schema,
        }
        decision_required = ["result_kind", "appraisal_draft", "expression_draft"]
        if transport == "atomic":
            decision_properties["expression_draft"] = expression_schema
        else:
            expression_properties = expression_schema.get("properties")
            expression_required = expression_schema.get("required")
            if not isinstance(expression_properties, dict) or not isinstance(
                expression_required, list
            ):
                raise ValueError("ExpressionDraft stream schema is incomplete")
            beat_array = expression_properties.get("beats")
            if not isinstance(beat_array, dict) or not isinstance(beat_array.get("items"), dict):
                raise ValueError("ExpressionDraft stream beat schema is incomplete")
            deferred_beats = deepcopy(beat_array)
            deferred_beats["maxItems"] = capabilities.max_later_beats
            deferred_modality = deferred_beats["items"].get("properties", {}).get("modality")
            if not isinstance(deferred_modality, dict):
                raise ValueError("ExpressionDraft deferred beat modality is incomplete")
            deferred_modality["enum"] = ["text"]
            head_properties = {
                key: deepcopy(value)
                for key, value in expression_properties.items()
                if key not in {"beats", "episode_disposition"}
            }
            head_properties.update(
                {
                    "type": {"type": "string", "enum": ["head"]},
                    "beat": deepcopy(beat_array["items"]),
                    "beats": deferred_beats,
                    "leading_typing_beat": deepcopy(beat_array["items"]),
                }
            )
            beat_modality = head_properties["beat"].get("properties", {}).get("modality")
            if isinstance(beat_modality, dict):
                beat_modality["enum"] = [
                    modality for modality in capabilities.modalities if modality != "typing"
                ]
            typing_modality = (
                head_properties["leading_typing_beat"].get("properties", {}).get("modality")
            )
            if isinstance(typing_modality, dict):
                typing_modality["enum"] = ["typing"]
            head_required = [
                "type",
                *[
                    field
                    for field in expression_required
                    if field not in {"beats", "episode_disposition"}
                ],
            ]
            # The stream head has three mutually exclusive beat transports:
            # an immediate visible beat (optionally preceded by typing), a
            # deferred beat array, or no beat for silence.  Required-tool
            # providers require every property to be present, so merely
            # making the sibling fields nullable is not enough: models may
            # otherwise emit an empty array/object for both transports.  Put
            # the transport exclusion directly in each timing branch.  This
            # mirrors ``_expression_event_head`` and prevents a provider from
            # returning a schema-valid but locally ambiguous head.
            null_transport = {"type": "null"}
            now_head_branch = {
                "properties": {
                    "timing_choice": {"enum": ["now"]},
                    "turn_posture": {"enum": [None, "continue", "interject", "supersede"]},
                    "beat": deepcopy(beat_array["items"]),
                    "beats": null_transport,
                },
                "required": ["beat"],
            }
            later_head_branch = {
                "properties": {
                    "timing_choice": {"enum": ["later"]},
                    "turn_posture": {"enum": [None, "yield", "continue", "supersede"]},
                    "beat": null_transport,
                    "beats": deferred_beats,
                    "leading_typing_beat": null_transport,
                },
                "required": ["beats"],
            }
            silent_head_branch = {
                "properties": {
                    "timing_choice": {"enum": ["silent"]},
                    "turn_posture": {"enum": [None, "yield", "continue", "supersede"]},
                    "beat": null_transport,
                    "beats": null_transport,
                    "leading_typing_beat": null_transport,
                }
            }
            continuation = {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["beat"]},
                    "beat": deepcopy(beat_array["items"]),
                    "world_claims": deepcopy(expression_properties["world_claims"]),
                },
                "required": ["type", "beat", "world_claims"],
                "additionalProperties": False,
            }
            end = {
                "type": "object",
                "properties": {"type": {"type": "string", "enum": ["end"]}},
                "required": ["type"],
                "additionalProperties": False,
            }
            decision_properties.update(
                {
                    "protocol": {
                        "type": "string",
                        "enum": ["character-interior-events.1"],
                    },
                    "events": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": capabilities.max_beats + 2,
                        "items": {
                            "anyOf": [
                                {
                                    "type": "object",
                                    "properties": head_properties,
                                    "required": head_required,
                                    "additionalProperties": False,
                                    "anyOf": [
                                        now_head_branch,
                                        later_head_branch,
                                        silent_head_branch,
                                    ],
                                },
                                continuation,
                                end,
                            ]
                        },
                    },
                }
            )
            decision_required = [
                "result_kind",
                "protocol",
                "appraisal_draft",
                "events",
            ]
        decision_branch: dict[str, object] = {
            "type": "object",
            "properties": decision_properties,
            "required": decision_required,
            "additionalProperties": False,
        }
        branches: list[dict[str, object]] = [decision_branch]
        if transport == "stream":
            if "text" not in capabilities.modalities or capabilities.max_beats < 1:
                raise ValueError("reply-only stream requires one available text beat")
            branches.append(
                {
                    "type": "object",
                    "properties": {
                        "result_kind": {
                            "type": "string",
                            "enum": ["reply_only"],
                        },
                        "protocol": {
                            "type": "string",
                            "enum": ["character-interior-events.1"],
                        },
                        "appraisal_draft": _reply_only_appraisal_schema(),
                        "events": _reply_only_stream_events_schema(
                            response_expectation_assessment_required=(
                                response_expectation_assessment_required
                            )
                        ),
                    },
                    "required": [
                        "result_kind",
                        "protocol",
                        "appraisal_draft",
                        "events",
                    ],
                    "additionalProperties": False,
                }
            )
        if schema_includes_recall:
            recall_required = ["result_kind", "recall_request"]
            if capabilities.private_turn_state_mode == "required":
                recall_required.append("private_turn_state")
            branches.append(
                {
                    "type": "object",
                    "properties": {
                        "result_kind": {"type": "string", "enum": ["recall"]},
                        "recall_request": _provider_schema(CharacterRecallRequest),
                        "private_turn_state": _provider_schema(PrivateTurnState),
                    },
                    "required": recall_required,
                    "additionalProperties": False,
                }
            )
        # Make the union's branch-exclusive fields explicit.  The ordinary
        # dialect may omit these siblings, while DeepSeek strict requires
        # every property to be present; using ``type: null`` here makes the
        # decision branch unable to fill recall fields (and vice versa)
        # instead of relying on a post-hoc host rejection.
        all_branch_properties: set[str] = set()
        for branch in branches:
            properties = branch.get("properties")
            if not isinstance(properties, dict):
                raise ValueError("inbound tool branch has no object properties")
            all_branch_properties.update(properties)
        for branch in branches:
            properties = branch["properties"]
            assert isinstance(properties, dict)
            for property_name in all_branch_properties - set(properties):
                properties[property_name] = {"type": "null"}
        # DeepSeek's function-calling dialect requires every function's root
        # parameters schema to declare ``type: object``.  Keep the semantic
        # decision/recall union below that provider-compatible root; the root
        # properties are only the lossless union of branch properties, while
        # the branch schemas and local ``unwrap`` remain authoritative for
        # exact result-kind validation.
        root_properties: dict[str, object] = {}
        for branch in branches:
            branch_properties = branch.get("properties")
            if not isinstance(branch_properties, dict):
                raise ValueError("inbound tool branch has no object properties")
            for property_name, property_schema in branch_properties.items():
                if property_name != "result_kind":
                    current = root_properties.get(property_name)
                    if current is None:
                        root_properties[property_name] = deepcopy(property_schema)
                        continue
                    if not isinstance(current, dict) or not isinstance(property_schema, dict):
                        raise ValueError("inbound root property schema is not an object")
                    current_is_null = current.get("type") == "null"
                    branch_is_null = property_schema.get("type") == "null"
                    if current_is_null and not branch_is_null:
                        root_properties[property_name] = {
                            "anyOf": [deepcopy(property_schema), {"type": "null"}]
                        }
                    elif not current_is_null and branch_is_null:
                        root_properties[property_name] = {
                            "anyOf": [deepcopy(current), {"type": "null"}]
                        }
                    elif current != property_schema:
                        root_properties[property_name] = {
                            "anyOf": [deepcopy(current), deepcopy(property_schema)]
                        }
                    continue
                # ``result_kind`` is shared by both branches.  The provider
                # facing envelope must admit every branch discriminator; the
                # branch-level ``anyOf`` schemas still enforce the exact
                # discriminator/field pairing and ``unwrap`` remains the
                # semantic authority after transport decoding.
                current = root_properties.get(property_name)
                if current is None:
                    root_properties[property_name] = deepcopy(property_schema)
                    continue
                if not isinstance(current, dict) or not isinstance(property_schema, dict):
                    raise ValueError("inbound result_kind schema is not an object")
                current_enum = current.get("enum")
                branch_enum = property_schema.get("enum")
                if not isinstance(current_enum, list) or not isinstance(branch_enum, list):
                    raise ValueError("inbound result_kind schema has no enum")
                current["enum"] = list(dict.fromkeys([*current_enum, *branch_enum]))
        parameters = {
            "type": "object",
            "properties": root_properties,
            "required": ["result_kind"],
            "additionalProperties": False,
            "anyOf": branches,
        }
        if schema_dialect == "deepseek-strict":
            strict_parameters = deepseek_strict_tool_schema(parameters)
            if not isinstance(strict_parameters, dict):
                raise ValueError("DeepSeek strict tool parameters must be an object")
            parameters = strict_parameters
        function = {
            "name": tool_name,
            "description": (
                "Return exactly one character-owned inbound result in this one call. "
                + (
                    "Choose the minimum sufficient branch that losslessly represents the external "
                    "effect you choose. result_kind="
                    + reply_only_completion_clause()
                    + ". "
                    + reply_only_bubble_clause()
                    + " It permits "
                    "the canonical appraisal and affect lifecycle, but not a relationship or "
                    "interaction update, media, typing, reaction, or additional "
                    "beat or stream continuation. Its compact appraisal carrier lets you choose "
                    "same-turn brief_rationale, behavior_tendency, stance, display_strategy, and "
                    "confidence; appraise and affect remain your choices. Choose "
                    "result_kind=decision only when the external effect you choose actually "
                    "requires a capability reply_only does not expose. "
                    + compact_gate_recall_instruction()
                    if transport == "stream"
                    else (
                        compact_gate_recall_instruction()
                        + "Otherwise choose result_kind=decision with complete appraisal_draft "
                        "and expression_draft. "
                    )
                )
                + " The host does not classify by topic, length, complexity, or keywords and "
                "does not choose the branch."
                + " Deployment capability profile="
                + capabilities.profile_id
                + f"; max_beats={capabilities.max_beats}; "
                + f"max_later_beats={capabilities.max_later_beats}."
                + " For timing_choice=now return at least one visible beat and never choose "
                "turn_posture=yield. For timing_choice=later return at least one text beat "
                "and never choose interject. For timing_choice=silent return no beats and "
                "never choose interject."
            ),
            "parameters": parameters,
        }
        if schema_dialect == "deepseek-strict":
            function["strict"] = True
        provider_tools = ({"type": "function", "function": function},)
        digest = "sha256:" + sha256(_canonical_json(parameters).encode("utf-8")).hexdigest()
        capabilities_digest = (
            "sha256:"
            + sha256(
                _canonical_json(capabilities.model_dump(mode="json")).encode("utf-8")
            ).hexdigest()
        )
        contract_digest = (
            "sha256:"
            + sha256(
                _canonical_json(
                    {
                        "phase": phase,
                        "transport": transport,
                        "schema_dialect": schema_dialect,
                        "recall_allowed": recall_allowed,
                        "require_turn_posture": require_turn_posture,
                        "response_expectation_assessment_required": (
                            response_expectation_assessment_required
                        ),
                        "schema_sha256": digest,
                        "capabilities_sha256": capabilities_digest,
                        "tool_name": tool_name,
                    }
                ).encode("utf-8")
            ).hexdigest()
        )
        identity = InboundToolContractIdentity(
            contract_id="character-inbound-forced-tool",
            phase=phase,
            transport=transport,
            schema_dialect=schema_dialect,
            tool_name=tool_name,
            version=_CONTRACT_VERSION,
            schema_sha256=digest,
            capabilities_sha256=capabilities_digest,
            contract_sha256=contract_digest,
        )
        return InboundToolContract(
            phase=phase,
            transport=transport,
            capabilities=capabilities,
            recall_allowed=recall_allowed,
            require_turn_posture=require_turn_posture,
            provider_tools=provider_tools,
            provider_tool_choice={"type": "function", "function": {"name": tool_name}},
            identity=identity,
        )


__all__ = [
    "InboundGateToolContract",
    "InboundToolContract",
    "InboundToolContractIdentity",
    "InboundToolContracts",
    "InboundToolSchemaDialect",
    "InboundToolPhase",
    "InboundToolTransport",
]
