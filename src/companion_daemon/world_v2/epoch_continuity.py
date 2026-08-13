"""Compile a bounded continuity snapshot and hydrate it at WorldStarted.

The snapshot is what she remembers — facts, relationships, active feeling,
open threads — not TriggerProcess leases, clock history, or model audits.
Imported authorities are rebound to the new WorldStarted so the new ledger
never reuses the archive's observation idempotency namespace.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import json

from .schema_core import FrozenModel
from .schemas import (
    AffectEpisodeProjection,
    AppraisalProjection,
    CharacterCoreProjection,
    CommitmentProjection,
    EvidenceRef,
    ExperienceProjection,
    FactProjection,
    LifeArcProjection,
    MemoryCandidateProjection,
    NpcProjection,
    PrivateImpressionProjection,
    RelationshipStateProjection,
    ThreadProjection,
    fact_semantic_fingerprint,
)


EPOCH_CONTINUITY_VERSION = "epoch-continuity.1"
_EXPERIENCE_WINDOW = timedelta(days=30)
_FACT_LIMIT = 64
_MEMORY_LIMIT = 32
_IMPRESSION_LIMIT = 16
_APPRAISAL_LIMIT = 16
_AFFECT_LIMIT = 16
_THREAD_LIMIT = 16
_COMMITMENT_LIMIT = 16
_EXPERIENCE_LIMIT = 24
_NPC_LIMIT = 32

_PROCESS_FIELDS = (
    "trigger_processes",
    "completed_trigger_ids",
    "clock_transition_history",
    "model_result_audits",
    "proposal_audits",
    "observation_refs",
)


class ContinuitySnapshot(FrozenModel):
    epoch_id: str
    archive_world_id: str
    archive_head_hash: str
    archive_world_revision: int
    continuity_version: str = EPOCH_CONTINUITY_VERSION
    facts: tuple[dict[str, Any], ...] = ()
    memory_candidates: tuple[dict[str, Any], ...] = ()
    relationship_states: tuple[dict[str, Any], ...] = ()
    affect_episodes: tuple[dict[str, Any], ...] = ()
    appraisals: tuple[dict[str, Any], ...] = ()
    threads: tuple[dict[str, Any], ...] = ()
    commitments: tuple[dict[str, Any], ...] = ()
    experiences: tuple[dict[str, Any], ...] = ()
    life_arcs: tuple[dict[str, Any], ...] = ()
    npcs: tuple[dict[str, Any], ...] = ()
    private_impressions: tuple[dict[str, Any], ...] = ()
    character_core: dict[str, Any] | None = None


def _hydrate[T](model: type[T], raw: dict[str, Any]) -> T:
    return model.model_validate_json(json.dumps(raw, ensure_ascii=False))


def _dump(item: object) -> dict[str, Any]:
    dumped = item.model_dump(mode="json")  # type: ignore[attr-defined]
    if not isinstance(dumped, dict):
        raise TypeError("continuity item must dump to an object")
    return dumped


def _status(item: object) -> str:
    values = getattr(item, "values", None)
    if values is not None:
        return str(getattr(values, "status", "") or "")
    return str(getattr(item, "status", "") or "")


def _updated_at(item: object) -> datetime:
    for name in ("updated_at", "committed_at", "accepted_at", "opened_at"):
        value = getattr(item, name, None)
        if isinstance(value, datetime):
            return value
        nested = getattr(getattr(item, "values", None), name, None)
        if isinstance(nested, datetime):
            return nested
    return datetime.min


def compile_continuity_snapshot(
    projection: object,
    *,
    epoch_id: str,
    logical_time: datetime | None = None,
) -> ContinuitySnapshot:
    """Compile remembered state. Process/audit/observation fields are dropped."""

    if set(ContinuitySnapshot.model_fields) & set(_PROCESS_FIELDS):
        raise RuntimeError("continuity snapshot must not carry process tables")
    at = logical_time or getattr(projection, "logical_time", None)
    facts = tuple(
        item
        for item in getattr(projection, "facts", ())
        if _status(item) == "active"
    )
    facts = tuple(sorted(facts, key=_updated_at, reverse=True)[:_FACT_LIMIT])
    memories = tuple(
        item
        for item in getattr(projection, "memory_candidates", ())
        if _status(item) == "active"
    )
    memories = tuple(sorted(memories, key=_updated_at, reverse=True)[:_MEMORY_LIMIT])
    affect = tuple(
        item
        for item in getattr(projection, "affect_episodes", ())
        if getattr(item, "status", None) == "active"
    )[:_AFFECT_LIMIT]
    appraisals = tuple(
        item
        for item in getattr(projection, "appraisals", ())
        if getattr(item, "status", None) == "active"
    )[:_APPRAISAL_LIMIT]
    threads = tuple(
        item
        for item in getattr(projection, "threads", ())
        if _status(item) == "open"
    )[:_THREAD_LIMIT]
    commitments = tuple(
        item
        for item in getattr(projection, "commitments", ())
        if _status(item) == "open"
    )[:_COMMITMENT_LIMIT]
    experiences = tuple(getattr(projection, "experiences", ()) or ())
    if isinstance(at, datetime):
        floor = at - _EXPERIENCE_WINDOW
        experiences = tuple(
            item
            for item in experiences
            if _updated_at(item) >= floor
        )
    experiences = tuple(sorted(experiences, key=_updated_at, reverse=True)[:_EXPERIENCE_LIMIT])
    impressions = tuple(
        item
        for item in getattr(projection, "private_impressions", ())
        if getattr(item, "status", None) == "active"
    )[:_IMPRESSION_LIMIT]
    npcs = tuple(
        item
        for item in getattr(projection, "npcs", ())
        if getattr(item, "status", None) == "active"
    )[:_NPC_LIMIT]
    arcs = tuple(
        item
        for item in getattr(projection, "life_arcs", ())
        if getattr(item, "status", None) in {"active", "open", None}
    )
    core = getattr(projection, "character_core", None)
    return ContinuitySnapshot(
        epoch_id=epoch_id,
        archive_world_id=str(getattr(projection, "world_id", "")),
        archive_head_hash=str(getattr(projection, "semantic_hash", "") or ""),
        archive_world_revision=int(getattr(projection, "world_revision", 0) or 0),
        facts=tuple(_dump(item) for item in facts),
        memory_candidates=tuple(_dump(item) for item in memories),
        relationship_states=tuple(
            _dump(item) for item in getattr(projection, "relationship_states", ())
        ),
        affect_episodes=tuple(_dump(item) for item in affect),
        appraisals=tuple(_dump(item) for item in appraisals),
        threads=tuple(_dump(item) for item in threads),
        commitments=tuple(_dump(item) for item in commitments),
        experiences=tuple(_dump(item) for item in experiences),
        life_arcs=tuple(_dump(item) for item in arcs),
        npcs=tuple(_dump(item) for item in npcs),
        private_impressions=tuple(_dump(item) for item in impressions),
        character_core=_dump(core) if core is not None else None,
    )


def _genesis_evidence(event_id: str, payload_hash: str) -> EvidenceRef:
    return EvidenceRef(
        ref_id=event_id,
        evidence_type="committed_world_event",
        claim_purpose="current_fact",
        source_world_revision=1,
        immutable_hash=payload_hash,
    )


def _rebind_fact(
    raw: dict[str, Any],
    *,
    genesis_event_id: str,
    genesis: EvidenceRef,
) -> FactProjection:
    fact = _hydrate(FactProjection, raw)
    origin = fact.origin.model_copy(update={"accepted_event_ref": genesis_event_id})
    values = fact.values.model_copy(
        update={
            "source_evidence_refs": _rebind_evidence(
                fact.values.source_evidence_refs, genesis=genesis
            ),
            "anchor_evidence_refs": _rebind_evidence(
                fact.values.anchor_evidence_refs, genesis=genesis
            ),
        }
    )
    fingerprint = fact_semantic_fingerprint(
        subject_ref=values.subject_ref,
        predicate_code=values.predicate_code,
        cardinality=values.cardinality,
        conflict_key=values.conflict_key,
        value_hash=values.value_hash,
        assertion_binding=values.assertion_binding,
        anchor_evidence_refs=values.anchor_evidence_refs,
        policy_refs=origin.policy_refs,
    )
    return fact.model_copy(
        update={
            "origin": origin,
            "values": values,
            "semantic_fingerprint": fingerprint,
        }
    )


def _rebind_evidence(
    refs: tuple[EvidenceRef, ...], *, genesis: EvidenceRef
) -> tuple[EvidenceRef, ...]:
    rebound: list[EvidenceRef] = []
    seen: set[str] = set()
    for item in refs:
        replacement = genesis if item.evidence_type == "committed_world_event" else item
        if replacement.ref_id in seen:
            continue
        seen.add(replacement.ref_id)
        rebound.append(replacement)
    return tuple(rebound)


def apply_continuity_snapshot(
    snapshot: ContinuitySnapshot,
    *,
    genesis_event_id: str,
    genesis_payload_hash: str,
    logical_time: datetime,
) -> dict[str, object]:
    """Hydrate remembered fields. Process tables stay empty."""

    genesis = _genesis_evidence(genesis_event_id, genesis_payload_hash)
    facts = []
    for raw in snapshot.facts:
        facts.append(
            _rebind_fact(raw, genesis_event_id=genesis_event_id, genesis=genesis)
        )
    affect = []
    for raw in snapshot.affect_episodes:
        episode = _hydrate(AffectEpisodeProjection, raw)
        origin = episode.origin.model_copy(update={"accepted_event_ref": genesis_event_id})
        affect.append(
            episode.model_copy(
                update={
                    "origin": origin,
                    "evidence_refs": _rebind_evidence(episode.evidence_refs, genesis=genesis),
                }
            )
        )
    appraisals = []
    for raw in snapshot.appraisals:
        item = _hydrate(AppraisalProjection, raw)
        origin = item.origin.model_copy(update={"accepted_event_ref": genesis_event_id})
        appraisals.append(
            item.model_copy(
                update={
                    "origin": origin,
                    "evidence_refs": _rebind_evidence(item.evidence_refs, genesis=genesis),
                }
            )
        )
    memories = tuple(_hydrate(MemoryCandidateProjection, raw) for raw in snapshot.memory_candidates)
    relationships = tuple(
        _hydrate(RelationshipStateProjection, raw) for raw in snapshot.relationship_states
    )
    threads = tuple(_hydrate(ThreadProjection, raw) for raw in snapshot.threads)
    commitments = tuple(_hydrate(CommitmentProjection, raw) for raw in snapshot.commitments)
    experiences = tuple(_hydrate(ExperienceProjection, raw) for raw in snapshot.experiences)
    life_arcs = tuple(_hydrate(LifeArcProjection, raw) for raw in snapshot.life_arcs)
    npcs = tuple(_hydrate(NpcProjection, raw) for raw in snapshot.npcs)
    impressions = tuple(
        _hydrate(PrivateImpressionProjection, raw) for raw in snapshot.private_impressions
    )
    core = (
        _hydrate(CharacterCoreProjection, snapshot.character_core)
        if snapshot.character_core is not None
        else None
    )
    del logical_time
    return {
        "facts": tuple(facts),
        "memory_candidates": memories,
        "relationship_states": relationships,
        "affect_episodes": tuple(affect),
        "appraisals": tuple(appraisals),
        "threads": threads,
        "commitments": commitments,
        "experiences": experiences,
        "life_arcs": life_arcs,
        "npcs": npcs,
        "private_impressions": impressions,
        "character_core": core,
        "trigger_processes": (),
        "completed_trigger_ids": (),
        "clock_transition_history": (),
        "model_result_audits": (),
        "proposal_audits": (),
        "observation_refs": (),
    }


__all__ = [
    "ContinuitySnapshot",
    "EPOCH_CONTINUITY_VERSION",
    "apply_continuity_snapshot",
    "compile_continuity_snapshot",
]
