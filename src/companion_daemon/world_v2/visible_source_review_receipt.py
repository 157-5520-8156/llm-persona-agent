"""Recheckable whole-candidate review records, without any release authority.

Self-consistent hashes do not prove that a provider reviewed a candidate.
Verification requires the caller's original preparation and immutable author
and reviewer audit bindings. This module has no provider, ledger or Action port.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import TYPE_CHECKING, Literal, Mapping

from pydantic import Field, model_validator

from .proposal_envelope import DecisionProposal, ExpressionPlanPayload
from .schema_core import FrozenModel
from .visible_source_closure_protocol import (
    VisibleSourceClosureWire,
    parse_visible_source_closure,
    visible_source_closure_messages,
    visible_source_verdict_provider_request_contract,
)

if TYPE_CHECKING:
    from .visible_source_composer import VisibleSourceTable


_MAX_BYTES = 512_000


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_json(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("visible review material exceeds its byte bound")
    value = json.loads(raw)
    if not isinstance(value, dict) or _json(value) != raw:
        raise ValueError("visible review material must be a canonical JSON object")
    return value


@dataclass(frozen=True, slots=True)
class PreparedVisibleSourceReview:
    """Immutable bytes; this is preparation, not proof of a completed review."""

    payload_json: str

    @property
    def payload_hash(self) -> str:
        return _hash(self.payload_json)

    def as_dict(self) -> dict:
        return _bounded_json(self.payload_json)


class VisibleReviewAuthorBinding(FrozenModel):
    model_call_id: str = Field(min_length=1, max_length=256)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Hash of the complete host-materialized proposal, not an excerpt or the
    # provider's HTTP envelope. The future consumer must join its author audit.
    proposal_material_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class VisibleReviewInvocationBinding(FrozenModel):
    purpose: Literal["source_review"] = "source_review"
    parent_model_call_id: str = Field(min_length=1, max_length=256)
    model_call_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=256)
    model_version: str = Field(min_length=1, max_length=256)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def prepare_visible_source_review(
    *,
    candidate: DecisionProposal,
    source_table: VisibleSourceTable,
    source_ref_aliases: Mapping[str, str],
) -> PreparedVisibleSourceReview:
    """Prepare every inline text Beat of one complete typed decision.

    This first record contract rejects non-inline payloads and more than the
    existing verdict protocol's sixteen Beats; it never skips or truncates
    them. Non-visible decisions do not need an accepted visible receipt.
    Source provenance is established by the original source compiler/caller.
    """
    from companion_daemon.llm import provider_invocation_request_hash
    from .visible_source_composer import (
        PLANNED_SOURCE_TABLE_CONTRACT, VISIBLE_SOURCE_TABLE_CONTRACT, VisibleSourceTable,
    )

    if not isinstance(candidate, DecisionProposal) or not isinstance(
        source_table, VisibleSourceTable
    ):
        raise ValueError("visible review requires a typed decision and compiled source table")
    candidate = DecisionProposal.model_validate_json(candidate.model_dump_json(), strict=True)
    candidate_json = _json(candidate.model_dump(mode="json"))
    _bounded_json(candidate_json)
    table = _bounded_json(source_table.payload_json)
    if table.get("contract") not in {VISIBLE_SOURCE_TABLE_CONTRACT, PLANNED_SOURCE_TABLE_CONTRACT}:
        raise ValueError("visible review source table contract is unsupported")
    if (
        candidate.trigger_ref != table["pin"]["trigger_ref"]
        or candidate.evaluated_world_revision != table["pin"]["world_revision"]
    ):
        raise ValueError("visible candidate does not bind the original source pin")
    rows = table["source_references"]
    materials = table["source_materials"]
    if table["table_hash"] != _hash(_json(rows)) or table["materials_hash"] != _hash(
        _json(materials)
    ):
        raise ValueError("visible review source table hashes do not bind its rows and materials")
    for index, row in enumerate(rows):
        material_index = row["material_index"]
        if type(material_index) is not int or not 0 <= material_index < len(materials):
            raise ValueError("visible review material index is outside the original table")
        material = materials[material_index]
        if (
            type(row["source_ref_index"]) is not int
            or row["source_ref_index"] != index
            or row["material_identity"] != material["material_identity"]
            or material["material_identity"] != _hash(_json(material["material"]))
        ):
            raise ValueError("visible review row changed its material identity")
    aliases = dict(source_ref_aliases)
    if any(
        not isinstance(k, str) or not k or not isinstance(v, str) or not v
        for k, v in aliases.items()
    ):
        raise ValueError("visible review alias map must contain exact nonempty string bindings")
    beats, claims = [], []
    for change in candidate.proposed_changes:
        if change.kind != "expression_plan_transition":
            continue
        plan = ExpressionPlanPayload.model_validate_json(change.payload.canonical_json, strict=True)
        claims.extend(claim.model_dump(mode="json") for claim in plan.world_claims)
        for beat in plan.beat_drafts:
            if (
                beat.inline_text is None
                or beat.payload_ref is not None
                or beat.inline_encrypted_payload is not None
            ):
                raise ValueError("visible review record requires every Beat's complete inline text")
            beats.append(
                {
                    "beat_index": len(beats),
                    "change_id": change.change_id,
                    "plan_id": plan.plan_id,
                    "beat_id": beat.beat_id,
                    "content_type": beat.content_type,
                    "text": beat.inline_text,
                }
            )
    if not 1 <= len(beats) <= 16:
        raise ValueError("visible review record requires one to sixteen complete inline Beats")
    contract = visible_source_verdict_provider_request_contract()
    request = {
        "messages": visible_source_closure_messages(
            visible_beats=tuple(beat["text"] for beat in beats),
            world_claims=tuple(claims),
            source_references=source_table.source_references(),
        ),
        "temperature": 0.0,
        "tools": contract["tools"],
        "tool_choice": contract["tool_choice"],
    }
    extras = {
        "tool_contract_identity": {
            "contract": contract["contract"],
            "schema_digest": contract["schema_digest"],
        },
        "visible_review_preparation": {
            "contract": "visible-source-review-request.1",
            "candidate_hash": _hash(candidate_json),
            "source_table_hash": source_table.payload_hash,
            "alias_map_hash": _hash(_json(aliases)),
            "beat_mapping_hash": _hash(_json(beats)),
        },
    }
    material = {
        "contract": "visible-source-review-request.1",
        "candidate_json": candidate_json,
        "source_table_json": source_table.payload_json,
        "source_ref_aliases": aliases,
        "beat_mapping": beats,
        "request": request,
        "identity_extras": extras,
        "request_hash": provider_invocation_request_hash(**request, identity_extras=extras),
    }
    raw = _json(material)
    _bounded_json(raw)
    return PreparedVisibleSourceReview(payload_json=raw)


def _restore_preparation(raw: str) -> PreparedVisibleSourceReview:
    from .visible_source_composer import VisibleSourceTable

    value = _bounded_json(raw)
    result = prepare_visible_source_review(
        candidate=DecisionProposal.model_validate_json(value["candidate_json"], strict=True),
        source_table=VisibleSourceTable(payload_json=value["source_table_json"]),
        source_ref_aliases=value["source_ref_aliases"],
    )
    if result.payload_json != raw:
        raise ValueError("visible review preparation changed its request or candidate binding")
    return result


def _verdict(prepared: PreparedVisibleSourceReview, raw: str) -> VisibleSourceClosureWire:
    from .visible_source_composer import VisibleSourceTable

    value = prepared.as_dict()
    rows = VisibleSourceTable(payload_json=value["source_table_json"]).source_references()
    return parse_visible_source_closure(
        raw,
        visible_beats=tuple(beat["text"] for beat in value["beat_mapping"]),
        source_references=rows,
        source_ref_kinds=tuple(row["kind"] for row in rows),
        source_ref_subject_roles=tuple(row["subject_role"] for row in rows),
    )


def _check_invocation_binding(
    prepared: PreparedVisibleSourceReview,
    author: VisibleReviewAuthorBinding,
    review: VisibleReviewInvocationBinding,
    raw_verdict: str,
) -> None:
    # Validate copied objects too, before a rejected verdict can be used as
    # feedback to the character. An unrelated/malformed review is technical.
    author = VisibleReviewAuthorBinding.model_validate_json(author.model_dump_json(), strict=True)
    review = VisibleReviewInvocationBinding.model_validate_json(
        review.model_dump_json(), strict=True
    )
    value = prepared.as_dict()
    if (
        author.proposal_material_hash != _hash(value["candidate_json"])
        or review.parent_model_call_id != author.model_call_id
        or review.model_call_id == author.model_call_id
        or review.request_hash != value["request_hash"]
        or review.response_hash != _hash(raw_verdict)
    ):
        raise ValueError("visible review invocation does not bind the complete candidate")


class VisibleSourceReviewRejected(ValueError):
    """A legal semantic rejection, distinct from an invalid reviewer wire."""

    def __init__(self, verdict: VisibleSourceClosureWire) -> None:
        super().__init__("the complete visible candidate contains an unclosed Beat")
        self.verdict = verdict


class VisibleSourceReviewReceipt(FrozenModel):
    contract: Literal["visible-source-review-receipt.1"] = "visible-source-review-receipt.1"
    prepared_json: str = Field(min_length=2, max_length=_MAX_BYTES)
    author: VisibleReviewAuthorBinding
    review: VisibleReviewInvocationBinding
    raw_verdict: str = Field(min_length=2, max_length=64_000)
    verdict: VisibleSourceClosureWire
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def record_is_self_consistent(self) -> VisibleSourceReviewReceipt:
        prepared = _restore_preparation(self.prepared_json)
        _check_invocation_binding(prepared, self.author, self.review, self.raw_verdict)
        parsed = _verdict(prepared, self.raw_verdict)
        if parsed != self.verdict or any(
            segment.decision == "unclosed" for segment in parsed.segments
        ):
            raise ValueError("accepted visible receipt requires the exact complete passing verdict")
        if self.receipt_hash != _hash(
            _json(self.model_dump(mode="json", exclude={"receipt_hash"}))
        ):
            raise ValueError("visible review receipt hash does not bind its full record")
        return self


def record_visible_source_review(
    *,
    prepared: PreparedVisibleSourceReview,
    author: VisibleReviewAuthorBinding,
    review: VisibleReviewInvocationBinding,
    raw_verdict: str,
) -> VisibleSourceReviewReceipt:
    """Record one accepted attempt; no retries, rewriting or Action authority."""
    prepared = _restore_preparation(prepared.payload_json)
    _check_invocation_binding(prepared, author, review, raw_verdict)
    verdict = _verdict(prepared, raw_verdict)
    if any(segment.decision == "unclosed" for segment in verdict.segments):
        raise VisibleSourceReviewRejected(verdict)
    value = {
        "contract": "visible-source-review-receipt.1",
        "prepared_json": prepared.payload_json,
        "author": author.model_dump(mode="json"),
        "review": review.model_dump(mode="json"),
        "raw_verdict": raw_verdict,
        "verdict": verdict.model_dump(mode="json"),
    }
    value["receipt_hash"] = _hash(_json(value))
    return VisibleSourceReviewReceipt.model_validate_json(_json(value), strict=True)


def verify_visible_source_review_receipt(
    *,
    receipt: VisibleSourceReviewReceipt,
    expected_prepared: PreparedVisibleSourceReview,
    expected_author: VisibleReviewAuthorBinding,
    expected_review: VisibleReviewInvocationBinding,
) -> VisibleSourceReviewReceipt:
    """Recheck against independent original preparation and immutable audits.

    These expected values must come from the caller's authority readers, not
    be copied from the receipt. This function alone installs no acceptance or
    replay policy, and a missing receipt must not select a weaker policy.
    """
    receipt = VisibleSourceReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    _restore_preparation(expected_prepared.payload_json)
    if receipt.prepared_json != expected_prepared.payload_json:
        raise ValueError("visible receipt differs from the original complete preparation")
    if receipt.author != expected_author or receipt.review != expected_review:
        raise ValueError("visible receipt differs from the immutable invocation audits")
    return receipt
