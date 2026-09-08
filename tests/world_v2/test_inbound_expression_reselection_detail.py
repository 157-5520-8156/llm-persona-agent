from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json

import pytest
from test_production_turn_application import (
    NOW,
    _config,
    _DeliveredTransport,
    _Identities,
    _Router,
)
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)

from companion_daemon.llm import MAX_PROVIDER_CALLS_PER_TURN
from companion_daemon.world_v2.character_interior.inbound_author import (
    _InboundCharacterAuthor as InboundCharacterAuthor,
    _expression_contract_log_detail,
    _parse_combined,
    _postel_expression_draft,
    _role_failure_payload_kwargs,
    _role_readable_expression_violation,
    _role_result_correction_instruction,
    materialize_expression_draft,
)
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from companion_daemon.world_v2.deliberation import (
    ModelInput,
    ModelRoute,
    ModelUsageProvenance,
    TriggerMessage,
    ValidationTechnicalFailure,
)
from companion_daemon.world_v2.expression_draft import (
    ExpressionDraftCapabilities,
    qq_expression_capabilities,
)
from companion_daemon.world_v2.proposal_envelope import ProposalEvidenceRef


def _request(*, call: str = "call:postel-media") -> ModelInput:
    return ModelInput(
        call_id=call,
        attempt_id=f"attempt:{call}",
        route=ModelRoute(tier="flash", reason_code="ordinary", router_version="test.1"),
        capsule_id="a" * 64,
        trigger_ref="event:observation:1",
        evaluated_world_revision=3,
        model_content_json=json.dumps({"world_revision": 3, "source_inventory": []}),
        trigger_evidence=(
            ProposalEvidenceRef(
                ref_id="observation:1",
                evidence_kind="observed_message",
                source_world_revision=3,
                immutable_hash="sha256:" + "b" * 64,
            ),
        ),
        trigger_message=TriggerMessage(
            event_ref="event:observation:1",
            event_payload_hash="sha256:" + "b" * 64,
            observation_ref="observation:1",
            source_world_revision=3,
            actor="user:primary",
            channel="qq_c2c",
            reply_target="qq:user:1",
            text="把你翻到的那本诗集拍一张给我看看",
        ),
    )


def _valid_photo_draft(**overrides: object) -> dict[str, object]:
    draft: dict[str, object] = {
        "private_turn_state": {
            "contract": "private-turn-state.1",
            "inner_state_summary": "他要看诗集，我想把书拍给他。",
            "attended_source_refs": [],
        },
        "timing_choice": "now",
        "cadence": "conversational",
        "beats": [{"modality": "text", "text": "行，拍给你。"}],
        "stance": "自然回应",
        "brief_rationale": "他点名要这本诗集。",
        "confidence": 7000,
        "world_claims": [],
        "media_request": "consider_available_candidate",
        "media_source_refs": [],
    }
    draft.update(overrides)
    return draft


def test_postel_drops_string_response_expectation_padding() -> None:
    cleaned = _postel_expression_draft(
        {
            **_valid_photo_draft(),
            "response_expectation": "awaiting_reply",
            "revisit": "later",
        }
    )
    assert "response_expectation" not in cleaned
    assert "revisit" not in cleaned


def test_string_response_expectation_still_materializes() -> None:
    capabilities = qq_expression_capabilities("napcat", media_request_available=True)
    draft = _valid_photo_draft()
    draft["response_expectation"] = "awaiting_reply"
    proposal = materialize_expression_draft(
        raw=json.dumps(draft, ensure_ascii=False),
        request=_request(),
        capabilities=capabilities,
        quick_recovery=False,
        require_explicit_authored_decision_fields=True,
    )
    payload = json.loads(json.dumps(proposal))
    changes = payload.get("proposed_changes") or []
    kinds = {item.get("kind") for item in changes if isinstance(item, dict)}
    assert "expression_plan_transition" in kinds


def test_postel_drops_unknown_beat_keys() -> None:
    cleaned = _postel_expression_draft(
        {
            **_valid_photo_draft(),
            "beats": [
                {"modality": "text", "text": "行，拍给你。", "note": "extra"},
            ],
        }
    )
    assert cleaned["beats"] == [{"modality": "text", "text": "行，拍给你。"}]


