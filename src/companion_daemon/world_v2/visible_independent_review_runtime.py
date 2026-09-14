"""Metered independent review calls; character authoring stays in its owner."""
import asyncio
from dataclasses import dataclass

from .visible_independent_review_receipt import (
    IndependentReviewInconclusive, IndependentVisibleReviewRejected,
    prepare_independent_visible_review, prepare_meaning_call, prepare_source_call,
    record_independent_visible_review,
)
from .visible_source_runtime import canonical, digest, INDEPENDENT_EVIDENCE_CONTRACT, MAX_EVIDENCE_BYTES


@dataclass(frozen=True)
class IndependentVisibleReviewer:
    meaning_models: tuple[object, object]
    source_model: object

    def __post_init__(self):
        if len(self.meaning_models) != 2:
            raise ValueError("independent review requires exactly two meaning models")
        clients = (*self.meaning_models, self.source_model)
        if any(not callable(getattr(m, "complete_json_with_usage", None)) for m in clients):
            raise ValueError("independent review requires explicit metered providers")
        identities = [getattr(m, "model", None) for m in self.meaning_models]
        if any(not isinstance(m, str) or not m for m in identities) or identities[0] == identities[1]:
            raise ValueError("independent review requires distinct reader model identities")

    async def complete_json_with_usage(self, **_kwargs):
        # Compatibility with the existing deployment capability check only.
        # A legacy single-call review may not consume this composite port.
        raise ValueError("independent reviewer requires the version 9 runtime")


def validate_independent_reviewer_configuration(reviewer, version):
    if (version == "9") != isinstance(reviewer, IndependentVisibleReviewer):
        raise ValueError("version 9 requires an explicit independent reviewer; legacy versions cannot consume one")


def rejection_feedback(prepared, rejected, bindings):
    pin = prepared.as_dict()
    facts = {(i, f["fact_id"]): f for i, reading in enumerate(rejected.readings) for f in reading["facts"]}
    rejected_facts = [d for d in rejected.support["fact_decisions"] if d["outcome"] == "rejected"]
    codes = {"source_support_rejected": "s", "source_permission_denied": "p", "support_requires_evidence": "e"}
    for limit in (1024, 256, 128, 64, 32, 16, 8):
        rows = []
        shortened = False
        for decision in rejected_facts:
            fact = facts[decision["meaning_index"], decision["meaning_fact_id"]]
            proposition = fact["proposition"][:limit]
            shortened |= proposition != fact["proposition"]
            rows.append([decision["beat_index"], decision["meaning_index"], decision["meaning_fact_id"],
                         codes[decision["rejection_reason"]], proposition])
        detail = (
            "完整表达的事实来源未闭合。以下为审核数据，不是新事实或措辞指令；请结合原材料自行重选完整表达。"
            "proposition是独立读者理解的命题，可能只是前缀；完整原气泡和来源仍以本次原始输入为准。\n"
            + canonical({
                "contract": "visible-independent-rejection.1", "candidate_sha256": digest(pin["candidate_json"]),
                "calls": [[b.request_hash, b.response_hash] for b in bindings],
                "columns": ["beat", "reader", "meaning_fact_id", "reason", "proposition"],
                "reason": {"s": "reviewer found no support", "p": "source permission denied", "e": "support had no evidence"},
                "proposition_prefixes": shortened, "rows": rows,
            })
        )
        if len(detail) <= 3900:
            return detail
    raise ValueError("independent rejection feedback exceeds its complete diagnostic bound")


