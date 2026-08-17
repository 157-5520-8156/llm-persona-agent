"""Regression tests for the 2026-07-20 fallback-rate reduction work.

Covers the remaining same-author reliability seams measured in production:
attempt-deadline awareness for corrective retries, corrective coverage for
non-claim structural rejects, and the pre-failure violation-quoting retry.
"""

from __future__ import annotations

import json
import time

import pytest

from companion_daemon.world_v2 import production_reliability_metrics as metrics
from companion_daemon.world_v2.character_interior.inbound_wire import (
    _ExpressionDraftWire,
)
from companion_daemon.world_v2.deliberation import (
    Deliberation,
    ModelInput,
    ModelOutput,
    ValidationTechnicalFailure,
    fit_secondary_call_timeout,
    remaining_attempt_seconds,
)
from companion_daemon.world_v2.character_interior.inbound_author import (
    _InboundCharacterAuthor as InboundCharacterAuthor,
)
from test_deliberation import _Quick, _Router, _capsule, _decision_raw
from test_character_interior_inbound_author import _request


@pytest.fixture(autouse=True)
def _reset_reliability_counters():
    metrics.reset_for_tests()
    yield
    metrics.reset_for_tests()


# --- attempt-deadline plumbing -------------------------------------------------


def test_fit_secondary_call_timeout_returns_default_outside_an_attempt() -> None:
    assert remaining_attempt_seconds() is None
    assert fit_secondary_call_timeout(8.0) == 8.0


class _DeadlineProbeMain:
    def __init__(self) -> None:
        self.remaining: list[float | None] = []
        self.fitted: list[float | None] = []

    async def propose(self, request: ModelInput) -> ModelOutput:
        self.remaining.append(remaining_attempt_seconds())
        self.fitted.append(fit_secondary_call_timeout(8.0))
        return ModelOutput(model_id="main", model_version="v1", raw_proposal=_decision_raw())


class _DeadlineProbeQuick:
    def __init__(self) -> None:
        self.remaining: list[float | None] = []

    async def recover(self, request: ModelInput, failure_code: str) -> ModelOutput:
        self.remaining.append(remaining_attempt_seconds())
        raise RuntimeError("probe only")


@pytest.mark.asyncio
async def test_deliberation_installs_the_main_attempt_deadline_for_adapters() -> None:
    main = _DeadlineProbeMain()
    await Deliberation(
        router=_Router(),
        main_model=main,
        quick_recovery=_Quick(),
        main_timeout_seconds=5.0,
    ).deliberate(_capsule(), attempt_id="attempt:deadline-probe")

    assert len(main.remaining) == 1
    assert main.remaining[0] is not None and 0.0 < main.remaining[0] <= 5.0
    # A corrective retry must be capped below the remaining attempt budget.
    assert main.fitted[0] is not None and main.fitted[0] < 5.0
    # Outside the attempt the contextvar is cleaned up again.
    assert remaining_attempt_seconds() is None


@pytest.mark.asyncio
async def test_deliberation_installs_the_quick_attempt_deadline_for_recovery() -> None:
    class _FailingMain:
        async def propose(self, request: ModelInput) -> ModelOutput:
            raise RuntimeError("main down")

    quick = _DeadlineProbeQuick()
    result = await Deliberation(
        router=_Router(),
        main_model=_FailingMain(),
        quick_recovery=quick,
        quick_timeout_seconds=3.0,
    ).deliberate(_capsule(), attempt_id="attempt:quick-deadline-probe")

    assert result.audit.status == "recovery_failed"
    assert len(quick.remaining) == 1
    assert quick.remaining[0] is not None and 0.0 < quick.remaining[0] <= 3.0


def test_fit_secondary_call_timeout_skips_when_no_useful_budget_remains() -> None:
    from companion_daemon.world_v2 import deliberation as deliberation_module

    token = deliberation_module._ATTEMPT_DEADLINE.set(time.monotonic() + 1.0)
    try:
        assert fit_secondary_call_timeout(8.0) is None
        assert fit_secondary_call_timeout(8.0, minimum_seconds=0.1) is not None
    finally:
        deliberation_module._ATTEMPT_DEADLINE.reset(token)


# --- reliability counters ------------------------------------------------------


def test_reliability_snapshot_counts_and_rate() -> None:
    metrics.record_dispatch_ack()
    metrics.record_dispatch_ack()
    metrics.record_visible_reply()
    metrics.record_visible_reply()
    metrics.record_visible_reply()
    metrics.record_failsafe()
    metrics.record_claim_repair()
    metrics.record_shape_repair()
    metrics.record_source_closure_reselection()
    metrics.record_claim_free_reply()
    metrics.record_backup_recovery()
    metrics.record_compact_inbound_branch("reply_only")
    metrics.record_compact_inbound_branch("full_turn")
    metrics.record_compact_inbound_branch("recall")

    snapshot = metrics.reliability_snapshot()

    assert snapshot["window_hours"] == 24
    assert snapshot["dispatch_acks_24h"] == 2
    # Existing health fields remain available with their stronger delivered
    # semantics; clients do not need to migrate keys merely to distinguish an
    # acknowledgement from user-visible evidence.
    assert snapshot["visible_replies_24h"] == 3
    assert snapshot["failsafe_24h"] == 1
    assert snapshot["failsafe_rate_24h"] == round(1 / 3, 4)
    assert snapshot["claim_repair_24h"] == 1
    assert snapshot["shape_repair_24h"] == 1
    assert snapshot["source_closure_reselection_24h"] == 1
    assert snapshot["claim_free_24h"] == 1
    assert snapshot["backup_recovery_24h"] == 1
    assert snapshot["compact_reply_only_24h"] == 1
    assert snapshot["compact_full_turn_24h"] == 1
    assert snapshot["compact_recall_24h"] == 1
    assert isinstance(snapshot["since"], str)


