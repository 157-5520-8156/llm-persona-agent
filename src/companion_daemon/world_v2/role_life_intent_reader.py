"""Read exact accepted role intentions and lifecycle evidence for source-specific readers.

The injected source verifier owns authorship. This reader only joins the
verified original Plan and its current accepted lifecycle head.
"""

from __future__ import annotations

import hashlib

from .life_events import ActivityPlannedPayload
from .schemas import ProjectionCursor


class RoleLifeIntentActivityReader:
    """Expose accepted intent and its exact lifecycle state, never results."""

    def __init__(self, *, ledger, plan_prefix, event_prefix, origin_field, derive, validate) -> None:
        self._ledger = ledger
        self._plan_prefix = plan_prefix
        self._event_prefix = event_prefix
        self._origin_field = origin_field
        self._derive = derive
        self._validate = validate

    def _read(self, *, plan_id, expected_cursor, actor_ref, viewer_privacy_ceiling, status):
        try:
            return self._read_context(
                status=status,
                plan_id=plan_id,
                expected_cursor=expected_cursor,
                actor_ref=actor_ref,
                viewer_privacy_ceiling=viewer_privacy_ceiling,
            )
        except (ValueError, TypeError, KeyError):
            return None

    def _read_context(self, *, plan_id, expected_cursor, actor_ref, viewer_privacy_ceiling, status):
        from .schemas import validate_plan_authority_state
        from .world_life_context import (
            AcceptedActivityIntention,
            ActiveActivityContextItem,
            CompletedActivityContextItem,
            ActiveWorldOccurrenceProposalBinding,
            WorldLifeSourceBinding,
        )

        if not plan_id.startswith(self._plan_prefix) or viewer_privacy_ceiling not in {
            "private",
            "withhold",
        }:
            return None
        projection = self._ledger.project()
        cursor = ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
        if cursor != expected_cursor:
            return None
        plan = next((x for x in projection.plans if x.plan_id == plan_id), None)
        if plan is None or plan.status != status or plan.owner_actor_ref != actor_ref:
            return None
        if plan.privacy_class == "withhold":
            return None
        validate_plan_authority_state(
            (plan,), projection.committed_world_event_refs, logical_time=projection.logical_time
        )
        authority = {x.event_id: x for x in projection.committed_world_event_refs}
        bindings = []
        planned = None
        for event_ref, event_type in (
            (self._event_prefix + plan_id.removeprefix(self._plan_prefix), "ActivityPlanned"),
            (plan.authority_origin.accepted_event_ref, plan.authority_origin.accepted_event_type),
        ):
            ref = authority.get(event_ref)
            located = self._ledger.lookup_event_commit(event_ref)
            if (
                ref is None
                or located is None
                or any(
                    (
                        ref.event_type != event_type,
                        located[0].event_type != event_type,
                        located[0].world_id != self._ledger.world_id,
                        located[0].payload_hash != ref.payload_hash,
                        located[0].logical_time != ref.logical_time,
                        located[1].ledger_sequence > cursor.ledger_sequence,
                        located[1].world_revision > cursor.world_revision,
                    )
                )
            ):
                return None
            if event_type == "ActivityPlanned":
                planned = ActivityPlannedPayload.model_validate_json(located[0].payload_json)
                self._validate(state=projection, event=located[0], payload=planned)
            elif located[0].payload().get("plan_id") != plan_id:
                return None
            bindings.append(
                WorldLifeSourceBinding(
                    authority_event_ref=event_ref,
                    authority_world_revision=ref.world_revision,
                    authority_payload_hash=ref.payload_hash,
                )
            )
        if planned is None or any(
            getattr(planned.plan, field) != getattr(plan, field)
            for field in (
                "plan_id",
                "activity_id",
                "activity_kind",
                "owner_actor_ref",
                "location_ref",
                "participant_refs",
                "privacy_class",
                "scheduled_window",
                "importance_bp",
            )
        ):
            return None
        origin = getattr(planned, self._origin_field)
        proposal = self._ledger.lookup_event_commit(origin.proposal_event_ref)
        if proposal is None or any(
            (
                proposal[0].event_type != "ProposalRecorded",
                proposal[0].world_id != self._ledger.world_id,
                proposal[0].payload_hash != origin.proposal_payload_hash,
                proposal[1].ledger_sequence > cursor.ledger_sequence,
            )
        ):
            return None
        _, intent = self._derive(
            state=projection,
            world_id=self._ledger.world_id,
            proposal_id=origin.proposal_id,
            owner_actor_ref=actor_ref,
        )
        # A concurrent advance invalidates this foreground read rather than
        # reading a lifecycle head from a different snapshot.
        head = self._ledger.project()
        if (head.world_revision, head.deliberation_revision, head.ledger_sequence) != (
            cursor.world_revision,
            cursor.deliberation_revision,
            cursor.ledger_sequence,
        ):
            return None
        item_type = (
            ActiveActivityContextItem if status == "active" else CompletedActivityContextItem
        )
        state_coordinates = (
            {
                "participant_refs": plan.participant_refs,
                "location_ref": plan.location_ref,
                "active_since": plan.authority_origin.accepted_at,
            }
            if status == "active"
            else {"ended_at": plan.authority_origin.accepted_at}
        )
        return item_type(
            **state_coordinates,
            activity_event_ref=plan.authority_origin.accepted_event_ref,
            plan_id=plan_id,
            plan_entity_revision=plan.entity_revision,
            owner_actor_ref=actor_ref,
            activity_kind=plan.activity_kind,
            privacy_class=plan.privacy_class,
            accepted_intention=AcceptedActivityIntention(
                content_ref=origin.proposal_event_ref + "#" + origin.change_id,
                content_payload_hash=hashlib.sha256(intent.intention.encode()).hexdigest(),
                text=intent.intention,
                truncated=False,
            ),
            proposal_source=ActiveWorldOccurrenceProposalBinding(
                authority_event_ref=origin.proposal_event_ref,
                authority_ledger_sequence=proposal[1].ledger_sequence,
                authority_payload_hash=origin.proposal_payload_hash,
            ),
            source_bindings=tuple(bindings),
        )
