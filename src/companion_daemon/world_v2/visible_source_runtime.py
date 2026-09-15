"""Explicit original-pin whole-candidate gate; no role or retrieval authoring."""

from __future__ import annotations

from companion_daemon.world_v2.visible_review_protocols import SUPPORTED_REVIEW_VERSIONS
import hashlib
import json

REQUIRED_POLICY = "visible-source-review-required.1"
PINNED_PROTOCOL_REQUIREMENT = "visible-source-review-required.2"
EVIDENCE_CONTRACT = "visible-source-runtime-evidence.1"
RECALL_EVIDENCE_CONTRACT = "visible-source-runtime-evidence.2"
INDEPENDENT_EVIDENCE_CONTRACT = "visible-source-runtime-evidence.3"
# The review evidence carries the original requirement, the complete author
# request and the accepted receipt.  Successful records reached 423KB, so the
# former 512KB carrier bound rejected otherwise-valid, already-billed reviews.
# Align the evidence with the 1MB visible-review audit carrier instead.
MAX_EVIDENCE_BYTES = 1_048_576
REQUIRED_CAPABILITY_PREFIX = "inbound-reviewed-turn-capability:sha256:"


def requires_visible_review_capability(capability_ref):
    from .visible_source_proactive import CAPABILITY_PREFIX
    return capability_ref.startswith((REQUIRED_CAPABILITY_PREFIX, CAPABILITY_PREFIX))


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def result_recall_audits(result):
    """Use the independently recorded author result, not the review carrier."""
    from .recall_runtime import TrustedRecallTrace, verify_trusted_recall_trace
    from .recall_audit import RecallAuditTrace

    traces = [getattr(result, "recall_trace", None), getattr(result, "prefetch_trace", None)]
    call = getattr(result, "winning_model_call_id", None) or getattr(result, "model_call_id", None)
    traces.extend(item.trace for item in getattr(result, "presented_prefetch_traces", ())
                  if item.model_call_id == call)
    audits = []
    for trace in traces:
        if isinstance(trace, TrustedRecallTrace):
            trace = verify_trusted_recall_trace(trace)
        if isinstance(trace, RecallAuditTrace) and trace not in audits:
            audits.append(trace)
    return tuple(audits)


def compile_requirement(*, request, capsule, review_protocol=None):
    from .visible_source_composer import compile_visible_source_table
    from .visible_independent_review_receipt import REVIEW_PROTOCOLS

    if review_protocol not in {None, *REVIEW_PROTOCOLS.values()}:
        raise ValueError("unsupported pinned visible review protocol")

    table = compile_visible_source_table(request=request, capsule=capsule)
    return canonical(
        {
            "contract": PINNED_PROTOCOL_REQUIREMENT if review_protocol else REQUIRED_POLICY,
            "original_input_json": canonical(request.model_dump(mode="json")),
            "original_input_hash": digest(canonical(request.model_dump(mode="json"))),
            "source_table_json": table.payload_json,
            **({"review_protocol": review_protocol} if review_protocol else {}),
        }
    )


def requirement_table(raw):
    from .deliberation import ModelInput
    from .visible_source_composer import VisibleSourceTable

    if not isinstance(raw, str) or len(raw.encode()) > MAX_EVIDENCE_BYTES:
        raise ValueError("visible review requirement is unavailable or oversized")
    value = json.loads(raw)
    from .visible_independent_review_receipt import REVIEW_PROTOCOLS

    pinned = value.get("contract") == PINNED_PROTOCOL_REQUIREMENT
    keys = {"contract", "original_input_json", "original_input_hash", "source_table_json"}
    if pinned:
        keys.add("review_protocol")
    if (
        set(value) != keys
        or value.get("contract") not in {REQUIRED_POLICY, PINNED_PROTOCOL_REQUIREMENT}
        or (pinned and value.get("review_protocol") not in REVIEW_PROTOCOLS.values())
        or canonical(value) != raw
    ):
        raise ValueError("visible review requirement contract is invalid")
    original = ModelInput.model_validate_json(value["original_input_json"])
    if (
        original.visible_source_requirement_json is not None
        or canonical(original.model_dump(mode="json")) != value["original_input_json"]
        or digest(value["original_input_json"]) != value["original_input_hash"]
    ):
        raise ValueError("visible review original input changed")
    table = VisibleSourceTable(payload_json=value["source_table_json"])
    pin = table.as_dict()["pin"]
    expected = {
        "capsule_id": original.capsule_id,
        "trigger_ref": original.trigger_ref,
        "world_revision": original.evaluated_world_revision,
        "deliberation_revision": original.evaluated_deliberation_revision,
        "ledger_sequence": original.evaluated_ledger_sequence,
        "model_input_hash": value["original_input_hash"],
        "model_content_hash": digest(original.model_content_json),
    }
    if any(pin.get(k) != v for k, v in expected.items()):
        raise ValueError("visible review table differs from its original input")
    return table


