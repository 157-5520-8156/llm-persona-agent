"""Shared pytest defaults for the repository."""

from __future__ import annotations

import pytest

DEBUG_DEEPSEEK_API_KEY = "pytest-deepseek-debug-key"


@pytest.fixture(autouse=True)
def _default_debug_deepseek_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", DEBUG_DEEPSEEK_API_KEY)
    monkeypatch.setenv("WORLD_V2_BACKGROUND_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("WORLD_V2_VISIBLE_EXPRESSION_PROFILE", "compact")
    monkeypatch.setenv("WORLD_V2_EXPRESSION_EPISODE_MODE", "stream")
    monkeypatch.setenv("WORLD_V2_ORDINARY_TEXT_REVIEW_MODE", "blocking")
    monkeypatch.setenv("WORLD_V2_VISIBLE_SOURCE_REVIEW_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("WORLD_V2_SELECTIVE_SOURCE_REVIEW_MODEL", "deepseek-v4-flash")
