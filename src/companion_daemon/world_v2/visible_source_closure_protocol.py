"""Compact whole-Beat verdict protocol for correlated factual source review.

The host supplies exact, indexed visible Beats and a pinned source table.  The
reviewer returns one small verdict per Beat; it never copies prose, calculates
offsets, or authors replacement dialogue. This is a review preparation protocol;
ordinary chat does not currently install a semantic reviewer. Readable, eligible
sources do not establish that a model will exhaustively classify the prose.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .context_capsule import ResolvedSourceBinding, source_bindings_hash


VISIBLE_SOURCE_CLOSURE_CONTRACT = "visible-beat-source-verdict.1"

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
# provider schema hand-authored, flat, and immutable: no Pydantic titles,
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


def visible_source_closure_schema() -> dict[str, object]:
    """Return an isolated provider schema for the exact strict-tool wire."""

    return deepcopy(_VISIBLE_BEAT_VERDICT_SCHEMA)


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
                    "support_eligibility": "eligible" if eligible else "baseline_only",
                    "support_subject_ref": support_subject,
                    "support_subject_role": support_role,
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


def visible_source_closure_messages(
    *,
    visible_beats: tuple[str, ...],
    world_claims: tuple[dict[str, object], ...],
    source_references: tuple[dict[str, object], ...],
    invalid_reason: VisibleSourceClosureWireFailure | None = None,
) -> list[dict[str, str]]:
    """Compile one compact request; correction never echoes invalid bytes."""

    if len(visible_beats) > 16:
        raise ValueError("visible source verdict supports at most sixteen Beats")
    packet: dict[str, object] = {
        "output_contract": {
            "contract": VISIBLE_SOURCE_CLOSURE_CONTRACT,
            "authority": "correlated_source_guard_not_character_author",
        },
        "visible_beats": tuple(
            {"beat_index": index, "text": text} for index, text in enumerate(visible_beats)
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
    messages = [
        {"role": "system", "content": _SYSTEM_CONTRACT},
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
                        "correction_contract": "visible-beat-source-verdict-repair.1",
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
                            "contract": VISIBLE_SOURCE_CLOSURE_CONTRACT,
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
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
                source_references[index].get("support_eligibility") != "eligible"
                or not isinstance(source_references[index].get("review_material"), dict)
                or not _review_material(source_references[index]["review_material"])[1]
                for index in refs
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


def visible_source_verdict_schema_digest() -> str:
    encoded = json.dumps(
        visible_source_closure_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def visible_source_verdict_provider_request_contract() -> dict[str, object]:
    """Compile the one canonical strict-tool request contract for this protocol."""

    tool_name = "visible_beat_source_verdict_v1"
    return {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": ("Return exhaustive factual source verdicts for visible Beats."),
                    "strict": True,
                    "parameters": visible_source_closure_schema(),
                },
            }
        ],
        "tool_choice": {
            "type": "function",
            "function": {"name": tool_name},
        },
        "contract": VISIBLE_SOURCE_CLOSURE_CONTRACT,
        "schema_digest": visible_source_verdict_schema_digest(),
    }


__all__ = [
    "VISIBLE_SOURCE_CLOSURE_CONTRACT",
    "VisibleSourceClosureWire",
    "VisibleSourceClosureWireFailure",
    "compact_source_reference_table",
    "parse_visible_source_closure",
    "visible_source_closure_messages",
    "visible_source_closure_schema",
    "visible_source_verdict_provider_request_contract",
    "visible_source_verdict_schema_digest",
]