def test_extra_beat_note_still_materializes() -> None:
    capabilities = qq_expression_capabilities("napcat", media_request_available=True)
    draft = _valid_photo_draft()
    draft["beats"] = [{"modality": "text", "text": "行，拍给你。", "note": "extra"}]
    proposal = materialize_expression_draft(
        raw=json.dumps(draft, ensure_ascii=False),
        request=_request(),
        capabilities=capabilities,
        quick_recovery=False,
        require_explicit_authored_decision_fields=True,
    )
    payload = json.loads(json.dumps(proposal))
    changes = payload.get("proposed_changes") or []
    kinds = {item.get("kind") for item in changes if isinstance(item, dict)}
    assert "expression_plan_transition" in kinds


def test_postel_fills_omitted_media_source_refs_and_strips_extra_keys() -> None:
    cleaned = _postel_expression_draft(
        {
            **_valid_photo_draft(),
            "media_source_refs": None,
            "photo": True,
            "helpdesk_ticket": "do-not-keep",
        }
    )
    cleaned.pop("photo", None)
    assert "helpdesk_ticket" not in cleaned
    assert cleaned["media_request"] == "consider_available_candidate"
    assert cleaned["media_source_refs"] == []
    assert cleaned["world_claims"] == []


def test_postel_omitted_media_fields_materialize_when_explicit_fields_are_required() -> None:
    capabilities = qq_expression_capabilities("napcat", media_request_available=True)
    draft = _valid_photo_draft()
    draft.pop("media_source_refs")
    draft.pop("cadence")
    proposal = materialize_expression_draft(
        raw=json.dumps(draft, ensure_ascii=False),
        request=_request(),
        capabilities=capabilities,
        quick_recovery=False,
        require_explicit_authored_decision_fields=True,
    )
    payload = json.loads(json.dumps(proposal))
    changes = payload.get("proposed_changes") or []
    kinds = {item.get("kind") for item in changes if isinstance(item, dict)}
    assert "expression_plan_transition" in kinds


def test_empty_pydantic_errors_still_log_the_real_value_error() -> None:
    exc = ValueError("media request requires an immediate expression")
    detail = _expression_contract_log_detail(exc)
    assert "media request requires an immediate expression" in detail
    assert detail != ""


def test_missing_explicit_fields_are_explained_in_chinese() -> None:
    exc = ValueError(
        "authored ExpressionDraft is missing explicit fields: cadence,media_source_refs"
    )
    text = _role_readable_expression_violation(exc)
    assert "漏了必须由你亲口写明的字段" in text
    assert "media_source_refs" in text
    assert "cadence" in text


@pytest.mark.parametrize("missing_detail", [None, "", " \n "])
def test_reselection_instruction_does_not_invent_missing_detail(missing_detail) -> None:
    instruction = _role_result_correction_instruction(
        {
            "failure_code": "paired_expression_reselection_invalid",
            "failure_detail": missing_detail,
        }
    )
    assert "上一轮结果未通过校验" in instruction
    assert "结构校验" not in instruction
    assert "投递形状不合法" not in instruction
    assert "paired_expression_reselection_invalid" in instruction
    assert "没把具体原因写清楚" in instruction


@pytest.mark.parametrize("detail", [
    "media_request 只有配 timing_choice=now 才能执行。",
    " \n完整表达的来源审核未闭合：外部事实需要来源。\n ",
])
def test_reselection_instruction_carries_the_chinese_detail(detail) -> None:
    filled = _role_result_correction_instruction(
        {
            "failure_code": "paired_expression_reselection_invalid",
            "failure_detail": detail,
        }
    )
    assert f"具体原因：{detail} " in filled
    assert "结构校验" not in filled
    assert "投递形状不合法" not in filled


def test_role_failure_kwargs_never_leave_detail_empty() -> None:
    kwargs = _role_failure_payload_kwargs(
        '{"result_kind":"full_turn"}',
        ValueError("media request requires an immediate expression"),
    )
    assert kwargs["failure_detail"]
    assert "timing_choice=now" in kwargs["failure_detail"]
    assert kwargs["rejected_raw_excerpt"]


class _EmptyExpressionProvider:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.8) -> str:
        del temperature
        self.calls.append(messages)
        return json.dumps(
            {
                "appraisal_draft": {
                    "appraise": False,
                    "brief_rationale": "No material emotional shift.",
                    "behavior_tendency": "engage",
                    "stance": "open",
                    "display_strategy": "natural",
                    "confidence": 7000,
                },
                "expression_draft": {},
            },
            ensure_ascii=False,
        )


