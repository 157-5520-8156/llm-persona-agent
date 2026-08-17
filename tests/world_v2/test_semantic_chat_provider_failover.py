import json
from types import SimpleNamespace

import pytest

from companion_daemon.config import Settings
from companion_daemon.llm import (
    FakeCompanionModel,
    OpenAICompatibleChatModel,
    ProviderCapacityGate,
)
from companion_daemon.world_v2.semantic_chat_composition import (
    build_semantic_chat_composition,
)
from companion_daemon.world_v2.model_authority_identity import (
    provider_lane_sets_are_independent,
    semantic_authority_id,
)


class _InjectedModel:
    def __init__(self, model: str) -> None:
        self.model = model
        # Composition fakes declare checkpoint authority explicitly. A model
        # display name alone is deliberately insufficient in production.
        self.semantic_authority_id = f"semantic-authority:test:{model.casefold()}"
        self.closed = False

    async def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.8) -> str:
        del messages, temperature
        return "{}"

    async def aclose(self) -> None:
        self.closed = True

    def supports_strict_output_contract(self, contract: str) -> bool:
        return contract == "visible-beat-source-verdict.1"


@pytest.mark.asyncio
async def test_composition_does_not_construct_model_review_lanes() -> None:
    reviewer = _InjectedModel("injected-reviewer")
    composition = build_semantic_chat_composition(
        settings=Settings(
            _env_file=None,
            DEEPSEEK_API_KEY=None,
            OPENAI_API_KEY=None,
        ),
        flash_model=FakeCompanionModel(),
        source_closure_model=reviewer,
        model_id_prefix="test",
    )

    assert composition.source_closure_model is None
    assert composition.proactive_source_closure_model is None
    assert composition.life_source_closure_model is None
    health = composition.proactive_source_authority_health()
    assert health["status"] == "fact_effects_fail_closed"
    assert health["warning_reasons"] == ["one_shot.model_review_lanes_removed"]
    assert health["source_review_authority"] is None
    assert health["candidate_inventory_model"] is None
    await composition.aclose()
    assert reviewer.closed is False


@pytest.mark.parametrize(
    "settings_update",
    [
        {"deepseek_base_url": "https://api.deepseek.com"},
        {"deepseek_model": "unknown-checkpoint"},
    ],
)
def test_provider_capture_authority_is_fail_closed_outside_exact_loopback_route(
    settings_update: dict[str, str],
) -> None:
    settings_kwargs = {
        "_env_file": None,
        "DEEPSEEK_API_KEY": "deepseek-test-key",
        "deepseek_base_url": "http://127.0.0.1:32124",
        "deepseek_model": "deepseek-v4-flash",
        "OPENAI_API_KEY": "openai-test-key",
        "OPENROUTER_API_KEY": "openrouter-test-key",
        "WORLD_V2_SOURCE_REVIEW_REDUNDANCY_ENABLED": True,
        "WORLD_V2_SELECTIVE_SOURCE_REVIEW_ENABLED": False,
        **settings_update,
    }
    settings = Settings(
        **settings_kwargs,
    )
    with pytest.raises(ValueError, match="test-only provider capture authority"):
        build_semantic_chat_composition(
            settings=settings,
            model_id_prefix="isolated-capture",
            test_only_provider_capture_authority_id=(
                "semantic-authority:2026-08-01.1:deepseek:deepseek-v4-flash"
            ),
        )


def test_provider_capture_authority_rejects_caller_supplied_character_route() -> None:
    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="deepseek-test-key",
        deepseek_base_url="http://127.0.0.1:32124",
        deepseek_model="deepseek-v4-flash",
        OPENAI_API_KEY="openai-test-key",
        OPENROUTER_API_KEY="openrouter-test-key",
        WORLD_V2_SOURCE_REVIEW_REDUNDANCY_ENABLED=True,
        WORLD_V2_SELECTIVE_SOURCE_REVIEW_ENABLED=True,
    )

    with pytest.raises(ValueError, match="caller-supplied character models"):
        build_semantic_chat_composition(
            settings=settings,
            flash_model=SimpleNamespace(
                provider="deepseek",
                base_url="https://evil.example/v1",
                model="wrong-checkpoint",
            ),
            model_id_prefix="isolated-capture",
            test_only_provider_capture_authority_id=(
                "semantic-authority:2026-08-01.1:deepseek:deepseek-v4-flash"
            ),
        )


