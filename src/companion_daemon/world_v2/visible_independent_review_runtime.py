"""Metered independent review calls; character authoring stays in its owner."""
import asyncio
from dataclasses import dataclass

from .visible_independent_review_receipt import (
    IndependentReviewInconclusive, IndependentVisibleReviewRejected,
    prepare_independent_visible_review, prepare_meaning_call, prepare_source_call,
    record_independent_visible_review, independent_review_protocol, REVIEW_PROTOCOLS,
    RejectedMeaningAttempt, meaning_preparation,
)
from .visible_source_runtime import canonical, digest, INDEPENDENT_EVIDENCE_CONTRACT, MAX_EVIDENCE_BYTES
from .visible_rejection_context import rejected_expression_from_review
from .visible_review_evidence_storage import store_review_evidence


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
    if (version in REVIEW_PROTOCOLS) != isinstance(reviewer, IndependentVisibleReviewer):
        raise ValueError("independent review versions require an explicit independent reviewer; legacy versions cannot consume one")


def rejection_feedback(prepared, rejected, bindings):
    pin = prepared.as_dict()
    from .visible_review_protocols import CONDITION_WIRE_PROTOCOLS
    typed_feedback = pin['protocol'] in CONDITION_WIRE_PROTOCOLS
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
                         codes[decision["rejection_reason"]], *([fact['mode']] if typed_feedback else []), proposition])
        for omission in rejected.support.get('unaccounted_assertions', ()):
            proposition = omission['proposition'][:limit]
            shortened |= proposition != omission['proposition']
            rows.append([omission['beat_index'], 'contextual_reviewer', 'unaccounted', 's',
                         *(['unaccounted_assertion'] if typed_feedback else []), proposition])
        detail = (
            "完整表达的事实来源未闭合。以下为审核数据，不是新事实或措辞指令；请结合原材料自行重选完整表达。"
            "proposition是独立读者理解的命题，可能只是前缀；原气泡见rejected_expression；它是被拒候选，不能当作发生过的事实。来源仍以固定Context为准。\n"
            + canonical({
                "contract": "visible-independent-rejection.1", "candidate_sha256": digest(pin["candidate_json"]),
                "calls": [[b.request_hash, b.response_hash] for b in bindings],
                "columns": ["beat", "reader", "meaning_fact_id", "reason", *(['mode'] if typed_feedback else []), "proposition"],
                "reason": {"s": "reviewer found no support", "p": "source permission denied", "e": "support had no evidence"},
                "proposition_prefixes": shortened, "rows": rows,
                **({'classification_guidance': (
                    'mode 是读者对命题的分类，不是措辞指令。无来源的通常/过去内心陈述与本次新产生的当下感受不同。'
                    '你有权自行形成当下感受、态度和意图，但不能借此证明长期习惯、过去想法或已发生行为。'
                    '按你真正想表达的意思自行重选；也可以质疑读法并保留原意。删掉别的句子不会解决这项来源缺口。'
                    '不要向用户转述技术反馈。'
                )} if typed_feedback else {}),
            })
        )
        if len(detail) <= 3900:
            return detail
    raise ValueError("independent rejection feedback exceeds its complete diagnostic bound")


