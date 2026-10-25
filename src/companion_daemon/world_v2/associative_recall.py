"""Replay-stable lexical prefetch over already source-bound Context candidates.

This is the first, deliberately narrow Recall Index adapter.  It never reads
the ledger, invents semantic labels, or recommends behavior.  Its input has
already passed cursor, privacy, supersession, and source-proof checks; it only
selects a small evidence set whose surface form is reactivated by the current
message.  Dense, temporal-query, and structured-link adapters can later replace
or join it behind the same compiler seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import re
import unicodedata

from .lexical_feature_cache import lexical_feature_cache


ASSOCIATIVE_PREFETCH_POLICY_VERSION = "associative-prefetch.lexical-ngram.1"


@dataclass(frozen=True, slots=True)
class AssociativeRecallCandidate:
    """One source-bound Context item eligible for non-authoritative prefetch."""

    slice_name: str
    item_ref: str
    texts: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.slice_name not in {
            "open_threads",
            "relevant_facts",
            "active_memory_candidates",
        }:
            raise ValueError("associative recall candidate has an unsupported slice")
        if not self.item_ref or not self.texts or any(not text for text in self.texts):
            raise ValueError("associative recall candidate requires bounded source text")


@dataclass(frozen=True, slots=True)
class AssociativeRecallSelection:
    """Stable item identities to promote inside the existing Context budget."""

    item_refs: frozenset[tuple[str, str]]


def _flush_feature_run(
    features: set[str],
    *,
    run: list[str],
    kind: str | None,
) -> None:
    if not run or kind is None:
        return
    value = "".join(run)
    if kind == "cjk":
        # Character n-grams avoid pretending whitespace is a word boundary in
        # Chinese/Japanese text.  Two-grams preserve exact short names while
        # three-grams provide the specificity threshold used below.
        for width in (2, 3):
            features.update(
                value[offset : offset + width]
                for offset in range(max(0, len(value) - width + 1))
            )
        return
    if len(value) >= 3:
        features.add(value)


def _uncached_recall_features(texts: tuple[str, ...]) -> frozenset[str]:
    """Build language-light lexical cues without interpreting their meaning."""

    features: set[str] = set()
    for raw in texts:
        text = unicodedata.normalize("NFKC", raw).casefold()
        run: list[str] = []
        kind: str | None = None
        for character in text:
            codepoint = ord(character)
            next_kind = (
                "cjk"
                if (
                    0x3400 <= codepoint <= 0x4DBF
                    or 0x4E00 <= codepoint <= 0x9FFF
                    or 0x3040 <= codepoint <= 0x30FF
                )
                else "word"
                if character.isalnum()
                else None
            )
            if next_kind != kind:
                _flush_feature_run(features, run=run, kind=kind)
                run = []
                kind = next_kind
            if next_kind is not None:
                run.append(character)
        _flush_feature_run(features, run=run, kind=kind)
    return frozenset(features)


@lexical_feature_cache(max_bytes=64 * 1024 * 1024, maxsize=8192)
def _text_recall_features(text: str) -> frozenset[str]:
    return _uncached_recall_features((text,))


def _recall_features(texts: tuple[str, ...]) -> frozenset[str]:
    if len(texts) == 1:
        return _text_recall_features(texts[0])
    return frozenset(term for text in texts for term in _text_recall_features(text))


@lru_cache(maxsize=4096)
def _cached_lexical_relevance_bp(query_text: str, document_texts: tuple[str, ...]) -> int:
    query = _recall_features((query_text,))
    document = _recall_features(document_texts)
    if not query or not document:
        return 0
    overlap = query & document
    if not overlap:
        return 0
    numerator = sum(len(feature) * len(feature) for feature in overlap)
    denominator = max(1, sum(len(feature) * len(feature) for feature in query))
    return min(10_000, numerator * 10_000 // denominator)


def lexical_relevance_bp(query_text: str, document_texts: tuple[str, ...]) -> int:
    # Numeric surface overlap only, keyed by every exact input byte. No actor
    # selection, source eligibility, time or relevance-policy decision is cached.
    # Limit retained input size as well as entry count.
    if (type(query_text) is str and type(document_texts) is tuple
        and all(type(text) is str for text in document_texts)
        and len(query_text) + sum(map(len, document_texts)) <= 1024):
        return _cached_lexical_relevance_bp(query_text, document_texts)
    return _cached_lexical_relevance_bp.__wrapped__(query_text, document_texts)


_GENERIC_CONVERSATIONAL_NGRAMS = frozenset(
    {
        "今天",
        "昨天",
        "明天",
        "现在",
        "刚才",
        "这会",
        "会儿",
        "这会儿",
        "时候",
        "的时候",
        "没有",
        "有没",
        "有没有",
        "是不是",
        "是吧",
        "能不能",
        "能不",
        "不能",
        "要不要",
        "要不",
        "不要",
        "好不好",
        "好不",
        "不好",
        "对不对",
        "对不",
        "不对",
        "想起来",
        "想起",
        "起来",
        "觉得",
        "那种",
        "这种",
        "或者",
        "什么",
        "怎么",
        "那个",
        "这个",
        "一点",
        "一下",
        "其实",
        "反正",
        "就是",
        "还是",
        "可以",
        "已经",
        "自己",
        "我们",
        "你们",
        "他们",
        "因为",
        "所以",
        "但是",
        "如果",
        "的事",
        "说到",
        "印象",
        "以前",
    }
)


class AssociativeRecallCompiler:
    """Select a bounded evidence-only prefetch set behind one small Interface."""

    def __init__(self, *, max_items: int = 4) -> None:
        if not 1 <= max_items <= 8:
            raise ValueError("associative recall item budget is invalid")
        self._max_items = max_items

    def compile(
        self,
        *,
        cue_text: str,
        candidates: tuple[AssociativeRecallCandidate, ...],
        seed: str = "",
    ) -> AssociativeRecallSelection:
        compressed_vo = re.sub(
            r"([\u4e00-\u9fff])[的了过着张个次场顿口份遍回]([\u4e00-\u9fff])",
            r"\1\2",
            cue_text,
        )
        raw_cue_features = _recall_features(
            (cue_text, compressed_vo) if compressed_vo != cue_text else (cue_text,)
        )
        substantive_cue_features = frozenset(
            feature
            for feature in (raw_cue_features - _GENERIC_CONVERSATIONAL_NGRAMS)
            if not (
                feature != "小时候"
                and (
                    feature.startswith(("时候", "候", "没有", "有没", "或者", "觉得", "想起", "起来"))
                    or feature.endswith(("有", "没", "的", "了", "吗", "呢", "吧", "啊", "呀", "或"))
                )
            )
        )
        cue_features = substantive_cue_features or raw_cue_features
        ranked: list[tuple[int, str, str]] = []
        for candidate in candidates:
            doc_features = _recall_features(candidate.texts)
            overlap = cue_features & doc_features
            if not overlap:
                continue
            if (
                not any(len(feature) >= 3 for feature in overlap)
                and len(overlap) < 2
            ):
                continue
            distinct_overlap = frozenset(
                feature
                for feature in overlap
                if not any(
                    feature != other and feature in other for other in overlap
                )
            )
            score = sum(
                4 if feature.endswith("时候") else len(feature) * len(feature)
                for feature in distinct_overlap
            )
            if seed:
                jitter = (
                    int.from_bytes(
                        hashlib.sha256(
                            f"associative-recall.1:{seed}:{candidate.slice_name}:{candidate.item_ref}".encode(
                                "utf-8"
                            )
                        ).digest()[:2],
                        "big",
                    )
                    % 64
                )
                score = score * 8 + jitter
            ranked.append((score, candidate.slice_name, candidate.item_ref))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        top = list(ranked[: self._max_items])
        if (
            seed
            and self._max_items >= 2
            and top
            and not any(slice_name == "active_memory_candidates" for _, slice_name, _ in top)
        ):
            top_memory = next(
                (item for item in ranked if item[1] == "active_memory_candidates"),
                None,
            )
            if top_memory is not None:
                top[-1] = top_memory
        return AssociativeRecallSelection(
            item_refs=frozenset(
                (slice_name, item_ref)
                for _, slice_name, item_ref in top
            )
        )


__all__ = [
    "ASSOCIATIVE_PREFETCH_POLICY_VERSION",
    "AssociativeRecallCandidate",
    "AssociativeRecallCompiler",
    "AssociativeRecallSelection",
    "lexical_relevance_bp",
]
