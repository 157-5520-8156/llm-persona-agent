"""Life source review must be present, and weakening it must be explicit.

Life Development fails closed without a source reviewer, but the composition
used to discard the reviewer argument outright, which left the whole event
machine dark. Letting the World Author audit its own draft is the operator's
2026-08-14 trade; it stays off by default so the weaker boundary is visible.
"""

from __future__ import annotations

from companion_daemon.config import Settings
from companion_daemon.llm import FakeCompanionModel
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


def test_self_review_is_off_by_default() -> None:
    composition = _composition(_settings(WORLD_V2_LIFE_SELF_REVIEW_ALLOWED=False))

    assert composition.life_source_closure_model is None
    assert composition.life_source_runtime_isolation == "unavailable"


def test_operator_approved_self_review_installs_the_world_author() -> None:
    composition = _composition(
        _settings(WORLD_V2_LIFE_SELF_REVIEW_ALLOWED=True),
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
