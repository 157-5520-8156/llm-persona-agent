"""Whole-candidate records for two complete, evidence-blind semantic readings.

Self-consistency is not invocation proof. Admission must join the original
candidate/source pin and all immutable provider audits supplied by the caller.
This module has no provider, World mutation, dispatch or Action authority.
"""
from dataclasses import dataclass
import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .schema_core import FrozenModel
from .visible_candidate_meaning import prepare_candidate_meaning
from .visible_independent_meanings import IndependentMeaning, prepare_independent_meanings_sources
from .visible_source_composer import VisibleSourceTable
from .visible_source_review_receipt import (
    VisibleReviewAuthorBinding, VisibleReviewInvocationBinding, compile_visible_candidate_material,
)
from .visible_source_witness_experiment import _json, _unique

PROTOCOL = "visible-independent-review.1"
CANDIDATE_CONTRACT = "visible-independent-candidate.1"
RECEIPT_CONTRACT = "visible-source-review-receipt.9"
MAX_BYTES = 1_048_576


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _packet(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES:
        raise ValueError("independent visible review exceeds its byte bound")
    value = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(value, dict) or _json(value) != raw:
        raise ValueError("independent visible review requires canonical JSON")
    return value


@dataclass(frozen=True)
class PreparedIndependentVisibleReview:
    payload_json: str

    @property
    def sha256(self):
        return _hash(self.payload_json)

    def as_dict(self):
        return _packet(self.payload_json)


def prepare_independent_visible_review(*, candidate, source_table, source_ref_aliases):
    material = compile_visible_candidate_material(
        candidate=candidate, source_table=source_table, source_ref_aliases=source_ref_aliases,
    )
    # The complete proposal already owns these claims. They are not an
    # independent inventory of the candidate's visible factual assertions.
    material.pop("world_claims")
    raw = _json({"contract": CANDIDATE_CONTRACT, "protocol": PROTOCOL, **material})
    _packet(raw)
    return PreparedIndependentVisibleReview(raw)


def _restore(prepared):
    from .proposal_envelope import DecisionProposal

    pin = prepared.as_dict()
    expected = prepare_independent_visible_review(
        candidate=DecisionProposal.model_validate_json(pin["candidate_json"], strict=True),
        source_table=VisibleSourceTable(payload_json=pin["source_table_json"]),
        source_ref_aliases=pin["source_ref_aliases"],
    )
    if prepared.payload_json != expected.payload_json:
        raise ValueError("independent review candidate/source preparation changed")
    return pin


def meaning_preparation(prepared):
    pin = _restore(prepared)
    return prepare_candidate_meaning(
        beats=tuple(b["text"] for b in pin["beat_mapping"]), compact=True, explicit_questions=True,
        question_conditions=True, beat_conditions=True, require_complete_reading=True,
        closing_tail_transport=True,
    )


@dataclass(frozen=True)
class IndependentReviewCall:
    stage: str
    request: dict
    identity_extras: dict
    request_hash: str


def _call(prepared, *, stage, request, preparation_hash, dependencies=()):
    from companion_daemon.llm import provider_invocation_request_hash

    extras = {"visible_independent_review": {
        "protocol": PROTOCOL, "candidate_preparation_sha256": prepared.sha256,
        "stage": stage, "stage_preparation_sha256": preparation_hash,
        "meaning_response_sha256": list(dependencies),
    }}
    return IndependentReviewCall(
        stage, request, extras, provider_invocation_request_hash(**request, identity_extras=extras),
    )


def prepare_meaning_call(*, prepared, meaning_index):
    if type(meaning_index) is not int or meaning_index not in {0, 1}:
        raise ValueError("independent review requires meaning stage zero or one")
    meaning = meaning_preparation(prepared)
    return _call(prepared, stage=f"meaning:{meaning_index}", request=meaning.request(), preparation_hash=meaning.sha256)


def _interpreted(prepared, raw_responses):
    if len(raw_responses) != 2:
        raise ValueError("independent visible review needs two complete readings")
    meaning = meaning_preparation(prepared)
    results = [meaning.inspect_response(raw) for raw in raw_responses]
    for result in results:
        for decision in result["interpretation"]["decisions"]:
            if not decision["reading_complete"] or decision["unresolved_details"]:
                raise IndependentReviewInconclusive("candidate reading is incomplete or unresolved")
    return meaning, results


class IndependentReviewInconclusive(ValueError):
    """A reader's uncertainty is not evidence that the character invented facts."""


def prepare_source_call(*, prepared, meaning_raw_responses):
    pin = _restore(prepared)
    meaning, _ = _interpreted(prepared, meaning_raw_responses)
    sources = VisibleSourceTable(payload_json=pin["source_table_json"]).source_references()
    source = prepare_independent_meanings_sources(
        meanings=tuple(IndependentMeaning(meaning, raw) for raw in meaning_raw_responses), sources=sources,
    )
    if source is None:
        return None
    return _call(
        prepared, stage="sources", request=source.request(), preparation_hash=source.sha256,
        dependencies=tuple(_hash(raw) for raw in meaning_raw_responses),
    )


def _invocation(*, call, author, binding, raw):
    binding = VisibleReviewInvocationBinding.model_validate_json(binding.model_dump_json(), strict=True)
    if (binding.parent_model_call_id != author.model_call_id or binding.model_call_id == author.model_call_id
            or binding.request_hash != call.request_hash or binding.response_hash != _hash(raw)):
        raise ValueError("independent review invocation differs from its original stage")


def _outcomes(*, prepared, author, meaning_reviews, meaning_raw_responses, source_review, source_raw_response):
    pin = _restore(prepared)
    author = VisibleReviewAuthorBinding.model_validate_json(author.model_dump_json(), strict=True)
    if author.proposal_material_hash != _hash(pin["candidate_json"]):
        raise ValueError("independent review author does not bind the entire candidate")
    if len(meaning_reviews) != 2 or len(meaning_raw_responses) != 2:
        raise ValueError("independent review requires exactly two meaning invocations")
    if meaning_reviews[0].model_id == meaning_reviews[1].model_id:
        raise ValueError("independent review requires two distinct reader model identities")
    bindings = [*meaning_reviews, *([source_review] if source_review is not None else [])]
    if len({b.model_call_id for b in bindings}) != len(bindings):
        raise ValueError("independent review cannot reuse an invocation for another stage")
    for index, (binding, raw) in enumerate(zip(meaning_reviews, meaning_raw_responses, strict=True)):
        _invocation(call=prepare_meaning_call(prepared=prepared, meaning_index=index), author=author, binding=binding, raw=raw)
    meaning, readings = _interpreted(prepared, meaning_raw_responses)
    source_call = prepare_source_call(prepared=prepared, meaning_raw_responses=meaning_raw_responses)
    support = None
    if source_call is None:
        if source_review is not None or source_raw_response is not None:
            raise ValueError("source-free reading has an unrelated source invocation")
    else:
        if source_review is None or source_raw_response is None:
            raise ValueError("factual readings require their actual source invocation")
        _invocation(call=source_call, author=author, binding=source_review, raw=source_raw_response)
        source = prepare_independent_meanings_sources(
            meanings=tuple(IndependentMeaning(meaning, raw) for raw in meaning_raw_responses),
            sources=VisibleSourceTable(payload_json=pin["source_table_json"]).source_references(),
        )
        support = source.inspect_response(source_raw_response)
    outcomes = []
    for index in range(len(pin["beat_mapping"])):
        facts = [f for reading in readings for f in reading["facts"] if f["beat_index"] == index]
        if not facts:
            # Both actual readers affirm complete understanding and positively
            # represent this Beat. This is not promotion of a probe's
            # not_assessed or of the character author's empty world_claims.
            outcomes.append("source_free")
        else:
            decisions = [d for d in support["fact_decisions"] if d["beat_index"] == index]
            if len(decisions) != len(facts):
                raise ValueError("source decisions omit an independently read fact")
            outcomes.append("unclosed" if any(d["outcome"] == "rejected" for d in decisions) else "closed")
    return tuple(outcomes), readings, support


class IndependentVisibleReviewRejected(ValueError):
    def __init__(self, *, outcomes, readings, support):
        super().__init__("the complete candidate contains an unclosed independent reading")
        self.outcomes = outcomes
        self.readings = readings
        self.support = support


class IndependentVisibleReviewReceipt(FrozenModel):
    contract: Literal["visible-source-review-receipt.9"] = RECEIPT_CONTRACT
    prepared_json: str = Field(min_length=2, max_length=MAX_BYTES)
    author: VisibleReviewAuthorBinding
    meaning_reviews: tuple[VisibleReviewInvocationBinding, VisibleReviewInvocationBinding]
    meaning_raw_responses: tuple[str, str]
    source_review: VisibleReviewInvocationBinding | None
    source_raw_response: str | None
    beat_outcomes: tuple[Literal["closed", "source_free"], ...] = Field(min_length=1, max_length=16)
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def record_is_self_consistent(self):
        if len(self.model_dump_json().encode()) > MAX_BYTES:
            raise ValueError("independent receipt exceeds its byte bound")
        outcomes, _, _ = _outcomes(
            prepared=PreparedIndependentVisibleReview(self.prepared_json), author=self.author,
            meaning_reviews=self.meaning_reviews, meaning_raw_responses=self.meaning_raw_responses,
            source_review=self.source_review, source_raw_response=self.source_raw_response,
        )
        if outcomes != self.beat_outcomes or "unclosed" in outcomes:
            raise ValueError("independent receipt requires the exact passing outcome for every Beat")
        if self.receipt_hash != _hash(_json(self.model_dump(mode="json", exclude={"receipt_hash"}))):
            raise ValueError("independent receipt hash differs from its full record")
        return self


def record_independent_visible_review(
    *, prepared, author, meaning_reviews, meaning_raw_responses, source_review=None, source_raw_response=None,
):
    outcomes, readings, support = _outcomes(
        prepared=prepared, author=author, meaning_reviews=meaning_reviews,
        meaning_raw_responses=meaning_raw_responses, source_review=source_review, source_raw_response=source_raw_response,
    )
    if "unclosed" in outcomes:
        raise IndependentVisibleReviewRejected(outcomes=outcomes, readings=readings, support=support)
    value = {
        "contract": RECEIPT_CONTRACT, "prepared_json": prepared.payload_json,
        "author": author.model_dump(mode="json"),
        "meaning_reviews": [b.model_dump(mode="json") for b in meaning_reviews],
        "meaning_raw_responses": meaning_raw_responses,
        "source_review": source_review.model_dump(mode="json") if source_review else None,
        "source_raw_response": source_raw_response, "beat_outcomes": outcomes,
    }
    value["receipt_hash"] = _hash(_json(value))
    return IndependentVisibleReviewReceipt.model_validate_json(_json(value), strict=True)


def verify_independent_visible_review_receipt(*, receipt, expected_prepared, expected_author, expected_invocations):
    """Expected bindings must come from immutable audits, never this record."""
    receipt = IndependentVisibleReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    _restore(expected_prepared)
    bindings = (*receipt.meaning_reviews, *((receipt.source_review,) if receipt.source_review else ()))
    if (receipt.prepared_json != expected_prepared.payload_json or receipt.author != expected_author
            or bindings != tuple(expected_invocations)):
        raise ValueError("independent receipt differs from original candidate or immutable invocation audits")
    return receipt
