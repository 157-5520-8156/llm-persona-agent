"""Compact whole-Beat verdict protocol for correlated factual source review.

The host supplies exact, indexed visible Beats and a pinned source table. The
reviewer returns one verdict per whole Beat. Version 2 also locates a rejected
span for diagnostic feedback; it never authors replacement dialogue. Readable,
eligible sources do not establish exhaustive semantic classification.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .context_capsule import ResolvedSourceBinding, source_bindings_hash
from .visible_life_source import settled_life_source_support
from .world_life_context import ActiveActivityContextItem, CompletedActivityContextItem, PlannedActivityContextItem


VISIBLE_SOURCE_CLOSURE_CONTRACT = "visible-beat-source-verdict.1"
VISIBLE_SOURCE_VERDICT_V2_CONTRACT = "visible-beat-source-verdict.2"
VISIBLE_SOURCE_VERDICT_V3_CONTRACT = "visible-beat-source-verdict.3"
VISIBLE_SOURCE_VERDICT_V4_CONTRACT = "visible-beat-source-verdict.4"
VISIBLE_SOURCE_VERDICT_V5_CONTRACT = "visible-beat-source-verdict.5"
VISIBLE_SOURCE_VERDICT_V6_CONTRACT = "visible-beat-source-verdict.6"
VISIBLE_SOURCE_VERDICT_V7_CONTRACT = "visible-beat-source-verdict.7"
VISIBLE_SOURCE_VERDICT_V8_CONTRACT = "visible-beat-source-verdict.8"
MAX_VISIBLE_SOURCE_PROBLEM_CHARS = 64
MAX_VISIBLE_SOURCE_PROBLEM_JSON_CHARS = 96
MAX_VISIBLE_SOURCE_VERDICT_V2_BYTES = 32_768

VisibleSemanticRole = Literal[
    "immediate_private_state",
    "source_bearing_private_episode",
    "embedded_external_proposition",
    "standalone_external_proposition",
    "world_unbound_generalization",
    "nonassertive_content",
]
VisibleClosureDecision = Literal["source_free", "closed", "unclosed"]
VisibleSubjectRole = Literal["companion", "counterpart", "general", "other", "none"]
VisibleSourceRelation = Literal[
    "unclosed",
    "not_external_proposition",
    "exact_current_report_discourse_coverage",
    "exact_dialogue_record_coverage",
    "first_person_immediate_private_continuity",
    "declared_world_claim_source_coverage",
    "pinned_context_authority_coverage",
]
VisibleSourceClosureWireFailureCode = Literal[
    "schema_invalid",
    "beat_coverage_invalid",
    "ref_set_invalid",
    "verdict_role_invalid",
    "verdict_ref_invalid",
    "subject_binding_invalid",
    "diagnostic_coverage_invalid",
    "diagnostic_locator_invalid",
    "diagnostic_ref_invalid",
]

_ProviderSemanticRole = Literal[
    "private_state",
    "commitment",
    "external_proposition",
    "generalization",
    "question",
    "mixed",
]
_ProviderSubjectRole = Literal[
    "companion",
    "counterpart",
    "general",
    "none",
    "mixed",
]


class VisibleSourceClosureLocator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    beat_index: int
    char_start: int
    char_end: int
    text: str


class _ProviderVisibleBeatVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    beat_index: int
    verdict: VisibleClosureDecision
    semantic_role: _ProviderSemanticRole
    subject_role: _ProviderSubjectRole
    source_ref_indexes: tuple[int, ...]


class _ProviderVisibleBeatVerdictWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["visible-beat-source-verdict.1"]
    decisions: tuple[_ProviderVisibleBeatVerdict, ...]


class VisibleSourceClosureSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    locator: VisibleSourceClosureLocator
    semantic_role: VisibleSemanticRole
    subject_role: VisibleSubjectRole
    decision: VisibleClosureDecision
    source_relation: VisibleSourceRelation
    source_ref_indexes: tuple[int, ...] = ()


class VisibleSourceClosureWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["visible-beat-source-verdict.1"]
    segments: tuple[VisibleSourceClosureSegment, ...]


class VisibleSourceRejectionDiagnostic(BaseModel):
    """Reviewer explanation only; these references confer no source authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    beat_index: int = Field(ge=0, le=15)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=1)
    related_source_ref_indexes: tuple[int, ...] = Field(max_length=8)
    source_problem: str = Field(min_length=1, max_length=MAX_VISIBLE_SOURCE_PROBLEM_CHARS)

    @field_validator("source_problem")
    @classmethod
    def problem_is_bounded(cls, value: str) -> str:
        if (
            not value.strip()
            or len(json.dumps(value, ensure_ascii=False)) > MAX_VISIBLE_SOURCE_PROBLEM_JSON_CHARS
        ):
            raise ValueError("source problem must be nonblank and JSON-bounded")
        return value


class ParsedVisibleSourceVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: VisibleSourceClosureWire
    rejections: tuple[VisibleSourceRejectionDiagnostic, ...]


class _ProviderVisibleBeatVerdictV2(_ProviderVisibleBeatVerdict):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _ProviderVisibleBeatVerdictWireV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["visible-beat-source-verdict.2"]
    decisions: tuple[_ProviderVisibleBeatVerdictV2, ...] = Field(max_length=16)
    rejections: tuple[VisibleSourceRejectionDiagnostic, ...] = Field(max_length=16)


class _ProviderVisibleBeatBranchV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    beat_index: int
    subject_role: _ProviderSubjectRole


class _ProviderVisibleBeatClosedV3(_ProviderVisibleBeatBranchV3):
    verdict: Literal["closed"]
    semantic_role: Literal["external_proposition", "mixed"]
    first_source_ref_index: int
    additional_source_ref_indexes: tuple[int, ...]


class _ProviderVisibleBeatSourceFreeV3(_ProviderVisibleBeatBranchV3):
    verdict: Literal["source_free"]
    semantic_role: Literal["private_state", "commitment", "generalization", "question"]


class _ProviderVisibleBeatUnclosedV3(_ProviderVisibleBeatBranchV3):
    verdict: Literal["unclosed"]
    semantic_role: Literal["external_proposition", "mixed"]


class _ProviderVisibleBeatVerdictWireV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["visible-beat-source-verdict.3"]
    decisions: tuple[
        _ProviderVisibleBeatClosedV3 | _ProviderVisibleBeatSourceFreeV3 | _ProviderVisibleBeatUnclosedV3,
        ...,
    ] = Field(max_length=16)
    rejections: tuple[VisibleSourceRejectionDiagnostic, ...] = Field(max_length=16)


class _ProviderVisibleBeatVerdictWireV4(_ProviderVisibleBeatVerdictWireV3):
    contract: Literal["visible-beat-source-verdict.4"]


class _ProviderVisibleBeatVerdictWireV5(_ProviderVisibleBeatVerdictWireV3):
    contract: Literal["visible-beat-source-verdict.5"]


_SOURCE_PROBLEM_CODES = (
    "support_missing", "subject_mismatch", "time_mismatch", "status_mismatch",
    "polarity_mismatch", "disclosure_not_authorized", "support_not_eligible",
    "other_source_problem",
)


