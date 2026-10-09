"""Source-bound narrative navigation; never future knowledge or causal proof."""

from __future__ import annotations

from typing import Literal
from pydantic import Field
from .schema_core import FrozenModel


class StoryAnchor(FrozenModel):
    source_kind: Literal["prehistory", "occurrence"]
    source_ref: str = Field(min_length=1, max_length=512)
    authority_ref: str = Field(min_length=1, max_length=512)
    authority_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    authority_revision: int = Field(ge=1)


class LivedStoryNavigation(FrozenModel):
    authority: Literal["author_navigation_not_causal_evidence"] = (
        "author_navigation_not_causal_evidence"
    )
    actor_ref: str = Field(min_length=1, max_length=256)
    thread_refs: tuple[str, ...] = Field(max_length=6)
    anchors: tuple[StoryAnchor, ...] = Field(default=(), max_length=24)


def anchor_catalog(projection, *, actor_ref):
    """Retained history and owned settled events at the exact current prefix."""
    from .prehistory_memory_source import prehistory_memory_binding

    result = {}
    active = {
        b
        for c in projection.memory_candidates
        if c.values.status == "active" and c.values.privacy_ceiling != "withhold"
        for b in c.values.source_bindings
    }
    for row in projection.prehistory_records:
        if (
            row.actor_ref != actor_ref
            or row.record.privacy_class == "withhold"
            or prehistory_memory_binding(row) not in active
        ):
            continue
        # The external author must not consume the protagonist's private
        # retrospective interpretations as material to script the world.
        if row.record.story is None or any(
            b.kind in {"historical_interpretation", "intention"} for b in row.record.story.blocks
        ):
            continue
        result[row.record.record_id] = StoryAnchor(
            source_kind="prehistory",
            source_ref=row.record.record_id,
            authority_ref=row.accepted_event_ref,
            authority_hash=row.accepted_payload_hash,
            authority_revision=row.accepted_world_revision,
        )
    for o in projection.world_occurrences:
        if o.status == "settled" and actor_ref in o.participant_refs and o.visibility != "withhold":
            result[o.occurrence_id] = StoryAnchor(
                source_kind="occurrence",
                source_ref=o.occurrence_id,
                authority_ref=o.settlement_event_ref,
                authority_hash=o.settlement_payload_hash,
                authority_revision=o.settlement_world_revision,
            )
    return result


def validate_navigation(navigation, *, projection, privacy_class):
    catalog = anchor_catalog(projection, actor_ref=navigation.actor_ref)
    ranks = ("public", "shareable", "personal", "private", "withhold")
    floors = {r.record.record_id: r.record.privacy_class for r in projection.prehistory_records}
    floors.update({o.occurrence_id: o.visibility for o in projection.world_occurrences})
    for candidate in projection.memory_candidates:
        if candidate.values.status == "active":
            for binding in candidate.values.source_bindings:
                if binding.source_id in floors:
                    floors[binding.source_id] = max(
                        (floors[binding.source_id], candidate.values.privacy_ceiling),
                        key=ranks.index,
                    )
    for anchor in navigation.anchors:
        if catalog.get(anchor.source_ref) != anchor:
            raise ValueError("story anchor is unavailable or changed")
        if ranks.index(privacy_class) < ranks.index(floors[anchor.source_ref]):
            raise ValueError("story navigation weakens source privacy")


def connect_readable_documents(documents, *, world_life, actor_ref):
    """Join only documents already admitted by ordinary Memory/World readers.

    Shared opaque keys are search navigation. No title, planned node, future
    body, new interpretation or undisclosed source ID reaches a model.
    """
    from .world_outlook import digest

    if not any(
        item.content is not None
        and item.content.world_consequence is not None
        and item.content.world_consequence.story_navigation is not None
        for item in world_life
    ):
        return documents

    by_source = {}
    for i, doc in enumerate(documents):
        if doc.actor_ref != actor_ref or doc.context_only:
            continue
        if doc.prehistory is not None:
            ref = doc.source_item_ref.split(":window:")[0]
        elif doc.settled_life_json:
            import json

            ref = json.loads(doc.settled_life_json)["occurrence_id"]
        else:
            ref = doc.source_item_ref
        by_source.setdefault(ref, []).append(i)
    links = {}
    for item in world_life:
        reading = item.content.world_consequence if item.content is not None else None
        nav = reading.story_navigation if reading is not None else None
        if nav is None or nav.actor_ref != actor_ref:
            continue
        current = by_source.get(item.occurrence_id, ())
        for anchor in nav.anchors:
            eligible = [
                i
                for i in by_source.get(anchor.source_ref, ())
                if any(
                    b.ref == anchor.authority_ref
                    and b.immutable_hash == anchor.authority_hash
                    and b.source_world_revision == anchor.authority_revision
                    for b in documents[i].source_bindings
                )
            ]
            if not current or not eligible:
                continue
            key = "lived-story-link:" + digest(
                [actor_ref, item.occurrence_id, anchor.model_dump(mode="json")]
            )
            endpoints = (*current, *eligible)
            if any(
                len(set(documents[i].link_refs) | links.get(i, set()) | {key}) > 32
                for i in endpoints
            ):
                continue
            for i in endpoints:
                links.setdefault(i, set()).add(key)
    return [
        doc.model_copy(update={"link_refs": tuple(sorted(set(doc.link_refs) | links[i]))})
        if i in links
        else doc
        for i, doc in enumerate(documents)
    ]


def expand_lived_story_hits(*, hits, eligible_rows, query, index_version):
    """One-hop, append-only navigation within the existing reading budgets."""
    from .recall_index import (
        RecallHit,
        _canonical_json,
        RECALL_MODEL_READING_MAX_BYTES,
        RECALL_RESULT_MAX_BYTES,
    )
    from .recall_model_reading import interior_recall_item

    if not hits or query.story_window is not None or query.episode_window is not None:
        return hits
    keys = {
        ref for hit in hits for ref in hit.document.link_refs if ref.startswith("lived-story-link:")
    }
    if not keys:
        return hits
    selected = list(hits)
    seen = {h.document.document_id for h in hits}
    candidates = sorted(
        (
            d
            for d, _ in eligible_rows
            if not d.context_only and d.document_id not in seen and set(d.link_refs) & keys
        ),
        key=lambda d: d.document_id,
    )
    for doc in candidates:
        if len(selected) >= min(query.limit, 6):
            break
        hit = RecallHit(
            document=doc.model_copy(update={"retrieval_text": None}),
            match_channels=("structured",),
            score_bp=0,
            lexical_score_bp=0,
            dense_score_bp=0,
            temporal_score_bp=0,
            structured_score_bp=10_000,
            accessibility_offset_bp=0,
        )
        proposed = (*selected, hit)
        if (
            len(
                _canonical_json(
                    {
                        "items": [
                            interior_recall_item(h.document, index_version=index_version)
                            for h in proposed
                        ]
                    }
                ).encode()
            )
            > RECALL_MODEL_READING_MAX_BYTES
        ):
            continue
        if (
            len(_canonical_json([h.model_dump(mode="json") for h in proposed]).encode())
            > RECALL_RESULT_MAX_BYTES
        ):
            continue
        selected.append(hit)
    return tuple(selected)