def _verify_original_capability(requirement, lineage, author_request_json=None):
    """The immutable role lineage commits to the original host requirement.

    A receipt may repeat the body, but cannot replace it without changing the
    capability issued before the author ran. No current Context is consulted.
    """
    from .deliberation import ModelInput
    from .character_interior.inbound_turn import _model_input_material

    if lineage is not None and lineage.purpose == "proactive_contact":
        from .visible_source_proactive import verify_original_capability
        return verify_original_capability(
            requirement=requirement, lineage=lineage, author_request_json=author_request_json
        )
    if lineage is None or lineage.purpose != "inbound_turn":
        raise ValueError("visible review requires its original inbound role lineage")
    table = requirement_table(requirement)
    value = json.loads(requirement)
    original = ModelInput.model_validate_json(value["original_input_json"])
    qualified = original.model_copy(update={"visible_source_requirement_json": requirement})
    capability = _model_input_material(qualified, transport_operation="complete")
    if lineage.capability_ref != REQUIRED_CAPABILITY_PREFIX + digest(canonical(capability)):
        raise ValueError("visible review requirement differs from the original role capability")
    pin = table.as_dict()["pin"]
    if (lineage.causal_world_id is not None and lineage.causal_world_id != pin["world_id"]) or (
        lineage.causal_actor_ref is not None and lineage.causal_actor_ref != pin["actor_ref"]
    ):
        raise ValueError("visible review actor/world differs from the original role lineage")
    return original


