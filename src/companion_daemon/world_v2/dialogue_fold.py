"""Deterministic extractive folds for dialogue that would otherwise be dropped."""

from __future__ import annotations

import json

from .present_prompt import (
    PRESENT_DIALOGUE_FOLD_CHUNK_ITEMS,
    PRESENT_DIALOGUE_FOLD_LINE_CHARACTERS,
    PRESENT_DIALOGUE_SEQUENCE_SCALE,
    PRESENT_DIALOGUE_SLICE_CHARACTERS,
)


def dialogue_fold_bucket(sequence: object) -> int | None:
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        return None
    span = PRESENT_DIALOGUE_FOLD_CHUNK_ITEMS * PRESENT_DIALOGUE_SEQUENCE_SCALE
    return sequence // span


def fold_dialogue_line(entry: dict[str, object]) -> str:
    speaker = entry.get("speaker")
    text = entry.get("text")
    label = speaker if isinstance(speaker, str) and speaker else "unknown"
    body = text.strip() if isinstance(text, str) else ""
    if len(body) > PRESENT_DIALOGUE_FOLD_LINE_CHARACTERS:
        body = body[:PRESENT_DIALOGUE_FOLD_LINE_CHARACTERS]
    return f"{label}: {body}"


def _fold_chunk(entries: list[dict[str, object]]) -> dict[str, object]:
    ids = [
        item["dialogue_id"]
        for item in entries
        if isinstance(item.get("dialogue_id"), str)
    ]
    occurred = [
        item["occurred_at"]
        for item in entries
        if isinstance(item.get("occurred_at"), str)
    ]
    chunk: dict[str, object] = {
        "dialogue_ids": ids,
        "lines": [fold_dialogue_line(item) for item in entries],
    }
    if occurred:
        chunk["from"] = occurred[0]
        chunk["to"] = occurred[-1]
    return chunk


def _encoded_size(
    folds: list[dict[str, object]],
    verbatim: list[dict[str, object]],
) -> int:
    return len(
        json.dumps(
            {"folded_dialogue": folds, "recent_dialogue": verbatim},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def fold_dialogue_entries(
    entries: list[dict[str, object]],
    *,
    budget_characters: int = PRESENT_DIALOGUE_SLICE_CHARACTERS,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if not entries:
        return [], []
    if _encoded_size([], entries) <= budget_characters:
        return [], entries
    buckets: dict[int, list[dict[str, object]]] = {}
    if all(dialogue_fold_bucket(entry.get("sequence")) is not None for entry in entries):
        for entry in entries:
            bucket = dialogue_fold_bucket(entry.get("sequence"))
            assert bucket is not None
            buckets.setdefault(bucket, []).append(entry)
    else:
        for offset in range(0, len(entries), PRESENT_DIALOGUE_FOLD_CHUNK_ITEMS):
            buckets[offset // PRESENT_DIALOGUE_FOLD_CHUNK_ITEMS] = entries[
                offset : offset + PRESENT_DIALOGUE_FOLD_CHUNK_ITEMS
            ]
    groups = [buckets[key] for key in sorted(buckets)]
    folds: list[dict[str, object]] = []
    while groups and _encoded_size(folds, [item for group in groups for item in group]) > budget_characters:
        if len(groups) == 1:
            verbatim = list(groups[0])
            while (
                _encoded_size(folds, verbatim) > budget_characters
                and len(verbatim) > 1
            ):
                take = min(PRESENT_DIALOGUE_FOLD_CHUNK_ITEMS, len(verbatim) - 1)
                folds.append(_fold_chunk(verbatim[:take]))
                verbatim = verbatim[take:]
            return folds, verbatim
        folds.append(_fold_chunk(groups[0]))
        groups = groups[1:]
    return folds, [item for group in groups for item in group]


__all__ = [
    "dialogue_fold_bucket",
    "fold_dialogue_entries",
    "fold_dialogue_line",
]
