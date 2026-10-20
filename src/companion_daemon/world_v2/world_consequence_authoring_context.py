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
    if (manifest.execution_intention_sources_version == "4"
            and manifest.active_attempt_consequence is None
            and manifest.completed_activity_consequence is None
            and manifest.event_detail_capability is None):
        # Independent World generation has no selected protagonist attempt.
        # Exact active/completed consequences retain their own original route;
        # historical anchors are facts, not an invitation to repeat an action.
        return WorldConsequenceAuthoringContext(
            authority=derive_world_consequence_authority(
                pinned_state=pinned, actor_ref=actor_ref, source_events=(),
            ),
            execution_materials=(),
        )
    anchors = set(manifest.anchor_refs)
    completion = manifest.completed_activity_consequence
    if completion is not None:
        from .completed_activity_consequence import validate_completed_activity_consequence

        validate_completed_activity_consequence(
            ledger=ledger, pinned_state=pinned, actor_ref=actor_ref, descriptor=completion,
        )
        # Other current activities can remain readable context, but they have
        # no execution authority in this request for one completed attempt.
        anchors = {completion.execution_binding.source_event_ref}
    active = manifest.active_attempt_consequence
    if active is not None:
        from .active_attempt_consequence import validate_active_attempt_consequence

        validate_active_attempt_consequence(
            ledger=ledger, content_store=content_store,
            pinned_state=pinned, actor_ref=actor_ref, descriptor=active,
            intention_reader_version=manifest.execution_intention_sources_version or "2",
        )
        anchors = {active.execution_binding.source_event_ref}
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
                intention_reader_version=manifest.execution_intention_sources_version or "1",
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
    if completion is not None and authority.execution_bindings != (completion.execution_binding,):
        raise ValueError("completed activity consequence lacks its exact readable attempt")
    if active is not None and authority.execution_bindings != (active.execution_binding,):
        raise ValueError("active attempt consequence lacks its exact readable intention")
    return WorldConsequenceAuthoringContext(
        authority=authority, execution_materials=tuple(available),
    )