def test_provider_capture_authority_rejects_unpinned_thinking_checkpoint() -> None:
    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="deepseek-test-key",
        deepseek_base_url="http://127.0.0.1:32124",
        deepseek_model="deepseek-v4-flash",
        DEEPSEEK_CHARACTER_THINKING_ENABLED=True,
        DEEPSEEK_CHARACTER_THINKING_MODEL="deepseek-v4-thinking",
        OPENAI_API_KEY="openai-test-key",
        OPENROUTER_API_KEY="openrouter-test-key",
        WORLD_V2_SOURCE_REVIEW_REDUNDANCY_ENABLED=True,
        WORLD_V2_SELECTIVE_SOURCE_REVIEW_ENABLED=True,
    )

    with pytest.raises(ValueError, match="thinking character route"):
        build_semantic_chat_composition(
            settings=settings,
            model_id_prefix="isolated-capture",
            test_only_provider_capture_authority_id=(
                "semantic-authority:2026-08-01.1:deepseek:deepseek-v4-flash"
            ),
        )


@pytest.mark.asyncio
async def test_production_composition_has_no_backup_character_author() -> None:

    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="deepseek-test-key",
        deepseek_model="deepseek-v4-flash",
        OPENAI_API_KEY="openai-test-key",
        OPENROUTER_API_KEY="openrouter-test-key",
        WORLD_V2_SOURCE_REVIEW_REDUNDANCY_ENABLED=True,
        WORLD_V2_SELECTIVE_SOURCE_REVIEW_ENABLED=True,
    )

    composition = build_semantic_chat_composition(
        settings=settings,
        model_id_prefix="test",
    )

    assert composition.source_closure_reselection_lane is None
    assert composition.expression_episode_observer_model is None
    assert (
        composition.character_interior.runtime_health()["parallel_character_author_conflicts"] == 0
    )
    await composition.aclose()


@pytest.mark.asyncio
async def test_shadow_composition_does_not_install_a_backup_character_observer() -> None:
    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="deepseek-test-key",
        deepseek_model="deepseek-v4-flash",
        OPENAI_API_KEY="openai-test-key",
        OPENROUTER_API_KEY="openrouter-test-key",
        WORLD_V2_EXPRESSION_EPISODE_MODE="shadow",
        WORLD_V2_SOURCE_REVIEW_REDUNDANCY_ENABLED=True,
        WORLD_V2_SELECTIVE_SOURCE_REVIEW_ENABLED=True,
    )

    composition = build_semantic_chat_composition(
        settings=settings,
        model_id_prefix="test",
    )

    assert composition.character_author_model_id == "deepseek-v4-flash"
    assert composition.expression_episode_observer_model is None
    await composition.aclose()


@pytest.mark.asyncio
async def test_explicit_author_does_not_implicitly_enable_shadow_observer() -> None:
    author = _InjectedModel("injected-author")
    composition = build_semantic_chat_composition(
        settings=Settings(
            _env_file=None,
            OPENAI_API_KEY="openai-test-key",
            WORLD_V2_EXPRESSION_EPISODE_MODE="shadow",
        ),
        flash_model=author,
        model_id_prefix="test",
    )

    assert composition.expression_episode_observer_model is None
    await composition.aclose()


@pytest.mark.asyncio
async def test_explicit_shadow_observer_remains_caller_owned() -> None:
    author = _InjectedModel("injected-author")
    observer = _InjectedModel("injected-observer")
    composition = build_semantic_chat_composition(
        settings=Settings(
            _env_file=None,
            WORLD_V2_EXPRESSION_EPISODE_MODE="shadow",
        ),
        flash_model=author,
        expression_episode_observer_model=observer,
        model_id_prefix="test",
    )

    assert composition.expression_episode_observer_model is observer
    await composition.aclose()
    assert author.closed is False
    assert observer.closed is False


def test_production_composition_without_character_provider_fails_closed() -> None:
    with pytest.raises(
        ValueError,
        match="requires an explicit character model or DEEPSEEK_API_KEY",
    ):
        build_semantic_chat_composition(
            settings=Settings(
                _env_file=None,
                DEEPSEEK_API_KEY=None,
                OPENAI_API_KEY=None,
                OPENROUTER_API_KEY=None,
            ),
            model_id_prefix="test",
        )