def test_wait_pair_incomplete_is_explained_in_chinese() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_WAIT_PAIR_INCOMPLETE

    text = _role_readable_expression_violation(
        ValueError(
            SLIM_WAIT_PAIR_INCOMPLETE
            + "。这次缺了：waiting_for。宿主不会替你补上缺的字段。"
            "两个一起写才会在那个秒数叫醒你；只写一半不会生效，也不会被默默丢掉。"
            "两个都不写也可以，那就是这一轮没有人叫你。"
        )
    )
    assert SLIM_WAIT_PAIR_INCOMPLETE in text
    assert "这次缺了：waiting_for" in text
    assert "结构校验没过" not in text


def test_parse_combined_waiting_for_without_wait_does_not_compile_a_hope() -> None:
    raw = (
        '{"result_kind": "reply_only", "payload_json": '
        '"{\\"messages\\":[\\"行 等你\\"],'
        '\\"meaning_of_this\\":\\"他会回来继续说\\",'
        '\\"my_state\\":\\"我先等他回来\\",'
        '\\"waiting_for\\":\\"他倒完水回来\\"}"}'
    )
    parsed = _parse_combined(raw)
    assert parsed["expression_draft"].get("response_expectation") is None


def test_parse_combined_rejects_come_back_without_come_back_in() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_COME_BACK_PAIR_INCOMPLETE

    raw = (
        '{"result_kind": "reply_only", "payload_json": '
        '"{\\"messages\\":[\\"那家店我回头再说\\"],'
        '\\"meaning_of_this\\":\\"那家店的话题还没说完\\",'
        '\\"my_state\\":\\"我想先记下\\",'
        '\\"come_back\\":\\"书店那家店\\"}"}'
    )
    with pytest.raises(ValueError, match=SLIM_COME_BACK_PAIR_INCOMPLETE) as caught:
        _parse_combined(raw)
    assert "这次缺了：come_back_in" in str(caught.value)


def test_parse_combined_rejects_us_deltas_without_about_us() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE

    raw = (
        '{"result_kind": "reply_only", "payload_json": '
        '"{\\"messages\\":[\\"嗯\\"],'
        '\\"meaning_of_this\\":\\"他这次认真听了\\",'
        '\\"my_state\\":\\"我觉得近了一点\\",'
        '\\"us_deltas\\":{\\"closeness_bp\\":40},\\"why_us\\":\\"他认真听\\"}"}'
    )
    with pytest.raises(ValueError, match=SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE) as caught:
        _parse_combined(raw)
    assert "这次缺了：about_us" in str(caught.value)


def test_compact_gate_payload_json_invalid_is_explained_in_chinese() -> None:
    text = _role_readable_expression_violation(
        ValueError("compact gate carrier payload_json is invalid")
    )
    assert "payload_json" in text
    assert "合法 JSON" in text


def test_media_authority_violation_is_explained_in_chinese() -> None:
    text = _role_readable_expression_violation(
        ValueError("media request source lacks immutable event authority")
    )
    assert "没有不可变事件权威" in text
    assert "media_request 写成 none" in text
    assert "media_source_refs 写成 []" in text


def test_extra_beat_keys_are_explained_in_chinese() -> None:
    text = _role_readable_expression_violation(
        ValueError("beats.0.note Extra inputs are not permitted")
    )
    assert "契约不认" in text
    assert "modality" in text


def test_parse_combined_accepts_payload_json_with_trailing_extra_brace() -> None:
    raw = (
        '{"result_kind": "reply_only", "payload_json": '
        '"{\\"messages\\":[\\"是逛完了 那本诗集挺旧的 封面都泛黄了\\",'
        '\\"不过拍出来可能不太好看 光线也一般\\",'
        '\\"你要是真想看 我明天白天拍给你也行\\"],'
        '\\"meaning_of_this\\":\\"他还记得我昨天说去旧书市的事\\",'
        '\\"my_state\\":\\"我有点意外，也愿意给他看\\",'
        '\\"affect\\":\\"open\\",'
        '\\"components\\":[{\\"dimension\\":\\"warmth\\",\\"target_intensity_bp\\":4500}],'
        '\\"matters_bp\\":4500,'
        '\\"about_us\\":\\"他主动记得我提过的事，虽然只是让我拍张照片，但这种被记得的感觉有点暖\\",'
        '\\"why_us\\":\\"我们认识没多久，他愿意记这些小事，说明有在认真听我说话\\",'
        '\\"us_deltas\\":{\\"closeness_bp\\":25,\\"trust_bp\\":15}}}"}'
    )
    parsed = _parse_combined(raw)
    assert set(parsed) == {"appraisal_draft", "expression_draft"}
    beats = parsed["expression_draft"].get("beats") or []
    texts = [
        item.get("text")
        for item in beats
        if isinstance(item, dict)
    ]
    assert "是逛完了 那本诗集挺旧的 封面都泛黄了" in texts


