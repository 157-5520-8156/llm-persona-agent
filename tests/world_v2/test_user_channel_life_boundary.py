from __future__ import annotations

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.ledger_context_resolver import (
    _EXPERIENCE_CONTENT_UNAVAILABLE_REASONS,
)
from companion_daemon.world_v2.life_content import (
    USER_CHANNEL_AUTHORITY_LIMIT_REASON,
    LifeContentCompiler,
    collect_user_channel_limited_content_refs,
)
from companion_daemon.world_v2.life_content_events import (
    LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED,
    LifeContentRecordedPayload,
    LifeContentUserChannelAuthorityLimitedPayload,
)
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore,
    StoredLifeContent,
)
from companion_daemon.world_v2.life_events import NpcStateChangedPayload
from companion_daemon.world_v2.reducers import REDUCER_BUNDLE_VERSION
from companion_daemon.world_v2.schemas import NpcSocialVariables, NpcSubjectiveState
from companion_daemon.world_v2.world_life_context import WorldLifeContextCompiler
from test_life_content import (
    _cursor,
    _projection_with_bound_content,
    _projection_with_bound_experience_content,
)
from test_life_projection import (
    LIFE_TIME,
    WORLD_ID,
    commit,
    event,
    seed_through_proposal,
    settlement_batch,
    world_evidence,
)


def _limit_event(*, content_refs: tuple[str, ...], delivery_count: int, action_count: int):
    payload = LifeContentUserChannelAuthorityLimitedPayload(
        content_refs=content_refs,
        inspected_media_delivery_count=delivery_count,
        inspected_media_delivery_action_count=action_count,
    ).model_dump(mode="json")
    return event(
        "event:life-content-user-channel-limit",
        LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED,
        payload,
        at=LIFE_TIME,
    ).model_copy(
        update={
            "idempotency_key": domain_idempotency_key(
                event_type=LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED,
                world_id=WORLD_ID,
                payload=payload,
            )
        }
    )


def _ledger_with_npc_life_content() -> WorldLedger:
    ledger = WorldLedger.in_memory(
        world_id=WORLD_ID,
        accepted_batch_issuer=AcceptedLedgerBatchIssuer(),
    )
    seed_through_proposal(ledger)
    commit(ledger, settlement_batch())
    before = ledger.project().npcs[0]
    state = NpcSubjectiveState(
        subject_ref="actor:companion",
        inner_state_content_ref="content:npc-state:lin:1",
        inner_state_payload_hash="a" * 64,
        relationship_to_subject=NpcSocialVariables(
            trust_bp=5_600,
            closeness_bp=4_200,
            tension_bp=900,
        ),
        goal_content_refs=("content:npc-goal:lin:portfolio",),
        goal_content_hashes=("b" * 64,),
        organization_refs=("organization:internship-team",),
        life_arc_refs=("life-arc:npc:lin:internship",),
        source_event_refs=("occurrence-settled",),
        evolved_at=ledger.project().logical_time,
    )
    after = before.model_copy(
        update={"entity_revision": before.entity_revision + 1, "subjective_state": state}
    )
    payload = NpcStateChangedPayload(
        change_id="change:npc:lin:state:1",
        transition_id="transition:npc:lin:state:1",
        expected_entity_revision=before.entity_revision,
        evidence_refs=(world_evidence(ledger, "occurrence-settled", "private_hypothesis"),),
        policy_refs=("policy:npc-ecology.1",),
        npc_before=before,
        npc_after=after,
    )
    change = event(
        "event:npc:lin:state:1",
        "NpcStateChanged",
        payload.model_dump(mode="json"),
    ).model_copy(update={"actor": "npc:lin"})
    revision = ledger.project().world_revision + 1

    def descriptor(*, kind: str, ref: str, content_hash: str):
        state_change = NpcStateChangedPayload.model_validate_json(change.payload_json)
        return event(
            "event:descriptor:" + ref,
            "LifeContentRecorded",
            LifeContentRecordedPayload(
                content_id="descriptor:" + ref,
                content_kind=kind,
                content_ref=ref,
                content_payload_hash=content_hash,
                privacy_class=state_change.npc_after.privacy_class,
                source_kind="npc_state",
                source_event_ref=change.event_id,
                source_world_revision=revision,
                source_payload_hash=change.payload_hash,
                source_entity_id=state_change.npc_after.npc_id,
                source_entity_revision=state_change.npc_after.entity_revision,
            ).model_dump(mode="json"),
        )

    commit(
        ledger,
        (
            change,
            descriptor(
                kind="npc_inner_state",
                ref=state.inner_state_content_ref,
                content_hash=state.inner_state_payload_hash,
            ),
            descriptor(
                kind="npc_goal",
                ref=state.goal_content_refs[0],
                content_hash=state.goal_content_hashes[0],
            ),
        ),
    )
    return ledger