async def review_candidate(*, request, output, author_request_json, reviewer, review_version="1"):
    from companion_daemon.llm import (
        model_call_scope,
        model_provider_request_identity_scope,
        model_request_emission_scope,
    )
    from .deliberation import ModelUsageProvenance, ProviderSubcallAudit, ValidationTechnicalFailure
    from .proposal_envelope import DecisionProposal, validate_proposal_envelope
    from .visible_source_review_receipt import (
        prepare_visible_source_review,
        record_visible_source_review,
        VisibleReviewAuthorBinding,
        VisibleReviewInvocationBinding,
        VisibleSourceReviewRejected,
    )
    from .visible_source_author_request import verify_visible_source_author_request
    from .visible_source_closure_protocol import VisibleSourceClosureWireFailure

    aliases = verify_visible_source_author_request(
        author_request_json, expected_request_hash=output.winning_request_hash
    )
    if review_version not in SUPPORTED_REVIEW_VERSIONS:
        raise ValueError("visible source review version is unsupported")
    requirement = request.visible_source_requirement_json
    table = requirement_table(requirement)
    from .visible_independent_review_receipt import REVIEW_PROTOCOLS
    from .visible_independent_review_receipt import independent_review_protocol
    if json.loads(requirement).get("review_protocol") != independent_review_protocol(review_version):
        raise ValidationTechnicalFailure("source_review_exception", failure_detail="visible review protocol differs from original requirement")
    proposal = validate_proposal_envelope(output.raw_proposal)
    if not isinstance(proposal, DecisionProposal) and (
        proposal.proposed_changes or proposal.action_intents
    ):
        raise ValidationTechnicalFailure(
            "source_review_exception",
            failure_detail="required whole review cannot use a legacy visible carrier",
        )
    if not isinstance(proposal, DecisionProposal) or not any(
        c.kind == "expression_plan_transition" for c in proposal.proposed_changes
    ):
        return output.model_copy(
            update={
                "visible_source_review_json": canonical(
                    {
                        "contract": INDEPENDENT_EVIDENCE_CONTRACT if review_version in REVIEW_PROTOCOLS else EVIDENCE_CONTRACT,
                        "requirement_json": requirement,
                        "author_request_json": author_request_json,
                        "receipt": None,
                    }
                )
            }
        )
    from .recall_runtime import verify_trusted_recall_trace
    from .visible_recall_sources import supplement_recalled_prehistory

    table, recall_audits = supplement_recalled_prehistory(
        table=table,
        audits=tuple(verify_trusted_recall_trace(trace) for trace in request.visible_source_recall_traces),
        author_request_json=author_request_json,
    ) if request.visible_source_recall_traces else (table, ())
    if review_version in REVIEW_PROTOCOLS:
        from .visible_independent_review_runtime import review_independent_candidate
        return await review_independent_candidate(
            request=request, output=output, proposal=proposal, source_table=table, aliases=aliases,
            author_request_json=author_request_json, reviewer=reviewer, recall_audits=recall_audits, review_version=review_version,
        )
    prepared = prepare_visible_source_review(
        candidate=proposal, source_table=table, source_ref_aliases=aliases,
        review_version=review_version,
    )
    value = prepared.as_dict()
    parent = output.winning_model_call_id
    call_id = "model-call:" + digest(
        canonical(
            {
                "parent": parent,
                "purpose": "visible_source_review",
                "request_hash": value["request_hash"],
            }
        )
    )
    raw = None
    usage = None
    subcall = None
    rejection = None
    failure_detail = None
    try:
        operation = getattr(reviewer, "complete_json_with_usage", None)
        if not callable(operation):
            raise ValueError("visible review requires the explicit metered tool provider")
        with (
            model_call_scope("source_review"),
            model_request_emission_scope(
                provider_call_id=call_id, entry_marker=None, completion_marker=None
            ),
            model_provider_request_identity_scope(
                request_hash=value["request_hash"], identity_extras=value["identity_extras"]
            ),
        ):
            raw, usage_value = await operation(**value["request"])
        usage = ModelUsageProvenance.model_validate(usage_value)
        binding = VisibleReviewInvocationBinding(
            parent_model_call_id=parent,
            model_call_id=call_id,
            model_id=str(getattr(reviewer, "model", type(reviewer).__name__)),
            model_version=str(getattr(reviewer, "VERSION", type(reviewer).__name__)),
            request_hash=value["request_hash"],
            response_hash=digest(raw),
        )
        subcall = ProviderSubcallAudit(
            **binding.model_dump(mode="python"), lane="direct", outcome="winner", usage=usage
        )
        try:
            receipt = record_visible_source_review(
                prepared=prepared,
                author=VisibleReviewAuthorBinding(
                    model_call_id=parent,
                    request_hash=output.winning_request_hash,
                    proposal_material_hash=digest(value["candidate_json"]),
                ),
                review=binding,
                raw_verdict=raw,
            )
        except VisibleSourceReviewRejected as exc:
            rejection = exc
            if review_version in {"2", "3", "4", "5", "6", "7", "8"}:
                from .visible_source_rejection_feedback import rejection_feedback

                failure_detail = rejection_feedback(prepared=prepared, rejection=exc, review=binding)
            else:
                failure_detail = "完整表达的来源审核未闭合，请按原材料重选完整表达：" + canonical(exc.verdict.model_dump(mode="json"))
    except Exception as exc:
        failure_code = (
            "source_review_timeout" if isinstance(exc, TimeoutError) else "source_review_exception"
        )
        # A returned invalid verdict still has a real, billable provider result.
        # Its audit remains a completed invocation; it is not a passing receipt.
        from .model_usage_budget import ModelUsageAdmissionError

        if subcall is None and not isinstance(exc, ModelUsageAdmissionError):
            subcall = ProviderSubcallAudit(
                purpose="source_review",
                parent_model_call_id=parent,
                model_call_id=call_id,
                request_hash=value["request_hash"],
                model_id=str(getattr(reviewer, "model", type(reviewer).__name__)),
                model_version=str(getattr(reviewer, "VERSION", type(reviewer).__name__)),
                lane="direct",
                outcome="timeout" if isinstance(exc, TimeoutError) else "exception",
                failure_code=failure_code,
            )
        if isinstance(exc, ModelUsageAdmissionError):
            detail = "visible_source_review.admission." + str(
                getattr(exc, "reason", "usage_admission_failed")
            )
        elif isinstance(exc, VisibleSourceClosureWireFailure):
            detail = "visible_source_review." + type(exc).__name__ + "." + str(
                getattr(exc, "code", "unknown")
            )
            if getattr(exc, "beat_index", None) is not None:
                detail += ".beat:" + str(exc.beat_index)
            if getattr(exc, "field", None):
                detail += ".field:" + str(exc.field)[:80]
        else:
            detail = "visible_source_review." + type(exc).__name__
        raise ValidationTechnicalFailure(
            failure_code,
            model_call_id=parent,
            request_hash=output.winning_request_hash,
            attempted_model_id=output.model_id,
            attempted_model_version=output.model_version,
            usage=output.usage,
            provider_subcall_audits=(
                *output.provider_subcall_audits,
                *((subcall,) if subcall is not None else ()),
            ),
            failure_detail=detail,
        ) from exc
    if rejection is not None:
        raise ValidationTechnicalFailure(
            "paired_expression_reselection_invalid",
            model_call_id=parent,
            request_hash=output.winning_request_hash,
            attempted_model_id=output.model_id,
            attempted_model_version=output.model_version,
            usage=output.usage,
            provider_subcall_audits=(*output.provider_subcall_audits, subcall),
            failure_detail=failure_detail,
        ) from rejection
    evidence = canonical(
        {
            "contract": RECALL_EVIDENCE_CONTRACT if recall_audits else EVIDENCE_CONTRACT,
            "requirement_json": requirement,
            "author_request_json": author_request_json,
            "receipt": receipt.model_dump(mode="json"),
            **({"recall_audits": [audit.model_dump(mode="json") for audit in recall_audits]}
               if recall_audits else {}),
        }
    )
    if len(evidence.encode()) > MAX_EVIDENCE_BYTES:
        # The carrier limit is checked after a completed, billable review.
        # Retain its independent invocation even though no receipt can escape.
        raise ValidationTechnicalFailure(
            "source_review_exception",
            model_call_id=parent,
            request_hash=output.winning_request_hash,
            attempted_model_id=output.model_id,
            attempted_model_version=output.model_version,
            usage=output.usage,
            provider_subcall_audits=(*output.provider_subcall_audits, subcall),
            failure_detail="visible_source_review.evidence_size_exceeded",
        )
    return output.model_copy(
        update={
            "visible_source_review_json": evidence,
            "provider_subcall_audits": (*output.provider_subcall_audits, subcall),
        }
    )