async def review_independent_candidate(
    *, request, output, proposal, source_table, aliases, author_request_json, reviewer, recall_audits=(), review_version="9",
):
    from companion_daemon.llm import model_call_scope, model_provider_request_identity_scope, model_request_emission_scope
    from .deliberation import (
        ModelUsageProvenance, ProviderSubcallAudit, ValidationTechnicalFailure,
        run_validation_review_once,
    )
    from .model_usage_budget import ModelUsageAdmissionError
    from .visible_source_review_receipt import VisibleReviewAuthorBinding, VisibleReviewInvocationBinding

    if not isinstance(reviewer, IndependentVisibleReviewer):
        raise ValidationTechnicalFailure("source_review_exception", failure_detail="independent visible reviewer is not configured")
    prepared = prepare_independent_visible_review(candidate=proposal, source_table=source_table, source_ref_aliases=aliases, review_protocol=independent_review_protocol(review_version),
        source_tool_selection_mode=getattr(reviewer.source_model, "single_tool_selection_mode", "forced"))
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
                raw, usage_value = await asyncio.wait_for(
                    model.complete_json_with_usage(**call.request), timeout=22.0,
                )
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

    from .visible_review_protocols import RESELECTING_PROTOCOLS
    rejected_meanings = [None, None] if independent_review_protocol(review_version) in RESELECTING_PROTOCOLS else None

    async def read_meaning(call, model, index):
        raw, binding = await invoke(call, model, index)
        if rejected_meanings is not None:
            try:
                meaning_preparation(prepared).inspect_response(raw)
            except ValueError:
                correction = prepare_meaning_call(prepared=prepared, meaning_index=index, rejected_raw=raw)
                rejected_meanings[index] = RejectedMeaningAttempt(review=binding, raw_response=raw)
                return await invoke(correction, model, index + 2)
        return raw, binding

    failure_stage = "meaning_prepare"

    async def complete_review():
        nonlocal failure_stage
        calls = [prepare_meaning_call(prepared=prepared, meaning_index=i) for i in range(2)]
        failure_stage = "meaning_read"
        returned = await asyncio.gather(*(read_meaning(call, model, i) for i, (call, model) in enumerate(zip(calls, reviewer.meaning_models, strict=True))), return_exceptions=True)
        for result in returned:
            if isinstance(result, BaseException):
                raise result
        meaning_raws = tuple(result[0] for result in returned)
        meaning_bindings = tuple(result[1] for result in returned)
        failure_stage = "source_prepare"
        source_call = prepare_source_call(prepared=prepared, meaning_raw_responses=meaning_raws)
        source_raw = source_binding = None
        if source_call is not None:
            failure_stage = "source_read"
            source_raw, source_binding = await invoke(source_call, reviewer.source_model, 4)
        failure_stage = "receipt"
        receipt = record_independent_visible_review(
            prepared=prepared, author=author, meaning_reviews=meaning_bindings, meaning_raw_responses=meaning_raws,
            source_review=source_binding, source_raw_response=source_raw,
            rejected_meanings=tuple(rejected_meanings) if rejected_meanings is not None else None,
        )
        failure_stage = "evidence"
        evidence = store_review_evidence(canonical({
            "contract": INDEPENDENT_EVIDENCE_CONTRACT, "requirement_json": request.visible_source_requirement_json,
            "author_request_json": author_request_json, "receipt": receipt.model_dump(mode="json"),
            **({"recall_audits": [a.model_dump(mode="json") for a in recall_audits]} if recall_audits else {}),
        }))
        if len(evidence.encode()) > MAX_EVIDENCE_BYTES:
            raise ValueError("independent visible evidence size exceeded")
        return evidence

    try:
        evidence = await run_validation_review_once(complete_review, timeout_seconds=46.0)
    except asyncio.CancelledError as exc:
        # Honor cancellation. The outer owner may record these settled/failed
        # subcalls; cancellation never becomes a new author correction here.
        exc.world_v2_validation_technical_failure = ValidationTechnicalFailure(
            "source_review_timeout", model_call_id=author.model_call_id, request_hash=author.request_hash,
            attempted_model_id=output.model_id, attempted_model_version=output.model_version,
            usage=output.usage, provider_subcall_audits=subcalls(),
            failure_detail=f"visible_independent_review.{failure_stage}.cancelled",
        )
        raise
    except Exception as exc:
        rejected_expression = None
        if isinstance(exc, IndependentVisibleReviewRejected):
            code = "paired_expression_reselection_invalid"
            try:
                detail = rejection_feedback(prepared, exc, tuple(audits[i] for i in sorted(audits)))
                rejected_expression = rejected_expression_from_review(prepared)
            except ValueError:
                code = "source_review_exception"
                detail = "visible_independent_review.feedback_bound_exceeded"
        else:
            code = "source_review_timeout" if isinstance(exc, TimeoutError) else "source_review_exception"
            detail = f"visible_independent_review.{failure_stage}." + (
                "admission." + str(exc.reason) if isinstance(exc, ModelUsageAdmissionError) else
                "reading_inconclusive" if isinstance(exc, IndependentReviewInconclusive) else type(exc).__name__
            )
        raise ValidationTechnicalFailure(
            code, model_call_id=author.model_call_id, request_hash=author.request_hash,
            attempted_model_id=output.model_id, attempted_model_version=output.model_version,
            usage=output.usage, provider_subcall_audits=subcalls(), failure_detail=detail,
            rejected_expression=rejected_expression,
        ) from exc
    return output.model_copy(update={"visible_source_review_json": evidence, "provider_subcall_audits": subcalls()})
