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


def test_bilibili_vision_and_funnel_helpers_match_dsh_bili_scroll_invariants() -> None:
    import io
    import struct
    from PIL import Image
    from companion_daemon.world_v2.external_world_perception.bilibili import (
        _cover_url,
        _crop_tile,
        _https,
        _image_headers,
        _should_inspect_frames,
        _snapshot_entries,
        _unpack_pvdata_entries,
    )

    assert _https("http://i0.hdslb.com/a.jpg") == "https://i0.hdslb.com/a.jpg"
    assert _https("//i0.hdslb.com/b.jpg") == "https://i0.hdslb.com/b.jpg"
    assert _cover_url("http://i0.hdslb.com/d.jpg@1c.png") == "https://i0.hdslb.com/d.jpg@512w.jpg"
    assert sorted(k.lower() for k in _image_headers()) == ["referer", "user-agent"]

    # 6-hour video seconds must not be mistaken for milliseconds
    sec = {"index": [0, 0, 5, 10, 5538, 22163]}
    assert [t for t, _ in _snapshot_entries(sec, 22164.0)] == [0.0, 0.0, 5.0, 10.0, 5538.0, 22163.0]
    ms = {"index": [0, 5000, 22163000]}
    assert [t for t, _ in _snapshot_entries(ms, 22164.0)] == [0.0, 5.0, 22163.0]

    pv_raw = struct.pack(">3H", 0, 12, 48)
    assert _unpack_pvdata_entries(pv_raw) == [(0.0, 0), (12.0, 1), (48.0, 2)]

    # 2x1 sprite sheet: tile 1 should crop the right half (480x270 blue tile)
    sheet = Image.new("RGB", (960, 270), (255, 0, 0))
    sheet.paste(Image.new("RGB", (480, 270), (0, 0, 255)), (480, 0))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    tile_bytes = _crop_tile(buf.getvalue(), 1, 480, 270)
    cropped = Image.open(io.BytesIO(tile_bytes)).convert("RGB")
    assert cropped.size == (480, 270)
    r, _g, b = cropped.getpixel((240, 135))
    assert r < 40 and b > 200

    # Subtitle-first gating: rich talk subtitle skips vision; missing subtitle or visual category triggers vision
    long_talk_sub = (
        "[0s]今天跟大家聊聊我们组上周重构数据库遇到的一个奇葩死锁问题 "
        "[15s]当时线上告警突然响了，我们排查了整整三个小时才发现是事务隔离级别配错了 "
        "[40s]最后把索引顺序调过来之后延迟立刻降到了两毫秒以内，真的太刺激了"
    )
    assert _should_inspect_frames(entry={"tname": "社科·法律·心理"}, subtitle_excerpt=long_talk_sub) is False
    assert _should_inspect_frames(entry={"tname": "美食制作"}, subtitle_excerpt=long_talk_sub) is True
    assert _should_inspect_frames(entry={"tname": "日常"}, subtitle_excerpt="") is True