@pytest.mark.asyncio
async def test_paired_expression_reselection_invalid_carries_readable_detail() -> None:
    provider = _EmptyExpressionProvider()
    cognition = InboundCharacterAuthor(
        flash_model=provider,
        expression_capabilities=ExpressionDraftCapabilities(
            profile_id="expression:test-reselection-detail.1",
            modalities=("text",),
            private_turn_state_mode="required",
        ),
    )
    with pytest.raises(ValidationTechnicalFailure) as caught:
        await cognition.propose(_request(call="call:reselection-detail"))
    assert caught.value.failure_code == "paired_expression_reselection_invalid"
    assert isinstance(caught.value.failure_detail, str)
    assert caught.value.failure_detail.strip()


class _UnauthorizedMediaProvider:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.8) -> str:
        del temperature
        self.calls.append(messages)
        return json.dumps(
            {
                "appraisal_draft": {
                    "appraise": False,
                    "brief_rationale": "No material emotional shift.",
                    "behavior_tendency": "engage",
                    "stance": "open",
                    "display_strategy": "natural",
                    "confidence": 7000,
                },
                "expression_draft": _valid_photo_draft(media_source_refs=["S18"]),
            },
            ensure_ascii=False,
        )


@pytest.mark.asyncio
async def test_unauthorized_media_source_raises_chinese_reselection_detail() -> None:
    provider = _UnauthorizedMediaProvider()
    cognition = InboundCharacterAuthor(
        flash_model=provider,
        expression_capabilities=qq_expression_capabilities(
            "napcat",
            media_request_available=True,
        ),
    )
    with pytest.raises(ValidationTechnicalFailure) as caught:
        await cognition.propose(_request(call="call:unauthorized-media"))
    assert caught.value.failure_code == "paired_expression_reselection_invalid"
    detail = str(caught.value.failure_detail)
    assert detail.strip()
    assert "短名" in detail or "不可变事件权威" in detail
    assert len(provider.calls) == 1


def test_parse_combined_rejects_illegal_declared_display() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_DECLARED_DISPLAY_INVALID

    raw = (
        '{"result_kind": "reply_only", "payload_json": '
        '"{\\"messages\\":[\\"给你看\\"],'
        '\\"meaning_of_this\\":\\"他想看一张\\",'
        '\\"my_state\\":\\"我想给他看\\",'
        '\\"declared_display\\":\\"nsfw\\"}"}'
    )
    with pytest.raises(ValueError, match=SLIM_DECLARED_DISPLAY_INVALID):
        _parse_combined(raw)


def test_illegal_slim_display_and_later_stems_pass_through_without_host_prefix() -> None:
    from companion_daemon.world_v2.present_prompt import (
        SLIM_DECLARED_DISPLAY_INVALID,
        SLIM_HOW_IT_LANDED_INVALID,
        SLIM_LATER_NOT_A_DURATION,
        SLIM_LATER_REQUIRES_TEXT,
    )

    for stem in (
        SLIM_DECLARED_DISPLAY_INVALID,
        SLIM_HOW_IT_LANDED_INVALID,
        SLIM_LATER_NOT_A_DURATION,
        SLIM_LATER_REQUIRES_TEXT,
    ):
        text = _role_readable_expression_violation(
            ValueError(stem + "。宿主不会替你改成一个合法值，也不会把写坏的声明丢掉后假装没写。")
        )
        assert text.startswith(stem)
        assert "结构校验没过" not in text


def test_parse_combined_rejects_we_are_without_said_as() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_COMMITMENT_TRIPLET_INCOMPLETE

    raw = (
        '{"result_kind": "reply_only", "payload_json": '
        '"{\\"messages\\":[\\"我们算朋友了吧\\"],'
        '\\"meaning_of_this\\":\\"他也在确认我们的关系\\",'
        '\\"my_state\\":\\"我想把关系说清楚\\",'
        '\\"we_are\\":\\"friend\\",\\"calling_it\\":\\"friends\\"}"}'
    )
    with pytest.raises(ValueError, match=SLIM_COMMITMENT_TRIPLET_INCOMPLETE) as caught:
        _parse_combined(raw)
    assert "这次缺了：said_as" in str(caught.value)