class _ProviderRejectionV6(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    beat_index: int
    related_source_ref_indexes: tuple[int, ...]
    source_problem: Literal[
        "support_missing", "subject_mismatch", "time_mismatch", "status_mismatch",
        "polarity_mismatch", "disclosure_not_authorized", "support_not_eligible",
        "other_source_problem",
    ]


class _ProviderVisibleBeatVerdictWireV6(_ProviderVisibleBeatVerdictWireV3):
    contract: Literal["visible-beat-source-verdict.6"]
    rejections: tuple[_ProviderRejectionV6, ...] = Field(max_length=16)


class VisibleSourceClosureWireFailure(ValueError):
    """Content-free structural coordinate for one invalid reviewer wire."""

    def __init__(
        self,
        code: VisibleSourceClosureWireFailureCode,
        message: str,
        *,
        beat_index: int | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.beat_index = beat_index
        self.field = field

    def correction_coordinate(self) -> dict[str, object]:
        coordinate: dict[str, object] = {"code": self.code}
        if self.beat_index is not None:
            coordinate["beat_index"] = self.beat_index
        if self.field is not None:
            coordinate["field"] = self.field
        return coordinate


# DeepSeek strict tools support a deliberate JSON-Schema subset.  Keep this
# provider schema hand-authored and immutable: no Pydantic titles,
# minLength/maxLength/maxItems, or dialect-specific root unions.
_VISIBLE_BEAT_VERDICT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "contract": {
            "type": "string",
            "enum": [VISIBLE_SOURCE_CLOSURE_CONTRACT],
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "beat_index": {"type": "integer", "minimum": 0, "maximum": 15},
                    "verdict": {
                        "type": "string",
                        "enum": ["source_free", "closed", "unclosed"],
                    },
                    "semantic_role": {
                        "type": "string",
                        "enum": [
                            "private_state",
                            "commitment",
                            "external_proposition",
                            "generalization",
                            "question",
                            "mixed",
                        ],
                    },
                    "subject_role": {
                        "type": "string",
                        "enum": [
                            "companion",
                            "counterpart",
                            "general",
                            "none",
                            "mixed",
                        ],
                    },
                    "source_ref_indexes": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": [
                    "beat_index",
                    "verdict",
                    "semantic_role",
                    "subject_role",
                    "source_ref_indexes",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["contract", "decisions"],
    "additionalProperties": False,
}


class _ProviderVisibleBeatVerdictWireV7(_ProviderVisibleBeatVerdictWireV6):
    contract: Literal["visible-beat-source-verdict.7"]


class _ProviderVisibleBeatVerdictWireV8(_ProviderVisibleBeatVerdictWireV6):
    contract: Literal["visible-beat-source-verdict.8"]


def _versioned_contract(version: Literal["1", "2", "3", "4", "5", "6", "7", "8"]) -> str:
    if version == "1":
        return VISIBLE_SOURCE_CLOSURE_CONTRACT
    if version == "2":
        return VISIBLE_SOURCE_VERDICT_V2_CONTRACT
    if version == "3":
        return VISIBLE_SOURCE_VERDICT_V3_CONTRACT
    if version == "4":
        return VISIBLE_SOURCE_VERDICT_V4_CONTRACT
    if version == "5":
        return VISIBLE_SOURCE_VERDICT_V5_CONTRACT
    if version == "6":
        return VISIBLE_SOURCE_VERDICT_V6_CONTRACT
    if version == "7":
        return VISIBLE_SOURCE_VERDICT_V7_CONTRACT
    if version == "8":
        return VISIBLE_SOURCE_VERDICT_V8_CONTRACT
    raise ValueError("visible source verdict version must be 1, 2, 3, 4, 5, 6, 7 or 8")


def visible_source_closure_schema(*, version: Literal["1", "2", "3", "4", "5", "6", "7", "8"] = "1") -> dict[str, object]:
    """Return an isolated provider schema for the exact strict-tool wire."""

    contract = _versioned_contract(version)
    if version in {"3", "4", "5", "6", "7", "8"}:
        schema = visible_source_closure_schema(version="2")
        schema["properties"]["contract"]["enum"] = [contract]
        original = schema["properties"]["decisions"]["items"]["properties"]
        branches = []
        for verdict, roles in (
            ("closed", ["external_proposition", "mixed"]),
            ("source_free", ["private_state", "commitment", "generalization", "question"]),
            ("unclosed", ["external_proposition", "mixed"]),
        ):
            properties = {
                key: deepcopy(value) for key, value in original.items()
                if key != "source_ref_indexes"
            }
            properties["verdict"]["enum"] = [verdict]
            properties["semantic_role"]["enum"] = roles
            if verdict == "closed":
                properties["first_source_ref_index"] = {"type": "integer"}
                properties["additional_source_ref_indexes"] = deepcopy(original["source_ref_indexes"])
            branches.append({
                "type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False,
            })
        schema["properties"]["decisions"]["items"] = {"anyOf": branches}
        if version in {"6", "7", "8"}:
            diagnostic = schema["properties"]["rejections"]["items"]
            for field in ("char_start", "char_end"):
                diagnostic["properties"].pop(field)
                diagnostic["required"].remove(field)
            diagnostic["properties"]["source_problem"]["enum"] = list(_SOURCE_PROBLEM_CODES)
        return schema
    schema = deepcopy(_VISIBLE_BEAT_VERDICT_SCHEMA)
    if version == "2":
        schema["properties"]["contract"]["enum"] = [contract]
        schema["properties"]["rejections"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "beat_index": {"type": "integer", "minimum": 0, "maximum": 15},
                    "char_start": {"type": "integer", "minimum": 0},
                    "char_end": {"type": "integer", "minimum": 1},
                    "related_source_ref_indexes": {"type": "array", "items": {"type": "integer"}},
                    "source_problem": {"type": "string"},
                },
                "required": [
                    "beat_index", "char_start", "char_end",
                    "related_source_ref_indexes", "source_problem",
                ],
                "additionalProperties": False,
            },
        }
        schema["required"].append("rejections")
    return schema


_MATERIAL_CONTRACT = "visible-source-materials.1"
_ENTRY_FIELDS = (
    "kind",
    "lane",
    "scope",
    "authority",
    "packet_contract",
    "epistemic_status",
    "source_refs",
    "actor_ref",
    "actor",
    "privacy_class",
    "availability",
    "does_not_authorize",
    "permits_natural_visible_uptake_without_world_claim",
    "natural_uptake_does_not_need_attribution_phrase",
)
_ITEM_FIELDS = (
    "item_ref",
    "source_ref",
    "source_refs",
    "source_hash",
    "source_bindings",
    "value_hash",
    "value",
    "privacy_class",
    "authority_scope",
    "availability",
)
_COORDINATE_FIELDS = (
    "contract",
    "parent_item_ref",
    "claim_scope",
    "field_path",
    "value",
    "logical_at",
)
_MESSAGE_FIELDS = (
    "event_ref",
    "event_payload_hash",
    "observation_ref",
    "source_world_revision",
    "actor",
    "channel",
    "text",
    "observed_at",
)
_REPORT_FIELDS = ("dialogue_ref", "text", "occurred_at", "sequence", "continuity_reasons")
_NON_SUPPORT_AUTHORITIES = frozenset(
    {
        "reference_metadata_only",
        "private_attention_exact_time_only_not_world_claim",
        "non_authoritative_advisory_not_external_fact",
        "attention_only_biographical_context_not_world_claim_authority;"
        "use_exact_biographical_coordinate_authority",
    }
)


def _selected_fields(value: object, fields: tuple[str, ...]) -> dict[str, object]:
    return (
        {key: deepcopy(value[key]) for key in fields if key in value}
        if isinstance(value, dict)
        else {}
    )


def _readable(value: object) -> bool:
    """Presence only: this never classifies the meaning or truth of prose."""
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and value != {} and value != []


def _material_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _historical_memory(value: object):
    from .memory_retrieval import MemoryRetrievalItem

    if not isinstance(value, dict) or "source_excerpts" not in value:
        return None
    try:
        memory = MemoryRetrievalItem.model_validate_json(json.dumps(value), strict=True)
    except (TypeError, ValueError):
        return None
    if memory.privacy_ceiling == "withhold" or any(
        source.prehistory is None for source in memory.source_excerpts
    ):
        return None
    return memory