@pytest.mark.asyncio
async def test_inbound_qq_bilibili_json_card_and_shortlink_enrichment() -> None:
    from companion_daemon.world_v2.external_world_perception.bilibili import (
        _INBOUND_BILI_SUMMARY_CACHE,
        _VIDEO_ENRICHMENT_CACHE,
        enrich_qq_fragment_with_bilibili_video,
    )
    from companion_daemon.world_v2.qq_ingress_policy import normalize_onebot_qq_ingress

    _INBOUND_BILI_SUMMARY_CACHE.clear()
    _VIDEO_ENRICHMENT_CACHE.clear()

    bili_card_json = json.dumps(
        {
            "app": "com.tencent.miniapp_01",
            "prompt": "[QQ小程序]哔哩哔哩",
            "meta": {
                "detail_1": {
                    "appid": "1109937557",
                    "title": "哔哩哔哩",
                    "desc": "挑战在大学食堂连吃一周减脂餐，第3天破防了",
                    "qqdocurl": "https://b23.tv/abcDEF1",
                }
            },
        },
        ensure_ascii=False,
    )
    raw_event = {
        "post_type": "message",
        "message_type": "private",
        "user_id": "2849281720",
        "message_id": "msg-bili-card-1",
        "time": 1761350400,
        "message": [
            {"type": "json", "data": {"data": bili_card_json}},
            {"type": "text", "data": {"text": "快看这个笑死我了"}},
        ],
    }

    fragment = normalize_onebot_qq_ingress(raw_event)
    assert fragment is not None
    assert fragment.text is not None
    assert "https://b23.tv/abcDEF1" in fragment.text
    assert "快看这个笑死我了" in fragment.text

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://b23.tv/abcDEF1":
            return httpx.Response(
                302,
                headers={"Location": "https://www.bilibili.com/video/BV1xx411c7mD?share_source=qq"},
            )
        if url == "https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "bvid": "BV1xx411c7mD",
                        "aid": 999,
                        "cid": 888,
                        "title": "挑战在大学食堂连吃一周减脂餐，第3天破防了",
                        "desc": "本来想认真减脂，结果阿姨手抖加了一碗红烧肉",
                        "tname": "社科·法律·心理",
                        "duration": 180,
                        "pubdate": 1761350400,
                        "owner": {"name": "阿柴今天吃什么"},
                        "stat": {"view": 528000, "like": 46200, "danmaku": 3100},
                    },
                },
            )
        if url == "https://api.bilibili.com/x/player/wbi/v2?bvid=BV1xx411c7mD&cid=888":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [
                                {
                                    "lan": "ai-zh",
                                    "lan_doc": "中文（自动生成）",
                                    "subtitle_url": "//aisubtitle.hdslb.com/bfs/ai_subtitle/test.json",
                                }
                            ]
                        }
                    },
                },
            )
        if url == "https://aisubtitle.hdslb.com/bfs/ai_subtitle/test.json":
            return httpx.Response(
                200,
                json={
                    "body": [
                        {"from": 0.0, "content": "第一天我们在二楼窗口点了水煮鸡胸肉和西兰花，味道淡得像在嚼纸板"},
                        {"from": 42.5, "content": "到了第三天中午实在扛不住了，阿姨问我要不要淋点红烧肉汁，我直接鬼使神差说来一整份红烧肉"},
                        {"from": 95.0, "content": "最后称体重不仅没瘦反而重了一斤半，减脂计划彻底宣告破产"},
                    ]
                },
            )
        if url == "https://comment.bilibili.com/888.xml":
            return httpx.Response(
                200,
                text='<i><d p="43.0,1,25,16777215,0,0,0,0">阿姨：看你怪可怜的</d></i>',
            )
        if url == "https://api.bilibili.com/x/v2/reply?type=1&oid=999&sort=2&ps=5":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "replies": [
                            {
                                "member": {"uname": "路人甲"},
                                "content": {"message": "42秒向红烧肉屈服那段演我本人"},
                                "like": 1314,
                            }
                        ]
                    },
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        enriched = await enrich_qq_fragment_with_bilibili_video(
            fragment,
            http_client=client,
            bili_sessdata="test_sess",
        )

    assert enriched.text is not None
    assert "【对方分享的B站视频实看：" in enriched.text
    assert "字幕原文实读：[0s]第一天我们在二楼窗口点了水煮鸡胸肉和西兰花" in enriched.text
    assert "[42s]到了第三天中午实在扛不住了" in enriched.text
    assert "时间轴弹幕：[43s]阿姨：看你怪可怜的" in enriched.text
    assert "高赞神评：路人甲：42秒向红烧肉屈服那段演我本人(赞1314)" in enriched.text


def test_bilibili_popular_signal_deduplicates_across_stat_revisions() -> None:
    import sqlite3
    from threading import RLock
    from types import SimpleNamespace
    from companion_daemon.world_v2.external_world_perception.attention import (
        SQLiteShadowAttentionCoordinator,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE external_signal_revisions (
            signal_revision_ref TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            observed_at TEXT NOT NULL
        );
        CREATE TABLE external_signal_observation_state (
            signal_id TEXT PRIMARY KEY,
            latest_signal_revision_ref TEXT NOT NULL,
            effective_expires_at TEXT NOT NULL
        );
        """
    )
    runtime = SimpleNamespace(
        world_id="world:test",
        actor_ref="character:shen_zhizhi",
        attention_policy_revision="policy:1",
        deployment_mode_revision="deploy:1",
        model=SimpleNamespace(model_id="deepseek-chat"),
        merge_wait_seconds=10,
        window_ttl_seconds=300,
        lease_seconds=60,
        model_timeout_seconds=30,
        max_candidate_dossiers=6,
        attempt_retention_seconds=3600,
    )
    lock = RLock()
    now = datetime(2026, 10, 25, 13, 0, tzinfo=UTC)
    coord = SQLiteShadowAttentionCoordinator(
        connection=conn,
        lock=lock,
        database_write_lock=lock,
        runtime=runtime,  # type: ignore[arg-type]
        exposable_source_ids=("cn.social.bilibili.popular.v1",),
        wall_clock=lambda: now,
    )
    conn.execute(
        "INSERT INTO external_signal_revisions VALUES ('rev:bili:1', 'sig:bili:BV1w2Hw6KE1j', 'cn.social.bilibili.popular.v1', '2026-10-25T09:00:00Z')"
    )
    conn.execute(
        "INSERT INTO external_signal_revisions VALUES ('rev:bili:2', 'sig:bili:BV1w2Hw6KE1j', 'cn.social.bilibili.popular.v1', '2026-10-25T13:09:00Z')"
    )
    conn.execute(
        "INSERT INTO external_signal_observation_state VALUES ('sig:bili:BV1w2Hw6KE1j', 'rev:bili:2', '2026-10-25T19:09:00Z')"
    )
    assert [r["signal_revision_ref"] for r in coord._eligible_signal_rows(observed_at=now)] == ["rev:bili:2"]

    conn.execute(
        "INSERT INTO external_perception_attention_exposures VALUES ('rev:bili:1', 'policy:1', 'deploy:1', 'attempt:1', 'selected', '2026-10-25T09:01:00Z')"
    )
    assert coord._eligible_signal_rows(observed_at=now) == ()



