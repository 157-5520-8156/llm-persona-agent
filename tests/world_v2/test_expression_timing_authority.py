"""Hard timing bounds reject; only the same character can choose a new time."""

from copy import deepcopy
from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.expression_draft import (
    TEXT_ONLY_EXPRESSION_CAPABILITIES,
    materialize_expression_draft,
)
from test_character_interior_inbound_wire import _qq_request
from test_launch_visible_source_gate import _app, _audits
from test_production_turn_application import NOW
from test_whole_candidate_author import _decision, _inbound
from test_world_stimulus_life_intent import _http_result


def _expression(**updates):
    return {
        "timing_choice": "now",
        "beats": [{"modality": "text", "text": "我三天后再来找你说。"}],
        "stance": "defer",
        "brief_rationale": "我想自己选一个时间再说。",
        "world_claims": [],
        **updates,
    }


def _materialize(value):
    request = _qq_request().model_copy(update={
        "model_content_json": json.dumps({"logical_time": NOW.isoformat(), "slices": {}})
    })
    return materialize_expression_draft(
        value=value, request=request, capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES
    )


@pytest.mark.parametrize("wait,expiry", [(259_200, 345_600), (60, 60), (60, 200_000)])
@pytest.mark.parametrize("field", ["later", "response_expectation", "revisit"])
def test_invalid_authored_window_is_rejected_without_changing_raw(field, wait, expiry):
    if field == "later":
        value = _expression(timing_choice="later", delay_seconds=wait,
                            expires_after_seconds=expiry)
    else:
        payload = {"wait_seconds": wait, "expires_after_seconds": expiry}
        if field == "response_expectation":
            payload.update(hoped_response="想听你说一句", pressure_bp=2000, importance_bp=3000)
        else:
            payload["thought"] = "这件事我还想说"
        value = _expression(**{field: payload})
    before = deepcopy(value)
    with pytest.raises(ValueError):
        _materialize(value)
    assert value == before


def test_valid_authored_window_is_preserved_exactly_in_authorized_intents():
    value = _expression(timing_choice="later", delay_seconds=40_123,
                        expires_after_seconds=99_876)
    value["beats"][0]["text"] = "我晚些再来找你说。"
    proposal = _materialize(value)
    expected = (NOW + timedelta(seconds=40_123), NOW + timedelta(seconds=99_876))
    assert tuple(intent.due_window for intent in proposal.action_intents) == (expected,)
    plan = proposal.proposed_changes[0].payload.value()
    assert plan["beat_drafts"][0]["inline_text"] == value["beats"][0]["text"]
    assert proposal.stance == value["stance"]
    assert proposal.brief_rationale == value["brief_rationale"]


class _TimingHTTP:
    tool_version = "3"
    review_version = "4"

    def __init__(self, *, corrected=True):
        self.corrected = corrected
        self.requests = []
        self.authors = self.reviews = 0
        self.forbid_calls = False

    async def __call__(self, request):
        assert not self.forbid_calls, "cold replay must not re-author or review accepted choices"
        body = json.loads(request.content)
        self.requests.append(body)
        name = body["tool_choice"]["function"]["name"]
        if name.startswith("character_inbound_"):
            self.authors += 1
            assert self.authors <= 2, "timing validation opened a third character call"
            authored = _decision()
            corrected = self.corrected and self.authors == 2
            text = "我晚些再来找你说。" if corrected else "我三天后再来找你说。"
            authored["expression_draft"].update(
                timing_choice="later", delay_seconds=40_123 if corrected else 259_200,
                expires_after_seconds=99_876 if corrected else 345_600,
                beats=[{"modality": "text", "text": text}],
            )
            return _http_result(body, {"result": authored})
        assert name == "visible_beat_source_verdict_v4"
        self.reviews += 1
        # An invalid timing candidate must never reach source review.
        packet = json.loads(body["messages"][-1]["content"])
        assert packet["visible_beats"][0]["text"] == "我晚些再来找你说。"
        return _http_result(body, {
            "contract": "visible-beat-source-verdict.4",
            "decisions": [{"beat_index": 0, "verdict": "source_free",
                           "semantic_role": "commitment", "subject_role": "companion"}],
            "rejections": [],
        })


@pytest.mark.asyncio
@pytest.mark.parametrize("corrected", [True, False])
async def test_public_inbound_reselects_once_and_never_authorizes_the_clamped_time(
    tmp_path, corrected
):
    http = _TimingHTTP(corrected=corrected)
    async with _app(tmp_path / "world.sqlite", http) as app:
        outcome = await app.respond(_inbound())
        evidence = app.export_replay_evidence()
        assert http.authors == 2
        authors = [body for body in http.requests
                   if body["tool_choice"]["function"]["name"].startswith("character_inbound_")]
        original, correction = [json.loads(body["messages"][-1]["content"]) for body in authors]
        failure = correction["inner_life_snapshot"]["role_result_correction"]
        assert "delay_seconds" in failure["failure_detail"]
        assert "86400" in failure["failure_detail"] or "86,400" in failure["failure_detail"]
        assert correction["request"] == original["request"]
        assert correction["expression_hard_boundaries"] == original["expression_hard_boundaries"]
        audits = _audits(app)
        rejected = [audit for audit in audits
                    if audit.route.router_version == "authored-candidate-audit.1"]
        assert len(rejected) == (1 if corrected else 2)
        assert all(audit.status == "main_invalid" and audit.usage for audit in rejected)
        if corrected:
            assert outcome.status == "deferred" and outcome.deferred_refs, outcome
            assert http.reviews == 1
            (plan,) = evidence.projection.expression_plans
            assert plan.state == "authorized"
            # Public status is deferred because this authorized action is future-due.
            (action,) = evidence.projection.actions
            assert action.state == "authorized" and action.kind == "followup"
            assert action.not_before == NOW + timedelta(seconds=40_123)
            assert action.expires_at == NOW + timedelta(seconds=99_876)
            assert tuple(row.text for row in evidence.projection.stored_message_payloads) == (
                "我晚些再来找你说。",
            )
        else:
            assert outcome.status == "deferred"
            assert http.reviews == 0
            assert not evidence.projection.actions
            assert not evidence.projection.expression_plans
            assert all(audit.outcome != "winner" for audit in audits)

    if corrected:
        http.forbid_calls = True
        async with _app(tmp_path / "world.sqlite", http) as reopened:
            assert reopened.export_replay_evidence().projection == evidence.projection
            repeated = await reopened.respond(_inbound())
            assert repeated.status == outcome.status
            assert reopened.export_replay_evidence().projection == evidence.projection
        assert (http.authors, http.reviews) == (2, 1)
