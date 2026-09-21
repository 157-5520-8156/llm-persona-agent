"""One metered, context-aware review; character correction stays with Core."""

import asyncio

from companion_daemon.llm import (
    complete_with_timeout, model_call_scope, model_provider_request_identity_scope,
    model_request_emission_scope,
)

from .deliberation import ModelUsageProvenance, ProviderSubcallAudit, ValidationTechnicalFailure, run_validation_review_once
from .model_usage_budget import ModelUsageAdmissionError
from .visible_grounded_review import (
    GroundedVisibleReviewRejected, prepare_grounded_review, record_grounded_review_receipt,
)
from .visible_independent_review_configuration import GroundedVisibleReviewer
from .visible_rejection_context import rejected_expression_from_review
from .visible_review_evidence_storage import store_review_evidence
from .visible_source_review_receipt import VisibleReviewAuthorBinding, VisibleReviewInvocationBinding
from .visible_source_runtime import canonical, digest, INDEPENDENT_EVIDENCE_CONTRACT, MAX_EVIDENCE_BYTES


async def review_grounded_candidate(
    *, request, output, proposal, source_table, aliases, author_request_json,
    reviewer, recall_audits=(), usage_purpose="source_review",
):
    if not isinstance(reviewer, GroundedVisibleReviewer):
        raise ValidationTechnicalFailure("source_review_exception", failure_detail="grounded reviewer is not configured")
    prepared = prepare_grounded_review(candidate=proposal, source_table=source_table, source_ref_aliases=aliases)
    model = reviewer.source_model
    author = VisibleReviewAuthorBinding(
        model_call_id=output.winning_model_call_id, request_hash=output.winning_request_hash,
        proposal_material_hash=digest(prepared.as_dict()["candidate_json"]),
    )
    call_id = "model-call:" + digest(canonical({
        "parent": author.model_call_id, "stage": "grounded_review", "request_hash": prepared.request_hash,
    }))
    raw = None
    audit = None
    stage = "provider"

    def failure(code, detail, rejected=None):
        return ValidationTechnicalFailure(
            code, model_call_id=author.model_call_id, request_hash=author.request_hash,
            attempted_model_id=output.model_id, attempted_model_version=output.model_version,
            usage=output.usage,
            provider_subcall_audits=(*output.provider_subcall_audits, *((audit,) if audit else ())),
            failure_detail=detail, rejected_expression=rejected,
        )

    async def invoke():
        with (
            model_call_scope(usage_purpose),
            model_request_emission_scope(provider_call_id=call_id, entry_marker=None, completion_marker=None),
            model_provider_request_identity_scope(request_hash=prepared.request_hash, identity_extras=prepared.identity_extras),
        ):
            return await complete_with_timeout(model.complete_json_with_usage(**prepared.request()), timeout_seconds=22.0)

    try:
        # Enter the existing candidate validation phase rather than consuming
        # the author's remaining deadline. This grants time, never another call.
        raw, usage_value = await run_validation_review_once(invoke, timeout_seconds=24.0)
        binding = VisibleReviewInvocationBinding(
            parent_model_call_id=author.model_call_id, model_call_id=call_id,
            model_id=str(getattr(model, "model", type(model).__name__)),
            model_version=str(getattr(model, "VERSION", type(model).__name__)),
            request_hash=prepared.request_hash, response_hash=digest(raw),
        )
        audit = ProviderSubcallAudit(**binding.model_dump(mode="python"), lane="direct", outcome="winner",
                                    usage=ModelUsageProvenance.model_validate(usage_value))
        stage = "receipt"
        receipt = record_grounded_review_receipt(prepared=prepared, author=author, review=binding, raw_response=raw)
        stage = "evidence"
        evidence = store_review_evidence(canonical({
            "contract": INDEPENDENT_EVIDENCE_CONTRACT,
            "requirement_json": request.visible_source_requirement_json,
            "author_request_json": author_request_json,
            "receipt": receipt.model_dump(mode="json"),
            **({"recall_audits": [a.model_dump(mode="json") for a in recall_audits]} if recall_audits else {}),
        }))
        if len(evidence.encode()) > MAX_EVIDENCE_BYTES:
            raise ValueError("grounded review evidence exceeds carrier bound")
    except BaseException as exc:
        if audit is None and not isinstance(exc, ModelUsageAdmissionError):
            timeout = isinstance(exc, (TimeoutError, asyncio.CancelledError))
            audit = ProviderSubcallAudit(
                purpose="source_review", parent_model_call_id=author.model_call_id, model_call_id=call_id,
                model_id=str(getattr(model, "model", type(model).__name__)),
                model_version=str(getattr(model, "VERSION", type(model).__name__)),
                request_hash=prepared.request_hash, response_hash=digest(raw) if raw is not None else None,
                lane="direct", outcome="timeout" if timeout else "exception",
                failure_code="source_review_timeout" if timeout else "source_review_exception",
            )
        if isinstance(exc, asyncio.CancelledError):
            exc.world_v2_validation_technical_failure = failure("source_review_timeout", f"visible_grounded_review.{stage}.cancelled")
            raise
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, GroundedVisibleReviewRejected):
            detail = exc.feedback
            if len(detail) <= 3900:
                raise failure("paired_expression_reselection_invalid", detail, rejected_expression_from_review(prepared)) from exc
            raise failure("source_review_exception", "visible_grounded_review.feedback_bound_exceeded") from exc
        code = "source_review_timeout" if isinstance(exc, TimeoutError) else "source_review_exception"
        reason = "admission." + str(exc.reason) if isinstance(exc, ModelUsageAdmissionError) else type(exc).__name__
        raise failure(code, f"visible_grounded_review.{stage}.{reason}") from exc
    return output.model_copy(update={
        "visible_source_review_json": evidence,
        "provider_subcall_audits": (*output.provider_subcall_audits, audit),
    })
