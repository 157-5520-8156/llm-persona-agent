"""Independent semantic truth closure for model-authored life development.

The World Author remains free to invent proposal-scoped environmental
possibilities.  This module has a deliberately narrower authority: it checks
only whether material presented as *existing* World truth is entailed by the
exact cited sources, whether factual prose escaped the claim declarations, and
whether an executable typed location contradicts the proposal it coordinates.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator

from .background_context_profile import (
    background_context_profile_for_purpose,
    slice_background_capsule_context,
)
from .context_capsule import ResolvedSourceBinding, source_bindings_hash
from .life_development_draft import (
    LifeDevelopmentCapabilityManifest,
    LifeDevelopmentClaimDeclaration,
    LifeDevelopmentOutcomeDraft,
    LifeDevelopmentPossibilityDraft,
    LifeDevelopmentTimingDraft,
)
from .life_review_identity import (
    SOURCE_BOUND_NOVEL_EVIDENCE_PACKET_CONTRACT,
    novel_origin_evidence_packet_contract,
    GENERAL_EVIDENCE_PACKET_CONTRACT as _GENERAL_EVIDENCE_PACKET_CONTRACT,
    WORLD_CONSEQUENCE_GENERAL_EVIDENCE_PACKET_CONTRACT,
)
from .life_context import LIFE_REVIEW_PROJECTION_CONTRACT
from .schema_core import FrozenModel
from .schemas import ProjectionCursor, WorldEvent
from .world_life_context import BiographicalWorldContextItem, WorldLifeModelContextItem


_REVIEW_CONTRACT = "life-development-source-closure-review.1"
_WORLD_LIFE_ITEM_ADAPTER = TypeAdapter(WorldLifeModelContextItem)
_PRIVACY_RANK = {"shareable": 0, "personal": 1, "private": 2, "withhold": 3}
_NOVEL_ORIGIN_CONTRACT = "life-development-novel-origin-review.5"
_WORLD_CONSEQUENCE_REVIEW_CONTRACT = "life-development-novel-origin-review.6"
_MANIFEST_BINDING_CONTRACT = "life-development-review-manifest-binding.2"
_EXISTING_WORLD_EVIDENCE_CONTRACT = (
    "life-development-novel-origin-existing-world-evidence.1"
)
_OUTCOME_PREREQUISITE_AUTHORITY_KINDS = (
    "retroactive_relationship_or_shared_history",
    "existing_entity_or_fact_masquerading_as_novel",
    "imported_current_or_prior_prerequisite",
    "completed_user_channel_act",
    "character_interior_authorship",
)
# These slices carry existing-world semantics needed to distinguish a genuinely
# novel branch from imported current/prior truth.  Context Capsule is already
# the bounded/ranked retrieval authority.  This transport boundary keeps every
# source-bound item in those slices; it must not introduce a second truncation
# that could hide the last accepted fact or experience.
_NOVEL_ORIGIN_EVIDENCE_SLICES = (
    "character_core",
    "current_situation",
    "recent_dialogue",
    "relationship_slice",
    "open_threads",
    "appraisals",
    "affect_episodes",
    "relevant_facts",
    "recent_experiences",
    "world_life",
    "private_impressions",
    "perception_results",
)


def _canonicalize_unique_string_set(value: object) -> object:
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        if len(value) != len(set(value)):
            raise ValueError("source-closure coordinates must be unique")
        return tuple(sorted(set(value)))
    return value


class LifeDevelopmentTypedLocationConflict(FrozenModel):
    """One exact prose coordinate that contradicts the typed execution place."""

    typed_location_ref: str = Field(min_length=1, max_length=512)
    prose_path: str = Field(min_length=1, max_length=256)
    conflicting_fragment: str = Field(min_length=1, max_length=1_000)


class LifeDevelopmentSourceClosureReview(FrozenModel):
    """One model-authored semantic adjudication with deterministic coordinates."""

    decision: Literal["supported", "unsupported"]
    unsupported_claim_ids: tuple[str, ...] = Field(default=(), max_length=24)
    undeclared_fact_fragments: tuple[str, ...] = Field(default=(), max_length=32)
    undeclared_fact_paths: tuple[str, ...] = Field(default=(), max_length=32)
    typed_location_conflicts: tuple[LifeDevelopmentTypedLocationConflict, ...] = Field(
        default=(),
        max_length=8,
    )
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator(
        "unsupported_claim_ids",
        "undeclared_fact_fragments",
        "undeclared_fact_paths",
        mode="before",
    )
    @classmethod
    def canonicalize_coordinates(cls, value: object) -> object:
        return _canonicalize_unique_string_set(value)

    @field_validator("typed_location_conflicts", mode="before")
    @classmethod
    def tupleize_location_coordinates(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def decision_matches_coordinates(self) -> "LifeDevelopmentSourceClosureReview":
        if any(
            not fragment.strip() for fragment in self.undeclared_fact_fragments
        ):
            raise ValueError("undeclared fact fragments cannot be blank")
        conflict_keys = tuple(
            (
                conflict.typed_location_ref,
                conflict.prose_path,
                conflict.conflicting_fragment,
            )
            for conflict in self.typed_location_conflicts
        )
        if len(conflict_keys) != len(set(conflict_keys)):
            raise ValueError("typed-location conflict coordinates must be unique")
        coordinates = (
            self.unsupported_claim_ids,
            self.undeclared_fact_fragments,
            self.undeclared_fact_paths,
            self.typed_location_conflicts,
        )
        if self.decision == "supported" and any(coordinates):
            raise ValueError("supported review cannot carry rejection coordinates")
        if self.decision == "unsupported" and not any(coordinates):
            raise ValueError("unsupported review requires at least one exact coordinate")
        return self


NovelOriginViolationKind = Literal[
    "retroactive_relationship_or_shared_history",
    "completed_character_experience",
    "completed_user_channel_act",
    "existing_entity_or_fact_masquerading_as_novel",
    "imported_current_or_prior_prerequisite",
    "objective_transition_not_entailed_by_candidate",
]


class LifeDevelopmentNovelOriginClaimFinding(FrozenModel):
    """One exact novel-claim coordinate rejected by the focused critic."""

    claim_id: str = Field(min_length=1, max_length=128)
    violation_kinds: tuple[NovelOriginViolationKind, ...] = Field(
        min_length=1,
        max_length=4,
    )
    exact_fragments: tuple[str, ...] = Field(min_length=1, max_length=8)

    @field_validator("violation_kinds", "exact_fragments", mode="before")
    @classmethod
    def canonicalize_coordinates(cls, value: object) -> object:
        return _canonicalize_unique_string_set(value)

    @model_validator(mode="after")
    def coordinates_are_nonempty(self) -> "LifeDevelopmentNovelOriginClaimFinding":
        if any(not item.strip() for item in self.exact_fragments):
            raise ValueError("novel-origin claim fragments cannot be blank")
        if "completed_user_channel_act" in self.violation_kinds:
            raise ValueError(
                "completed_user_channel_act belongs on outcomes.N.text, not unsupported_claims"
            )
        return self


class LifeDevelopmentNovelOriginNpcFinding(FrozenModel):
    """One exact provisional-NPC coordinate rejected by the focused critic."""

    local_ref: str = Field(min_length=1, max_length=80)
    violation_kinds: tuple[NovelOriginViolationKind, ...] = Field(
        min_length=1,
        max_length=4,
    )
    exact_fragments: tuple[str, ...] = Field(min_length=1, max_length=8)

    @field_validator("violation_kinds", "exact_fragments", mode="before")
    @classmethod
    def canonicalize_coordinates(cls, value: object) -> object:
        return _canonicalize_unique_string_set(value)

    @model_validator(mode="after")
    def coordinates_are_nonempty(self) -> "LifeDevelopmentNovelOriginNpcFinding":
        if any(not item.strip() for item in self.exact_fragments):
            raise ValueError("novel-origin NPC fragments cannot be blank")
        return self


class LifeDevelopmentNovelOriginPlaceFinding(FrozenModel):
    """One exact provisional-place coordinate rejected by the focused critic."""

    local_ref: str = Field(min_length=1, max_length=80)
    violation_kinds: tuple[NovelOriginViolationKind, ...] = Field(
        min_length=1, max_length=4
    )
    exact_fragments: tuple[str, ...] = Field(min_length=1, max_length=8)

    @field_validator("violation_kinds", "exact_fragments", mode="before")
    @classmethod
    def canonicalize_coordinates(cls, value: object) -> object:
        return _canonicalize_unique_string_set(value)

    @model_validator(mode="after")
    def coordinates_are_nonempty(self) -> "LifeDevelopmentNovelOriginPlaceFinding":
        if any(not item.strip() for item in self.exact_fragments):
            raise ValueError("novel-origin place fragments cannot be blank")
        return self


class LifeDevelopmentOutcomePrerequisiteFinding(FrozenModel):
    """One exact outcome fragment outside World Author fact or actor authority."""

    prose_path: str = Field(
        pattern=r"^outcomes\.(0|[1-9][0-9]*)\.text$",
        max_length=256,
    )
    violation_kinds: tuple[
        NovelOriginViolationKind | Literal["character_interior_authorship"], ...
    ] = Field(
        min_length=1,
        max_length=4,
    )
    exact_fragments: tuple[str, ...] = Field(min_length=1, max_length=8)

    @field_validator("violation_kinds", "exact_fragments", mode="before")
    @classmethod
    def canonicalize_coordinates(cls, value: object) -> object:
        return _canonicalize_unique_string_set(value)

    @model_validator(mode="after")
    def coordinates_are_nonempty(
        self,
    ) -> "LifeDevelopmentOutcomePrerequisiteFinding":
        if any(not item.strip() for item in self.exact_fragments):
            raise ValueError("outcome-prerequisite fragments cannot be blank")
        if not set(_OUTCOME_PREREQUISITE_AUTHORITY_KINDS).intersection(self.violation_kinds):
            raise ValueError(
                "outcome-prerequisite findings must identify truth imported from "
                "outside the current proposal branch or an actor-authority violation"
            )
        return self


class LifeDevelopmentObjectiveTransitionFinding(FrozenModel):
    """One exact objective-transition summary rejected as imported history."""

    prose_path: str = Field(
        pattern=(
            r"^outcomes\.(0|[1-9][0-9]*)\."
            r"objective_biographical_transition\.summary$"
        ),
        max_length=256,
    )
    violation_kinds: tuple[NovelOriginViolationKind, ...] = Field(
        min_length=1,
        max_length=4,
    )
    exact_fragments: tuple[str, ...] = Field(min_length=1, max_length=8)

    @field_validator("violation_kinds", "exact_fragments", mode="before")
    @classmethod
    def canonicalize_coordinates(cls, value: object) -> object:
        return _canonicalize_unique_string_set(value)

    @model_validator(mode="after")
    def coordinates_are_nonempty(
        self,
    ) -> "LifeDevelopmentObjectiveTransitionFinding":
        if any(not item.strip() for item in self.exact_fragments):
            raise ValueError("objective-transition fragments cannot be blank")
        return self


class LifeDevelopmentDynamicLifeDirectionFinding(FrozenModel):
    """An exact authored durable-context string, never a local semantic verdict."""

    prose_path: str = Field(
        pattern=(
            r"^outcomes\.(0|[1-9][0-9]*)\.dynamic_life_direction\."
            r"(summary|(context_tags|supersedes_context_tag_prefixes|"
            r"narrative_tags)\.(0|[1-9][0-9]*))$"
        ),
        max_length=256,
    )
    violation_kinds: tuple[
        Literal[
            "imported_current_or_prior_prerequisite",
            "durable_context_not_entailed_by_candidate",
            "character_interior_authorship",
        ], ...
    ] = Field(min_length=1, max_length=3)
    exact_fragments: tuple[str, ...] = Field(min_length=1, max_length=8)

    @field_validator("violation_kinds", "exact_fragments", mode="before")
    @classmethod
    def canonicalize_coordinates(cls, value: object) -> object:
        return _canonicalize_unique_string_set(value)

    @model_validator(mode="after")
    def fragments_are_nonempty(self) -> "LifeDevelopmentDynamicLifeDirectionFinding":
        if any(not item.strip() for item in self.exact_fragments):
            raise ValueError("dynamic-life-direction fragments cannot be blank")
        return self


class LifeDevelopmentNovelOriginReview(FrozenModel):
    """Independent model verdict over novel fact origin, not story quality."""

    decision: Literal["supported", "unsupported"]
    unsupported_claims: tuple[LifeDevelopmentNovelOriginClaimFinding, ...] = Field(
        default=(),
        max_length=24,
    )
    unsupported_provisional_npcs: tuple[
        LifeDevelopmentNovelOriginNpcFinding,
        ...,
    ] = Field(default=(), max_length=16)
    unsupported_provisional_places: tuple[
        LifeDevelopmentNovelOriginPlaceFinding,
        ...,
    ] = Field(default=(), max_length=16)
    unsupported_outcome_prerequisites: tuple[
        LifeDevelopmentOutcomePrerequisiteFinding,
        ...,
    ] = Field(default=(), max_length=8)
    unsupported_objective_transitions: tuple[
        LifeDevelopmentObjectiveTransitionFinding,
        ...,
    ] = Field(default=(), max_length=8)
    unsupported_dynamic_life_directions: tuple[
        LifeDevelopmentDynamicLifeDirectionFinding, ...,
    ] = Field(default=(), max_length=32, exclude_if=lambda value: not value)
    # Reuse the historical slot without changing supported review payloads.
    # `.3` admits exact premise fragments for unsupported truth or character
    # authorship: the installed general closure checks refs, not prose meaning.
    undeclared_premise_fragments: tuple[str, ...] = Field(default=(), max_length=32)
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator(
        "unsupported_claims",
        "unsupported_provisional_npcs",
        "unsupported_provisional_places",
        "unsupported_outcome_prerequisites",
        "unsupported_objective_transitions",
        "unsupported_dynamic_life_directions",
        mode="before",
    )
    @classmethod
    def tupleize_findings(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("undeclared_premise_fragments", mode="before")
    @classmethod
    def canonicalize_premise_coordinates(cls, value: object) -> object:
        return _canonicalize_unique_string_set(value)

    @model_validator(mode="after")
    def decision_matches_coordinates(self) -> "LifeDevelopmentNovelOriginReview":
        if any(not item.strip() for item in self.undeclared_premise_fragments):
            raise ValueError("premise fragments cannot be blank")
        claim_ids = tuple(item.claim_id for item in self.unsupported_claims)
        npc_refs = tuple(item.local_ref for item in self.unsupported_provisional_npcs)
        place_refs = tuple(
            item.local_ref for item in self.unsupported_provisional_places
        )
        outcome_paths = tuple(
            item.prose_path for item in self.unsupported_outcome_prerequisites
        )
        transition_paths = tuple(
            item.prose_path for item in self.unsupported_objective_transitions
        )
        direction_paths = tuple(
            item.prose_path for item in self.unsupported_dynamic_life_directions
        )
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("novel-origin claim findings must be unique")
        if len(npc_refs) != len(set(npc_refs)):
            raise ValueError("novel-origin NPC findings must be unique")
        if len(place_refs) != len(set(place_refs)):
            raise ValueError("novel-origin place findings must be unique")
        if len(outcome_paths) != len(set(outcome_paths)):
            raise ValueError("outcome-prerequisite findings must use unique paths")
        if len(transition_paths) != len(set(transition_paths)):
            raise ValueError("objective-transition findings must use unique paths")
        if len(direction_paths) != len(set(direction_paths)):
            raise ValueError("dynamic-life-direction findings must use unique paths")
        coordinates = (
            self.unsupported_claims,
            self.unsupported_provisional_npcs,
            self.unsupported_provisional_places,
            self.unsupported_outcome_prerequisites,
            self.unsupported_objective_transitions,
            self.unsupported_dynamic_life_directions,
            self.undeclared_premise_fragments,
        )
        if self.decision == "supported" and any(coordinates):
            raise ValueError("supported novel-origin review cannot carry coordinates")
        if self.decision == "unsupported" and not any(coordinates):
            raise ValueError("unsupported novel-origin review requires exact coordinates")
        return self


class LifeDevelopmentWorldConsequenceFinding(LifeDevelopmentOutcomePrerequisiteFinding):
    """An exact authored field in the new carrier, never a legacy text alias."""

    # Keep the full existing vocabulary: supplementary classifications remain
    # legal alongside an authority finding. The legacy text-carrier schema is
    # frozen; only the current carrier publishes this existing parser rule.
    violation_kinds: tuple[
        NovelOriginViolationKind | Literal["character_interior_authorship"], ...
    ] = Field(
        min_length=1,
        max_length=4,
        json_schema_extra={
            "contains": {"enum": list(_OUTCOME_PREREQUISITE_AUTHORITY_KINDS)},
            "uniqueItems": True,
        },
    )
    prose_path: str = Field(
        pattern=(
            r"^outcomes\.(0|[1-9][0-9]*)\.world_consequence\."
            r"(environment_text|authorized_attempt_result\.text)$"
        ),
        max_length=256,
    )


class LifeDevelopmentWorldConsequenceReview(LifeDevelopmentNovelOriginReview):
    """The existing focused verdict with explicitly versioned consequence paths."""

    unsupported_outcome_prerequisites: tuple[LifeDevelopmentWorldConsequenceFinding, ...] = Field(
        default=(), max_length=8
    )


class LifeDevelopmentSourceClosureError(ValueError):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        violations: tuple[dict[str, str], ...] = (),
    ) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.violations = violations


def _unwrap_review_transport_envelope(
    decoded: dict[str, object],
    *,
    error_code: str,
    review_label: str,
) -> dict[str, object]:
    """Decode the current strict-provider envelope without breaking old bytes."""

    if set(decoded) != {"review"}:
        return decoded
    review = decoded["review"]
    if not isinstance(review, dict):
        raise LifeDevelopmentSourceClosureError(
            error_code,
            f"{review_label} transport envelope must contain one review object",
        )
    return review


def _review_output_contract(
    *,
    contract: str,
    review_model: type[FrozenModel],
) -> dict[str, object]:
    """Describe the transport envelope and the semantic verdict invariant."""

    return {
        "contract": contract,
        "transport_envelope": {
            "required_root_key": "review",
            "additional_root_fields": False,
        },
        # Provider strict-schema installation remains the execution boundary.
        # The model-visible copy needs the exact structural keywords, not
        # Pydantic's repeated human-facing titles/descriptions.
        "review_schema": _compact_review_schema(
            review_model.model_json_schema(mode="validation")
        ),
        "decision_coordinate_authority": {
            "supported": "all_rejection_coordinate_arrays_empty",
            "unsupported": "at_least_one_rejection_coordinate_array_non_empty",
        },
    }


def _compact_review_schema(value: object) -> object:
    """Drop annotation-only JSON Schema metadata without changing structure."""

    if isinstance(value, dict):
        return {
            key: _compact_review_schema(item)
            for key, item in value.items()
            if key not in {"title", "description"}
        }
    if isinstance(value, list):
        return [_compact_review_schema(item) for item in value]
    return value


def parse_life_development_source_closure_review(
    *,
    raw: str,
    draft: LifeDevelopmentPossibilityDraft,
) -> LifeDevelopmentSourceClosureReview:
    """Decode one bounded reviewer result without inventing a local verdict."""

    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16_000:
        raise LifeDevelopmentSourceClosureError(
            "invalid_source_closure_output",
            "source-closure review must be bounded JSON text",
        )
    json_text = raw.strip()
    if json_text.startswith("```") and json_text.endswith("```"):
        first_newline = json_text.find("\n")
        opening = json_text[:first_newline].strip().casefold()
        if first_newline > 0 and opening in {"```", "```json"}:
            json_text = json_text[first_newline + 1 : -3].strip()
    try:
        decoded = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise LifeDevelopmentSourceClosureError(
            "invalid_source_closure_json",
            "source-closure review is not valid JSON",
        ) from exc
    if not isinstance(decoded, dict):
        raise LifeDevelopmentSourceClosureError(
            "invalid_source_closure_shape",
            "source-closure review must be one JSON object",
        )
    review_value = _unwrap_review_transport_envelope(
        decoded,
        error_code="invalid_source_closure_shape",
        review_label="source-closure review",
    )
    try:
        review = LifeDevelopmentSourceClosureReview.model_validate_json(
            json.dumps(review_value, ensure_ascii=False, separators=(",", ":"))
        )
    except ValueError as exc:
        detail = "source-closure review violates its exact output contract"
        structured: tuple[dict[str, str], ...] = ()
        if isinstance(exc, ValidationError):
            violations = []
            machine = []
            for error in exc.errors(include_url=False, include_input=False):
                location = ".".join(str(part) for part in error["loc"]) or "<root>"
                violations.append(f"{location}: {error['msg']} [{error['type']}]")
                machine.append(
                    {
                        "path": location,
                        "message": str(error["msg"]),
                        "type": str(error["type"]),
                    }
                )
            if violations:
                detail = f"{detail}: {'; '.join(violations)}"
                structured = tuple(machine)
        raise LifeDevelopmentSourceClosureError(
            "invalid_source_closure_shape",
            detail[:8_000],
            violations=structured,
        ) from exc
    declared = {
        claim.claim_id
        for claim in draft.claim_declarations
        if claim.scope == "existing_world"
    }
    unknown = tuple(sorted(set(review.unsupported_claim_ids) - declared))
    if unknown:
        raise LifeDevelopmentSourceClosureError(
            "unknown_source_closure_claim",
            "unsupported_claim_ids contains ids absent from the reviewed draft: "
            + ", ".join(unknown),
        )
    prose = _general_source_prose_coordinates(draft)
    unknown_paths = tuple(
        path for path in review.undeclared_fact_paths if path not in prose
    )
    if unknown_paths:
        raise LifeDevelopmentSourceClosureError(
            "unknown_source_closure_path",
            "undeclared_fact_paths contains paths absent from the reviewed prose: "
            + ", ".join(unknown_paths),
        )
    missing_fragments = tuple(
        fragment
        for fragment in review.undeclared_fact_fragments
        if not any(fragment in value for value in prose.values())
    )
    exact_fragments = tuple(
        fragment
        for fragment in review.undeclared_fact_fragments
        if fragment not in missing_fragments
    )
    for conflict in review.typed_location_conflicts:
        if (
            draft.location_ref is None
            or conflict.typed_location_ref != draft.location_ref
        ):
            raise LifeDevelopmentSourceClosureError(
                "unknown_typed_location_coordinate",
                "typed-location conflict does not bind the draft's actual location_ref",
            )
        value = _typed_location_prose_coordinates(draft).get(
            conflict.prose_path
        )
        if value is None or conflict.conflicting_fragment not in value:
            raise LifeDevelopmentSourceClosureError(
                "unknown_typed_location_coordinate",
                "typed-location conflict path/fragment is absent from the reviewed prose",
            )
    if missing_fragments:
        has_exact_rejection_coordinate = bool(
            review.unsupported_claim_ids
            or exact_fragments
            or review.undeclared_fact_paths
            or review.typed_location_conflicts
        )
        if not has_exact_rejection_coordinate:
            raise LifeDevelopmentSourceClosureError(
                "unknown_source_closure_fragment",
                "undeclared_fact_fragments contains text absent from the reviewed prose: "
                + ", ".join(missing_fragments),
            )
        review = review.model_copy(
            update={"undeclared_fact_fragments": exact_fragments}
        )
    return review


def novel_origin_review_tool_contract(
    draft: LifeDevelopmentPossibilityDraft,
) -> dict[str, object]:
    """Enforce the existing review object's transport, never its verdict.

    The logical review request already carries this canonical model's schema.
    Provider usage/capture retains the actual tool-bearing HTTP request too.
    Historical message compilation and parsing remain unchanged.
    """
    from .character_interior.structured_role_tool_contract import _provider_schema
    from .character_interior.inbound_tool_contract import deepseek_strict_tool_schema

    def require_fields(value):
        if isinstance(value, list):
            return [require_fields(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: require_fields(item) for key, item in value.items()}
        if isinstance(result.get("properties"), dict):
            result["required"] = list(result["properties"])
        return result

    # Canonical default-empty finding arrays must remain arrays, not the
    # nullable placeholders used by optional role-result union branches.
    review_schema = require_fields(_provider_schema(_novel_review_model(draft)))
    parameters = deepseek_strict_tool_schema({
        "type": "object", "properties": {"review": review_schema},
        "required": ["review"], "additionalProperties": False,
    })
    name = "life_novel_origin_review_v1"
    return {
        "tools": [{"type": "function", "function": {
            "name": name, "strict": True,
            "description": "Return one complete source-origin review using the supplied evidence.",
            "parameters": parameters,
        }}],
        "tool_choice": {"type": "function", "function": {"name": name}},
    }


def parse_life_development_novel_origin_review(
    *,
    raw: str,
    draft: LifeDevelopmentPossibilityDraft,
) -> LifeDevelopmentNovelOriginReview:
    """Decode a focused novel-origin verdict and verify every model coordinate."""

    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16_000:
        raise LifeDevelopmentSourceClosureError(
            "invalid_novel_origin_output",
            "novel-origin review must be bounded JSON text",
        )
    json_text = raw.strip()
    if json_text.startswith("```") and json_text.endswith("```"):
        first_newline = json_text.find("\n")
        opening = json_text[:first_newline].strip().casefold()
        if first_newline > 0 and opening in {"```", "```json"}:
            json_text = json_text[first_newline + 1 : -3].strip()
    try:
        decoded = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise LifeDevelopmentSourceClosureError(
            "invalid_novel_origin_json",
            "novel-origin review is not valid JSON",
        ) from exc
    if not isinstance(decoded, dict):
        raise LifeDevelopmentSourceClosureError(
            "invalid_novel_origin_shape",
            "novel-origin review must be one JSON object",
        )
    review_value = _unwrap_review_transport_envelope(
        decoded,
        error_code="invalid_novel_origin_shape",
        review_label="novel-origin review",
    )
    try:
        review = _novel_review_model(draft).model_validate_json(
            json.dumps(review_value, ensure_ascii=False, separators=(",", ":"))
        )
    except ValueError as exc:
        detail = "novel-origin review violates its exact output contract"
        structured: tuple[dict[str, str], ...] = ()
        if isinstance(exc, ValidationError):
            violations = []
            machine = []
            for error in exc.errors(include_url=False, include_input=False):
                location = ".".join(str(part) for part in error["loc"]) or "<root>"
                violations.append(f"{location}: {error['msg']} [{error['type']}]")
                machine.append(
                    {
                        "path": location,
                        "message": str(error["msg"]),
                        "type": str(error["type"]),
                    }
                )
            if violations:
                detail = f"{detail}: {'; '.join(violations)}"
                structured = tuple(machine)
        raise LifeDevelopmentSourceClosureError(
            "invalid_novel_origin_shape",
            detail[:8_000],
            violations=structured,
        ) from exc

    if any(fragment not in draft.premise for fragment in review.undeclared_premise_fragments):
        raise LifeDevelopmentSourceClosureError(
            "unknown_novel_origin_premise_fragment",
            "focused critic premise fragment is absent from the exact premise",
        )

    novel_claims = {
        item.claim_id: item.summary
        for item in draft.claim_declarations
        if item.scope == "novel_world_generation"
    }
    for finding in review.unsupported_claims:
        summary = novel_claims.get(finding.claim_id)
        if summary is None:
            raise LifeDevelopmentSourceClosureError(
                "unknown_novel_origin_claim",
                "focused critic claim finding is absent from novel declarations",
            )
        if any(fragment not in summary for fragment in finding.exact_fragments):
            raise LifeDevelopmentSourceClosureError(
                "unknown_novel_origin_claim_fragment",
                "focused critic claim fragment is absent from the exact claim summary",
            )

    npc_summaries: dict[str, list[str]] = {}
    for outcome in draft.outcomes:
        for npc in outcome.provisional_npcs:
            npc_summaries.setdefault(npc.local_ref, []).append(npc.summary)
    for finding in review.unsupported_provisional_npcs:
        summaries = npc_summaries.get(finding.local_ref)
        if summaries is None:
            raise LifeDevelopmentSourceClosureError(
                "unknown_novel_origin_npc",
                "focused critic NPC finding is absent from provisional NPCs",
            )
        if any(
            not any(fragment in summary for summary in summaries)
            for fragment in finding.exact_fragments
        ):
            raise LifeDevelopmentSourceClosureError(
                "unknown_novel_origin_npc_fragment",
                "focused critic NPC fragment is absent from the exact NPC summaries",
            )
    place_summaries: dict[str, list[str]] = {}
    for outcome in draft.outcomes:
        for place in outcome.provisional_places:
            place_summaries.setdefault(place.local_ref, []).append(place.summary)
    for finding in review.unsupported_provisional_places:
        summaries = place_summaries.get(finding.local_ref)
        if summaries is None:
            raise LifeDevelopmentSourceClosureError(
                "unknown_novel_origin_place",
                "focused critic place finding is absent from provisional places",
            )
        if any(
            not any(fragment in summary for summary in summaries)
            for fragment in finding.exact_fragments
        ):
            raise LifeDevelopmentSourceClosureError(
                "unknown_novel_origin_place_fragment",
                "focused critic place fragment is absent from exact place summaries",
            )
    outcome_text = _outcome_prose_coordinates(draft)
    for finding in review.unsupported_outcome_prerequisites:
        text = outcome_text.get(finding.prose_path)
        if text is None:
            raise LifeDevelopmentSourceClosureError(
                "unknown_novel_origin_outcome_path",
                "focused critic outcome path is absent from the reviewed draft",
            )
        if any(fragment not in text for fragment in finding.exact_fragments):
            raise LifeDevelopmentSourceClosureError(
                "unknown_novel_origin_outcome_fragment",
                "focused critic outcome fragment is absent from the exact outcome text",
            )
    transition_summaries = {
        f"outcomes.{index}.objective_biographical_transition.summary": (
            outcome.objective_biographical_transition.summary
        )
        for index, outcome in enumerate(draft.outcomes)
        if outcome.objective_biographical_transition is not None
    }
    for finding in review.unsupported_objective_transitions:
        summary = transition_summaries.get(finding.prose_path)
        if summary is None:
            raise LifeDevelopmentSourceClosureError(
                "unknown_objective_transition_path",
                "focused critic transition path is absent from the reviewed draft",
            )
        if any(fragment not in summary for fragment in finding.exact_fragments):
            raise LifeDevelopmentSourceClosureError(
                "unknown_objective_transition_fragment",
                "focused critic transition fragment is absent from the exact summary",
            )
    direction_strings = _dynamic_life_direction_coordinates(draft)
    for finding in review.unsupported_dynamic_life_directions:
        text = direction_strings.get(finding.prose_path)
        if text is None:
            raise LifeDevelopmentSourceClosureError(
                "unknown_dynamic_life_direction_path",
                "focused critic direction path is absent from the reviewed draft",
            )
        if any(fragment not in text for fragment in finding.exact_fragments):
            raise LifeDevelopmentSourceClosureError(
                "unknown_dynamic_life_direction_fragment",
                "focused critic fragment is absent from the exact direction field",
            )
    return review


def _uses_world_consequence(draft: LifeDevelopmentPossibilityDraft) -> bool:
    return any(outcome.world_consequence is not None for outcome in draft.outcomes)


def _novel_review_model(draft: LifeDevelopmentPossibilityDraft):
    return (
        LifeDevelopmentWorldConsequenceReview
        if _uses_world_consequence(draft)
        else LifeDevelopmentNovelOriginReview
    )


def _novel_review_contract(draft: LifeDevelopmentPossibilityDraft) -> str:
    return (
        _WORLD_CONSEQUENCE_REVIEW_CONTRACT
        if _uses_world_consequence(draft)
        else _NOVEL_ORIGIN_CONTRACT
    )


def _outcome_prose_coordinates(draft: LifeDevelopmentPossibilityDraft) -> dict[str, str]:
    return {
        f"outcomes.{index}.{path}": text
        for index, outcome in enumerate(draft.outcomes)
        for path, text in outcome.prose_fields.items()
    }


def _outcome_prose_surface(outcome: LifeDevelopmentOutcomeDraft) -> dict[str, object]:
    if outcome.world_consequence is None:
        return {"text": outcome.text}
    return {"world_consequence": outcome.world_consequence.model_dump(mode="json")}


def _required_execution_authority(value: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("world-consequence review requires the original execution_authority")
    return value


def _completed_lifecycle_guidance(*, manifest, execution_authority) -> str:
    """Explain only the new, audit-verified reading; old requests stay exact."""
    completion = manifest.completed_activity_consequence
    expected = completion.lifecycle_reading if completion is not None else None
    supplied = (
        execution_authority.get("completed_activity_lifecycle")
        if isinstance(execution_authority, dict) else None
    )
    if expected is None:
        if supplied is not None:
            raise ValueError("lifecycle reading is absent from the original manifest")
        return ""
    if supplied != expected.model_dump(mode="json"):
        raise ValueError("lifecycle reading differs from the original verified author evidence")
    return (
        "\nexecution_authority.completed_activity_lifecycle is an original-pin-verified "
        "reading of two transitions of the same owned Plan. plan_id identifies the "
        "activity; event_ref and transition_ref identify separate changes, so different "
        "transition identifiers do not imply different activities. Each expected_plan_revision "
        "is the state before its own transition, and resulting_plan_revision is the state "
        "after it; successive revisions are expected. This reading supports only the "
        "recorded execution start/resumption, later lifecycle end, owner, identity, order "
        "and times. It can support a lifecycle-only claim citing those bound events. "
        "It proves no concrete intended action happened or succeeded, no location presence, "
        "embedded history, emotion or completed Experience. Continue to review every such "
        "additional claim against its own authority, including new candidate consequence prose."
    )


def _world_consequence_actor_boundary() -> str:
    return (
        "Inspect every world_consequence.environment_text and optional "
        "world_consequence.authorized_attempt_result.text field. Environment prose "
        "may create external changes, never a new companion action, choice or "
        "subjective response. An authorized_attempt_result may describe only the "
        "objective result of the exact earlier attempt in execution_authority and "
        "its matching source-bound material. This packet pairs the original authority "
        "with execution_materials; each readable item needs its matching binding. "
        "An execution_binding cannot authorize "
        "a new companion action, extend the attempt, or grant motivation, thoughts "
        "or feelings. Its exact attempt may have a candidate objective success or "
        "failure, but an ActivityStarted is not prior proof of success. Opaque refs "
        "and hashes prove no unstated action. A future Plan, "
        "sibling candidate or later outcome-token selection cannot supply missing "
        "pre-author execution authority. The Character Model authors any new "
        "response separately. Report unauthorized companion action, choice or "
        "interior as character_interior_authorship in unsupported_outcome_prerequisites. "
        "Use the exact supplied outcomes.N.world_consequence.environment_text or "
        "outcomes.N.world_consequence.authorized_attempt_result.text path and a "
        "verbatim fragment from that one field. A structured label never proves "
        "that its prose respects this boundary. "
    )


def _dynamic_life_direction_coordinates(
    draft: LifeDevelopmentPossibilityDraft,
) -> dict[str, str]:
    coordinates: dict[str, str] = {}
    for index, outcome in enumerate(draft.outcomes):
        direction = outcome.dynamic_life_direction
        if direction is None:
            continue
        prefix = f"outcomes.{index}.dynamic_life_direction"
        coordinates[f"{prefix}.summary"] = direction.summary
        for field in ("context_tags", "supersedes_context_tag_prefixes", "narrative_tags"):
            for item_index, text in enumerate(getattr(direction, field)):
                coordinates[f"{prefix}.{field}.{item_index}"] = text
    return coordinates


def _general_source_prose_coordinates(
    draft: LifeDevelopmentPossibilityDraft,
) -> dict[str, str]:
    """Return prose over which the general reviewer has negative authority."""

    values: dict[str, str] = {"premise": draft.premise}
    for outcome_index, outcome in enumerate(draft.outcomes):
        prefix = f"outcomes.{outcome_index}"
        for npc_index, npc in enumerate(outcome.provisional_npcs):
            values[f"{prefix}.provisional_npcs.{npc_index}.summary"] = npc.summary
        for place_index, place in enumerate(outcome.provisional_places):
            values[f"{prefix}.provisional_places.{place_index}.summary"] = place.summary
        if outcome.objective_biographical_transition is not None:
            values[f"{prefix}.objective_biographical_transition.summary"] = (
                outcome.objective_biographical_transition.summary
            )
        visual = outcome.visual_evidence
        if visual is None:
            continue
        if visual.activity_description is not None:
            values[f"{prefix}.visual_evidence.activity_description"] = (
                visual.activity_description
            )
        if visual.location is not None:
            for field in (
                "location_ref",
                "kind",
                "country",
                "region",
                "city",
                "publicness",
            ):
                value = getattr(visual.location, field)
                if value is not None:
                    values[f"{prefix}.visual_evidence.location.{field}"] = value
        if visual.environment is not None:
            for field in ("light", "weather", "structure", "region"):
                value = getattr(visual.environment, field)
                if value is not None:
                    values[f"{prefix}.visual_evidence.environment.{field}"] = value
        for object_index, item in enumerate(visual.objects):
            values[f"{prefix}.visual_evidence.objects.{object_index}.kind"] = item.kind
            values[
                f"{prefix}.visual_evidence.objects.{object_index}.description"
            ] = item.description
    return values


def _typed_location_prose_coordinates(
    draft: LifeDevelopmentPossibilityDraft,
) -> dict[str, str]:
    """Expose semantic execution prose only to the typed-location boundary."""

    return {
        **_general_source_prose_coordinates(draft),
        **_outcome_prose_coordinates(draft),
    }


def _review_manifest_binding(
    *,
    context: dict[str, object],
    manifest: LifeDevelopmentCapabilityManifest,
    draft: LifeDevelopmentPossibilityDraft,
    review_lane: Literal["general", "focused"],
) -> dict[str, object]:
    """Bind the exact reviewed capabilities without sending the whole menu.

    The parser has already checked that every selected typed capability belongs
    to this immutable manifest.  A reviewer needs the selected location, the
    existing-entity inventory and the full manifest identity; unrelated places,
    scheduling affordances and policy limits cannot prove any claim in this
    draft and only increase transport latency.
    """

    selected_locations = ()
    descriptor: dict[str, object] | None = None
    if review_lane == "general":
        has_location = draft.location_ref is not None
        if has_location != (draft.location_capability_ref is not None):
            raise ValueError(
                "reviewed location requires both location_ref and capability_ref"
            )
        selected_locations = tuple(
            item
            for item in manifest.location_capabilities
            if draft.location_ref is not None
            and item.location_ref == draft.location_ref
            and draft.location_capability_ref is not None
            and item.capability_ref == draft.location_capability_ref
        )
        expected_location_count = int(has_location)
        if len(selected_locations) != expected_location_count:
            raise ValueError(
                "review packet requires exactly the selected location capability"
            )
        descriptor = _selected_location_descriptor_evidence(
            context=context,
            location_ref=draft.location_ref,
        )
    known_entity_index = []
    for entity_ref in manifest.entity_refs:
        descriptor_evidence = _entity_descriptor_evidence(
            context=context,
            entity_ref=entity_ref,
        )
        rendered_evidence = (
            descriptor_evidence
            if review_lane == "general"
            else [
                {
                    "slice": evidence["slice"],
                    "item_ref": evidence["item"].get("item_ref"),
                    "value_hash": evidence["item"].get("value_hash"),
                    "source_hash": evidence["item"].get("source_hash"),
                }
                for evidence in descriptor_evidence
            ]
        )
        known_entity_index.append(
            {
                "entity_ref": entity_ref,
                "descriptor_status": (
                    "source_bound_exact_ref_join"
                    if descriptor_evidence
                    else "opaque_ref_only"
                ),
                "descriptor_evidence": rendered_evidence,
                "absence_is_not_evidence": True,
            }
        )
    binding = {
        "contract": _MANIFEST_BINDING_CONTRACT,
        "manifest_hash": manifest.manifest_hash,
        "owner_actor_ref": manifest.owner_actor_ref,
        "pinned_cursor": manifest.pinned_cursor.model_dump(mode="json"),
        # The manifest supplies only the authorized refs.  A descriptor is
        # added solely when a pinned, source-bound Context item contains that
        # exact ref.  There is no name-field guess, alias heuristic or substring
        # match here; a missing join is therefore explicitly non-evidence.
        "authorized_entity_refs": list(manifest.entity_refs),
        "known_entity_index": known_entity_index,
        "known_entity_index_scope": (
            (
                "non_exhaustive_exact_ref_join_inline; "
                if review_lane == "general"
                else "non_exhaustive_exact_ref_pointer_into_existing_evidence; "
            )
            + "opaque_without_source_bound_match; "
            "absence_is_not_evidence_of_novelty"
        ),
    }
    if review_lane == "general":
        binding["selected_location_capabilities"] = [
            item.model_dump(mode="json")
            for item in selected_locations
        ]
        binding["selected_location_descriptor"] = descriptor
    return binding


def _context_identity(context: dict[str, object]) -> dict[str, object]:
    """Keep immutable capsule identity while omitting non-authoritative bulk."""

    names = (
        "snapshot_id",
        "snapshot_hash",
        "world_id",
        "world_revision",
        "deliberation_revision",
        "ledger_sequence",
        "logical_time",
        "trigger_ref",
    )
    identity = {name: context[name] for name in names if name in context}
    cursor = {
        name: context[name]
        for name in ("world_revision", "deliberation_revision", "ledger_sequence")
        if name in context
    }
    if cursor:
        identity["cursor"] = cursor
    return identity


def _selected_location_descriptor_evidence(
    *,
    context: dict[str, object],
    location_ref: str | None,
) -> dict[str, object] | None:
    """Return a source-bound descriptor when Context actually carries one.

    Current production Context generally exposes only the opaque location ref,
    privacy and scene coordinates—not a canonical human name/city/kind.  The
    packet says so explicitly rather than letting a reviewer treat the ref as
    semantic evidence it does not contain.
    """

    if location_ref is None:
        return None
    slices = context.get("slices")
    current = slices.get("current_situation") if isinstance(slices, dict) else None
    items = current.get("items") if isinstance(current, dict) else None
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            location = value.get("location_slice") if isinstance(value, dict) else None
            if not isinstance(location, dict) or location.get("location_ref") != location_ref:
                continue
            compact_item = _compact_source_bound_item(item)
            source_bound = (
                compact_item is not None
                and compact_item.get("authority_scope")
                == "exact_source_bound_existing_truth"
            )
            descriptor_fields = {
                name: location[name]
                for name in (
                    "location_ref",
                    "canonical_name",
                    "aliases",
                    "city",
                    "kind",
                    "publicness",
                    "privacy_class",
                    "zone_ref",
                    "scene_visibility",
                )
                if name in location and (name == "location_ref" or source_bound)
            }
            has_semantic_descriptor = any(
                name in descriptor_fields
                for name in ("canonical_name", "aliases", "city", "kind", "publicness")
            )
            return {
                "scope": (
                    "canonical_descriptor"
                    if has_semantic_descriptor
                    else "ref_level_only"
                ),
                "descriptor": descriptor_fields,
                "item_ref": (
                    compact_item.get("item_ref") if source_bound else None
                ),
                "value_hash": (
                    compact_item.get("value_hash") if source_bound else None
                ),
                "source_hash": (
                    compact_item.get("source_hash") if source_bound else None
                ),
                "source_bindings": (
                    compact_item.get("source_bindings", []) if source_bound else []
                ),
                "absence_is_not_evidence": True,
            }
    return {
        "scope": "ref_level_only",
        "descriptor": {"location_ref": location_ref},
        "item_ref": None,
        "value_hash": None,
        "source_hash": None,
        "source_bindings": [],
        "absence_is_not_evidence": True,
    }


def _compact_source_bound_item(item: object) -> dict[str, object] | None:
    """Retain semantic value plus its exact capsule/source identity only."""

    if not isinstance(item, dict) or "value" not in item:
        return None
    review_value_hash = _canonical_hash(item["value"])
    capsule_item_value_hash = item.get("value_hash")
    compact = {
        name: item[name]
        for name in (
            "item_ref",
            "source_hash",
            "source_bindings",
            "value",
        )
        if name in item
    }
    source_bindings_value = compact.get("source_bindings")
    if source_bindings_value is not None and not isinstance(
        source_bindings_value,
        (list, tuple),
    ):
        raise ValueError("review item source_bindings must be an array")
    if source_bindings_value:
        try:
            source_bindings = tuple(
                ResolvedSourceBinding.model_validate(binding)
                for binding in source_bindings_value
            )
        except ValidationError as exc:
            raise ValueError(
                "source-bound review item has invalid source bindings"
            ) from exc
        binding_identities = tuple(
            (
                binding.source_kind,
                binding.authority_type,
                binding.ref,
                binding.source_world_revision,
                binding.immutable_hash,
            )
            for binding in source_bindings
        )
        if binding_identities != tuple(sorted(set(binding_identities))):
            raise ValueError(
                "source-bound review item bindings must be unique and sorted"
            )
        if not isinstance(compact.get("item_ref"), str) or not compact["item_ref"]:
            raise ValueError("source-bound review item requires an item_ref")
        if capsule_item_value_hash != review_value_hash:
            raise ValueError(
                "source-bound review item value_hash does not bind its value"
            )
        if compact.get("source_hash") != source_bindings_hash(source_bindings):
            raise ValueError(
                "source-bound review item source_hash does not bind its sources"
            )
        compact["source_bindings"] = [
            binding.model_dump(mode="json") for binding in source_bindings
        ]
        compact["value_hash"] = review_value_hash
        compact["authority_scope"] = "exact_source_bound_existing_truth"
        return compact
    # Some model-view slices (notably compact dialogue) retain only item/value
    # identity, while the displayed value may itself be a projection of the
    # original CapsuleItem. Keep the two hashes semantically distinct. These
    # bytes may alert the critic but cannot prove a claim.
    if not isinstance(compact.get("item_ref"), str):
        return None
    capsule_item_source_hash = compact.pop("source_hash", None)
    compact["capsule_item_source_hash"] = (
        capsule_item_source_hash
        if isinstance(capsule_item_source_hash, str)
        else None
    )
    compact["capsule_item_value_hash"] = (
        capsule_item_value_hash
        if isinstance(capsule_item_value_hash, str)
        else None
    )
    compact["review_value_hash"] = review_value_hash
    compact["authority_scope"] = "capsule_bound_reviewer_baseline_only"
    return compact


def _contains_exact_ref(value: object, *, ref: str) -> bool:
    """Match a canonical ref by value equality, never by prose heuristics."""

    if isinstance(value, str):
        return value == ref
    if isinstance(value, dict):
        return any(_contains_exact_ref(item, ref=ref) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_exact_ref(item, ref=ref) for item in value)
    return False


def _entity_descriptor_evidence(
    *,
    context: dict[str, object],
    entity_ref: str,
) -> list[dict[str, object]]:
    """Join one manifest entity to exact source-bound Context evidence.

    This is a deterministic structural join.  It does not decide whether the
    evidence describes a name, alias, relationship, status or any other
    semantic property; that remains the independent reviewer's job.
    """

    slices = context.get("slices")
    if not isinstance(slices, dict):
        return []
    evidence: list[dict[str, object]] = []
    for slice_name in _NOVEL_ORIGIN_EVIDENCE_SLICES:
        slice_value = slices.get(slice_name)
        if (
            not isinstance(slice_value, dict)
            or slice_value.get("availability") != "available"
        ):
            continue
        items = slice_value.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            source_bindings = item.get("source_bindings")
            if not isinstance(source_bindings, list) or not source_bindings:
                continue
            if not (
                _contains_exact_ref(item.get("value"), ref=entity_ref)
                or _contains_exact_ref(source_bindings, ref=entity_ref)
            ):
                continue
            compact = _compact_source_bound_item(item)
            if (
                compact is not None
                and compact.get("authority_scope")
                == "exact_source_bound_existing_truth"
            ):
                evidence.append(
                    {
                        "slice": slice_name,
                        "item": compact,
                    }
                )
    return evidence


def _novel_origin_existing_world_evidence(
    context: dict[str, object],
) -> dict[str, object]:
    """Compile bounded existing truth for the focused origin critic.

    This is evidence selection, not a verdict: fixed source domains and the
    Context compiler's existing rank order decide what is transported.  No
    keyword, motive, mood or story-quality rule is applied here.
    """

    slices = context.get("slices")
    compact_slices: dict[str, list[dict[str, object]]] = {}
    if isinstance(slices, dict):
        for name in _NOVEL_ORIGIN_EVIDENCE_SLICES:
            value = slices.get(name)
            if not isinstance(value, dict) or value.get("availability") != "available":
                continue
            items = value.get("items")
            if not isinstance(items, list):
                continue
            compact_items = tuple(
                compact
                for item in items
                if (compact := _compact_source_bound_item(item)) is not None
            )
            if compact_items:
                compact_slices[name] = list(compact_items)
    return {
        "contract": _EXISTING_WORLD_EVIDENCE_CONTRACT,
        "context_binding": _context_identity(context),
        "slices": compact_slices,
        "selection_policy": {
            "source_domains": list(_NOVEL_ORIGIN_EVIDENCE_SLICES),
            "order": "pinned_context_rank_order",
            "item_budget": "all_items_already_bounded_by_pinned_context_capsule",
            "verdict_authority": "none",
            "coverage": "non_exhaustive",
            "absence_is_not_evidence": True,
            "outer_capsule_caveat": (
                "this_is_a_bounded_model_view; capsule_id_and_model_content_hash_"
                "remain_the_outer_audit_authority"
            ),
            "active_memory_candidates": {
                "included": False,
                "authority": "non_authoritative_candidate",
                "absence_is_not_evidence": True,
            },
        },
    }


def _general_reviewed_surface(
    draft: LifeDevelopmentPossibilityDraft,
) -> dict[str, object]:
    """Expose only prose/coordinates owned by the general reviewer."""

    return {
        "authored_subject_ref": draft.authored_subject_ref,
        "premise": draft.premise,
        "premise_claim_refs": list(draft.premise_claim_refs),
        "claim_declarations": [item.model_dump(mode="json") for item in draft.claim_declarations],
        "location_ref": draft.location_ref,
        "location_capability_ref": draft.location_capability_ref,
        "entity_refs": list(draft.entity_refs),
        "outcomes": [
            {
                "experienced_by_ref": outcome.experienced_by_ref,
                # Outcome text is visible here only for typed-location
                # consistency; it has no general undeclared-fact coordinate.
                **(_outcome_prose_surface(outcome) if draft.location_ref is not None else {}),
                "claim_refs": list(outcome.claim_refs),
                "provisional_npcs": [
                    npc.model_dump(mode="json") for npc in outcome.provisional_npcs
                ],
                "provisional_places": [
                    place.model_dump(mode="json") for place in outcome.provisional_places
                ],
                "objective_biographical_transition": (
                    outcome.objective_biographical_transition.model_dump(mode="json")
                    if outcome.objective_biographical_transition is not None
                    else None
                ),
                "visual_evidence": (
                    outcome.visual_evidence.model_dump(mode="json")
                    if outcome.visual_evidence is not None
                    else None
                ),
            }
            for outcome in draft.outcomes
        ],
    }


def _novel_origin_reviewed_surface(
    draft: LifeDevelopmentPossibilityDraft,
) -> dict[str, object]:
    """Expose the whole current premise as well as exact novel coordinates."""

    return {
        "authored_subject_ref": draft.authored_subject_ref,
        "premise": draft.premise,
        "premise_claim_refs": list(draft.premise_claim_refs),
        "claim_declarations": [item.model_dump(mode="json") for item in draft.claim_declarations],
        "entity_refs": list(draft.entity_refs),
        "outcomes": [
            {
                **_outcome_prose_surface(outcome),
                "user_channel_completion": outcome.user_channel_completion,
                "dynamic_life_direction": (
                    outcome.dynamic_life_direction.model_dump(mode="json")
                    if outcome.dynamic_life_direction is not None
                    else None
                ),
                "claim_refs": list(outcome.claim_refs),
                "provisional_npcs": [
                    {
                        "local_ref": npc.local_ref,
                        "summary": npc.summary,
                    }
                    for npc in outcome.provisional_npcs
                ],
                "provisional_places": [
                    {"local_ref": place.local_ref, "summary": place.summary}
                    for place in outcome.provisional_places
                ],
                "objective_biographical_transition": (
                    outcome.objective_biographical_transition.model_dump(mode="json")
                    if outcome.objective_biographical_transition is not None
                    else None
                ),
            }
            for outcome in draft.outcomes
        ],
    }


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _evidence_packet_binding(
    *,
    contract: str,
    reviewed_surface: dict[str, object],
    pinned_authority: dict[str, object],
) -> dict[str, str]:
    return {
        "contract": contract,
        "packet_hash": _canonical_hash(
            {
                "contract": contract,
                "reviewed_surface": reviewed_surface,
                "pinned_authority": pinned_authority,
            }
        ),
    }


def life_development_review_packet_identity(
    messages: list[dict[str, str]],
) -> tuple[str, str]:
    """Read the canonical evidence identity from a compiled reviewer request."""

    if len(messages) < 2 or messages[-1].get("role") != "user":
        raise ValueError("life review messages have no evidence packet")
    try:
        request = json.loads(messages[-1]["content"])
        binding = request["evidence_packet_binding"]
        contract = binding["contract"]
        packet_hash = binding["packet_hash"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("life review evidence packet binding is malformed") from exc
    if (
        not isinstance(contract, str)
        or not contract
        or not isinstance(packet_hash, str)
        or len(packet_hash) != 64
    ):
        raise ValueError("life review evidence packet identity is malformed")
    return contract, packet_hash


def _iter_pinned_context_items(
    context: dict[str, object],
) -> "list[tuple[str, dict[str, object]]]":
    """Enumerate (slice_name, item) pairs already exposed to the World Author."""

    slices = context.get("slices") if isinstance(context, dict) else None
    nested = context.get("pinned_world_context") if isinstance(context, dict) else None
    if isinstance(nested, dict) and isinstance(nested.get("slices"), (dict, list)):
        slices = nested.get("slices")
    pairs: list[tuple[str, dict[str, object]]] = []
    if isinstance(slices, dict):
        iterable = slices.items()
    elif isinstance(slices, list):
        iterable = (
            (str(item.get("slice") or item.get("name") or index), item)
            for index, item in enumerate(slices)
            if isinstance(item, dict)
        )
    else:
        iterable = ()
    for name, slice_value in iterable:
        if not isinstance(slice_value, dict):
            continue
        items = slice_value.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    pairs.append((str(name), item))
    return pairs


def _legacy_item_matches_ref(item: dict[str, object], ref: str) -> bool:
    if item.get("item_ref") == ref or item.get("source_ref") == ref:
        return True
    bindings = item.get("source_bindings")
    if isinstance(bindings, list):
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            if ref in {
                binding.get("ref"),
                binding.get("authority_event_ref"),
                binding.get("authority_type"),
            }:
                return True
    value = item.get("value")
    if isinstance(value, dict):
        for key in (
            "fact_id",
            "biography_id",
            "reviewed_timeline_ref",
            "timeline_source_event_ref",
            "source_ref",
            "ref",
        ):
            if value.get(key) == ref:
                return True
        try:
            serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            serialized = ""
        if ref in serialized:
            return True
    return False


def _pinned_item_refs(name: str, item: dict[str, object]) -> set[str]:
    """Read identities from validated envelopes and typed biography fields.

    The Capsule is the source selector. Neither a nested prose value nor an
    authority *type* is an identity. An opaque ref still proves no semantics.
    """
    try:
        compact = _compact_source_bound_item(item)
    except (TypeError, ValueError):
        return set()
    if (
        compact is None
        or compact.get("authority_scope") != "exact_source_bound_existing_truth"
        or item.get("privacy_class") not in {"shareable", "personal", "private"}
    ):
        return set()
    refs = {
        compact["item_ref"],
        *(binding["ref"] for binding in compact["source_bindings"]),
    }
    value = item.get("value")
    if name == "world_life":
        try:
            life = _WORLD_LIFE_ITEM_ADAPTER.validate_json(json.dumps(value))
        except (TypeError, ValueError):
            return set()
        bindings = {
            (binding["ref"], binding["source_world_revision"], binding["immutable_hash"])
            for binding in compact["source_bindings"]
        }
        sources = getattr(life, "source_bindings", (getattr(life, "source", None),))
        if (
            any(
                source is None
                or (
                    source.authority_event_ref,
                    source.authority_world_revision,
                    source.authority_payload_hash,
                ) not in bindings
                for source in sources
            )
            or _PRIVACY_RANK[item["privacy_class"]] < _PRIVACY_RANK[life.privacy_class]
        ):
            return set()
        identity = (
            getattr(life, "biography_id", None)
            or getattr(life, "activity_event_ref", None)
            or getattr(life, "occurrence_id", None)
        )
        if identity != compact["item_ref"]:
            return set()
        if isinstance(life, BiographicalWorldContextItem):
            if life.timeline_source_event_ref not in {binding[0] for binding in bindings}:
                return set()
            refs.update((life.reviewed_timeline_ref, life.timeline_source_event_ref))
    return refs


def _available_pinned_items(
    context: dict[str, object],
) -> Iterator[tuple[str, dict[str, object]]]:
    slices = context.get("slices")
    if not isinstance(slices, dict):
        return
    for name in _NOVEL_ORIGIN_EVIDENCE_SLICES:
        lane = slices.get(name)
        if not isinstance(lane, dict) or lane.get("availability") != "available":
            continue
        items = lane.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    yield name, item


def pinned_context_grounding_refs(context: dict[str, object]) -> tuple[str, ...]:
    """Expose only the refs the current pinned-material reader can resolve."""
    return tuple(sorted({
        ref
        for name, item in _available_pinned_items(context)
        for ref in _pinned_item_refs(name, item)
    }))


def resolve_cited_pinned_material(
    *,
    context: dict[str, object],
    manifest: LifeDevelopmentCapabilityManifest,
    ref: str,
    version: Literal["1", "2"] = "2",
) -> dict[str, object] | None:
    """Resolve one citable non-ledger ref to its exact pinned material.

    The World Author is offered these refs in the same pinned Context or
    capability manifest.  The reviewer must judge entailment against exactly
    the material the author could read, never against the bare opaque ref.
    """

    if version not in {"1", "2"}:
        raise ValueError("unknown pinned source material reader")
    # The legacy matcher exists only to recompile persisted request bytes.
    # Production manifests explicitly select version 2; it is never fallback.
    if version == "1":
        matches = [
            {"slice": name, "item": item}
            for name, item in _iter_pinned_context_items(context)
            if _legacy_item_matches_ref(item, ref)
        ]
    else:
        matches = [
            {"slice": name, "item": item}
            for name, item in _available_pinned_items(context)
            if ref in _pinned_item_refs(name, item)
            and all(
                binding["source_world_revision"] <= manifest.pinned_cursor.world_revision
                for binding in item["source_bindings"]
            )
        ]
    if matches:
        return {
            "source_ref": ref,
            "authority_kind": "pinned_context_item",
            "materials": matches,
        }
    capabilities = [
        item.model_dump(mode="json")
        for item in manifest.location_capabilities
        if ref in item.authority_refs
    ]
    if capabilities:
        return {
            "source_ref": ref,
            "authority_kind": "reviewed_location_catalog_policy",
            "materials": capabilities,
        }
    return None


def life_development_source_closure_messages(
    *,
    context: dict[str, object],
    manifest: LifeDevelopmentCapabilityManifest,
    draft: LifeDevelopmentPossibilityDraft,
    cited_events: tuple[WorldEvent, ...],
    cited_pinned_materials: tuple[dict[str, object], ...] = (),
    execution_authority: dict[str, object] | None = None,
    reviewer_is_independent: bool | None = None,
) -> list[dict[str, str]]:
    """Compile the independent reviewer request from the exact pinned inputs."""

    current = _uses_world_consequence(draft)
    existing_claim_refs = {
        ref
        for claim in draft.claim_declarations
        if claim.scope == "existing_world"
        for ref in claim.source_refs
    }
    cited_by_ref = {event.event_id: event for event in cited_events}
    cited_ids = tuple(event.event_id for event in cited_events)
    material_by_ref = {str(item["source_ref"]): item for item in cited_pinned_materials}
    material_ids = tuple(str(item["source_ref"]) for item in cited_pinned_materials)
    if manifest.pinned_source_materials_version == "2":
        for material in cited_pinned_materials:
            exact = resolve_cited_pinned_material(
                context=context, manifest=manifest, ref=material["source_ref"]
            )
            if exact is None or material != exact:
                raise ValueError("cited pinned material differs from its source-bound authority")
    if (
        len(cited_ids) != len(cited_by_ref)
        or len(material_ids) != len(material_by_ref)
        or set(cited_by_ref) & set(material_by_ref)
        or set(cited_by_ref) | set(material_by_ref) != existing_claim_refs
    ):
        raise ValueError(
            "source-review cited events and pinned materials must exactly close "
            "existing-world claim refs"
        )
    event_material = [
        {
            "source_ref": event.event_id,
            "world_id": event.world_id,
            "event_type": event.event_type,
            "actor": event.actor,
            "source": event.source,
            "logical_time": event.logical_time.isoformat(),
            "payload_hash": event.payload_hash,
            "payload": event.payload(),
        }
        for event in (cited_by_ref[ref] for ref in sorted(cited_by_ref))
    ]
    authority = (
        "an independent " if reviewer_is_independent is not False else "a configured "
    )
    system = (
        f"You are {authority}semantic source-closure reviewer, not the World "
        "Author and not the Character Model. Judge only non-negotiable truth and "
        "coordinate authority. Do not judge whether a development is interesting, "
        "likely, tasteful, socially appropriate, emotionally fitting, or what the "
        "character should choose. The World Author is explicitly free to create "
        "proposal-scoped novel environmental events, adverse surprises, provisional "
        "NPCs, and scoped novel places under novel_world_generation without a source. "
        "Do not reject those merely because they are invented. Existing-world claims "
        "are different: every such claim must be semantically entailed by its exact "
        "cited source material; the presence of a source id is never sufficient. A "
        "ClockAdvanced event proves only its recorded time movement, not weather, a "
        "message, a person, a relationship, a venue, or an activity. A biographical "
        "event proves only its exact recorded biography/calendar/residence fields; "
        "residence context is not proof of current physical presence. "
        "Novel generation cannot be used to smuggle a prior friendship, known person, "
        "user/shared history, or completed character experience. Any existing named "
        "person participating in the possibility must be bound through manifest-listed "
        "entity_refs and exact existing-world evidence; a genuinely new person remains "
        "proposal-scoped and must use the provisional-NPC authority instead. Opaque "
        "entity or location refs prove only authorized identity coordinates; "
        "they do not prove a name, alias, city, kind, publicness, or other place/person "
        "semantics absent from source-bound descriptors. A baseline-only or "
        "non-exhaustive Context item may alert you to a possible conflict, but its "
        "absence never proves novelty and an item without source_bindings cannot support "
        "an existing_world claim or, by itself, justify an unsupported verdict. A newly "
        "authored weather or environmental condition must be covered by its own "
        "novel_world_generation claim, not inferred from calendar context. Identify "
        "undeclared current facts only in premise, visual evidence, provisional-NPC, "
        "and provisional-place summaries. Outcome text has no negative coordinate in this general review lane: "
        "do not return an outcome text path or copy an outcome-only fragment into "
        "undeclared_fact_fragments. A separate focused critic reviews only imported "
        "current/prior prerequisites, retroactive history, and completed "
        "user-channel acts and companion interior authorship in outcome text. "
        + (
            "Only environmental changes and results of exactly bound earlier attempts "
            "are candidate World consequences; the focused critic checks whether "
            "their prose invents any new companion action, choice or response. "
            if current
            else "Objective candidate actions, NPC talk and world consequences remain "
            "unsettled; the focused critic checks their fact and actor authority. "
        )
        + "If a typed location_ref is "
        "present, it must be the execution coordinate of the proposed Plan or "
        "occurrence; other places may appear only as explicit background, origin, or "
        "hypothetical alternatives, not as a hidden destination. A proposal-scoped "
        "novel execution place remains valid only with no contradictory typed "
        "location and does not become a reusable location capability. For undeclared "
        "factual prose, prefer undeclared_fact_paths copied from the supplied parser "
        "coordinate catalogue. Use undeclared_fact_fragments only when a shorter "
        "coordinate is useful, and then copy a verbatim substring without quotation "
        "marks, a path prefix, or commentary. Return exactly one JSON object matching "
        "the supplied output contract, with the complete verdict inside its required "
        "review envelope."
    )
    if cited_pinned_materials:
        system += (
            "\ncited_pinned_materials contains the exact pinned Context or manifest "
            "material selected by an existing_world claim's source_refs. Judge those "
            "claims against the exact listed fields only; an absent field is not "
            "evidence and an opaque ref adds no unstated fact. A reviewed "
            "location/catalog-policy material proves only the recorded schedules and "
            "affordances of its listed locations; it never proves the protagonist's "
            "presence, a completed action, or any fact absent from its recorded fields."
        )
    reviewed_surface = _general_reviewed_surface(draft)
    pinned_source_evidence = {
        "contract": "life-development-source-evidence.1",
        "context_binding": _context_identity(context),
        "cited_committed_events": event_material,
        "manifest_binding": _review_manifest_binding(
            context=context,
            manifest=manifest,
            draft=draft,
            review_lane="general",
        ),
        "authority_note": (
            "Only the exact semantic content above may support existing_world "
            "claims. Opaque ids and broad event types add no unstated facts."
        ),
    }
    if cited_pinned_materials:
        pinned_source_evidence["cited_pinned_materials"] = list(cited_pinned_materials)
    if current:
        pinned_source_evidence["execution_authority"] = _required_execution_authority(
            execution_authority
        )
        system += _completed_lifecycle_guidance(
            manifest=manifest, execution_authority=execution_authority,
        )
    request = {
        "review_contract": _REVIEW_CONTRACT,
        "reviewed_surface": reviewed_surface,
        "pinned_source_evidence": pinned_source_evidence,
        "evidence_packet_binding": _evidence_packet_binding(
            contract=(
                WORLD_CONSEQUENCE_GENERAL_EVIDENCE_PACKET_CONTRACT
                if current
                else _GENERAL_EVIDENCE_PACKET_CONTRACT
            ),
            reviewed_surface=reviewed_surface,
            pinned_authority=pinned_source_evidence,
        ),
        "review_dimensions": {
            "existing_world_entailment": "exact_cited_sources_only",
            "undeclared_factual_prose": (
                "premise,provisional_npcs,provisional_places,"
                "objective_biographical_transition,visual_evidence"
            ),
            "outcome_text_authority": {
                "general_reviewer": "no_negative_coordinate_authority",
                "focused_novel_origin_critic": (
                    "imported_prerequisites_history_user_channel_and_companion_interior"
                ),
                "branch_internal_objective_candidates": (
                    "environment_and_exact_prior_attempt_result_only" if current else "allowed"
                ),
                "companion_interior_authorship": "reserved_for_character_model",
                "completed_user_channel_act": "not_allowed_without_action_receipt",
            },
            "novel_world_generation": {
                "proposal_scoped_environment": "allowed",
                "adverse_or_unfavorable_event": "allowed",
                "provisional_npc": "allowed",
                "scoped_novel_place": "allowed",
                "invented_prior_relationship_or_completed_history": "not_allowed",
            },
            "typed_location_consistency": (
                "typed execution coordinate must match the semantic proposal; reject "
                "only with typed_location_ref plus an exact prose_path and verbatim "
                "conflicting_fragment from that field"
            ),
        },
        "parser_coordinate_catalog": _source_closure_coordinate_catalog(draft),
        "output_contract": _review_output_contract(
            contract=_REVIEW_CONTRACT,
            review_model=LifeDevelopmentSourceClosureReview,
        ),
    }
    if reviewer_is_independent is not None:
        # Runtime review authority differs from historical reference checks even
        # when resuming an old manifest. The request hash binds this boundary.
        system += (
            "\nReview authority contract: life-development-semantic-source-review.1. "
            "This model must judge the exact supplied evidence; reference existence "
            "alone cannot establish support."
        )
    if reviewer_is_independent is False:
        system += (
            "\nIndependence from the author authority is not established. "
            "Do not treat this as independent verification; apply the same evidence boundaries."
        )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]


def life_development_source_closure_correction_message(
    *,
    raw: str,
    error: LifeDevelopmentSourceClosureError,
    draft: LifeDevelopmentPossibilityDraft,
) -> dict[str, str]:
    """Ask the same reviewer to repair only its invalid wire result."""

    return {
        "role": "user",
        "content": json.dumps(
            {
                "invalid_review_output": raw,
                "validation_failure": {
                    "code": error.code,
                    "detail": error.detail,
                    "violations": list(error.violations),
                },
                "review_contract": _REVIEW_CONTRACT,
                "parser_coordinate_catalog": _source_closure_coordinate_catalog(
                    draft
                ),
                "output_contract": _review_output_contract(
                    contract=_REVIEW_CONTRACT,
                    review_model=LifeDevelopmentSourceClosureReview,
                ),
                "instruction": (
                    "Return one complete replacement review for the identical draft and "
                    "pinned evidence. Prefer an exact undeclared_fact_path from the "
                    "catalogue over paraphrasing prose. Any undeclared_fact_fragment "
                    "must be copied verbatim without quotes, path labels, or commentary. "
                    "Do not change the World Author's proposal, invent evidence, or make "
                    "a style/motive judgement."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def _source_closure_coordinate_catalog(
    draft: LifeDevelopmentPossibilityDraft,
) -> dict[str, object]:
    """Expose only deterministic coordinates already present in the draft."""

    prose_paths = sorted(_general_source_prose_coordinates(draft))
    typed_location_paths = sorted(_typed_location_prose_coordinates(draft))
    return {
        "unsupported_claim_ids": [
            item.claim_id
            for item in draft.claim_declarations
            if item.scope == "existing_world"
        ],
        "undeclared_fact_paths": prose_paths,
        "fragment_rule": (
            "copy_a_verbatim_substring_from_the_selected_path_without_quotes_or_commentary"
        ),
        "typed_location": {
            "typed_location_ref": draft.location_ref,
            "prose_paths": typed_location_paths,
        },
    }


def life_development_novel_origin_messages(
    *,
    context: dict[str, object],
    manifest: LifeDevelopmentCapabilityManifest,
    draft: LifeDevelopmentPossibilityDraft,
    execution_authority: dict[str, object] | None = None,
    reviewer_is_independent: bool | None = None,
) -> list[dict[str, str]]:
    """Compile an independent hard-boundary review of novel fact origin."""

    profile = background_context_profile_for_purpose("life_development_novel_origin_review")
    context = slice_background_capsule_context(context, profile)
    current = _uses_world_consequence(draft)
    packet_contract = novel_origin_evidence_packet_contract(
        world_consequence=current, manifest_version=manifest.version,
    )
    review_projection = context.get("life_review_projection")
    if packet_contract == SOURCE_BOUND_NOVEL_EVIDENCE_PACKET_CONTRACT and (
        not isinstance(review_projection, dict)
        or review_projection.get("contract") != LIFE_REVIEW_PROJECTION_CONTRACT
    ):
        raise ValueError("qualified Life review requires its exact selected-source projection")
    if packet_contract == SOURCE_BOUND_NOVEL_EVIDENCE_PACKET_CONTRACT:
        for name in ("relevant_facts", "recent_dialogue"):
            lane = context.get("slices", {}).get(name, {})
            for item in lane.get("items", []):
                if not item.get("source_bindings"):
                    raise ValueError("qualified Life review cannot use unproved selected items")
    outcome_path = "world_consequence field" if current else "outcomes.N.text"
    authority = (
        "an independent " if reviewer_is_independent is not False else "a configured "
    )
    system = (
        f"You are {authority}focused novel-origin critic, not the general "
        "source reviewer, World Author, or Character Model. Review only hard truth "
        "origin; never judge plot quality, likelihood, "
        "motive, mood, style, or whether the character should participate. A "
        "novel_world_generation claim may create a genuinely new current environmental "
        "contingency, scoped place, provisional stranger, first encounter, or new "
        "relationship starting point. It cannot retroactively create a prior "
        "friendship, classmate relationship, period of no contact, shared history, "
        "known person, recurring habit, or completed character experience. The same "
        "boundary applies to provisional-NPC, provisional-place, and objective "
        "biographical-transition summaries. An objective transition may state only "
        "a present coordinate made true by that exact candidate branch; it cannot "
        "smuggle in prior history, a character motive, a plan, or a hoped-for future. "
        "Opaque entity/location refs "
        "prove identity coordinates only, not unstated names, "
        "aliases or place semantics. The supplied existing-world view is non-exhaustive: "
        "Manifest entity descriptor entries in this lane are exact pointers into the "
        "supplied existing_world_evidence slices; a pointer adds no semantics beyond "
        "the item whose slice, item_ref, value_hash, and source_hash all match. "
        "baseline-only items may reveal a possible imported premise, but absence never "
        "proves novelty, and an item without source_bindings cannot support an "
        "existing_world claim or, by itself, justify an unsupported verdict. Inspect "
        "each outcome independently in its own temporal order. Separate events "
        "the candidate would create from facts it assumes were already true before "
        "those events. An embedded assertion keeps its own truth requirements: "
        "thinking, remembering, noticing or intending something does not create "
        "the past experience, existing object, relationship, or external obligation "
        "mentioned inside that thought. A future date alone also cannot create an "
        "already-arranged external commitment. Such prerequisites need exact "
        "existing-world authority, or an earlier event in this same candidate "
        "that actually creates them; an unselected sibling outcome is not evidence. "
        "Inspect these embedded prerequisites before deciding that an entire "
        "outcome is branch-internal. "
        + (
            _world_consequence_actor_boundary()
            if current
            else "Objective candidate actions, NPC conversation, "
            "photography and world consequences remain unsettled candidates; that "
            "freedom does not validate unrelated embedded facts. The World Author "
            "cannot assign the companion new feelings, motives, thoughts, intentions "
            "or subjective reactions in an outcome, even conditionally or in a "
            "character_choice branch. Selecting a supplied outcome token is not "
            "authorship of her inner response; the Character Model chooses and "
            "appraises separately. Exact source-bound historical interior may be "
            "referenced as context, never rewritten as a new reaction. Put a new "
            "interior authorship finding on unsupported_outcome_prerequisites, using "
            "the exact outcomes.N.text path, character_interior_authorship violation "
            "kind and a verbatim fragment from that outcome. "
        )
        + "A completed "
        "user-channel act in that text is different: sending him "
        "a message or photo, his receiving it, or his reply through that channel is "
        "Action-ledger territory and is not a branch-internal life event. Put that "
        "finding only on unsupported_outcome_prerequisites for the exact "
        f"{outcome_path} path with violation kind completed_user_channel_act "
        "and a verbatim fragment from that outcome. Do not put "
        "completed_user_channel_act on unsupported_claims, NPCs, places, or "
        "objective transitions; claim summaries do not contain the send. "
        "user_channel_completion=none is a missing Action receipt, not proof "
        "that the prose is innocent: if that outcome text already states the "
        "send, his receipt, or his reply as a completed fact, mark it. "
        "Inspect the COMPLETE premise independently of its claim declarations: "
        "declarations may omit assertions made only in the prose. The installed "
        "general closure checks source-ref existence; it does not establish premise "
        "meaning or the World Author's permission. A premise may create an external "
        "opportunity, but cannot invent prior/current character activity, memory or "
        "relationships, nor author her present motives, emotions, attention or "
        "reaction. A character_core preference or habit does not prove current "
        "presence, completed experience or reaction. Exact source-bound historical "
        "character material may be cited as existing context, never upgraded to a "
        "new reaction to this proposal. Even declaring invented interior state as "
        "novel does not grant character authorship. Put each unsupported premise "
        "assertion in undeclared_premise_fragments as a verbatim substring of "
        "premise; explain the missing truth or authorship authority in reason. "
        "Do not reject a neutral environmental opportunity merely because it could "
        "evoke feelings: the Character Model decides any response later. Visual "
        "declaration coverage and typed location remain outside this focused lane. "
        "Return only parser-verifiable "
        "coordinates: each unsupported novel claim uses its exact claim_id and "
        "verbatim fragments from that claim summary; each provisional NPC or place uses its "
        "exact local_ref and verbatim fragments from its summary; each objective "
        "transition uses its exact supplied summary path and verbatim fragments; each imported "
        f"outcome prerequisite uses an exact supplied {outcome_path} prose_path and "
        "verbatim fragments from that one outcome. Each dynamic_life_direction is "
        "a proposed durable world effect: inspect its entire object, including "
        "summary, tags, supersession and duration, against that exact branch and "
        "pinned authority. It may not import prior facts or establish the character's "
        "motives, desires or subjective direction. Report unsupported authored "
        "strings in unsupported_dynamic_life_directions using the supplied field "
        "path and verbatim fragments from that field. Return exactly one JSON object "
        "matching the supplied contract, with the complete verdict inside its required "
        "review envelope."
    )
    reviewed_surface = _novel_origin_reviewed_surface(draft)
    pinned_authority = {
        "existing_world_evidence": _novel_origin_existing_world_evidence(
            context
        ),
        "manifest_binding": _review_manifest_binding(
            context=context,
            manifest=manifest,
            draft=draft,
            review_lane="focused",
        ),
    }
    if current:
        pinned_authority["execution_authority"] = _required_execution_authority(execution_authority)
        system += _completed_lifecycle_guidance(
            manifest=manifest, execution_authority=execution_authority,
        )
    if packet_contract == SOURCE_BOUND_NOVEL_EVIDENCE_PACKET_CONTRACT:
        pinned_authority["review_projection"] = review_projection
    request = {
        "review_contract": _novel_review_contract(draft),
        "reviewed_surface": reviewed_surface,
        "pinned_authority": pinned_authority,
        "evidence_packet_binding": _evidence_packet_binding(
            contract=packet_contract,
            reviewed_surface=reviewed_surface,
            pinned_authority=pinned_authority,
        ),
        "review_dimensions": {
            "novel_claim_origin": ("no_prior_relationship_shared_history_or_completed_experience"),
            "provisional_npc_origin": "new_person_or_new_relationship_start_only",
            "provisional_place_origin": "new_place_without_invented_prior_history_only",
            "objective_biographical_transition": (
                "present_objective_coordinate_semantically_entailed_by_exact_"
                "candidate_text_and_branch_only"
            ),
            "dynamic_life_direction": {
                "surface": "complete_durable_context_object",
                "authority": "world_author_event_impact_entailed_by_exact_branch",
                "reject": "imported_history_unentailed_context_or_character_interior_authorship",
                "coordinates": "exact_summary_or_tag_field_paths",
            },
            "outcome_prerequisites": {
                "reject": ("imported_current_or_prior_fact_or_retroactive_history_outside_branch"),
                "allow": (
                    "environment_and_exact_prior_attempt_result_only"
                    if current
                    else "objective_candidate_actions_npc_talk_and_world_consequences"
                ),
                "character_interior": "cannot_author_new_state_or_reaction",
                "historical_interior": "exact_source_bound_context_only_not_new_reaction",
                "reject_unbound": ("completed_user_channel_act_message_or_media_delivered_to_him"),
            },
            "current_premise_coverage": {
                "surface": "complete_premise_independent_of_claim_declarations",
                "truth": "each_current_or_prior_assertion_needs_matching_authority",
                "character_interior": "cannot_author_new_state_or_reaction",
                "coordinate": "undeclared_premise_fragments_verbatim_from_premise",
            },
            "character_behavior": "choice_and_evaluation_out_of_scope",
        },
        "parser_coordinate_catalog": _novel_origin_coordinate_catalog(draft),
        "output_contract": _review_output_contract(
            contract=_novel_review_contract(draft),
            review_model=_novel_review_model(draft),
        ),
    }
    if reviewer_is_independent is not None:
        # Runtime review authority differs from historical reference checks even
        # when resuming an old manifest. The request hash binds this boundary.
        system += (
            "\nReview authority contract: life-development-semantic-source-review.1. "
            "This model must judge the exact supplied evidence; reference existence "
            "alone cannot establish support."
        )
    if reviewer_is_independent is False:
        system += (
            "\nIndependence from the author authority is not established. "
            "Do not treat this as independent verification; apply the same evidence boundaries."
        )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]


def life_development_novel_origin_correction_message(
    *,
    error: LifeDevelopmentSourceClosureError,
    draft: LifeDevelopmentPossibilityDraft,
) -> dict[str, str]:
    """Ask the same focused critic to repair only an invalid wire result."""

    return {
        "role": "user",
        "content": json.dumps(
            {
                "validation_failure": {
                    "code": error.code,
                    "detail": error.detail,
                    "violations": list(error.violations),
                },
                "review_contract": _novel_review_contract(draft),
                "parser_coordinate_catalog": _novel_origin_coordinate_catalog(draft),
                "output_contract": _review_output_contract(
                    contract=_novel_review_contract(draft),
                    review_model=_novel_review_model(draft),
                ),
                "instruction": (
                    (
                        "Return one complete replacement review for the identical draft "
                        "and pinned authority, using only exact coordinates from this catalog. "
                        + _world_consequence_actor_boundary()
                        + "Preserve all other focused truth-origin boundaries: premise, claims, "
                        "NPCs, places, objective transitions, dynamic life directions and "
                        "completed user-channel acts. Do not judge or rewrite the story."
                    )
                    if _uses_world_consequence(draft)
                    else "Return one complete replacement review for the identical draft "
                    "and pinned authority. Preserve the focused truth-origin boundary, "
                    "use only exact parser-verifiable coordinates from the supplied "
                    "catalogue, and do not judge or change the story. Objective "
                    "candidate actions, NPC dialogue and world consequences are "
                    "allowed. New companion feelings, motives, thoughts, intentions "
                    "or subjective reactions are character_interior_authorship: "
                    "report the exact outcomes.N.text path and verbatim fragment "
                    "in unsupported_outcome_prerequisites. Source-bound historical "
                    "interior is context only, not a new reaction. The premise "
                    "likewise cannot invent past activity or a present reaction. "
                    "Copy premise findings exactly "
                    "from premise into undeclared_premise_fragments. Durable-context "
                    "findings use unsupported_dynamic_life_directions and exact "
                    "fragments from their supplied summary or tag field path."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def _novel_origin_coordinate_catalog(
    draft: LifeDevelopmentPossibilityDraft,
) -> dict[str, object]:
    """Expose exact focused-review coordinates without assigning a verdict."""

    return {
        "undeclared_premise_fragments": {
            "prose_path": "premise",
            "authority": "unsupported_truth_or_character_authorship",
        },
        "novel_claim_ids": [
            item.claim_id
            for item in draft.claim_declarations
            if item.scope == "novel_world_generation"
        ],
        "provisional_npc_refs": sorted(
            {npc.local_ref for outcome in draft.outcomes for npc in outcome.provisional_npcs}
        ),
        "provisional_place_refs": sorted(
            {place.local_ref for outcome in draft.outcomes for place in outcome.provisional_places}
        ),
        "outcome_prerequisite_paths": list(_outcome_prose_coordinates(draft)),
        "objective_transition_paths": [
            f"outcomes.{index}.objective_biographical_transition.summary"
            for index, outcome in enumerate(draft.outcomes)
            if outcome.objective_biographical_transition is not None
        ],
        "dynamic_life_direction_paths": list(_dynamic_life_direction_coordinates(draft)),
        "fragment_rule": (
            "copy_verbatim_substrings_from_the_matching_claim_npc_place_transition_"
            "or_outcome_path_or_premise"
        ),
        "claim_violation_kinds": [
            "retroactive_relationship_or_shared_history",
            "completed_character_experience",
            "existing_entity_or_fact_masquerading_as_novel",
        ],
        "outcome_prerequisite_violation_kinds": [
            "retroactive_relationship_or_shared_history",
            "existing_entity_or_fact_masquerading_as_novel",
            "imported_current_or_prior_prerequisite",
            "completed_user_channel_act",
            "character_interior_authorship",
        ],
    }


def possibility_draft_from_outcome_texts(
    *,
    texts: tuple[str, ...],
    owner_actor_ref: str,
) -> tuple[LifeDevelopmentPossibilityDraft, LifeDevelopmentCapabilityManifest]:
    """Adapter draft so the focused critic can inspect arbitrary outcome prose."""

    padded = list(texts[:4])
    while len(padded) < 2:
        padded.append("她待在原地，这段候选变化没有发生。")
    claim_id = "local:claim:candidate-self-life"
    draft = LifeDevelopmentPossibilityDraft(
        decision="propose",
        authored_subject_ref=owner_actor_ref,
        causal_authority="world_contingency",
        outcome_resolution_authority="world_contingency",
        premise_scope="external_opportunity",
        premise="一次尚未结算的生活分支出场。",
        premise_claim_refs=(claim_id,),
        claim_declarations=(
            LifeDevelopmentClaimDeclaration(
                claim_id=claim_id,
                summary="这一候选分支里她过了一段自己的生活。",
                scope="novel_world_generation",
                subject_scope="world_environment",
                source_refs=(),
            ),
        ),
        timing=LifeDevelopmentTimingDraft(mode="now", duration_minutes=30),
        anchor_refs=("event:operator:user-channel-prose-review",),
        privacy_class="personal",
        outcomes=tuple(
            LifeDevelopmentOutcomeDraft(
                experienced_by_ref=owner_actor_ref,
                text=text,
                user_channel_completion="none",
                privacy_class="personal",
                relative_plausibility_weight=1,
                claim_refs=(claim_id,),
            )
            for text in padded
        ),
    )
    manifest = LifeDevelopmentCapabilityManifest(
        version="life-development-capability.user-channel-review.1",
        owner_actor_ref=owner_actor_ref,
        pinned_cursor=ProjectionCursor(
            world_revision=1,
            deliberation_revision=1,
            ledger_sequence=1,
        ),
        anchor_refs=("event:operator:user-channel-prose-review",),
        grounding_refs=("event:operator:user-channel-prose-review",),
        location_capabilities=(),
        entity_refs=(),
        max_future_days=30,
        max_window_minutes=12 * 60,
    )
    return draft, manifest


__all__ = [
    "LifeDevelopmentObjectiveTransitionFinding",
    "LifeDevelopmentOutcomePrerequisiteFinding",
    "LifeDevelopmentNovelOriginReview",
    "LifeDevelopmentNovelOriginPlaceFinding",
    "LifeDevelopmentSourceClosureError",
    "LifeDevelopmentSourceClosureReview",
    "life_development_novel_origin_correction_message",
    "life_development_novel_origin_messages",
    "life_development_review_packet_identity",
    "life_development_source_closure_correction_message",
    "life_development_source_closure_messages",
    "parse_life_development_novel_origin_review",
    "parse_life_development_source_closure_review",
    "pinned_context_grounding_refs",
    "possibility_draft_from_outcome_texts",
    "resolve_cited_pinned_material",
]
