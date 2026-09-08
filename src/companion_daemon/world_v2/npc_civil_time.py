"""A narrow civil-time reading; no NPC access to the rest of the biography."""

from datetime import datetime
import hashlib
import sqlite3
from typing import Literal

from pydantic import Field, model_validator

from .biographical_timeline_authority import BiographicalTimelineConfiguredPayload
from .local_chronology import LocalChronology
from .schema_core import FrozenModel
from .world_life_context import WorldLifeSourceBinding


class NpcTimeFieldBinding(WorldLifeSourceBinding):
    """Event provenance authorizes only this displayed JSON-pointer field."""

    field_path: Literal["/logical_time_to", "/timezone_name"]
    value: str
    authority_scope: Literal["exact_time_field_only"] = "exact_time_field_only"


class NpcCivilTime(FrozenModel):
    contract: Literal["npc-civil-time.1"] = "npc-civil-time.1"
    status: Literal["available", "unavailable"] = "unavailable"
    reason_code: str | None = "not_compiled"
    timezone_name: str | None = None
    local_time: str | None = None
    source_bindings: tuple[NpcTimeFieldBinding, ...] = Field(default=(), max_length=2)

    @model_validator(mode="after")
    def availability_matches_sources(self):
        if self.status == "available":
            if (
                self.reason_code is not None
                or not self.timezone_name
                or not self.local_time
                or tuple(item.field_path for item in self.source_bindings)
                != ("/logical_time_to", "/timezone_name")
            ):
                raise ValueError("available NPC civil time needs both exact time fields")
        elif (
            not self.reason_code
            or self.timezone_name is not None
            or self.local_time is not None
            or self.source_bindings
        ):
            raise ValueError("unavailable NPC civil time cannot carry inferred fields")
        return self

    @property
    def source_refs(self) -> tuple[str, ...]:
        return tuple(item.authority_event_ref for item in self.source_bindings)


def _read_exact(ledger, authority):
    reader = getattr(ledger, "lookup_event_commit", None)
    if not callable(reader):
        return None
    try:
        located = reader(authority.event_id)
    except (OSError, sqlite3.Error):
        return None
    if located is None:
        return None
    event, commit = located
    if (
        event.world_id != ledger.world_id
        or event.event_id != authority.event_id
        or event.event_type != authority.event_type
        or event.logical_time != authority.logical_time
        or event.event_id not in commit.event_ids
        or commit.world_revision < authority.world_revision
        or event.payload_hash != authority.payload_hash
        or hashlib.sha256(event.payload_json.encode("utf-8")).hexdigest() != authority.payload_hash
    ):
        raise ValueError("NPC time source binding is inconsistent")
    return event


def compile_npc_civil_time(*, ledger, projection, catalog_timezone_name=None) -> NpcCivilTime:
    """Read the pinned Clock and immutable World timezone, never infer from place."""

    now = projection.logical_time
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        return NpcCivilTime(reason_code="clock_missing")
    clocks = tuple(
        item
        for item in projection.committed_world_event_refs
        if item.event_type == "ClockAdvanced" and item.logical_time == now
    )
    if not clocks:
        return NpcCivilTime(reason_code="clock_missing")
    clock = max(clocks, key=lambda item: item.world_revision)
    try:
        clock_event = _read_exact(ledger, clock)
        if clock_event is None:
            return NpcCivilTime(reason_code="clock_unreadable")
        clock_value = clock_event.payload()["logical_time_to"]
        clock_time = datetime.fromisoformat(clock_value)
        if clock_time.tzinfo is None or clock_time.utcoffset() is None or clock_time != now:
            raise ValueError("Clock does not prove the pinned time")
    except (KeyError, TypeError, ValueError):
        return NpcCivilTime(reason_code="clock_invalid")
    timelines = tuple(
        item
        for item in projection.committed_world_event_refs
        if item.event_type == "BiographicalTimelineConfigured"
    )
    if len(timelines) != 1:
        return NpcCivilTime(
            reason_code="timeline_missing" if not timelines else "timeline_ambiguous"
        )
    timeline = timelines[0]
    try:
        timeline_event = _read_exact(ledger, timeline)
        if timeline_event is None:
            return NpcCivilTime(reason_code="timeline_unreadable")
        recorded = BiographicalTimelineConfiguredPayload.model_validate_json(
            timeline_event.payload_json
        )
    except (TypeError, ValueError):
        return NpcCivilTime(reason_code="timeline_invalid")
    if catalog_timezone_name is not None and catalog_timezone_name != recorded.timezone_name:
        return NpcCivilTime(reason_code="catalog_timezone_mismatch")
    local = LocalChronology(timezone_name=recorded.timezone_name).localize(clock_time)
    return NpcCivilTime(
        status="available",
        reason_code=None,
        timezone_name=recorded.timezone_name,
        local_time=local.isoformat(),
        source_bindings=tuple(
            NpcTimeFieldBinding(
                authority_event_ref=source.event_id,
                authority_world_revision=source.world_revision,
                authority_payload_hash=source.payload_hash,
                field_path=path,
                value=value,
            )
            for source, path, value in (
                (clock, "/logical_time_to", clock_value),
                (timeline, "/timezone_name", recorded.timezone_name),
            )
        ),
    )
