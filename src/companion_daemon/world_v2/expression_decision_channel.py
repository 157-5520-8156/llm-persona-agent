"""Gate: every allowed decision stays reachable on every expression format.

The recurring failure shape is: she authors a decision, the transport format
she picked cannot carry it, and the host either rejects with a branch-change
nudge or silently strips the field so text still lands.  From her side the
turn succeeded; the external effect never started.

This module enumerates decisions that are installed as allowed when their
capability is on, and asserts each is expressible on every installed format
without silent drop.  Format-exclusive surfaces (interaction protocol,
typing/reaction, continuation) stay listed as explicit exclusions—attempting
them on reply_only must remain a *visible* failure, never a quiet strip.

Media intent is the first closed case: ``photo`` / ``media_request`` must
survive both reply_only and full_turn when timing is now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .present_prompt import (
    SLIM_OPTIONAL_SPECIMEN_KEYS,
    compile_slim_interior_envelope,
    reply_only_slim_shape_specimen,
    slim_consider_json_schema,
)


ExpressionFormat = str  # reply_only_slim | reply_only_events | full_turn


@dataclass(frozen=True)
class DecisionChannel:
    """One role-owned decision that must not silently fall out of a format."""

    decision_id: str
    formats: frozenset[ExpressionFormat]
    prove: Callable[[], None]
    notes: str = ""


def _require_media_on_head(envelope: dict[str, object]) -> None:
    events = envelope.get("events")
    if not isinstance(events, list) or not events:
        raise AssertionError("compiled envelope has no head event")
    head = events[0]
    if not isinstance(head, dict):
        raise AssertionError("compiled envelope head is not an object")
    if head.get("media_request") != "consider_available_candidate":
        raise AssertionError(
            "media intent was dropped or rewritten: "
            f"media_request={head.get('media_request')!r}"
        )


def _prove_media_intent_reply_only_slim() -> None:
    slim = {
        "messages": ["这就发你"],
        "felt": "想分享",
        "photo": True,
    }
    envelope = compile_slim_interior_envelope(slim, reply_only=True)
    if envelope is None:
        raise AssertionError("reply_only slim photo=true did not compile")
    _require_media_on_head(envelope)


def _prove_media_intent_reply_only_events() -> None:
    # Canonical reply_only events head: media must survive validation path via
    # the same slim compiler (events specimen expands through it).
    slim = {
        "messages": ["给你看一张"],
        "felt": "想分享",
        "photo": "event:shareable-photo:demo",
    }
    envelope = compile_slim_interior_envelope(slim, reply_only=True)
    if envelope is None:
        raise AssertionError("reply_only events photo source_ref did not compile")
    _require_media_on_head(envelope)
    head = envelope["events"][0]
    assert isinstance(head, dict)
    if head.get("media_source_refs") != ["event:shareable-photo:demo"]:
        raise AssertionError(
            "media_source_refs silently dropped on reply_only events path: "
            f"{head.get('media_source_refs')!r}"
        )


def _prove_media_intent_full_turn() -> None:
    slim = {
        "messages": ["发你一张"],
        "felt": "想分享",
        "photo": True,
    }
    envelope = compile_slim_interior_envelope(slim, reply_only=False)
    if envelope is None:
        raise AssertionError("full_turn photo=true did not compile")
    _require_media_on_head(envelope)


def _prove_media_intent_later_is_visible_reject() -> None:
    slim = {
        "messages": ["晚点发你"],
        "felt": "想分享",
        "later": 60,
        "photo": True,
    }
    try:
        compile_slim_interior_envelope(slim, reply_only=True)
    except ValueError as exc:
        if "exceeds text-only capability" not in str(exc) and "later" not in str(exc).lower():
            raise AssertionError(
                f"later+photo must fail visibly, got: {exc}"
            ) from exc
        return
    raise AssertionError("later+photo on reply_only must be a visible failure")


def _prove_slim_specimen_advertises_photo() -> None:
    specimen = reply_only_slim_shape_specimen()
    if "photo" not in specimen:
        raise AssertionError("reply_only slim specimen must advertise photo")
    schema = slim_consider_json_schema()
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "photo" not in properties:
        raise AssertionError("slim schema must list photo")
    if "photo" not in SLIM_OPTIONAL_SPECIMEN_KEYS:
        raise AssertionError("photo missing from SLIM_OPTIONAL_SPECIMEN_KEYS")


# Decisions that are allowed when their capability is on, and that every listed
# format must be able to carry without silent drop.  Adding a new role-owned
# effect here without a prove() that succeeds on each format turns the gate red.
_MEDIA_FORMATS = frozenset({"reply_only_slim", "reply_only_events", "full_turn"})

INSTALLED_DECISION_CHANNELS: tuple[DecisionChannel, ...] = (
    DecisionChannel(
        decision_id="media_intent",
        formats=_MEDIA_FORMATS,
        prove=_prove_media_intent_reply_only_slim,
        notes="reply_only slim photo=true keeps media_request",
    ),
    DecisionChannel(
        decision_id="media_intent",
        formats=_MEDIA_FORMATS,
        prove=_prove_media_intent_reply_only_events,
        notes="reply_only events photo source_ref keeps media_source_refs",
    ),
    DecisionChannel(
        decision_id="media_intent",
        formats=_MEDIA_FORMATS,
        prove=_prove_media_intent_full_turn,
        notes="full_turn photo=true keeps media_request",
    ),
    DecisionChannel(
        decision_id="media_intent_later_visible_reject",
        formats=frozenset({"reply_only_slim"}),
        prove=_prove_media_intent_later_is_visible_reject,
        notes="later+photo remains a visible reject, not a silent strip",
    ),
    DecisionChannel(
        decision_id="media_intent_advertised",
        formats=frozenset({"reply_only_slim"}),
        prove=_prove_slim_specimen_advertises_photo,
        notes="specimen/schema that advertise photo must not lie",
    ),
)


def required_decision_ids() -> frozenset[str]:
    return frozenset(item.decision_id for item in INSTALLED_DECISION_CHANNELS)


def formats_covering(decision_id: str) -> frozenset[ExpressionFormat]:
    covered: set[ExpressionFormat] = set()
    for item in INSTALLED_DECISION_CHANNELS:
        if item.decision_id == decision_id or item.decision_id.startswith(
            f"{decision_id}_"
        ):
            covered |= set(item.formats)
    return frozenset(covered)


def assert_expression_decision_channel_coverage() -> None:
    """Fail closed when an installed decision cannot ride an installed format.

    Style matches ``assert_declared_due_wake_coverage``: enumerate × prove, so a
    new decision that is only wired on full_turn turns CI red before production
    silently drops it on reply_only.
    """

    if not INSTALLED_DECISION_CHANNELS:
        raise AssertionError("no expression decision channels installed")

    media_formats = formats_covering("media_intent")
    missing = _MEDIA_FORMATS - media_formats
    if missing:
        raise AssertionError(
            "media_intent missing format coverage: " + ", ".join(sorted(missing))
        )

    failures: list[str] = []
    for channel in INSTALLED_DECISION_CHANNELS:
        try:
            channel.prove()
        except Exception as exc:  # noqa: BLE001 - gate must surface every miss
            failures.append(f"{channel.decision_id} ({channel.notes}): {exc}")
    if failures:
        raise AssertionError(
            "expression decision channel gaps:\n- " + "\n- ".join(failures)
        )


__all__ = (
    "DecisionChannel",
    "INSTALLED_DECISION_CHANNELS",
    "assert_expression_decision_channel_coverage",
    "formats_covering",
    "required_decision_ids",
)
