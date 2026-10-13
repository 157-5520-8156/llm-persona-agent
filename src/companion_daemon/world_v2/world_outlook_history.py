"""One pinned offer for already displayed, source-verified story references."""

from __future__ import annotations

import json
from typing import Literal

from .context_capsule import ResolvedItemMetadata, canonical_value_hash
from .lived_story import StoryAnchor, anchor_catalog
from .schema_core import FrozenModel, PrivacyClass
from .world_life_context import WorldLifeContextItem


_RANK = {value: rank for rank, value in enumerate(("public", "shareable", "personal", "private", "withhold"))}


class OfferedStoryAnchor(FrozenModel):
    anchor: StoryAnchor
    privacy_class: PrivacyClass


class OutlookHistoryOffer(FrozenModel):
    contract: Literal["outlook-history-offer.1"] = "outlook-history-offer.1"
    items: tuple[OfferedStoryAnchor, ...]

    @property
    def anchors(self) -> tuple[StoryAnchor, ...]:
        return tuple(item.anchor for item in self.items)

    def validate_privacy(self, draft, *, precise_errors=False) -> None:
        floors = {item.anchor: item.privacy_class for item in self.items}
        threads = {item.thread_id: item for item in draft.threads}
        if any(anchor not in floors for thread in draft.threads for anchor in thread.history_anchors):
            raise ValueError("story anchors were not offered in the pinned history offer")
        violations = []
        for node in draft.opportunities:
            for thread_ref in node.thread_refs:
                for anchor in threads[thread_ref].history_anchors:
                    if anchor in floors and _RANK[node.execution.privacy_class] < _RANK[floors[anchor]]:
                        if not precise_errors:
                            raise ValueError("outlook navigation weakens displayed source privacy")
                        violations.append((node.opportunity_id, floors[anchor]))
        if violations:
            minimums = {}
            for identity, floor in violations:
                minimums[identity] = max((minimums.get(identity, floor), floor), key=_RANK.__getitem__)
            raise ValueError("outlook execution privacy is below its linked history: " + "; ".join(
                f"{identity!r} requires at least {floor}" for identity, floor in minimums.items()
            ))


def prepare_history_offer(*, projection, actor_ref, context, history_material, current_outlook):
    """Union eligible references actually shown through the three input surfaces.

    The catalog proves availability; it is never itself an offer. A quoted ID,
    resolver metadata, active plan or undisplayed record cannot add a source.
    No source text is fetched, rewritten or duplicated here.
    """
    catalog = anchor_catalog(projection, actor_ref=actor_ref)
    floors = {r.record.record_id: r.record.privacy_class for r in projection.prehistory_records}
    floors.update({o.occurrence_id: o.visibility for o in projection.world_occurrences})
    # Content privacy belongs to the settled source, not the route by which it
    # happened to be displayed this turn. Carry/retrieval must retain it too.
    occurrences = {o.occurrence_id: o for o in projection.world_occurrences}
    for occurrence in occurrences.values():
        for outcome in occurrence.candidate_outcomes:
            if outcome.candidate_result_ref == occurrence.settled_outcome_ref:
                floors[occurrence.occurrence_id] = max(
                    (floors[occurrence.occurrence_id], outcome.privacy_class), key=_RANK.__getitem__,
                )
    for descriptor in projection.life_content_descriptors:
        occurrence = occurrences.get(descriptor.source_entity_id)
        if occurrence is not None and (
            descriptor.content_kind == "occurrence_result"
            and descriptor.content_ref == occurrence.result_payload_ref
            and descriptor.content_payload_hash == (occurrence.result_payload_hash or "").removeprefix("sha256:")
            and descriptor.source_entity_revision == occurrence.entity_revision
            and descriptor.source_event_ref == occurrence.settlement_event_ref
            and descriptor.source_world_revision == occurrence.settlement_world_revision
            and descriptor.source_payload_hash == occurrence.settlement_payload_hash
        ):
            floors[occurrence.occurrence_id] = max(
                (floors[occurrence.occurrence_id], descriptor.privacy_class), key=_RANK.__getitem__,
            )
    for candidate in projection.memory_candidates:
        if candidate.values.status == "active":
            for binding in candidate.values.source_bindings:
                if binding.source_id in floors:
                    floors[binding.source_id] = max(
                        (floors[binding.source_id], candidate.values.privacy_ceiling), key=_RANK.__getitem__,
                    )
    offered = {}

    def add(anchor, privacy=None):
        if catalog.get(anchor.source_ref) != anchor:
            return
        floor = max((floors[anchor.source_ref], privacy or floors[anchor.source_ref]), key=_RANK.__getitem__)
        if floor == "withhold":
            return
        previous = offered.get(anchor.source_ref)
        if previous is not None:
            floor = max((floor, previous.privacy_class), key=_RANK.__getitem__)
        offered[anchor.source_ref] = OfferedStoryAnchor(anchor=anchor, privacy_class=floor)

    for item in history_material:
        add(StoryAnchor.model_validate(item["anchor"]))
    for thread in current_outlook.get("threads", ()):
        for raw in thread.get("history_anchors", ()):
            add(StoryAnchor.model_validate(raw))
    for anchor, privacy in _visible_settlements(projection, actor_ref, context, catalog):
        add(anchor, privacy)
    return OutlookHistoryOffer(items=tuple(offered.values()))