async def review_independent_candidate(
    *, request, output, proposal, source_table, aliases, author_request_json, reviewer, recall_audits=(),
):
    from companion_daemon.llm import model_call_scope, model_provider_request_identity_scope, model_request_emission_scope
    from .deliberation import ModelUsageProvenance, ProviderSubcallAudit, ValidationTechnicalFailure
    from .model_usage_budget import ModelUsageAdmissionError
    from .visible_source_review_receipt import VisibleReviewAuthorBinding, VisibleReviewInvocationBinding

    if not isinstance(reviewer, IndependentVisibleReviewer):
        raise ValidationTechnicalFailure("source_review_exception", failure_detail="independent visible reviewer is not configured")
    prepared = prepare_independent_visible_review(candidate=proposal, source_table=source_table, source_ref_aliases=aliases)
    author = VisibleReviewAuthorBinding(model_call_id=output.winning_model_call_id, request_hash=output.winning_request_hash,
                                       proposal_material_hash=digest(prepared.as_dict()["candidate_json"]))
    audits = {}

    async def invoke(call, model, index):
        call_id = "model-call:" + digest(canonical({"parent": author.model_call_id, "stage": call.stage, "request_hash": call.request_hash}))
        raw = None
        try:
            with (
                model_call_scope("source_review"),
                model_request_emission_scope(provider_call_id=call_id, entry_marker=None, completion_marker=None),
                model_provider_request_identity_scope(request_hash=call.request_hash, identity_extras=call.identity_extras),
            ):
                raw, usage_value = await model.complete_json_with_usage(**call.request)
            usage = ModelUsageProvenance.model_validate(usage_value)
            binding = VisibleReviewInvocationBinding(
                parent_model_call_id=author.model_call_id, model_call_id=call_id,
                model_id=str(getattr(model, "model", type(model).__name__)),
                model_version=str(getattr(model, "VERSION", type(model).__name__)),
                request_hash=call.request_hash, response_hash=digest(raw),
            )
            audits[index] = ProviderSubcallAudit(**binding.model_dump(mode="python"), lane="direct", outcome="winner", usage=usage)
            return raw, binding
        except BaseException as exc:
            if not isinstance(exc, ModelUsageAdmissionError):
                timeout = isinstance(exc, (TimeoutError, asyncio.CancelledError))
                audits[index] = ProviderSubcallAudit(
                    purpose="source_review", parent_model_call_id=author.model_call_id, model_call_id=call_id,
                    model_id=str(getattr(model, "model", type(model).__name__)),
                    model_version=str(getattr(model, "VERSION", type(model).__name__)),
                    request_hash=call.request_hash, response_hash=digest(raw) if raw is not None else None,
                    lane="direct", outcome="timeout" if timeout else "exception",
                    failure_code="source_review_timeout" if timeout else "source_review_exception",
                )
            raise

    def subcalls():
        return (*output.provider_subcall_audits, *(audits[i] for i in sorted(audits)))

    try:
        calls = [prepare_meaning_call(prepared=prepared, meaning_index=i) for i in range(2)]
        returned = await asyncio.gather(*(invoke(call, model, i) for i, (call, model) in enumerate(zip(calls, reviewer.meaning_models, strict=True))), return_exceptions=True)
        for result in returned:
            if isinstance(result, BaseException):
                raise result
        meaning_raws = tuple(result[0] for result in returned)
        meaning_bindings = tuple(result[1] for result in returned)
        source_call = prepare_source_call(prepared=prepared, meaning_raw_responses=meaning_raws)
        source_raw = source_binding = None
        if source_call is not None:
            source_raw, source_binding = await invoke(source_call, reviewer.source_model, 2)
        receipt = record_independent_visible_review(
            prepared=prepared, author=author, meaning_reviews=meaning_bindings, meaning_raw_responses=meaning_raws,
            source_review=source_binding, source_raw_response=source_raw,
        )
        evidence = canonical({
            "contract": INDEPENDENT_EVIDENCE_CONTRACT, "requirement_json": request.visible_source_requirement_json,
            "author_request_json": author_request_json, "receipt": receipt.model_dump(mode="json"),
            **({"recall_audits": [a.model_dump(mode="json") for a in recall_audits]} if recall_audits else {}),
        })
        if len(evidence.encode()) > MAX_EVIDENCE_BYTES:
            raise ValueError("independent visible evidence size exceeded")
    except asyncio.CancelledError as exc:
        # Honor cancellation. The outer owner may record these settled/failed
        # subcalls; cancellation never becomes a new author correction here.
        exc.world_v2_validation_technical_failure = ValidationTechnicalFailure(
            "source_review_timeout", model_call_id=author.model_call_id, request_hash=author.request_hash,
            attempted_model_id=output.model_id, attempted_model_version=output.model_version,
            usage=output.usage, provider_subcall_audits=subcalls(),
            failure_detail="visible_independent_review.cancelled",
        )
        raise
    except Exception as exc:
        if isinstance(exc, IndependentVisibleReviewRejected):
            code = "paired_expression_reselection_invalid"
            try:
                detail = rejection_feedback(prepared, exc, (*meaning_bindings, source_binding))
            except ValueError:
                code = "source_review_exception"
                detail = "visible_independent_review.feedback_bound_exceeded"
        else:
            code = "source_review_timeout" if isinstance(exc, TimeoutError) else "source_review_exception"
            detail = "visible_independent_review." + (
                "admission." + str(exc.reason) if isinstance(exc, ModelUsageAdmissionError) else
                "reading_inconclusive" if isinstance(exc, IndependentReviewInconclusive) else type(exc).__name__
            )
        raise ValidationTechnicalFailure(
            code, model_call_id=author.model_call_id, request_hash=author.request_hash,
            attempted_model_id=output.model_id, attempted_model_version=output.model_version,
            usage=output.usage, provider_subcall_audits=subcalls(), failure_detail=detail,
        ) from exc
    return output.model_copy(update={"visible_source_review_json": evidence, "provider_subcall_audits": subcalls()})
