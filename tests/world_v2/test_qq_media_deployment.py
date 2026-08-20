"""The production media factory fails safe and composes only when complete."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)

from companion_daemon.config import Settings
from companion_daemon.world_v2.media_authority_provisioning import MediaAuthorityProvisioner
from companion_daemon.world_v2.production_turn_application import (
    WorldV2TurnApplicationConfig,
)
from companion_daemon.world_v2.qq_c2c_onebot_app import create_qq_c2c_onebot_app
from companion_daemon.world_v2.media_provider_transport import (
    MediaProviderDiagnosticRecorder,
)
from companion_daemon.world_v2.qq_media_deployment import (
    _compose_high_private_lane,
    build_qq_media_preview_deployment,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

NOW = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)
WORLD_ID = "world:qq-media-deployment"


class _Identities:
    def resolve(self, *, platform: str, platform_user_id: str) -> tuple[str, str]:
        return (f"user:{platform_user_id}", "user:user.1")


class _NoModel:
    async def propose(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("factory test does not deliberate")


class _Router:
    async def route(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("factory test does not route")


class _Transport:
    provider = "platform:test"

    async def send(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("factory test does not dispatch")

    async def lookup(self, **_kwargs):  # type: ignore[no-untyped-def]
        return None


async def _provisioned_world(path: Path) -> None:
    app = build_sqlite_world_v2_test_application(
        path=path,
        config=WorldV2TurnApplicationConfig(
            world_id=WORLD_ID,
            companion_actor_ref="agent:companion",
            reply_target="user:user.1",
            action_pump_owner="pump:qq-media-deployment",
        ),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_NoModel(),
        ),
        transport=_Transport(),
        now=NOW,
    )
    try:
        await app.tick(
            tick_id="deployment:1", logical_time_from=NOW,
            logical_time_to=NOW + timedelta(minutes=1),
            observed_at=NOW + timedelta(minutes=1), trace_id="trace:deployment",
            causation_id="cause:deployment", correlation_id="correlation:deployment",
            reason="test",
        )
    finally:
        app.close()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    try:
        MediaAuthorityProvisioner(
            ledger=ledger, signing_key_hex="11" * 32, subject_ref="user:user.1",
        ).ensure()
    finally:
        ledger.close()


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_path": tmp_path / "qq-media-deployment.sqlite",
        "WORLD_V2_MEDIA_PREVIEW_ENABLED": "1",
        "ALLOW_AUTO_IMAGE_GENERATION": "1",
        "DEEPSEEK_API_KEY": "test-deepseek",
        "DEEPSEEK_DEBUG_API_KEY": "test-deepseek-debug",
        "OPENAI_API_KEY": "test-openai",
        "OPENROUTER_API_KEY": None,
        "CIVITAI_API_KEY": None,
        "CIVITAI_KREA2_ENABLED": "0",
        "NAPCAT_ALLOWED_PRIVATE_USER_IDS": "10001",
        "PRIMARY_USER_ID": "geoff",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_factory_disables_without_the_explicit_switch(tmp_path: Path) -> None:
    settings = _settings(tmp_path, WORLD_V2_MEDIA_PREVIEW_ENABLED="0")
    assert build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID) is None


def test_factory_disables_without_credentials(tmp_path: Path) -> None:
    assert (
        build_qq_media_preview_deployment(
            settings=_settings(tmp_path, OPENAI_API_KEY=None), world_id=WORLD_ID
        )
        is None
    )
    assert (
        build_qq_media_preview_deployment(
            settings=_settings(tmp_path, DEEPSEEK_API_KEY=None), world_id=WORLD_ID
        )
        is None
    )


def test_factory_disables_until_enforcement_grants_are_provisioned(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID) is None


@pytest.mark.asyncio
async def test_inspector_hardening_normalizes_list_fields_without_touching_verdicts() -> None:
    import json

    import httpx

    from companion_daemon.world_v2.qq_media_deployment import InspectorHardeningTransport

    content = {
        "passed": False,
        "reason": "subject missing",
        "observed_summary": "a park path",
        "observed_facts": {"environment": "park", "lighting": "golden hour"},
        "deviations": "no subject present",
        "salient_expression_cues": None,
    }

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(content)}}]},
        )

    transport = InspectorHardeningTransport(
        proxy_url=None, inner=httpx.MockTransport(respond)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post("https://provider.test/chat/completions", json={})
    payload = json.loads(response.json()["choices"][0]["message"]["content"])
    assert payload["observed_facts"] == ["environment: park", "lighting: golden hour"]
    assert payload["deviations"] == ["no subject present"]
    # An explicit JSON null would crash the parser's slice; it becomes [].
    assert payload["salient_expression_cues"] == []
    # Verdict material is never rewritten by deployment hardening.
    assert payload["passed"] is False
    assert payload["reason"] == "subject missing"
    await transport.aclose()


def test_media_observation_surface_is_token_gated_and_read_only(tmp_path: Path) -> None:
    app = create_qq_c2c_onebot_app(
        adapter="napcat",
        settings=Settings(
            database_path=tmp_path / "qq-media-endpoints.sqlite",
            NAPCAT_ALLOWED_PRIVATE_USER_IDS="10001",
            DELIVERY_RECONCILIATION_TOKEN="operator-secret",
        ),
        use_fake_model=True,
    )
    with TestClient(app) as client:
        # Missing/wrong token cannot read what she generated or sent.
        assert client.get("/internal/world-v2/media/previews").status_code == 403
        assert (
            client.get(
                "/internal/world-v2/media/previews",
                headers={"X-World-V2-Internal-Token": "wrong"},
            ).status_code
            == 403
        )
        listed = client.get(
            "/internal/world-v2/media/previews",
            headers={"X-World-V2-Internal-Token": "operator-secret"},
        )
        assert listed.status_code == 200
        assert listed.json() == {"previews": []}
        # There is no approval verb anywhere: delivery is the world's own
        # decision, so the old approve/dismiss routes must not exist.
        for verb in ("approve", "dismiss"):
            response = client.post(
                f"/internal/world-v2/media/previews/preview:x/{verb}",
                headers={"X-World-V2-Internal-Token": "operator-secret"},
            )
            assert response.status_code in {404, 405}


def test_media_observation_surface_disabled_without_a_token(tmp_path: Path) -> None:
    app = create_qq_c2c_onebot_app(
        adapter="napcat",
        settings=Settings(
            database_path=tmp_path / "qq-media-endpoints-disabled.sqlite",
            NAPCAT_ALLOWED_PRIVATE_USER_IDS="10001",
            DELIVERY_RECONCILIATION_TOKEN=None,
        ),
        use_fake_model=True,
    )
    with TestClient(app) as client:
        response = client.get("/internal/world-v2/media/previews")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_factory_composes_a_complete_preview_deployment_when_provisioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORLD_V2_ENABLE_INSECURE_TEST_ROOT", "1")
    settings = _settings(tmp_path)
    await _provisioned_world(Path(settings.database_path))
    bundle = build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID)
    assert bundle is not None
    try:
        deployment = bundle.deployment
        assert not hasattr(deployment, "selection_model")
        assert deployment.continuation is not None
        assert deployment.acceptance.grant.grant_id == "grant:world-v2:media-planning"
        assert deployment.continuation.render_grant.grant_id == "grant:world-v2:media-render"
        assert (
            deployment.continuation.inspection_grant.grant_id
            == "grant:world-v2:media-inspection"
        )
        # Zero render/inspection reservations keep the lane free of the paid
        # CostProfile requirement while budgets still bootstrap.
        assert deployment.continuation.render_amount_limit == 0
        assert deployment.continuation.inspection_amount_limit == 0
        # World-owned delivery with conservative operational guardrails; the
        # decision authority is a system policy ref, never a human operator.
        assert deployment.auto_delivery is not None
        assert deployment.auto_delivery.delivery_target_ref == "conversation:qq:c2c:10001"
        assert deployment.auto_delivery.recipient_ref == "user:geoff"
        assert deployment.auto_delivery.policy_actor.startswith("system:")
        assert deployment.auto_delivery.max_deliveries_per_day <= 2
        assert bundle.transport.provider == "provider:event-media"
        assert hasattr(bundle.transport, "lookup_execution_result")
        renderer = bundle.transport._renderer
        assert renderer.specialized_generators == {}
        assert renderer.private_prompt_author is None
        inner = _unwrap(renderer.generator)
        assert inner.spend_store is not None
        assert Path(inner.spend_store.path) == Path(settings.database_path)
        high_plan = SimpleNamespace(
            private_render_contract=SimpleNamespace(render_route="adult_suggestive"),
            suggestive_private_contract=None,
        )
        ordinary_plan = SimpleNamespace(
            private_render_contract=None,
            suggestive_private_contract=None,
        )
        assert renderer._generator_for(high_plan) is None
        assert renderer._generator_for(ordinary_plan) is renderer.generator
    finally:
        bundle.transport.close()


def _unwrap(obj: object) -> object:
    while hasattr(obj, "_delegate"):
        obj = obj._delegate
    return obj


def _high_plan(*, route: str = "adult_suggestive") -> SimpleNamespace:
    return SimpleNamespace(
        private_render_contract=SimpleNamespace(render_route=route),
        suggestive_private_contract=None,
    )


def _ordinary_plan() -> SimpleNamespace:
    return SimpleNamespace(
        private_render_contract=None,
        suggestive_private_contract=None,
    )


@pytest.mark.asyncio
async def test_factory_keeps_ordinary_lane_when_civitai_key_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("WORLD_V2_ENABLE_INSECURE_TEST_ROOT", "1")
    settings = _settings(tmp_path, CIVITAI_KREA2_ENABLED="1", CIVITAI_API_KEY=None)
    await _provisioned_world(Path(settings.database_path))
    with caplog.at_level(logging.WARNING, logger="companion_daemon.world_v2.qq_media_deployment"):
        bundle = build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID)
    assert bundle is not None
    try:
        renderer = bundle.transport._renderer
        assert renderer.specialized_generators == {}
        assert renderer.private_prompt_author is None
        assert renderer._generator_for(_high_plan()) is None
        assert renderer._generator_for(_ordinary_plan()) is renderer.generator
        assert "missing: CIVITAI_API_KEY" in caplog.text
        assert "high-private P3 fail-closed" in caplog.text
    finally:
        bundle.transport.close()


@pytest.mark.asyncio
async def test_factory_keeps_ordinary_lane_when_krea2_template_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("WORLD_V2_ENABLE_INSECURE_TEST_ROOT", "1")
    settings = _settings(
        tmp_path,
        CIVITAI_KREA2_ENABLED="1",
        CIVITAI_API_KEY="test-civitai",
        CIVITAI_KREA2_TEMPLATE_PATH=tmp_path / "missing-krea2-template.json",
    )
    await _provisioned_world(Path(settings.database_path))
    with caplog.at_level(logging.WARNING, logger="companion_daemon.world_v2.qq_media_deployment"):
        bundle = build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID)
    assert bundle is not None
    try:
        renderer = bundle.transport._renderer
        assert renderer.specialized_generators == {}
        assert renderer.private_prompt_author is None
        assert renderer._generator_for(_ordinary_plan()) is renderer.generator
        assert "missing: CIVITAI_KREA2_TEMPLATE_PATH" in caplog.text
        assert "high-private P3 fail-closed" in caplog.text
    finally:
        bundle.transport.close()


@pytest.mark.asyncio
async def test_factory_installs_krea2_high_private_when_credentials_are_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from companion_daemon.event_media import FirstPersonPrivatePromptAuthor, MediaRenderFailure
    from companion_daemon.image_generation import (
        CivitaiTemplateWorkflowImageGenerator,
        OpenAIImageGenerator,
    )
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.sourced_life_media import SourcedLifeMediaRenderer

    monkeypatch.setenv("WORLD_V2_ENABLE_INSECURE_TEST_ROOT", "1")
    settings = _settings(
        tmp_path,
        CIVITAI_KREA2_ENABLED="1",
        CIVITAI_API_KEY="test-civitai",
    )
    await _provisioned_world(Path(settings.database_path))
    with caplog.at_level(logging.WARNING, logger="companion_daemon.world_v2.qq_media_deployment"):
        bundle = build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID)
    assert bundle is not None
    try:
        renderer = bundle.transport._renderer
        assert isinstance(renderer, SourcedLifeMediaRenderer)
        assert set(renderer.specialized_generators) == {
            "adult_suggestive",
            "adult_explicit",
        }
        suggestive = renderer.specialized_generators["adult_suggestive"]
        explicit = renderer.specialized_generators["adult_explicit"]
        assert suggestive is explicit
        assert isinstance(_unwrap(suggestive), CivitaiTemplateWorkflowImageGenerator)
        assert isinstance(_unwrap(renderer.generator), OpenAIImageGenerator)
        assert Path(_unwrap(suggestive).spend_store.path) == Path(settings.database_path)
        assert Path(_unwrap(renderer.generator).spend_store.path) == Path(settings.database_path)
        assert renderer._generator_for(_high_plan()) is suggestive
        assert renderer._generator_for(_high_plan(route="adult_explicit")) is explicit
        assert renderer._generator_for(_ordinary_plan()) is renderer.generator
        assert renderer._generator_for(_ordinary_plan()) is not suggestive
        assert isinstance(renderer.private_prompt_author, FirstPersonPrivatePromptAuthor)
        assert isinstance(renderer.private_prompt_author.model, DeepSeekChatModel)
        assert renderer.private_prompt_author.model.provider == "deepseek"
        assert "falling back to deepseek:" in caplog.text
        assert "HERMES_PRIVATE_PROMPT_ENABLED but OPENROUTER_API_KEY is missing" in caplog.text
        assert "high-private P3 krea2 installed, private prompt author deepseek:" in caplog.text
        assert "Hermes fallback; OPENROUTER_API_KEY missing" in caplog.text
        unsourced = await renderer.render(
            SimpleNamespace(
                plan_id="plan:unsourced-high",
                event_id="",
                primary_evidence_ref="/activity/description",
                evidence_values={"/activity/description": "雨后校园小路"},
                private_render_contract=SimpleNamespace(render_route="adult_suggestive"),
            )
        )
        assert isinstance(unsourced, MediaRenderFailure)
        assert unsourced.reason == "unsourced_event"
        assert unsourced.attempts == 0
    finally:
        bundle.transport.close()


@pytest.mark.asyncio
async def test_factory_uses_hermes_private_prompt_author_when_openrouter_is_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from companion_daemon.event_media import FirstPersonPrivatePromptAuthor
    from companion_daemon.llm import OpenAICompatibleChatModel

    monkeypatch.setenv("WORLD_V2_ENABLE_INSECURE_TEST_ROOT", "1")
    settings = _settings(
        tmp_path,
        CIVITAI_KREA2_ENABLED="1",
        CIVITAI_API_KEY="test-civitai",
        OPENROUTER_API_KEY="test-openrouter",
    )
    await _provisioned_world(Path(settings.database_path))
    with caplog.at_level(logging.WARNING, logger="companion_daemon.world_v2.qq_media_deployment"):
        bundle = build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID)
    assert bundle is not None
    try:
        author = bundle.transport._renderer.private_prompt_author
        assert isinstance(author, FirstPersonPrivatePromptAuthor)
        assert isinstance(author.model, OpenAICompatibleChatModel)
        assert author.model.model == "nousresearch/hermes-4-70b"
        assert author.model.provider == "openrouter"
        assert author.model.base_url == "https://openrouter.ai/api/v1"
        assert "private prompt author installed" in caplog.text
        assert "openrouter:nousresearch/hermes-4-70b via hermes_openrouter" in caplog.text
        assert "falling back" not in caplog.text
        assert (
            "high-private P3 krea2 installed, private prompt author "
            "openrouter:nousresearch/hermes-4-70b"
        ) in caplog.text
    finally:
        bundle.transport.close()


@pytest.mark.asyncio
async def test_factory_uses_explicit_deepseek_when_hermes_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from companion_daemon.event_media import FirstPersonPrivatePromptAuthor
    from companion_daemon.llm import DeepSeekChatModel

    monkeypatch.setenv("WORLD_V2_ENABLE_INSECURE_TEST_ROOT", "1")
    settings = _settings(
        tmp_path,
        CIVITAI_KREA2_ENABLED="1",
        CIVITAI_API_KEY="test-civitai",
        HERMES_PRIVATE_PROMPT_ENABLED=False,
    )
    await _provisioned_world(Path(settings.database_path))
    with caplog.at_level(logging.WARNING, logger="companion_daemon.world_v2.qq_media_deployment"):
        bundle = build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID)
    assert bundle is not None
    try:
        author = bundle.transport._renderer.private_prompt_author
        assert isinstance(author, FirstPersonPrivatePromptAuthor)
        assert isinstance(author.model, DeepSeekChatModel)
        assert "falling back" not in caplog.text
        assert "OPENROUTER_API_KEY is missing" not in caplog.text
        assert "via deepseek" in caplog.text
        assert "Hermes fallback" not in caplog.text
    finally:
        bundle.transport.close()


def test_compose_fail_closes_high_private_when_author_credentials_are_missing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(
        tmp_path,
        CIVITAI_KREA2_ENABLED="1",
        CIVITAI_API_KEY="test-civitai",
        DEEPSEEK_API_KEY=None,
        DEEPSEEK_DEBUG_API_KEY=None,
        OPENROUTER_API_KEY=None,
    )
    with caplog.at_level(logging.WARNING, logger="companion_daemon.world_v2.qq_media_deployment"):
        composed = _compose_high_private_lane(
            settings,
            diagnostic_recorder=MediaProviderDiagnosticRecorder(),
            world_id=WORLD_ID,
        )
    assert composed is None
    assert "missing: OPENROUTER_API_KEY, DEEPSEEK_API_KEY" in caplog.text
    assert "private prompt author model" not in caplog.text


def test_compose_logs_when_krea2_is_disabled(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(tmp_path, CIVITAI_KREA2_ENABLED="0", CIVITAI_API_KEY="test-civitai")
    with caplog.at_level(logging.WARNING, logger="companion_daemon.world_v2.qq_media_deployment"):
        composed = _compose_high_private_lane(
            settings,
            diagnostic_recorder=MediaProviderDiagnosticRecorder(),
            world_id=WORLD_ID,
        )
    assert composed is None
    assert "disabled: CIVITAI_KREA2_ENABLED" in caplog.text