def verify_output(*, request, output):
    """The requirement comes from the original caller, never from this output."""
    raw = output.visible_source_review_json
    if request.visible_source_requirement_json is None:
        if raw is not None:
            raise ValueError("unexpected visible review evidence")
        return
    if raw is None:
        raise ValueError("required visible review receipt is missing")
    from .recall_runtime import verify_trusted_recall_trace
    verify_evidence(
        raw=raw,
        proposal=output.raw_proposal,
        requirement=request.visible_source_requirement_json,
        author_call=output.winning_model_call_id,
        author_request_hash=output.winning_request_hash,
        subcalls=output.provider_subcall_audits,
        recall_audits=tuple(verify_trusted_recall_trace(trace)
                            for trace in request.visible_source_recall_traces),
    )


def verify_evidence(*, raw, proposal, requirement, author_call, author_request_hash, subcalls,
                    recall_audits=()):
    from .proposal_envelope import validate_proposal_envelope, DecisionProposal
    from .visible_source_review_receipt import (
        VisibleSourceReviewReceipt,
        prepare_visible_source_review,
        verify_visible_source_review_receipt,
        VisibleReviewAuthorBinding,
        VisibleReviewInvocationBinding,
        review_version_for_contract,
    )

    if not isinstance(raw, str) or len(raw.encode()) > MAX_EVIDENCE_BYTES:
        raise ValueError("visible review evidence is missing or oversized")
    value = json.loads(raw)
    if (
        value.get("contract") not in {EVIDENCE_CONTRACT, RECALL_EVIDENCE_CONTRACT, INDEPENDENT_EVIDENCE_CONTRACT}
        or value.get("requirement_json") != requirement
        or canonical(value) != raw
    ):
        raise ValueError("visible review requirement mismatch")
    table = requirement_table(requirement)
    from .visible_independent_review_receipt import REVIEW_PROTOCOLS
    independent = value["contract"] == INDEPENDENT_EVIDENCE_CONTRACT
    pinned_protocol = json.loads(requirement).get("review_protocol")
    if independent != (pinned_protocol in REVIEW_PROTOCOLS.values()):
        raise ValueError("visible receipt protocol differs from original requirement")
    proposal = validate_proposal_envelope(proposal)
    if not isinstance(proposal, DecisionProposal) and (
        proposal.proposed_changes or proposal.action_intents
    ):
        raise ValueError("required whole review cannot use a legacy visible carrier")
    visible = isinstance(proposal, DecisionProposal) and any(
        c.kind == "expression_plan_transition" for c in proposal.proposed_changes
    )
    if visible and any(
        c.payload.value().get("visible_source_review_policy") != REQUIRED_POLICY
        for c in proposal.proposed_changes
        if c.kind == "expression_plan_transition"
    ):
        raise ValueError("original candidate lost its required review policy")
    if not visible:
        if value.get("receipt") is not None:
            raise ValueError("non-visible decision has an unrelated receipt")
        return None
    if independent:
        from .visible_independent_review_receipt import IndependentVisibleReviewReceipt
        receipt = IndependentVisibleReviewReceipt.model_validate_json(canonical(value["receipt"]))
    else:
        receipt = VisibleSourceReviewReceipt.model_validate_json(canonical(value["receipt"]))
    from .visible_source_author_request import verify_visible_source_author_request

    aliases = verify_visible_source_author_request(
        value["author_request_json"], expected_request_hash=author_request_hash
    )
    if value["contract"] == RECALL_EVIDENCE_CONTRACT or (independent and "recall_audits" in value):
        from .recall_audit import RecallAuditTrace
        from .visible_recall_sources import supplement_recalled_prehistory

        recorded = value.get("recall_audits")
        if not isinstance(recorded, list) or not 1 <= len(recorded) <= 2:
            raise ValueError("visible review lacks its bounded recall audits")
        selected = tuple(RecallAuditTrace.model_validate_json(canonical(item)) for item in recorded)
        if any(audit not in recall_audits for audit in selected):
            raise ValueError("visible review recall differs from the independently recorded author result")
        table, used = supplement_recalled_prehistory(
            table=table, audits=selected, author_request_json=value["author_request_json"],
        )
        if used != selected:
            raise ValueError("visible review recall was not presented to its author")
    elif "recall_audits" in value:
        raise ValueError("legacy visible review cannot carry new recall authority")
    if independent:
        from .visible_independent_review_receipt import (
            prepare_independent_visible_review, verify_independent_visible_review_receipt, receipt_invocations,
        )
        bindings = receipt_invocations(receipt)
        expected = []
        for binding in bindings:
            matching = [s for s in subcalls if s.purpose == "source_review" and s.model_call_id == binding.model_call_id and s.outcome == "winner"]
            if len(matching) != 1:
                raise ValueError("independent review requires every original provider subcall")
            expected.append(VisibleReviewInvocationBinding(**{k: getattr(matching[0], k) for k in VisibleReviewInvocationBinding.model_fields}))
        return verify_independent_visible_review_receipt(
            receipt=receipt,
            expected_prepared=prepare_independent_visible_review(candidate=proposal, source_table=table, source_ref_aliases=aliases, review_protocol=pinned_protocol),
            expected_author=VisibleReviewAuthorBinding(model_call_id=author_call, request_hash=author_request_hash,
                                                       proposal_material_hash=digest(canonical(proposal.model_dump(mode="json")))),
            expected_invocations=tuple(expected),
        )
    prepared = prepare_visible_source_review(
        candidate=proposal, source_table=table, source_ref_aliases=aliases,
        review_version=review_version_for_contract(receipt.contract, family="receipt"),
    )
    matching = [
        s
        for s in subcalls
        if s.purpose == "source_review"
        and s.model_call_id == receipt.review.model_call_id
        and s.outcome == "winner"
    ]
    if len(matching) != 1:
        raise ValueError("visible review requires its original provider subcall")
    subcall = matching[0]
    return verify_visible_source_review_receipt(
        receipt=receipt,
        expected_prepared=prepared,
        expected_author=VisibleReviewAuthorBinding(
            model_call_id=author_call,
            request_hash=author_request_hash,
            proposal_material_hash=digest(canonical(proposal.model_dump(mode="json"))),
        ),
        expected_review=VisibleReviewInvocationBinding(
            **{k: getattr(subcall, k) for k in VisibleReviewInvocationBinding.model_fields}
        ),
    )


