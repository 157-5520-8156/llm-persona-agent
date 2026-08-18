"""Non-text inbound observations must reach cognition as facts, not exceptions.

A QQ face-only envelope (production seq 4012) is him speaking.  The host must
expose opaque provider identifiers and the bound observation, never translate
``qq-face:74`` into mood, and never mint a fake text body.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from companion_daemon.world_v2.character_interior.inbound_author import _cache_key
from companion_daemon.world_v2.character_interior.inbound_wire import _ExpressionDraftWire
from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute, TriggerMessage
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.fact_draft_adapter import (
    FactObservationProposalAdapter,
    FactObservationSource,
    materialize_fact_observation_draft,
)
from companion_daemon.world_v2.pinned_turn import PinnedTurnCompiler
from companion_daemon.world_v2.proposal_envelope import ProposalEvidenceRef
from companion_daemon.world_v2.qq_ingress_policy import normalize_onebot_qq_ingress
from companion_daemon.world_v2.schemas import Observation, WorldEvent


NOW = datetime(2026, 8, 18, 12, 4, 5, tzinfo=UTC)
WORLD = "world:inbound-nontext"
FACE_HASH = "sha256:" + "b" * 64


def _reaction_metadata(*, face: str = "qq-face:74", source_id: str = "1937366025") -> dict[str, object]:
    return {
        "schema_version": "world-v2-qq-coalescing.2",
        "policy_version": "world-v2-qq-ingress-matrix.2",
        "policy_digest": "d" * 64,
        "batch_id": "qq-ingress-batch:test-reaction",
        "source_event_ids": [source_id],
        "reaction_target_message_id": source_id,
        "source_payload_hashes": ["e" * 64],
        "content_shapes": ["reaction"],
        "continuity_signals": ["unknown"],
        "reply_refs": [],
        "reaction_refs": [face],
        "sticker_refs": [],
        "control_events": [],
        "ordered_fragment_count": 1,
    }


def _observation(
    *,
    text: str | None = None,
    attachment_refs: tuple[str, ...] = (),
    coalescing_metadata: dict[str, object] | None = None,
    observation_id: str = "observation:qq:reaction:1",
    source_event_id: str = "1937366025",
) -> Observation:
    payload = text or json.dumps(coalescing_metadata or {}, sort_keys=True)
    return Observation(
        schema_version="world-v2.1",
        observation_id=observation_id,
        world_id=WORLD,
        logical_time=NOW,
        created_at=NOW,
        trace_id="trace:inbound-nontext",
        causation_id=f"qq:{source_event_id}",
        correlation_id=f"qq:{source_event_id}",
        source="platform:qq",
        source_event_id=source_event_id,
        actor="user:primary",
        channel="qq",
        payload_ref=f"ingress:qq:{source_event_id}",
        payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
        text=text,
        received_at=NOW,
        reply_context={"target": "conversation:qq:c2c:owner", "platform_message_id": source_event_id},
        attachment_refs=attachment_refs,
        coalescing_metadata=coalescing_metadata or {},
    )


def _observation_event(observation: Observation) -> WorldEvent:
    payload = observation.model_dump(mode="json")
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:trigger:observation:platform:qq:reaction:1",
        world_id=observation.world_id,
        event_type="ObservationRecorded",
        logical_time=observation.logical_time,
        created_at=observation.created_at,
        actor=observation.actor,
        source=observation.source,
        trace_id=observation.trace_id,
        causation_id=observation.causation_id,
        correlation_id=observation.correlation_id,
        idempotency_key=domain_idempotency_key(
            event_type="ObservationRecorded",
            world_id=observation.world_id,
            payload=payload,
        )
        or observation.observation_id,
        payload=payload,
    )


def _model_input(trigger: TriggerMessage | None) -> ModelInput:
    evidence = ()
    if trigger is not None:
        evidence = (
            ProposalEvidenceRef(
                ref_id=trigger.observation_ref,
                evidence_kind="observed_message",
                source_world_revision=trigger.source_world_revision,
                immutable_hash=trigger.event_payload_hash,
            ),
        )
    return ModelInput(
        call_id="model-call:inbound-nontext:1",
        attempt_id="attempt:inbound-nontext:1",
        route=ModelRoute(tier="flash", reason_code="test", router_version="test.1"),
        capsule_id="a" * 64,
        trigger_ref="event:trigger:observation:platform:qq:reaction:1",
        evaluated_world_revision=4,
        model_content_json="{}",
        trigger_evidence=evidence,
        trigger_message=trigger,
    )


def test_text_only_trigger_dump_stays_byte_stable_without_nontext_keys() -> None:
    trigger = TriggerMessage(
        event_ref="event:observation:text:1",
        event_payload_hash=FACE_HASH,
        observation_ref="observation:text:1",
        source_world_revision=4,
        actor="user:primary",
        channel="qq",
        reply_target="conversation:qq:c2c:owner",
        platform_message_id="text-1",
        text="在吗",
    )
    dumped = trigger.model_dump(mode="json")
    assert "reaction_refs" not in dumped
    assert "sticker_refs" not in dumped
    assert "reply_refs" not in dumped


def test_reaction_only_trigger_is_valid_without_minting_text() -> None:
    trigger = TriggerMessage(
        event_ref="event:observation:reaction:1",
        event_payload_hash=FACE_HASH,
        observation_ref="observation:reaction:1",
        source_world_revision=4,
        actor="user:primary",
        channel="qq",
        reply_target="conversation:qq:c2c:owner",
        platform_message_id="1937366025",
        reaction_refs=("qq-face:74",),
    )
    dumped = trigger.model_dump(mode="json")
    assert trigger.text is None
    assert dumped["reaction_refs"] == ["qq-face:74"]
    assert "text" in dumped
    assert dumped["text"] is None
    assert "笑" not in json.dumps(dumped, ensure_ascii=False)
    assert "开心" not in json.dumps(dumped, ensure_ascii=False)


def test_empty_trigger_is_still_rejected() -> None:
    with pytest.raises(ValueError, match="inbound observation evidence"):
        TriggerMessage(
            event_ref="event:observation:empty:1",
            event_payload_hash=FACE_HASH,
            observation_ref="observation:empty:1",
            source_world_revision=4,
            actor="user:primary",
            channel="qq",
            reply_target="conversation:qq:c2c:owner",
        )


def test_compiler_exposes_production_shaped_reaction_without_forged_text() -> None:
    observation = _observation(coalescing_metadata=_reaction_metadata())
    event = _observation_event(observation)

    trigger = PinnedTurnCompiler._trigger_message(
        observation, event, source_world_revision=4012
    )

    assert trigger is not None
    assert trigger.text is None
    assert trigger.reaction_refs == ("qq-face:74",)
    assert trigger.platform_message_id == "1937366025"
    assert trigger.observation_ref == observation.observation_id
    assert trigger.event_ref == event.event_id


def test_inbound_cache_key_accepts_reaction_trigger() -> None:
    trigger = TriggerMessage(
        event_ref="event:trigger:observation:platform:qq:reaction:1",
        event_payload_hash=FACE_HASH,
        observation_ref="observation:qq:reaction:1",
        source_world_revision=4,
        actor="user:primary",
        channel="qq",
        reply_target="conversation:qq:c2c:owner",
        platform_message_id="1937366025",
        reaction_refs=("qq-face:74",),
    )
    key = _cache_key(_model_input(trigger))
    assert key == (
        "event:trigger:observation:platform:qq:reaction:1",
        "observation:qq:reaction:1",
        FACE_HASH,
    )


def test_inbound_cache_key_still_rejects_a_missing_trigger() -> None:
    with pytest.raises(ValueError, match="verified current message"):
        _cache_key(_model_input(None))


def test_sticker_and_attachment_and_reply_refs_compile() -> None:
    sticker = _observation(
        coalescing_metadata={
            **_reaction_metadata(),
            "content_shapes": ["sticker"],
            "reaction_refs": [],
            "sticker_refs": ["qq-sticker:sha256:" + "a" * 64],
        }
    )
    sticker_trigger = PinnedTurnCompiler._trigger_message(
        sticker, _observation_event(sticker), source_world_revision=1
    )
    assert sticker_trigger is not None
    assert sticker_trigger.text is None
    assert sticker_trigger.sticker_refs == ("qq-sticker:sha256:" + "a" * 64,)

    image = _observation(
        attachment_refs=("qq-attachment:image:sha256:" + "c" * 64,),
        coalescing_metadata={
            **_reaction_metadata(),
            "content_shapes": ["attachment"],
            "reaction_refs": [],
        },
    )
    image_trigger = PinnedTurnCompiler._trigger_message(
        image, _observation_event(image), source_world_revision=1
    )
    assert image_trigger is not None
    assert image_trigger.text is None
    assert image_trigger.attachment_refs == image.attachment_refs
    assert image_trigger.attachment_media_types == ("image",)

    quoted = _observation(
        coalescing_metadata={
            **_reaction_metadata(),
            "content_shapes": ["reaction"],
            "reply_refs": ["qq-message:29"],
        }
    )
    quoted_trigger = PinnedTurnCompiler._trigger_message(
        quoted, _observation_event(quoted), source_world_revision=1
    )
    assert quoted_trigger is not None
    assert quoted_trigger.reply_refs == ("qq-message:29",)
    assert quoted_trigger.reaction_refs == ("qq-face:74",)


class _SilentChat:
    model = "silent-nontext"

    async def complete(self, messages, *, temperature: float = 0.2):  # type: ignore[no-untyped-def]
        del messages, temperature
        raise AssertionError("presentation test must not call the model")


def test_presented_trigger_is_the_opaque_identifier_after_desensitization() -> None:
    trigger = TriggerMessage(
        event_ref="event:trigger:observation:platform:qq:reaction:1",
        event_payload_hash=FACE_HASH,
        observation_ref="observation:qq:reaction:1",
        source_world_revision=4,
        actor="user:primary",
        channel="qq",
        reply_target="conversation:qq:c2c:owner",
        platform_message_id="1937366025",
        reaction_refs=("qq-face:74",),
    )
    messages = _ExpressionDraftWire(model=_SilentChat())._messages(
        request=_model_input(trigger),
        quick_recovery=False,
        failure_code=None,
    )
    user = json.loads(messages[1]["content"])
    presented = user["current_trigger_message"]
    blob = json.dumps(messages, ensure_ascii=False)
    assert presented["reaction_refs"] == ["qq-face:74"]
    assert presented["text"] is None
    assert presented["platform_message_id"] == "1937366025"
    assert "qq-face:74" in blob
    assert "太阳" not in blob
    assert "他笑了" not in blob
    assert "他觉得开心" not in blob
    assert "not a host translation of mood" in messages[0]["content"]


def test_ingress_shapes_for_nontext_onebot_envelopes() -> None:
    face = normalize_onebot_qq_ingress(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 2759284998,
            "message_id": 1937366025,
            "time": NOW.timestamp(),
            "message": [{"type": "face", "data": {"id": "74"}}],
        }
    )
    sticker = normalize_onebot_qq_ingress(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 2759284998,
            "message_id": 2,
            "time": NOW.timestamp(),
            "message": [{"type": "mface", "data": {"emoji_id": "abc"}}],
        }
    )
    image = normalize_onebot_qq_ingress(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 2759284998,
            "message_id": 3,
            "time": NOW.timestamp(),
            "message": [{"type": "image", "data": {"url": "https://private.invalid/a.jpg"}}],
        }
    )
    quoted_only = normalize_onebot_qq_ingress(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 2759284998,
            "message_id": 4,
            "time": NOW.timestamp(),
            "message": [{"type": "reply", "data": {"id": "29"}}],
        }
    )
    typing = normalize_onebot_qq_ingress(
        {
            "post_type": "notice",
            "notice_type": "input_status",
            "user_id": 2759284998,
            "event_id": "typing-1",
            "status": "start",
            "time": NOW.timestamp(),
        }
    )

    assert face is not None
    assert face.content_shape == "reaction"
    assert face.reaction_refs == ("qq-face:74",)
    assert face.text is None
    assert sticker is not None
    assert sticker.content_shape == "sticker"
    assert sticker.sticker_ref is not None
    assert image is not None
    assert image.content_shape == "attachment"
    assert image.attachment_refs
    assert quoted_only is None
    assert typing is not None
    assert typing.content_shape == "control"


def test_fact_draft_materializes_retain_false_for_a_reaction() -> None:
    observation = _observation(coalescing_metadata=_reaction_metadata())
    event = _observation_event(observation)
    result = materialize_fact_observation_draft(
        raw=json.dumps({"retain": False}),
        observation=observation,
        observation_event=event,
        source_world_revision=1,
    )
    assert result is None


def test_fact_draft_rejects_retain_true_without_source_text() -> None:
    observation = _observation(coalescing_metadata=_reaction_metadata())
    event = _observation_event(observation)
    with pytest.raises(ValueError):
        materialize_fact_observation_draft(
            raw=json.dumps(
                {
                    "retain": True,
                    "predicate_code": "preference.likes",
                    "value": "太阳",
                    "privacy_class": "personal",
                    "confidence": 8000,
                    "rationale": "host translation",
                }
            ),
            observation=observation,
            observation_event=event,
            source_world_revision=1,
        )


class _MustNotBeCalled:
    model = "blocked-nontext-fact"

    async def complete(self, messages, *, temperature: float = 0.2):  # type: ignore[no-untyped-def]
        del messages, temperature
        raise AssertionError("non-text FactDraft must not spend a model call")


@pytest.mark.asyncio
async def test_fact_draft_skips_the_model_when_there_is_no_verbal_assertion() -> None:
    observation = _observation(coalescing_metadata=_reaction_metadata())
    event = _observation_event(observation)
    result = await FactObservationProposalAdapter(model=_MustNotBeCalled()).propose(
        observation=observation,
        observation_event=event,
        source_world_revision=1,
    )
    assert result is None


class _TextOnlyBatchChat:
    model = "text-only-batch"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, *, temperature: float = 0.2):  # type: ignore[no-untyped-def]
        self.calls += 1
        request = json.loads(messages[1]["content"])
        observations = request["observations"]
        assert [item["text"] for item in observations] == [
            "我最近很喜欢喝乌龙茶。",
            "今天早点休息。",
        ]
        return json.dumps(
            {
                "decisions": [
                    {"observation_id": item["observation_id"], "result": {"retain": False}}
                    for item in observations
                ]
            }
        )


@pytest.mark.asyncio
async def test_fact_draft_batch_does_not_send_textless_members_to_the_model() -> None:
    reaction = _observation(coalescing_metadata=_reaction_metadata())
    first = _observation(
        text="我最近很喜欢喝乌龙茶。",
        observation_id="observation:fact-text-1",
        source_event_id="text-1",
        coalescing_metadata={},
    )
    second = _observation(
        text="今天早点休息。",
        observation_id="observation:fact-text-2",
        source_event_id="text-2",
        coalescing_metadata={},
    )
    chat = _TextOnlyBatchChat()
    results = await FactObservationProposalAdapter(model=chat).propose_batch(
        sources=(
            FactObservationSource(
                observation=reaction,
                event=_observation_event(reaction),
                world_revision=1,
            ),
            FactObservationSource(
                observation=first,
                event=_observation_event(first),
                world_revision=2,
            ),
            FactObservationSource(
                observation=second,
                event=_observation_event(second),
                world_revision=3,
            ),
        ),
        evaluated_world_revision=3,
    )
    assert chat.calls == 1
    assert results == (None, None, None)