def test_limited_experience_is_omitted_without_making_the_slice_unavailable() -> None:
    forged = "她挑了一张下午在书店拍的角落照片，于是把照片发了过去。"
    projection, descriptor, text = _projection_with_bound_experience_content(text=forged)
    store = InMemoryImmutableLifeContentStore()
    store.put_if_absent(
        StoredLifeContent(
            content_ref=descriptor.content_ref,
            content_kind=descriptor.content_kind,
            content_payload_hash=descriptor.content_payload_hash,
            text=text,
        )
    )
    limited = frozenset({descriptor.content_ref})

    result = LifeContentCompiler(store=store).compile(
        cursor=_cursor(projection),
        actor_ref="actor:companion",
        viewer_privacy_ceiling="private",
        projection=projection,
        user_channel_limited_content_refs=limited,
    )

    assert result.experience_items == ()
    assert result.suppressions[0].reason == USER_CHANNEL_AUTHORITY_LIMIT_REASON
    assert USER_CHANNEL_AUTHORITY_LIMIT_REASON not in _EXPERIENCE_CONTENT_UNAVAILABLE_REASONS

    visible = LifeContentCompiler(store=store).compile(
        cursor=_cursor(projection),
        actor_ref="actor:companion",
        viewer_privacy_ceiling="private",
        projection=projection,
    )
    assert visible.experience_items[0].content.text == forged


def test_limited_occurrence_is_dropped_from_world_life_context() -> None:
    projection, descriptor, text = _projection_with_bound_content(
        text="书店的那张角落照片已经发给他了，他说好滴。"
    )
    store = InMemoryImmutableLifeContentStore()
    store.put_if_absent(
        StoredLifeContent(
            content_ref=descriptor.content_ref,
            content_kind="occurrence_result",
            content_payload_hash=descriptor.content_payload_hash,
            text=text,
        )
    )
    companion_projection = projection.model_copy(
        update={
            "world_occurrences": (
                projection.world_occurrences[0].model_copy(
                    update={"participant_refs": ("actor:companion",)}
                ),
            )
        }
    )
    compiler = WorldLifeContextCompiler(life_content=LifeContentCompiler(store=store))
    before = compiler.compile(
        projection=companion_projection,
        actor_ref="actor:companion",
        cursor=_cursor(companion_projection),
    )
    after = compiler.compile(
        projection=companion_projection,
        actor_ref="actor:companion",
        cursor=_cursor(companion_projection),
        user_channel_limited_content_refs=frozenset({descriptor.content_ref}),
    )

    assert before[0].content is not None
    assert before[0].content.text == text
    assert after == ()


def test_limit_event_rejects_unknown_content_ref() -> None:
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    seed_through_proposal(ledger)
    commit(ledger, settlement_batch())

    with pytest.raises(ValueError, match="unknown life content refs"):
        commit(
            ledger,
            [
                _limit_event(
                    content_refs=("payload:missing",),
                    delivery_count=0,
                    action_count=0,
                )
            ],
        )


def test_limit_event_rejects_inspected_delivery_count_mismatch() -> None:
    ledger = _ledger_with_npc_life_content()

    with pytest.raises(ValueError, match="inspected media delivery count"):
        commit(
            ledger,
            [
                _limit_event(
                    content_refs=("content:npc-state:lin:1",),
                    delivery_count=1,
                    action_count=0,
                )
            ],
        )


def test_limit_event_is_append_only_and_discoverable_from_the_ledger() -> None:
    ledger = _ledger_with_npc_life_content()
    semantic_before = ledger.project().semantic_hash
    refs = ("content:npc-goal:lin:portfolio", "content:npc-state:lin:1")
    commit(
        ledger,
        [_limit_event(content_refs=refs, delivery_count=0, action_count=0)],
    )
    projection = ledger.project()

    assert projection.reducer_bundle_version == REDUCER_BUNDLE_VERSION
    assert projection.reducer_bundle_version == "world-v2-reducers.56"
    assert projection.semantic_hash != semantic_before
    assert collect_user_channel_limited_content_refs(
        ledger=ledger,
        projection=projection,
    ) == frozenset(refs)
    assert any(
        item.event_type == LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED
        for item in projection.committed_world_event_refs
    )


def test_npc_outcome_defaults_user_channel_completion_none_and_rejects_sent() -> None:
    from pydantic import ValidationError

    from companion_daemon.world_v2.npc_ecology import NpcWorldOutcomeDraft

    allowed = NpcWorldOutcomeDraft(text="林在书店坐了一下午，把最卡的一页改清楚了。", privacy="personal")
    assert allowed.user_channel_completion == "none"
    with pytest.raises(ValidationError):
        NpcWorldOutcomeDraft(
            text="林把照片发给了他。",
            privacy="personal",
            user_channel_completion="sent",  # type: ignore[arg-type]
        )


def test_impression_limit_payload_canonicalizes_ids() -> None:
    from companion_daemon.world_v2.private_impression_events import (
        PrivateImpressionUserChannelAuthorityLimitedPayload,
    )

    payload = PrivateImpressionUserChannelAuthorityLimitedPayload(
        impression_ids=(
            "impression:bd04c493",
            "impression:eca0e3bb",
            "impression:bd04c493",
        ),
        inspected_media_delivery_count=0,
        inspected_media_delivery_action_count=0,
    )
    assert payload.impression_ids == ("impression:bd04c493", "impression:eca0e3bb")
    assert payload.limitation == "not_user_channel_authority"