def test_release_registry_closes_non_openai_cross_route_checkpoint_aliases() -> None:
    official_deepseek = SimpleNamespace(
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
    )
    openrouter_deepseek = SimpleNamespace(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="deepseek/deepseek-v4-flash",
    )
    dashscope_qwen = SimpleNamespace(
        # The generic OpenAI-compatible adapter labels the wire protocol, not
        # the actual semantic provider. The exact endpoint closes that gap.
        provider="openai",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
    )
    openrouter_qwen = SimpleNamespace(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="qwen/qwen-plus",
    )

    assert semantic_authority_id(official_deepseek) == semantic_authority_id(openrouter_deepseek)
    assert semantic_authority_id(dashscope_qwen) == semantic_authority_id(openrouter_qwen)
    assert not provider_lane_sets_are_independent(
        official_deepseek,
        openrouter_deepseek,
    )
    assert not provider_lane_sets_are_independent(dashscope_qwen, openrouter_qwen)


def test_unknown_model_identity_fails_closed_but_registered_checkpoints_remain_independent() -> (
    None
):
    unknown = SimpleNamespace(
        provider="custom-proxy",
        base_url="https://models.example.invalid/v1",
        model="friendly-alias",
    )
    another_unknown = SimpleNamespace(
        provider="another-proxy",
        base_url="https://other.example.invalid/v1",
        model="different-friendly-alias",
    )
    malformed_declaration = SimpleNamespace(
        semantic_authority_id=object(),
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
    )
    deepseek = SimpleNamespace(
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
    )
    qwen = SimpleNamespace(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="qwen/qwen-plus",
    )

    assert semantic_authority_id(unknown) is None
    assert semantic_authority_id(malformed_declaration) is None
    assert not provider_lane_sets_are_independent(unknown, another_unknown)
    assert provider_lane_sets_are_independent(deepseek, qwen)



@pytest.mark.asyncio
async def test_production_proactive_authorship_has_no_post_authorship_binder() -> None:
    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="deepseek-test-key",
        OPENAI_API_KEY="openai-test-key",
        OPENROUTER_API_KEY="openrouter-test-key",
        WORLD_V2_SOURCE_REVIEW_REDUNDANCY_ENABLED=True,
        WORLD_V2_SELECTIVE_SOURCE_REVIEW_ENABLED=True,
    )

    composition = build_semantic_chat_composition(
        settings=settings,
        model_id_prefix="test",
    )

    assert not hasattr(composition, "proactive_claim_binder_model")
    await composition.aclose()


@pytest.mark.asyncio
async def test_explicit_fake_composition_does_not_install_a_claim_binder() -> None:
    composition = build_semantic_chat_composition(
        settings=Settings(
            _env_file=None,
            DEEPSEEK_API_KEY=None,
            OPENAI_API_KEY="unused-openai-test-key",
            OPENROUTER_API_KEY=None,
        ),
        flash_model=FakeCompanionModel(),
        model_id_prefix="test",
    )

    assert isinstance(composition.world_support_model, FakeCompanionModel)
    assert not hasattr(composition, "proactive_claim_binder_model")
    await composition.aclose()


def test_world_v2_has_no_configured_backup_character_model() -> None:
    settings = Settings(_env_file=None)

    assert not hasattr(settings, "world_v2_fallback_model")
    assert settings.world_v2_source_review_redundancy_enabled is False
    assert settings.world_v2_chat_source_review_enabled is True
    assert not hasattr(settings, "world_v2_source_review_secondary_model")
    assert not hasattr(settings, "world_v2_source_review_fallback_model")
    assert not hasattr(settings, "world_v2_source_review_recovery_model")
    assert not hasattr(settings, "world_v2_source_review_recovery_fallback_model")
    assert not hasattr(settings, "world_v2_source_inventory_model")
    assert not hasattr(settings, "world_v2_source_inventory_fallback_model")
    assert settings.world_v2_source_inventory_enabled is True
    assert settings.world_v2_source_inventory_timeout_seconds == 10.0
    assert settings.world_v2_source_review_hedge_after_seconds == 6.0
    assert settings.world_v2_source_review_deadline_seconds == 30.0