def _review_item(raw: object) -> tuple[dict[str, object], bool]:
    item = _selected_fields(raw, _ITEM_FIELDS)
    value = item.get("value")
    if (
        item.get("privacy_class") == "withhold"
        or item.get("availability") == "unavailable"
        or isinstance(value, dict)
        and value.get("privacy_class") == "withhold"
    ):
        item.pop("value", None)
        return item, False
    exact_value = item.get("value_hash") == _material_hash(value)
    if not exact_value and "value_hash" in item:
        # Ordinary chat may carry a slim value beside the original full hash.
        # Retain that metadata without presenting it as a hash of this value.
        item["unverified_value_hash"] = item.pop("value_hash")
    try:
        bindings = tuple(
            ResolvedSourceBinding.model_validate(binding)
            for binding in item.get("source_bindings", ())
        )
        exact_sources = bool(bindings) and source_bindings_hash(bindings) == item.get("source_hash")
    except (TypeError, ValueError):
        exact_sources = False
    # These explicit payload fields have readable-body contracts. Unknown
    # shapes remain baseline material until their producer is qualified here.
    has_body = isinstance(value, dict) and any(
        _readable(value.get(field)) for field in ("text", "summary", "source_excerpt")
    )
    historical = _historical_memory(value)
    if historical is not None:
        expected = set(historical.committed_source_claims())
        actual = {
            (binding.ref, binding.source_world_revision, binding.immutable_hash)
            for binding in bindings if binding.source_kind == "committed_event"
        } if exact_sources else set()
        has_body = expected == actual
    return item, bool(exact_value and exact_sources and has_body)


def _review_material(entry: dict[str, object]) -> tuple[dict[str, object], bool]:
    material = _selected_fields(entry, _ENTRY_FIELDS)
    eligible = False
    if entry.get("privacy_class") == "withhold" or entry.get("availability") == "unavailable":
        return material, False
    kind = entry.get("kind")
    if kind == "biographical_coordinate":
        coordinate = _selected_fields(entry.get("material"), _COORDINATE_FIELDS)
        material["material"] = coordinate
        eligible = (
            coordinate.get("contract") == "biographical-coordinate-authority.1"
            and coordinate.get("claim_scope") == "current_world"
            and all(
                _readable(coordinate.get(field)) for field in ("field_path", "logical_at", "value")
            )
            and entry.get("source_refs")
            == ["biography-coordinate:sha256:" + _material_hash(coordinate)]
        )
    elif kind == "current_counterpart_report":
        material["message"] = _selected_fields(entry.get("message"), _MESSAGE_FIELDS)
        reports = entry.get("messages", ())
        material["messages"] = (
            [_selected_fields(report, _REPORT_FIELDS) for report in reports]
            if isinstance(reports, (list, tuple))
            else []
        )
        message = material["message"]
        eligible = bool(
            _readable(message.get("text"))
            and message.get("actor")
            and message.get("event_ref")
            and message.get("event_payload_hash")
        )
    elif kind == "pinned_context_item":
        material["item"], eligible = _review_item(entry.get("item"))
    elif kind == "pinned_context_slice":
        raw_slice = entry.get("slice")
        selected = _selected_fields(
            raw_slice,
            (
                "availability",
                "privacy_class",
                "source_refs",
                "source_hash",
                "pinned_world_revision",
            ),
        )
        raw_items = (
            raw_slice.get("items", ())
            if isinstance(raw_slice, dict)
            and selected.get("availability") == "available"
            and selected.get("privacy_class") != "withhold"
            else ()
        )
        items = (
            [_review_item(item) for item in raw_items]
            if isinstance(raw_items, (list, tuple))
            else []
        )
        selected["items"] = [item for item, _ in items]
        material["slice"] = selected
        eligible = (
            bool(items)
            and all(valid for _, valid in items)
            and selected.get("availability") == "available"
        )
    if isinstance(entry.get("authority"), str) and entry["authority"] in _NON_SUPPORT_AUTHORITIES:
        eligible = False
    return material, eligible


def _activity_support(
    material: dict[str, object],
    source_ref: str,
) -> tuple[str, dict[str, object]] | None:
    """Qualify one exact lifecycle ref in the reader's already selected body.

    The public activity reader owns original role/Plan audit validation. This
    projection checks the typed value and its internal event bindings; it never
    reads another source or promotes the intention's embedded prose to fact.
    """
    if (
        material.get("lane") != "world_life"
        or source_ref not in material.get("source_refs", ())
        or material.get("privacy_class") == "withhold"
        or material.get("availability") == "unavailable"
        or (
            isinstance(material.get("authority"), str)
            and material["authority"] in _NON_SUPPORT_AUTHORITIES
        )
    ):
        return None
    projected, _ = _review_material(material)
    if projected.get("kind") == "pinned_context_item":
        items = [projected["item"]]
    elif projected.get("kind") == "pinned_context_slice":
        items = projected["slice"]["items"]
    else:
        return None
    matching = [item for item in items if item.get("item_ref") == source_ref]
    if len(matching) != 1:
        return None
    item = matching[0]
    value = item.get("value")
    if not isinstance(value, dict) or item.get("value_hash") != _material_hash(value):
        return None
    context_kind = value.get("context_kind")
    if not isinstance(context_kind, str):
        return None
    activity_type = {
        "active_activity": ActiveActivityContextItem,
        "completed_activity": CompletedActivityContextItem,
        "planned_activity": PlannedActivityContextItem,
    }.get(context_kind)
    if activity_type is None:
        return None
    try:
        activity = activity_type.model_validate_json(json.dumps(value, ensure_ascii=False))
        bindings = tuple(
            ResolvedSourceBinding.model_validate(binding)
            for binding in item.get("source_bindings", ())
        )
    except (TypeError, ValueError):
        return None
    intention = activity.accepted_intention
    if (
        len(bindings) != (1 if isinstance(activity, PlannedActivityContextItem) else 2)
        or source_bindings_hash(bindings) != item.get("source_hash")
        or activity.activity_event_ref != source_ref
        or item.get("privacy_class") != activity.privacy_class
        or intention.truncated
        or hashlib.sha256(intention.text.encode("utf-8")).hexdigest()
        != intention.content_payload_hash
    ):
        return None
    if isinstance(activity, PlannedActivityContextItem):
        planned = activity.source_bindings[0]
        binding = bindings[0]
        if (
            planned.authority_event_ref != source_ref
            or binding.ref != source_ref
            or binding.source_kind != "committed_event"
            or binding.authority_type != "ActivityPlanned"
            or binding.source_world_revision != planned.authority_world_revision
            or binding.immutable_hash != planned.authority_payload_hash
        ):
            return None
        return activity.owner_actor_ref, {
            "contract": "visible-planned-activity-source.1",
            "status": "planned",
            "source_event_type": "ActivityPlanned",
            "scope": activity.planning_scope,
        }
    planned, lifecycle = activity.source_bindings
    if (
        planned.authority_event_ref == lifecycle.authority_event_ref
        or lifecycle.authority_event_ref != source_ref
        or planned.authority_world_revision >= lifecycle.authority_world_revision
    ):
        return None
    by_ref = {binding.ref: binding for binding in bindings}
    if len(by_ref) != 2:
        return None
    for inner in activity.source_bindings:
        outer = by_ref.get(inner.authority_event_ref)
        if (
            outer is None
            or outer.source_kind != "committed_event"
            or outer.source_world_revision != inner.authority_world_revision
            or outer.immutable_hash != inner.authority_payload_hash
        ):
            return None
    lifecycle_type = by_ref[source_ref].authority_type
    expected_types = (
        {"ActivityStarted", "ActivityResumed"}
        if isinstance(activity, ActiveActivityContextItem)
        else {"ActivityCompleted"}
    )
    if (
        by_ref[planned.authority_event_ref].authority_type != "ActivityPlanned"
        or lifecycle_type not in expected_types
    ):
        return None
    return activity.owner_actor_ref, {
        "contract": "visible-activity-source.1",
        "status": activity.status,
        "source_event_type": lifecycle_type,
        "scope": (
            "activity_in_progress_not_intention_fulfilled"
            if isinstance(activity, ActiveActivityContextItem)
            else activity.completion_scope
        ),
    }


def _eligible_reference(row: dict[str, object]) -> bool:
    if row.get("support_eligibility") != "eligible":
        return False
    material = row.get("review_material")
    if not isinstance(material, dict):
        return False
    if "settled_life_support" in row:
        support = settled_life_source_support(material, row.get("source_ref"))
        return bool(
            support is not None
            and support[0] == row.get("support_subject_ref")
            and support[1] == row["settled_life_support"]
        )
    if "activity_support" in row:
        support = _activity_support(material, row.get("source_ref"))
        return bool(
            support is not None
            and support[0] == row.get("support_subject_ref")
            and support[1] == row["activity_support"]
        )
    return _review_material(material)[1]


