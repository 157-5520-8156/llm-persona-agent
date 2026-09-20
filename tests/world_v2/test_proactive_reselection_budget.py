"""Same-role proactive correction owns the existing bounded validation phase."""

import asyncio
from collections import OrderedDict
from contextlib import contextmanager
import json
from types import SimpleNamespace

import httpx
import pytest

from companion_daemon.world_v2 import deliberation
from companion_daemon.world_v2.character_interior.core import _RoleFacultyTechnicalFailure
from companion_daemon.world_v2.character_interior.ports import _RoleResultContractError
from companion_daemon.world_v2.character_interior.proactive_visible_review import (
    ReviewedProactiveStructuredRoleFaculty,
)
from companion_daemon.world_v2.interactive_turn_budget import InteractiveTurnBudgetPolicy
from test_proactive_visible_source_gate import _run_scenario


@contextmanager
def validation_state(state):
    token = deliberation._VALIDATION_ATTEMPT.set(state)
    try:
        yield
    finally:
        deliberation._VALIDATION_ATTEMPT.reset(token)


def make_state(now, *, key="candidate:one", budget=None, author_deadline=11):
    budget = budget or InteractiveTurnBudgetPolicy(clock=lambda: now[0]).start()
    return deliberation._ValidationAttemptState(
        budget=budget, author_deadline=author_deadline, candidate_key=key,
    )


def reject(*, ordinal=0, code="paired_expression_reselection_invalid"):
    gate = object.__new__(ReviewedProactiveStructuredRoleFaculty)
    gate._rejected = OrderedDict()
    candidate = deliberation.AuthoredCandidateInvocationAudit(
        purpose="proactive_visible_candidate", model_call_id="model-call:role",
        request_hash="a" * 64, response_hash="b" * 64,
        model_id="fixture", model_version="fixture.1", outcome="validation_rejected",
    )
    gate._fail_review(
        request=SimpleNamespace(inner_turn_id="turn:one", correction_ordinal=ordinal),
        author=SimpleNamespace(model_call_id=candidate.model_call_id,
                               request_hash=candidate.request_hash,
                               response_hash=candidate.response_hash),
        candidate=candidate, prior_candidates=(),
        failure=deliberation.ValidationTechnicalFailure(code, failure_detail="unsupported fixture"),
    )


def test_first_semantic_rejection_after_author_deadline_gets_its_own_phase():
    now = [0.0]
    state = make_state(now)
    now[0] = 20.0  # Ordinary author elapsed; candidate hard ceiling has not.
    with validation_state(state), pytest.raises(_RoleResultContractError):
        reject()
    assert state.truth_boundary_active
    assert state.reselection_deadline == 120.0
    assert state.author_deadline == 11
    assert state.hard_deadline == 157


@pytest.mark.parametrize("code", ["source_review_exception", "source_review_timeout"])
def test_unavailable_review_cannot_activate_semantic_correction_budget(code):
    now = [0.0]
    state = make_state(now)
    with validation_state(state), pytest.raises(_RoleFacultyTechnicalFailure) as error:
        reject(code=code)
    assert error.value.failure_code == code
    assert not state.truth_boundary_active
    assert state.reselection_deadline is None


def test_exhausted_candidate_hard_deadline_does_not_open_or_dispatch_correction():
    now = [0.0]
    state = make_state(now)
    now[0] = state.hard_deadline
    with validation_state(state), pytest.raises(_RoleFacultyTechnicalFailure) as error:
        reject()
    assert error.value.failure_code == "authored_subcall_timeout"
    assert state.reselection_deadline is None
    assert not state.truth_boundary_active
    assert not state.budget._validation_recovery.reselection_deadlines
    # Already paid/rejected author evidence survives the exhausted timing path.
    assert error.value.evidence.authored_candidate_audits[0].outcome == "validation_rejected"


def test_second_semantic_rejection_cannot_open_another_correction():
    now = [0.0]
    state = make_state(now)
    with validation_state(state), pytest.raises(_RoleFacultyTechnicalFailure) as error:
        reject(ordinal=1)
    assert error.value.failure_code == "authored_expression_reselection_invalid"
    assert state.reselection_deadline is None


def test_reselection_is_one_shot_and_cannot_borrow_another_candidates_phase():
    now = [0.0]
    first = make_state(now)
    now[0] = 12.0
    with validation_state(first):
        assert deliberation.begin_validation_reselection_recovery()
    deadline = first.reselection_deadline
    now[0] = 20.0
    with validation_state(first):
        assert deliberation.begin_validation_reselection_recovery()
    assert first.reselection_deadline == deadline
    second = make_state(now, key="candidate:two", budget=first.budget, author_deadline=31)
    now[0] = 50.0
    with validation_state(second):
        assert deliberation.begin_validation_reselection_recovery()
    assert second.reselection_deadline == 150
    now[0] = deadline + 1
    with validation_state(first):
        assert not deliberation.begin_validation_reselection_recovery()
    assert first.reselection_deadline == deadline
    assert first.budget._validation_recovery.reselection_deadlines == {
        "candidate:one": 112, "candidate:two": 150,
    }


@pytest.mark.asyncio
async def test_public_proactive_correction_survives_expired_author_window_and_is_reviewed(
    tmp_path, monkeypatch,
):
    import companion_daemon.world_v2.production_turn_application as composition

    marks = []
    original_bind = composition._bind_production_character_interior

    def bind(**kwargs):
        policy = InteractiveTurnBudgetPolicy(
            total_seconds=0.5, hedge_after_seconds=0.1,
            acceptance_dispatch_reserve_seconds=0.05,
            validation_recovery_seconds=0.4, validation_reselection_seconds=0.8,
        )
        kwargs["background_turn_budget_policy"] = policy
        return original_bind(**kwargs)

    monkeypatch.setattr(composition, "_bind_production_character_interior", bind)
    original_http = httpx.MockTransport.handle_async_request
    authors = reviews = 0
    original_author_deadline = None

    async def delayed_http(self, request):
        nonlocal authors, reviews, original_author_deadline
        body = json.loads(request.content)
        name = body["tool_choice"]["function"]["name"]
        if name == "character_role_proactive_contact_v1":
            authors += 1
            state = deliberation._VALIDATION_ATTEMPT.get()
            assert state is not None
            if authors == 1:
                original_author_deadline = state.author_deadline
                await asyncio.sleep(0.12)
            else:
                assert authors == 2
                assert state.author_deadline == original_author_deadline
                assert state.reselection_deadline is not None
                marks.append("correction_entered_with_phase")
                # Complete after the *original* author deadline. Without the
                # proactive phase handoff the real Deliberation timer cancels.
                await asyncio.sleep(max(0, original_author_deadline - state.budget.clock()) + 0.08)
                marks.append("correction_returned_after_author_deadline")
        elif authors and name.startswith("visible_beat_source_verdict"):
            reviews += 1
            await asyncio.sleep(0.08)
        return await original_http(self, request)

    monkeypatch.setattr(httpx.MockTransport, "handle_async_request", delayed_http)
    evidence, _authors, _reviews, _delivery = await _run_scenario(
        tmp_path, monkeypatch, "reselect", review_version="6",
    )
    assert (authors, reviews) == (2, 2)
    assert marks == ["correction_entered_with_phase", "correction_returned_after_author_deadline"]
    assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
