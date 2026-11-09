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


def test_current_slim_contract_is_shorter_and_does_not_promise_semantic_fallback():
    from companion_daemon.world_v2.character_interior.inbound_prompt import compact_atomic_slim_system_prompt
    from companion_daemon.world_v2.present_prompt import SLIM_CONSIDER_KEYS
    args = dict(identity_instruction='EXACT_IDENTITY', recall_available=True, private_cognition_scope=True)
    old = compact_atomic_slim_system_prompt(**args)
    current = compact_atomic_slim_system_prompt(**args, concise_contract=True)
    assert current.startswith('character-inbound-prompt.9-slim\n')
    assert old.startswith('character-inbound-prompt.2-slim\n')
    assert len(current) < 7500
    assert '只对那份写坏的 appraisal 记 affect no_change' not in current
    assert 'expression_draft.world_claims' not in current
    assert PRIVATE_COGNITION_INSTRUCTION in current and 'EXACT_IDENTITY' in current
    assert all(key in current for key in SLIM_CONSIDER_KEYS)
    assert '错误坐标是技术失败' in current


def test_slim_recall_shape_matches_actual_contract_and_is_offered_only_when_available():
    from companion_daemon.world_v2.character_interior.inbound_prompt import (
        compact_atomic_slim_system_prompt, _current_slim_recall_instruction,
    )
    from companion_daemon.world_v2.private_turn_state import PrivateTurnState
    from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
    text = _current_slim_recall_instruction()
    start = text.index('{')
    shape, _ = json.JSONDecoder().raw_decode(text[start:])
    PrivateTurnState.model_validate_json(json.dumps(shape['private_turn_state']))
    CharacterRecallRequest.model_validate_json(json.dumps(shape['recall_request']))
    common = dict(identity_instruction='identity', concise_contract=True)
    assert text in compact_atomic_slim_system_prompt(**common, recall_available=True)
    assert text not in compact_atomic_slim_system_prompt(**common, recall_available=False)