def test_commitment_triplet_is_explained_in_chinese() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_COMMITMENT_TRIPLET_INCOMPLETE

    text = _role_readable_expression_violation(
        ValueError(
            SLIM_COMMITMENT_TRIPLET_INCOMPLETE
            + "。这次缺了：said_as。宿主不会替你补上缺的字段。"
        )
    )
    assert SLIM_COMMITMENT_TRIPLET_INCOMPLETE in text
    assert "这次缺了：said_as" in text
    assert "结构校验没过" not in text


def _valid_dual_draft(*, text: str = "行，我等你。") -> dict[str, object]:
    return {
        "appraisal_draft": {
            "appraise": False,
            "brief_rationale": "No material emotional shift.",
            "behavior_tendency": "observe",
            "stance": "wait",
            "display_strategy": "withhold",
            "confidence": 3000,
        },
        "expression_draft": {
            "private_turn_state": {
                "contract": "private-turn-state.1",
                "inner_state_summary": "他话只说到一半，我想先应一声。",
                "attended_source_refs": [],
            },
            "timing_choice": "now",
            "cadence": "conversational",
            "beats": [{"modality": "text", "text": text}],
            "stance": "attentive",
            "brief_rationale": "Stay with the current conversation.",
            "confidence": 7800,
            "world_claims": [],
            "media_request": "none",
            "media_source_refs": [],
        },
    }


class _HalfWaitThenFixedProvider:
    model = "combined-flash"

    def __init__(self, *, repair: bool = True) -> None:
        self.calls: list[list[dict[str, str]]] = []
        self.repair = repair

    async def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.8) -> str:
        del temperature
        self.calls.append(messages)
        if self.repair and "上一轮结果未通过校验" in messages[0]["content"]:
            return json.dumps(_valid_dual_draft(), ensure_ascii=False)
        return json.dumps(
            {
                "result_kind": "reply_only",
                "payload_json": json.dumps(
                    {
                        "messages": ["行 等你"],
                        "meaning_of_this": "他会回来继续说",
                        "my_state": "我先等他回来",
                        "wait": 60,
                        "pressure_bp": 4200,
                        "importance_bp": 5600,
                    },
                    ensure_ascii=False,
                ),
            },
            ensure_ascii=False,
        )


async def _respond_half_set(
    *,
    tmp_path,
    provider: object,
    stem: str,
    text: str,
    expression_episode_mode: str = "off",
):
    cognition = InboundCharacterAuthor(flash_model=provider)
    app = build_sqlite_world_v2_test_application(
        path=tmp_path / f"paired-{stem}-reselection.sqlite",
        config=replace(_config(), expression_episode_mode=expression_episode_mode),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=cognition,
        ),
        transport=_DeliveredTransport(),
        now=NOW,
    )
    try:
        outcome = await app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id=f"message:half-{stem}",
                text=text,
                observed_at=NOW,
                trace_id=f"trace:half-{stem}",
            )
        )
    finally:
        app.close()
    return outcome


async def _respond_half_wait(*, tmp_path, provider: _HalfWaitThenFixedProvider):
    return await _respond_half_set(
        tmp_path=tmp_path,
        provider=provider,
        stem="wait-pair",
        text="你猜我今天碰到谁了",
    )


@pytest.mark.asyncio
async def test_half_written_wait_pair_gets_one_same_occasion_reselection(tmp_path) -> None:
    provider = _HalfWaitThenFixedProvider(repair=True)
    outcome = await _respond_half_wait(tmp_path=tmp_path, provider=provider)

    assert outcome.status == "action_authorized"
    assert len(provider.calls) == 2
    assert len(provider.calls) <= MAX_PROVIDER_CALLS_PER_TURN
    correction = provider.calls[1][0]["content"]
    assert "上一轮结果未通过校验" in correction
    assert "这次缺了：waiting_for" in correction
    assert "宿主不会替你补上缺的字段" in correction


@pytest.mark.asyncio
async def test_half_written_wait_pair_still_fails_after_one_reselection(tmp_path) -> None:
    provider = _HalfWaitThenFixedProvider(repair=False)
    outcome = await _respond_half_wait(tmp_path=tmp_path, provider=provider)

    assert outcome.status == "deferred"
    assert len(provider.calls) == 2
    assert len(provider.calls) <= MAX_PROVIDER_CALLS_PER_TURN
    assert "上一轮结果未通过校验" in provider.calls[1][0]["content"]