def _packet_materials(
    rows: tuple[dict[str, object], ...],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Material occurs once in the packet, even when an entry has many refs."""
    materials: list[dict[str, object]] = []
    references: list[dict[str, object]] = []
    indexes: dict[str, int] = {}
    for row in rows:
        reference = {key: value for key, value in row.items() if key != "review_material"}
        material = row.get("review_material")
        if isinstance(material, dict):
            # New packets expose prose only through the privacy-projected
            # material. The historical text field must not bypass that filter.
            reference.pop("evidence_text", None)
            identity = json.dumps(
                material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            if identity not in indexes:
                indexes[identity] = len(materials)
                materials.append(material)
            reference["material_index"] = indexes[identity]
        references.append(reference)
    return references, materials


def _material_subject(
    material: dict[str, object],
    subjects: dict[str, object],
) -> tuple[str | None, str | None]:
    """Bind explicit source actors to this packet's participants, never names."""
    if material.get("kind") == "biographical_coordinate":
        actor = subjects.get("companion_actor_ref")
    elif material.get("kind") == "current_counterpart_report":
        actor = material.get("message", {}).get("actor")
    elif (material.get("lane") == "active_memory_candidates"
          and material.get("authority") == "retained_character_prehistory_exact_excerpt_only"):
        memory = _historical_memory(material.get("item", {}).get("value"))
        if memory is None:
            return None, None
        actors = {source.prehistory.actor_ref for source in memory.source_excerpts}
        actor = next(iter(actors)) if len(actors) == 1 else None
    else:
        items = (
            [material["item"]] if "item" in material else material.get("slice", {}).get("items", ())
        )
        actors = {
            value[field]
            for item in items
            for value in (item.get("value"),)
            if isinstance(value, dict)
            for field in ("subject_ref", "speaker_ref")
            if isinstance(value.get(field), str) and value[field]
        }
        actor = next(iter(actors)) if len(actors) == 1 else None
    if not isinstance(actor, str) or not actor:
        return None, None
    role = (
        "companion"
        if actor == subjects.get("companion_actor_ref")
        else "counterpart"
        if actor == subjects.get("counterpart_actor_ref")
        else None
    )
    return actor, role


def compact_source_reference_table(
    source_evidence: dict[str, object],
) -> tuple[dict[str, object], ...]:
    """Keep historical refs and attach one shared projection per selected entry."""

    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    subjects = source_evidence.get("subjects", {})
    if not isinstance(subjects, dict):
        raise ValueError("source evidence subjects must be an object")
    counterpart_actor_ref = subjects.get("counterpart_actor_ref")
    companion_actor_ref = subjects.get("companion_actor_ref")
    entries = source_evidence.get("entries", ())
    if not isinstance(entries, (list, tuple)):
        raise ValueError("source evidence entries must be a sequence")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("source evidence entry must be an object")
        refs = entry.get("source_refs", ())
        if not isinstance(refs, (list, tuple)):
            raise ValueError("source evidence refs must be a sequence")
        material, eligible = _review_material(entry)
        support_subject, support_role = _material_subject(material, subjects)
        eligible = eligible and support_role is not None
        for ref in refs:
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("source evidence ref must be non-empty")
            normalized = ref.strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            activity = _activity_support(material, normalized)
            settled_life = settled_life_source_support(material, normalized)
            row_subject, row_role, row_eligible = support_subject, support_role, eligible
            specific_support = activity if activity is not None else settled_life
            if specific_support is not None:
                row_subject = specific_support[0]
                row_role = (
                    "companion"
                    if row_subject == companion_actor_ref
                    else "counterpart"
                    if row_subject == counterpart_actor_ref
                    else None
                )
                row_eligible = row_role is not None
            message = entry.get("message")
            message_actor = message.get("actor") if isinstance(message, dict) else None
            evidence_text = (
                message.get("text")
                if isinstance(message, dict) and isinstance(message.get("text"), str)
                else message
                if isinstance(message, str)
                else entry.get("summary")
            )
            actor_ref = entry.get("actor_ref") or entry.get("actor") or message_actor
            subject_role = (
                "counterpart"
                if isinstance(counterpart_actor_ref, str) and actor_ref == counterpart_actor_ref
                else "companion"
                if (isinstance(companion_actor_ref, str) and actor_ref == companion_actor_ref)
                or (isinstance(actor_ref, str) and actor_ref.startswith("companion:"))
                else "other"
                if isinstance(actor_ref, str) and actor_ref
                else None
            )
            rows.append(
                {
                    "source_ref_index": len(rows),
                    "source_ref": normalized,
                    "kind": entry.get("kind"),
                    "epistemic_status": entry.get("epistemic_status"),
                    "actor_ref": actor_ref,
                    "subject_role": subject_role,
                    "evidence_text": evidence_text,
                    "review_material": material,
                    "support_eligibility": "eligible" if row_eligible else "baseline_only",
                    "support_subject_ref": row_subject,
                    "support_subject_role": row_role,
                    **({"activity_support": activity[1]} if activity is not None else {}),
                    **({"settled_life_support": settled_life[1]} if settled_life is not None else {}),
                }
            )
    return tuple(rows)


_SYSTEM_CONTRACT = """You are a factual source-boundary classifier. Return the forced tool only.
For every complete visible Beat, return exactly one decision with its host-provided beat_index:
- source_free only for immediate first-person private state, a genuine question without a factual presupposition, or a world-unbound generalization;
- source_free also covers a present speech act, promise, offer, intention, or commitment by the companion; it does not prove that a future or completed event happened;
- closed only when pinned evidence entails the exact fact with the same actor, polarity, time, and status;
- unclosed otherwise.
A plan is not completion. Negation is not supported by positive evidence. In candidate dialogue I=companion and you=counterpart. If any portion is an unclosed external proposition, the whole Beat is unclosed. source_ref_indexes must be empty unless verdict is closed. Do not rewrite or segment a Beat.
For semantic_role=mixed, subject_role identifies the actor of the source-bearing external clause, not the source-free private clause; never use mixed or none to evade actor binding.
Critical examples:
- "你刚才淋雨了。" plus pinned counterpart report "我刚才在路上淋雨了。" is closed with that report index: natural uptake of the exact current report does not require an attribution phrase.
- "我今天淋雨了。" plus evidence that the counterpart said "我淋雨了" is unclosed: candidate I is companion, evidence I is counterpart.
- "我有点担心你，你现在发烧了。" with no evidence is unclosed: the private-state clause does not hide the unsupported fever clause.
- "这件事我不会跟别人说。" is source_free with semantic_role commitment: it performs a current promise and does not claim that an external event already happened.
- "我已经把这件事告诉别人了。" is unclosed without evidence: completed disclosure is an external episode, not a promise.
- "我今天在公园摔了一跤。" with no evidence is unclosed: a completed first-person episode is an external proposition, not immediate private state.
- "你今天没有出门。" plus pinned evidence "我今天出门了。" is unclosed: positive evidence does not support the opposite polarity.
- "你今天很忙吗？" is source_free when it is a genuine question without a factual presupposition."""

_DIAGNOSTICS_SYSTEM_CONTRACT = """
Version 2 keeps every complete Beat verdict and adds rejections. Return exactly one rejection for each unclosed Beat and none for closed or source_free Beats. Each rejection has beat_index, char_start, char_end, related_source_ref_indexes, and source_problem.
Locate a nonempty disputed span in the original Beat using zero-based Unicode code point offsets [char_start, char_end); count neither UTF-8 bytes nor UTF-16 code units. Do not copy or rewrite the span. The host derives its quotation from the pinned original Beat.
related_source_ref_indexes are at most eight unique indexes into the supplied source table, or empty. They explain a source problem; they never supply authority, close a Beat, or change source_ref_indexes. source_problem explains the missing or mismatched source support in at most 64 Unicode characters and 96 JSON-encoded characters including quotes, using unescaped Unicode. Do not author replacement dialogue or instruct the character's choices. Review every full Beat even when only part is disputed."""

_SYSTEM_CONTRACT_V3 = (
    _SYSTEM_CONTRACT.replace(
        "source_ref_indexes must be empty unless verdict is closed.",
        "Each verdict branch must contain exactly its own fields.",
    )
    + _DIAGNOSTICS_SYSTEM_CONTRACT.replace("Version 2", "Version 3").replace(
        "change source_ref_indexes", "change the selected source authority"
    )
    + """
VERDICT BRANCH TRANSPORT V3:
Every decision has beat_index, verdict, semantic_role and subject_role. A closed decision also requires first_source_ref_index (one integer) and additional_source_ref_indexes (an array, empty when no additional support is needed). Select the actual first supporting source index yourself from the supplied pinned table. All selected indexes together must be unique, in range and at most eight; their evidence must entail the exact claim with the same actor, polarity, time, status and disclosure authority. Never invent, guess or default a source index. If no eligible evidence supports an external proposition, return unclosed and its rejection diagnostic.
source_free and unclosed decisions have no source fields: omit first_source_ref_index, additional_source_ref_indexes and source_ref_indexes entirely, including null or empty padding. closed also has no source_ref_indexes field. The host only combines the explicit closed first/additional indexes or normalizes a source-free/unclosed branch to an empty source set; it never chooses support. Diagnostic related_source_ref_indexes explain a problem and cannot close a Beat.
HARD CLASSIFICATION MATRIX (any violation makes the whole verdict wire invalid):
- private_state: verdict MUST be source_free and subject_role MUST be companion.
- commitment: verdict MUST be source_free and subject_role MUST be companion; a promise, offer, intention or commitment is always performed by the companion speaker.
- generalization: verdict MUST be source_free and subject_role MUST be general or none.
- external_proposition or mixed: verdict MUST be closed or unclosed. closed requires at least one selected row with support_eligibility exactly "eligible" whose support_subject_role equals the decision subject_role. Rows marked baseline_only, host_only, context_only or any value other than "eligible" can never close a Beat. If no such row exists, return unclosed with its rejection.
- A diagnostic span must satisfy char_start >= 0 and char_start < char_end <= the supplied text_length of its Beat.
Do not choose a verdict first and a role second: read the text, then apply this matrix exactly."""
)


_SYSTEM_CONTRACT_V4 = """EVIDENCE CARDS V4:
Each source_reference_tables entry supplies columns and rows. Read a row by pairing its values with those columns. source_ref_index is the original selectable index; material_index selects source_materials. Groups preserve different field sets: a missing column is absent, not a null value. Group/card position is never a source index. Each reference retains its OWN support_eligibility and actor; sharing a material grants no shared authority. Host-only integrity hashes were omitted from this view, but the host retains and revalidates the exact original table.
Judge what each material establishes, not whether candidate wording resembles it. An accepted intention proves only that the companion intends a task in its scheduled window. Objects and past events named inside that intention have NOT thereby been independently observed. Reporting a future intention is allowed; asserting an object's present condition or a prior episode needs separate eligible evidence. This distinction does not deny that the object or episode exists.
If a Beat combines a supported future intention with an unsupported present/past fact, mark the WHOLE Beat unclosed and locate the disputed factual clause. A matching phrase inside an intention is insufficient support for that clause. For example, an intention to repair something does not prove it was previously repaired or is currently broken. Conversely a pure statement of a future intention need not invent an execution source. Active/completed lifecycle states likewise do not prove the intention's embedded history or successful outcome.
Do not classify a statement about the counterpart as the companion's immediate private state. Natural uptake of an exact current counterpart report is allowed, using that report's index and counterpart subject; it need not quote or formally attribute the user.

ACTIVITY SOURCE READING: An active activity source proves that its lifecycle is in progress. Its accepted_intention.text may support a first-person statement that she is currently working on the exact intended thing, but only when the wording adds no place, object, duration, completion, result, or third-party fact absent from that exact text. A generic activity name or a different activity is not entailed; an intention is still not completion.
""" + _SYSTEM_CONTRACT_V3.replace("Version 3", "Version 4").replace("TRANSPORT V3", "TRANSPORT V4")


# Version 4 is a historical receipt compiler. New semantic instructions must
# have a new request/tool identity so old preparations remain reproducible.
_SYSTEM_CONTRACT_V5 = """IMMEDIATE EXPERIENCE AND SELF-HISTORY V5:
Read what the complete utterance asserts, including presupposed causes and transitions. First-person wording and recent timing do not make an event private_state. A report of having slept, woken, left, arrived, eaten, checked something or met someone asserts an episode or transition even when casual, bodily, or only moments old. Classify it as external_proposition (mixed if combined with private state); require eligible pinned evidence for the same actor, time and event status. Without it the whole Beat is unclosed. This is an entailment distinction, not a word or tense blacklist.
A present sensation, feeling, preference or self-assessment alone can be source_free/private_state. For example, "还有点困" or "脑子有点懵" alone reports current experience; "早 才醒没多久" additionally asserts a recent waking episode and needs evidence. "刚醒，脑子有点懵" still needs evidence for waking. A habitual sleep schedule, day sheet, routine, persona, current clock time, or newly authored private appraisal cannot independently prove that this episode occurred today. Absence of evidence does not prove that it did not occur.
Keep the companion's present conversational agency: "刚才我听偏了" may acknowledge her interpretation in the ongoing conversation as private_state, without claiming an offscreen event. "我去查过了才发现听偏了" additionally claims a completed check and needs its source. A pure intention such as "等会儿想睡一会儿" remains source_free/commitment. An eligible record of the exact episode can close self-history; do not reject it merely for being personal. Use the full pinned context and preserve every source's authority limits.

""" + _SYSTEM_CONTRACT_V4.replace("EVIDENCE CARDS V4", "EVIDENCE CARDS V5").replace(
    "Version 4", "Version 5"
).replace("TRANSPORT V4", "TRANSPORT V5")


_SYSTEM_CONTRACT_V6 = _SYSTEM_CONTRACT_V5.replace(
    _DIAGNOSTICS_SYSTEM_CONTRACT.replace("Version 2", "Version 5").replace(
        "change source_ref_indexes", "change the selected source authority"
    ),
    "",
).replace(
    "- A diagnostic span must satisfy char_start >= 0 and char_start < char_end <= the supplied text_length of its Beat.",
    "- Each rejection must identify one unclosed complete Beat by its beat_index.",
).replace("locate the disputed factual clause", "identify its complete Beat").replace(
    "V5", "V6"
).replace("Version 5", "Version 6") + """
REJECTION TRANSPORT V6:
Return exactly one rejection for each unclosed Beat and none for other Beats. Its only fields are beat_index, related_source_ref_indexes and source_problem. Select source_problem from the exact short codes in the tool schema; never write prose or replacement dialogue. Use other_source_problem when no more specific code fits. These codes describe evidence boundaries, not character motivation or behavior. related_source_ref_indexes are up to eight unique original indexes explaining the problem, or empty; they cannot close a Beat.
Do not count characters or output char_start/char_end. The host binds your selected beat_index to that entire original Beat for feedback, without selecting a clause or changing your verdict. Review the whole Beat and preserve every factual and disclosure boundary above.
"""


# Separate request identity preserves all earlier semantic contracts and receipts.
_SYSTEM_CONTRACT_V8 = """EVIDENCE COMPOSITION BOUNDARY V8:
Before marking a complete Beat closed, identify each external occurrence or action it asserts and which source actually establishes that occurrence, its actor and time. Selecting several eligible references does not grant a new combined authority.
An accepted intention records an intention. A started/completed activity lifecycle records that lifecycle status. A settlement containing only environmental text records that environment. Combining these three cannot prove an unrecorded action, arrival, position, accomplished task or past personal episode. Participant/location metadata situates an environmental occurrence; it does not independently record an action by that participant. An exact authorized_attempt_result can establish only the action/result it actually records.
For example, a plan to visit a market, an ended activity lifecycle, and a later report that stalls closed do not establish that she stood at a particular stall that morning. They can separately support the recorded plan, lifecycle status and environmental closure. An independent record that she actually arrived or acted at that time can support that exact action; do not reject supported action records by category.
Sources may jointly support different clauses only when each asserted factual link already has its own support. Temporal proximity, plausibility, matching location or intention wording cannot supply a missing link. If an action or time link is missing, mark the whole Beat unclosed and identify the related refs as diagnostics. Absence of support is not evidence that the action never happened. Preserve source-free current feelings, conversational acts and pure future intentions.

""" + _SYSTEM_CONTRACT_V6.replace("V6", "V8").replace(
    "Version 6", "Version 8"
)


def _evidence_first_packet(packet: dict[str, object]) -> dict[str, object]:
    candidate_keys = ("visible_beats", "world_claims")
    return {
        **{key: value for key, value in packet.items() if key not in candidate_keys},
        **{key: packet[key] for key in candidate_keys},
    }


def visible_source_closure_messages(
    *,
    visible_beats: tuple[str, ...],
    world_claims: tuple[dict[str, object], ...],
    source_references: tuple[dict[str, object], ...],
    invalid_reason: VisibleSourceClosureWireFailure | None = None,
    version: Literal["1", "2", "3", "4", "5", "6", "7", "8"] = "1",
) -> list[dict[str, str]]:
    """Compile one compact request; correction never echoes invalid bytes."""

    contract = _versioned_contract(version)
    if len(visible_beats) > 16:
        raise ValueError("visible source verdict supports at most sixteen Beats")
    packet: dict[str, object] = {
        "output_contract": {
            "contract": contract,
            "authority": "correlated_source_guard_not_character_author",
        },
        "visible_beats": tuple(
            (
                {"beat_index": index, "text": text, "text_length": len(text)}
                if version in {"3", "4", "5", "6", "7", "8"}
                else {"beat_index": index, "text": text}
            )
            for index, text in enumerate(visible_beats)
        ),
        "dialogue_subject_contract": {
            "candidate_first_person": "companion_actor",
            "candidate_second_person": "counterpart_actor",
            "evidence_actor_ref_is_authoritative": True,
            "subject_swap_is_unclosed": True,
        },
        "world_claims": world_claims,
        "source_references": source_references,
    }
    if any("review_material" in row for row in source_references):
        references, materials = _packet_materials(source_references)
        packet.update(
            {
                "source_material_contract": _MATERIAL_CONTRACT,
                "source_references": references,
                "source_materials": materials,
                "source_support_contract": (
                    "Only eligible references may close a Beat. baseline_only is not supporting "
                    "authority. Material retains its original scope, actor, privacy, time and status; "
                    "readability does not prove entailment or authorize disclosure. "
                    "support_subject_ref/support_subject_role identify the readable material's "
                    "subject; legacy subject_role may be absent and cannot override them."
                ),
            }
        )
        if any("settled_life_support" in row for row in source_references):
            packet["settled_life_support_contract"] = {
                "contract": "visible-settled-life-source.1",
                "scope": (
                    "A settlement proves only its recorded environment at settled_at. "
                    "It does not establish the companion's current location or activity. "
                    "An authorized_attempt_result proves only the result bound to that exact "
                    "execution. A character response or composite Experience is a private "
                    "reading, not evidence that a new external event or action occurred. "
                    "Truncated material proves no omitted detail; private source visibility "
                    "is not permission to disclose it. Judge entailment for each full Beat."
                ),
            }
        if any("activity_support" in row for row in source_references):
            packet["activity_support_contract"] = {
                "contract": "visible-activity-source.1",
                "scope": (
                    "The exact lifecycle ref supports only its typed activity state. "
                    "Active means in progress; completed means the lifecycle ended. "
                    "Neither establishes intention fulfillment, embedded history, location "
                    "presence or objective outcome. accepted_intention remains intention-only; "
                    "private material does not become authorized for disclosure."
                ),
            }
        if any(
            row.get("activity_support", {}).get("contract") == "visible-planned-activity-source.1"
            for row in source_references
        ):
            packet["planned_activity_support_contract"] = {
                "contract": "visible-planned-activity-source.1",
                "scope": (
                    "The exact ActivityPlanned ref proves only an accepted intention and its "
                    "scheduled window. It proves no started activity, location presence, "
                    "past experience, embedded backstory, completion or objective outcome."
                ),
            }
    if version in {"4", "5", "6", "7", "8"}:
        from .visible_source_evidence_cards import compile_visible_evidence_cards

        references, materials = _packet_materials(source_references)
        packet.pop("source_references")
        packet.update(compile_visible_evidence_cards(references=references, materials=materials))
    if version in {"7", "8"}:
        # Preserve every evidence value and original index. Only candidate-specific
        # fields move behind the stable evidence, allowing correction reviews to
        # reuse a provider prefix. Earlier request bytes remain replayable.
        packet = _evidence_first_packet(packet)
    messages = [
        {
            "role": "system",
            "content": (
                _SYSTEM_CONTRACT_V8 if version == "8"
                else _SYSTEM_CONTRACT_V6.replace("V6", "V7").replace("Version 6", "Version 7") if version == "7"
                else _SYSTEM_CONTRACT_V6 if version == "6"
                else _SYSTEM_CONTRACT_V5 if version == "5"
                else _SYSTEM_CONTRACT_V4 if version == "4"
                else _SYSTEM_CONTRACT_V3 if version == "3"
                else _SYSTEM_CONTRACT + (_DIAGNOSTICS_SYSTEM_CONTRACT if version == "2" else "")
            ),
        },
        {
            "role": "user",
            "content": json.dumps(packet, ensure_ascii=False, separators=(",", ":")),
        },
    ]
    if invalid_reason is not None:
        source_subject_roles = tuple(
            {
                "source_ref_index": index,
                "subject_role": (
                    row.get("support_subject_role")
                    if "review_material" in row
                    else row.get("subject_role")
                ),
                **(
                    {
                        "support_subject_ref": row.get("support_subject_ref"),
                        "support_eligibility": row.get("support_eligibility"),
                    }
                    if "review_material" in row
                    else {}
                ),
            }
            for index, row in enumerate(source_references)
        )
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "correction_contract": f"visible-beat-source-verdict-repair.{version}",
                        "failure": invalid_reason.correction_coordinate(),
                        "structural_constraints": {
                            "expected_beat_indexes": list(range(len(visible_beats))),
                            "source_ref_count": len(source_references),
                            "source_subject_roles": source_subject_roles,
                            "verdict_role_ref_matrix": {
                                "source_free": {
                                    "semantic_roles": [
                                        "private_state",
                                        "commitment",
                                        "generalization",
                                        "question",
                                    ],
                                    "source_ref_indexes": "empty",
                                },
                                "closed": {
                                    "semantic_roles": [
                                        "external_proposition",
                                        "mixed",
                                    ],
                                    "source_ref_indexes": ("one_to_eight_unique_in_range"),
                                },
                                "unclosed": {
                                    "semantic_roles": [
                                        "external_proposition",
                                        "mixed",
                                    ],
                                    "source_ref_indexes": "empty",
                                },
                            },
                        },
                        "instruction": (
                            "Return one complete replacement verdict list for the identical "
                            "pinned candidate. Do not change or author the candidate."
                        ),
                        "output_contract": {
                            "contract": contract,
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
    if version in {"3", "4", "5", "6", "7", "8"} and invalid_reason is not None:
        repair = json.loads(messages[-1]["content"])
        matrix = repair["structural_constraints"]["verdict_role_ref_matrix"]
        for verdict, constraints in matrix.items():
            del constraints["source_ref_indexes"]
            if verdict == "closed":
                constraints.update(
                    first_source_ref_index="one_required_actual_pinned_index",
                    additional_source_ref_indexes="zero_to_seven_more_unique_pinned_indexes",
                )
            else:
                constraints["source_fields"] = "absent_including_null_and_empty_padding"
        messages[-1]["content"] = json.dumps(repair, ensure_ascii=False, separators=(",", ":"))
    return messages


def parse_visible_source_closure(
    raw: str,
    *,
    visible_beats: tuple[str, ...],
    source_ref_kinds: tuple[str | None, ...],
    source_ref_subject_roles: tuple[str | None, ...] = (),
    source_references: tuple[dict[str, object], ...] | None = None,
) -> VisibleSourceClosureWire:
    """Validate exhaustive whole-Beat decisions and pinned source bindings."""

    try:
        provider_wire = _ProviderVisibleBeatVerdictWire.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        location = errors[0].get("loc", ()) if errors else ()
        safe_location = tuple(
            item
            for item in location
            if isinstance(item, int)
            or (
                isinstance(item, str)
                and item
                in {
                    "contract",
                    "decisions",
                    "beat_index",
                    "verdict",
                    "semantic_role",
                    "subject_role",
                    "source_ref_indexes",
                }
            )
        )
        beat_index = (
            safe_location[1]
            if len(safe_location) > 1
            and safe_location[0] == "decisions"
            and isinstance(safe_location[1], int)
            else None
        )
        field = ".".join(str(item) for item in safe_location) if safe_location else None
        raise VisibleSourceClosureWireFailure(
            "schema_invalid",
            "visible source verdict wire schema is invalid",
            beat_index=beat_index,
            field=field,
        ) from None
    if len(visible_beats) > 16:
        raise ValueError("visible source verdict supports at most sixteen Beats")
    if source_ref_subject_roles and len(source_ref_subject_roles) != len(source_ref_kinds):
        raise ValueError("source kind and subject tables must align")
    if source_references is not None and (
        len(source_references) != len(source_ref_kinds)
        or any(
            row.get("source_ref_index") != index
            or row.get("kind") != source_ref_kinds[index]
            or (
                source_ref_subject_roles
                and row.get("subject_role") != source_ref_subject_roles[index]
            )
            for index, row in enumerate(source_references)
        )
    ):
        raise ValueError("source material table must align with pinned source indexes")
    decisions = provider_wire.decisions
    expected_indexes = tuple(range(len(visible_beats)))
    actual_indexes = tuple(decision.beat_index for decision in decisions)
    if (
        len(actual_indexes) != len(expected_indexes)
        or len(set(actual_indexes)) != len(actual_indexes)
        or set(actual_indexes) != set(expected_indexes)
    ):
        raise VisibleSourceClosureWireFailure(
            "beat_coverage_invalid",
            "source verdicts must cover each visible Beat exactly once",
            field="decisions",
        )
    # Array order carries no semantic authority when the indexes form one
    # complete unique cover. Canonicalize that transport-only variation before
    # validating each indexed verdict; missing or duplicate coverage still
    # fails closed above.
    decisions = tuple(sorted(decisions, key=lambda decision: decision.beat_index))

    normalized: list[VisibleSourceClosureSegment] = []
    for decision, text in zip(decisions, visible_beats, strict=True):
        beat_index = decision.beat_index
        refs = decision.source_ref_indexes
        if len(refs) > 8 or len(set(refs)) != len(refs):
            raise VisibleSourceClosureWireFailure(
                "ref_set_invalid",
                "source verdict indexes must be bounded and unique",
                beat_index=beat_index,
                field=f"decisions.{beat_index}.source_ref_indexes",
            )
        if any(index < 0 or index >= len(source_ref_kinds) for index in refs):
            raise VisibleSourceClosureWireFailure(
                "ref_set_invalid",
                "source verdict index is outside pinned evidence",
                beat_index=beat_index,
                field=f"decisions.{beat_index}.source_ref_indexes",
            )
        if decision.verdict == "source_free":
            if decision.semantic_role not in {
                "private_state",
                "commitment",
                "generalization",
                "question",
            }:
                raise VisibleSourceClosureWireFailure(
                    "verdict_role_invalid",
                    "external or mixed Beat cannot be source-free",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.semantic_role",
                )
            if refs:
                raise VisibleSourceClosureWireFailure(
                    "verdict_ref_invalid",
                    "source-free Beat cannot claim source refs",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.source_ref_indexes",
                )
            if (
                decision.semantic_role in {"private_state", "commitment"}
                and decision.subject_role != "companion"
            ):
                raise VisibleSourceClosureWireFailure(
                    "subject_binding_invalid",
                    "companion private state or commitment cannot change actor",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.subject_role",
                )
            if decision.semantic_role == "generalization" and decision.subject_role not in {
                "general",
                "none",
            }:
                raise VisibleSourceClosureWireFailure(
                    "subject_binding_invalid",
                    "source-free generalization must retain general scope",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.subject_role",
                )
        elif decision.verdict == "closed":
            if decision.semantic_role not in {"external_proposition", "mixed"}:
                raise VisibleSourceClosureWireFailure(
                    "verdict_role_invalid",
                    "only an external or mixed Beat can bind sources",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.semantic_role",
                )
            if not refs:
                raise VisibleSourceClosureWireFailure(
                    "verdict_ref_invalid",
                    "closed Beat requires at least one pinned source",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.source_ref_indexes",
                )
            if source_references is not None and any(
                not _eligible_reference(source_references[index]) for index in refs
            ):
                raise VisibleSourceClosureWireFailure(
                    "verdict_ref_invalid",
                    "closed Beat requires eligible readable source material",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.source_ref_indexes",
                )
            if source_references is not None and any(
                source_references[index].get("support_subject_role") != decision.subject_role
                for index in refs
            ):
                raise VisibleSourceClosureWireFailure(
                    "subject_binding_invalid",
                    "closed Beat source material actor does not match subject role",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.subject_role",
                )
        else:
            if decision.semantic_role not in {"external_proposition", "mixed"}:
                raise VisibleSourceClosureWireFailure(
                    "verdict_role_invalid",
                    "unclosed Beat must identify external semantic material",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.semantic_role",
                )
            if refs:
                raise VisibleSourceClosureWireFailure(
                    "verdict_ref_invalid",
                    "unclosed Beat cannot claim partial source authority",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.source_ref_indexes",
                )

        subject_role: VisibleSubjectRole = (
            "other" if decision.subject_role == "mixed" else decision.subject_role
        )
        if refs and source_ref_subject_roles:
            known_roles = {
                source_ref_subject_roles[index]
                for index in refs
                if source_ref_subject_roles[index] is not None
            }
            if decision.subject_role in {"none", "mixed"}:
                raise VisibleSourceClosureWireFailure(
                    "subject_binding_invalid",
                    "closed Beat must identify its source actor",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.subject_role",
                )
            if known_roles and subject_role not in known_roles:
                raise VisibleSourceClosureWireFailure(
                    "subject_binding_invalid",
                    "closed Beat source actor does not match subject role",
                    beat_index=beat_index,
                    field=f"decisions.{beat_index}.subject_role",
                )

        role: VisibleSemanticRole = {
            "private_state": "immediate_private_state",
            "commitment": "nonassertive_content",
            "external_proposition": "standalone_external_proposition",
            "generalization": "world_unbound_generalization",
            "question": "nonassertive_content",
            "mixed": "embedded_external_proposition",
        }[decision.semantic_role]
        relation: VisibleSourceRelation = (
            "unclosed"
            if decision.verdict == "unclosed"
            else "first_person_immediate_private_continuity"
            if decision.semantic_role == "private_state"
            else "not_external_proposition"
            if decision.verdict == "source_free"
            else _relation_for_source_kinds(tuple(source_ref_kinds[index] for index in refs))
        )
        normalized.append(
            VisibleSourceClosureSegment(
                locator=VisibleSourceClosureLocator(
                    beat_index=decision.beat_index,
                    char_start=0,
                    char_end=len(text),
                    text=text,
                ),
                semantic_role=role,
                subject_role=subject_role,
                decision=decision.verdict,
                source_relation=relation,
                source_ref_indexes=refs,
            )
        )
    return VisibleSourceClosureWire(
        contract=VISIBLE_SOURCE_CLOSURE_CONTRACT,
        segments=tuple(normalized),
    )


def _relation_for_source_kinds(kinds: tuple[str | None, ...]) -> VisibleSourceRelation:
    if not kinds:
        return "unclosed"
    normalized = frozenset(kind for kind in kinds if isinstance(kind, str))
    if normalized and normalized <= {"current_counterpart_report"}:
        return "exact_current_report_discourse_coverage"
    if normalized and normalized <= {"recent_dialogue", "dialogue_record"}:
        return "exact_dialogue_record_coverage"
    return "pinned_context_authority_coverage"


def _unique_verdict_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate verdict member")
        value[key] = item
    return value


def _normalize_verdict_v3_transport(raw: str, *, version: Literal["3", "4", "5", "6", "7", "8"] = "3", visible_beats: tuple[str, ...] = ()) -> str:
    """Merge explicit fields only; the unchanged v2 chain decides all authority."""
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_VISIBLE_SOURCE_VERDICT_V2_BYTES:
            raise ValueError("verdict branch transport is not bounded JSON")
        json.loads(raw, object_pairs_hook=_unique_verdict_members)
        model = {
            "3": _ProviderVisibleBeatVerdictWireV3,
            "4": _ProviderVisibleBeatVerdictWireV4,
            "5": _ProviderVisibleBeatVerdictWireV5,
            "6": _ProviderVisibleBeatVerdictWireV6,
            "7": _ProviderVisibleBeatVerdictWireV7,
            "8": _ProviderVisibleBeatVerdictWireV8,
        }[version]
        wire = model.model_validate_json(raw)
    except (ValueError, TypeError, RecursionError):
        raise VisibleSourceClosureWireFailure(
            "schema_invalid", f"visible source verdict v{version} branch transport is invalid",
        ) from None
    decisions = []
    for branch in wire.decisions:
        decision = branch.model_dump(mode="json")
        if isinstance(branch, _ProviderVisibleBeatClosedV3):
            decision["source_ref_indexes"] = [
                decision.pop("first_source_ref_index"),
                *decision.pop("additional_source_ref_indexes"),
            ]
        else:
            decision["source_ref_indexes"] = []
        decisions.append(decision)
    rejections = []
    for item in wire.rejections:
        diagnostic = item.model_dump(mode="json")
        if version in {"6", "7", "8"}:
            if not 0 <= item.beat_index < len(visible_beats):
                raise VisibleSourceClosureWireFailure(
                    "diagnostic_locator_invalid", "rejection index is outside the original Beats",
                )
            diagnostic.update(char_start=0, char_end=len(visible_beats[item.beat_index]))
        rejections.append(diagnostic)
    return json.dumps({
        "contract": VISIBLE_SOURCE_VERDICT_V2_CONTRACT,
        "decisions": decisions,
        "rejections": rejections,
    }, ensure_ascii=False, separators=(",", ":"))


def parse_visible_source_verdict(
    raw: str,
    *,
    version: Literal["1", "2", "3", "4", "5", "6", "7", "8"] = "1",
    visible_beats: tuple[str, ...],
    source_ref_kinds: tuple[str | None, ...],
    source_ref_subject_roles: tuple[str | None, ...] = (),
    source_references: tuple[dict[str, object], ...] | None = None,
) -> ParsedVisibleSourceVerdict:
    """Keep original whole-Beat closure authoritative; diagnostics only explain rejection."""

    _versioned_contract(version)
    if version in {"3", "4", "5", "6", "7", "8"}:
        return parse_visible_source_verdict(
            _normalize_verdict_v3_transport(raw, version=version, visible_beats=visible_beats), version="2",
            visible_beats=visible_beats, source_ref_kinds=source_ref_kinds,
            source_ref_subject_roles=source_ref_subject_roles, source_references=source_references,
        )
    if version == "1":
        return ParsedVisibleSourceVerdict(
            verdict=parse_visible_source_closure(
                raw, visible_beats=visible_beats, source_ref_kinds=source_ref_kinds,
                source_ref_subject_roles=source_ref_subject_roles, source_references=source_references,
            ),
            rejections=(),
        )
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_VISIBLE_SOURCE_VERDICT_V2_BYTES:
            raise ValueError("diagnostic verdict is not bounded JSON")
        # Pydantic's JSON parser otherwise accepts duplicate keys by keeping the
        # last value. Version 2 gives conflicting coordinates no such authority.
        json.loads(raw, object_pairs_hook=_unique_verdict_members)
        provider_wire = _ProviderVisibleBeatVerdictWireV2.model_validate_json(raw)
    except (ValueError, TypeError, RecursionError):
        raise VisibleSourceClosureWireFailure(
            "schema_invalid", "visible source diagnostic verdict wire schema is invalid",
        ) from None
    verdict = parse_visible_source_closure(
        json.dumps({
            "contract": VISIBLE_SOURCE_CLOSURE_CONTRACT,
            "decisions": [decision.model_dump(mode="json") for decision in provider_wire.decisions],
        }, ensure_ascii=False, separators=(",", ":")),
        visible_beats=visible_beats, source_ref_kinds=source_ref_kinds,
        source_ref_subject_roles=source_ref_subject_roles, source_references=source_references,
    )
    rejections = provider_wire.rejections
    indexes = tuple(item.beat_index for item in rejections)
    expected = {item.locator.beat_index for item in verdict.segments if item.decision == "unclosed"}
    if len(set(indexes)) != len(indexes) or set(indexes) != expected:
        raise VisibleSourceClosureWireFailure(
            "diagnostic_coverage_invalid", "diagnostics must cover each unclosed Beat exactly once",
            field="rejections",
        )
    for item in rejections:
        if not 0 <= item.char_start < item.char_end <= len(visible_beats[item.beat_index]):
            raise VisibleSourceClosureWireFailure(
                "diagnostic_locator_invalid",
                "diagnostic span must be nonempty within its original Beat",
                beat_index=item.beat_index, field="rejections.char_start.char_end",
            )
        refs = item.related_source_ref_indexes
        if len(set(refs)) != len(refs) or any(
            index < 0 or index >= len(source_ref_kinds) for index in refs
        ):
            raise VisibleSourceClosureWireFailure(
                "diagnostic_ref_invalid", "diagnostic references must be unique pinned indexes",
                beat_index=item.beat_index, field="rejections.related_source_ref_indexes",
            )
    return ParsedVisibleSourceVerdict(
        verdict=verdict, rejections=tuple(sorted(rejections, key=lambda item: item.beat_index)),
    )


def visible_source_verdict_schema_digest(*, version: Literal["1", "2", "3", "4", "5", "6", "7", "8"] = "1") -> str:
    encoded = json.dumps(
        visible_source_closure_schema(version=version),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def visible_source_verdict_provider_request_contract(
    *, version: Literal["1", "2", "3", "4", "5", "6", "7", "8"] = "1",
) -> dict[str, object]:
    """Compile the one canonical strict-tool request contract for this protocol."""

    contract = _versioned_contract(version)
    tool_name = f"visible_beat_source_verdict_v{version}"
    return {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": ("Return exhaustive factual source verdicts for visible Beats."),
                    "strict": True,
                    "parameters": visible_source_closure_schema(version=version),
                },
            }
        ],
        "tool_choice": {
            "type": "function",
            "function": {"name": tool_name},
        },
        "contract": contract,
        "schema_digest": visible_source_verdict_schema_digest(version=version),
    }


