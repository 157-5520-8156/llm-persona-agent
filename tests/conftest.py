"""Shared pytest defaults for the repository."""

from __future__ import annotations

import pytest

DEBUG_DEEPSEEK_API_KEY = "pytest-deepseek-debug-key"


@pytest.fixture(autouse=True)
def _default_debug_deepseek_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", DEBUG_DEEPSEEK_API_KEY)
