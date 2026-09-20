"""Production app composition only; all HTTP is forbidden in these checks."""
import httpx
import pytest
import asyncio
import sqlite3

from companion_daemon.config import Settings
from companion_daemon.world_v2.qq_c2c_onebot_app import create_qq_c2c_onebot_app
import companion_daemon.world_v2.qq_c2c_host as host_module
import companion_daemon.world_v2.semantic_chat_composition as semantic_module
from companion_daemon.world_v2.visible_independent_review_runtime import IndependentVisibleReviewer


EXPERIMENTAL_PROFILES = ("experimental_independent_v21", "experimental_independent_v22")


def settings(tmp_path, **overrides):
    values = dict(_env_file=None, database_path=tmp_path / "world.sqlite",
                  DEEPSEEK_API_KEY="offline-profile-test", DEEPSEEK_DEBUG_API_KEY="offline-profile-test",
                  DEEPSEEK_CHARACTER_THINKING_ENABLED=False,
                  WORLD_V2_TEXT_ENDPOINT_ENABLED=False, WORLD_V2_MEDIA_PREVIEW_ENABLED=False,
                  WORLD_V2_EXPRESSION_EPISODE_MODE="off",
                  NAPCAT_ALLOWED_PRIVATE_USER_IDS="10001", QQ_ADAPTER="napcat")
    return Settings(**(values | overrides))


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [None, "whole_v3_review_v6", "whole_v3_review_v7", "whole_v3_review_v8", *EXPERIMENTAL_PROFILES])
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
        assert captured["visible_source_review_version"] == (profile.rsplit("_v", 1)[1] if profile else "1")
        assert captured["usage_observer"] is not None
        reviewer = semantic.source_closure_model
        if profile in EXPERIMENTAL_PROFILES:
            assert isinstance(reviewer, IndependentVisibleReviewer)
            clients = (*reviewer.meaning_models, reviewer.source_model)
            assert len({id(client) for client in clients}) == 3
            assert [client.model for client in reviewer.meaning_models] == ["deepseek-v4-pro", "deepseek-v4-flash"]
            assert reviewer.source_model.model == "deepseek-v4-pro"
            assert reviewer.source_response_mode == "json_object"
            assert reviewer.scope_permission_context and not reviewer.scope_subjective_history
            assert all(client in semantic._owned_models and not client.thinking_enabled for client in clients)
            assert all(client.usage_observer is captured["usage_observer"] for client in clients)
            assert not semantic._owned_finalizers
        elif profile:
            assert reviewer is not None and reviewer in semantic._owned_models
            assert reviewer is not semantic.world_support_model
            assert reviewer.usage_observer is captured["usage_observer"]
        else:
            assert reviewer is None
    finally:
        await host.aclose()
        await host.wait_for_shutdown_quiescence()
    assert semantic._models_closed


@pytest.mark.parametrize("profile", ["whole_v3_review_v6", "whole_v3_review_v7", "whole_v3_review_v8", *EXPERIMENTAL_PROFILES])
@pytest.mark.parametrize("mode", ["shadow", "stream"])
def test_release_profile_refuses_nonatomic_expression_before_database_creation(tmp_path, mode, profile):
    with pytest.raises(ValueError, match="atomic expression"):
        create_qq_c2c_onebot_app(adapter="napcat", settings=settings(
            tmp_path, WORLD_V2_VISIBLE_EXPRESSION_PROFILE=profile,
            WORLD_V2_EXPRESSION_EPISODE_MODE=mode))
    assert not (tmp_path / "world.sqlite").exists()


