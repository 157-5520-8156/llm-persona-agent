"""Conversation-adjacent media occasions and source-bound candidate facts.

This module does not decide whether she shares a photo.  It only answers two
host questions:

1. Is now a moment when the already-opened candidate may be *offered*?
2. Which ledger-closed world facts may she see while choosing?

Occasions are built from her own structured ``media_request`` field, open
threads whose evidence overlaps the candidate, and a live conversation:
either he spoke recently relative to *now*, or a candidate opened around
when he last spoke.  Counterpart message *text* is never inspected.  Lived facts keep ``source_ref`` and a privacy class so they can
be dropped the same way Inner Life materials are redacted; the host does not
author a "reason to share".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from .event_ecology_media import occurrence_world_fact_slice
from .image_evidence_contract import ImageEvidenceDeclaredPayload
from .private_image_evidence_contract import RecipientScopedImageEvidenceDeclaredPayload
from .schema_core import FrozenModel, PrivacyClass


CONVERSATION_OCCASION_WINDOW = timedelta(hours=2)
_TEXT_LIMIT = 240
_PRIVACY_RANK = {
    "public": 0,
    "shareable": 1,
    "personal": 2,
    "private": 3,
    "withhold": 4,
}
OccasionKind = Literal[
    "her_media_request",
    "open_thread_overlap",
    "live_conversation_fresh_candidate",
]


class CandidateOccasion(FrozenModel):
    """Host-only timing gate.  Never copied into the model-facing payload."""

    kind: OccasionKind
    source_refs: tuple[str, ...] = ()


def privacy_allows(privacy: object, *, ceiling: object) -> bool:
    """Keep a fact only when it is no more private than the candidate ceiling."""

    if privacy not in _PRIVACY_RANK or ceiling not in _PRIVACY_RANK:
        return False
    if privacy == "withhold":
        return False
    return _PRIVACY_RANK[privacy] <= _PRIVACY_RANK[ceiling]


def is_counterpart_actor(actor: object, *, character_actor_ref: str) -> bool:
    """Counterpart identity is the ``user:`` actor namespace, not message text."""

    if not isinstance(actor, str) or not actor or actor == character_actor_ref:
        return False
    return actor.startswith("user:")


def clip_fact_text(value: object, *, limit: int = _TEXT_LIMIT) -> str | None:
    if not isinstance(value, str):
        return None
    clipped = " ".join(value.split()).strip()
    if not clipped:
        return None
    return clipped[:limit]


def _observation_keys(obs: object) -> tuple[str, ...]:
    keys: list[str] = []
    for attr in ("source_event_id", "observation_id"):
        value = getattr(obs, attr, None)
        if isinstance(value, str) and value:
            keys.append(value)
    return tuple(keys)


def _event_matches_observation(event_id: str, keys: tuple[str, ...]) -> bool:
    """QQ coalesced ids live inside the WorldEvent id; this is identity, not text."""

    if not event_id:
        return False
    for key in keys:
        if event_id == key:
            return True
        if len(key) >= 24 and key in event_id:
            return True
    return False


def counterpart_last_spoke_at(
    projection: object, *, character_actor_ref: str
) -> datetime | None:
    counterpart = tuple(
        obs
        for obs in getattr(projection, "message_observations", ())
        if is_counterpart_actor(getattr(obs, "actor", None), character_actor_ref=character_actor_ref)
    )
    if not counterpart:
        return None
    keyed = tuple((obs, _observation_keys(obs)) for obs in counterpart)
    hashes = {
        getattr(obs, "event_payload_hash", None)
        for obs in counterpart
        if isinstance(getattr(obs, "event_payload_hash", None), str)
        and len(getattr(obs, "event_payload_hash", "")) >= 64
    }
    latest: datetime | None = None
    for ref in getattr(projection, "committed_world_event_refs", ()):
        event_id = getattr(ref, "event_id", "") or ""
        payload_hash = getattr(ref, "payload_hash", None)
        matched = payload_hash in hashes or any(
            _event_matches_observation(event_id, keys) for _obs, keys in keyed
        )
        if not matched:
            continue
        at = getattr(ref, "logical_time", None)
        if isinstance(at, datetime) and (latest is None or at > latest):
            latest = at
    return latest


def _thread_evidence_refs(thread: object) -> set[str]:
    values = getattr(thread, "values", None)
    refs: set[str] = set()
    if values is not None:
        for field in ("source_evidence_refs", "anchor_evidence_refs"):
            for item in getattr(values, field, ()) or ():
                ref_id = getattr(item, "ref_id", None)
                if isinstance(ref_id, str) and ref_id:
                    refs.add(ref_id)
        origin = getattr(thread, "origin", None)
        accepted = getattr(origin, "accepted_event_ref", None) if origin is not None else None
        if isinstance(accepted, str) and accepted:
            refs.add(accepted)
    for item in getattr(thread, "source_refs", ()) or ():
        if isinstance(item, str) and item:
            refs.add(item)
    return refs


def compile_candidate_occasion(
    *,
    projection: object,
    candidate: object,
    logical_time: datetime,
    character_actor_ref: str,
) -> CandidateOccasion | None:
    """Return one conversation-adjacent occasion, or None to withhold the ask.

    A missing occasion is not a decline.  The candidate stays available.
    """

    source_refs = {
        getattr(item, "event_ref", None) or item
        for item in getattr(candidate, "source_events", ()) or ()
    }
    source_refs.update(getattr(candidate, "source_event_refs", ()) or ())
    source_refs = {item for item in source_refs if isinstance(item, str) and item}

    for process in getattr(projection, "trigger_processes", ()):
        if getattr(process, "process_kind", None) != "media_request":
            continue
        if getattr(process, "state", None) == "terminal":
            continue
        evidence = getattr(process, "source_evidence_ref", None)
        refs = (evidence,) if isinstance(evidence, str) and evidence else ()
        return CandidateOccasion(kind="her_media_request", source_refs=refs)

    committed = {
        getattr(item, "event_id", None): item
        for item in getattr(projection, "committed_world_event_refs", ())
    }
    for manifest in getattr(projection, "expression_plan_manifests", ()):
        if getattr(manifest, "media_request", "none") != "consider_available_candidate":
            continue
        acceptance_ref = getattr(manifest, "acceptance_event_ref", None)
        accepted = committed.get(acceptance_ref)
        accepted_at = getattr(accepted, "logical_time", None) if accepted is not None else None
        if not isinstance(accepted_at, datetime):
            continue
        if logical_time - accepted_at > CONVERSATION_OCCASION_WINDOW:
            continue
        refs = (acceptance_ref,) if isinstance(acceptance_ref, str) else ()
        return CandidateOccasion(kind="her_media_request", source_refs=refs)

    for thread in (
        *getattr(projection, "threads", ()),
        *getattr(projection, "conversation_threads", ()),
    ):
        values = getattr(thread, "values", None)
        status = (
            getattr(values, "status", None)
            if values is not None
            else getattr(thread, "status", None)
        )
        if status != "open":
            continue
        privacy = getattr(values, "privacy_class", None) if values is not None else "shareable"
        if privacy == "withhold":
            continue
        overlap = _thread_evidence_refs(thread) & source_refs
        if not overlap:
            continue
        origin = getattr(thread, "origin", None)
        origin_ref = getattr(origin, "accepted_event_ref", None) if origin is not None else None
        thread_id = getattr(thread, "thread_id", None)
        refs = tuple(
            item
            for item in (origin_ref, thread_id, *sorted(overlap))
            if isinstance(item, str) and item
        )
        return CandidateOccasion(kind="open_thread_overlap", source_refs=refs)

    last_spoke = counterpart_last_spoke_at(
        projection, character_actor_ref=character_actor_ref
    )
    opened_at = getattr(candidate, "opened_at", None)
    live_now = (
        isinstance(last_spoke, datetime)
        and abs(logical_time - last_spoke) <= CONVERSATION_OCCASION_WINDOW
    )
    opened_around_speech = (
        isinstance(last_spoke, datetime)
        and isinstance(opened_at, datetime)
        and abs(last_spoke - opened_at) <= CONVERSATION_OCCASION_WINDOW
    )
    if live_now or opened_around_speech:
        spoken_ref = None
        for obs in getattr(projection, "message_observations", ()):
            if not is_counterpart_actor(
                getattr(obs, "actor", None), character_actor_ref=character_actor_ref
            ):
                continue
            keys = _observation_keys(obs)
            for ref in getattr(projection, "committed_world_event_refs", ()):
                event_id = getattr(ref, "event_id", "") or ""
                if (
                    _event_matches_observation(event_id, keys)
                    and getattr(ref, "logical_time", None) == last_spoke
                ):
                    spoken_ref = event_id
                    break
            if spoken_ref:
                break
        refs = tuple(
            item
            for item in (spoken_ref, getattr(candidate, "opened_event_ref", None))
            if isinstance(item, str) and item
        )
        return CandidateOccasion(
            kind="live_conversation_fresh_candidate", source_refs=refs
        )
    return None


def _plain_mapping(value: object, *, keys: tuple[str, ...]) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, object] = {}
    for key in keys:
        item = value.get(key)
        if isinstance(item, str):
            clipped = clip_fact_text(item)
            if clipped is not None:
                out[key] = clipped
        elif isinstance(item, (int, float, bool)):
            out[key] = item
    return out


def _lookup_declared_evidence(ledger: object, event_ref: str, payload_hash: str) -> object | None:
    lookup = getattr(ledger, "lookup_event_commit", None)
    if not callable(lookup):
        return None
    located = lookup(event_ref)
    if located is None:
        return None
    event, _commit = located
    if getattr(event, "payload_hash", None) != payload_hash:
        return None
    event_type = getattr(event, "event_type", None)
    payload_json = getattr(event, "payload_json", None)
    if not isinstance(payload_json, str):
        return None
    try:
        if event_type == "ImageEvidenceDeclared":
            return ImageEvidenceDeclaredPayload.model_validate_json(payload_json)
        if event_type == "RecipientScopedImageEvidenceDeclared":
            return RecipientScopedImageEvidenceDeclaredPayload.model_validate_json(
                payload_json
            )
    except ValueError:
        return None
    return None


def compile_lived_facts(
    *,
    projection: object,
    candidate: object,
    ledger: object | None = None,
    material_reader: object | None = None,
    character_actor_ref: str = "",
) -> tuple[dict[str, object], ...]:
    """Source-closed facts she may judge.  No host-authored share rationale."""

    ceiling = getattr(candidate, "privacy_ceiling", None)
    source_refs = {
        getattr(item, "event_ref", None)
        for item in getattr(candidate, "source_events", ()) or ()
    }
    source_refs.update(getattr(candidate, "source_event_refs", ()) or ())
    source_refs = {item for item in source_refs if isinstance(item, str) and item}
    facts: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def add(fact: dict[str, object]) -> None:
        source_ref = fact.get("source_ref")
        kind = fact.get("kind")
        privacy = fact.get("privacy")
        if not isinstance(source_ref, str) or not source_ref:
            return
        if not isinstance(kind, str) or not kind:
            return
        if not privacy_allows(privacy, ceiling=ceiling):
            return
        key = (kind, source_ref)
        if key in seen:
            return
        seen.add(key)
        facts.append(fact)

    if ledger is not None:
        for source in getattr(candidate, "source_events", ()) or ():
            event_ref = getattr(source, "event_ref", None)
            payload_hash = getattr(source, "payload_hash", None)
            if not isinstance(event_ref, str) or not isinstance(payload_hash, str):
                continue
            declaration = _lookup_declared_evidence(ledger, event_ref, payload_hash)
            if declaration is None:
                continue
            evidence = getattr(declaration, "image_evidence", None)
            if evidence is None:
                continue
            visibility = getattr(evidence, "visibility", None)
            activity = getattr(evidence, "activity", None)
            location = getattr(evidence, "location", None)
            situation = getattr(evidence, "situational_context", None)
            fact: dict[str, object] = {
                "kind": "image_evidence",
                "source_ref": event_ref,
                "privacy": visibility,
            }
            summary = clip_fact_text(getattr(evidence, "summary", None))
            outcome = clip_fact_text(getattr(evidence, "outcome", None))
            if summary:
                fact["summary"] = summary
            if outcome:
                fact["outcome"] = outcome
            activity_slice = _plain_mapping(
                activity if isinstance(activity, dict) else (
                    activity.model_dump(mode="json") if hasattr(activity, "model_dump") else None
                ),
                keys=("kind", "description"),
            )
            kind = activity_slice.get("kind")
            if isinstance(kind, str) and ("." in kind or len(kind) > 48):
                # Catalog opening tokens are authority handles, not lived facts.
                activity_slice.pop("kind", None)
            if activity_slice:
                fact["activity"] = activity_slice
            location_slice = _plain_mapping(
                location if isinstance(location, dict) else (
                    location.model_dump(mode="json") if hasattr(location, "model_dump") else None
                ),
                keys=("kind", "publicness"),
            )
            if location_slice:
                fact["location"] = location_slice
            if situation is not None:
                dumped = (
                    situation.model_dump(mode="json", exclude_none=True)
                    if hasattr(situation, "model_dump")
                    else situation if isinstance(situation, dict) else {}
                )
                situation_slice = _plain_mapping(
                    dumped, keys=("season", "academic_phase")
                )
                if situation_slice:
                    fact["situation"] = situation_slice
            add(fact)

    for occurrence in getattr(projection, "world_occurrences", ()):
        settlement = getattr(occurrence, "settlement_event_ref", None)
        trigger = getattr(occurrence, "trigger_ref", None)
        if settlement not in source_refs and trigger not in source_refs:
            continue
        if getattr(occurrence, "status", None) != "settled":
            continue
        source_ref = settlement if isinstance(settlement, str) else trigger
        if not isinstance(source_ref, str):
            continue
        fact = {
            "kind": "settled_occurrence",
            "source_ref": source_ref,
            "privacy": getattr(occurrence, "visibility", None),
        }
        for key, value in occurrence_world_fact_slice(occurrence).items():
            if key == "settled_at" and hasattr(value, "isoformat"):
                fact["settled_at"] = value.isoformat()
            elif key == "participant_refs":
                fact["participant_refs"] = list(value)
            elif key in {"location_ref", "settled_outcome_ref"} and isinstance(value, str):
                fact[key] = value
        participants = tuple(fact.get("participant_refs") or ())
        if any(
            is_counterpart_actor(item, character_actor_ref=character_actor_ref)
            for item in participants
        ):
            add(
                {
                    "kind": "shared_participant",
                    "source_ref": source_ref,
                    "privacy": getattr(occurrence, "visibility", None),
                    "participant_kind": "counterpart",
                }
            )
        if material_reader is not None:
            read = getattr(material_reader, "read_for_occurrence", None)
            if callable(read):
                try:
                    material = read(occurrence=occurrence)
                except Exception:
                    material = None
                outcomes = getattr(material, "outcomes", ()) if material is not None else ()
                if outcomes:
                    text = clip_fact_text(getattr(outcomes[0], "text", None))
                    if text:
                        fact["what_happened"] = text
        add(fact)

    for thread in (
        *getattr(projection, "threads", ()),
        *getattr(projection, "conversation_threads", ()),
    ):
        values = getattr(thread, "values", None)
        status = (
            getattr(values, "status", None)
            if values is not None
            else getattr(thread, "status", None)
        )
        if status != "open":
            continue
        privacy = (
            getattr(values, "privacy_class", "shareable")
            if values is not None
            else "shareable"
        )
        overlap = _thread_evidence_refs(thread) & source_refs
        if not overlap:
            continue
        origin = getattr(thread, "origin", None)
        source_ref = getattr(origin, "accepted_event_ref", None) if origin is not None else None
        if not isinstance(source_ref, str) or not source_ref:
            source_ref = next(iter(sorted(overlap)))
        kind = getattr(getattr(thread, "values", thread), "kind", None)
        fact = {
            "kind": "open_thread",
            "source_ref": source_ref,
            "privacy": privacy,
            "status": "open",
        }
        if isinstance(kind, str) and kind:
            fact["thread_kind"] = kind
        add(fact)

    committed = {
        getattr(item, "event_id", None): item
        for item in getattr(projection, "committed_world_event_refs", ())
    }
    logical_time = getattr(projection, "logical_time", None)
    for manifest in getattr(projection, "expression_plan_manifests", ()):
        if getattr(manifest, "media_request", "none") != "consider_available_candidate":
            continue
        acceptance_ref = getattr(manifest, "acceptance_event_ref", None)
        if not isinstance(acceptance_ref, str):
            continue
        accepted = committed.get(acceptance_ref)
        accepted_at = getattr(accepted, "logical_time", None) if accepted is not None else None
        if (
            isinstance(logical_time, datetime)
            and isinstance(accepted_at, datetime)
            and logical_time - accepted_at > CONVERSATION_OCCASION_WINDOW
        ):
            continue
        add(
            {
                "kind": "her_media_request",
                "source_ref": acceptance_ref,
                "privacy": "shareable",
                "media_request": "consider_available_candidate",
            }
        )

    for process in getattr(projection, "trigger_processes", ()):
        if getattr(process, "process_kind", None) != "media_request":
            continue
        if getattr(process, "state", None) == "terminal":
            continue
        evidence = getattr(process, "source_evidence_ref", None)
        if not isinstance(evidence, str) or not evidence:
            continue
        add(
            {
                "kind": "her_media_request",
                "source_ref": evidence,
                "privacy": "shareable",
                "media_request": "consider_available_candidate",
            }
        )

    for fact in compile_text_cross_lane_facts(
        projection,
        logical_time=logical_time if isinstance(logical_time, datetime) else None,
    ):
        add(fact)

    return tuple(facts)


def compile_text_cross_lane_facts(
    projection: object,
    *,
    logical_time: datetime | None,
) -> tuple[dict[str, object], ...]:
    """Text-lane timing facts visible while she chooses a photo.

    Source-closed excerpts only; the host does not infer intent from wording.
    """

    from .later_expression_freshness import queued_later_facts
    from .response_expectation_view import (
        living_unanswered_hope,
        pending_response_expectation,
    )

    facts: list[dict[str, object]] = []
    at = logical_time or getattr(projection, "logical_time", None)
    try:
        hope = living_unanswered_hope(projection)
    except (TypeError, ValueError, AttributeError):
        hope = None
    if hope is not None:
        excerpt = clip_fact_text(hope.hoped_response, limit=96)
        if excerpt:
            facts.append(
                {
                    "kind": "text_lane_hope",
                    "source_ref": f"hope:wr:{hope.declared_world_revision}",
                    "privacy": "private",
                    "hoped_response": excerpt,
                    "declared_seconds_ago": hope.declared_seconds_ago,
                }
            )
    try:
        pending = pending_response_expectation(projection)
    except (TypeError, ValueError, AttributeError):
        pending = None
    if pending is not None and hope is None:
        excerpt = clip_fact_text(pending.hoped_response, limit=96)
        if excerpt:
            facts.append(
                {
                    "kind": "text_lane_expectation",
                    "source_ref": "response-expectation:pending",
                    "privacy": "private",
                    "hoped_response": excerpt,
                    "declared_seconds_ago": pending.declared_seconds_ago,
                }
            )
    for item in queued_later_facts(projection, logical_time=at):
        excerpt = clip_fact_text(item.text, limit=120)
        if not excerpt:
            continue
        facts.append(
            {
                "kind": "text_lane_queued_message",
                "source_ref": item.authority_event_ref,
                "privacy": "private",
                "text": excerpt,
                "send_at": item.send_at.isoformat(),
                "he_spoke_after": item.he_spoke_after,
                "i_spoke_after": item.i_spoke_after,
            }
        )
    return tuple(facts)


def safe_summary_from_lived_facts(facts: tuple[dict[str, object], ...]) -> str:
    """Derive the short label *after* privacy filtering, like lived_moment."""

    base = "一件已确认、可选择但不必分享的生活事件"
    for fact in facts:
        kind = fact.get("kind")
        text = None
        if kind == "image_evidence":
            text = fact.get("summary") or fact.get("outcome")
            activity = fact.get("activity")
            if not text and isinstance(activity, dict):
                text = activity.get("description")
        elif kind == "settled_occurrence":
            text = fact.get("what_happened")
        clipped = clip_fact_text(text, limit=160)
        if clipped:
            return f"{base}｜具体发生：{clipped}"
    return base


__all__ = [
    "CONVERSATION_OCCASION_WINDOW",
    "CandidateOccasion",
    "clip_fact_text",
    "compile_candidate_occasion",
    "compile_lived_facts",
    "compile_text_cross_lane_facts",
    "counterpart_last_spoke_at",
    "is_counterpart_actor",
    "privacy_allows",
    "safe_summary_from_lived_facts",
]
