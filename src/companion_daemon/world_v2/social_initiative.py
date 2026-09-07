"""Authority-only compiler for human-like conversation initiative opportunities.

Timers never write prose here.  They only make an immutable source eligible for
the existing proactive deliberation lane, where the model remains free to act
now, later, or stay silent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .later_expression_freshness import (
    LATER_REFRESH_CONSIDERATION_PREFIX,
    due_stale_later_actions,
    later_refresh_action_id_matching,
    later_refresh_consideration_id,
    later_refresh_is_terminal,
)
from .ledger import LedgerPort
from .random_authority import RandomAuthority, RandomDrawRecordedPayload
from .response_expectation_view import (
    expired_expectation_consideration_id,
    is_overnight_local,
    pending_response_expectation,
    unanswered_response_expectations,
)
from .revisit_intention_view import (
    due_commitment_consideration_id,
    due_revisit_consideration_id,
    due_thread_consideration_id,
    revisit_source_plan_id,
    thread_due_schedule_sources,
    unfinished_revisits,
)
from .schema_core import FrozenModel
from .schemas import CommittedWorldEventRef, WorldEvent


_SITUATION_STIMULUS_EVENT_TYPES = frozenset(
    {
        "ActivityStarted",
        "ActivityPaused",
        "ActivityResumed",
        "ActivityCompleted",
        "ActivityAbandoned",
        "WorldOccurrenceActivated",
        "WorldOccurrenceSettled",
        "ExperienceCommitted",
        "ExternalPerceptionRecorded",
        "LifeArcChanged",
        "NpcStatusChanged",
        "AffectEpisodeOpened",
        "AffectEpisodeUpdated",
        "AffectEpisodeResolved",
        "AffectEpisodeSuperseded",
        "RelationshipSignalAccepted",
        "RelationshipSlowVariableAdjusted",
        "ThreadUpdated",
        "ThreadExpired",
        "PrivateCommitmentDue",
        "PrivateCommitmentDeadlineBroken",
    }
)
SITUATION_STIMULUS_EVENT_TYPES = _SITUATION_STIMULUS_EVENT_TYPES
_SITUATION_WINDOW = timedelta(minutes=10)
_ACTOR_SCOPED_SITUATION_EVENT_TYPES = frozenset(
    {
        "ActivityStarted",
        "ActivityPaused",
        "ActivityResumed",
        "ActivityCompleted",
        "ActivityAbandoned",
        "WorldOccurrenceActivated",
        "WorldOccurrenceSettled",
        "ExperienceCommitted",
        "ExternalPerceptionRecorded",
        "LifeArcChanged",
        "NpcStatusChanged",
    }
)
# Tier B: only these may independently mint a consider after the short ambient
# window closes. Pause/resume, relationship internals, and thread/commitment
# leftovers stay hitch-only (they have their own lanes or are too noisy to
# wake him).  Affect high points — a new episode or an authored intensity
# change ("心情不好想找人说话") — may mint: life-driven sharing is meant to
# be the main waker, and the shared outreach budget still bounds the total.
_INDEPENDENT_SITUATION_MINT_EVENT_TYPES = frozenset(
    {
        "ActivityCompleted",
        "WorldOccurrenceSettled",
        "ExperienceCommitted",
        "ExternalPerceptionRecorded",
        "LifeArcChanged",
        "AffectEpisodeOpened",
        "AffectEpisodeUpdated",
    }
)


def situation_stimulus_is_observable(
    *, projection, event: WorldEvent, actor_ref: str
) -> bool:
    """Return whether one committed situation event may enter ``actor_ref``'s mind.

    Privacy labels govern later sharing; they do not prove that the protagonist
    witnessed an NPC-owned event.  Actor-scoped sources therefore require an
    exact participation, ownership, or accepted perception binding.  An
    ``ExternalPerceptionRecorded`` event is the disclosure-safe authority for
    something learned indirectly; the private source it summarized is not
    promoted into the protagonist's context.
    """

    if event.event_type not in _SITUATION_STIMULUS_EVENT_TYPES:
        return False
    if event.event_type not in _ACTOR_SCOPED_SITUATION_EVENT_TYPES:
        return True
    payload = event.payload()
    if event.event_type in {"WorldOccurrenceActivated", "WorldOccurrenceSettled"}:
        occurrence_id = payload.get("occurrence_id")
        occurrence = next(
            (
                item
                for item in getattr(projection, "world_occurrences", ())
                if item.occurrence_id == occurrence_id
            ),
            None,
        )
        return occurrence is not None and actor_ref in occurrence.participant_refs
    if event.event_type == "ExperienceCommitted":
        experience = next(
            (
                item
                for item in getattr(projection, "experiences", ())
                if getattr(getattr(item, "origin", None), "accepted_event_ref", None)
                == event.event_id
            ),
            None,
        )
        if experience is not None:
            values = getattr(experience, "values", experience)
            return actor_ref in getattr(values, "participant_refs", ())
        participants = (
            payload.get("experience", {}).get("values", {}).get("participant_refs", ())
            if isinstance(payload.get("experience"), dict)
            else ()
        )
        return actor_ref in participants
    if event.event_type == "ExternalPerceptionRecorded":
        return payload.get("actor_ref") == actor_ref
    if event.event_type == "LifeArcChanged":
        arc_after = payload.get("arc_after")
        return isinstance(arc_after, dict) and arc_after.get("owner_actor_ref") == actor_ref
    if event.event_type.startswith("Activity"):
        plan_id = payload.get("plan_id")
        plan = next(
            (
                item
                for item in getattr(projection, "plans", ())
                if item.plan_id == plan_id
                or getattr(
                    getattr(item, "authority_origin", None),
                    "accepted_event_ref",
                    None,
                )
                == event.event_id
            ),
            None,
        )
        return plan is not None and getattr(plan, "owner_actor_ref", None) == actor_ref
    # NPC lifecycle is not perception authority, even when the new state is
    # public/shareable. Shared participation or a later perception event has
    # its own committed source and can independently wake consideration.
    return False


# A quiet gap may become ambient only at the first scheduler wake after its
# spontaneous window expires.  Later wakes must drop the stale context instead
# of backfilling an old conversation.
_AMBIENT_EXPIRY_GRACE_SECONDS = 60


class SocialInitiativePolicy(FrozenModel):
    spontaneous_idle_seconds: int = Field(default=1_800, ge=60, le=172_800)
    # Quiet-gap TTL for the short spontaneous/ambient lane (not the delay
    # ceiling). Delay candidates max out near 8h; this expiry is 12h.
    spontaneous_expiry_seconds: int = Field(default=43_200, ge=120, le=604_800)
    contact_cooldown_seconds: int = Field(default=900, ge=60, le=86_400)
    local_timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    consideration_band_override_seconds: tuple[int, int] | None = None
    # Shared daily budget for S18 long_silence (A) + post-ambient situation
    # independent mint (B). One shared cap keeps "惦记" from becoming 话痨.
    # Two life-driven considers per day (3h apart) make life the main waker
    # instead of her own hope-expiry alarm clocks.
    shared_outreach_daily_limit: int = Field(default=2, ge=0, le=4)
    shared_outreach_min_interval_seconds: int = Field(
        default=10_800, ge=3_600, le=172_800
    )
    # After ambient closes, draw a further delay so the first ask lands ~18–36h
    # after his last message (12h expiry + 6–24h), not on a fixed clock.
    long_silence_delay_band_seconds: tuple[int, int] = (21_600, 86_400)
    # Soft respect for consecutive silents on shared-budget lanes: after this
    # many, add one extra day of cooldown. Cap is intentional — never mute forever.
    shared_outreach_silent_streak_threshold: int = Field(default=2, ge=1, le=8)
    shared_outreach_silent_extra_cooldown_seconds: int = Field(
        default=86_400, ge=0, le=604_800
    )

    @model_validator(mode="after")
    def expiry_follows_opening(self) -> "SocialInitiativePolicy":
        if self.spontaneous_expiry_seconds <= self.spontaneous_idle_seconds:
            raise ValueError("spontaneous initiative expiry must follow idle opening")
        if self.consideration_band_override_seconds is not None:
            low, high = self.consideration_band_override_seconds
            if not 60 <= low <= high < self.spontaneous_expiry_seconds:
                raise ValueError(
                    "social initiative cadence override must fit the spontaneous window"
                )
        delay_low, delay_high = self.long_silence_delay_band_seconds
        if not 3_600 <= delay_low <= delay_high <= 172_800:
            raise ValueError("long silence delay band must stay within 1h–48h")
        return self


SocialInitiativeSourceKind = Literal[
    "spontaneous_contact",
    "ambient_presence",
    "post_silent",
    "long_silence",
    "situation_change",
    "expired_expectation",
    "thread",
    "commitment",
    "revisit_intention",
    "private_impression",
    "later_expression_refresh",
]

PRIVATE_IMPRESSION_OCCASION_REASON = "private_impression:unresolved"
_PRIVATE_IMPRESSION_CONSIDERATION_PREFIX = (
    "consideration:social-initiative:private-impression:"
)
_LONG_SILENCE_CONSIDERATION_PREFIX = (
    "consideration:social-initiative:long-silence:"
)
_SITUATION_INDEPENDENT_CONSIDERATION_PREFIX = (
    "consideration:social-initiative:situation-independent:"
)
SHARED_OUTREACH_BUDGET_REASON = "budget:shared_outreach"
LONG_SILENCE_OCCASION_REASON = "occasion:long_silence"
SITUATION_INDEPENDENT_OCCASION_REASON = "occasion:situation_independent"
PRIVATE_IMPRESSION_OPPORTUNITY_CONTEXT = (
    "An unresolved private impression is eligible for consideration. "
    "Timing evidence only; she still decides whether to speak, wait, or stay silent."
)
LONG_SILENCE_OPPORTUNITY_CONTEXT = (
    "The short ambient contact window after his last message has closed. "
    "A sparse long-silence consideration is due. Verified idle duration, "
    "open leftovers, and recent life materials are timing and attention "
    "evidence only; she still decides whether to speak, wait, or stay silent."
)


def long_silence_consideration_id(
    *, observation_id: str, local_day: str, delay_seconds: int
) -> str:
    """One effect-once long-silence identity per observation day and delay draw."""

    return _LONG_SILENCE_CONSIDERATION_PREFIX + hashlib.sha256(
        json.dumps(
            {
                "observation_id": observation_id,
                "local_day": local_day,
                "delay_seconds": delay_seconds,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def situation_independent_consideration_id(*, stimulus_anchor_event_id: str) -> str:
    """One effect-once identity for an independently minted situation window."""

    return _SITUATION_INDEPENDENT_CONSIDERATION_PREFIX + hashlib.sha256(
        json.dumps(
            {"stimulus_anchor_event_id": stimulus_anchor_event_id},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def is_shared_outreach_consideration_id(consideration_id: str) -> bool:
    return consideration_id.startswith(_LONG_SILENCE_CONSIDERATION_PREFIX) or (
        consideration_id.startswith(_SITUATION_INDEPENDENT_CONSIDERATION_PREFIX)
    )


def long_silence_opportunity_context() -> str:
    return LONG_SILENCE_OPPORTUNITY_CONTEXT


def private_impression_consideration_id(impression_id: str) -> str:
    """Return one effect-once consideration identity for a living impression."""

    return _PRIVATE_IMPRESSION_CONSIDERATION_PREFIX + hashlib.sha256(
        json.dumps(
            {"impression_id": impression_id},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def private_impression_opportunity_context() -> str:
    """Advisory text: eligibility only. Never the withheld reflection."""

    return PRIVATE_IMPRESSION_OPPORTUNITY_CONTEXT


def living_private_impression(projection):
    """Return the newest active impression with a committed origin, or None.

    Presence is eligibility for a consider. This reader does not inspect
    ``reflection_summary``; wording remains the character's, not a script.
    """

    candidates = []
    for impression in getattr(projection, "private_impressions", ()):
        if getattr(impression, "status", None) != "active":
            continue
        impression_id = getattr(impression, "impression_id", None)
        origin = getattr(impression, "origin", None)
        accepted = getattr(origin, "accepted_event_ref", None)
        if not isinstance(impression_id, str) or not impression_id:
            continue
        if not isinstance(accepted, str) or not accepted:
            continue
        candidates.append(impression)
    accepted_ids = {
        item.event_id
        for item in getattr(projection, "committed_world_event_refs", ())
        if getattr(item, "event_type", None) == "PrivateImpressionAccepted"
    }
    candidates = [
        item
        for item in candidates
        if getattr(getattr(item, "origin", None), "accepted_event_ref", None)
        in accepted_ids
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            getattr(item, "last_supported", None) or getattr(item, "first_seen", None),
            getattr(item, "impression_id", ""),
        )
    )
    return candidates[-1]


def private_impression_source_binds_head(
    *, projection, event: WorldEvent, opportunity
) -> bool:
    """Return whether a proactive source is the current living impression head."""

    impression = next(
        (
            item
            for item in getattr(projection, "private_impressions", ())
            if getattr(item, "impression_id", None) == opportunity.source_id
        ),
        None,
    )
    origin = getattr(impression, "origin", None)
    return (
        event.event_type == "PrivateImpressionAccepted"
        and impression is not None
        and getattr(impression, "status", None) == "active"
        and origin is not None
        and origin.accepted_event_ref == event.event_id
        and origin.accepted_event_ref == opportunity.source_event_ref
    )


class SocialInitiativeOpportunity(FrozenModel):
    source_kind: SocialInitiativeSourceKind
    source_id: str
    source_event_ref: str
    source_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_world_revision: int = Field(ge=1)
    trace_id: str
    correlation_id: str
    created_at: datetime
    consideration_id: str
    consideration_epoch: int = Field(default=0, ge=0)
    scheduled_for: datetime
    cadence_reason_codes: tuple[str, ...] = ()
    stimulus_event_refs: tuple[str, ...] = ()


class SocialInitiativeDecisionProfile(FrozenModel):
    """Explainable timing range; it never decides whether the character speaks."""

    consideration_band_seconds: tuple[int, int]
    delay_candidates_seconds: tuple[int, ...] = Field(min_length=1, max_length=3)
    candidate_weights: dict[str, int]
    reason_codes: tuple[str, ...]


class SocialInitiativeContextPolicy:
    """Translate relationship, affect, activity, and daypart into soft preference mass."""

    version = "social-initiative-context.2"

    def __init__(self, *, policy: SocialInitiativePolicy) -> None:
        self._policy = policy

    def compile(self, *, projection, logical_time: datetime) -> SocialInitiativeDecisionProfile:
        relationship = projection.relationship_states[-1] if projection.relationship_states else None
        variables = getattr(relationship, "variables", None)
        stage = str(getattr(relationship, "stage", "stranger"))
        trust = int(getattr(variables, "trust_bp", 0)) if variables is not None else 0
        closeness = int(getattr(variables, "closeness_bp", 0)) if variables is not None else 0
        mutuality = int(getattr(variables, "mutuality_bp", 0)) if variables is not None else 0
        highly_connected = min(trust, closeness, mutuality) >= 6_000 and (
            closeness + mutuality
        ) // 2 >= 7_000
        if stage in {"close_friend", "lover"} or highly_connected:
            low, high = 3_600, 7_200
            relationship_reason = f"relationship:{stage if relationship is not None else 'close'}"
        elif stage in {"friend", "ambiguous"} or (closeness + mutuality) // 2 >= 4_000:
            low, high = 7_200, 14_400
            relationship_reason = f"relationship:{stage if relationship is not None else 'friend'}"
        elif stage == "acquaintance":
            low, high = 10_800, 21_600
            relationship_reason = "relationship:acquaintance"
        else:
            low, high = 21_600, 28_800
            relationship_reason = "relationship:stranger"

        override = self._policy.consideration_band_override_seconds
        if override is not None:
            low, high = override
            relationship_reason = "relationship:test_override"

        approach = guarded = 0
        for episode in projection.affect_episodes:
            if getattr(episode, "status", None) != "active":
                continue
            for component in getattr(episode, "components", ()):
                intensity = int(getattr(component, "intensity_bp", 0))
                if getattr(component, "dimension", None) in {"warmth", "joy"}:
                    approach = max(approach, intensity)
                else:
                    guarded = max(guarded, intensity)
        if approach > guarded and approach >= 5_000:
            affect_reason = "affect:approach"
        elif guarded >= 5_000:
            affect_reason = "affect:guarded"
        else:
            affect_reason = "affect:neutral"

        engaged = any(getattr(plan, "status", None) == "active" for plan in projection.plans)
        if engaged:
            activity_reason = "activity:engaged"
        else:
            activity_reason = "activity:available"

        local_hour = logical_time.astimezone(ZoneInfo(self._policy.local_timezone)).hour
        if local_hour < 6:
            daypart_reason = "daypart:overnight"
        else:
            daypart_reason = "daypart:day"

        cadence_floor = 60 if override is not None else 2_700
        cadence_ceiling = min(28_800, self._policy.spontaneous_expiry_seconds - 1)
        low = min(max(cadence_floor, low), cadence_ceiling)
        high = min(max(low, high), cadence_ceiling)
        raw_candidates = (low, (low + high) // 2, high)
        candidates = tuple(dict.fromkeys(raw_candidates))
        early_bias = 0
        if affect_reason == "affect:approach":
            early_bias += 1_500
        elif affect_reason == "affect:guarded":
            early_bias -= 1_500
        if activity_reason == "activity:engaged":
            early_bias -= 1_000
        if daypart_reason == "daypart:overnight":
            early_bias -= 1_500
        early_bias = min(2_000, max(-2_000, early_bias))
        weights = (
            2_500 + early_bias,
            5_000,
            2_500 - early_bias,
        )
        candidate_weights: dict[str, int] = {}
        for delay, weight in zip(raw_candidates, weights, strict=True):
            ref = f"delay:{delay}"
            candidate_weights[ref] = candidate_weights.get(ref, 0) + weight
        return SocialInitiativeDecisionProfile(
            consideration_band_seconds=(low, high),
            delay_candidates_seconds=candidates,
            candidate_weights=candidate_weights,
            reason_codes=(
                relationship_reason,
                affect_reason,
                activity_reason,
                daypart_reason,
            ),
        )


def social_initiative_attempt_id(
    *, source_event_ref: str, profile: SocialInitiativeDecisionProfile,
    policy_version: str = SocialInitiativeContextPolicy.version,
) -> str:
    """Return the stable source/profile identity shared by writers and read models."""

    material = {
        "source_event_ref": source_event_ref,
        "policy_version": policy_version,
        "consideration_band_seconds": profile.consideration_band_seconds,
        "delay_candidates_seconds": profile.delay_candidates_seconds,
        "candidate_weights": profile.candidate_weights,
        "reason_codes": profile.reason_codes,
    }
    return "social-initiative:" + hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def social_initiative_consideration_id(
    *,
    attempt_id: str,
    delay_seconds: int,
    epoch: int,
    source_kind: SocialInitiativeSourceKind,
) -> str:
    return "consideration:social-initiative:" + hashlib.sha256(
        json.dumps(
            {
                "attempt_id": attempt_id,
                "delay_seconds": delay_seconds,
                "epoch": epoch,
                "kind": source_kind,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


_POST_SILENT_CONSIDERATION_PREFIX = "consideration:social-initiative:post-silent:"


def _local_day_key(instant: datetime, *, timezone_name: str) -> str:
    return instant.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def post_silent_consideration_id(
    *,
    attempt_id: str,
    delay_seconds: int,
    epoch: int,
    prior_trigger_id: str,
) -> str:
    """Return a durable, self-describing identity for a post-silent draw.

    ``TriggerProcess`` deliberately keeps the long-lived trigger reference
    opaque.  Recovery still needs to distinguish a post-silent opportunity
    from an ambient Clock opportunity, however; otherwise the same persisted
    process can be reopened through the ambient lane.  The prior trigger id is
    encoded (rather than interpreted) so this marker carries no semantic
    decision and remains reversible without a schema migration.
    """

    base = social_initiative_consideration_id(
        attempt_id=attempt_id,
        delay_seconds=delay_seconds,
        epoch=epoch,
        source_kind="post_silent",
    )
    encoded_trigger = prior_trigger_id.encode("utf-8").hex()
    return _POST_SILENT_CONSIDERATION_PREFIX + encoded_trigger + ":" + base.rsplit(
        ":", 1
    )[-1]


def post_silent_attempt_id(
    *, completion_event_ref: str, prior_trigger_id: str, policy_version: str
) -> str:
    """Return one stable draw identity for a silent epoch.

    Timing is selected once from the context available when this chain opens.
    Later affect/activity changes may alter the next model context, but they
    must not mint another draw for the same completed silent trigger.
    """

    material = {
        "completion_event_ref": completion_event_ref,
        "prior_trigger_id": prior_trigger_id,
        "policy_version": policy_version,
    }
    return "social-initiative-post-silent:" + hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def post_silent_prior_trigger_id(consideration_id: str) -> str | None:
    """Decode the prior silent trigger marker, failing closed on old ids."""

    if not consideration_id.startswith(_POST_SILENT_CONSIDERATION_PREFIX):
        return None
    encoded = consideration_id[len(_POST_SILENT_CONSIDERATION_PREFIX) :].split(":", 1)[0]
    if not encoded:
        return None
    try:
        decoded = bytes.fromhex(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded or None


class SocialInitiativeCompiler:
    """Find one eligible source without interpreting words or inventing facts."""

    def __init__(
        self, *, ledger: LedgerPort, actor_ref: str, policy: SocialInitiativePolicy
    ) -> None:
        if not actor_ref:
            raise ValueError("social initiative requires an actor authority")
        self._ledger = ledger
        self._actor_ref = actor_ref
        self._policy = policy
        self._context = SocialInitiativeContextPolicy(policy=policy)
        self._random = RandomAuthority(
            ledger=ledger, source="world-v2:social-initiative-random"
        )

    async def next_opportunity(
        self,
        projection,
        *,
        excluded_consideration_ids: frozenset[str] = frozenset(),
    ) -> SocialInitiativeOpportunity | None:
        logical_time = projection.logical_time
        if logical_time is None:
            return None
        pending = await self._pending_consideration(
            projection,
            excluded_consideration_ids=excluded_consideration_ids,
        )
        if pending is not None:
            # Opening a process durably consumes the timing opportunity.
            # Recovery therefore follows that process even if a later
            # contact cooldown or an expired stimulus window would no longer
            # mint the same opportunity from scratch.
            return pending
        recent_contact = max(
            (
                item.logical_time
                for item in projection.actions
                if item.kind in {"proactive_message", "followup"}
                and item.state not in {"failed", "cancelled", "expired"}
            ),
            default=None,
        )
        # A contact produced by another consideration controls only the normal
        # social cadence. It cannot settle or postpone an already committed
        # technical failure whose stable retry lineage is still valid. The
        # retry reader itself remains fail-closed on a newer user Observation.
        retry = await self._failed_consideration_retry(projection)
        if (
            retry is not None
            and retry.consideration_id not in excluded_consideration_ids
        ):
            # A due idle/retry slot is already a paid consider. Name it as
            # the living impression when one exists; do not wait out the
            # retry backoff just to ask about the same quiet gap.
            if retry.source_kind in {
                "spontaneous_contact",
                "ambient_presence",
                "post_silent",
                "long_silence",
            }:
                adopted = await self._adopt_private_impression(
                    projection,
                    excluded_consideration_ids=excluded_consideration_ids,
                    timing=retry,
                )
                if adopted is not None:
                    return await self._hitch_situation_materials(
                        projection, logical_time, adopted
                    )
            return retry
        later_refresh = await self._later_expression_refresh(
            projection,
            logical_time,
            excluded_consideration_ids=excluded_consideration_ids,
        )
        if later_refresh is not None:
            return await self._hitch_situation_materials(
                projection, logical_time, later_refresh
            )
        declared = await self._declared_attention_opportunity(
            projection,
            logical_time,
            excluded_consideration_ids=excluded_consideration_ids,
        )
        if declared is not None:
            return await self._hitch_situation_materials(
                projection, logical_time, declared
            )
        if recent_contact is not None and (
            logical_time - recent_contact
        ).total_seconds() < self._policy.contact_cooldown_seconds:
            return None
        # Situation stimuli are materials for already-paid considers. Do not
        # mint a dedicated model call on a delay table.
        # A pending message/ambient retry still owns that cadence context.
        # Once the runtime excludes the not-yet-due consideration, minting a
        # sibling cadence epoch would bypass its durable backoff. A failed
        # situation consideration is narrower: it owns only that stimulus
        # attempt and must not occupy the independent ambient cadence until
        # its retry becomes due.
        if retry is not None and retry.source_kind in {
            "spontaneous_contact",
            "ambient_presence",
            "post_silent",
            "long_silence",
        }:
            return None
        post_silent = await self._post_silent_consideration(projection, logical_time)
        if post_silent is not None:
            adopted = await self._adopt_private_impression(
                projection,
                excluded_consideration_ids=excluded_consideration_ids,
                timing=post_silent,
            )
            return await self._hitch_situation_materials(
                projection, logical_time, adopted or post_silent
            )
        if await self._post_silent_chain_active(projection):
            return None
        situation = await self._situation_independent_contact(
            projection,
            logical_time,
            excluded_consideration_ids=excluded_consideration_ids,
        )
        if situation is not None:
            return situation
        spontaneous = await self._spontaneous_contact(projection, logical_time)
        if (
            spontaneous is not None
            and spontaneous.consideration_id in excluded_consideration_ids
        ):
            return None
        if spontaneous is not None:
            adopted = await self._adopt_private_impression(
                projection,
                excluded_consideration_ids=excluded_consideration_ids,
                timing=spontaneous,
            )
            return await self._hitch_situation_materials(
                projection, logical_time, adopted or spontaneous
            )
        long_silence = await self._long_silence_contact(
            projection,
            logical_time,
            excluded_consideration_ids=excluded_consideration_ids,
        )
        if long_silence is None:
            return None
        adopted = await self._adopt_private_impression(
            projection,
            excluded_consideration_ids=excluded_consideration_ids,
            timing=long_silence,
        )
        return await self._hitch_situation_materials(
            projection, logical_time, adopted or long_silence
        )

    async def peek_next_due(self, projection) -> datetime | None:
        """Return the next compiler due instant without recording a draw.

        The QQ scheduler clock only wakes on exact dues.  Initiative cadence
        (post-silent or spontaneous) must be one of those dues, or a frozen
        Life interval can leave ``consideration_due`` true in health while
        ``drain_proactive_once`` stays idle until the next Life tick.
        """

        logical_time = projection.logical_time
        if logical_time is None:
            return None
        pending = await self._pending_consideration(
            projection,
            excluded_consideration_ids=frozenset(),
        )
        if pending is not None:
            return pending.scheduled_for or logical_time
        declared = await self._declared_attention_opportunity(
            projection,
            logical_time,
            excluded_consideration_ids=frozenset(),
            allow_future=True,
        )
        declared_due = declared.scheduled_for if declared is not None else None

        def earliest(other: datetime | None) -> datetime | None:
            return min(
                (due for due in (declared_due, other) if due is not None),
                default=None,
            )

        if declared_due is not None and declared_due <= logical_time:
            return declared_due
        recent_contact = max(
            (
                item.logical_time
                for item in projection.actions
                if item.kind in {"proactive_message", "followup"}
                and item.state not in {"failed", "cancelled", "expired"}
            ),
            default=None,
        )
        if recent_contact is not None:
            cooldown_until = recent_contact + timedelta(
                seconds=self._policy.contact_cooldown_seconds
            )
            if logical_time < cooldown_until:
                return earliest(cooldown_until)
        try:
            post_silent = await self._post_silent_consideration(
                projection,
                logical_time,
                record_draw=False,
                allow_future=True,
            )
        except ValueError:
            post_silent = None
        if post_silent is not None:
            due = post_silent.scheduled_for
            return earliest(due)
        if await self._post_silent_chain_active(projection):
            return declared_due
        try:
            situation = await self._situation_independent_contact(
                projection,
                logical_time,
                excluded_consideration_ids=frozenset(),
                record_draw=False,
                allow_future=True,
            )
        except ValueError:
            situation = None
        if situation is not None:
            return earliest(situation.scheduled_for)
        try:
            spontaneous = await self._spontaneous_contact(
                projection,
                logical_time,
                record_draw=False,
                allow_future=True,
            )
        except ValueError:
            spontaneous = None
        if spontaneous is not None:
            return earliest(spontaneous.scheduled_for)
        try:
            long_silence = await self._long_silence_contact(
                projection,
                logical_time,
                excluded_consideration_ids=frozenset(),
                record_draw=False,
                allow_future=True,
            )
        except ValueError:
            return declared_due
        if long_silence is None:
            return declared_due
        return earliest(long_silence.scheduled_for)

    async def unrecorded_cadence_still_open(self, projection) -> bool:
        """True when health may keep a message-formula due without a draw.

        ``peek_next_due`` is None both before the first RandomDraw and after
        ambient/post-silent are honestly closed.  Only the open-window case
        should keep health's parallel cadence shadow.
        """

        logical_time = projection.logical_time
        if logical_time is None:
            return False
        if await self._post_silent_chain_active(projection):
            return True
        if is_overnight_local(logical_time):
            return False
        if not projection.message_observations:
            return False
        latest = projection.message_observations[-1]
        source = await self._lookup(f"event:observation:{latest.observation_id}")
        if source is None:
            ref = next(
                (
                    item
                    for item in projection.committed_world_event_refs
                    if item.world_revision == latest.world_revision
                    and item.event_type == "ObservationRecorded"
                ),
                None,
            )
            source = await self._lookup(ref.event_id) if ref is not None else None
        if source is None or source[0].event_type != "ObservationRecorded":
            return False
        elapsed = (logical_time - source[0].logical_time).total_seconds()
        if elapsed < self._policy.spontaneous_idle_seconds:
            return False
        if elapsed >= self._policy.spontaneous_expiry_seconds:
            if self._policy.consideration_band_override_seconds is not None:
                return False
            if elapsed >= self._policy.spontaneous_expiry_seconds + _AMBIENT_EXPIRY_GRACE_SECONDS:
                return False
        try:
            pending = pending_response_expectation(projection)
        except (TypeError, ValueError, AttributeError):
            pending = None
        return pending is None

    async def _pending_consideration(
        self,
        projection,
        *,
        excluded_consideration_ids: frozenset[str],
    ) -> SocialInitiativeOpportunity | None:
        latest_message_revision = (
            projection.message_observations[-1].world_revision
            if projection.message_observations
            else 0
        )
        for process in getattr(projection, "trigger_processes", ()):
            if (
                process.process_kind != "proactive_action_deliberation"
                or process.state not in {"open", "claimed"}
                or process.source_evidence_ref is None
                or not process.trigger_ref.startswith(
                    "proactive-consideration:consideration:social-initiative:"
                )
            ):
                continue
            consideration_id = process.trigger_ref.removeprefix(
                "proactive-consideration:"
            )
            if consideration_id in excluded_consideration_ids:
                continue
            source_ref = next(
                (
                    item
                    for item in projection.committed_world_event_refs
                    if item.event_id == process.source_evidence_ref
                ),
                None,
            )
            if source_ref is None:
                continue
            located = await self._lookup(source_ref.event_id)
            if located is None:
                continue
            event = located[0]
            consideration_is_revisit = (
                event.event_type == "ExecutionReceiptRecorded"
                and consideration_id.startswith(
                    "consideration:social-initiative:revisit:"
                )
            )
            if (
                not consideration_is_revisit
                and latest_message_revision > source_ref.world_revision
            ):
                continue
            if event.event_type == "ClockAdvanced":
                prior_trigger_id = post_silent_prior_trigger_id(consideration_id)
                if prior_trigger_id is not None:
                    source_kind = "post_silent"
                    source_id = prior_trigger_id
                elif consideration_id.startswith(_LONG_SILENCE_CONSIDERATION_PREFIX):
                    source_kind = "long_silence"
                    source_id = f"long-silence:recovery:{source_ref.world_revision}"
                else:
                    source_kind = "ambient_presence"
                    source_id = f"ambient:recovery:{source_ref.world_revision}"
                stimulus_event_refs: tuple[str, ...] = ()
            elif event.event_type == "ObservationRecorded":
                source_kind = "spontaneous_contact"
                message = next(
                    (
                        item
                        for item in projection.message_observations
                        if item.world_revision == source_ref.world_revision
                    ),
                    None,
                )
                if message is None:
                    continue
                source_id = message.observation_id
                stimulus_event_refs = ()
            elif event.event_type in {"ThreadOpened", "ThreadUpdated"}:
                source_kind = "thread"
                source_id = next(
                    (
                        item.thread_id
                        for item in getattr(projection, "threads", ())
                        if any(
                            transition.accepted_event_ref == source_ref.event_id
                            for transition in getattr(projection, "thread_transitions", ())
                            if transition.thread_id == item.thread_id
                        )
                    ),
                    source_ref.event_id,
                )
                stimulus_event_refs = ()
            elif event.event_type in {
                "PrivateCommitmentOpened",
                "PrivateCommitmentDue",
            }:
                source_kind = "commitment"
                source_id = next(
                    (
                        item.commitment_id
                        for item in getattr(projection, "commitments", ())
                        if any(
                            transition.accepted_event_ref == source_ref.event_id
                            for transition in getattr(
                                projection, "commitment_transitions", ()
                            )
                            if transition.commitment_id == item.commitment_id
                        )
                    ),
                    source_ref.event_id,
                )
                stimulus_event_refs = ()
            elif event.event_type == "ExecutionReceiptRecorded":
                if consideration_is_revisit:
                    leftover = next(
                        (
                            item
                            for item in unfinished_revisits(projection)
                            if due_revisit_consideration_id(item.plan_id) == consideration_id
                            and item.receipt_event_id == source_ref.event_id
                        ),
                        None,
                    )
                    if leftover is None:
                        continue
                    source_kind = "revisit_intention"
                    source_id = leftover.plan_id
                else:
                    expired = next(
                        (
                            item
                            for item in unanswered_response_expectations(projection)
                            if expired_expectation_consideration_id(item.plan_id) == consideration_id
                            and item.receipt_event_id == source_ref.event_id
                        ),
                        None,
                    )
                    if expired is None:
                        continue
                    source_kind = "expired_expectation"
                    source_id = expired.plan_id
                stimulus_event_refs = ()
            elif event.event_type == "PrivateImpressionAccepted":
                impression = living_private_impression(projection)
                if (
                    impression is None
                    or impression.origin.accepted_event_ref != source_ref.event_id
                ):
                    continue
                source_kind = "private_impression"
                source_id = impression.impression_id
                stimulus_event_refs = ()
            elif (
                event.event_type == "ExpressionBeatAuthorized"
                and consideration_id.startswith(LATER_REFRESH_CONSIDERATION_PREFIX)
            ):
                source_kind = "later_expression_refresh"
                source_id = later_refresh_action_id_matching(
                    projection, consideration_id
                )
                if source_id is None:
                    continue
                stimulus_event_refs = ()
            elif event.event_type in _SITUATION_STIMULUS_EVENT_TYPES:
                if not situation_stimulus_is_observable(
                    projection=projection,
                    event=event,
                    actor_ref=self._actor_ref,
                ):
                    continue
                source_kind = "situation_change"
                if consideration_id.startswith(
                    _SITUATION_INDEPENDENT_CONSIDERATION_PREFIX
                ):
                    source_id = "situation-independent:" + source_ref.event_id
                else:
                    source_id = "situation-window:" + source_ref.event_id
                stimulus_event_refs = tuple(
                    item.event_id
                    for item in await self._observable_stimulus_refs(
                        projection,
                        tuple(
                            item
                            for item in projection.committed_world_event_refs
                            if item.event_type in _SITUATION_STIMULUS_EVENT_TYPES
                            and event.logical_time
                            <= item.logical_time
                            < event.logical_time + _SITUATION_WINDOW
                            and item.world_revision > latest_message_revision
                        ),
                    )
                )
                if source_ref.event_id not in stimulus_event_refs:
                    continue
            else:
                continue
            return await self._from_source(
                source_kind=source_kind,
                source_id=source_id,
                source_event_ref=source_ref.event_id,
                source_world_revision=source_ref.world_revision,
                consideration_id=consideration_id,
                scheduled_for=source_ref.logical_time,
                cadence_reason_codes=("recovery:persisted_process",),
                stimulus_event_refs=stimulus_event_refs,
            )
        return None

    async def _post_silent_chain_active(self, projection) -> bool:
        """Keep a role-owned silent cadence from spawning an ambient sibling.

        Once a consideration has been answered with ``silent``, its next
        recorded draw owns the idle cadence until that post-silent
        consideration settles, or a newer user Observation supersedes it.
        Settled means any terminal outcome on the post-silent process
        (authorized, silent again, grounding_rejected, deliberation-failed
        without an open retry).  Holding the chain forever after settlement
        starved ambient contact while health still reported consideration_due.
        """

        logical_time = projection.logical_time
        if logical_time is not None:
            # Outstanding (possibly future) post-silent still owns cadence.
            outstanding = await self._post_silent_consideration(
                projection,
                logical_time,
                record_draw=False,
                allow_future=True,
            )
            if outstanding is not None:
                return True

        latest_message_revision = (
            projection.message_observations[-1].world_revision
            if projection.message_observations
            else 0
        )
        for process in reversed(getattr(projection, "trigger_processes", ())):
            if (
                process.process_kind != "proactive_action_deliberation"
                or process.state != "terminal"
                or process.runtime_outcome_ref != "proactive:silent"
                or process.source_evidence_ref is None
                or not process.trigger_ref.startswith(
                    "proactive-consideration:consideration:social-initiative:"
                )
            ):
                continue
            prior_trigger_id = getattr(process, "trigger_id", None)
            if not isinstance(prior_trigger_id, str) or not prior_trigger_id:
                continue
            completion_ref = await self._silent_completion_ref(projection, prior_trigger_id)
            if completion_ref is None or latest_message_revision > completion_ref.world_revision:
                return False
            # Draw not yet recorded (peek/health path): hold ambient so the
            # next advance can mint the post-silent draw instead of a sibling.
            # Once any post-silent process for this silent is terminal, release.
            saw_post_silent = False
            for other in getattr(projection, "trigger_processes", ()):
                if other.process_kind != "proactive_action_deliberation":
                    continue
                trigger_ref = getattr(other, "trigger_ref", None)
                if not isinstance(trigger_ref, str):
                    continue
                prefix = "proactive-consideration:" + _POST_SILENT_CONSIDERATION_PREFIX
                if not trigger_ref.startswith(prefix):
                    continue
                consideration_id = trigger_ref.removeprefix("proactive-consideration:")
                if post_silent_prior_trigger_id(consideration_id) != prior_trigger_id:
                    continue
                saw_post_silent = True
                if other.state != "terminal":
                    return True
            if saw_post_silent:
                return False
            return True
        return False

    async def _post_silent_consideration(
        self,
        projection,
        logical_time: datetime,
        *,
        record_draw: bool = True,
        allow_future: bool = False,
    ) -> SocialInitiativeOpportunity | None:
        """Open the next recorded opportunity after a role-owned silence.

        A silent decision closes exactly one consideration.  The next timing
        draw is a new opportunity anchored to that immutable terminal event;
        it never reuses the old model result or invents a motive/wording.
        """

        latest_message_revision = (
            projection.message_observations[-1].world_revision
            if projection.message_observations
            else 0
        )
        for process in reversed(getattr(projection, "trigger_processes", ())):
            if (
                process.process_kind != "proactive_action_deliberation"
                or process.state != "terminal"
                or process.runtime_outcome_ref != "proactive:silent"
                or process.source_evidence_ref is None
                or not process.trigger_ref.startswith(
                    "proactive-consideration:consideration:social-initiative:"
                )
            ):
                continue
            prior_trigger_id = getattr(process, "trigger_id", None)
            if not isinstance(prior_trigger_id, str) or not prior_trigger_id:
                continue
            completion_ref = await self._silent_completion_ref(projection, prior_trigger_id)
            if completion_ref is None:
                continue
            if latest_message_revision > completion_ref.world_revision:
                return None
            profile = self._context.compile(projection=projection, logical_time=logical_time)
            source_ref = next(
                (
                    item
                    for item in projection.committed_world_event_refs
                    if item.event_id == process.source_evidence_ref
                ),
                None,
            )
            if source_ref is None:
                continue
            attempt_id = post_silent_attempt_id(
                completion_event_ref=completion_ref.event_id,
                prior_trigger_id=prior_trigger_id,
                policy_version=self._context.version,
            )
            draw_kwargs = dict(
                attempt_id=attempt_id,
                candidate_refs=tuple(
                    f"delay:{seconds}" for seconds in profile.delay_candidates_seconds
                ),
                candidate_weights=profile.candidate_weights,
                weight_policy_version=self._context.version,
                catalog_version="social-initiative-post-silent-delay.1",
                logical_time=logical_time,
                seed_instant=completion_ref.logical_time,
                actor="system:social-initiative",
                trace_id="trace:social-initiative:post-silent:" + prior_trigger_id[-24:],
                correlation_id=(
                    "correlation:social-initiative:post-silent:" + prior_trigger_id[-24:]
                ),
            )
            draw = await self._recorded_random_draw(projection, attempt_id)
            if draw is None:
                if not record_draw:
                    return None
                draw = (
                    await asyncio.to_thread(self._random.draw, **draw_kwargs)
                    if self._ledger.blocks_event_loop
                    else self._random.draw(**draw_kwargs)
                )
            try:
                delay_seconds = int(draw.selected_candidate_ref.removeprefix("delay:"))
            except (AttributeError, ValueError):
                raise ValueError("post-silent initiative draw did not select a delay")
            if delay_seconds not in profile.delay_candidates_seconds:
                raise ValueError("post-silent initiative draw selected an unknown delay")
            scheduled_for = completion_ref.logical_time + timedelta(seconds=delay_seconds)
            consideration_id = post_silent_consideration_id(
                attempt_id=attempt_id,
                delay_seconds=delay_seconds,
                epoch=0,
                prior_trigger_id=prior_trigger_id,
            )
            current = next(
                (
                    item
                    for item in reversed(getattr(projection, "trigger_processes", ()))
                    if item.process_kind == "proactive_action_deliberation"
                    and item.trigger_ref == "proactive-consideration:" + consideration_id
                ),
                None,
            )
            if current is not None:
                return None
            if logical_time < scheduled_for and not allow_future:
                return None
            return await self._from_source(
                source_kind="post_silent",
                source_id=prior_trigger_id,
                source_event_ref=source_ref.event_id,
                source_world_revision=source_ref.world_revision,
                consideration_id=consideration_id,
                scheduled_for=scheduled_for,
                cadence_reason_codes=("after:role_silent", *profile.reason_codes),
            )
        return None

    async def _recorded_random_draw(
        self, projection, attempt_id: str
    ) -> RandomDrawRecordedPayload | None:
        """Reuse a committed draw before compiling a changed soft profile."""

        for ref in reversed(projection.committed_world_event_refs):
            if ref.event_type != "RandomDrawRecorded":
                continue
            located = await self._lookup(ref.event_id)
            if located is None:
                continue
            payload = located[0].payload()
            if payload.get("attempt_id") == attempt_id:
                return RandomDrawRecordedPayload.model_validate_json(
                    json.dumps(payload, ensure_ascii=False)
                )
        return None

    async def _silent_completion_ref(self, projection, trigger_id: str):
        finder = getattr(self._ledger, "find_trigger_completion", None)
        if callable(finder):
            completion = (
                await asyncio.to_thread(finder, trigger_id)
                if self._ledger.blocks_event_loop
                else finder(trigger_id)
            )
            if completion is not None:
                located = await self._lookup(completion.event_id)
                if located is not None:
                    _event, commit = located
                    return CommittedWorldEventRef(
                        event_id=completion.event_id,
                        event_type=completion.event_type,
                        world_revision=commit.world_revision,
                        payload_hash=completion.payload_hash,
                        logical_time=completion.logical_time,
                    )
        return None

    async def _observable_stimulus_refs(
        self, projection, refs: tuple[object, ...]
    ) -> tuple[object, ...]:
        observable: list[object] = []
        for ref in refs:
            if ref.event_type not in _ACTOR_SCOPED_SITUATION_EVENT_TYPES:
                observable.append(ref)
                continue
            located = await self._lookup(ref.event_id)
            if located is None:
                continue
            event = located[0]
            if event.event_type != ref.event_type or event.logical_time != ref.logical_time:
                continue
            if situation_stimulus_is_observable(
                projection=projection,
                event=event,
                actor_ref=self._actor_ref,
            ):
                observable.append(ref)
        return tuple(observable)

    async def _recent_observable_situation_refs(
        self, projection, logical_time: datetime
    ) -> tuple[str, ...]:
        latest_message_revision = (
            projection.message_observations[-1].world_revision
            if projection.message_observations
            else 0
        )
        candidate_refs = tuple(
            sorted(
                (
                    item
                    for item in projection.committed_world_event_refs
                    if item.world_revision > latest_message_revision
                    and item.event_type in _SITUATION_STIMULUS_EVENT_TYPES
                    and item.logical_time <= logical_time
                ),
                key=lambda item: (item.logical_time, item.world_revision, item.event_id),
            )
        )
        refs = await self._observable_stimulus_refs(projection, candidate_refs)
        if not refs:
            return ()
        latest_cluster = [refs[0]]
        for ref in refs[1:]:
            if ref.logical_time - latest_cluster[0].logical_time >= _SITUATION_WINDOW:
                latest_cluster = [ref]
            else:
                latest_cluster.append(ref)
        return tuple(item.event_id for item in latest_cluster)

    async def _hitch_situation_materials(
        self,
        projection,
        logical_time: datetime,
        opportunity: SocialInitiativeOpportunity,
    ) -> SocialInitiativeOpportunity:
        if not isinstance(opportunity, SocialInitiativeOpportunity):
            return opportunity
        if opportunity.stimulus_event_refs:
            return opportunity
        refs = await self._recent_observable_situation_refs(projection, logical_time)
        if not refs:
            return opportunity
        reason_codes = opportunity.cadence_reason_codes
        if "stimulus:situation_change" not in reason_codes:
            reason_codes = (*reason_codes, "stimulus:situation_change")
        return opportunity.model_copy(
            update={
                "stimulus_event_refs": refs,
                "cadence_reason_codes": reason_codes,
            }
        )

    async def _failed_consideration_retry(
        self, projection
    ) -> SocialInitiativeOpportunity | None:
        settled_considerations = {
            item.trigger_ref
            for item in getattr(projection, "trigger_processes", ())
            if item.process_kind == "proactive_action_deliberation"
            and item.state == "terminal"
            and not str(item.runtime_outcome_ref).startswith(
                "proactive:deliberation-failed:"
            )
        }
        process = next(
            (
                item
                for item in reversed(getattr(projection, "trigger_processes", ()))
                if item.process_kind == "proactive_action_deliberation"
                and item.state == "terminal"
                and str(item.runtime_outcome_ref).startswith(
                    "proactive:deliberation-failed:"
                )
                and item.trigger_ref not in settled_considerations
                and item.trigger_ref.startswith("proactive-consideration:")
                and item.source_evidence_ref is not None
            ),
            None,
        )
        if process is None:
            return None
        source_ref = next(
            (
                item
                for item in projection.committed_world_event_refs
                if item.event_id == process.source_evidence_ref
            ),
            None,
        )
        if source_ref is None:
            return None
        result_ref = str(process.runtime_outcome_ref).removeprefix(
            "proactive:deliberation-failed:"
        )
        failed_audit = next(
            (
                item
                for item in reversed(projection.model_result_audits)
                if item.model_result_ref == result_ref
                and item.proposal_hash is None
            ),
            None,
        )
        if failed_audit is None:
            return None
        latest_message_revision = (
            projection.message_observations[-1].world_revision
            if projection.message_observations
            else 0
        )
        located = await self._lookup(source_ref.event_id)
        if located is None:
            return None
        event = located[0]
        consideration_id = process.trigger_ref.removeprefix(
            "proactive-consideration:"
        )
        if latest_message_revision > failed_audit.evaluated_world_revision and not (
            event.event_type == "ExecutionReceiptRecorded"
            and consideration_id.startswith("consideration:social-initiative:revisit:")
        ):
            return None
        prior_trigger_id = post_silent_prior_trigger_id(consideration_id)
        source_kind = (
            "post_silent"
            if event.event_type == "ClockAdvanced" and prior_trigger_id is not None
            else "long_silence"
            if event.event_type == "ClockAdvanced"
            and consideration_id.startswith(_LONG_SILENCE_CONSIDERATION_PREFIX)
            else "ambient_presence"
            if event.event_type == "ClockAdvanced"
            else "spontaneous_contact"
            if event.event_type == "ObservationRecorded"
            else "situation_change"
            if event.event_type in _SITUATION_STIMULUS_EVENT_TYPES
            else "revisit_intention"
            if event.event_type == "ExecutionReceiptRecorded"
            and consideration_id.startswith("consideration:social-initiative:revisit:")
            else "expired_expectation"
            if event.event_type == "ExecutionReceiptRecorded"
            else "private_impression"
            if event.event_type == "PrivateImpressionAccepted"
            else "later_expression_refresh"
            if event.event_type == "ExpressionBeatAuthorized"
            and consideration_id.startswith(LATER_REFRESH_CONSIDERATION_PREFIX)
            else None
        )
        if source_kind is None:
            return None
        if source_kind == "situation_change" and not situation_stimulus_is_observable(
            projection=projection,
            event=event,
            actor_ref=self._actor_ref,
        ):
            return None
        source_id = prior_trigger_id or f"retry:{source_ref.event_id}"
        if source_kind == "spontaneous_contact":
            message = next(
                (
                    item
                    for item in projection.message_observations
                    if item.world_revision == source_ref.world_revision
                ),
                None,
            )
            if message is None:
                return None
            source_id = message.observation_id
        elif source_kind == "expired_expectation":
            source_id = next(
                (
                    manifest.plan_id
                    for manifest in projection.expression_plan_manifests
                    if expired_expectation_consideration_id(manifest.plan_id) == consideration_id
                ),
                None,
            )
            if source_id is None:
                return None
        elif source_kind == "revisit_intention":
            source_id = (
                revisit_source_plan_id(projection, consideration_id)
                or f"retry:{source_ref.event_id}"
            )
        elif source_kind == "private_impression":
            impression = living_private_impression(projection)
            if (
                impression is None
                or impression.origin.accepted_event_ref != source_ref.event_id
            ):
                return None
            source_id = impression.impression_id
        elif source_kind == "later_expression_refresh":
            source_id = later_refresh_action_id_matching(
                projection, consideration_id
            )
            if source_id is None:
                return None
        stimulus_event_refs = ()
        if source_kind == "situation_change":
            observable = await self._observable_stimulus_refs(
                projection,
                tuple(
                    item
                    for item in projection.committed_world_event_refs
                    if item.event_type in _SITUATION_STIMULUS_EVENT_TYPES
                    and event.logical_time
                    <= item.logical_time
                    < event.logical_time + _SITUATION_WINDOW
                    and item.world_revision > latest_message_revision
                    and item.world_revision <= failed_audit.evaluated_world_revision
                ),
            )
            stimulus_event_refs = tuple(item.event_id for item in observable)
            if source_ref.event_id not in stimulus_event_refs:
                return None
        return await self._from_source(
            source_kind=source_kind,
            source_id=source_id,
            source_event_ref=source_ref.event_id,
            source_world_revision=source_ref.world_revision,
            consideration_id=consideration_id,
            scheduled_for=source_ref.logical_time,
            cadence_reason_codes=("technical_failure:retry",),
            stimulus_event_refs=stimulus_event_refs,
        )

    async def _later_expression_refresh(
        self,
        projection,
        logical_time: datetime,
        *,
        excluded_consideration_ids: frozenset[str],
    ):
        from .expression_reconsideration import expression_beat_is_gated

        for action in due_stale_later_actions(projection, logical_time):
            plan_id = getattr(action, "expression_plan_id", None)
            beat_id = getattr(action, "expression_beat_id", None)
            action_id = getattr(action, "action_id", None)
            if (
                not isinstance(plan_id, str)
                or not isinstance(beat_id, str)
                or not isinstance(action_id, str)
            ):
                continue
            if expression_beat_is_gated(
                projection=projection, plan_id=plan_id, beat_id=beat_id
            ):
                continue
            consideration_id = later_refresh_consideration_id(action_id)
            if consideration_id in excluded_consideration_ids:
                continue
            if later_refresh_is_terminal(projection, action_id):
                continue
            prefix = "proactive-consideration:" + consideration_id
            existing = next(
                (
                    item
                    for item in getattr(projection, "trigger_processes", ())
                    if item.process_kind == "proactive_action_deliberation"
                    and item.trigger_ref == prefix
                ),
                None,
            )
            if existing is not None:
                continue
            beat = next(
                (
                    item
                    for item in getattr(projection, "expression_beats", ())
                    if getattr(item, "beat_id", None) == beat_id
                ),
                None,
            )
            event_ref = getattr(beat, "event_ref", None) if beat is not None else None
            if not isinstance(event_ref, str) or not event_ref:
                continue
            committed = next(
                (
                    item
                    for item in getattr(projection, "committed_world_event_refs", ())
                    if item.event_id == event_ref
                ),
                None,
            )
            if committed is None:
                continue
            return await self._from_source(
                source_kind="later_expression_refresh",
                source_id=action_id,
                source_event_ref=event_ref,
                source_world_revision=committed.world_revision,
                consideration_id=consideration_id,
                scheduled_for=action.not_before,
                cadence_reason_codes=("later_expression:stale_before_dispatch",),
            )
        return None

    async def _declared_attention_opportunity(
        self,
        projection,
        logical_time: datetime,
        *,
        excluded_consideration_ids: frozenset[str],
        allow_future: bool = False,
    ) -> SocialInitiativeOpportunity | None:
        """One shared read for scheduling and draining her declared attention.

        No draw or model call occurs here. A future opening is returned only
        for scheduler inspection; drain selects already-due declarations.
        """

        expectation = await self._expired_expectation_contact(
            projection,
            logical_time,
            excluded_consideration_ids=excluded_consideration_ids,
            allow_future=allow_future,
        )
        leftover = await self._due_leftover_contact(
            projection,
            logical_time,
            excluded_consideration_ids=excluded_consideration_ids,
            allow_future=allow_future,
        )
        return min(
            (item for item in (expectation, leftover) if item is not None),
            key=lambda item: (item.scheduled_for or logical_time, item.consideration_id),
            default=None,
        )

    async def _expired_expectation_contact(
        self,
        projection,
        logical_time: datetime,
        *,
        excluded_consideration_ids: frozenset[str],
        allow_future: bool = False,
    ):
        for expired in unanswered_response_expectations(projection, due_only=not allow_future):
            consideration_id = expired_expectation_consideration_id(expired.plan_id)
            if consideration_id in excluded_consideration_ids:
                continue
            # Every plan retains one opportunity. Considering a newer plan
            # does not spend the opportunities of other declared hopes.
            if self._terminal_consideration(projection, consideration_id):
                continue
            opportunity = await self._from_source(
                source_kind="expired_expectation",
                source_id=expired.plan_id,
                source_event_ref=expired.receipt_event_id,
                source_world_revision=expired.receipt_world_revision,
                consideration_id=consideration_id,
                scheduled_for=expired.not_before,
                cadence_reason_codes=("expectation:expired_unanswered",),
            )
            if opportunity is not None:
                return opportunity
        return None

    def _leftover_already_materialized(self, projection, *, thread) -> bool:
        values = thread.values
        return any(
            commitment.values.status in {"open", "due"}
            and commitment.values.subject_ref == values.subject_ref
            and commitment.values.due_window == values.due_window
            and commitment.values.anchor_evidence_refs == values.anchor_evidence_refs
            and any(
                action.action_id == commitment.values.fulfillment_contract.expected_action_id
                for action in projection.actions
            )
            for commitment in getattr(projection, "commitments", ())
        )

    async def _due_leftover_contact(
        self,
        projection,
        logical_time: datetime,
        *,
        excluded_consideration_ids: frozenset[str],
        allow_future: bool = False,
    ):
        candidates: list[tuple[datetime, object]] = []
        for thread in getattr(projection, "threads", ()):
            values = getattr(thread, "values", None)
            due = getattr(values, "due_window", None)
            if (
                getattr(values, "status", None) != "open"
                or due is None
                or (not allow_future and logical_time < due.opens_at)
                or logical_time >= due.closes_at
                or self._leftover_already_materialized(projection, thread=thread)
            ):
                continue
            latest = next(
                (
                    item
                    for item in reversed(getattr(projection, "thread_transitions", ()))
                    if item.thread_id == thread.thread_id
                    and item.entity_revision == thread.entity_revision
                    and item.values_after == values
                ),
                None,
            )
            if latest is None:
                continue
            schedule_sources = thread_due_schedule_sources(projection, thread=thread)
            if not schedule_sources:
                continue
            consideration_id = due_thread_consideration_id(
                thread.thread_id, schedule_event_ref=schedule_sources[0]
            )
            if consideration_id in excluded_consideration_ids:
                continue
            if self._terminal_consideration(projection, consideration_id):
                continue
            # Old ledgers keyed a due process by entity alone. Its original
            # source still identifies which accepted schedule was considered;
            # it cannot consume a later character-accepted reschedule.
            if self._terminal_consideration(
                projection,
                due_thread_consideration_id(thread.thread_id),
                source_event_refs=frozenset(schedule_sources),
            ):
                continue
            opportunity = await self._from_source(
                source_kind="thread",
                source_id=thread.thread_id,
                source_event_ref=latest.accepted_event_ref,
                source_world_revision=self._source_world_revision(
                    projection, latest.accepted_event_ref
                ),
                consideration_id=consideration_id,
                scheduled_for=due.opens_at,
                cadence_reason_codes=("leftover:due_thread",),
            )
            if opportunity is not None:
                candidates.append((due.opens_at, opportunity))
        for commitment in getattr(projection, "commitments", ()):
            values = getattr(commitment, "values", None)
            due = getattr(values, "due_window", None)
            bound_action = next(
                (
                    item
                    for item in projection.actions
                    if item.action_id
                    == getattr(
                        getattr(values, "fulfillment_contract", None),
                        "expected_action_id",
                        None,
                    )
                ),
                None,
            )
            if (
                getattr(values, "status", None) not in {"open", "due"}
                or due is None
                or (not allow_future and logical_time < due.opens_at)
                or logical_time >= due.closes_at
                or bound_action is not None
            ):
                continue
            latest = next(
                (
                    item
                    for item in reversed(getattr(projection, "commitment_transitions", ()))
                    if item.commitment_id == commitment.commitment_id
                    and item.entity_revision == commitment.entity_revision
                    and item.values_after == values
                ),
                None,
            )
            if latest is None:
                continue
            consideration_id = due_commitment_consideration_id(commitment.commitment_id)
            if consideration_id in excluded_consideration_ids:
                continue
            if self._terminal_consideration(projection, consideration_id):
                continue
            opportunity = await self._from_source(
                source_kind="commitment",
                source_id=commitment.commitment_id,
                source_event_ref=latest.accepted_event_ref,
                source_world_revision=self._source_world_revision(
                    projection, latest.accepted_event_ref
                ),
                consideration_id=consideration_id,
                scheduled_for=due.opens_at,
                cadence_reason_codes=("leftover:due_commitment",),
            )
            if opportunity is not None:
                candidates.append((due.opens_at, opportunity))
        for leftover in unfinished_revisits(projection, due_only=not allow_future):
            consideration_id = due_revisit_consideration_id(leftover.plan_id)
            if (
                consideration_id not in excluded_consideration_ids
                and not self._terminal_consideration(projection, consideration_id)
            ):
                opportunity = await self._from_source(
                    source_kind="revisit_intention",
                    source_id=leftover.plan_id,
                    source_event_ref=leftover.receipt_event_id,
                    source_world_revision=leftover.receipt_world_revision,
                    consideration_id=consideration_id,
                    scheduled_for=leftover.not_before,
                    cadence_reason_codes=("leftover:due_revisit",),
                )
                if opportunity is not None:
                    candidates.append((leftover.not_before, opportunity))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1].source_id))
        return candidates[0][1]

    def _terminal_consideration(
        self,
        projection,
        consideration_id: str,
        *,
        source_event_refs: frozenset[str] | None = None,
    ) -> bool:
        prefix = "proactive-consideration:" + consideration_id
        existing = next(
            (
                item
                for item in getattr(projection, "trigger_processes", ())
                if item.process_kind == "proactive_action_deliberation"
                and item.trigger_ref == prefix
                and (
                    source_event_refs is None
                    or item.source_evidence_ref in source_event_refs
                )
            ),
            None,
        )
        return existing is not None and existing.state == "terminal"

    async def _adopt_private_impression(
        self,
        projection,
        *,
        excluded_consideration_ids: frozenset[str],
        timing: SocialInitiativeOpportunity,
    ) -> SocialInitiativeOpportunity | None:
        """Reuse an already-due consider slot as a private-impression occasion.

        Timing stays the relationship-band / cadence-floor draw that would have
        asked her anyway. This method only names the source. It does not read
        impression prose, invent a message, or decide whether she speaks.
        """

        impression = living_private_impression(projection)
        if impression is None:
            return None
        consideration_id = private_impression_consideration_id(impression.impression_id)
        if consideration_id in excluded_consideration_ids:
            return None
        if self._terminal_consideration(projection, consideration_id):
            return None
        origin_ref = impression.origin.accepted_event_ref
        latest_message_revision = (
            projection.message_observations[-1].world_revision
            if projection.message_observations
            else 0
        )
        source_world_revision = self._source_world_revision(projection, origin_ref)
        if latest_message_revision > source_world_revision:
            return None
        reason_codes = timing.cadence_reason_codes
        if PRIVATE_IMPRESSION_OCCASION_REASON not in reason_codes:
            reason_codes = (PRIVATE_IMPRESSION_OCCASION_REASON, *reason_codes)
        return await self._from_source(
            source_kind="private_impression",
            source_id=impression.impression_id,
            source_event_ref=origin_ref,
            source_world_revision=source_world_revision,
            consideration_id=consideration_id,
            consideration_epoch=timing.consideration_epoch,
            scheduled_for=timing.scheduled_for,
            cadence_reason_codes=reason_codes,
            stimulus_event_refs=timing.stimulus_event_refs,
        )

    def _source_world_revision(self, projection, event_id: str) -> int:
        ref = next(
            (
                item
                for item in projection.committed_world_event_refs
                if item.event_id == event_id
            ),
            None,
        )
        return getattr(ref, "world_revision", 1)

    def _ambient_window_closed(self, *, elapsed_seconds: float) -> bool:
        return (
            elapsed_seconds
            >= self._policy.spontaneous_expiry_seconds + _AMBIENT_EXPIRY_GRACE_SECONDS
        )

    def _shared_outreach_processes(self, projection):
        for process in getattr(projection, "trigger_processes", ()):
            if process.process_kind != "proactive_action_deliberation":
                continue
            if not process.trigger_ref.startswith("proactive-consideration:"):
                continue
            consideration_id = process.trigger_ref.removeprefix(
                "proactive-consideration:"
            )
            if not is_shared_outreach_consideration_id(consideration_id):
                continue
            yield process, consideration_id

    def _shared_outreach_uses_on_local_day(
        self, projection, logical_time: datetime
    ) -> int:
        day = _local_day_key(
            logical_time, timezone_name=self._policy.local_timezone
        )
        count = 0
        for process, _consideration_id in self._shared_outreach_processes(projection):
            source_ref = next(
                (
                    item
                    for item in projection.committed_world_event_refs
                    if item.event_id == process.source_evidence_ref
                ),
                None,
            )
            if source_ref is None:
                continue
            if _local_day_key(
                source_ref.logical_time, timezone_name=self._policy.local_timezone
            ) == day:
                count += 1
        return count

    def _last_shared_outreach_at(self, projection) -> datetime | None:
        latest: datetime | None = None
        for process, _consideration_id in self._shared_outreach_processes(projection):
            source_ref = next(
                (
                    item
                    for item in projection.committed_world_event_refs
                    if item.event_id == process.source_evidence_ref
                ),
                None,
            )
            if source_ref is None:
                continue
            if latest is None or source_ref.logical_time > latest:
                latest = source_ref.logical_time
        return latest

    def _shared_outreach_silent_streak(self, projection) -> int:
        streak = 0
        for process, _consideration_id in reversed(
            tuple(self._shared_outreach_processes(projection))
        ):
            if process.state != "terminal":
                continue
            if process.runtime_outcome_ref == "proactive:silent":
                streak += 1
                continue
            break
        return streak

    def _shared_outreach_budget_allows(
        self, projection, logical_time: datetime
    ) -> bool:
        if self._policy.shared_outreach_daily_limit <= 0:
            return False
        if (
            self._shared_outreach_uses_on_local_day(projection, logical_time)
            >= self._policy.shared_outreach_daily_limit
        ):
            return False
        last_at = self._last_shared_outreach_at(projection)
        if last_at is not None:
            required = self._policy.shared_outreach_min_interval_seconds
            streak = self._shared_outreach_silent_streak(projection)
            if streak >= self._policy.shared_outreach_silent_streak_threshold:
                required += self._policy.shared_outreach_silent_extra_cooldown_seconds
            if (logical_time - last_at).total_seconds() < required:
                return False
        return True

    async def _latest_user_observation_source(self, projection):
        if not projection.message_observations:
            return None
        latest = projection.message_observations[-1]
        source = await self._lookup(f"event:observation:{latest.observation_id}")
        if source is None:
            ref = next(
                (
                    item
                    for item in projection.committed_world_event_refs
                    if item.world_revision == latest.world_revision
                    and item.event_type == "ObservationRecorded"
                ),
                None,
            )
            source = await self._lookup(ref.event_id) if ref is not None else None
        if source is None or source[0].event_type != "ObservationRecorded":
            return None
        return latest, source

    async def _situation_independent_contact(
        self,
        projection,
        logical_time: datetime,
        *,
        excluded_consideration_ids: frozenset[str],
        record_draw: bool = True,
        allow_future: bool = False,
    ):
        """Mint a situation_change consider after ambient closes (tier B).

        Inside the short spontaneous window situation materials only hitch.
        After that window, a protagonist-observable life beat may open one
        shared-budget consider. She still chooses now/later/silent.
        """

        del record_draw  # No delay table; due at the stimulus cluster time.
        if is_overnight_local(logical_time):
            return None
        if self._policy.consideration_band_override_seconds is not None:
            # Qualification fixtures pin the short window only.
            return None
        if not self._shared_outreach_budget_allows(projection, logical_time):
            return None
        observed = await self._latest_user_observation_source(projection)
        if observed is None:
            return None
        _latest, source = observed
        elapsed = (logical_time - source[0].logical_time).total_seconds()
        if not self._ambient_window_closed(elapsed_seconds=elapsed):
            return None
        refs = await self._recent_observable_situation_refs(projection, logical_time)
        if not refs:
            return None
        has_mintable = False
        for event_id in refs:
            located = await self._lookup(event_id)
            if located is None:
                continue
            if located[0].event_type in _INDEPENDENT_SITUATION_MINT_EVENT_TYPES:
                has_mintable = True
                break
        if not has_mintable:
            return None
        anchor_id = refs[0]
        anchor_located = await self._lookup(anchor_id)
        if anchor_located is None:
            return None
        anchor = anchor_located[0]
        if logical_time < anchor.logical_time and not allow_future:
            return None
        consideration_id = situation_independent_consideration_id(
            stimulus_anchor_event_id=anchor.event_id
        )
        if consideration_id in excluded_consideration_ids:
            return None
        if self._terminal_consideration(projection, consideration_id):
            return None
        return await self._from_source(
            source_kind="situation_change",
            source_id="situation-independent:" + anchor.event_id,
            source_event_ref=anchor.event_id,
            source_world_revision=self._source_world_revision(
                projection, anchor.event_id
            ),
            consideration_id=consideration_id,
            scheduled_for=anchor.logical_time,
            cadence_reason_codes=(
                SHARED_OUTREACH_BUDGET_REASON,
                SITUATION_INDEPENDENT_OCCASION_REASON,
                "stimulus:situation_change",
            ),
            stimulus_event_refs=refs,
        )

    async def _long_silence_contact(
        self,
        projection,
        logical_time: datetime,
        *,
        excluded_consideration_ids: frozenset[str],
        record_draw: bool = True,
        allow_future: bool = False,
    ):
        """Sparse long-silence consider after ambient closes (tier A / S18).

        Randomness chooses whether/when the opportunity appears. Once due,
        deliberation is a full proactive consider: she may speak, wait, or
        stay silent. No timer writes prose.
        """

        if is_overnight_local(logical_time):
            return None
        if self._policy.consideration_band_override_seconds is not None:
            return None
        if not self._shared_outreach_budget_allows(projection, logical_time):
            return None
        observed = await self._latest_user_observation_source(projection)
        if observed is None:
            return None
        latest, source = observed
        elapsed = (logical_time - source[0].logical_time).total_seconds()
        if not self._ambient_window_closed(elapsed_seconds=elapsed):
            return None
        try:
            pending = pending_response_expectation(projection)
        except (TypeError, ValueError, AttributeError):
            pending = None
        if pending is not None:
            return None
        delay_low, delay_high = self._policy.long_silence_delay_band_seconds
        mid = (delay_low + delay_high) // 2
        candidates = tuple(dict.fromkeys((delay_low, mid, delay_high)))
        earliest_possible = source[0].logical_time + timedelta(
            seconds=self._policy.spontaneous_expiry_seconds + delay_low
        )
        if logical_time < earliest_possible and not allow_future:
            return None
        weights = {f"delay:{seconds}": 3_333 for seconds in candidates}
        if len(candidates) == 3:
            weights = {
                f"delay:{candidates[0]}": 2_500,
                f"delay:{candidates[1]}": 5_000,
                f"delay:{candidates[2]}": 2_500,
            }
        attempt_id = (
            "social-initiative:long-silence:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "source_event_ref": source[0].event_id,
                        "delay_candidates_seconds": candidates,
                        "policy": "long-silence.1",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        draw = await self._recorded_random_draw(projection, attempt_id)
        if draw is None and not record_draw:
            # Peek only: expose the soonest possible due without recording.
            earliest = source[0].logical_time + timedelta(
                seconds=self._policy.spontaneous_expiry_seconds + delay_low
            )
            if logical_time < earliest and not allow_future:
                return None
            local_day = _local_day_key(
                earliest, timezone_name=self._policy.local_timezone
            )
            consideration_id = long_silence_consideration_id(
                observation_id=latest.observation_id,
                local_day=local_day,
                delay_seconds=delay_low,
            )
            if consideration_id in excluded_consideration_ids:
                return None
            if self._terminal_consideration(projection, consideration_id):
                return None
            return SocialInitiativeOpportunity(
                source_kind="long_silence",
                source_id=f"long-silence:peek:{local_day}",
                source_event_ref=source[0].event_id,
                source_event_hash=source[0].payload_hash,
                source_world_revision=latest.world_revision,
                trace_id=source[0].trace_id,
                correlation_id=source[0].correlation_id,
                created_at=source[0].created_at,
                consideration_id=consideration_id,
                scheduled_for=earliest,
                cadence_reason_codes=(
                    SHARED_OUTREACH_BUDGET_REASON,
                    LONG_SILENCE_OCCASION_REASON,
                ),
            )
        if draw is None:
            draw_kwargs = dict(
                attempt_id=attempt_id,
                candidate_refs=tuple(f"delay:{seconds}" for seconds in candidates),
                candidate_weights=weights,
                weight_policy_version="long-silence.1",
                catalog_version="social-initiative-long-silence.1",
                logical_time=logical_time,
                seed_instant=source[0].logical_time,
                actor="system:social-initiative",
                trace_id=source[0].trace_id,
                correlation_id=source[0].correlation_id,
            )
            draw = (
                await asyncio.to_thread(self._random.draw, **draw_kwargs)
                if self._ledger.blocks_event_loop
                else self._random.draw(**draw_kwargs)
            )
        try:
            delay_seconds = int(draw.selected_candidate_ref.removeprefix("delay:"))
        except (AttributeError, ValueError):
            raise ValueError("long silence cadence draw did not select a delay")
        if delay_seconds not in candidates:
            raise ValueError("long silence cadence draw selected an unknown delay")
        scheduled_for = source[0].logical_time + timedelta(
            seconds=self._policy.spontaneous_expiry_seconds + delay_seconds
        )
        if logical_time < scheduled_for and not allow_future:
            return None
        local_day = _local_day_key(
            scheduled_for, timezone_name=self._policy.local_timezone
        )
        consideration_id = long_silence_consideration_id(
            observation_id=latest.observation_id,
            local_day=local_day,
            delay_seconds=delay_seconds,
        )
        if consideration_id in excluded_consideration_ids:
            return None
        if self._terminal_consideration(projection, consideration_id):
            return None
        clock_ref = min(
            (
                item
                for item in projection.committed_world_event_refs
                if item.event_type == "ClockAdvanced"
                and scheduled_for <= item.logical_time <= logical_time
            ),
            key=lambda item: (item.logical_time, item.event_id),
            default=None,
        )
        if clock_ref is None:
            return None
        return await self._from_source(
            source_kind="long_silence",
            source_id=f"long-silence:{local_day}",
            source_event_ref=clock_ref.event_id,
            source_world_revision=clock_ref.world_revision,
            consideration_id=consideration_id,
            scheduled_for=scheduled_for,
            cadence_reason_codes=(
                SHARED_OUTREACH_BUDGET_REASON,
                LONG_SILENCE_OCCASION_REASON,
            ),
        )

    async def _spontaneous_contact(
        self,
        projection,
        logical_time: datetime,
        *,
        record_draw: bool = True,
        allow_future: bool = False,
    ):
        # Hard boundary: do not offer a morning ping before local 07:00.
        # She still chooses now / later / silent once the day starts.
        if is_overnight_local(logical_time):
            return None
        if not projection.message_observations:
            return None
        latest = projection.message_observations[-1]
        source = await self._lookup(f"event:observation:{latest.observation_id}")
        if source is None:
            # Observation event ids are deployment-defined; resolve by the
            # exact committed revision retained in the projection instead.
            ref = next(
                (
                    item
                    for item in projection.committed_world_event_refs
                    if item.world_revision == latest.world_revision
                    and item.event_type == "ObservationRecorded"
                ),
                None,
            )
            source = await self._lookup(ref.event_id) if ref is not None else None
        if source is None or source[0].event_type != "ObservationRecorded":
            return None
        elapsed = (logical_time - source[0].logical_time).total_seconds()
        if elapsed < self._policy.spontaneous_idle_seconds:
            return None
        if elapsed >= self._policy.spontaneous_expiry_seconds:
            # Test/qualification overrides pin a deterministic band and do not
            # intend an ambient backfill after that band closes.
            if self._policy.consideration_band_override_seconds is not None:
                return None
            if elapsed >= self._policy.spontaneous_expiry_seconds + _AMBIENT_EXPIRY_GRACE_SECONDS:
                return None
        # Expiry switches the source_kind to ambient_presence below for the
        # first wake after the spontaneous window closes; later stale context
        # is dropped, never backfilled.
        try:
            pending = pending_response_expectation(projection)
        except (TypeError, ValueError, AttributeError):
            pending = None
        if pending is not None:
            return None
        profile = self._context.compile(projection=projection, logical_time=logical_time)
        attempt_id = social_initiative_attempt_id(
            source_event_ref=source[0].event_id,
            profile=profile,
            policy_version=self._context.version,
        )
        draw_kwargs = dict(
            attempt_id=attempt_id,
            candidate_refs=tuple(
                f"delay:{seconds}" for seconds in profile.delay_candidates_seconds
            ),
            candidate_weights=profile.candidate_weights,
            weight_policy_version=self._context.version,
            catalog_version="social-initiative-delay.1",
            logical_time=logical_time,
            seed_instant=source[0].logical_time,
            actor="system:social-initiative",
            trace_id=source[0].trace_id,
            correlation_id=source[0].correlation_id,
        )
        draw = await self._recorded_random_draw(projection, attempt_id)
        if draw is None:
            if not record_draw:
                return None
            draw = (
                await asyncio.to_thread(self._random.draw, **draw_kwargs)
                if self._ledger.blocks_event_loop
                else self._random.draw(**draw_kwargs)
            )
        try:
            delay_seconds = int(draw.selected_candidate_ref.removeprefix("delay:"))
        except (AttributeError, ValueError):
            raise ValueError("social initiative cadence draw did not select a delay")
        if delay_seconds not in profile.delay_candidates_seconds:
            raise ValueError("social initiative cadence draw selected an unknown delay")
        if elapsed < delay_seconds and not allow_future:
            return None
        consideration_epoch = max(0, int(elapsed // delay_seconds) - 1)
        ambient = elapsed >= self._policy.spontaneous_expiry_seconds
        consideration_id = social_initiative_consideration_id(
            attempt_id=attempt_id,
            delay_seconds=delay_seconds,
            epoch=consideration_epoch,
            source_kind="ambient_presence" if ambient else "spontaneous_contact",
        )
        source_kind = "ambient_presence" if ambient else "spontaneous_contact"
        source_id = latest.observation_id
        source_event_ref = source[0].event_id
        source_world_revision = latest.world_revision
        scheduled_for = source[0].logical_time + timedelta(
            seconds=delay_seconds * (consideration_epoch + 1)
        )
        if ambient:
            clock_ref = min(
                (
                    item
                    for item in projection.committed_world_event_refs
                    if item.event_type == "ClockAdvanced"
                    and scheduled_for <= item.logical_time <= logical_time
                ),
                key=lambda item: (item.logical_time, item.event_id),
                default=None,
            )
            if clock_ref is None:
                return None
            source_id = f"ambient:{consideration_epoch}"
            source_event_ref = clock_ref.event_id
            source_world_revision = clock_ref.world_revision
        opportunity = await self._from_source(
            source_kind=source_kind,
            source_id=source_id,
            source_event_ref=source_event_ref,
            source_world_revision=source_world_revision,
            consideration_id=consideration_id,
            consideration_epoch=consideration_epoch,
            scheduled_for=scheduled_for,
            cadence_reason_codes=profile.reason_codes,
        )
        current = next(
            (
                item
                for item in reversed(getattr(projection, "trigger_processes", ()))
                if item.process_kind == "proactive_action_deliberation"
                and item.trigger_ref == "proactive-consideration:" + consideration_id
            ),
            None,
        )
        if current is not None and current.state == "terminal":
            # A completed epoch is effect-once. A failed epoch is returned only
            # by the retry path once its technical backoff is actually due.
            return None
        return opportunity

    async def _from_source(
        self,
        *,
        source_kind,
        source_id: str,
        source_event_ref: str,
        source_world_revision: int,
        consideration_id: str | None = None,
        consideration_epoch: int = 0,
        scheduled_for: datetime | None = None,
        cadence_reason_codes: tuple[str, ...] = (),
        stimulus_event_refs: tuple[str, ...] = (),
    ):
        located = await self._lookup(source_event_ref)
        if located is None:
            return None
        event, commit = located
        return SocialInitiativeOpportunity(
            source_kind=source_kind,
            source_id=source_id,
            source_event_ref=event.event_id,
            source_event_hash=event.payload_hash,
            source_world_revision=source_world_revision,
            trace_id=event.trace_id,
            correlation_id=event.correlation_id,
            created_at=event.created_at,
            consideration_id=(
                consideration_id
                or "consideration:social-initiative:"
                + hashlib.sha256(
                    f"{source_kind}:{event.event_id}".encode()
                ).hexdigest()
            ),
            consideration_epoch=consideration_epoch,
            scheduled_for=scheduled_for or event.logical_time,
            cadence_reason_codes=cadence_reason_codes,
            stimulus_event_refs=stimulus_event_refs,
        )

    async def _lookup(self, event_id: str):
        if self._ledger.blocks_event_loop:
            import asyncio

            return await asyncio.to_thread(self._ledger.lookup_event_commit, event_id)
        return self._ledger.lookup_event_commit(event_id)


def technical_failure_point(*, projection, process) -> tuple[int | None, datetime | None]:
    """Locate the durable failure boundary shared by retry and health readers."""

    result_ref = str(process.runtime_outcome_ref).removeprefix(
        "proactive:deliberation-failed:"
    )
    audit = next(
        (
            item
            for item in reversed(projection.model_result_audits)
            if item.model_result_ref == result_ref
            and item.proposal_hash is None
        ),
        None,
    )
    if audit is None:
        return None, None
    committed = next(
        (
            item
            for item in projection.committed_world_event_refs
            if item.event_id == audit.event_ref
        ),
        None,
    )
    return (
        committed.world_revision
        if committed is not None
        else audit.evaluated_world_revision,
        committed.logical_time
        if committed is not None
        else (
            process.claim_lease.acquired_at
            if process.claim_lease is not None
            else None
        ),
    )


__all__ = [
    "LONG_SILENCE_OCCASION_REASON",
    "LONG_SILENCE_OPPORTUNITY_CONTEXT",
    "PRIVATE_IMPRESSION_OCCASION_REASON",
    "PRIVATE_IMPRESSION_OPPORTUNITY_CONTEXT",
    "SHARED_OUTREACH_BUDGET_REASON",
    "SITUATION_INDEPENDENT_OCCASION_REASON",
    "SocialInitiativeCompiler",
    "SocialInitiativeContextPolicy",
    "SocialInitiativeDecisionProfile",
    "SocialInitiativeOpportunity",
    "SocialInitiativePolicy",
    "SocialInitiativeSourceKind",
    "is_shared_outreach_consideration_id",
    "living_private_impression",
    "long_silence_consideration_id",
    "long_silence_opportunity_context",
    "private_impression_consideration_id",
    "private_impression_opportunity_context",
    "private_impression_source_binds_head",
    "situation_independent_consideration_id",
    "situation_stimulus_is_observable",
    "social_initiative_attempt_id",
    "social_initiative_consideration_id",
    "post_silent_attempt_id",
    "post_silent_consideration_id",
    "post_silent_prior_trigger_id",
    "technical_failure_point",
]
