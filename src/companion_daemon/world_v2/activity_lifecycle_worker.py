"""Explicit Life Ecology adapter for bounded activity deliberation."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from .activity_lifecycle_draft import (
    ActivityLifecycleModelDraft,
)
from .occasion import (
    OccasionSpendStore,
    mint_day_open,
    mint_life_beat,
    occasion_spend_store_for_ledger,
)
from .daily_occasion import (
    DEFAULT_LOCAL_TIMEZONE,
    DailyOccasionStore,
    daily_occasion_store_for_ledger,
    local_day_key,
)
from .activity_lifecycle_proposal import ActivityLifecycleProposalCompiler
from .activity_lifecycle_runtime import (
    ActivityLifecycleAcceptanceRuntime,
    ActivityLifecycleProposalRecorder,
)
from .character_interior import CharacterInterior, InteriorOpportunity
from .character_interior.audit import recorded_character_interior_model_result
from .character_interior.contracts import _InteriorCapabilityManifest
from .character_interior.run_result import CausalOpportunityRuntime
from .life_ecology_activity import ActivityOpeningCatalog
from .schema_core import FrozenModel
from .proposal_audit_schemas import ModelResultRecordedPayload
from .schemas import ProjectionCursor

_TIMING_MODEL = "world-v2:activity-timing"
_OCCASION_CONSUMED_FAILURES = frozenset(
    {
        "occasion_already_considered",
        "occasion_expired",
    }
)


def _timing_closure_draft(opening_token: str) -> ActivityLifecycleModelDraft:
    payload = {"decision": "select", "opening_token": opening_token}
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return ActivityLifecycleModelDraft(
        decision="opening_token",
        opening_token=opening_token,
        model=_TIMING_MODEL,
        raw_output=normalized,
        raw_output_hash=digest,
        normalized_json=normalized,
        normalized_output_hash=digest,
    )


def _openings_are_closed_window_abandons(openings: tuple) -> bool:
    """True when this wake offers only abandon, with no lived start/complete.

    Letting go of a window that already closed is not beginning the day, so it
    must not spend ``day_open``.  A mixed catalog that also offers start,
    complete, pause, or resume remains a real first chance.
    """

    return bool(openings) and all(item.operation == "abandon" for item in openings)


class ActivityLifecycleFollowupResult(FrozenModel):
    status: Literal["transitioned", "no_op", "blocked", "technical_failure"]
    reason_code: str | None = None
    proposal_event_ref: str | None = None
    character_interior_model_result: ModelResultRecordedPayload | None = None


class ActivityLifecycleWorker:
    """Turn one claimed ecology wake into at most one accepted transition.

    The worker owns orchestration only.  The model receives a safe capsule,
    the compiler derives authority, the recorder persists the proposal, and
    the acceptance runtime materializes the effect.  No fallback selection is
    made when the model declines or the catalog has no legal opening.
    """

    def __init__(
        self,
        *,
        ledger,
        catalog: ActivityOpeningCatalog,
        character_interior: CharacterInterior,
        owner_actor_ref: str,
        proposal_recorder: ActivityLifecycleProposalRecorder,
        acceptance_runtime: ActivityLifecycleAcceptanceRuntime,
        ecology_catalog_version: str,
        source: str = "world-v2:activity-lifecycle",
        daily_occasions: DailyOccasionStore | None = None,
        occasion_spends: OccasionSpendStore | None = None,
        local_timezone_name: str = DEFAULT_LOCAL_TIMEZONE,
        open_world_event=None,
        plan_material_reader=None,
    ) -> None:
        if not ecology_catalog_version or not source or not owner_actor_ref:
            raise ValueError(
                "activity lifecycle worker requires catalog version, source and owner actor"
            )
        self._ledger = ledger
        self._catalog = catalog
        self._character_interior = character_interior
        self._owner_actor_ref = owner_actor_ref
        self._proposal_recorder = proposal_recorder
        self._acceptance_runtime = acceptance_runtime
        self._compiler = ActivityLifecycleProposalCompiler(
            catalog=catalog, ecology_catalog_version=ecology_catalog_version
        )
        self._source = source
        self._daily_occasions = daily_occasions or daily_occasion_store_for_ledger(ledger)
        self._occasion_spends = occasion_spends or occasion_spend_store_for_ledger(ledger)
        self._local_timezone = ZoneInfo(local_timezone_name)
        self._open_world_event = open_world_event
        self._plan_material_reader = plan_material_reader

    async def advance_once(
        self,
        *,
        wake_event_ref: str,
        trigger_id: str,
        logical_time: datetime,
        actor: str,
        trace_id: str,
        correlation_id: str,
    ) -> ActivityLifecycleFollowupResult:
        projection = self._ledger.project()
        if projection.logical_time != logical_time:
            return ActivityLifecycleFollowupResult(
                status="blocked", reason_code="activity_lifecycle.logical_time_not_current"
            )
        cursor = ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
        catalog = self._catalog.openings_for(projection=projection, wake_event_ref=wake_event_ref)
        if catalog.status != "openings_available":
            return ActivityLifecycleFollowupResult(
                status="blocked" if catalog.status == "blocked_by_missing_capability" else "no_op",
                reason_code=catalog.reason_code or f"activity_lifecycle.{catalog.status}",
            )
        draft, failure_code = await self._character_choice(
            projection=projection,
            catalog=catalog,
            wake_event_ref=wake_event_ref,
            trigger_id=trigger_id,
        )
        if draft is None:
            return ActivityLifecycleFollowupResult(
                status="technical_failure",
                reason_code="activity_lifecycle." + (failure_code or "unknown"),
            )
        proposal = self._compiler.compile(
            projection=projection,
            wake_event_ref=wake_event_ref,
            ecology_trigger_id=trigger_id,
            draft=draft,
        )
        if proposal is None:
            reason = (
                "activity_lifecycle.day_open_already_spent"
                if draft.model is None
                else "activity_lifecycle.model_declined"
            )
            self._hitch_paid_noticed(
                draft=draft,
                wake_event_ref=wake_event_ref,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
            return ActivityLifecycleFollowupResult(
                status="no_op",
                reason_code=reason,
                character_interior_model_result=draft.character_interior_model_result,
            )
        recorded = self._proposal_recorder.record(
            cursor=cursor,
            proposal=proposal,
            actor=actor,
            source=self._source,
            created_at=logical_time,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        accepted_cursor = ProjectionCursor(
            world_revision=recorded.commit.world_revision,
            deliberation_revision=recorded.commit.deliberation_revision,
            ledger_sequence=recorded.commit.ledger_sequence,
        )
        self._acceptance_runtime.accept(
            handle=self._acceptance_runtime.pin_proposal(
                cursor=accepted_cursor, proposal_event_ref=recorded.proposal_event_ref
            ),
            actor=actor,
            source=self._source,
            logical_time=logical_time,
            created_at=logical_time,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        self._hitch_paid_noticed(
            draft=draft,
            wake_event_ref=wake_event_ref,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        return ActivityLifecycleFollowupResult(
            status="transitioned", proposal_event_ref=recorded.proposal_event_ref
        )

    async def _character_choice(
        self,
        *,
        projection,
        catalog,
        wake_event_ref: str,
        trigger_id: str,
    ) -> tuple[ActivityLifecycleModelDraft | None, str | None]:
        """Ask the sole protagonist author to choose one already-legal token.

        The activity catalog and acceptance runtime retain all plan authority.
        This method contributes only a source-bound capability view to the same
        ``CharacterInterior`` used by Chat, Proactive and open Life.
        """

        openings = catalog.openings
        day_key = local_day_key(projection.logical_time, self._local_timezone)
        first_occasion_id = f"occasion:day_open:{day_key}"
        first_chance_spent = self._daily_occasions.spent(
            "day_open", day_key
        ) or self._occasion_spends.spent(first_occasion_id)
        cause_bound = tuple(
            item for item in openings if item.opening_kind != "ordinary"
        )
        completes = tuple(item for item in openings if item.operation == "complete")
        if not openings:
            return ActivityLifecycleModelDraft(decision="no_op"), None
        if (
            len(completes) == 1
            and all(item.operation == "complete" for item in openings)
        ):
            return _timing_closure_draft(completes[0].opening_token), None
        if first_chance_spent and not cause_bound:
            # The one daily day_open Occasion is spent.  Ordinary reversals
            # stay quiet until the next local day; this is the G2 timing
            # boundary, not a scripted activity choice.
            self._daily_occasions.mark("day_open", day_key)
            return ActivityLifecycleModelDraft(decision="no_op"), None
        # Cause-bound openings (an observed user interruption, a clock
        # conflict, a shared-private invitation, or repair/resume authority)
        # are new life material.  They get their own bounded Occasion so she
        # can react on the same local day instead of being forced toward the
        # single complete token.
        closed_window_abandon_only = _openings_are_closed_window_abandons(openings)
        use_day_open = not first_chance_spent and not closed_window_abandon_only
        if closed_window_abandon_only:
            occasion_merge_key = self._closed_window_abandon_merge_key(
                projection=projection,
                catalog=catalog,
                wake_event_ref=wake_event_ref,
            )
        else:
            occasion_merge_key = day_key if use_day_open else wake_event_ref
        occasion = (
            mint_day_open(
                source_event_ref=wake_event_ref,
                created_at=projection.logical_time,
                merge_key=occasion_merge_key,
            )
            if use_day_open
            else mint_life_beat(
                source_event_ref=wake_event_ref,
                created_at=projection.logical_time,
                merge_key=occasion_merge_key,
            )
        )
        if closed_window_abandon_only and self._occasion_spends.spent(occasion.occasion_id):
            # Same missed-plan abandon set already had its one consider.
            # Re-asking every clock wake would burn a model call; a later
            # start/complete entering the catalog changes the merge key.
            return ActivityLifecycleModelDraft(decision="no_op"), None
        opening_summaries = []
        for item in openings:
            summary = item.safe_summary
            resolved = self._catalog.resolve_opening(
                projection=projection,
                wake_event_ref=wake_event_ref,
                opening_token=item.opening_token,
            )
            if resolved is not None and self._plan_material_reader is not None:
                try:
                    material = self._plan_material_reader.read_for_plan(
                        plan_id=resolved.plan_id
                    )
                    intention = getattr(material, "character_intention", None)
                    if isinstance(intention, str) and intention.strip():
                        clipped = " ".join(intention.strip().split())[:96]
                        summary = f"{item.safe_summary}; she planned: {clipped}"
                except Exception:
                    # Plan-material reading is optional foreground texture.
                    # A missing sidecar must not hide an otherwise legal
                    # activity opening from the character.
                    pass
            opening_summaries.append(
                {
                    "opening_token": item.opening_token,
                    "safe_summary": summary,
                }
            )
        capability = {
            "contract": "character-interior-activity-lifecycle-capability.2",
            "catalog_version": catalog.catalog_version,
            "catalog_hash": catalog.catalog_hash,
            "offered_tokens": [item.opening_token for item in openings],
            "openings": opening_summaries,
        }
        payload_json = json.dumps(
            capability,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        capability_hash = "sha256:" + hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        manifest = _InteriorCapabilityManifest(
            capability_ref=(
                "capability:activity-lifecycle:"
                + hashlib.sha256((trigger_id + ":" + capability_hash).encode("utf-8")).hexdigest()
            ),
            capability_kind="activity_lifecycle_choice",
            payload_json=payload_json,
            payload_hash=capability_hash,
            source_refs=(wake_event_ref,),
        )
        opportunity_identity = CausalOpportunityRuntime(
            world_id=self._ledger.world_id,
            actor_ref=self._owner_actor_ref,
            purpose="activity_lifecycle_choice",
        ).identity_for_refs((wake_event_ref,), epoch=wake_event_ref)
        cursor = ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
        result = await self._character_interior.consider(
            InteriorOpportunity(
                opportunity_ref=opportunity_identity.opportunity_ref,
                inner_turn_ref="inner-turn:activity-lifecycle:" + trigger_id,
                world_id=self._ledger.world_id,
                actor_ref=self._owner_actor_ref,
                trigger_ref=wake_event_ref,
                cursor=cursor,
                logical_time=projection.logical_time,
                purpose="activity_lifecycle_choice",
                source_refs=(wake_event_ref,),
                capability_manifest=manifest,
                context_note=(
                    "One exact clock wake offers already-authorized activity transitions. "
                    "The table decides whether an opportunity exists; she decides what "
                    "the day is like. The character owns select or no-op; the system owns "
                    "only token authority."
                ),
                occasion=occasion,
            )
        )
        if result.status == "technical_failure":
            failure_code = result.failure_code or "character_interior_technical_failure"
            if failure_code in _OCCASION_CONSUMED_FAILURES:
                if use_day_open:
                    self._daily_occasions.mark("day_open", day_key)
                return ActivityLifecycleModelDraft(decision="no_op"), None
            return None, failure_code
        if result.status != "decided" or not isinstance(result.decision, dict):
            return None, "character_interior_decision_missing"
        if use_day_open:
            self._daily_occasions.mark("day_open", day_key)
        if closed_window_abandon_only:
            self._occasion_spends.mark(occasion.occasion_id)
        decision = result.decision
        if (
            decision.get("contract") != "character-interior-purpose-decision.1"
            or decision.get("purpose") != "activity_lifecycle_choice"
            or decision.get("capability_ref") != manifest.capability_ref
            or decision.get("capability_payload_hash") != manifest.payload_hash
            or tuple(decision.get("source_refs", ())) != manifest.source_refs
        ):
            return None, "character_interior_decision_binding_invalid"
        payload = decision.get("payload")
        if not isinstance(payload, dict):
            return None, "character_interior_decision_payload_invalid"
        if payload.get("contract") != "character-interior-activity-lifecycle-choice.1":
            return None, "character_interior_decision_contract_invalid"
        noticed = payload.get("noticed")
        if noticed is not None and (not isinstance(noticed, str) or not noticed.strip()):
            return None, "character_interior_decision_payload_invalid"
        keys = {key for key in payload if key != "noticed"}
        choice = payload.get("decision")
        if choice == "no_op" and keys == {"contract", "decision"}:
            token = None
            draft_decision = "no_op"
        elif choice == "select" and keys == {
            "contract",
            "decision",
            "selected_token",
        }:
            token = payload.get("selected_token")
            if not isinstance(token, str) or token not in capability["offered_tokens"]:
                return None, "character_interior_selected_token_invalid"
            draft_decision = "opening_token"
        else:
            return None, "character_interior_decision_payload_invalid"
        lineage = result.author_lineage
        model_id = getattr(lineage, "model_id", None)
        if not isinstance(model_id, str) or not model_id:
            return None, "character_interior_author_lineage_missing"
        normalized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        model_result_audit = recorded_character_interior_model_result(
            result,
            purpose="activity_lifecycle_choice",
            subject_ref=result.opportunity_ref,
            trigger_ref=wake_event_ref,
            capability_ref=manifest.capability_ref,
            route_tier="flash",
            route_reason_code="activity_lifecycle.character_choice",
            router_version="character-interior-activity-lifecycle-capability.2",
            causal_opportunity=opportunity_identity,
        )
        return (
            ActivityLifecycleModelDraft(
                decision=draft_decision,
                opening_token=token,
                model=model_id,
                raw_output=normalized,
                raw_output_hash=digest,
                normalized_json=normalized,
                normalized_output_hash=digest,
                character_interior_model_result=model_result_audit,
            ),
            None,
        )

    def _closed_window_abandon_merge_key(
        self,
        *,
        projection,
        catalog,
        wake_event_ref: str,
    ) -> str:
        """Stable Occasion key for one missed-plan abandon set.

        Catalog hashes include the clock wake, so they cannot bound consider.
        Plan identity plus operation stays the same across idle ticks and
        changes when a startable window actually opens.
        """

        parts: list[str] = []
        for item in catalog.openings:
            resolved = self._catalog.resolve_opening(
                projection=projection,
                wake_event_ref=wake_event_ref,
                opening_token=item.opening_token,
            )
            if resolved is None:
                parts.append(
                    f"{item.operation}:{item.opening_kind}:{item.cause_kind or ''}"
                )
            else:
                parts.append(
                    f"{resolved.plan_id}:{resolved.plan_revision}:"
                    f"{resolved.operation}:{resolved.opening_kind}"
                )
        digest = hashlib.sha256(
            json.dumps(
                {"world_id": self._ledger.world_id, "openings": sorted(parts)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return "closed-window-abandon:" + digest

    def _hitch_paid_noticed(
        self,
        *,
        draft: ActivityLifecycleModelDraft,
        wake_event_ref: str,
        trace_id: str,
        correlation_id: str,
    ) -> None:
        runtime = self._open_world_event
        raw = draft.normalized_json or draft.raw_output
        if runtime is None or not raw:
            return
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        noticed = payload.get("noticed")
        if not isinstance(noticed, str) or not noticed.strip():
            return
        try:
            runtime.commit_from_paid_moment(
                moment=noticed.strip(),
                wake_event_ref=wake_event_ref,
                model=draft.model or "paid-turn:day_open",
                raw_output=raw,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "paid noticed hitch failed wake=%s", wake_event_ref, exc_info=True
            )


__all__ = ["ActivityLifecycleFollowupResult", "ActivityLifecycleWorker"]