class _HalfCommitmentThenFixedProvider:
    model = "combined-flash"

    def __init__(self, *, repair: bool = True) -> None:
        self.calls: list[list[dict[str, str]]] = []
        self.repair = repair

    async def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.8) -> str:
        del temperature
        self.calls.append(messages)
        if self.repair and "上一轮结果未通过校验" in messages[0]["content"]:
            return json.dumps(
                _valid_dual_draft(text="我们算朋友了吧"),
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "result_kind": "reply_only",
                "payload_json": json.dumps(
                    {
                        "messages": ["我们算朋友了吧"],
                        "meaning_of_this": "他也在认真确认我们的关系",
                        "my_state": "我想把关系说清楚",
                        "we_are": "friend",
                        "calling_it": "friends",
                    },
                    ensure_ascii=False,
                ),
            },
            ensure_ascii=False,
        )


@pytest.mark.asyncio
async def test_half_written_commitment_triplet_gets_one_same_occasion_reselection(
    tmp_path,
) -> None:
    provider = _HalfCommitmentThenFixedProvider(repair=True)
    outcome = await _respond_half_set(
        tmp_path=tmp_path,
        provider=provider,
        stem="commitment-triplet",
        text="我不把你当随便聊聊的网友。跟你说话是认真的",
    )

    assert outcome.status == "action_authorized"
    assert len(provider.calls) == 2
    assert len(provider.calls) <= MAX_PROVIDER_CALLS_PER_TURN
    correction = provider.calls[1][0]["content"]
    assert "上一轮结果未通过校验" in correction
    assert "这次缺了：said_as" in correction
    assert "宿主不会替你补上缺的字段" in correction


def _stream_usage(*, ref: str) -> ModelUsageProvenance:
    material = {
        "usage_contract": "model-usage.1",
        "route_class": "chat",
        "input_tokens": 20,
        "output_tokens": 8,
        "thinking_tokens": 0,
        "token_provenance": "provider_reported",
        "transport": "provider_api",
        "provider": "paired-fake-provider",
        "provider_usage_ref": ref,
    }
    digest = sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ModelUsageProvenance(**material, provider_usage_hash=digest)


class _HalfWaitThenFixedStreamProvider:
    """Production compact path: stream + forced tool, not complete()."""

    model = "combined-flash"
    supports_required_tool_choice = True
    supports_strict_tool_choice = True

    def __init__(self, *, repair: bool = True) -> None:
        self.calls: list[list[dict[str, str]]] = []
        self.repair = repair

    def _raw(self, messages: list[dict[str, str]]) -> str:
        if self.repair and messages and "上一轮结果未通过校验" in messages[0]["content"]:
            inner = {
                "messages": ["行 等你"],
                "meaning_of_this": "他会回来继续说",
                "my_state": "我先等他回来",
                "waiting_for": "他倒完水回来",
                "wait": 60,
                "pressure_bp": 4200,
                "importance_bp": 5600,
            }
        else:
            inner = {
                "messages": ["行 等你"],
                "meaning_of_this": "他会回来继续说",
                "my_state": "我先等他回来",
                "wait": 60,
                "pressure_bp": 4200,
                "importance_bp": 5600,
            }
        return json.dumps(
            {
                "result_kind": "reply_only",
                "payload_json": json.dumps(inner, ensure_ascii=False),
            },
            ensure_ascii=False,
        )

    async def complete_json_stream_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
        on_text_delta=None,  # type: ignore[no-untyped-def]
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, ModelUsageProvenance]:
        del temperature, tools, tool_choice
        self.calls.append(messages)
        raw = self._raw(messages)
        if on_text_delta is not None:
            on_text_delta(raw)
        return raw, _stream_usage(ref=f"usage:half-wait-stream:{len(self.calls)}")


@pytest.mark.asyncio
async def test_stream_compact_half_wait_pair_gets_one_same_occasion_reselection(
    tmp_path,
) -> None:
    provider = _HalfWaitThenFixedStreamProvider(repair=True)
    outcome = await _respond_half_set(
        tmp_path=tmp_path,
        provider=provider,
        stem="wait-pair-stream",
        text="你猜我今天碰到谁了",
        expression_episode_mode="stream",
    )

    assert outcome.status == "action_authorized"
    assert len(provider.calls) == 2
    assert len(provider.calls) <= MAX_PROVIDER_CALLS_PER_TURN
    correction = provider.calls[1][0]["content"]
    assert "上一轮结果未通过校验" in correction
    assert "这次缺了：waiting_for" in correction
    assert "宿主不会替你补上缺的字段" in correction
