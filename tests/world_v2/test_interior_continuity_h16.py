from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json

import pytest

from companion_daemon.world_v2.character_interior import (
    CharacterInterior,
    InteriorOpportunity,
)
from companion_daemon.world_v2.occasion import (
    OCCASION_KINDS,
    OccasionAlreadyConsidered,
    OccasionConsiderGate,
    InMemoryOccasionSpendStore,
    mint_day_open,
    mint_life_beat,
    mint_occasion,
    mint_quiet_gap,
    mint_unsettled_feeling,
    mint_user_message,
    occasion_is_expired,
)
from companion_daemon.world_v2.schemas import ProjectionCursor


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
CURSOR = ProjectionCursor(
    world_revision=3,
    deliberation_revision=1,
    ledger_sequence=8,
)
_FACETS = (
    "private_self",
    "selective_memory",
    "appraisal_affect",
    "emotional_continuity",
    "subjective_relationship",
    "aspirations_conflicts",
    "autonomous_impulses",
    "expression_stance",
)


def test_all_five_occasion_kinds_mint_with_merge_key_and_expiry() -> None:
    helpers = (
        mint_user_message(source_event_ref="event:obs:1", created_at=NOW),
        mint_quiet_gap(source_event_ref="event:obs:2", created_at=NOW),
        mint_unsettled_feeling(source_event_ref="event:appraisal:1", created_at=NOW),
        mint_life_beat(source_event_ref="event:clock:1", created_at=NOW),
        mint_day_open(
            source_event_ref="event:clock:1",
            created_at=NOW,
            merge_key="2026-08-14",
        ),
    )

    assert OCCASION_KINDS == tuple(item.kind for item in helpers)
    for item in helpers:
        assert item.merge_key
        assert item.expires_at is not None
        assert item.expires_at > NOW
        assert item.occasion_id == f"occasion:{item.kind}:{item.merge_key}"


def test_sixth_occasion_kind_is_rejected() -> None:
    try:
        mint_occasion(
            kind="sixth",  # type: ignore[arg-type]
            source_event_ref="event:x",
            created_at=NOW,
        )
    except ValueError:
        return
    raise AssertionError("a sixth Occasion kind was accepted")


def test_durable_occasion_spend_rejects_after_restart() -> None:
    store = InMemoryOccasionSpendStore(world_id="world:h16")
    first = OccasionConsiderGate(store=store)
    identity = mint_user_message(source_event_ref="event:obs:1", created_at=NOW)
    first.admit(identity.occasion_id)
    first.mark_spent(identity.occasion_id)

    restarted = OccasionConsiderGate(store=store)
    try:
        restarted.admit(identity.occasion_id)
    except OccasionAlreadyConsidered:
        return
    raise AssertionError("restarted gate forgot a spent Occasion")


def _facet(name: str) -> dict[str, object]:
    return {
        "availability": "available",
        "content": {"summary": name},
        "source_refs": (f"source:{name}",),
    }


class _Projection:
    async def project(self, *, subject):
        return {
            "world_id": subject.world_id,
            "actor_ref": subject.actor_ref,
            "cursor": subject.cursor,
            "logical_time": NOW,
            "situation": {
                "availability": "available",
                "content": {"activity": "reading"},
                "source_refs": ("source:situation",),
            },
            "continuity": {
                "availability": "available",
                "content": {"open_thread": "none"},
                "source_refs": ("source:continuity",),
            },
            "facets": {name: _facet(name) for name in _FACETS},
        }


class _Role:
    name = "character-role"
    purposes = ("proactive_contact", "inbound_turn")

    def __init__(self) -> None:
        self.consider_calls = 0

    async def experience(self, request):
        return {
            "status": "transition",
            "summary": "unused",
            "attended_source_refs": ("source:appraisal_affect",),
            "proposals": (),
            "author_lineage": {
                "model_id": "character-role:test",
                "model_version": "character-role:test.1",
                "model_call_id": "model-call:test:experience",
                "request_hash": "sha256:" + ("a" * 64),
                "response_hash": "sha256:" + ("b" * 64),
                "attempt_ordinal": 0,
                "parent_model_call_id": None,
            },
        }

    async def consider(self, request):
        self.consider_calls += 1
        identity = {
            "inner_turn_id": request.inner_turn_id,
            "phase": request.phase,
            "recall_completed": request.recall_completed,
            "correction_ordinal": request.correction_ordinal,
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        return {
            "status": "decision",
            "summary": "She chose.",
            "attended_source_refs": ("source:expression_stance",),
            "decision": {"expression_mode": "reply"},
            "proposals": (),
            "author_lineage": {
                "model_id": "character-role:test",
                "model_version": "character-role:test.1",
                "model_call_id": f"model-call:test:{digest}",
                "request_hash": "sha256:" + digest,
                "response_hash": "sha256:" + hashlib.sha256(b"ok").hexdigest(),
                "attempt_ordinal": 0,
                "parent_model_call_id": None,
            },
        }


def _opportunity(**updates: object) -> InteriorOpportunity:
    values: dict[str, object] = {
        "opportunity_ref": "opportunity:h16:1",
        "inner_turn_ref": "turn:h16:1",
        "world_id": "world:h16",
        "actor_ref": "character:zhizhi",
        "trigger_ref": "event:obs:1",
        "cursor": CURSOR,
        "logical_time": NOW,
        "purpose": "proactive_contact",
        "source_refs": ("source:expression_stance",),
    }
    values.update(updates)
    return InteriorOpportunity(**values)


@pytest.mark.asyncio
async def test_expired_quiet_gap_is_dropped_before_the_role_model() -> None:
    role = _Role()
    interior = CharacterInterior(projection=_Projection(), role=role)
    expired = mint_quiet_gap(
        source_event_ref="event:obs:old",
        created_at=NOW - timedelta(hours=14),
    )
    assert occasion_is_expired(now=NOW, expires_at=expired.expires_at)

    decision = await interior.consider(
        _opportunity(
            opportunity_ref="opportunity:h16:expired",
            occasion=expired,
        )
    )

    assert decision.status == "technical_failure"
    assert decision.failure_code == "occasion_expired"
    assert role.consider_calls == 0


@pytest.mark.asyncio
async def test_quiet_gap_consider_is_once_across_the_gate() -> None:
    role = _Role()
    interior = CharacterInterior(projection=_Projection(), role=role)
    occasion = mint_quiet_gap(source_event_ref="event:obs:1", created_at=NOW)
    first = await interior.consider(_opportunity(occasion=occasion))
    second = await interior.consider(
        _opportunity(
            opportunity_ref="opportunity:h16:2",
            inner_turn_ref="turn:h16:2",
            occasion=occasion,
        )
    )

    assert first.status == "decided"
    assert second.status == "technical_failure"
    assert second.failure_code == "occasion_already_considered"
    assert role.consider_calls == 1
