"""Freshness of a delayed (``timing_choice=later``) expression before dispatch.

She may write a message now and ask the host to send it later.  That decision
is frozen into an Action ``kind=followup`` with ``not_before``.  The host must
not then fire the frozen payload after the world has moved: she spoke from
another plan, he spoke, or the conversational sitting after the due instant
has closed.  The host does not judge whether two texts "repeat".  It refuses
to auto-send the frozen payload and offers her one more consider.

This is the same disease as ``media_conversation_window``: a decision made in
one sitting must not execute unchanged in a later one.  Media measures thirty
minutes from *selection*.  Text sits in a faster conversation clock, so this
module measures TTL from *due* (``not_before``), not from write.  Her chosen
delay is honoured; the sitting clock starts when the message becomes due.

TTL follows ``conversation_cadence.DEFAULT_RHYTHM_PROFILE`` exit bands, the
same heat table wake-truth already uses for short hope wakes:

- hot (exit 120s): back-and-forth; a minute-old later is a different beat.
- warm (exit 720s): recent conversation; twelve minutes after due is a new
  chapter of the same sitting.
- cold (1800s): no live exchange; thirty minutes after due matches the media
  send window so an overnight later still has a same-morning chance.

Cadence ``now`` beats stay ``kind=reply`` even when they carry a 0.35–7s
``not_before``.  This module never treats those as later followups, so 2/3/4
consecutive bubbles are not stalled by sibling dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo

from companion_daemon.conversation_cadence import (
    DEFAULT_RHYTHM_PROFILE,
    derive_conversation_cadence,
)


LaterStaleReason = Literal["companion_spoke", "counterpart_spoke", "ttl_elapsed"]

LATER_FOLLOWUP_STATES = frozenset(
    {"authorized", "scheduled", "claimed", "dispatch_started"}
)
_VISIBLE_SPEECH_STATES = frozenset({"provider_accepted", "delivered"})
_VISIBLE_SPEECH_KINDS = frozenset({"reply", "proactive_message", "followup"})
_CONTACT_SKIP_STATES = frozenset({"failed", "cancelled", "expired"})

# Seconds after due.  Hot/warm copy rhythm *exit* so a sitting that is still
# classified that heat may still send; cold uses the media 30-minute band.
LATER_TTL_SECONDS = {
    "hot": int(DEFAULT_RHYTHM_PROFILE.hot_exit_seconds),
    "warm": int(DEFAULT_RHYTHM_PROFILE.warm_exit_seconds),
    "cold": 1_800,
}

LATER_REFRESH_CONSIDERATION_PREFIX = (
    "consideration:social-initiative:later-refresh:"
)
LATER_REFRESH_OPPORTUNITY_CONTEXT = (
    "A delayed message she already wrote is due, and the world changed "
    "since she wrote it. Timing evidence only; she still decides whether "
    "to send it, change it, or stay silent."
)


def later_refresh_opportunity_context(projection: object | None = None) -> str:
    """Context for a stale later followup refresh consider."""

    from .media_conversation_window import media_cross_lane_timing_clause

    base = LATER_REFRESH_OPPORTUNITY_CONTEXT
    if projection is None:
        return base
    clause = media_cross_lane_timing_clause(projection)
    if not clause:
        return base
    return f"{base} {clause}"[:512]

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_HASH = 64


@dataclass(frozen=True, slots=True)
class QueuedLaterExpressionFact:
    """Source-closed fact that a later followup is still waiting to send."""

    action_id: str
    plan_id: str
    beat_id: str
    text: str
    written_at: datetime
    send_at: datetime
    he_spoke_after: bool
    i_spoke_after: bool
    authority_event_ref: str
    authority_world_revision: int
    authority_payload_hash: str


def is_later_followup(action: object) -> bool:
    """True for a deferred text Action, never a cadence ``now`` reply."""

    return (
        getattr(action, "kind", None) == "followup"
        and getattr(action, "not_before", None) is not None
        and getattr(action, "expression_plan_id", None) is not None
        and getattr(action, "expression_beat_id", None) is not None
    )


def later_ttl_seconds(heat: str) -> int:
    return LATER_TTL_SECONDS.get(heat, LATER_TTL_SECONDS["cold"])


def later_refresh_consideration_id(action_id: str) -> str:
    """Return one effect-once consideration identity for a stale later Action."""

    return LATER_REFRESH_CONSIDERATION_PREFIX + hashlib.sha256(
        json.dumps(
            {"action_id": action_id},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def later_refresh_process(projection: object, *, action_id: str):
    prefix = "proactive-consideration:" + later_refresh_consideration_id(action_id)
    for process in getattr(projection, "trigger_processes", ()) or ():
        if (
            getattr(process, "process_kind", None) == "proactive_action_deliberation"
            and getattr(process, "trigger_ref", None) == prefix
        ):
            return process
    return None


def later_refresh_is_terminal(projection: object, action_id: str) -> bool:
    process = later_refresh_process(projection, action_id=action_id)
    return process is not None and getattr(process, "state", None) == "terminal"


def later_refresh_action_id_matching(projection: object, consideration_id: str) -> str | None:
    if not isinstance(consideration_id, str) or not consideration_id.startswith(
        LATER_REFRESH_CONSIDERATION_PREFIX
    ):
        return None
    for action in getattr(projection, "actions", ()) or ():
        action_id = getattr(action, "action_id", None)
        if isinstance(action_id, str) and later_refresh_consideration_id(action_id) == consideration_id:
            return action_id
    return None


def conversation_heat_at(projection: object, logical_time: datetime) -> str:
    """Classify sitting heat at dispatch from in/out lines up to ``logical_time``."""

    recent: list[dict[str, str]] = []
    for ref in getattr(projection, "committed_world_event_refs", ()) or ():
        if getattr(ref, "event_type", None) != "ObservationRecorded":
            continue
        at = getattr(ref, "logical_time", None)
        if not isinstance(at, datetime) or at > logical_time:
            continue
        recent.append(
            {"user_id": "", "direction": "in", "observed_at": at.isoformat()}
        )
    for action in getattr(projection, "actions", ()) or ():
        if (
            getattr(action, "kind", None) not in _VISIBLE_SPEECH_KINDS
            or getattr(action, "state", None) not in _VISIBLE_SPEECH_STATES
        ):
            continue
        at = getattr(action, "logical_time", None)
        if not isinstance(at, datetime) or at > logical_time:
            continue
        recent.append(
            {"user_id": "", "direction": "out", "observed_at": at.isoformat()}
        )
    recent.sort(key=lambda item: item["observed_at"])
    previous_heat = None
    if len(recent) >= 2:
        try:
            prior_at = datetime.fromisoformat(recent[-2]["observed_at"])
        except ValueError:
            prior_at = None
        if prior_at is not None:
            previous_heat = derive_conversation_cadence(
                {"recent_messages": recent[:-1]},
                user_id="later-cadence",
                observed_at=prior_at,
            ).heat
    return derive_conversation_cadence(
        {"recent_messages": recent},
        user_id="later-cadence",
        observed_at=logical_time,
        previous_heat=previous_heat,
    ).heat


def counterpart_spoke_after(projection: object, *, written_at: datetime) -> bool:
    return any(
        getattr(ref, "event_type", None) == "ObservationRecorded"
        and isinstance(getattr(ref, "logical_time", None), datetime)
        and ref.logical_time > written_at
        for ref in getattr(projection, "committed_world_event_refs", ()) or ()
    )


def companion_spoke_after(
    projection: object, *, written_at: datetime, plan_id: str | None
) -> bool:
    """True when a *different* plan became provider-visible after this write.

    Same-plan cadence siblings do not count.  Another queued later does not
    count until it is actually accepted or delivered.
    """

    for action in getattr(projection, "actions", ()) or ():
        if (
            getattr(action, "kind", None) not in _VISIBLE_SPEECH_KINDS
            or getattr(action, "state", None) not in _VISIBLE_SPEECH_STATES
        ):
            continue
        other_plan = getattr(action, "expression_plan_id", None)
        if plan_id is not None and other_plan == plan_id:
            continue
        at = getattr(action, "logical_time", None)
        if isinstance(at, datetime) and at > written_at:
            return True
    return False


def later_stale_reason(
    projection: object, action: object, logical_time: datetime
) -> LaterStaleReason | None:
    """Why a due later followup must not be dispatched as the frozen payload.

    Order is ownership, then world-changed, then sitting TTL: if he spoke,
    inbound reconsideration owns the beat; if she spoke from another plan,
    this later is stale; if neither happened, the due sitting may still
    lapse.
    """

    if not is_later_followup(action):
        return None
    written_at = getattr(action, "logical_time", None)
    not_before = getattr(action, "not_before", None)
    plan_id = getattr(action, "expression_plan_id", None)
    if not isinstance(written_at, datetime) or not isinstance(not_before, datetime):
        return "ttl_elapsed"
    if counterpart_spoke_after(projection, written_at=written_at):
        return "counterpart_spoke"
    if companion_spoke_after(projection, written_at=written_at, plan_id=plan_id):
        return "companion_spoke"
    heat = conversation_heat_at(projection, logical_time)
    ttl = timedelta(seconds=later_ttl_seconds(heat))
    if logical_time >= not_before + ttl:
        return "ttl_elapsed"
    return None


def later_dispatch_allowed(
    projection: object, action: object, logical_time: datetime | None
) -> bool:
    """Fail closed for a later followup whose sitting is no longer current."""

    if not is_later_followup(action):
        return True
    if logical_time is None:
        return False
    state = getattr(action, "state", None)
    if state in _CONTACT_SKIP_STATES:
        return False
    return later_stale_reason(projection, action, logical_time) is None


def _beat_for_action(projection: object, action: object):
    beat_id = getattr(action, "expression_beat_id", None)
    for beat in getattr(projection, "expression_beats", ()) or ():
        if getattr(beat, "beat_id", None) == beat_id:
            return beat
    return None


def _payload_text(projection: object, *, payload_ref: str | None) -> str | None:
    if not isinstance(payload_ref, str) or not payload_ref:
        return None
    for item in getattr(projection, "stored_message_payloads", ()) or ():
        if getattr(item, "payload_ref", None) == payload_ref:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                return text
    return None


def _authority_for_beat(projection: object, beat: object):
    event_ref = getattr(beat, "event_ref", None)
    if not isinstance(event_ref, str) or not event_ref:
        return None
    for ref in getattr(projection, "committed_world_event_refs", ()) or ():
        if getattr(ref, "event_id", None) != event_ref:
            continue
        payload_hash = getattr(ref, "payload_hash", None)
        world_revision = getattr(ref, "world_revision", None)
        if (
            isinstance(payload_hash, str)
            and len(payload_hash) == _HASH
            and isinstance(world_revision, int)
            and world_revision >= 1
        ):
            return ref
    return None


def iter_queued_later_followups(projection: object) -> tuple[object, ...]:
    """Authorized later Actions still waiting, including ones not yet due.

    After she has already re-decided (terminal later-refresh), the zombie
    frozen Action is hidden so it does not keep looking like a live queue.
    """

    items = []
    for action in getattr(projection, "actions", ()) or ():
        if not is_later_followup(action):
            continue
        if getattr(action, "state", None) not in LATER_FOLLOWUP_STATES:
            continue
        action_id = getattr(action, "action_id", None)
        if not isinstance(action_id, str):
            continue
        if later_refresh_is_terminal(projection, action_id):
            continue
        items.append(action)
    items.sort(
        key=lambda item: (
            getattr(item, "not_before", None) or getattr(item, "logical_time"),
            getattr(item, "action_id", ""),
        )
    )
    return tuple(items)


def queued_later_facts(
    projection: object, *, logical_time: datetime | None
) -> tuple[QueuedLaterExpressionFact, ...]:
    """Compile queued later followups as facts. Missing text or authority drops the row."""

    del logical_time
    facts: list[QueuedLaterExpressionFact] = []
    for action in iter_queued_later_followups(projection):
        beat = _beat_for_action(projection, action)
        if beat is None:
            continue
        text = _payload_text(
            projection,
            payload_ref=getattr(action, "payload_ref", None)
            or getattr(beat, "payload_ref", None),
        )
        authority = _authority_for_beat(projection, beat)
        written_at = getattr(action, "logical_time", None)
        send_at = getattr(action, "not_before", None)
        plan_id = getattr(action, "expression_plan_id", None)
        beat_id = getattr(action, "expression_beat_id", None)
        action_id = getattr(action, "action_id", None)
        if (
            not isinstance(text, str)
            or authority is None
            or not isinstance(written_at, datetime)
            or not isinstance(send_at, datetime)
            or not isinstance(plan_id, str)
            or not isinstance(beat_id, str)
            or not isinstance(action_id, str)
        ):
            continue
        facts.append(
            QueuedLaterExpressionFact(
                action_id=action_id,
                plan_id=plan_id,
                beat_id=beat_id,
                text=text,
                written_at=written_at,
                send_at=send_at,
                he_spoke_after=counterpart_spoke_after(
                    projection, written_at=written_at
                ),
                i_spoke_after=companion_spoke_after(
                    projection, written_at=written_at, plan_id=plan_id
                ),
                authority_event_ref=authority.event_id,
                authority_world_revision=authority.world_revision,
                authority_payload_hash=authority.payload_hash,
            )
        )
    return tuple(facts)


def due_stale_later_actions(
    projection: object, logical_time: datetime
) -> tuple[object, ...]:
    """Later followups that are due, still live, and no longer current."""

    found = []
    for action in iter_queued_later_followups(projection):
        not_before = getattr(action, "not_before", None)
        if not isinstance(not_before, datetime) or logical_time < not_before:
            continue
        expires_at = getattr(action, "expires_at", None)
        if isinstance(expires_at, datetime) and logical_time >= expires_at:
            continue
        if later_stale_reason(projection, action, logical_time) is None:
            continue
        found.append(action)
    return tuple(found)


def later_refresh_source_binds_head(
    *, projection, event, opportunity
) -> bool:
    """True when this consider still names the due stale later Action."""

    from .expression_reconsideration import expression_beat_is_gated

    if getattr(event, "event_type", None) != "ExpressionBeatAuthorized":
        return False
    action_id = getattr(opportunity, "source_id", None)
    action = next(
        (
            item
            for item in getattr(projection, "actions", ()) or ()
            if getattr(item, "action_id", None) == action_id
        ),
        None,
    )
    logical_time = getattr(projection, "logical_time", None)
    if (
        action is None
        or not is_later_followup(action)
        or not isinstance(logical_time, datetime)
        or getattr(action, "state", None) not in LATER_FOLLOWUP_STATES
    ):
        return False
    not_before = getattr(action, "not_before", None)
    if not isinstance(not_before, datetime) or logical_time < not_before:
        return False
    beat = _beat_for_action(projection, action)
    if beat is None or getattr(beat, "event_ref", None) != getattr(event, "event_id", None):
        return False
    plan_id = getattr(action, "expression_plan_id", None)
    beat_id = getattr(action, "expression_beat_id", None)
    if not isinstance(plan_id, str) or not isinstance(beat_id, str):
        return False
    if expression_beat_is_gated(projection=projection, plan_id=plan_id, beat_id=beat_id):
        return False
    return later_stale_reason(projection, action, logical_time) is not None


def local_clock_hhmm(instant: datetime) -> str:
    return instant.astimezone(_SHANGHAI).strftime("%H:%M")


__all__ = [
    "LATER_FOLLOWUP_STATES",
    "LATER_REFRESH_CONSIDERATION_PREFIX",
    "LATER_REFRESH_OPPORTUNITY_CONTEXT",
    "LATER_TTL_SECONDS",
    "QueuedLaterExpressionFact",
    "companion_spoke_after",
    "conversation_heat_at",
    "counterpart_spoke_after",
    "due_stale_later_actions",
    "is_later_followup",
    "iter_queued_later_followups",
    "later_dispatch_allowed",
    "later_refresh_action_id_matching",
    "later_refresh_consideration_id",
    "later_refresh_is_terminal",
    "later_refresh_opportunity_context",
    "later_refresh_source_binds_head",
    "later_stale_reason",
    "later_ttl_seconds",
    "local_clock_hhmm",
    "queued_later_facts",
]