__all__ = [
    "VISIBLE_SOURCE_CLOSURE_CONTRACT",
    "VISIBLE_SOURCE_VERDICT_V2_CONTRACT",
    "VISIBLE_SOURCE_VERDICT_V3_CONTRACT",
    "VISIBLE_SOURCE_VERDICT_V4_CONTRACT",
    "VISIBLE_SOURCE_VERDICT_V5_CONTRACT",
    "VISIBLE_SOURCE_VERDICT_V6_CONTRACT",
    "VISIBLE_SOURCE_VERDICT_V7_CONTRACT",
    "MAX_VISIBLE_SOURCE_PROBLEM_CHARS",
    "MAX_VISIBLE_SOURCE_PROBLEM_JSON_CHARS",
    "MAX_VISIBLE_SOURCE_VERDICT_V2_BYTES",
    "ParsedVisibleSourceVerdict",
    "VisibleSourceRejectionDiagnostic",
    "VisibleSourceClosureWire",
    "VisibleSourceClosureWireFailure",
    "compact_source_reference_table",
    "parse_visible_source_closure",
    "parse_visible_source_verdict",
    "visible_source_closure_messages",
    "visible_source_closure_schema",
    "visible_source_verdict_provider_request_contract",
    "visible_source_verdict_schema_digest",
]
