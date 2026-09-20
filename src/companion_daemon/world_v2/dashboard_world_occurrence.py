"""Read only a published, selected World environment at the owner's pin."""

from dataclasses import dataclass
from typing import Literal

from .ledger import LedgerPort
from .life_content import collect_user_channel_limited_content_refs
from .life_content_events import LifeContentRecordedPayload
from .life_content_reading import selected_world_consequence
from .life_content_store import ImmutableLifeContentStore
from .occurrence_result_content_runtime import require_published_world_consequence
from .schemas import LedgerProjection, ProjectionCursor, WorldEvent, WorldOccurrenceProjection


_PRIVACY = {"public": 0, "shareable": 1, "personal": 2, "private": 3, "withhold": 4}


@dataclass(frozen=True)
class DashboardWorldOccurrenceReading:
    status: Literal["read", "not_settled", "unavailable", "withheld"]
    text: str | None = None
    truncated: bool = False


def _event_at(
    ledger: LedgerPort, projection: LedgerProjection, event_ref: str, event_type: str,
) -> WorldEvent:
    refs = [item for item in projection.committed_world_event_refs if item.event_id == event_ref]
    located = ledger.lookup_event_commit(event_ref)
    if len(refs) != 1 or located is None:
        raise ValueError("dashboard World content source is not pinned")
    event, commit = located
    event = WorldEvent.model_validate_json(event.model_dump_json())
    ref = refs[0]
    if (
        event.world_id != projection.world_id or event.event_id != event_ref
        or event.event_type != event_type or ref.event_type != event_type
        or event.payload_hash != ref.payload_hash or event.logical_time != ref.logical_time
        or commit.world_revision != ref.world_revision
        or commit.world_revision > projection.world_revision
        or commit.deliberation_revision > projection.deliberation_revision
        or commit.ledger_sequence > projection.ledger_sequence
    ):
        raise ValueError("dashboard World content source differs from its pin")
    return event


def read_dashboard_world_occurrence(
    *, ledger: LedgerPort, store: ImmutableLifeContentStore | None,
    projection: LedgerProjection, cursor: ProjectionCursor, occurrence: WorldOccurrenceProjection,
    actor_ref: str, viewer_privacy_ceiling: Literal["public", "shareable", "personal", "private"],
    max_characters: int = 240,
) -> DashboardWorldOccurrenceReading:
    """No candidate, role response, model audit or arbitrary ref is a fallback."""
    unavailable = DashboardWorldOccurrenceReading("unavailable")
    try:
        if (
            ledger.world_id != projection.world_id or occurrence not in projection.world_occurrences
            or not actor_ref or actor_ref not in occurrence.participant_refs
            or viewer_privacy_ceiling not in _PRIVACY or viewer_privacy_ceiling == "withhold"
            or max_characters < 2
            or any(getattr(cursor, key) != getattr(projection, key) for key in (
                "world_revision", "deliberation_revision", "ledger_sequence",
            ))
        ):
            return unavailable
        if occurrence.status != "settled":
            return DashboardWorldOccurrenceReading("not_settled")
        candidates = [item for item in occurrence.candidate_outcomes
                      if item.candidate_result_ref == occurrence.settled_outcome_ref]
        descriptors = [item for item in projection.life_content_descriptors
                       if item.source_kind == "occurrence_settlement"
                       and item.source_event_ref == occurrence.settlement_event_ref]
        if len(candidates) != 1 or len(descriptors) != 1 or store is None:
            return unavailable
        candidate, descriptor = candidates[0], descriptors[0]
        if any(_PRIVACY[value] > _PRIVACY[viewer_privacy_ceiling] or value == "withhold"
               for value in (occurrence.visibility, candidate.privacy_class, descriptor.privacy_class)):
            return DashboardWorldOccurrenceReading("withheld")
        settlement = _event_at(ledger, projection, occurrence.settlement_event_ref, "WorldOccurrenceSettled")
        settled = settlement.payload()
        if any(settled.get(key) != getattr(occurrence, key) for key in (
            "occurrence_id", "result_id", "result_payload_ref", "result_payload_hash",
        )) or settled.get("candidate_result_ref") != occurrence.settled_outcome_ref:
            return unavailable
        publication = _event_at(ledger, projection, descriptor.descriptor_event_ref, "LifeContentRecorded")
        published = LifeContentRecordedPayload.model_validate_json(publication.payload_json)
        if any(getattr(published, key) != getattr(descriptor, key)
               for key in LifeContentRecordedPayload.model_fields):
            return unavailable
        limited = collect_user_channel_limited_content_refs(ledger=ledger, projection=projection)
        if candidate.content_ref in limited or occurrence.result_payload_ref in limited:
            return DashboardWorldOccurrenceReading("withheld")
        require_published_world_consequence(projection=projection, occurrence=occurrence, content_store=store)
        consequence = selected_world_consequence(
            store=store, projection=projection, occurrence=occurrence, actor_ref=actor_ref,
            viewer_privacy_ceiling=viewer_privacy_ceiling, user_channel_limited_content_refs=limited,
        )
        text = " ".join(consequence.environment_text.split())
        truncated = len(text) > max_characters
        return DashboardWorldOccurrenceReading(
            "read", text[:max_characters - 1] + "…" if truncated else text, truncated,
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return unavailable