def _visible_settlements(projection, actor_ref, context, catalog):
    # Background slicing preserves these pinned identities even when it drops
    # items. Iterate the actual item list, never the pre-slicing resolver refs.
    if context.get("actor_ref") != actor_ref or any(
        context.get(key) != getattr(projection, key)
        for key in ("world_id", "world_revision", "deliberation_revision", "ledger_sequence")
    ):
        return
    lane = context.get("slices", {}).get("world_life", {})
    if lane.get("availability") != "available":
        return
    committed = {ref.event_id: ref for ref in projection.committed_world_event_refs}
    occurrences = {o.occurrence_id: o for o in projection.world_occurrences}
    descriptors = {d.content_id: d for d in projection.life_content_descriptors}
    for item in lane.get("items", ()):
        try:
            value = WorldLifeContextItem.model_validate_json(json.dumps(item["value"]))
            metadata = ResolvedItemMetadata.model_validate_json(json.dumps(
                {key: value for key, value in item.items() if key != "value"},
            ))
        except (ValueError, TypeError, KeyError):
            continue
        anchor = catalog.get(value.occurrence_id)
        content = value.content
        occurrence = occurrences.get(value.occurrence_id)
        if anchor is None or occurrence is None or content is None:
            continue
        descriptor = descriptors.get(content.content_id)
        if (
            metadata.item_ref != anchor.source_ref
            or metadata.value_hash != canonical_value_hash(value)
            or value.occurrence_entity_revision != occurrence.entity_revision
            or value.participant_refs != tuple(sorted(occurrence.participant_refs))
            or value.location_ref != occurrence.location_ref
            or value.result_id != occurrence.result_id
            or value.result_payload_ref != occurrence.result_payload_ref
            or value.result_payload_hash != occurrence.result_payload_hash
            or value.settled_at != occurrence.settled_at
            or (value.source.authority_event_ref, value.source.authority_world_revision, value.source.authority_payload_hash)
            != (anchor.authority_ref, anchor.authority_revision, anchor.authority_hash)
            or content.source_entity_id != value.occurrence_id
            or content.source_entity_revision != value.occurrence_entity_revision
            or (content.authority_event_ref, content.authority_world_revision, content.authority_payload_hash)
            != (anchor.authority_ref, anchor.authority_revision, anchor.authority_hash)
            or content.content_kind != "occurrence_result"
            or content.content_ref != value.result_payload_ref
            or content.content_payload_hash != value.result_payload_hash.removeprefix("sha256:")
            or descriptor is None
            or any(getattr(content, key) != getattr(descriptor, key) for key in (
                "content_ref", "content_payload_hash", "content_kind", "source_entity_id", "source_entity_revision",
                "descriptor_event_ref", "descriptor_world_revision", "descriptor_payload_hash",
            ))
        ):
            continue
        required = {
            (anchor.authority_ref, anchor.authority_revision, anchor.authority_hash),
            (content.descriptor_event_ref, content.descriptor_world_revision, content.descriptor_payload_hash),
        }
        claims = {(b.ref, b.source_world_revision, b.immutable_hash) for b in metadata.source_bindings}
        if not required <= claims or any(
            b.source_kind != "committed_event"
            or b.ref not in committed
            or (b.authority_type, b.source_world_revision, b.immutable_hash)
            != (committed[b.ref].event_type, committed[b.ref].world_revision, committed[b.ref].payload_hash)
            for b in metadata.source_bindings
        ):
            continue
        floor = max((occurrence.visibility, descriptor.privacy_class, value.privacy_class,
                     content.privacy_class, metadata.privacy_class), key=_RANK.__getitem__)
        if floor == "withhold" or any(
            _RANK[p] < _RANK[descriptor.privacy_class]
            for p in (value.privacy_class, content.privacy_class, metadata.privacy_class)
        ):
            continue
        yield anchor, floor
