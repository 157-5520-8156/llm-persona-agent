"""Tests for Bilibili popular video external perception and video-sharing source closure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest

from companion_daemon.world_v2.expression_draft import (
    ExpressionBeatDraftChoice,
    ExpressionDraft,
    PrivateTurnState,
     TEXT_ONLY_EXPRESSION_CAPABILITIES,
    WorldClaimDraft,
    materialize_expression_draft,
    world_claim_source_refs_by_scope,
)
from companion_daemon.world_v2.external_perception_events import (
    ExternalPerceptionChannelProof,
    ExternalPerceptionProjection,
    ExternalSignalSnapshotProjection,
    compile_external_perception_life_influences,
)
from companion_daemon.world_v2.external_world_perception import (
    BILIBILI_POPULAR_URL,
    BilibiliPopularAdapter,
    build_production_source_profiles,
    load_external_perception_source_registry,
)
from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute, TriggerMessage
from companion_daemon.world_v2.model_facing_context import compact_chat_model_facing_context
from companion_daemon.world_v2.proposal_envelope import ProposalEvidenceRef


@pytest.mark.asyncio
async def test_bilibili_popular_adapter_parses_video_items_and_rejects_malformed() -> None:
    payload = {
        "code": 0,
        "message": "0",
        "ttl": 1,
        "data": {
            "list": [
                {
                    "bvid": "BV1xx411c7mD",
                    "title": "挑战在大学食堂连吃一周减脂餐，第3天破防了",
                    "desc": "本来想认真减脂，结果阿姨手抖之后直接加了一碗红烧肉",
                    "tname": "日常",
                    "pubdate": 1761350400,
                    "owner": {"mid": 12345, "name": "阿柴今天吃什么"},
                    "stat": {"view": 528000, "like": 46200, "danmaku": 3100},
                    "rcmd_reason": {"content": "百万播放"},
                },
                {
                    "bvid": "invalid_bvid",
                    "title": "bad item",
                    "pubdate": 1761350400,
                },
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == BILIBILI_POPULAR_URL
        assert request.headers.get("referer") == "https://www.bilibili.com/"
        return httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = BilibiliPopularAdapter(http_client=client)
        now = datetime(2026, 10, 25, 6, 0, tzinfo=UTC)
        page = await adapter.fetch(
            after=None,
            observed_at=now,
            deadline_at=now + timedelta(seconds=10),
            limit=10,
        )

    assert len(page.items) == 1
    assert page.parser_rejected_item_count == 1
    item = page.items[0]
    assert item.upstream_item_id == "bvid:BV1xx411c7mD"
    assert item.signal_kind == "bilibili_popular_video"
    assert item.canonical_url == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert "【日常】挑战在大学食堂连吃一周减脂餐，第3天破防了（UP主：阿柴今天吃什么）" == item.headline
    assert "UP主：阿柴今天吃什么" in item.licensed_summary
    assert "热门标签：百万播放" in item.licensed_summary
    assert "播放 52.8万" in item.licensed_summary
    assert "https://www.bilibili.com/video/BV1xx411c7mD" in item.licensed_summary


def test_checked_in_bilibili_registry_loads_and_builds_live_profiles() -> None:
    registry = load_external_perception_source_registry(
        "configs/external-perception-sources.bilibili.json"
    )
    enabled = [item for item in registry.sources if item.enabled]
    assert any(
        item.source_id == "cn.social.bilibili.popular.v1"
        and item.adapter_kind == "bilibili_popular"
        and item.policy.may_quote
        and item.policy.may_expose_to_character_model
        and item.policy.may_freeze_durable_snapshot
        for item in enabled
    )
    client = httpx.AsyncClient()
    try:
        result = build_production_source_profiles(
            deployment_mode="live",
            registry_path="configs/external-perception-sources.bilibili.json",
            http_client=client,
        )
        assert result.status == "ready"
        assert any(
            isinstance(profile.adapter, BilibiliPopularAdapter)
            for profile in result.source_profiles
        )
    finally:
        # No network I/O was performed.
        pass


def test_external_perception_influence_preserves_url_and_authorizes_world_claims() -> None:
    from companion_daemon.world_v2.external_perception_events import (
        canonical_external_perception_json,
        external_perception_value_hash,
    )
    from companion_daemon.world_v2.schemas import ProjectionCursor

    visible_material = {
        "headline": "【日常】挑战在大学食堂连吃一周减脂餐，第3天破防了（UP主：阿柴今天吃什么）",
        "canonical_url": "https://www.bilibili.com/video/BV1xx411c7mD",
    }
    visible_json = canonical_external_perception_json(visible_material)
    visible_hash = external_perception_value_hash(visible_material)
    snapshot = ExternalSignalSnapshotProjection(
        snapshot_ref="ext-snapshot:bili:1",
        signal_revision_ref="ext-rev:bili:1",
        source_id="cn.social.bilibili.popular.v1",
        upstream_publisher_ref="publisher:bilibili",
        upstream_item_id="bvid:BV1xx411c7mD",
        source_policy_revision="bilibili-popular-direct-api-live-2026-10-25",
        source_payload_hash="1" * 64,
        normalized_hash="2" * 64,
        headline="【日常】挑战在大学食堂连吃一周减脂餐，第3天破防了（UP主：阿柴今天吃什么）",
        licensed_summary="B站热门视频《挑战在大学食堂连吃一周减脂餐，第3天破防了》（BV号：BV1xx411c7mD）；UP主：阿柴今天吃什么；链接：https://www.bilibili.com/video/BV1xx411c7mD",
        canonical_url="https://www.bilibili.com/video/BV1xx411c7mD",
        published_at=datetime(2026, 10, 25, 5, 0, tzinfo=UTC),
        observed_at=datetime(2026, 10, 25, 5, 30, tzinfo=UTC),
        expires_at=datetime(2026, 10, 25, 11, 30, tzinfo=UTC),
        model_visible_material_json=visible_json,
        model_visible_material_hash=visible_hash,
        may_expose_to_character_model=True,
        may_quote=True,
        may_freeze_durable_snapshot=True,
        acceptance_id="acc:bili:1",
        attention_attempt_id="attempt:1",
        model_result_event_ref="event:model:1",
        model_result_event_payload_hash="4" * 64,
        accepted_event_ref="event:external-perception:snapshot:1",
        accepted_event_payload_hash="3" * 64,
        accepted_world_revision=10,
    )
    perception = ExternalPerceptionProjection(
        acceptance_id="acc:bili:1",
        perception_id="ext-perception:bili:1",
        actor_ref="character:shen_zhizhi",
        attention_attempt_id="attempt:1",
        window_id="window:1",
        pinned_cursor=ProjectionCursor(
            world_revision=10,
            deliberation_revision=1,
            ledger_sequence=10,
        ),
        encountered_world_time=datetime(2026, 10, 25, 5, 35, tzinfo=UTC),
        observed_wall_time=datetime(2026, 10, 25, 5, 35, tzinfo=UTC),
        candidate_ref="candidate:bili:1",
        candidate_snapshot_hash="5" * 64,
        snapshot_ref="ext-snapshot:bili:1",
        snapshot_event_ref="event:external-perception:snapshot:1",
        snapshot_event_payload_hash="3" * 64,
        channel=ExternalPerceptionChannelProof(
            channel_ref="channel:public_information_read:bili",
            channel_kind="public_information_feed",
            proof_refs=("event:cap:bili",),
            proof_hash="7" * 64,
            access_summary="public Bilibili popular feed",
        ),
        subjective_summary="刚刷B站看到一个挑战在大学食堂吃一周减脂餐结果第3天被红烧肉破防的视频，太真实了",
        epistemic_notes="B站热门公开视频，仅代表UP主发布的视频内容",
        attended_context_refs=("situation:1",),
        attention_model_result_ref="call:1",
        attention_model_event_ref="event:model:1",
        attention_model_event_payload_hash="4" * 64,
        privacy_class="shareable",
        accepted_event_ref="event:external-perception:recorded:1",
        accepted_event_payload_hash="6" * 64,
        accepted_world_revision=11,
    )

    class _Proj:
        external_signal_snapshots = (snapshot,)
        external_perceptions = (perception,)

    influences = compile_external_perception_life_influences(_Proj())
    assert len(influences) == 1
    inf = influences[0]
    assert inf.canonical_url == "https://www.bilibili.com/video/BV1xx411c7mD"

    raw_context = {
        "world_id": "world:test",
        "actor_ref": "character:shen_zhizhi",
        "consumer_scope": "character_cognition",
        "context_compiler_version": "v2",
        "logical_time": "2026-10-25T06:00:00+00:00",
        "world_revision": 12,
        "deliberation_revision": 1,
        "ledger_sequence": 12,
        "viewer_privacy_ceiling": "private",
        "slices": {
            "perception_results": {
                "availability": "available",
                "items": [
                    {
                        "item_ref": inf.influence_id,
                        "source_ref": inf.influence_id,
                        "privacy_class": "private",
                        "value": inf.model_dump(mode="json"),
                        "source_bindings": [
                            {
                                "ref": inf.source_event_ref,
                                "source_kind": "committed_event",
                                "authority_type": "ExternalPerceptionRecorded",
                                "source_world_revision": inf.source_world_revision,
                                "immutable_hash": "6" * 64,
                            }
                        ],
                    }
                ],
            }
        },
    }
    compacted_json = compact_chat_model_facing_context(json.dumps(raw_context))
    compacted = json.loads(compacted_json)
    lanes = world_claim_source_refs_by_scope(
        context=compacted,
        stable_identity_source_refs=frozenset(),
    )
    assert inf.influence_id in lanes["current_world"]
    assert inf.influence_id in lanes["past_world"]

    req = ModelInput(
        call_id="call:share:1",
        attempt_id="attempt:share:1",
        route=ModelRoute(
            tier="flash",
            reason_code="test",
            router_version="test.v1",
        ),
        trigger_ref="event:obs:1",
        evaluated_world_revision=12,
        evaluated_deliberation_revision=1,
        evaluated_ledger_sequence=12,
        capsule_id="7" * 64,
        model_content_json=json.dumps(raw_context),
        trigger_evidence=(
            ProposalEvidenceRef(
                ref_id="event:obs:1",
                evidence_kind="observed_message",
                source_world_revision=12,
                immutable_hash="sha256:" + "8" * 64,
            ),
        ),
        trigger_message=TriggerMessage(
            event_ref="event:obs:1",
            observation_ref="event:obs:1",
            actor="user:1",
            channel="qq",
            reply_target="qq:private:1",
            text="在干嘛呢",
            source_world_revision=12,
            event_payload_hash="sha256:" + "8" * 64,
        ),
    )
    draft_dict = ExpressionDraft(
        private_turn_state=PrivateTurnState(
            contract="private-turn-state.1",
            inner_state_summary="正摸鱼刷B站看到个减脂餐被红烧肉破防的视频，顺手发给他乐一下",
            attended_source_refs=(inf.influence_id,),
        ),
        timing_choice="now",
        turn_posture="continue",
        stance="warm_engaged",
        confidence=8500,
        brief_rationale="share funny bilibili video",
        impulse_summary="share video",
        beats=(
            ExpressionBeatDraftChoice(
                modality="text",
                text="刚在B站刷到阿柴今天吃什么的《挑战在大学食堂连吃一周减脂餐，第3天破防了》，看到阿姨手抖加红烧肉那段笑死我了 https://www.bilibili.com/video/BV1xx411c7mD",
            ),
        ),
        world_claims=(
            WorldClaimDraft(
                claim_text="刚在B站刷到UP主阿柴今天吃什么的视频《挑战在大学食堂连吃一周减脂餐，第3天破防了》（BV1xx411c7mD）",
                scope="past_world",
                source_refs=(inf.influence_id,),
            ),
        ),
    ).model_dump(mode="json")

    proposal = materialize_expression_draft(
        value=draft_dict,
        request=req,
        capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES,
        private_state_context_json=compacted_json,
    )
    assert any(
        ev.ref_id == "event:external-perception:recorded:1"
        and ev.evidence_kind == "committed_world_event"
        for ev in proposal.evidence_refs
    )
