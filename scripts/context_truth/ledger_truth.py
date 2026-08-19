"""Path B: ledger-projection truth, independent of the snapshot compiler."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3
from pathlib import Path
from typing import Any

from companion_daemon.world_v2.photographable_inventory import shareable_photo_facts
from companion_daemon.world_v2.present_moment_candidate import activity_kind_is_sleep
from companion_daemon.world_v2.qq_face_render_catalog import lookup_face_render
from companion_daemon.world_v2.recent_dialogue import observation_dialogue_text
from companion_daemon.world_v2.schemas import LedgerProjection, Observation, WorldEvent

from .types import CatalogFace, CompanionLine, CounterpartLine, LedgerTruth

RECENT_WINDOW = 8
_SIDECAR_PROCESS_TABLES = (
    "external_perception_source_evidence",
    "external_perception_raw_evidence",
    "external_perception_rejected_items",
    "external_signal_search_documents",
    "external_perception_storage_samples",
    "external_perception_attention_exposures",
    "external_perception_attention_opportunities",
    "external_perception_attention_attempts",
    "external_signal_revisions",
    "external_perception_live_outbox",
)


def _payload(event: WorldEvent) -> dict[str, Any]:
    try:
        decoded = json.loads(event.payload_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _as_observation(event: WorldEvent) -> Observation | None:
    if event.event_type != "ObservationRecorded":
        return None
    payload = _payload(event)
    body = payload.get("observation") if isinstance(payload.get("observation"), dict) else payload
    try:
        return Observation.model_validate(body)
    except Exception:
        return None


def _counterpart_line(event: WorldEvent) -> CounterpartLine | None:
    observation = _as_observation(event)
    if observation is None:
        payload = _payload(event)
        text = payload.get("text") if isinstance(payload.get("text"), str) else ""
        meta = payload.get("coalescing_metadata") if isinstance(payload.get("coalescing_metadata"), dict) else {}
        reaction_refs = tuple(
            item for item in (meta.get("reaction_refs") or ()) if isinstance(item, str) and item
        )
        sticker_refs = tuple(
            item for item in (meta.get("sticker_refs") or ()) if isinstance(item, str) and item
        )
        visible = text.strip()
        if not visible and (reaction_refs or sticker_refs):
            visible = " ".join((*reaction_refs, *sticker_refs))
        if not visible:
            return None
        return CounterpartLine(
            event_id=event.event_id,
            text=visible,
            occurred_at=event.logical_time,
            reaction_refs=reaction_refs,
            sticker_refs=sticker_refs,
        )
    visible = observation_dialogue_text(observation)
    meta = observation.coalescing_metadata if isinstance(observation.coalescing_metadata, dict) else {}
    reaction_refs = tuple(
        item for item in (meta.get("reaction_refs") or ()) if isinstance(item, str) and item
    )
    sticker_refs = tuple(
        item for item in (meta.get("sticker_refs") or ()) if isinstance(item, str) and item
    )
    text = visible or (observation.text or "")
    if not text.strip() and not reaction_refs and not sticker_refs:
        return None
    # Path B for catalog names is the catalog itself. Keep the raw observation
    # text (not the compiler's rendered label) so K5 can see a bare number.
    raw_text = observation.text.strip() if isinstance(observation.text, str) and observation.text.strip() else ""
    if not raw_text and (reaction_refs or sticker_refs):
        raw_text = " ".join((*reaction_refs, *sticker_refs))
    return CounterpartLine(
        event_id=event.event_id,
        text=raw_text or text,
        occurred_at=observation.logical_time or event.logical_time,
        reaction_refs=reaction_refs,
        sticker_refs=sticker_refs,
    )


def _lookup(ledger: Any, event_id: str) -> WorldEvent | None:
    lookup = getattr(ledger, "lookup_event_commit", None)
    if not callable(lookup):
        return None
    located = lookup(event_id)
    if located is None:
        return None
    event = located[0]
    return event if isinstance(event, WorldEvent) else None


def _observation_events(ledger: Any, projection: LedgerProjection) -> tuple[WorldEvent, ...]:
    """Load ObservationRecorded by ledger event_id, not QQ coalesced ids.

    ``message_observations.source_event_id`` is the platform coalesced id
    (``qq:…:qq-coalesced:…``). ``lookup_event_commit`` keys on WorldEvent
    ``event_id`` (``event:trigger:observation:…``). Using the coalesced id
    used to make Path B look like he never spoke.
    """

    recent = getattr(ledger, "recent_events_by_type", None)
    if callable(recent) and projection.logical_time is not None:
        try:
            found = recent(
                event_types=frozenset({"ObservationRecorded"}),
                since=datetime(1970, 1, 1, tzinfo=UTC),
                limit=4096,
            )
        except Exception:
            found = ()
        if found:
            return tuple(found)
    events: list[WorldEvent] = []
    for item in projection.committed_world_event_refs:
        if item.event_type != "ObservationRecorded":
            continue
        event = _lookup(ledger, item.event_id)
        if event is not None:
            events.append(event)
    return tuple(events)


def _event_actor(event: WorldEvent) -> str | None:
    payload = _payload(event)
    actor = payload.get("actor")
    if isinstance(actor, str) and actor:
        return actor
    raw = getattr(event, "actor", None)
    return raw if isinstance(raw, str) and raw else None


def _available_candidates(
    projection: LedgerProjection,
) -> tuple[tuple[str, ...], dict[str, int]]:
    facts = shareable_photo_facts(projection, logical_time=projection.logical_time)
    ids = tuple(
        fact.candidate_id
        for fact in facts
        if fact.photo_in_hand and isinstance(fact.candidate_id, str) and fact.candidate_id
    )
    counts: dict[str, int] = {}
    for item in projection.photo_candidates:
        status = str(getattr(item, "status", "") or "")
        counts[status] = counts.get(status, 0) + 1
    return ids, counts


def _catalog_faces(lines: tuple[CounterpartLine, ...]) -> tuple[CatalogFace, ...]:
    faces: list[CatalogFace] = []
    for line in lines:
        for ref in (*line.reaction_refs, *line.sticker_refs):
            entry = lookup_face_render(ref)
            faces.append(
                CatalogFace(
                    provider_ref=ref,
                    catalog_name=entry.name if entry is not None else None,
                    catalog_glyph=entry.glyph if entry is not None else None,
                    seen_text=line.text,
                )
            )
    return tuple(faces)


def _sidecar_table_counts(path: Path | None) -> dict[str, int]:
    if path is None or not path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        names = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        counts: dict[str, int] = {}
        for table in names:
            if not (
                table.startswith("external_perception_")
                or table.startswith("external_signal_")
            ):
                continue
            if table.endswith("_fts") or "_fts_" in table:
                continue
            try:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                continue
        return counts
    finally:
        conn.close()


def _sidecar_processed_count(counts: dict[str, int]) -> int | None:
    if not counts:
        return None
    ranked = [counts[name] for name in _SIDECAR_PROCESS_TABLES if name in counts]
    if ranked:
        return max(ranked)
    return max(counts.values()) if counts else None


def collect_ledger_truth(
    *,
    ledger: Any,
    projection: LedgerProjection,
    counterpart_actor_ref: str,
    sidecar_path: Path | None = None,
) -> LedgerTruth:
    available_ids, status_counts = _available_candidates(projection)
    counterpart: list[CounterpartLine] = []
    for event in _observation_events(ledger, projection):
        actor = _event_actor(event)
        if actor is not None and actor != counterpart_actor_ref:
            continue
        line = _counterpart_line(event)
        if line is None:
            continue
        counterpart.append(line)
    counterpart.sort(
        key=lambda item: (
            item.occurred_at or datetime.min.replace(tzinfo=UTC),
            item.event_id,
        )
    )
    payloads = {item.payload_ref: item for item in projection.stored_message_payloads}
    settled: list[CompanionLine] = []
    waiting: list[CompanionLine] = []
    for beat in projection.expression_beats:
        stored = payloads.get(beat.payload_ref)
        if stored is None:
            continue
        row = CompanionLine(
            payload_ref=beat.payload_ref,
            text=stored.text,
            state=beat.state,
        )
        if beat.state == "settled":
            settled.append(row)
        elif beat.state == "authorized":
            waiting.append(row)
    logical = projection.logical_time
    active_appraisals = tuple(
        item.appraisal_id
        for item in projection.appraisals
        if item.status == "active" and (logical is None or item.expires_at > logical)
    )
    expired_appraisals = tuple(
        item.appraisal_id
        for item in projection.appraisals
        if item.status == "active" and logical is not None and item.expires_at <= logical
    )
    counterpart_rel = next(
        (
            item
            for item in projection.relationship_states
            if item.subject_ref == counterpart_actor_ref
        ),
        None,
    )
    if counterpart_rel is None and len(projection.relationship_states) == 1:
        counterpart_rel = projection.relationship_states[0]
    active_plans = tuple(
        item.plan_id for item in projection.plans if item.status == "active"
    )
    non_sleep = tuple(
        item.plan_id
        for item in projection.plans
        if item.status == "active" and not activity_kind_is_sleep(item.activity_kind)
    )
    npc_rel = sum(
        1
        for item in projection.relationship_states
        if item.subject_ref != counterpart_actor_ref
        and not str(item.subject_ref).startswith("user:")
    )
    sidecar_counts = _sidecar_table_counts(sidecar_path)
    return LedgerTruth(
        world_id=projection.world_id,
        cursor_seq=projection.ledger_sequence,
        world_revision=projection.world_revision,
        logical_time=projection.logical_time,
        available_photo_candidate_ids=available_ids,
        photo_candidate_status_counts=status_counts,
        media_delivery_ids=tuple(item.delivery_id for item in projection.media_deliveries),
        last_counterpart=counterpart[-1] if counterpart else None,
        counterpart_all=tuple(counterpart),
        counterpart_recent=tuple(counterpart[-RECENT_WINDOW:]),
        companion_all_settled=tuple(settled),
        companion_recent_settled=tuple(settled[-RECENT_WINDOW:]),
        companion_waiting=tuple(waiting),
        catalog_faces=_catalog_faces(tuple(counterpart)),
        external_perception_count=len(projection.external_perceptions),
        perception_result_count=len(projection.perception_results),
        sidecar_processed_count=_sidecar_processed_count(sidecar_counts),
        active_appraisal_ids=active_appraisals,
        expired_appraisal_ids=expired_appraisals,
        active_affect_ids=tuple(
            item.episode_id for item in projection.affect_episodes if item.status == "active"
        ),
        active_impression_ids=tuple(
            item.impression_id
            for item in projection.private_impressions
            if item.status == "active"
        ),
        open_thread_ids=tuple(
            item.thread_id for item in projection.threads if item.values.status == "open"
        ),
        active_aspiration_ids=tuple(
            item.aspiration_id for item in projection.aspirations if item.status == "active"
        ),
        active_plan_ids=active_plans,
        active_life_arc_ids=tuple(
            item.arc_id for item in projection.life_arcs if item.status == "active"
        ),
        relationship_stage=counterpart_rel.stage if counterpart_rel is not None else None,
        counterpart_subject_ref=counterpart_rel.subject_ref if counterpart_rel is not None else None,
        npc_relationship_count=npc_rel,
        active_memory_count=sum(
            1 for item in projection.memory_candidates if item.values.status == "active"
        ),
        fact_count=len(projection.facts),
        settled_occurrence_count=sum(
            1 for item in projection.world_occurrences if item.status == "settled"
        ),
        experience_count=len(projection.experiences),
        has_character_core=projection.character_core is not None,
        active_non_sleep_plan_ids=non_sleep,
        interaction_act_count=len(projection.interaction_acts),
        notes={
            "photo_candidate_total": len(projection.photo_candidates),
            "photo_in_hand": len(available_ids),
            "message_observations": len(projection.message_observations),
            "observation_events_resolved": len(counterpart),
            "expression_beats": len(projection.expression_beats),
            "stored_message_payloads": len(projection.stored_message_payloads),
            "sidecar_table_counts": sidecar_counts,
        },
    )
