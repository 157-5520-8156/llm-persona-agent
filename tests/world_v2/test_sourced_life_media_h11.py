from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from companion_daemon.config import Settings
from companion_daemon.world_v2.qq_media_deployment import build_qq_media_preview_deployment
from companion_daemon.world_v2.sourced_life_media import (
    SourcedLifeMediaInspector,
    SourcedLifeMediaRenderer,
    sourced_life_closure_error,
)
from test_qq_media_deployment import WORLD_ID, _provisioned_world, _settings


def test_media_lane_defaults_are_on() -> None:
    assert Settings.model_fields["world_v2_media_preview_enabled"].default is True
    assert Settings.model_fields["allow_auto_image_generation"].default is True


def test_factory_disables_without_auto_image_generation(tmp_path: Path) -> None:
    settings = _settings(tmp_path, ALLOW_AUTO_IMAGE_GENERATION="0")
    assert build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID) is None


def test_factory_still_disables_without_credentials_when_switches_default_on(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, OPENAI_API_KEY=None)
    assert build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID) is None


def test_unsourced_or_unlived_plans_fail_closed() -> None:
    assert sourced_life_closure_error(SimpleNamespace(event_id="", primary_evidence_ref="/activity/description", evidence_values={"/activity/description": "雨后"})) == "unsourced_event"
    assert sourced_life_closure_error(SimpleNamespace(event_id="event:walk", primary_evidence_ref="/legacy/action", evidence_values={"/legacy/action": "pose"})) == "unsourced_evidence"
    assert sourced_life_closure_error(SimpleNamespace(event_id="event:walk", primary_evidence_ref="/activity/description", evidence_values={})) == "unsourced_evidence"
    assert sourced_life_closure_error(SimpleNamespace(event_id="event:walk", primary_evidence_ref="/activity/description", evidence_values={"/activity/description": "  "})) == "unsourced_evidence"
    assert sourced_life_closure_error(SimpleNamespace(event_id="event:walk", primary_evidence_ref="/activity/description", evidence_values={"/activity/description": "雨后校园小路"})) is None


@pytest.mark.asyncio
async def test_inspector_fails_closed_when_the_image_is_missing(tmp_path: Path) -> None:
    plan = SimpleNamespace(
        event_id="event:walk",
        primary_evidence_ref="/activity/description",
        evidence_values={"/activity/description": "雨后校园小路"},
    )
    inspection = await SourcedLifeMediaInspector().inspect(
        tmp_path / "missing.png", plan=plan, prompt="unused"
    )
    assert inspection.passed is False
    assert inspection.reason == "missing_image"
    assert inspection.inspector_model == "deterministic:sourced-life"


@pytest.mark.asyncio
async def test_inspector_accepts_a_sourced_existing_image_without_a_vision_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("sourced-life inspection must not call a provider")

    monkeypatch.setattr(httpx.AsyncClient, "post", _blocked)
    path = tmp_path / "walk.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    plan = SimpleNamespace(
        event_id="event:walk",
        primary_evidence_ref="/activity/description",
        evidence_values={"/activity/description": "雨后校园小路"},
    )
    inspection = await SourcedLifeMediaInspector().inspect(path, plan=plan, prompt="unused")
    assert inspection.passed is True
    assert inspection.reason == "sourced_life"
    assert inspection.inspector_model == "deterministic:sourced-life"
    assert inspection.observed_summary == "雨后校园小路"


@pytest.mark.asyncio
async def test_renderer_does_not_generate_when_the_plan_is_unsourced(tmp_path: Path) -> None:
    from companion_daemon.event_media import MediaRenderFailure

    class _Generator:
        async def generate(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("unsourced plans must not call the image provider")

    result = await SourcedLifeMediaRenderer(
        generator=_Generator(),
        inspector=SourcedLifeMediaInspector(),
        output_dir=tmp_path,
    ).render(
        SimpleNamespace(
            plan_id="plan:unsourced",
            event_id="",
            primary_evidence_ref="/legacy/action",
            evidence_values={"/legacy/action": "pose"},
        )
    )
    assert isinstance(result, MediaRenderFailure)
    assert result.reason == "unsourced_event"
    assert result.attempts == 0


@pytest.mark.asyncio
async def test_provisioned_factory_installs_sourced_life_inspection_not_openai_vision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from companion_daemon import event_media

    monkeypatch.setenv("WORLD_V2_ENABLE_INSECURE_TEST_ROOT", "1")
    settings = _settings(tmp_path, ALLOW_AUTO_IMAGE_GENERATION="1")
    await _provisioned_world(Path(settings.database_path))
    bundle = build_qq_media_preview_deployment(settings=settings, world_id=WORLD_ID)
    assert bundle is not None
    try:
        inspector = bundle.transport._renderer.inspector
        while hasattr(inspector, "_delegate"):
            inspector = inspector._delegate
        assert isinstance(bundle.transport._renderer, SourcedLifeMediaRenderer)
        assert isinstance(inspector, SourcedLifeMediaInspector)
        assert not isinstance(inspector, event_media.OpenAIMediaInspector)
    finally:
        bundle.transport.close()
