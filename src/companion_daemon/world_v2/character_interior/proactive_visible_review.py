"""Whole-candidate review inside the sole proactive role's Core boundary.

The existing StructuredRole still authors and normalizes every decision. This
adapter captures the actual metered invocation and adds evidence before Core
can record its terminal. Core alone owns the one constrained correction.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
import json

from companion_daemon.llm import model_request_emission_scope
from ..deliberation import (
    AuthoredCandidateInvocationAudit,
    ModelOutput,
    ModelUsageProvenance,
    ValidationTechnicalFailure,
)
from ..visible_source_author_request import prepare_proactive_visible_source_author_request
from ..visible_source_proactive import (
    CAPABILITY_CONTRACT,
    DECISION_CONTRACT,
    REQUIREMENT_KEY,
    qualified_input,
    record_output,
)
from ..visible_source_runtime import canonical, digest, review_candidate
from .inbound_turn import _recall_control_transfer_audit, _sanitized_role_technical_failure
from .ports import _InteriorRoleResult, _RoleResultContractError
from .structured_role import StructuredCharacterRoleFaculty


@dataclass
class _Invocation:
    owner_task: asyncio.Task | None = None
    parameters: dict | None = None
    raw: str | None = None
    usage: ModelUsageProvenance | None = None


_INVOCATION: ContextVar[_Invocation | None] = ContextVar(
    "proactive_review_invocation", default=None
)


class _MeteredProactiveModel:
    """Task-local metering over the same injected model, limits and HTTP pool."""

    def __init__(self, model):
        self._wrapped = model

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    async def complete_json(self, messages, *, temperature=0.8, **kwargs):
        invocation = _INVOCATION.get()
        if invocation is None or invocation.owner_task is not asyncio.current_task():
            return await self._wrapped.complete_json(messages, temperature=temperature, **kwargs)
        invocation.parameters = {"messages": messages, "temperature": temperature, **kwargs}
        operation = getattr(self._wrapped, "complete_json_with_usage", None)
        if not callable(operation):
            raise TypeError("required proactive author has no metered tool operation")
        raw, usage = await operation(messages, temperature=temperature, **kwargs)
        invocation.raw = raw
        invocation.usage = ModelUsageProvenance.model_validate(usage)
        return raw


class ReviewedProactiveStructuredRoleFaculty(StructuredCharacterRoleFaculty):
    def __init__(
        self, *, reviewer, expression_capabilities, visible_source_review_version="1", **kwargs
    ):
        if type(visible_source_review_version) is not str or visible_source_review_version not in {"1", "2", "3", "4", "5", "6", "7"}:
            raise ValueError("unsupported visible source review version")
        if visible_source_review_version != "1" and not callable(
            getattr(reviewer, "complete_json_with_usage", None)
        ):
            raise ValueError("versioned source review requires the explicit metered source reviewer")
        super().__init__(**kwargs)
        self._visible_source_review_version = visible_source_review_version
        self._model = _MeteredProactiveModel(self._model)
        self._visible_reviewer = reviewer
        self._visible_capabilities = expression_capabilities
        self._rejected: OrderedDict[str, tuple[tuple, tuple]] = OrderedDict()

    def _remember_rejected(self, request, candidate, reviews):
        previous, _ = self._rejected.get(request.inner_turn_id, ((), ()))
        candidates = (*previous, candidate)
        self._rejected[request.inner_turn_id] = (candidates, reviews)
        self._rejected.move_to_end(request.inner_turn_id)
        while len(self._rejected) > 128:
            self._rejected.popitem(last=False)
        return candidates

    def _candidate_audit(self, invocation, *, model_call_id, request_hash):
        return AuthoredCandidateInvocationAudit(
            purpose="proactive_visible_candidate",
            model_call_id=model_call_id,
            request_hash=request_hash.removeprefix("sha256:"),
            response_hash=digest(invocation.raw),
            model_id=self._model_id,
            model_version=self._model_version,
            outcome="validation_rejected",
            usage=invocation.usage,
        )

    def _fail_review(self, *, request, author, candidate, prior_candidates, failure):
        semantic_rejection = failure.failure_code == "paired_expression_reselection_invalid"
        if not semantic_rejection:
            candidate = candidate.model_copy(update={"outcome": "validation_unresolved"})
        candidates = (*prior_candidates, candidate)
        self._remember_rejected(request, candidate, failure.provider_subcall_audits)
        if semantic_rejection and request.correction_ordinal == 0:
            raise _RoleResultContractError(
                "role_result_schema_invalid",
                detail=failure.failure_detail[:4096],
                response_hash=author.response_hash,
                request_hash=author.request_hash,
                model_call_id=author.model_call_id,
            ) from failure
        if semantic_rejection:
            failure.failure_code = "authored_expression_reselection_invalid"
        failure.authored_candidate_audits = candidates
        failure.usage = None
        self._rejected.pop(request.inner_turn_id, None)
        raise _sanitized_role_technical_failure(
            failure, failure_code=failure.failure_code
        ) from None

    @staticmethod
    def _capability_view(manifest):
        view = StructuredCharacterRoleFaculty._capability_view(manifest)
        if manifest is not None and manifest.payload.get("contract") == CAPABILITY_CONTRACT:
            view["payload"] = {
                key: value for key, value in view["payload"].items() if key != REQUIREMENT_KEY
            }
        return view

    async def _complete(self, request):
        if request.purpose != "proactive_contact":
            return await super()._complete(request)
        manifest = request.capability_manifest
        if (
            manifest is not None
            and manifest.payload.get("contract") == "character-interior-proactive-capability.1"
            and manifest.capability_ref.startswith("capability:proactive:")
        ):
            # Atomic inbound authoring can be enabled independently. Historical
            # proactive capability bytes retain their original Structured path.
            return await super()._complete(request)
        from ..proactive_action import (
            _ProactiveGroundingViolation,
            _materialize_interior_proactive_draft,
            _validate_proactive_grounding,
            proactive_draft_from_role_result,
        )

        original = qualified_input(manifest)
        payload = json.loads(manifest.payload_json)
        if (
            canonical(payload["expression_capabilities"])
            != canonical(self._visible_capabilities.prompt_value())
            or payload["world_id"] != request.snapshot.world_id
            or payload["actor_ref"] != request.snapshot.actor_ref
        ):
            raise RuntimeError("required proactive capability changed deployment subject")
        tool = self._tool_contract(request)
        messages = self._messages(request, contract=self._resolve_contract(request))
        request_hash = self._provider_request_hash(messages=messages, tool_contract=tool)
        model_call_id = self._model_call_id(request=request, request_hash=request_hash)
        invocation = _Invocation(owner_task=asyncio.current_task())
        token = _INVOCATION.set(invocation)
        try:
            with model_request_emission_scope(
                provider_call_id=model_call_id, entry_marker=None, completion_marker=None
            ):
                normalized = await super()._complete(request)
        except _RoleResultContractError as exc:
            if invocation.raw is not None and invocation.usage is not None:
                _, reviews = self._rejected.get(request.inner_turn_id, ((), ()))
                candidate = self._candidate_audit(
                    invocation, model_call_id=model_call_id, request_hash=request_hash
                )
                candidates = self._remember_rejected(request, candidate, reviews)
                if request.correction_ordinal == 1:
                    self._rejected.pop(request.inner_turn_id, None)
                    failure = ValidationTechnicalFailure(
                        "authored_expression_reselection_invalid",
                        model_call_id=model_call_id,
                        request_hash=request_hash.removeprefix("sha256:"),
                        attempted_model_id=self._model_id,
                        attempted_model_version=self._model_version,
                        authored_candidate_audits=candidates,
                        provider_subcall_audits=reviews,
                        failure_detail=exc.detail,
                    )
                    raise _sanitized_role_technical_failure(
                        failure, failure_code=failure.failure_code
                    ) from None
            raise
        except asyncio.CancelledError as exc:
            # Only the enclosing Deliberation deadline may promote this
            # evidence to a technical result. External cancellation still
            # propagates through Core without authoring a terminal decision.
            candidates, reviews = self._rejected.pop(request.inner_turn_id, ((), ()))
            recall, _ = _recall_control_transfer_audit(request)
            if recall is not None and all(
                item.model_call_id != recall.model_call_id for item in candidates
            ):
                candidates = (*candidates, recall)
            if candidates or reviews:
                exc.world_v2_validation_technical_failure = ValidationTechnicalFailure(
                    "authored_subcall_timeout",
                    model_call_id=model_call_id,
                    request_hash=request_hash.removeprefix("sha256:"),
                    attempted_model_id=self._model_id,
                    attempted_model_version=self._model_version,
                    authored_candidate_audits=candidates,
                    provider_subcall_audits=reviews,
                )
            raise
        except Exception as exc:
            # A failed corrective author must still retire the preceding paid
            # candidate and reviewer. No completed result or usage is invented
            # for the failed current invocation.
            from ..model_usage_budget import BackgroundSpendCapDenied, ModelUsageAdmissionError

            candidates, reviews = self._rejected.pop(request.inner_turn_id, ((), ()))
            recall, _ = _recall_control_transfer_audit(request)
            if recall is not None and all(
                item.model_call_id != recall.model_call_id for item in candidates
            ):
                candidates = (*candidates, recall)
            if invocation.raw is not None:
                candidate = self._candidate_audit(
                    invocation, model_call_id=model_call_id, request_hash=request_hash
                ).model_copy(update={"outcome": "validation_unresolved"})
                candidates = (*candidates, candidate)
            denied = isinstance(exc, ModelUsageAdmissionError)
            code = (
                exc.reason
                if isinstance(exc, BackgroundSpendCapDenied)
                else "model_usage_admission_failed"
                if denied
                else "authored_subcall_timeout"
                if isinstance(exc, TimeoutError)
                else "authored_subcall_exception"
            )
            failure = ValidationTechnicalFailure(
                code,
                model_call_id=None if denied else model_call_id,
                request_hash=None if denied else request_hash.removeprefix("sha256:"),
                attempted_model_id=self._model_id,
                attempted_model_version=self._model_version,
                authored_candidate_audits=candidates,
                provider_subcall_audits=reviews,
                failure_detail="proactive author provider failure: " + type(exc).__name__,
            )
            raise _sanitized_role_technical_failure(failure, failure_code=code) from None
        finally:
            _INVOCATION.reset(token)
        result = _InteriorRoleResult.model_validate(normalized)
        result = result.model_copy(
            update={"author_usage_json": canonical(invocation.usage.model_dump(mode="json"))}
        )
        # Recall is still the Core-owned control transfer. Its eventual complete
        # decision is reviewed against this original selected Capsule.
        if result.status != "decision":
            return result.model_dump(mode="python")
        author = result.author_lineage
        parameters = invocation.parameters
        prior_candidates, prior_reviews = self._rejected.get(request.inner_turn_id, ((), ()))
        recall, _ = _recall_control_transfer_audit(request)
        if recall is not None and all(
            item.model_call_id != recall.model_call_id for item in prior_candidates
        ):
            prior_candidates = (*prior_candidates, recall)
        candidate = self._candidate_audit(
            invocation,
            model_call_id=author.model_call_id,
            request_hash=author.request_hash,
        )
        output = None
        try:
            body = prepare_proactive_visible_source_author_request(
                **parameters,
                identity_extras=self._provider_identity_extras(tool_contract=tool),
                expected_request_hash=author.request_hash.removeprefix("sha256:"),
            )
            draft = proactive_draft_from_role_result(
                decision=result, expression_capabilities=self._visible_capabilities
            )
            try:
                draft = _validate_proactive_grounding(draft=draft, request=original)
            except _ProactiveGroundingViolation as exc:
                if exc.code != "proactive_world_claim_source_lane_mismatch":
                    raise
                raise ValidationTechnicalFailure(
                    "paired_expression_reselection_invalid",
                    model_call_id=author.model_call_id,
                    request_hash=author.request_hash.removeprefix("sha256:"),
                    attempted_model_id=self._model_id,
                    attempted_model_version=self._model_version,
                    provider_subcall_audits=prior_reviews,
                    failure_detail=canonical(
                        {
                            "code": exc.code,
                            "path": exc.path,
                            "constraint": "Use only the original pinned source catalog for each claim; choose the complete decision again.",
                        }
                    ),
                ) from exc
            proposal = _materialize_interior_proactive_draft(
                draft=draft,
                request=original,
                target=payload["target_ref"],
                expression_capabilities=self._visible_capabilities,
                grounding_outcome="accepted" if draft.beats else "not_required",
            )
            output = ModelOutput(
                model_id=author.model_id,
                model_version=author.model_version,
                raw_proposal=proposal.model_dump(mode="json"),
                winning_model_call_id=author.model_call_id,
                winning_request_hash=author.request_hash.removeprefix("sha256:"),
                input_tokens=invocation.usage.input_tokens,
                output_tokens=invocation.usage.output_tokens,
                usage=invocation.usage,
                authored_candidate_audits=prior_candidates,
                provider_subcall_audits=prior_reviews,
            )
            output = await review_candidate(
                request=original,
                output=output,
                author_request_json=body,
                reviewer=self._visible_reviewer,
                review_version=self._visible_source_review_version,
            )
            try:
                recorded = record_output(output)
            except (TypeError, ValueError) as exc:
                raise ValidationTechnicalFailure(
                    "source_review_exception",
                    model_call_id=author.model_call_id,
                    request_hash=author.request_hash.removeprefix("sha256:"),
                    attempted_model_id=self._model_id,
                    attempted_model_version=self._model_version,
                    provider_subcall_audits=output.provider_subcall_audits,
                    failure_detail="reviewed proactive completed output is not a bounded carrier",
                ) from exc
        except asyncio.CancelledError as exc:
            self._rejected.pop(request.inner_turn_id, None)
            # The current author completed, but its pending review did not.
            # Preserve only completed evidence; the provider usage ledger
            # independently retains any unknown charge for the cancelled RPC.
            exc.world_v2_validation_technical_failure = ValidationTechnicalFailure(
                "source_review_timeout",
                attempted_model_id=str(
                    getattr(self._visible_reviewer, "model", type(self._visible_reviewer).__name__)
                ),
                attempted_model_version=str(
                    getattr(self._visible_reviewer, "VERSION", type(self._visible_reviewer).__name__)
                ),
                authored_candidate_audits=(
                    *prior_candidates,
                    candidate.model_copy(update={"outcome": "validation_unresolved"}),
                ),
                provider_subcall_audits=prior_reviews,
            )
            raise
        except ValidationTechnicalFailure as exc:
            self._fail_review(
                request=request,
                author=author,
                candidate=candidate,
                prior_candidates=prior_candidates,
                failure=exc,
            )
        except (TypeError, ValueError) as exc:
            # Local preparation/receipt errors are infrastructure failures.
            # Only the typed source-lane violation above can spend correction.
            failure = ValidationTechnicalFailure(
                "source_review_exception",
                model_call_id=author.model_call_id,
                request_hash=author.request_hash.removeprefix("sha256:"),
                attempted_model_id=self._model_id,
                attempted_model_version=self._model_version,
                provider_subcall_audits=(
                    output.provider_subcall_audits if output is not None else prior_reviews
                ),
                failure_detail=("proactive visible review preparation failed: " + type(exc).__name__ + ": " + str(exc)[:240]),
            )
            self._fail_review(
                request=request,
                author=author,
                candidate=candidate,
                prior_candidates=prior_candidates,
                failure=failure,
            )
        self._rejected.pop(request.inner_turn_id, None)
        return result.model_copy(
            update={"decision": {"contract": DECISION_CONTRACT, **recorded}}
        ).model_dump(mode="python")
