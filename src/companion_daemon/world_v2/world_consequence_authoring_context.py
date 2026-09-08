"""Bounded, readable execution context from an already-pinned author manifest.

This module does not expand the manifest, choose an activity, or decide what
its consequence should be. Original model/request audit proof belongs to the
caller; replay validation here proves the exact same pinned readable inputs.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from .schema_core import FrozenModel
from .world_consequence_contract import (
    WORLD_CONSEQUENCE_CONTRACT,
    WorldConsequenceAuthority,
    WorldConsequenceAuthorityError,
    derive_world_consequence_authority,
)
from .world_consequence_execution_context import (
    WorldConsequenceExecutionMaterial,
    build_world_consequence_execution_materials,
)


class WorldConsequenceAuthoringContext(FrozenModel):
    authority: WorldConsequenceAuthority
    execution_materials: tuple[WorldConsequenceExecutionMaterial, ...] = Field(max_length=4)

    @model_validator(mode="after")
    def authority_matches_only_readable_materials(self):
        if any(item.status != "available" for item in self.execution_materials):
            raise ValueError("authoring context cannot offer unavailable execution material")
        if self.authority.execution_bindings != tuple(
            item.execution_binding for item in self.execution_materials
        ):
            raise ValueError("authoring context authority differs from its readable materials")
        return self


def build_world_consequence_authoring_context(
    *, ledger, content_store, manifest, actor_ref: str,
) -> WorldConsequenceAuthoringContext:
    """Offer at most four available sources, newest first, from anchor_refs only.

    A missing anchor is unavailable to this request even if another accepted
    Plan exists in the ledger. Non-execution, private-to-another-actor, withheld,
    unreadable and uncertain sources provide no execution permission. Empty
    authority remains valid for a purely environmental consequence.
    """
    from .life_development_draft import LifeDevelopmentCapabilityManifest

    manifest = LifeDevelopmentCapabilityManifest.model_validate_json(
        manifest.model_dump_json(exclude_computed_fields=True)
    )
    if manifest.outcome_contract != WORLD_CONSEQUENCE_CONTRACT:
        raise ValueError("world consequence authoring requires the explicit .2 manifest")
    if manifest.owner_actor_ref != actor_ref:
        raise ValueError("world consequence authoring actor differs from manifest owner")
    pinned = ledger.project_at(manifest.pinned_cursor)
    if pinned.world_id != ledger.world_id or any((
        pinned.world_revision != manifest.pinned_cursor.world_revision,
        pinned.deliberation_revision != manifest.pinned_cursor.deliberation_revision,
        pinned.ledger_sequence != manifest.pinned_cursor.ledger_sequence,
    )):
        raise ValueError("world consequence authoring projection differs from manifest pin")
    anchors = set(manifest.anchor_refs)
    candidates = sorted(
        (source for source in pinned.committed_world_event_refs
         if source.event_id in anchors and source.event_type in {
             "ActivityStarted", "ActivityResumed", "ExecutionReceiptRecorded",
         }),
        key=lambda source: (-source.world_revision, source.event_id),
    )
    available = []
    selected_events = []
    for source in candidates:
        found = ledger.lookup_event_commit(source.event_id)
        if found is None:
            continue
        event, commit = found
        if any((
            event.event_id != source.event_id,
            event.world_id != pinned.world_id,
            event.event_type != source.event_type,
            event.payload_hash != source.payload_hash,
            event.logical_time != source.logical_time,
            event.event_id not in commit.event_ids,
            commit.world_revision < source.world_revision,
            commit.world_revision > pinned.world_revision,
            commit.deliberation_revision > pinned.deliberation_revision,
            commit.ledger_sequence > pinned.ledger_sequence,
        )):
            raise ValueError("world consequence authoring source is not its exact pinned event")
        try:
            material = build_world_consequence_execution_materials(
                ledger=ledger, content_store=content_store, pinned_state=pinned,
                actor_ref=actor_ref, source_events=(event,),
            )[0]
        except WorldConsequenceAuthorityError as exc:
            # These are typed domain exclusions, not judgments about prose.
            # Integrity/request errors continue to fail closed to the caller.
            if exc.code not in {
                "world_consequence.activity_actor_or_plan",
                "world_consequence.receipt_action_binding",
                "world_consequence.receipt_not_observed_result",
            }:
                raise
            continue
        if material.status != "available":
            continue
        available.append(material)
        selected_events.append(event)
        if len(available) == 4:
            break
    authority = derive_world_consequence_authority(
        pinned_state=pinned, actor_ref=actor_ref, source_events=tuple(selected_events),
    )
    return WorldConsequenceAuthoringContext(
        authority=authority, execution_materials=tuple(available),
    )


def validate_world_consequence_authoring_context(
    *, ledger, content_store, manifest, actor_ref: str, authority, execution_materials,
) -> None:
    """Re-read the original prefix and require exact full material equality.

    Never replace a missing original source or sidecar with the current head,
    abbreviated text, another actor's activity, or a differently ranked subset.
    """
    supplied = WorldConsequenceAuthoringContext(
        authority=WorldConsequenceAuthority.model_validate_json(authority.model_dump_json()),
        execution_materials=tuple(
            WorldConsequenceExecutionMaterial.model_validate_json(item.model_dump_json())
            for item in execution_materials
        ),
    )
    expected = build_world_consequence_authoring_context(
        ledger=ledger, content_store=content_store, manifest=manifest, actor_ref=actor_ref,
    )
    if supplied != expected:
        raise ValueError("world consequence authoring context differs from original pinned materials")


__all__ = [
    "WorldConsequenceAuthoringContext", "build_world_consequence_authoring_context",
    "validate_world_consequence_authoring_context",
]