def test_reliability_snapshot_prunes_entries_older_than_the_window() -> None:
    metrics.record_failsafe()
    metrics._events["failsafe"].appendleft(time.time() - 25 * 3600)

    snapshot = metrics.reliability_snapshot()

    assert snapshot["failsafe_24h"] == 1
    assert snapshot["failsafe_rate_24h"] is None  # no visible replies recorded


def test_compact_inbound_branch_counter_rejects_unknown_transport() -> None:
    with pytest.raises(ValueError, match="branch is not installed"):
        metrics.record_compact_inbound_branch("host_guessed_reply")


_BROKEN_SHAPE_EXPRESSION = {
    "timing_choice": "now",
    "beats": [{"modality": "text", "text": "我在的。", "note": "extra"}],
    "stance": "attentive",
    "brief_rationale": "Stay with the current conversation.",
    "confidence": 7200,
    "world_claims": [],
}

_VALID_APPRAISAL = {
    "appraise": False,
    "brief_rationale": "No material emotional shift.",
    "behavior_tendency": "observe",
    "stance": "wait",
    "display_strategy": "withhold",
    "confidence": 3000,
}


class _ShapeRepairedCombinedProvider:
    """A provider whose second call would repair; H17 forbids that second call."""

    model = "combined-flash"

    def __init__(self, *, corrected_on_call: int = 2) -> None:
        self.calls: list[list[dict[str, str]]] = []
        self._corrected_on_call = corrected_on_call

    async def complete(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> str:
        del temperature
        self.calls.append(messages)
        expression = (
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我在的，这句我接住了。"}],
                "stance": "attentive",
                "brief_rationale": "Corrected the beat shape only.",
                "confidence": 7200,
                "world_claims": [],
            }
            if len(self.calls) >= self._corrected_on_call
            else _BROKEN_SHAPE_EXPRESSION
        )
        return json.dumps(
            {"appraisal_draft": _VALID_APPRAISAL, "expression_draft": expression},
            ensure_ascii=False,
        )


# --- one-shot corrective policy (H1b/H17) --------------------------------------
# The same-contract corrective retry was retired.  A malformed structural wire
# is a technical failure for that one Occasion; the host never repairs it with
# local prose or repeats the identical model call.


@pytest.mark.asyncio
async def test_paired_shape_reject_is_terminal_without_same_contract_retry() -> None:
    provider = _ShapeRepairedCombinedProvider(corrected_on_call=2)
    cognition = InboundCharacterAuthor(flash_model=provider)
    request = _request(revision=3, call="call:paired-shape-repair")

    await cognition._appraisal_materializer.propose(request)
    with pytest.raises(ValidationTechnicalFailure) as caught:
        await cognition._expression_materializer.propose(request)

    assert caught.value.failure_code == "paired_expression_reselection_invalid"
    assert len(provider.calls) == 1
    assert metrics.reliability_snapshot()["shape_repair_24h"] == 0


@pytest.mark.asyncio
async def test_deadline_deferred_repair_is_never_started_after_h17() -> None:
    from companion_daemon.world_v2 import deliberation as deliberation_module

    provider = _ShapeRepairedCombinedProvider(corrected_on_call=2)
    cognition = InboundCharacterAuthor(flash_model=provider)
    request = _request(revision=3, call="call:pre-failsafe-retry")

    token = deliberation_module._ATTEMPT_DEADLINE.set(time.monotonic() + 1.0)
    try:
        await cognition._appraisal_materializer.propose(request)
    finally:
        deliberation_module._ATTEMPT_DEADLINE.reset(token)

    with pytest.raises(ValidationTechnicalFailure):
        await cognition._expression_materializer.propose(request)

    assert len(provider.calls) == 1
    assert metrics.reliability_snapshot()["failsafe_24h"] == 0


@pytest.mark.asyncio
async def test_spent_corrective_is_not_repeated_after_h17() -> None:
    provider = _ShapeRepairedCombinedProvider(corrected_on_call=99)
    cognition = InboundCharacterAuthor(flash_model=provider)
    request = _request(revision=3, call="call:pre-failsafe-exhausted")

    await cognition._appraisal_materializer.propose(request)
    with pytest.raises(ValidationTechnicalFailure) as caught:
        await cognition._expression_materializer.propose(request)

    assert caught.value.failure_code == "paired_expression_reselection_invalid"
    assert len(provider.calls) == 1
    assert metrics.reliability_snapshot()["failsafe_24h"] == 0


@pytest.mark.asyncio
async def test_direct_adapter_rejects_non_claim_shape_without_retry() -> None:
    class _DirectShapeProvider:
        model = "direct-flash"

        def __init__(self) -> None:
            self.calls: list[list[dict[str, str]]] = []

        async def complete(
            self, messages: list[dict[str, str]], *, temperature: float = 0.8
        ) -> str:
            del temperature
            self.calls.append(messages)
            return json.dumps(_BROKEN_SHAPE_EXPRESSION, ensure_ascii=False)

    direct = _DirectShapeProvider()
    adapter = _ExpressionDraftWire(model=direct)
    with pytest.raises(ValidationTechnicalFailure) as caught:
        await adapter.propose(_request(revision=3, call="call:direct-shape-repair"))

    assert caught.value.failure_code == "authored_expression_reselection_invalid"
    assert len(direct.calls) == 1
