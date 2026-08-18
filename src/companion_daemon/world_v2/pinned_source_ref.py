"""Deterministic, fail-closed matching of model-written source refs.

Opaque source identifiers are host-owned.  The character may choose *which*
pinned sources to name, but the strings themselves are not a semantic
decision.  When a written token uniquely identifies one item in this turn's
pinned catalog — exact copy, short id, collapsed duplicate segment, missing
``beat`` slot with a matching trailing index, or a unique hash fragment — the
host restores the canonical ref.  Zero or several catalog hits stay
unresolved.  The host never guesses.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_MIN_HASH_RUN = 8
_FAILURE_DETAIL_LIMIT = 3_800
_CATALOG_LISTING_LIMIT = 24
_REF_DISPLAY_LIMIT = 160


@dataclass(frozen=True, slots=True)
class CiteableSource:
    """One pickable source: a short id plus the canonical pinned ref."""

    token: str
    source_ref: str
    label: str | None = None

    def prompt_item(self) -> dict[str, str]:
        item = {"id": self.token, "ref": self.source_ref}
        if self.label:
            item["label"] = self.label
        return item


@dataclass(frozen=True, slots=True)
class PinnedSourceCatalog:
    """This turn's pinned, citeable sources and the short ids that select them."""

    items: tuple[CiteableSource, ...]
    token_to_ref: Mapping[str, str]
    canonical_refs: frozenset[str]

    @classmethod
    def from_refs(
        cls,
        refs: Iterable[str],
        *,
        labels: Mapping[str, str] | None = None,
        existing_tokens: Mapping[str, str] | None = None,
    ) -> "PinnedSourceCatalog":
        label_map = dict(labels or ())
        items: list[CiteableSource] = []
        token_to_ref: dict[str, str] = {}
        seen: set[str] = set()
        reserved = set(existing_tokens or ())
        reserved.update(ref for ref in refs if isinstance(ref, str) and ref)
        if existing_tokens:
            for token, source_ref in existing_tokens.items():
                if not isinstance(token, str) or not isinstance(source_ref, str):
                    continue
                if not token or not source_ref or source_ref in seen:
                    continue
                items.append(
                    CiteableSource(
                        token=token,
                        source_ref=source_ref,
                        label=label_map.get(source_ref),
                    )
                )
                token_to_ref[token] = source_ref
                seen.add(source_ref)
                reserved.add(token)
                reserved.add(source_ref)
        ordinal = 0
        for source_ref in refs:
            if not isinstance(source_ref, str) or not source_ref or source_ref in seen:
                continue
            while True:
                token = f"s{ordinal}"
                ordinal += 1
                if token not in reserved and token != source_ref:
                    break
            items.append(
                CiteableSource(
                    token=token,
                    source_ref=source_ref,
                    label=label_map.get(source_ref),
                )
            )
            token_to_ref[token] = source_ref
            seen.add(source_ref)
            reserved.add(token)
        return cls(
            items=tuple(items),
            token_to_ref=token_to_ref,
            canonical_refs=frozenset(seen),
        )

    @classmethod
    def for_resolution(
        cls,
        refs: Iterable[str],
        *,
        tokens: Mapping[str, str] | None = None,
    ) -> "PinnedSourceCatalog":
        """Catalog used only to restore written tokens, without minting new ids.

        Inbound already showed ``S1`` / ``T1``.  Minting a parallel ``s0`` here
        would let an unshown id uniquely hit whichever leftover ref sorted
        first, which is guessing.
        """

        token_to_ref = {
            token: source_ref
            for token, source_ref in dict(tokens or ()).items()
            if isinstance(token, str)
            and token
            and isinstance(source_ref, str)
            and source_ref
        }
        canonical = {ref for ref in refs if isinstance(ref, str) and ref}
        canonical.update(token_to_ref.values())
        items = tuple(
            CiteableSource(token=token, source_ref=source_ref)
            for token, source_ref in token_to_ref.items()
        )
        return cls(
            items=items,
            token_to_ref=token_to_ref,
            canonical_refs=frozenset(canonical),
        )

    def extended(self, refs: Iterable[str]) -> "PinnedSourceCatalog":
        """Add more canonical refs without renumbering already issued ids."""

        extra = [ref for ref in refs if isinstance(ref, str) and ref]
        if not extra:
            return self
        labels = {
            item.source_ref: item.label for item in self.items if item.label
        }
        return self.from_refs(
            (*self.canonical_refs, *extra),
            labels=labels,
            existing_tokens=self.token_to_ref,
        )

    def prompt_value(self) -> dict[str, object]:
        return {
            "instruction": (
                "attended_source_refs、decision.source_refs 和 world_claims.source_refs "
                "只写下面的 id（如 s0），或原样抄 ref。"
                "不要手写拼接不透明字符串。点名哪些、引不引，仍由你决定；"
                "宿主只把 id 还原成权威 ref。"
            ),
            "items": [item.prompt_item() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class SourceRefResolution:
    """Result of matching one written token against the pinned catalog."""

    status: Literal["unique", "unknown", "ambiguous"]
    written: str
    source_ref: str | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""


def resolve_pinned_source_ref(
    written: str,
    catalog: PinnedSourceCatalog,
) -> SourceRefResolution:
    """Return a unique catalog hit, or fail closed."""

    if not isinstance(written, str) or not written:
        return SourceRefResolution(
            status="unknown",
            written=written if isinstance(written, str) else "",
            reason="空字符串不是来源。",
        )
    if written in catalog.canonical_refs:
        return SourceRefResolution(
            status="unique", written=written, source_ref=written, reason="exact"
        )
    mapped = catalog.token_to_ref.get(written)
    if mapped is not None:
        return SourceRefResolution(
            status="unique", written=written, source_ref=mapped, reason="token"
        )

    hits: dict[str, str] = {}

    def _note(canonical: str, reason: str) -> None:
        previous = hits.get(canonical)
        if previous is None:
            hits[canonical] = reason

    for collapsed in _collapse_candidates(written):
        if collapsed in catalog.canonical_refs:
            _note(collapsed, "collapsed_duplicate_segment")

    trailing = _trailing_index_key(written)
    if trailing is not None:
        matches = [
            ref
            for ref in catalog.canonical_refs
            if _trailing_index_key(ref) == trailing
        ]
        if len(matches) == 1:
            _note(matches[0], "missing_beat_unique_index")

    hex_runs = _hex_runs(written)
    if hex_runs:
        hex_matches = [
            ref
            for ref in catalog.canonical_refs
            if _hex_runs_covered(hex_runs, ref)
        ]
        if len(hex_matches) == 1:
            _note(hex_matches[0], "unique_hash_fragment")

    collapsed_written = _collapse_all_consecutive(_segments(written))
    written_parts = _segments(collapsed_written)
    if collapsed_written and len(written_parts) >= 4 and len(written) >= 24:
        prefix_matches = [
            ref
            for ref in catalog.canonical_refs
            if _collapse_all_consecutive(_segments(ref)) == collapsed_written
        ]
        if len(prefix_matches) == 1:
            _note(prefix_matches[0], "unique_containment")

    if len(hits) == 1:
        canonical, reason = next(iter(hits.items()))
        return SourceRefResolution(
            status="unique",
            written=written,
            source_ref=canonical,
            reason=reason,
        )
    if len(hits) > 1:
        return SourceRefResolution(
            status="ambiguous",
            written=written,
            candidates=tuple(sorted(hits)),
            reason="形状能对上清单里的多条，宿主不能猜你点的是哪一条。",
        )
    nearby = _nearby_catalog_refs(written, catalog)
    return SourceRefResolution(
        status="unknown",
        written=written,
        candidates=nearby,
        reason=(
            "不在本轮钉住的可用来源清单里，也无法唯一对应到清单中的一条。"
            if not nearby
            else "形状不对，且不能唯一对应到清单中的一条。"
        ),
    )


def resolve_pinned_source_ref_list(
    written: object,
    catalog: PinnedSourceCatalog,
) -> tuple[list[str], tuple[SourceRefResolution, ...]]:
    """Rewrite uniquely resolved refs; leave unresolved items unchanged.

    Duplicate writings that uniquely restore the same canonical ref collapse
    to one occurrence.  That restores identity, it does not choose a source
    she did not name.
    """

    if not isinstance(written, list):
        return [], ()
    resolved: list[str] = []
    seen: set[str] = set()
    failures: list[SourceRefResolution] = []
    for item in written:
        if not isinstance(item, str):
            failures.append(
                SourceRefResolution(
                    status="unknown",
                    written="",
                    reason="来源必须是字符串。",
                )
            )
            continue
        outcome = resolve_pinned_source_ref(item, catalog)
        if outcome.status == "unique" and outcome.source_ref is not None:
            if outcome.source_ref not in seen:
                resolved.append(outcome.source_ref)
                seen.add(outcome.source_ref)
            continue
        failures.append(outcome)
        if item not in seen:
            resolved.append(item)
            seen.add(item)
    return resolved, tuple(failures)


def unpinned_source_failure_detail(
    *,
    code: str,
    field: str,
    failures: Sequence[SourceRefResolution],
    catalog: PinnedSourceCatalog,
) -> str:
    """Chinese, exact, bounded explanation for the same role model's one retry."""

    heading = {
        "attended_source_unpinned": "attended_source_refs 点名了本轮钉不住的来源。",
        "decision_source_unpinned": "decision.source_refs 点名了本轮钉不住的来源。",
        "private_turn_state.unpinned_source": (
            "private_turn_state.attended_source_refs 点名了本轮钉不住的来源。"
        ),
    }.get(code, f"{code}：点名了本轮钉不住的来源。")
    lines = [
        heading,
        f"字段 {field}。宿主不会替你改写或丢掉这些点名；只有能唯一对上清单里一条时才还原形状。",
    ]
    for failure in failures:
        written = _clip(failure.written, _REF_DISPLAY_LIMIT) or "（空）"
        extra = ""
        if failure.candidates:
            shown = "；".join(
                _clip(item, _REF_DISPLAY_LIMIT) for item in failure.candidates[:4]
            )
            extra = f" 相近但无法唯一确定：{shown}"
        lines.append(f"- 「{written}」：{failure.reason}{extra}")
    lines.append("本轮可用来源（写 id 或原样抄 ref；点名哪些仍由你决定）：")
    if not catalog.items:
        lines.append("- （本轮清单是空的，不要编造 ref）")
    else:
        for item in catalog.items[:_CATALOG_LISTING_LIMIT]:
            label = f" {item.label}" if item.label else ""
            lines.append(
                f"- {item.token} → {_clip(item.source_ref, _REF_DISPLAY_LIMIT)}{label}"
            )
        remaining = len(catalog.items) - _CATALOG_LISTING_LIMIT
        if remaining > 0:
            lines.append(f"- …另有 {remaining} 条，见 citeable_sources 全文")
    lines.append("不要手写拼接不透明字符串。")
    detail = "\n".join(lines)
    if len(detail) <= _FAILURE_DETAIL_LIMIT:
        return detail
    return detail[: _FAILURE_DETAIL_LIMIT - 1] + "…"


def _segments(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.split(":") if part != "")


def _collapse_all_consecutive(segments: Sequence[str]) -> str:
    collapsed: list[str] = []
    for part in segments:
        if collapsed and collapsed[-1] == part:
            continue
        collapsed.append(part)
    return ":".join(collapsed)


def _collapse_candidates(written: str) -> tuple[str, ...]:
    segments = list(_segments(written))
    if len(segments) < 2:
        return ()
    generated: list[str] = []
    collapsed_one = list(segments)
    index = 1
    while index < len(collapsed_one):
        if collapsed_one[index] == collapsed_one[index - 1]:
            del collapsed_one[index]
            generated.append(":".join(collapsed_one))
            continue
        index += 1
    all_collapsed = _collapse_all_consecutive(segments)
    if all_collapsed and all_collapsed != written:
        generated.append(all_collapsed)
    return tuple(dict.fromkeys(item for item in generated if item and item != written))


def _trailing_index_key(value: str) -> tuple[tuple[str, ...], str] | None:
    segments = _segments(value)
    if len(segments) < 2 or not segments[-1].isdigit():
        return None
    head = list(segments[:-1])
    if "beat" in head:
        beat_at = head.index("beat")
        head = head[:beat_at]
    if len(head) < 2:
        return None
    return tuple(head), segments[-1]


def _hex_runs(value: str) -> tuple[str, ...]:
    runs: list[str] = []
    for part in _segments(value):
        candidate = part.removeprefix("sha256:")
        if len(candidate) >= _MIN_HASH_RUN and all(char in _HEX_DIGITS for char in candidate):
            runs.append(candidate.lower())
    return tuple(dict.fromkeys(runs))


def _hex_runs_covered(runs: Sequence[str], source_ref: str) -> bool:
    catalog_runs = _hex_runs(source_ref)
    if not catalog_runs:
        return False
    for run in runs:
        if not any(
            catalog.startswith(run) or run.startswith(catalog)
            for catalog in catalog_runs
        ):
            return False
    return True


def _nearby_catalog_refs(
    written: str,
    catalog: PinnedSourceCatalog,
) -> tuple[str, ...]:
    runs = _hex_runs(written)
    if not runs:
        return ()
    nearby = [
        item.source_ref
        for item in catalog.items
        if _hex_runs_covered(runs, item.source_ref)
    ]
    return tuple(nearby[:4])


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


__all__ = [
    "CiteableSource",
    "PinnedSourceCatalog",
    "SourceRefResolution",
    "resolve_pinned_source_ref",
    "resolve_pinned_source_ref_list",
    "unpinned_source_failure_detail",
]
