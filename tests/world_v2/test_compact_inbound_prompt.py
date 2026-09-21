"""Compact atomic-v3 instructions remain part of the exact audited request."""

from copy import deepcopy
import json

import pytest

from companion_daemon.llm import provider_invocation_request_hash
from companion_daemon.world_v2.character_interior import inbound_author as wire
from companion_daemon.world_v2.character_interior.inbound_prompt import compact_atomic_system_prompt
from companion_daemon.world_v2.private_cognition_scope import INSTRUCTION as PRIVATE_COGNITION_INSTRUCTION
from companion_daemon.world_v2.visible_source_author_request import verify_visible_source_author_request
from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate
from test_character_interior_inbound_author import _request
from test_inbound_correction_prefix import _CaptureCompiler, _CapturedRequest
from test_inbound_atomic_result_v2 import _contract
from test_launch_visible_source_gate import _app, _audits, _ReviewHTTP
from test_visible_source_diagnostics_runtime import _DiagnosticHTTP
from test_whole_candidate_author import BEATS, _inbound


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("private_scope", [False, True])
def test_compact_prompt_keeps_authority_and_refreshes_only_after_recall_suffix(private_scope):
    initial = _contract(version="3", phase="initial", recall_allowed=True)
    final = _contract(version="3", phase="after_recall", recall_allowed=False)
    identity = "身份原文：保留性格、经历和逐字标点。\nSecond identity line."
    prompt = compact_atomic_system_prompt(
        identity_instruction=identity,
        branch_instruction=wire._atomic_branch_instruction(initial),
        private_cognition_scope=private_scope,
    )
    assert identity in prompt
    # These are the explicit authority distinctions this reduction must retain;
    # their presence is a prompt regression check, not a model-behavior claim.
    for meaning in (
        "系统启动以前的过去", "缺少记录表示未知",
        "环境发生了某事，也不自动证明你亲眼看见",
        "对方的消息证明对方这样说过", "appraise=false 时 affect=no_change",
        "appraise=true 时 meanings、attribution、severity 均不得为 null",
        "counterparty_roles 表示互动的另一方，不得包含 subject_role 自身",
        "timing_choice=now/later/silent 均由你选", "技术失败不替你选择沉默",
    ):
        assert meaning in prompt
    assert (PRIVATE_COGNITION_INSTRUCTION in prompt) is private_scope
    prefix, marker, before = prompt.rpartition(wire._ATOMIC_BRANCH_MARKER)
    assert "recall" in json.loads(before)
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": "fixed evidence"}]
    wire._refresh_atomic_branch_instruction(messages, final)
    after_prefix, after_marker, after = messages[0]["content"].rpartition(wire._ATOMIC_BRANCH_MARKER)
    assert (after_prefix, after_marker) == (prefix, marker)
    assert json.loads(after) == {
        kind: list(fields) for kind, fields in final.result_branch_fields().items()
    }
    assert "recall" not in json.loads(after)
    assert messages[1] == {"role": "user", "content": "fixed evidence"}