def read_registered_npc_identity_sources(*, ledger, content_store, manifest):
    """Read exact registered/epoch-carried identity, never a locator as a fact."""
    import json
    from .epoch_continuity import ContinuitySnapshot
    from .life_content_store import life_content_payload_hash
    from .life_development_source_closure import REGISTERED_NPC_IDENTITY_CONTRACT
    from .schemas import NpcProjection

    if manifest.execution_intention_sources_version != "4" or not manifest.npc_capabilities:
        return None
    pinned = ledger.project_at(manifest.pinned_cursor)
    if (pinned.world_id != ledger.world_id or any(
            getattr(pinned, name) != getattr(manifest.pinned_cursor, name)
            for name in ("world_revision", "deliberation_revision", "ledger_sequence"))):
        raise ValueError("NPC identity projection differs from its original pin")
    by_ref = {"npc:" + npc.npc_id: npc for npc in pinned.npcs}
    recorded_refs = {r.event_id: r for r in pinned.committed_world_event_refs}
    citable = set(manifest.anchor_refs) | set(manifest.grounding_refs)

    def exact_source(ref, event_type):
        found = ledger.lookup_event_commit(ref)
        if found is None:
            return None
        event, commit = found
        recorded = recorded_refs.get(ref)
        if (recorded is None or recorded.event_type != event_type
                or event.event_id != ref or event.event_type != event_type
                or event.world_id != pinned.world_id
                or event.payload_hash != recorded.payload_hash
                or event.logical_time != recorded.logical_time
                or event.event_id not in commit.event_ids
                or not recorded.world_revision <= commit.world_revision <= pinned.world_revision
                or commit.deliberation_revision > pinned.deliberation_revision
                or commit.ledger_sequence > pinned.ledger_sequence):
            raise ValueError("NPC identity differs from its exact pinned source")
        return event, recorded

    epoch_sources = None
    items = []
    for cap in manifest.npc_capabilities:
        if cap.npc_ref not in manifest.entity_refs:
            raise ValueError("NPC identity differs from offered entity refs")
        npc = by_ref.get(cap.npc_ref)
        if npc is None:
            raise ValueError("NPC identity is absent from its original pinned registry")
        if npc.stable_identity_ref != cap.identity_content_ref:
            raise ValueError("NPC identity differs from its exact pinned registration")
        registration = npc.registration_event_ref
        origin = None
        kind = "npc_registration"
        if registration in citable and registration in cap.authority_refs:
            origin = exact_source(registration, "NpcRegistered")
            if origin is not None:
                source = origin[0].payload().get("npc", {})
                if (source.get("npc_id") != npc.npc_id
                        or source.get("stable_identity_ref") != cap.identity_content_ref):
                    raise ValueError("NPC identity differs from its exact pinned registration")
        if origin is None:
            # Epoch continuity carries canonical NPC definitions, not their
            # archive event rows. Prove the exact entry in a citable current
            # WorldStarted; never synthesize the absent old registration.
            if epoch_sources is None:
                epoch_sources = []
                for ref in recorded_refs.values():
                    if ref.event_type != "WorldStarted" or ref.event_id not in citable:
                        continue
                    witnessed = exact_source(ref.event_id, "WorldStarted")
                    raw = witnessed[0].payload().get("continuity") if witnessed is not None else None
                    if raw is not None:
                        snapshot = ContinuitySnapshot.model_validate_json(json.dumps(raw))
                        epoch_sources.append((witnessed, snapshot))
            matches = []
            for witnessed, snapshot in epoch_sources:
                if not (witnessed[0].event_id in cap.authority_refs
                        or registration is not None and registration in cap.authority_refs):
                    continue
                for raw in snapshot.npcs:
                    carried = NpcProjection.model_validate_json(json.dumps(raw))
                    if (carried.npc_id == npc.npc_id
                            and carried.stable_identity_ref == cap.identity_content_ref
                            and carried.registration_event_ref == registration):
                        matches.append(witnessed)
            if len(matches) > 1:
                raise ValueError("NPC identity has ambiguous epoch continuity authority")
            if not matches:
                continue
            origin = matches[0]
            kind = "epoch_continuity_npc_identity"
        if manifest.npc_privacy_floors is not None:
            floor = next((f.privacy_class for f in manifest.npc_privacy_floors
                          if f.npc_ref == cap.npc_ref), None)
            if floor != npc.privacy_class:
                raise ValueError("NPC identity differs from its pinned privacy floor")
        stored = (content_store.read_exact(content_ref=cap.identity_content_ref)
                  if content_store is not None else None)
        if stored is None:
            continue
        if (stored.content_ref != cap.identity_content_ref
                or stored.content_kind != "provisional_npc_introduction"
                or stored.text != cap.identity_summary
                or stored.content_payload_hash != cap.identity_payload_hash
                or life_content_payload_hash(stored.text) != cap.identity_payload_hash):
            raise ValueError("NPC identity differs from its exact immutable content")
        event, recorded = origin
        items.append({"npc_ref": cap.npc_ref, "identity_content_ref": cap.identity_content_ref,
            "identity_summary": stored.text, "identity_payload_hash": stored.content_payload_hash,
            "privacy_class": npc.privacy_class, "authority_refs": [event.event_id],
            "source_kind": kind, "source_event_type": event.event_type,
            "source_payload_hash": event.payload_hash, "source_world_revision": recorded.world_revision,
            "original_registration_ref": registration})
    if len({row["npc_ref"] for row in items}) != len(items):
        raise ValueError("duplicate pinned NPC identity")
    return ({"contract": REGISTERED_NPC_IDENTITY_CONTRACT,
             "scope": "exact_registered_identity_at_origin_not_presence_or_new_action",
             "manifest_hash": manifest.manifest_hash,
             "owner_actor_ref": manifest.owner_actor_ref,
             "pinned_cursor": manifest.pinned_cursor.model_dump(mode="json"),
             "items": sorted(items, key=lambda row: row["npc_ref"])} if items else None)


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
