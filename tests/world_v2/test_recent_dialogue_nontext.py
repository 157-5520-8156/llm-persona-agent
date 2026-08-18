"""A textless reaction is still him speaking and must enter recent dialogue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib

from companion_daemon.world_v2.conversation_continuity import ConversationContinuityCompiler
from companion_daemon.world_v2.recent_dialogue import (
    RecentDialogueCompiler,
    observation_dialogue_text,
)
from companion_daemon.world_v2.schemas import (
    CommittedWorldEventRef,
    LedgerProjection,
    MessageObservationRef,
    Observation,
    WorldEvent,
)


NOW = datetime(2026, 8, 18, 15, 14, 28, tzinfo=UTC)
WORLD_ID = "world:recent-dialogue-nontext"
PHOTO = "好！"
SUN_EXPLAINED = "是个太阳的表情来着，表示我今天心情不错"


class _EventLookup:
    def __init__(self, events: tuple[WorldEvent, ...]) -> None:
        self._events = {item.event_id: item for item in events}

    def lookup_event_commit(self, event_id: str):  # type: ignore[no-untyped-def]
        event = self._events.get(event_id)
        return None if event is None else (event, object())


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _event_ref(event: WorldEvent, *, world_revision: int) -> CommittedWorldEventRef:
    return CommittedWorldEventRef(
        event_id=event.event_id,
        event_type=event.event_type,
        world_revision=world_revision,
        payload_hash=event.payload_hash,
        logical_time=event.logical_time,
    )


def _world_event(
    *,
    event_id: str,
    event_type: str,
    logical_time: datetime,
    payload: dict[str, object],
) -> WorldEvent:
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=event_id,
        world_id=WORLD_ID,
        event_type=event_type,
        logical_time=logical_time,
        created_at=logical_time,
        actor="system:test",
        source="test",
        trace_id=f"trace:{event_id}",
        causation_id=f"cause:{event_id}",
        correlation_id="conversation:nontext",
        idempotency_key=f"idempotency:{event_id}",
        payload=payload,
    )


def _observation(
    *,
    suffix: str,
    at: datetime,
    text: str | None,
    coalescing_metadata: dict[str, object] | None = None,
) -> Observation:
    body = text or "reaction"
    return Observation(
        schema_version="world-v2.1",
        observation_id=f"observation:{suffix}",
        world_id=WORLD_ID,
        logical_time=at,
        created_at=at,
        trace_id=f"trace:observation:{suffix}",
        causation_id=f"message:{suffix}",
        correlation_id="conversation:nontext",
        source="platform:qq",
        source_event_id=f"message:{suffix}",
        actor="user:primary",
        channel="qq_c2c",
        payload_ref=f"payload:observation:{suffix}",
        payload_hash="sha256:" + _hash(body),
        text=text,
        received_at=at,
        coalescing_metadata=coalescing_metadata or {},
    )


def _append_observation(
    *,
    observation: Observation,
    revision: int,
) -> tuple[WorldEvent, CommittedWorldEventRef, MessageObservationRef]:
    event = _world_event(
        event_id=f"event:observation:{observation.observation_id}",
        event_type="ObservationRecorded",
        logical_time=observation.logical_time,
        payload=observation.model_dump(mode="json"),
    )
    ref = _event_ref(event, world_revision=revision)
    message = MessageObservationRef(
        observation_id=observation.observation_id,
        source=observation.source,
        source_event_id=observation.source_event_id,
        content_payload_hash=observation.payload_hash,
        event_payload_hash=event.payload_hash,
        world_revision=ref.world_revision,
        actor=observation.actor,
        channel=observation.channel,
        payload_ref=observation.payload_ref,
    )
    return event, ref, message


def test_sun_reaction_catalog_label_is_a_render_fact_not_a_typed_body() -> None:
    observation = _observation(
        suffix="sun",
        at=NOW,
        text=None,
        coalescing_metadata={
            "schema_version": "world-v2-qq-coalescing.2",
            "content_shapes": ["reaction"],
            "reaction_refs": ["qq-face:74"],
            "sticker_refs": [],
        },
    )
    assert observation.text is None
    assert observation_dialogue_text(observation) == "☀️ 太阳"


def test_textless_sun_reaction_enters_dialogue_and_takes_current_turn() -> None:
    explained = _observation(suffix="explained", at=NOW - timedelta(minutes=99), text=SUN_EXPLAINED)
    photo = _observation(suffix="photo", at=NOW - timedelta(minutes=95), text=PHOTO)
    sun = _observation(
        suffix="sun",
        at=NOW,
        text=None,
        coalescing_metadata={
            "schema_version": "world-v2-qq-coalescing.2",
            "content_shapes": ["reaction"],
            "reaction_refs": ["qq-face:74"],
            "sticker_refs": [],
        },
    )
    events: list[WorldEvent] = []
    refs: list[CommittedWorldEventRef] = []
    observations: list[MessageObservationRef] = []
    for revision, item in enumerate((explained, photo, sun), start=1):
        event, ref, message = _append_observation(observation=item, revision=revision)
        events.append(event)
        refs.append(ref)
        observations.append(message)

    compiled = RecentDialogueCompiler(
        ledger=_EventLookup(tuple(events)),  # type: ignore[arg-type]
    ).compile_with_acknowledgements(
        projection=LedgerProjection.model_construct(
            committed_world_event_refs=tuple(refs),
            message_observations=tuple(observations),
            expression_plan_manifests=(),
            minimal_reply_manifests=(),
            proposal_audits=(),
            expression_plans=(),
            actions=(),
            execution_receipts=(),
            stored_message_payloads=(),
            expression_payload_descriptors=(),
        ),
        actor_ref="agent:companion",
        subject_refs=frozenset({"user:primary"}),
    )
    by_text = {item.text: item for item in compiled.dialogue}
    assert "☀️ 太阳" in by_text
    assert PHOTO in by_text
    assert SUN_EXPLAINED in by_text

    sun_event_id = "event:observation:observation:sun"
    selected = ConversationContinuityCompiler().compile(
        dialogue=compiled.dialogue,
        trigger_ref=sun_event_id,
    )
    marked = {item.text: item.continuity_reasons for item in selected.dialogue}
    assert "current_turn" in marked["☀️ 太阳"]
    assert "current_turn" not in marked.get(PHOTO, ())
    assert SUN_EXPLAINED in marked
