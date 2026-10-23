"""Life review topology switches.

Default (2026-10-21): ``structural`` -- the review lifecycle stays, but no model
judges Life prose. ``model`` keeps the earlier semantic topology, where Life
Development fails closed without a reviewer and World Author self-review is the
operator's explicit 2026-08-14 trade.
"""

from __future__ import annotations

import json

import pytest

from companion_daemon.config import Settings
from companion_daemon.llm import FakeCompanionModel
from companion_daemon.world_v2.life_structural_review import StructuralLifeReviewModel
from companion_daemon.world_v2.semantic_chat_composition import (
    build_semantic_chat_composition,
)


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        DEEPSEEK_API_KEY=None,
        OPENAI_API_KEY=None,
        **overrides,
    )


def _composition(settings: Settings):  # type: ignore[no-untyped-def]
    return build_semantic_chat_composition(
        settings=settings,
        flash_model=FakeCompanionModel(),
        world_support_model=FakeCompanionModel(),
        model_id_prefix="test",
    )


def test_structural_review_is_the_default_and_calls_no_provider() -> None:
    composition = _composition(_settings())

    assert isinstance(composition.life_source_closure_model, StructuralLifeReviewModel)
    assert composition.life_source_closure_model is not composition.world_support_model
    assert composition.life_source_runtime_isolation == "structural"
    health = composition.life_source_authority_health()
    assert health["status"] == "structural"
    assert health["warning"] is False


@pytest.mark.asyncio
async def test_default_composition_keeps_lean_author_without_life_semantic_reviewer(tmp_path, monkeypatch):
    from companion_daemon.world_v2.present_prompt import LEAN_PRESENTATION_MARKER
    from test_life_source_view import _prepared

    _, request, _ = await _prepared(tmp_path, monkeypatch)
    settings = _settings()
    assert settings.world_v2_life_candidate_review_enabled is False
    composition = _composition(settings)
    try:
        faculty = composition.character_interior._registry.for_purpose("world_stimulus_appraisal")
        assert not faculty.requires_life_source_review
        assert faculty._host_clerical_binding
        messages, _tool, _bindings = faculty._prepare_request(request, contract=faculty._resolve_contract(request))
        assert json.loads(messages[-1]["content"])["presentation"] == LEAN_PRESENTATION_MARKER
        assert isinstance(composition.life_source_closure_model, StructuralLifeReviewModel)
    finally:
        await composition.aclose()


def test_structural_review_respects_the_life_review_master_switch() -> None:
    composition = _composition(_settings(WORLD_V2_LIFE_SOURCE_REVIEW_ENABLED=False))

    assert composition.life_source_closure_model is None
    assert composition.life_source_runtime_isolation == "unavailable"


def test_structural_review_does_not_replace_an_injected_reviewer() -> None:
    independent = FakeCompanionModel()
    composition = build_semantic_chat_composition(
        settings=_settings(),
        flash_model=FakeCompanionModel(),
        world_support_model=FakeCompanionModel(),
        life_source_closure_model=independent,
        model_id_prefix="test",
    )

    assert composition.life_source_closure_model is independent


def test_self_review_is_off_by_default() -> None:
    composition = _composition(_settings(
        WORLD_V2_LIFE_SELF_REVIEW_ALLOWED=False, WORLD_V2_LIFE_REVIEW_MODE="model",
    ))

    assert composition.life_source_closure_model is None
    assert composition.life_source_runtime_isolation == "unavailable"


def test_operator_approved_self_review_installs_the_world_author() -> None:
    composition = _composition(
        _settings(WORLD_V2_LIFE_SELF_REVIEW_ALLOWED=True, WORLD_V2_LIFE_REVIEW_MODE="model"),
    )

    assert composition.life_source_closure_model is composition.world_support_model
    assert composition.life_source_runtime_isolation == "self_review_operator_approved"


def test_self_review_still_respects_the_life_review_master_switch() -> None:
    composition = _composition(
        _settings(
            WORLD_V2_LIFE_SELF_REVIEW_ALLOWED=True,
            WORLD_V2_LIFE_SOURCE_REVIEW_ENABLED=False,
        ),
    )

    assert composition.life_source_closure_model is None


def test_an_injected_independent_reviewer_still_wins() -> None:
    independent = FakeCompanionModel()
    composition = build_semantic_chat_composition(
        settings=_settings(WORLD_V2_LIFE_SELF_REVIEW_ALLOWED=True),
        flash_model=FakeCompanionModel(),
        world_support_model=FakeCompanionModel(),
        life_source_closure_model=independent,
        model_id_prefix="test",
    )

    assert composition.life_source_closure_model is independent
    assert composition.life_source_runtime_isolation == "independent"
