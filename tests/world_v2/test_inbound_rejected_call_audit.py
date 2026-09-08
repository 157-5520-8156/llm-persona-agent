"""Returned invalid author calls retain their original transport audit."""

from contextlib import asynccontextmanager
from dataclasses import replace
from hashlib import sha256
import json

import httpx
import pytest
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.deliberation import ValidationTechnicalFailure
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from test_character_interior_inbound_author import _request
from test_production_turn_application import NOW, _config, _DeliveredTransport, _Identities, _Router
from test_whole_candidate_author import BEATS, _decision, _inbound, _model_audits
from test_world_stimulus_life_intent import _http_result


TOKENS = ((113, 57), (227, 83))


class _ReturnedCalls:
    def __init__(self, *, invalid_final=False, failure_stage="expression"):
        self.invalid_final = invalid_final
        self.failure_stage = failure_stage
        self.requests = []
        self.request_hashes = []
        self.arguments = []
        self.provider_usage = []

    async def __call__(self, request):
        body = json.loads(request.content)
        ordinal = len(self.requests)
        assert ordinal < 2, "the same Core correction opened an extra author"
        assert not body.get("stream")
        self.requests.append(body)
        self.request_hashes.append(request.headers["X-Girl-Agent-Request-Identity"])
        invalid = ordinal == 0 or self.invalid_final
        authored = _decision(invalid=invalid)
        if invalid:
            # The excerpt is intentionally too short to prove this complete
            # returned tool payload; the tail must participate in its hash.
            authored["expression_draft"]["beats"][0]["text"] = "先听我把想法说完。" * 100
            authored["expression_draft"]["brief_rationale"] = f"原始调用尾部:{ordinal}"
        fields = body["tools"][0]["function"]["parameters"]["properties"]
        authored = {key: authored.get(key) for key in fields}
        if invalid and self.failure_stage == "envelope":
            authored["appraisal_draft"] = []
        if invalid and self.failure_stage == "appraisal":
            authored["appraisal_draft"]["confidence"] = -1
        self.arguments.append(json.dumps(authored, ensure_ascii=False))
        response = _http_result(body, authored).json()
        input_tokens, output_tokens = TOKENS[ordinal]
        response["usage"] = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        return httpx.Response(200, json=response)


@asynccontextmanager
async def _application(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    model = DeepSeekChatModel(
        "offline-fixture",
        "http://127.0.0.1:9",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
        usage_observer=provider.provider_usage.append,
    )
    # The existing capture hook verifies the full logical request identity
    # before exposing this hash to our in-process MockTransport.
    model._test_only_capture_exact_request_identity = True
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
        update={"private_turn_state_mode": "required"},
    )
    author = _InboundCharacterAuthor(
        flash_model=model,
        expression_capabilities=capabilities,
        require_explicit_authored_decision_fields=True,
        whole_candidate_mode=True,
    )
    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "rejected-call.sqlite",
        config=replace(
            _config(), expression_capabilities=capabilities, expression_episode_mode="off"
        ),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(inbound_author=author),
        transport=_DeliveredTransport(),
        now=NOW,
    )
    try:
        yield app, author
    finally:
        app.close()
        await model.aclose()


def _assert_complete_return(audit, provider, ordinal):
    raw = provider.arguments[ordinal]
    assert len(raw) > 800
    assert audit.request_hash == provider.request_hashes[ordinal]
    assert audit.response_hash == sha256(raw.encode()).hexdigest()
    assert audit.response_hash != sha256(raw[:800].encode()).hexdigest()
    assert audit.model_id == "deepseek-v4-flash"
    assert audit.model_version == _InboundCharacterAuthor.VERSION
    assert audit.status == "main_invalid"
    assert audit.outcome == "invalid"
    assert audit.usage is not None
    assert (audit.usage.input_tokens, audit.usage.output_tokens) == TOKENS[ordinal]


def _assert_independent_provider_metering(provider):
    # This is the provider observer's own record, not a sum of ModelResult
    # parent and expanded child rows, and does not write a spend database.
    assert (
        tuple((usage.prompt_tokens, usage.completion_tokens) for usage in provider.provider_usage)
        == TOKENS
    )
    assert all(usage.status == "succeeded" for usage in provider.provider_usage)


def _assert_physical_usage_once(audits):
    physical = {}
    for audit in audits:
        if audit.usage is None:
            continue
        usage = audit.usage
        previous = physical.setdefault(usage.provider_usage_ref, usage)
        assert previous == usage
    assert len(physical) == 2
    assert sum(usage.input_tokens for usage in physical.values()) == sum(pair[0] for pair in TOKENS)
    assert sum(usage.output_tokens for usage in physical.values()) == sum(
        pair[1] for pair in TOKENS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ("expression", "appraisal", "envelope"))