def recorded_candidate_requires_review(*, audit, model_result_audits):
    from .proposal_audit_schemas import RecordedModelResultAudit

    for row in model_result_audits:
        if row.model_result_ref == audit.model_result_ref:
            recorded = RecordedModelResultAudit.model_validate_json(row.audit_json)
            lineage = recorded.character_interior_lineage
            return lineage is not None and requires_visible_review_capability(lineage.capability_ref)
    return False


def verify_recorded_candidate(*, audit, model_result_audits):
    from .proposal_audit_schemas import RecordedModelResultAudit
    from .proposal_envelope import validate_proposal_envelope
    from .deliberation import ProviderSubcallAudit

    proposal = validate_proposal_envelope(json.loads(audit.proposal_json))
    expression = [c for c in proposal.proposed_changes if c.kind == "expression_plan_transition"]
    if (
        len(expression) != 1
        or expression[0].payload.value().get("visible_source_review_policy") != REQUIRED_POLICY
    ):
        raise ValueError("new visible review policy is missing from the original candidate")
    matches = [row for row in model_result_audits if row.model_result_ref == audit.model_result_ref]
    if len(matches) != 1 or matches[0].audit_contract != "model-result-audit.9":
        raise ValueError("required review has no original .9 model audit")
    row = matches[0]
    parent = RecordedModelResultAudit.model_validate_json(row.audit_json)
    if parent.visible_source_review_json is None:
        raise ValueError("required review carrier is missing")
    value = json.loads(parent.visible_source_review_json)
    requirement = value["requirement_json"]
    original = _verify_original_capability(
        requirement, parent.character_interior_lineage, value.get("author_request_json")
    )
    pin = requirement_table(requirement).as_dict()["pin"]
    context = parent.decision_context
    if context is not None and (
        context.world_revision,
        context.deliberation_revision,
        context.ledger_sequence,
    ) != (
        original.evaluated_world_revision,
        original.evaluated_deliberation_revision,
        original.evaluated_ledger_sequence,
    ):
        raise ValueError("visible review cursor differs from the original model decision context")
    if (
        row.capsule_id != audit.capsule_id
        or row.trigger_ref != audit.trigger_ref
        or row.proposal_hash != audit.proposal_hash
        or pin["capsule_id"] != audit.capsule_id
        or pin["trigger_ref"] != audit.trigger_ref
        or pin["world_revision"] != audit.evaluated_world_revision
    ):
        raise ValueError("review pin differs from its original proposal audit")
    subcalls = []
    for item in model_result_audits:
        recorded = RecordedModelResultAudit.model_validate_json(item.audit_json)
        if (
            recorded.parent_model_call_id != parent.model_call_id
            or recorded.route.router_version != "provider-subcall-audit.1"
            or recorded.route.reason_code != "validation.source_review"
            or recorded.outcome != "winner"
        ):
            continue
        subcalls.append(
            ProviderSubcallAudit(
                purpose="source_review",
                parent_model_call_id=recorded.parent_model_call_id,
                model_call_id=recorded.model_call_id,
                model_id=recorded.model_id,
                model_version=recorded.model_version,
                request_hash=recorded.request_hash,
                response_hash=recorded.response_hash,
                lane="direct",
                outcome="winner",
            )
        )
    receipt = verify_evidence(
        raw=parent.visible_source_review_json,
        proposal=proposal,
        requirement=requirement,
        author_call=parent.model_call_id,
        author_request_hash=parent.request_hash,
        subcalls=subcalls,
        recall_audits=result_recall_audits(parent),
    )
    if receipt is None:
        raise ValueError("visible candidate has no passing receipt")
    return receipt.receipt_hash
