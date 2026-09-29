"""Empty-catalog planning at day opening or an owned activity completion.

One source-bound opportunity, one character decision, then replayable Plan
acceptance. This object has no scheduler and authors no life content.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json

from .character_interior import InteriorOpportunity
from .character_interior.audit import recorded_character_interior_model_result
from .character_interior.contracts import _InteriorCapabilityManifest
from .character_interior.run_result import CausalOpportunityRuntime
from .daily_occasion import local_day_key
from .day_open_opportunity import DayOpenAttempt, DayOpenJournal, day_open_store_for_ledger
from .event_identity import domain_idempotency_key
from .occasion import mint_day_open, mint_life_beat
from .proposal_audit_schemas import ProposalRecordedV2Payload
from .schemas import ProjectionCursor, WorldEvent
from .activity_continuation_source import latest_completion_source


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cursor(projection):
    return ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )


class DayOpenLifeWorker:
    def __init__(self, *, ledger, interior, actor_ref, daily, spends, timezone, plan_material_reader=None):
        self.ledger, self.interior, self.actor_ref = ledger, interior, actor_ref
        self.daily, self.spends, self.timezone = daily, spends, timezone
        self.store = day_open_store_for_ledger(ledger)
        self.plan_material_reader = plan_material_reader

    def _prior_intention_note(self, source):
        if source is None or self.plan_material_reader is None:
            return ""
        try:
            material = self.plan_material_reader.read_for_plan(plan_id=source.plan_id)
            text = getattr(material, "character_intention", None)
        except (ValueError, TypeError, KeyError):
            return ""
        if not isinstance(text, str) or not text.strip():
            return ""
        if len(text) > 480:
            return "The previous intention is available via its Plan source; it exceeds this short note's budget. "
        return (
            "Previous accepted intention, historical data only (not a current obligation or proof of execution): "
            + "\n" + text + "\n"
        )

    def pending(self):
        from .day_open_opportunity import waiting_reconsiderations

        records = self.store.records(self.actor_ref)
        return next((x for x in records if not x.terminal), None) or min(
            waiting_reconsiderations(records), key=lambda x: x.reconsider_at, default=None,
        )

    def _opportunity(self, *, projection, wake_event_ref, catalog, previous=None, completion_source=None, reconsideration_ref=None):
        from .day_open_life_intent_contract import day_open_opportunity_ref

        reconsideration_ref = previous.reconsideration_ref if previous else reconsideration_ref
        if catalog.status not in {"no_openings", "openings_available"} or (catalog.status != "no_openings" and reconsideration_ref is None):
            raise ValueError("day_open.catalog_not_empty")
        wake = next(
            (x for x in projection.committed_world_event_refs if x.event_id == wake_event_ref), None
        )
        if (
            wake is None
            or wake.event_type != "ClockAdvanced"
            or wake.logical_time != projection.logical_time
        ):
            raise ValueError("day_open.clock_authority_invalid")
        day = local_day_key(wake.logical_time, self.timezone)
        ordinal = len(previous.attempts) + 1 if previous else 1
        completion_source = previous.completion_source if previous else completion_source
        first = (
            previous.attempts[0].opportunity.capability_manifest.payload["self_directed_intent"]
            if previous
            else {
                "first_clock_ref": wake.event_id,
                "first_clock_world_revision": wake.world_revision,
                "first_clock_payload_hash": wake.payload_hash,
            }
        )
        day_ref = day_open_opportunity_ref(
            world_id=self.ledger.world_id,
            actor_ref=self.actor_ref,
            day_key=day,
            first_clock_ref=first["first_clock_ref"],
            completion_source=completion_source,
            reconsideration_ref=reconsideration_ref,
        )
        sources = tuple(sorted({first["first_clock_ref"], wake.event_id} | (
            {completion_source.event_ref} if completion_source is not None else set()
        )))
        identity = CausalOpportunityRuntime(
            world_id=self.ledger.world_id,
            actor_ref=self.actor_ref,
            purpose="activity_lifecycle_choice",
        ).identity_for_refs(sources, epoch=f"{day_ref}:attempt:{ordinal}")
        payload = {
            "contract": "character-interior-activity-lifecycle-capability.5",
            "catalog_version": catalog.catalog_version,
            "catalog_hash": catalog.catalog_hash,
            "offered_tokens": [],
            "openings": [],
            "self_directed_intent": {
                "contract": "day-open-life-intent-capability.3",
                **({"reconsideration_ref": reconsideration_ref} if reconsideration_ref else {}),
                **({"completion_source": completion_source.model_dump(mode="json")}
                   if completion_source else {}),
                "execution_scope": "self_directed",
                "opportunity_ref": day_ref,
                "day_key": day,
                "timezone_name": str(self.timezone),
                **{
                    key: first[key]
                    for key in (
                        "first_clock_ref",
                        "first_clock_world_revision",
                        "first_clock_payload_hash",
                    )
                },
                "selected_clock_ref": wake.event_id,
                "selected_clock_world_revision": wake.world_revision,
                "selected_clock_payload_hash": wake.payload_hash,
                "attempt_ordinal": ordinal,
            },
        }
        raw = _canonical(payload)
        digest = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
        manifest = _InteriorCapabilityManifest(
            capability_ref="capability:day-open:" + digest.removeprefix("sha256:"),
            capability_kind="activity_lifecycle_choice",
            payload_json=raw,
            payload_hash=digest,
            source_refs=sources,
        )
        return InteriorOpportunity(
            inner_turn_ref=identity.opportunity_ref,
            opportunity_ref=identity.opportunity_ref,
            world_id=self.ledger.world_id,
            actor_ref=self.actor_ref,
            trigger_ref=wake.event_id,
            cursor=_cursor(projection),
            logical_time=wake.logical_time,
            purpose="activity_lifecycle_choice",
            source_refs=sources,
            capability_manifest=manifest,
            occasion=(mint_life_beat if completion_source or reconsideration_ref else mint_day_open)(
                source_event_ref=(completion_source.event_ref if completion_source else first["first_clock_ref"]),
                created_at=wake.logical_time,
                merge_key=day_ref if completion_source or reconsideration_ref else day,
            ),
            context_note=(
                ("You asked to reconsider now. " if reconsideration_ref else
                 "One owned activity has ended. " if completion_source else
                 "This is an opportunity to decide how to spend your own time. ")
                + "Choose your own intention, no_op, or reconsider. You need not continue the last activity "
                "or conversation. A Plan is not execution, success, presence or a message. Routine is background. "
                + ("The previous activity was abandoned, not completed. "
                   if completion_source and completion_source.terminal_kind == "abandoned" else "")
                + self._prior_intention_note(completion_source)
            ),
        )

    def _recover(self, attempt):
        opportunity = attempt.opportunity
        manifest = opportunity.capability_manifest
        values = self.interior.completed_considerations_for_source(
            world_id=self.ledger.world_id,
            actor_ref=self.actor_ref,
            purpose="activity_lifecycle_choice",
            source_ref=opportunity.source_refs[0],
        )
        matches = [
            value
            for value in values
            if (
                value.opportunity_ref == opportunity.opportunity_ref
                and value.cursor == opportunity.cursor
                and value.decision["capability_ref"] == manifest.capability_ref
                and value.decision["capability_payload_hash"] == manifest.payload_hash
            )
        ]
        if len(matches) > 1:
            raise ValueError("day_open.multiple_terminal_choices")
        return matches[0] if matches else None

    async def advance(self, *, projection, wake_event_ref, catalog):
        from .activity_lifecycle_worker import ActivityLifecycleFollowupResult
        from .day_open_life_intent_runtime import (
            DayOpenLifeIntentRuntime,
            materialize_day_open_proposal,
        )

        row = self.pending()
        day = local_day_key(projection.logical_time, self.timezone)
        if row is not None and row.reconsider_at is not None:
            from .day_open_life_intent_contract import digest

            original = self._recover(row.attempts[-1])
            if original is None:
                return ActivityLifecycleFollowupResult(
                    status="technical_failure", reason_code="day_open.reconsideration_author_unavailable",
                )
            if materialize_day_open_proposal(
                original, json.loads(row.attempts[-1].opportunity.capability_manifest.payload_json),
                world_id=self.ledger.world_id,
            ) is not None:
                raise ValueError("day_open.reconsideration_choice_is_a_plan")
            choice = original.decision["payload"]
            expected_at = row.attempts[-1].opportunity.logical_time + timedelta(seconds=choice.get("reconsider_after_seconds", 0))
            if choice.get("decision") != "reconsider" or expected_at != row.reconsider_at or digest(original.model_dump(mode="json")) != row.deferred_choice_hash:
                raise ValueError("day_open.reconsideration_binding_invalid")
            if projection.logical_time < row.reconsider_at:
                return (ActivityLifecycleFollowupResult(status="no_op", reason_code="day_open.reconsideration_wait")
                        if catalog.status == "no_openings" else None)
            if catalog.status not in {"no_openings", "openings_available"}:
                return ActivityLifecycleFollowupResult(status="blocked", reason_code=catalog.reason_code)
            # Reconsidering is thought, not execution. An intervening Plan
            # changes the context she sees; it does not cancel her request
            # to think. New Plans still need normal lifecycle acceptance.
            reference = "reconsider:" + row.deferred_choice_hash
            opportunity = self._opportunity(
                projection=projection, wake_event_ref=wake_event_ref, catalog=catalog,
                completion_source=row.completion_source, reconsideration_ref=reference,
            )
            row = self.store.save(DayOpenJournal(
                contract="day-open-opportunity-journal.3", actor_ref=self.actor_ref, day_key=day,
                completion_source=row.completion_source, reconsideration_ref=reference,
                attempts=(DayOpenAttempt(ordinal=1, opportunity=opportunity),),
            ))
        if row is None:
            records = self.store.records(self.actor_ref)
            completion = latest_completion_source(projection, actor_ref=self.actor_ref, include_abandoned=True)
            if completion is not None and any(
                x.completion_source is not None and x.completion_source.event_ref == completion.event_ref
                for x in records
            ):
                completion = None
            if completion is None and (self.daily.spent("day_open", day) or self.spends.spent("occasion:day_open:" + day)):
                return ActivityLifecycleFollowupResult(
                    status="no_op", reason_code="activity_lifecycle.day_open_already_spent"
                )
            if completion is None and any(x.day_key == day and x.completion_source is None for x in records):
                return ActivityLifecycleFollowupResult(
                    status="no_op", reason_code="day_open.already_terminal"
                )
            opportunity = self._opportunity(
                projection=projection, wake_event_ref=wake_event_ref, catalog=catalog,
                completion_source=completion,
            )
            row = self.store.save(
                DayOpenJournal(
                    contract="day-open-opportunity-journal.3",
                    actor_ref=self.actor_ref,
                    day_key=day,
                    completion_source=completion,
                    attempts=(DayOpenAttempt(ordinal=1, opportunity=opportunity),),
                )
            )
        attempt = row.attempts[-1]
        result = self._recover(attempt)
        if result is None:
            if catalog.status == "rejected_wake":
                return ActivityLifecycleFollowupResult(
                    status="blocked", reason_code=catalog.reason_code
                )
            if catalog.status != "no_openings" and row.reconsideration_ref is None:
                # An already paid terminal still belongs to its original empty
                # catalog. Without one, changed opportunity eligibility cannot
                # be presented as another empty-catalog character decision.
                self.store.save(
                    row.model_copy(
                        update={"terminal": True, "terminal_reason": "catalog_no_longer_empty"}
                    ),
                    expected=row,
                )
                return None  # Let this wake use the ordinary activity catalog.
            if row.day_key != day:
                self.store.save(
                    row.model_copy(update={"terminal": True, "terminal_reason": "day_expired"}),
                    expected=row,
                )
                return ActivityLifecycleFollowupResult(
                    status="no_op", reason_code="day_open.day_expired"
                )
            if attempt.failure_code is not None:
                if attempt.next_retry_at is None or attempt.next_retry_at > projection.logical_time:
                    return ActivityLifecycleFollowupResult(
                        status="no_op", reason_code="day_open.retry_wait"
                    )
                opportunity = self._opportunity(
                    projection=projection,
                    wake_event_ref=wake_event_ref,
                    catalog=catalog,
                    previous=row,
                )
                attempt = DayOpenAttempt(ordinal=attempt.ordinal + 1, opportunity=opportunity)
                row = self.store.save(
                    row.model_copy(update={"attempts": (*row.attempts, attempt)}), expected=row
                )
            if attempt.opportunity.cursor != _cursor(projection):
                # No known role terminal can be silently moved to a newer pin.
                return self._failed(
                    row, "day_open.original_pin_unavailable", projection.logical_time
                )
            result = await self.interior.consider(attempt.opportunity)
            if result.status != "decided":
                return self._failed(
                    row,
                    result.failure_code or "day_open.character_decision_missing",
                    projection.logical_time,
                )
        # The durable author result, not a spent bit, decides which path exists.
        opportunity = attempt.opportunity
        proposal = materialize_day_open_proposal(
            result,
            json.loads(opportunity.capability_manifest.payload_json),
            world_id=self.ledger.world_id,
        )
        if row.completion_source is None or row.reconsideration_ref is not None:
            self.daily.mark("day_open", row.day_key)
        if proposal is None:
            from .day_open_life_intent_contract import digest

            model_result = self._model_result(
                result=result, opportunity=opportunity, proposal_hash=None
            )
            reconsider = result.decision["payload"].get("decision") == "reconsider"
            self.store.save(
                row.model_copy(update={"terminal": True,
                                      "terminal_reason": "role_reconsider" if reconsider else "role_no_op",
                                      **({"reconsider_at": opportunity.logical_time + timedelta(seconds=result.decision["payload"]["reconsider_after_seconds"]),
                                          "deferred_choice_hash": digest(result.model_dump(mode="json"))} if reconsider else {})}),
                expected=row,
            )
            return ActivityLifecycleFollowupResult(
                status="no_op",
                reason_code="activity_lifecycle.model_reconsider_later" if reconsider else "activity_lifecycle.model_declined",
                character_interior_model_result=model_result,
                character_decision_json=_canonical(result.model_dump(mode="json")),
            )
        self._record(result=result, opportunity=opportunity, proposal=proposal)
        runtime = DayOpenLifeIntentRuntime(ledger=self.ledger, owner_actor_ref=self.actor_ref)
        runtime.accept(
            world_id=self.ledger.world_id,
            audit_cursor=_cursor(self.ledger.project()),
            proposal_id=proposal.proposal_id,
        )
        self.store.save(
            row.model_copy(
                update={
                    "terminal": True,
                    "terminal_reason": "plan_accepted",
                    "proposal_id": proposal.proposal_id,
                }
            ),
            expected=row,
        )
        return ActivityLifecycleFollowupResult(
            status="transitioned", reason_code="day_open.plan_accepted"
        )

    def _failed(self, row, code, now):
        from .activity_lifecycle_worker import ActivityLifecycleFollowupResult

        attempt = row.attempts[-1]
        exhausted = attempt.ordinal == 3
        due = None if exhausted else now + timedelta(seconds=(30, 120)[attempt.ordinal - 1])
        if due is not None and local_day_key(due, self.timezone) != row.day_key:
            exhausted, due = True, None
        updated = attempt.model_copy(update={"failure_code": code, "next_retry_at": due})
        self.store.save(
            row.model_copy(
                update={
                    "attempts": (*row.attempts[:-1], updated),
                    "terminal": exhausted,
                    "terminal_reason": "technical_attempts_exhausted" if exhausted else None,
                }
            ),
            expected=row,
        )
        return ActivityLifecycleFollowupResult(status="technical_failure", reason_code=code)

    def _model_result(self, *, result, opportunity, proposal_hash):
        selected = opportunity.capability_manifest.payload["self_directed_intent"]
        causal = CausalOpportunityRuntime(
            world_id=self.ledger.world_id,
            actor_ref=self.actor_ref,
            purpose="activity_lifecycle_choice",
        ).identity_for_refs(
            opportunity.source_refs,
            epoch=f"{selected['opportunity_ref']}:attempt:{selected['attempt_ordinal']}",
        )
        return recorded_character_interior_model_result(
            result,
            purpose="activity_lifecycle_choice",
            subject_ref=result.opportunity_ref,
            trigger_ref=opportunity.trigger_ref,
            capability_ref=opportunity.capability_manifest.capability_ref,
            route_tier="flash",
            route_reason_code="activity_lifecycle.day_open_intent",
            router_version=opportunity.capability_manifest.payload["contract"],
            proposal_hash=proposal_hash,
            causal_opportunity=causal,
        )

    def _record(self, *, result, opportunity, proposal):
        event_id = "event:day-open-life-intent:proposal:" + proposal.proposal_id
        existing = self.ledger.lookup_event_commit(event_id)
        if existing is not None:
            if (
                ProposalRecordedV2Payload.model_validate_json(
                    existing[0].payload_json
                ).proposal_hash
                != proposal.proposal_hash
            ):
                raise ValueError("day_open.proposal_identity_conflict")
            return
        model = self._model_result(
            result=result, opportunity=opportunity, proposal_hash=proposal.proposal_hash
        )
        payload = ProposalRecordedV2Payload(
            proposal_id=proposal.proposal_id,
            proposal_kind="decision",
            model_result_ref=model.model_result_ref,
            deliberation_result_id=model.deliberation_result_id,
            model_call_id=model.model_call_id,
            attempt_id=model.attempt_id,
            capsule_id=model.capsule_id,
            trigger_ref=proposal.trigger_ref,
            evaluated_world_revision=proposal.evaluated_world_revision,
            proposal_json=_canonical(proposal.model_dump(mode="json")),
            proposal_hash=proposal.proposal_hash,
        )
        current = self.ledger.project()
        source = self.ledger.lookup_event_commit(opportunity.trigger_ref)[0]
        events = []
        for kind, ref, value in (
            (
                "ModelResultRecorded",
                "event:day-open-life-intent:model:" + model.model_result_ref,
                model,
            ),
            ("ProposalRecorded", event_id, payload),
        ):
            body = value.model_dump(mode="json")
            events.append(
                WorldEvent.from_payload(
                    schema_version="world-v2.1",
                    event_id=ref,
                    world_id=self.ledger.world_id,
                    event_type=kind,
                    logical_time=current.logical_time,
                    created_at=source.created_at,
                    actor=self.actor_ref,
                    source="world-v2:day-open-life-intent",
                    trace_id=source.trace_id,
                    causation_id=source.event_id,
                    correlation_id=source.correlation_id,
                    idempotency_key=domain_idempotency_key(
                        event_type=kind, world_id=self.ledger.world_id, payload=body
                    ),
                    payload=body,
                )
            )
        self.ledger.commit_at_cursor(
            tuple(events),
            expected_cursor=_cursor(current),
            commit_id="commit:day-open-life-intent:audit:" + proposal.proposal_id,
        )