async def test_rejected_then_valid_core_result_records_the_first_complete_call_once(
    tmp_path,
    monkeypatch,
    failure_stage,
):
    provider = _ReturnedCalls(failure_stage=failure_stage)
    async with _application(tmp_path, monkeypatch, provider) as (app, _author):
        outcome = await app.respond(_inbound())
        repeated = await app.respond(_inbound())
        evidence = app.export_replay_evidence()

    assert outcome.status == repeated.status == "action_authorized"
    assert len(provider.requests) == 2
    audits = _model_audits(evidence)
    rejected = tuple(
        audit for audit in audits if audit.route.router_version == "authored-candidate-audit.1"
    )
    assert len(rejected) == 1
    _assert_complete_return(rejected[0], provider, 0)
    assert rejected[0].slot == "primary"
    assert rejected[0].route.reason_code == "author_candidate.primary_initial.validation_rejected"
    (winner,) = (audit for audit in audits if audit.outcome == "winner")
    assert winner.model_call_id != rejected[0].model_call_id
    assert winner.request_hash == provider.request_hashes[1]
    assert winner.usage is not None
    assert (winner.usage.input_tokens, winner.usage.output_tokens) == TOKENS[1]
    assert tuple(item.text for item in evidence.projection.stored_message_payloads) == BEATS
    _assert_physical_usage_once(audits)
    _assert_independent_provider_metering(provider)


@pytest.mark.asyncio
async def test_two_invalid_core_results_preserve_both_complete_calls_without_action(
    tmp_path,
    monkeypatch,
):
    provider = _ReturnedCalls(invalid_final=True)
    async with _application(tmp_path, monkeypatch, provider) as (app, _author):
        outcome = await app.respond(_inbound())
        evidence = app.export_replay_evidence()

    assert outcome.status == "deferred"
    assert len(provider.requests) == 2
    assert evidence.projection.actions == ()
    audits = _model_audits(evidence)
    rejected = tuple(
        audit for audit in audits if audit.route.router_version == "authored-candidate-audit.1"
    )
    assert len(rejected) == 2
    assert len({audit.model_call_id for audit in rejected}) == 2
    by_request = {audit.request_hash: audit for audit in rejected}
    for ordinal, request_hash in enumerate(provider.request_hashes):
        _assert_complete_return(by_request[request_hash], provider, ordinal)
    assert by_request[provider.request_hashes[0]].slot == "primary"
    assert by_request[provider.request_hashes[1]].slot == "corrective"
    assert all(audit.outcome != "winner" for audit in audits)
    _assert_physical_usage_once(audits)
    _assert_independent_provider_metering(provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ("capsule", "cursor", "context", "presentation", "call"))
async def test_correction_cannot_borrow_a_rejected_call_from_another_pin(
    tmp_path,
    monkeypatch,
    changed,
):
    provider = _ReturnedCalls()
    request = _request(revision=3, call="call:rejected-original")
    updates = {
        "capsule": {"capsule_id": "c" * 64},
        "cursor": {"evaluated_deliberation_revision": 1},
        "context": {"model_content_json": '{"world_revision":3,"changed_context":true}'},
        "presentation": {"model_content_json": json.dumps({"world_revision": 3}, indent=2)},
        "call": {"call_id": "call:different-author-attempt"},
    }[changed]
    async with _application(tmp_path, monkeypatch, provider) as (_app, author):
        with pytest.raises(ValidationTechnicalFailure) as rejected:
            await author.propose(request)
        assert len(rejected.value.authored_candidate_audits) == 1
        output = await author.correct_role_result(
            request.model_copy(update=updates),
            "role_result_schema_invalid",
        )

    assert len(provider.requests) == 2
    assert output.authored_candidate_audits == ()
    assert output.usage is not None
    assert (output.usage.input_tokens, output.usage.output_tokens) == TOKENS[1]


@pytest.mark.asyncio
async def test_diagnostic_excerpt_and_claimed_identity_cannot_replace_a_complete_return():
    class ExceptionOnlyInitialProvider:
        model = "fixture:no-complete-return"

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, *, temperature=0.8):
            del messages, temperature
            self.calls += 1
            if self.calls == 1:
                raise ValidationTechnicalFailure(
                    "paired_expression_reselection_invalid",
                    model_call_id="call:diagnostic-only",
                    request_hash="a" * 64,
                    attempted_model_id=self.model,
                    attempted_model_version="fixture.1",
                    rejected_raw_hash="b" * 64,
                    rejected_raw_excerpt="只有诊断片段，没有完整作者返回值。",
                    failure_detail="诊断性异常；没有可冻结的完整返回值。",
                )
            value = _decision()
            value.pop("result_kind")
            return json.dumps(value, ensure_ascii=False)

    provider = ExceptionOnlyInitialProvider()
    author = _InboundCharacterAuthor(flash_model=provider, whole_candidate_mode=True)
    request = _request(revision=3, call="call:missing-complete-return")
    with pytest.raises(ValidationTechnicalFailure) as rejected:
        await author.propose(request)
    assert rejected.value.authored_candidate_audits == ()
    output = await author.correct_role_result(request, "role_result_schema_invalid")
    assert provider.calls == 2
    assert output.authored_candidate_audits == ()
