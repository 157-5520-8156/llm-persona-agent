"""Production app composition only; all HTTP is forbidden in these checks."""
import httpx
import pytest

from companion_daemon.config import Settings
from companion_daemon.world_v2.qq_c2c_onebot_app import create_qq_c2c_onebot_app
import companion_daemon.world_v2.qq_c2c_host as host_module


def settings(tmp_path, **overrides):
    values = dict(_env_file=None, database_path=tmp_path / "world.sqlite",
                  DEEPSEEK_API_KEY="offline-profile-test", DEEPSEEK_DEBUG_API_KEY="offline-profile-test",
                  DEEPSEEK_CHARACTER_THINKING_ENABLED=False,
                  WORLD_V2_TEXT_ENDPOINT_ENABLED=False, WORLD_V2_MEDIA_PREVIEW_ENABLED=False,
                  WORLD_V2_EXPRESSION_EPISODE_MODE="off",
                  NAPCAT_ALLOWED_PRIVATE_USER_IDS="10001", QQ_ADAPTER="napcat")
    return Settings(**(values | overrides))


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [None, "whole_v3_review_v6"])
async def test_onebot_explicit_release_profile_owns_metered_whole_reviewer(tmp_path, monkeypatch, profile):
    async def forbidden(*args, **kwargs):
        pytest.fail("composition check must not send HTTP")
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    original = host_module.build_semantic_chat_composition
    captured = {}
    def build(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(host_module, "build_semantic_chat_composition", build)
    options = {"WORLD_V2_VISIBLE_EXPRESSION_PROFILE": profile} if profile else {}
    app = create_qq_c2c_onebot_app(adapter="napcat", settings=settings(tmp_path, **options))
    host = app.state.qq_c2c_host
    semantic = host._semantic_chat
    try:
        assert captured["visible_source_review_required"] is bool(profile)
        assert captured["visible_author_tool_version"] == ("3" if profile else "1")
        assert captured["visible_source_review_version"] == ("6" if profile else "1")
        assert captured["usage_observer"] is not None
        reviewer = semantic.source_closure_model
        if profile:
            assert reviewer is not None and reviewer in semantic._owned_models
            assert reviewer is not semantic.world_support_model
            assert reviewer.usage_observer is captured["usage_observer"]
        else:
            assert reviewer is None
    finally:
        await host.aclose()
        await host.wait_for_shutdown_quiescence()
    assert semantic._models_closed


@pytest.mark.parametrize("mode", ["shadow", "stream"])
def test_release_profile_refuses_nonatomic_expression_before_database_creation(tmp_path, mode):
    with pytest.raises(ValueError, match="atomic expression"):
        create_qq_c2c_onebot_app(adapter="napcat", settings=settings(
            tmp_path, WORLD_V2_VISIBLE_EXPRESSION_PROFILE="whole_v3_review_v6",
            WORLD_V2_EXPRESSION_EPISODE_MODE=mode))
    assert not (tmp_path / "world.sqlite").exists()
