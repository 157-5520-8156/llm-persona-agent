"""Reviewed inbound QQ face render labels.

The catalog maps a provider face identifier onto the platform's picker /
slash-command name (and an optional glyph).  That is a public, auditable
property of the identifier — equivalent to describing what an inbound image
looks like — not a host reading of mood or intent.

Unknown IDs stay unmatched.  This table is not the outbound sticker allowlist.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Mapping

import yaml
from pydantic import Field, model_validator

from .schema_core import FrozenModel

DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[3] / "configs" / "qq_face_render_catalog.yaml"
)
FACE_REF_PREFIX = "qq-face:"
CATALOG_ID = "qq-face-render.1"

INBOUND_SURFACE_PROMPT_CLAUSE = (
    " The current trigger is a non-text inbound observation. "
    "inbound_surfaces.platform_render_name and platform_render_glyph are that "
    "identifier's platform catalog labels — what QQ renders — from a reviewed "
    "catalog or the provider's own sticker summary. They are not a host "
    "translation of his mood, intent, or what the face 'means'. Identifiers "
    "with no catalog match stay as the original provider ref; the host does "
    "not guess a name."
)

INBOUND_SURFACE_PROMPT_CLAUSE_ZH = (
    " 如果当前 trigger 没有正文、只有 reaction_refs / sticker_refs："
    "inbound_surfaces.platform_render_name 和 platform_render_glyph 是该标识在 "
    "QQ 上的渲染名称（平台公开目录名，或表情包自带的 summary），"
    "不是宿主对他情绪或意图的翻译。目录没有的标识只保留原始编号，不猜名字。"
)

_FORBIDDEN_ANNOTATION_MARKERS = (
    "通常表示",
    "他很开心",
    "他在示好",
    "他表示赞同",
    "host translation of mood",
)


class FaceRenderEntry(FrozenModel):
    """One reviewed platform render label for a QQ face id."""

    face_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=32)
    glyph: str | None = Field(default=None, min_length=1, max_length=8)
    family: Literal["system_face", "unicode_emoji"]
    sources: tuple[str, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def name_is_a_label_not_an_annotation(self) -> "FaceRenderEntry":
        blob = f"{self.name}{self.glyph or ''}"
        if any(marker in blob for marker in _FORBIDDEN_ANNOTATION_MARKERS):
            raise ValueError("face render catalog must not carry mood annotations")
        return self


class InboundSurfaceFact(FrozenModel):
    """Platform render appearance of one inbound provider identifier.

    ``platform_render_name`` is the catalog or provider label.  It is never a
    host guess about why he sent it.
    """

    provider_ref: str = Field(min_length=1, max_length=512)
    kind: Literal["qq_system_face", "qq_unicode_emoji", "qq_market_sticker"]
    epistemic_status: Literal[
        "platform_render_label_not_mood_or_intent",
        "unmatched_provider_ref_no_guessed_name",
    ]
    platform_render_name: str | None = Field(
        default=None, min_length=1, max_length=80, exclude_if=lambda value: value is None
    )
    platform_render_glyph: str | None = Field(
        default=None, min_length=1, max_length=8, exclude_if=lambda value: value is None
    )
    catalog_id: str | None = Field(
        default=None, min_length=1, max_length=64, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def matched_surfaces_carry_a_name(self) -> "InboundSurfaceFact":
        matched = self.epistemic_status == "platform_render_label_not_mood_or_intent"
        if matched and self.platform_render_name is None:
            raise ValueError("matched inbound surface needs a platform render name")
        if not matched and (
            self.platform_render_name is not None or self.platform_render_glyph is not None
        ):
            raise ValueError("unmatched inbound surface must not invent a name")
        if matched and self.kind != "qq_market_sticker" and self.catalog_id is None:
            raise ValueError("catalog face surface needs a catalog id")
        return self


def _parse_entries(raw_faces: object) -> dict[str, FaceRenderEntry]:
    if not isinstance(raw_faces, list) or not raw_faces:
        raise ValueError("qq face render catalog needs a faces list")
    entries: dict[str, FaceRenderEntry] = {}
    for item in raw_faces:
        if not isinstance(item, Mapping):
            raise ValueError("qq face render catalog face row is invalid")
        face_id = str(item.get("id") or "").strip()
        sources = item.get("sources")
        if not face_id or not isinstance(sources, list):
            raise ValueError("qq face render catalog face row is missing id or sources")
        raw_name = item.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError(f"qq face render catalog name is invalid for id {face_id}")
        glyph = item.get("glyph")
        family_raw = item.get("family")
        if family_raw not in {"system_face", "unicode_emoji"}:
            raise ValueError(f"qq face render catalog family is invalid for id {face_id}")
        entry = FaceRenderEntry(
            face_id=face_id,
            name=raw_name.strip(),
            glyph=str(glyph).strip() if isinstance(glyph, str) and glyph.strip() else None,
            family=family_raw,
            sources=tuple(str(source) for source in sources),
        )
        if entry.face_id in entries:
            raise ValueError(f"qq face render catalog has a duplicate id: {entry.face_id}")
        entries[entry.face_id] = entry
    return entries


@lru_cache(maxsize=1)
def load_face_render_catalog(
    path: str | None = None,
) -> tuple[str, dict[str, FaceRenderEntry]]:
    """Load the reviewed catalog once. Missing or malformed files fail closed."""

    catalog_path = Path(path) if path else DEFAULT_CATALOG_PATH
    if not catalog_path.is_file():
        raise RuntimeError(f"qq face render catalog is missing: {catalog_path}")
    raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("qq face render catalog root must be a mapping")
    catalog_id = str(raw.get("catalog_id") or "").strip()
    if catalog_id != CATALOG_ID:
        raise ValueError("qq face render catalog_id is not the installed contract")
    entries = _parse_entries(raw.get("faces"))
    return catalog_id, entries


def lookup_face_render(provider_ref: str) -> FaceRenderEntry | None:
    """Return the catalog row for ``qq-face:{id}``, or None. Never approximate."""

    if not provider_ref.startswith(FACE_REF_PREFIX):
        return None
    face_id = provider_ref[len(FACE_REF_PREFIX) :]
    if not face_id:
        return None
    _catalog_id, entries = load_face_render_catalog()
    return entries.get(face_id)


def _face_kind(family: str) -> Literal["qq_system_face", "qq_unicode_emoji"]:
    if family == "unicode_emoji":
        return "qq_unicode_emoji"
    return "qq_system_face"


def compile_inbound_surfaces(
    *,
    reaction_refs: tuple[str, ...],
    sticker_refs: tuple[str, ...] = (),
    sticker_provider_labels: tuple[str | None, ...] = (),
) -> tuple[InboundSurfaceFact, ...]:
    """Attach catalog/provider labels to opaque refs. Unmatched refs stay honest."""

    catalog_id, _entries = load_face_render_catalog()
    surfaces: list[InboundSurfaceFact] = []
    for ref in reaction_refs:
        entry = lookup_face_render(ref)
        if entry is None:
            surfaces.append(
                InboundSurfaceFact(
                    provider_ref=ref,
                    kind="qq_system_face",
                    epistemic_status="unmatched_provider_ref_no_guessed_name",
                )
            )
            continue
        surfaces.append(
            InboundSurfaceFact(
                provider_ref=ref,
                kind=_face_kind(entry.family),
                epistemic_status="platform_render_label_not_mood_or_intent",
                platform_render_name=entry.name,
                platform_render_glyph=entry.glyph,
                catalog_id=catalog_id,
            )
        )
    for index, ref in enumerate(sticker_refs):
        label = (
            sticker_provider_labels[index]
            if index < len(sticker_provider_labels)
            else None
        )
        if isinstance(label, str) and label.strip():
            surfaces.append(
                InboundSurfaceFact(
                    provider_ref=ref,
                    kind="qq_market_sticker",
                    epistemic_status="platform_render_label_not_mood_or_intent",
                    platform_render_name=label.strip()[:80],
                )
            )
        else:
            surfaces.append(
                InboundSurfaceFact(
                    provider_ref=ref,
                    kind="qq_market_sticker",
                    epistemic_status="unmatched_provider_ref_no_guessed_name",
                )
            )
        if len(surfaces) >= 16:
            break
    return tuple(surfaces[:16])


__all__ = [
    "CATALOG_ID",
    "DEFAULT_CATALOG_PATH",
    "FACE_REF_PREFIX",
    "FaceRenderEntry",
    "INBOUND_SURFACE_PROMPT_CLAUSE",
    "INBOUND_SURFACE_PROMPT_CLAUSE_ZH",
    "InboundSurfaceFact",
    "compile_inbound_surfaces",
    "load_face_render_catalog",
    "lookup_face_render",
]