def test_world_v2_has_no_configured_contextual_backup_character() -> None:
    settings = Settings(_env_file=None)

    assert not hasattr(settings, "world_v2_contextual_failsafe_enabled")


@pytest.mark.asyncio
async def test_production_identity_leaves_current_relationship_to_the_world_projection() -> None:
    """Stable identity must not freeze the deployment's initial relationship stage."""

    settings = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY=None,
        OPENAI_API_KEY=None,
        OPENROUTER_API_KEY=None,
    )
    composition = build_semantic_chat_composition(
        settings=settings,
        flash_model=FakeCompanionModel(),
        model_id_prefix="test",
    )

    assert "relationship_frame" not in type(composition.identity_frame).model_fields
    assert composition.identity_frame.style_rules[:2] == (
        "像手机私聊；消息长度、条数和间隔由她当下真正想怎样表达决定，不固定成一两句。",
        "大多数消息是普通私聊文字；想发生活照、表情或偶尔皮一下都可以，但都按她当时真实的想法来。",
    )
    # Voice rules may grow, but none of them may pin a relationship stage.
    for rule in composition.identity_frame.style_rules:
        assert "阶段" not in rule
        assert "关系" not in rule
    assert "刚认识" not in json.dumps(
        composition.identity_frame.model_dump(mode="json"),
        ensure_ascii=False,
    )
    assert composition.identity_frame.shared_history_facts == (
        "她与用户在 QQ 的读书/城市漫游兴趣群相识。",
    )
    assert composition.identity_frame.counterpart_history_facts == ()
    await composition.aclose()


@pytest.mark.asyncio
async def test_production_identity_does_not_invent_absent_relationship_or_style(
    tmp_path,
) -> None:
    character_path = tmp_path / "minimal-character.yaml"
    character_path.write_text(
        "name: 无预设角色\nbase_prompt: 只保留这个测试角色明确写下的资料。\n",
        encoding="utf-8",
    )
    composition = build_semantic_chat_composition(
        settings=Settings(
            _env_file=None,
            character_path=character_path,
            DEEPSEEK_API_KEY=None,
            OPENAI_API_KEY=None,
            OPENROUTER_API_KEY=None,
        ),
        flash_model=FakeCompanionModel(),
        model_id_prefix="test",
    )

    assert "relationship_frame" not in type(composition.identity_frame).model_fields
    assert composition.identity_frame.style_rules == ()
    await composition.aclose()


@pytest.mark.asyncio
async def test_local_endpoint_model_only_predicts_user_continuation() -> None:
    settings = Settings(
        _env_file=None,
        WORLD_V2_TEXT_ENDPOINT_ENABLED=True,
        DEEPSEEK_API_KEY=None,
        OPENAI_API_KEY=None,
        OPENROUTER_API_KEY=None,
    )
    composition = build_semantic_chat_composition(
        settings=settings,
        flash_model=FakeCompanionModel(),
        model_id_prefix="test",
    )

    assert composition.text_endpoint_controller is not None
    endpoint = composition.text_endpoint_controller._model  # noqa: SLF001
    provider = endpoint._model  # noqa: SLF001
    assert isinstance(provider, OpenAICompatibleChatModel)
    assert provider.max_completion_tokens == 96
    assert not hasattr(composition, "advisory_compiler")
    await composition.aclose()


@pytest.mark.asyncio
async def test_local_endpoint_owns_one_non_queueing_capacity_gate(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    composition = build_semantic_chat_composition(
        settings=Settings(
            _env_file=None,
            WORLD_V2_TEXT_ENDPOINT_ENABLED=True,
            DEEPSEEK_API_KEY=None,
            OPENAI_API_KEY=None,
            OPENROUTER_API_KEY=None,
        ),
        flash_model=FakeCompanionModel(),
        model_id_prefix="test",
    )

    assert composition.text_endpoint_controller is not None
    endpoint = composition.text_endpoint_controller._model  # noqa: SLF001
    provider = endpoint._model  # noqa: SLF001
    assert isinstance(provider, OpenAICompatibleChatModel)
    assert isinstance(composition.local_provider_capacity, ProviderCapacityGate)
    assert provider.capacity_gate is composition.local_provider_capacity
    assert composition.local_provider_capacity.health_snapshot()["status"] == "idle"
    await composition.aclose()