@pytest.mark.asyncio
async def test_compact_prompt_reaches_http_hash_and_full_correction_carrier(tmp_path, monkeypatch):
    compiled = []
    compile_prompt = wire.compact_atomic_system_prompt

    def capture(*, identity_instruction, branch_instruction, **kwargs):
        result = compile_prompt(
            identity_instruction=identity_instruction,
            branch_instruction=branch_instruction,
            **kwargs,
        )
        compiled.append((identity_instruction, branch_instruction, result))
        return result

    monkeypatch.setattr(wire, "compact_atomic_system_prompt", capture)
    http = _DiagnosticHTTP(["unclosed", "pass"], tool_version="3")
    http.review_version = "4"
    async with _app(tmp_path / "world.sqlite", http) as app:
        assert (await app.respond(_inbound())).status == "action_authorized"
        assert (http.authors, http.reviews) == (2, 2)
        first, first_review, corrected, final_review = http.requests
        assert len(compiled) == 2
        for identity, branch, prompt in compiled:
            assert branch
            assert identity in prompt and branch in prompt
            assert "character-inbound-prompt.1" in prompt
        assert first["messages"][0]["content"] == compiled[0][2]
        assert corrected["messages"][0]["content"] == compiled[1][2]
        assert corrected["messages"][0] == first["messages"][0]
        assert first["tools"] == corrected["tools"]
        assert first["tool_choice"] == corrected["tool_choice"]

        initial_user = json.loads(first["messages"][1]["content"])
        corrected_user = json.loads(corrected["messages"][1]["content"])
        correction = corrected_user.pop("role_result_correction")
        assert corrected_user == initial_user
        coordinate = correction["coordinate"]
        assert coordinate["failure_code"] == "role_result_schema_invalid"
        detail = coordinate["failure_detail"]
        feedback = json.loads(detail.split("\n", 1)[1])
        assert feedback["rows"][0][:3] == [0, 0, len(BEATS[0])]
        assert BEATS[0].startswith(feedback["rows"][0][3])
        assert feedback["rows"][0][4] == "材料没有支持该陈述所表达的已发生经历。"
        assert "coordinate.failure_detail" in correction["instruction"]
        assert detail not in correction["instruction"]
        first_packet = json.loads(first_review["messages"][-1]["content"])
        final_packet = json.loads(final_review["messages"][-1]["content"])
        for field in ("source_materials", "source_reference_tables"):
            assert first_packet[field] == final_packet[field]

        winner = next(row for row in _audits(app) if row.visible_source_review_json is not None)
        raw_carrier = json.loads(winner.visible_source_review_json)["author_request_json"]
        carrier = json.loads(raw_carrier)
        for field in ("messages", "temperature", "tools", "tool_choice"):
            assert carrier[field] == corrected[field]
        assert provider_invocation_request_hash(**{
            key: value for key, value in carrier.items() if key != "contract"
        }) == winner.request_hash
        verify_visible_source_author_request(raw_carrier, expected_request_hash=winner.request_hash)
        forged = deepcopy(carrier)
        forged["messages"][0]["content"] += " changed prompt"
        with pytest.raises(ValueError, match="hash differs"):
            verify_visible_source_author_request(_json(forged), expected_request_hash=winner.request_hash)


@pytest.mark.asyncio
async def test_captured_legacy_receipt_cold_replays_without_current_prompt_compiler(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite"
    old = _ReviewHTTP(["pass"], tool_version="1")
    async with _app(path, old) as app:
        assert (await app.respond(_inbound())).status == "action_authorized"
        winner = next(row for row in _audits(app) if row.visible_source_review_json is not None)
        carrier_json = json.loads(winner.visible_source_review_json)["author_request_json"]
        carrier = json.loads(carrier_json)
        assert "character-inbound-prompt.1" not in carrier["messages"][0]["content"]
        evidence = app.export_replay_evidence()
        decision = next(row for row in evidence.projection.proposal_audits if row.proposal_kind == "decision")
        receipt_hash = verify_recorded_candidate(
            audit=decision, model_result_audits=evidence.projection.model_result_audits,
        )

    def forbidden(*args, **kwargs):
        raise AssertionError("cold replay must read the captured request, not compile today's prompt")

    monkeypatch.setattr(wire, "compact_atomic_system_prompt", forbidden)
    cold = _ReviewHTTP([], tool_version="3")
    async with _app(path, cold) as app:
        assert (await app.respond(_inbound())).status == "action_authorized"
        assert cold.requests == []
        recovered = app.export_replay_evidence()
        assert recovered.projection.model_result_audits == evidence.projection.model_result_audits
        assert verify_recorded_candidate(
            audit=decision, model_result_audits=recovered.projection.model_result_audits,
        ) == receipt_hash
        verify_visible_source_author_request(carrier_json, expected_request_hash=winner.request_hash)


@pytest.mark.asyncio
@pytest.mark.parametrize("version,stream,forced", [
    ("1", False, True), ("2", False, True), ("1", True, True),
    ("3", False, False),
])
async def test_legacy_stream_and_nonforced_requests_do_not_use_compact_prompt(
    monkeypatch, version, stream, forced,
):
    def forbidden(*args, **kwargs):
        raise AssertionError("compact prompt is limited to whole-candidate forced atomic v3")

    monkeypatch.setattr(wire, "compact_atomic_system_prompt", forbidden)
    provider = _CaptureCompiler()
    provider.supports_required_tool_choice = forced
    author = wire._InboundCharacterAuthor(
        flash_model=provider, atomic_tool_envelope_version=version,
        whole_candidate_mode=True, visible_source_review_model=provider,
    )
    request = _request(revision=3, call="call:compact-prompt-scope")
    context = json.loads(request.model_content_json)
    context["inner_life_snapshot"] = {"materials": {}}
    request = request.model_copy(update={"model_content_json": _json(context)})
    with pytest.raises(_CapturedRequest):
        await author._propose_appraisal(request, transport_provider=provider if stream else None)
    assert "character-inbound-prompt.1" not in provider.parameters["messages"][0]["content"]
