"""H1d: model-bearing review lanes are gone. One-shot is the default."""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src" / "companion_daemon" / "world_v2"

_HAYSTACK = (
    _SRC / "life_development_runtime.py",
    _SRC / "semantic_chat_composition.py",
    _SRC / "character_interior" / "inbound_wire.py",
    _SRC / "character_interior" / "inbound_author.py",
    _SRC / "character_interior" / "production.py",
    _SRC / "proactive_action.py",
)

# ``life_development_novel_origin_review`` is intentionally live again: the
# optional focused World Author critic (prose / user-channel boundary). H1d
# still forbids the deleted one-shot closure/proof lanes below.
_FORBIDDEN = (
    "life_development_source_closure_review",
    "visible_source_closure_proof_v1",
    "candidate_external_proposition_inventory",
)

_DELETED_MODULES = (
    "companion_daemon.world_v2.structured_source_review_model",
    "companion_daemon.world_v2.source_review_authority",
    "companion_daemon.world_v2.visible_source_review_model",
)


def test_production_haystack_has_no_model_review_purposes() -> None:
    missing = [path for path in _HAYSTACK if not path.is_file()]
    assert missing == [], f"haystack files missing: {missing}"
    hits: list[str] = []
    for path in _HAYSTACK:
        text = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            if needle in text:
                hits.append(f"{path.relative_to(_ROOT)}:{needle}")
    assert hits == []


def test_llm_review_modules_are_deleted() -> None:
    for module_name in _DELETED_MODULES:
        with pytest.raises(ModuleNotFoundError):
            __import__(module_name)


def test_semantic_chat_does_not_construct_source_review_authority() -> None:
    text = (_SRC / "semantic_chat_composition.py").read_text(encoding="utf-8")
    assert "SourceReviewAuthority(" not in text


def test_life_review_identity_is_kept() -> None:
    path = _SRC / "life_review_identity.py"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "SOURCE_REVIEW_SUBJECT_CONTRACT" in text
    assert "current_source_review_subject_hash" in text