def test_historical_slim_prompt_bytes_are_unchanged_by_conversation_cleanup():
    import hashlib
    import itertools
    from companion_daemon.world_v2.character_interior.inbound_prompt import compact_atomic_slim_system_prompt

    prompts = [compact_atomic_slim_system_prompt(
        identity_instruction="固定测试身份", recall_available=recall,
        private_cognition_scope=private, concise_contract=concise, nontext_messages=nontext,
    ) for recall, private, concise, nontext in itertools.product((False, True), repeat=4)]
    digest = hashlib.sha256(json.dumps(prompts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert digest == "44e565da809ae29df2472bfeccdcfdde4f204f75d7b6beda51d57966654a821c"


@pytest.mark.parametrize("recall", [False, True])
@pytest.mark.parametrize("media", [False, True])
def test_conversation_prompt_retains_authority_and_capabilities_without_social_scripts(recall, media):
    from companion_daemon.world_v2.character_interior.inbound_prompt import (
        CONVERSATION_PROMPT_VERSION, conversation_atomic_slim_system_prompt,
        _current_slim_recall_instruction,
    )
    from companion_daemon.world_v2.present_prompt import SLIM_CONSIDER_KEYS

    identity = "原样保留的角色身份与性格。"
    prompt = conversation_atomic_slim_system_prompt(
        identity_instruction=identity, recall_available=recall,
        private_cognition_scope=True, media_request_available=media,
    )
    assert prompt.startswith(CONVERSATION_PROMPT_VERSION + "\n")
    assert prompt.count(identity) == 1
    assert prompt.count(PRIVATE_COGNITION_INSTRUCTION) == 1
    assert (_current_slim_recall_instruction().replace("、meaning_of_this", "") in prompt) is recall
    for boundary in (
        "系统启动以前的过去", "缺少记录表示未知", "不得无依据否认", "计划只证明意图",
        "结束只证明生命周期结束", "不自动证明你亲眼看见",
        "不自动证明内容真实、动作完成或对方已读", "user_channel_limited",
        "对当前消息的原意承接、纯当前感受", "一般表达不需声明",
        "不能靠模糊语气或 world_claims=[] 豁免", "source_refs",
        "active_affect_heads", "affect_target_bounds", "episode_id", "component_id",
        "5..86400", "每轴每轮±500", "实际阶段变化仍以送达为准",
        "技术失败不替你选择沉默", "动机、态度、措辞", "都由你决定",
        "不是绑架后续回合的永久守则",
    ):
        assert boundary in prompt
    for script in (
        "先定内心再出动作",
        "以及他的言下之意",
        "嗯哼我在听",
        "5..20 秒短",
        "热聊中想快点追问",
        "绝不写",
        "固定三段式",
    ):
        assert script not in prompt
    assert "meaning_of_this" not in prompt
    if media:
        assert "photo 必填" in prompt and all(
            key in prompt for key in (SLIM_CONSIDER_KEYS - {"meaning_of_this"})
        )
        assert "请求只授权尝试" in prompt and "declared_display" in prompt
    else:
        assert "本轮没有媒体请求能力" in prompt and "photo 必填" not in prompt


@pytest.mark.asyncio
async def test_previous_slim4_prompt_recovers_original_receipt_after_cleanup(tmp_path, monkeypatch):
    import test_slim_atomic_author as fixture
    from companion_daemon.world_v2.character_interior import inbound_prompt
    from companion_daemon.world_v2.character_interior.lived_evidence_scope import LIVED_EVIDENCE_SCOPE
    from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
    from companion_daemon.world_v2.visible_review_evidence_storage import read_review_evidence
    from test_production_turn_application import _DeliveredTransport
    from test_slim_atomic_author import _CurrentSlimHTTP, _application, _slim

    identity = [CompanionIdentityFrame(
        companion_name="测试角色", counterpart_name="测试者",
        stable_identity_facts=("测试角色的身份事实。",), personality_frame="旧版角色描述。",
    )]
    original_author = fixture._InboundCharacterAuthor

    def author_with_profile(**kwargs):
        return original_author(**{**kwargs, "identity_frame": identity[0]})

    monkeypatch.setattr(fixture, "_InboundCharacterAuthor", author_with_profile)

    def historical_prompt(*, identity_instruction, recall_available, private_cognition_scope=False,
                          media_request_available=False):
        assert media_request_available is False
        return inbound_prompt.compact_atomic_slim_system_prompt(
            identity_instruction=identity_instruction, recall_available=recall_available,
            private_cognition_scope=private_cognition_scope, concise_contract=True, nontext_messages=True,
        )

    old_http = _CurrentSlimHTTP(first_payload={**_slim(), "messages": ["旧提示下原来选好的话"]})
    with monkeypatch.context() as old:
        old.setattr(wire, "conversation_atomic_slim_system_prompt", historical_prompt)
        old.setattr(inbound_prompt, "CONVERSATION_LIVED_SCOPE", {
            **LIVED_EVIDENCE_SCOPE, "contract": "character-lived-evidence.2-conversational",
            "instruction": LIVED_EVIDENCE_SCOPE["instruction"].replace("缺少记录也不证明事情从没发生。", "")
            + inbound_prompt.CONVERSATIONAL_GROUNDING,
        })
        async with _application(tmp_path, old, old_http, tool_version="slim4", review_version="25") as app:
            accepted = await app.respond(_inbound())
            assert accepted.status == "action_authorized"
            stored = next(row for row in _audits(app) if row.visible_source_review_json)
            original_request = read_review_evidence(stored.visible_source_review_json)["author_request_json"]
            assert json.loads(original_request)["messages"][0]["content"].startswith("character-inbound-prompt.11-")

    def forbidden(**kwargs):
        raise AssertionError("accepted old turn must not be recompiled with a new prompt")

    identity[0] = identity[0].model_copy(update={"personality_frame": "新版只描述性格，不规定逐情境回应。"})
    monkeypatch.setattr(wire, "conversation_atomic_slim_system_prompt", forbidden)
    cold_http = _CurrentSlimHTTP()
    delivery = _DeliveredTransport()
    async with _application(tmp_path, monkeypatch, cold_http, tool_version="slim4",
                            review_version="25", transport=delivery) as app:
        recovered = await app.respond(_inbound())
        assert recovered.authorized_action_ids == accepted.authorized_action_ids
        assert not cold_http.requests
        assert (await app.drain_actions_once()).status == "settled"
        assert delivery.bodies == ["旧提示下原来选好的话"]
        stored = next(row for row in _audits(app) if row.visible_source_review_json)
        assert read_review_evidence(stored.visible_source_review_json)["author_request_json"] == original_request
        evidence = app.export_replay_evidence()
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash


@pytest.mark.asyncio
async def test_current_author_receives_cleaned_prompt_and_no_duplicate_behavior_tutorial(tmp_path, monkeypatch):
    from companion_daemon.world_v2.character_interior.inbound_prompt import CONVERSATION_PROMPT_VERSION
    from test_inbound_dialogue_overlap_presentation import _captured_author_request

    request = await _captured_author_request(tmp_path, monkeypatch, "slim4")
    system = request["messages"][0]["content"]
    context = json.loads(request["messages"][1]["content"])
    assert system.startswith(CONVERSATION_PROMPT_VERSION + "\n")
    assert "先定内心再出动作" not in system
    assert context["lived_evidence_scope"]["contract"] == "character-lived-evidence.3-author-instructions"
    assert "不必追着旧问题讨答案" not in context["lived_evidence_scope"]["instruction"]
    assert "inner_life_snapshot" in context and "expression_hard_boundaries" in context