@pytest.fixture
def tracked_review_resources(monkeypatch):
    from companion_daemon.world_v2 import life_content_store

    async def forbidden(*args, **kwargs):
        pytest.fail("composition check must not send HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    clients, stores, interior_kwargs = [], [], {}
    real_model = semantic_module.DeepSeekChatModel
    real_store = life_content_store.SQLiteImmutableLifeContentStore
    real_compose = semantic_module.compose_production_character_interior

    class TrackedModel(real_model):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.close_count = 0
            clients.append(self)

        async def aclose(self):
            self.close_count += 1
            await super().aclose()

    class TrackedStore(real_store):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.close_count = 0
            stores.append(self)

        def close(self):
            self.close_count += 1
            super().close()

    def compose(**kwargs):
        interior_kwargs.update(kwargs)
        return real_compose(**kwargs)

    monkeypatch.setattr(semantic_module, "DeepSeekChatModel", TrackedModel)
    monkeypatch.setattr(life_content_store, "SQLiteImmutableLifeContentStore", TrackedStore)
    monkeypatch.setattr(semantic_module, "compose_production_character_interior", compose)
    return clients, stores, interior_kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", EXPERIMENTAL_PROFILES)
async def test_experimental_routes_keep_author_and_life_independent_and_close_once(profile, tmp_path, tracked_review_resources):
    clients, stores, interior = tracked_review_resources
    configured = settings(tmp_path, WORLD_V2_VISIBLE_EXPRESSION_PROFILE=profile,
        WORLD_V2_VISIBLE_SOURCE_REVIEW_MODEL="source-only-fixture",
        WORLD_V2_LIFE_CANDIDATE_REVIEW_ENABLED=True,
        WORLD_V2_LIFE_CANDIDATE_REVIEW_MODEL="life-only-fixture")
    app = create_qq_c2c_onebot_app(adapter="napcat", settings=configured)
    host = app.state.qq_c2c_host
    semantic = host._semantic_chat
    life = interior["life_source_reviewer"]
    try:
        review = semantic.source_closure_model
        assert interior["visible_source_review_version"] == profile.rsplit("_v", 1)[1]
        assert interior["flash_model"].model == configured.deepseek_model
        assert semantic.world_support_model.model == configured.deepseek_model
        assert review.source_model.model == "source-only-fixture"
        assert life.model.model == "life-only-fixture"
        assert life.model is not review.source_model
        assert all(client.usage_observer is interior["flash_model"].usage_observer for client in clients)
        assert life.store in semantic._owned_finalizers
        assert life.store._world_id == host_module.qq_c2c_world_id(configured.primary_user_id)
        assert life.store._connection.execute("PRAGMA database_list").fetchone()[2] == str(configured.database_path)
        assert not semantic.life_source_authority_health()["contracts"]["life-development-source-closure-review.1"]["release_qualified"]
    finally:
        await host.aclose()
        await host.wait_for_shutdown_quiescence()
    await host.aclose()
    assert all(client.close_count == 1 and client.client.is_closed for client in clients)
    assert life.store.close_count == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        life.store._connection.execute("SELECT 1")


@pytest.mark.asyncio
async def test_life_evidence_store_outlives_deferred_semantic_tasks(tmp_path, tracked_review_resources):
    clients, _stores, interior = tracked_review_resources
    host = create_qq_c2c_onebot_app(adapter="napcat", settings=settings(tmp_path,
        WORLD_V2_LIFE_CANDIDATE_REVIEW_ENABLED=True)).state.qq_c2c_host
    semantic = host._semantic_chat
    life = interior["life_source_reviewer"]
    released = asyncio.Event()

    class PendingOwner:
        shutdown_pending_task_count = 1

        async def aclose(self):
            pass

        async def wait_for_shutdown_quiescence(self):
            await released.wait()
            self.shutdown_pending_task_count = 0

    semantic._owned_task_owners += (PendingOwner(),)
    try:
        await semantic.aclose()
        assert not semantic._models_closed
        assert life.store._connection.execute("SELECT 1").fetchone() == (1,)
        assert all(client.close_count == 0 for client in clients)
        released.set()
        await semantic.wait_for_shutdown_quiescence()
        assert life.store.close_count == 1
        assert all(client.close_count == 1 for client in clients)
    finally:
        released.set()
        await host.aclose()
        await host.wait_for_shutdown_quiescence()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", EXPERIMENTAL_PROFILES)
async def test_explicit_review_injections_remain_caller_owned(profile, tmp_path, tracked_review_resources):
    from companion_daemon.world_v2.character_interior.life_source_review import LifeSourceReviewer
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore

    clients, _stores, _interior = tracked_review_resources
    configured = settings(tmp_path, WORLD_V2_VISIBLE_EXPRESSION_PROFILE=profile,
        WORLD_V2_LIFE_CANDIDATE_REVIEW_ENABLED=True)
    def model(name):
        return semantic_module.DeepSeekChatModel(api_key="offline", base_url="https://api.deepseek.com", model=name)
    author, first, second, source, life_model = (model(name) for name in ("author", "reader-a", "reader-b", "source", "life"))
    review = IndependentVisibleReviewer(meaning_models=(first, second), source_model=source)
    store = SQLiteImmutableLifeContentStore(path=str(tmp_path / "caller.sqlite"), world_id="world:caller")
    host = host_module.build_qq_c2c_host(settings=configured, recipient_id="10001", model=author,
        visible_source_review_model=review, life_source_reviewer=LifeSourceReviewer(model=life_model, evidence_store=store))
    try:
        await host.aclose()
        await host.wait_for_shutdown_quiescence()
        assert all(client.close_count == 0 for client in clients)
        assert store.close_count == 0
        assert store._connection.execute("SELECT 1").fetchone() == (1,)
    finally:
        for client in clients:
            await client.aclose()
        store.close()


@pytest.mark.parametrize("profile, conflicting_version", [
    ("experimental_independent_v21", "19"),
    ("experimental_independent_v22", "19"),
    ("experimental_independent_v21", "22"),
    ("experimental_independent_v22", "21"),
])
def test_experimental_profile_rejects_explicit_version_conflict_before_open(tmp_path, profile, conflicting_version):
    with pytest.raises(ValueError, match="conflict"):
        host_module.build_qq_c2c_host(settings=settings(tmp_path,
            WORLD_V2_VISIBLE_EXPRESSION_PROFILE=profile), recipient_id="10001",
            visible_source_review_version=conflicting_version)
    assert not (tmp_path / "world.sqlite").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", EXPERIMENTAL_PROFILES)
async def test_life_store_and_owned_clients_close_when_character_composition_fails(profile, tmp_path, monkeypatch, tracked_review_resources):
    clients, stores, _interior = tracked_review_resources

    def fail(**kwargs):
        raise ValueError("offline composition failure")

    monkeypatch.setattr(semantic_module, "compose_production_character_interior", fail)
    with pytest.raises(ValueError, match="offline composition failure"):
        create_qq_c2c_onebot_app(adapter="napcat", settings=settings(tmp_path,
            WORLD_V2_VISIBLE_EXPRESSION_PROFILE=profile,
            WORLD_V2_LIFE_CANDIDATE_REVIEW_ENABLED=True))
    assert stores and all(store.close_count == 1 for store in stores)
    await asyncio.gather(*semantic_module._FAILED_BUILD_CLEANUPS)
    assert clients and all(client.close_count == 1 for client in clients)


@pytest.mark.asyncio
async def test_provider_close_failure_still_releases_life_evidence(tmp_path, tracked_review_resources):
    clients, _stores, interior = tracked_review_resources
    host = create_qq_c2c_onebot_app(adapter="napcat", settings=settings(tmp_path,
        WORLD_V2_LIFE_CANDIDATE_REVIEW_ENABLED=True)).state.qq_c2c_host
    first = clients[0]
    close_first = first.aclose

    async def failing_close():
        await close_first()
        raise RuntimeError("offline close failure")

    first.aclose = failing_close
    with pytest.raises(RuntimeError, match="offline close failure"):
        await host.aclose()
    assert all(client.close_count == 1 for client in clients)
    assert interior["life_source_reviewer"].store.close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", EXPERIMENTAL_PROFILES)
async def test_partial_independent_provider_construction_closes_earlier_clients(profile, tmp_path, monkeypatch, tracked_review_resources):
    clients, _stores, _interior = tracked_review_resources
    real_model = semantic_module.DeepSeekChatModel

    class FailSecondReader(real_model):
        def __init__(self, *args, **kwargs):
            if len(clients) == 2 and kwargs["model"] == "deepseek-v4-flash":
                raise ValueError("offline second reader failed to initialize")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(semantic_module, "DeepSeekChatModel", FailSecondReader)
    with pytest.raises(ValueError, match="offline second reader"):
        create_qq_c2c_onebot_app(adapter="napcat", settings=settings(tmp_path,
            WORLD_V2_VISIBLE_EXPRESSION_PROFILE=profile))
    assert len(clients) == 2  # Author and first reader were constructed before the failure.
    await asyncio.gather(*semantic_module._FAILED_BUILD_CLEANUPS)
    assert all(client.close_count == 1 and client.client.is_closed for client in clients)


def test_fake_author_cannot_implicitly_open_a_paid_life_reviewer(tmp_path):
    with pytest.raises(ValueError, match="explicit Life candidate reviewer"):
        create_qq_c2c_onebot_app(adapter="napcat", use_fake_model=True,
            settings=settings(tmp_path, WORLD_V2_LIFE_CANDIDATE_REVIEW_ENABLED=True))
    assert not (tmp_path / "world.sqlite").exists()
