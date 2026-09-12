"""Retired review lanes stay removed; restored Life reviews stay Life-owned."""

from __future__ import annotations

import ast
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

# General source closure and focused novel-origin review are restored for
# Life Development only. The retired proof/inventory lanes remain forbidden.
_FORBIDDEN = (
    "visible_source_closure_proof_v1",
    "candidate_external_proposition_inventory",
)

_LIFE_REVIEW_PURPOSES = frozenset(
    {
        "life_development_source_closure_review",
        "life_development_novel_origin_review",
    }
)

_DELETED_MODULES = (
    "companion_daemon.world_v2.structured_source_review_model",
    "companion_daemon.world_v2.source_review_authority",
    "companion_daemon.world_v2.visible_source_review_model",
)


def test_production_haystack_has_no_retired_proof_or_inventory_lanes() -> None:
    missing = [path for path in _HAYSTACK if not path.is_file()]
    assert missing == [], f"haystack files missing: {missing}"
    hits: list[str] = []
    for path in _HAYSTACK:
        text = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            if needle in text:
                hits.append(f"{path.relative_to(_ROOT)}:{needle}")
    assert hits == []


@pytest.mark.parametrize("path", _HAYSTACK, ids=lambda path: path.stem)
def test_restored_life_review_purposes_are_owned_by_life_runtime(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    purposes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr if isinstance(node.func, ast.Attribute) else None
        )
        if name != "model_call_scope":
            continue
        purpose = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "purpose"),
            node.args[0] if node.args else None,
        )
        if isinstance(purpose, ast.Constant) and isinstance(purpose.value, str):
            purposes.add(purpose.value)
    expected = (
        _LIFE_REVIEW_PURPOSES
        if path == _SRC / "life_development_runtime.py"
        else frozenset()
    )
    assert purposes & _LIFE_REVIEW_PURPOSES == expected, path.relative_to(_ROOT)


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
