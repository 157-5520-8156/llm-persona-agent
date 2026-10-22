"""Unbidden memory: what she just lived can bring an old memory to mind.

Covers the 2026-10-21 lived-moment recall path: life triggers schedule the same
automatic association a message does, the coordinator skips setting prose and
contentless fragments for unbidden attention, background profiles keep the
surfaced memory, and the presentation tells her what it is without telling
her what to do with it.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.background_context_profile import (
    background_context_profile_for_purpose,
    slice_background_inner_life_snapshot,
)
from companion_daemon.world_v2.character_interior.continuity_view import (
    LIVED_RECALL_PURPOSES,
    with_automatic_prefetch,
)
from companion_daemon.world_v2.ledger_context_resolver import (
    _lived_moment_recall_trigger,
    lived_moment_recall_cue,
)
from companion_daemon.world_v2.present_prompt import (
    AUTOMATIC_PREFETCH_FRAMING,
    AUTOMATIC_PREFETCH_FRAMING_KEY,
    _expand_automatic_prefetch,
    _present_automatic_prefetch,
)
from companion_daemon.world_v2.recall_attention import build_automatic_recall_request
from companion_daemon.world_v2.recall_runtime import _automatic_attention_eligible


def _excerpt(text, *, authority, entity, revision):
    return SimpleNamespace(
        text=text, character_response=None, world_consequence=None,
        authority_event_ref=authority, descriptor_event_ref="event:descriptor:" + entity,
        source_entity_id=entity, authority_world_revision=revision,
    )


def _experience(text, *, experience_id, authority, revision):
    return SimpleNamespace(
        experience_id=experience_id,
        origin=SimpleNamespace(accepted_event_ref=authority),
        content=_excerpt(text, authority=authority, entity=experience_id, revision=revision),
    )


def test_only_lived_moments_trigger_unbidden_association() -> None:
    assert _lived_moment_recall_trigger("event:life-aftermath:settlement:occurrence:life-development:abc")
    assert _lived_moment_recall_trigger("event:outcome-settlement:abc")
    assert _lived_moment_recall_trigger("event:activity-lifecycle-effect:abc")
    assert _lived_moment_recall_trigger("event:affect-mutation:abc")
    # Memory upkeep, retention and clock ticks are not moments she lives.
    assert not _lived_moment_recall_trigger("memory-review-job:abc")
    assert not _lived_moment_recall_trigger("event:life-aftermath:experience:occurrence:abc")
    assert not _lived_moment_recall_trigger("event:trigger:clock:tick:qq-c2c-v2:2026-10-21T09:00:00+00:00")
    assert not _lived_moment_recall_trigger("event:trigger:observation:platform:qq:1")


def test_cue_prefers_the_moment_the_trigger_names_then_her_latest_moment() -> None:
    trigger = "event:life-aftermath:settlement:occurrence:life-development:rain"
    settled = (
        _excerpt("一阵短雨停了，屋檐还在滴水。", authority=trigger,
                 entity="occurrence:life-development:rain", revision=9),
        _excerpt("书店进了一批旧诗集。", authority="event:other", entity="occurrence:other", revision=12),
    )
    experiences = (
        _experience("我在屋檐下等雨停，鞋尖湿了一点。", experience_id="experience:rain",
                    authority=trigger, revision=9),
    )
    cue = lived_moment_recall_cue(trigger_ref=trigger, settled_items=settled, experience_items=experiences)
    assert cue.startswith("我在屋檐下等雨停")
    assert "一阵短雨停了" in cue
    assert "旧诗集" not in cue

    feeling = lived_moment_recall_cue(
        trigger_ref="event:affect-mutation:x", settled_items=settled, experience_items=experiences,
    )
    # A feeling names no moment: her latest lived moment is the cue.
    assert "我在屋檐下等雨停" in feeling and "旧诗集" in feeling
    assert lived_moment_recall_cue(trigger_ref="event:affect-mutation:x", settled_items=(),
                                   experience_items=()) is None


def test_cue_reads_prose_not_enum_or_reference_identifiers() -> None:
    from companion_daemon.world_v2.ledger_context_resolver import _readable_strings

    found = _readable_strings({
        "kind": "settled_world_environment",
        "source_ref": "event:life-aftermath:settlement:abc",
        "environment_text": "一阵短雨已经停了。",
        "note": "Rain stopped over the courtyard.",
    })
    assert found == ["一阵短雨已经停了。", "Rain stopped over the courtyard."]


def test_lived_moment_query_names_her_moment_not_his_words() -> None:
    request = build_automatic_recall_request(
        observation_text="一阵短雨停了", observation_label="她刚经历", limit=8,
    )
    assert request.query_text.startswith("她刚经历：一阵短雨停了")
    assert "用户刚说" not in request.query_text
    assert request.limit == 8
    default = build_automatic_recall_request(observation_text="下雨了")
    assert default.query_text.startswith("用户刚说：下雨了")


def _doc(text, *, slice_name="recent_experiences", retrieval=None):
    return SimpleNamespace(text=text, retrieval_text=retrieval, source_slice=slice_name)


def test_unbidden_attention_skips_setting_prose_and_contentless_fragments() -> None:
    assert not _automatic_attention_eligible(
        _doc("x", retrieval="environment; settled_at=2026-10-06\n图书馆一层入口的还书箱被清空过一次")
    )
    assert not _automatic_attention_eligible(_doc("哈哈哈哈哈", slice_name="recent_dialogue"))
    assert not _automatic_attention_eligible(_doc("在干嘛？", slice_name="recent_dialogue"))
    assert _automatic_attention_eligible(_doc("今天七夕，本来约好打游戏结果被放鸽子了", slice_name="recent_dialogue"))
    assert _automatic_attention_eligible(_doc("页边周明远的字迹又出现了，我想顺着这条线再走一步。"))


def test_framing_tells_her_what_it_is_and_round_trips_losslessly() -> None:
    canonical = {"items": [{"source_ref": "memory:rain", "text": "高中时放学突然下暴雨。"}]}
    presented = _present_automatic_prefetch(canonical, constants=())
    assert presented[AUTOMATIC_PREFETCH_FRAMING_KEY] == AUTOMATIC_PREFETCH_FRAMING
    assert "都由你决定" in AUTOMATIC_PREFETCH_FRAMING
    assert _expand_automatic_prefetch(presented, constants=()) == canonical
    # Nothing surfaced: nothing to frame.
    assert AUTOMATIC_PREFETCH_FRAMING_KEY not in _present_automatic_prefetch({"items": []}, constants=())


def test_background_profiles_keep_a_surfaced_memory_only_when_present() -> None:
    profile = background_context_profile_for_purpose("world_stimulus_appraisal")
    assert "automatic_prefetch" not in profile.snapshot_material_keys
    assert with_automatic_prefetch(profile, {"situation": {}}) is profile
    kept = with_automatic_prefetch(profile, {"automatic_prefetch": {"items": []}})
    assert kept.snapshot_material_keys[-1] == "automatic_prefetch"
    assert kept.profile_id.endswith("/lived-moment-recall.1")
    snapshot = {
        "materials": {
            "situation": {"items": []},
            "automatic_prefetch": {"items": [{"source_ref": "memory:rain", "text": "下暴雨那天"}]},
        },
        "source_refs": ["memory:rain"],
    }
    assert "automatic_prefetch" not in slice_background_inner_life_snapshot(snapshot, profile)["materials"]
    assert "automatic_prefetch" in slice_background_inner_life_snapshot(snapshot, kept)["materials"]
    assert {"world_stimulus_appraisal", "proactive_contact"} <= LIVED_RECALL_PURPOSES
    assert "memory_consolidation" not in LIVED_RECALL_PURPOSES


def test_background_faculty_waits_for_an_association_by_default() -> None:
    from companion_daemon.world_v2.character_interior.structured_role import (
        StructuredCharacterRoleFaculty,
    )
    from test_character_interior_structured_role import _RequiredToolQueueModel

    role = StructuredCharacterRoleFaculty(model=_RequiredToolQueueModel(), model_id="m")
    assert role.automatic_prefetch_join_seconds == 8.0
    off = StructuredCharacterRoleFaculty(
        model=_RequiredToolQueueModel(), model_id="m", automatic_prefetch_join_seconds=None,
    )
    assert not hasattr(off, "automatic_prefetch_join_seconds")


@pytest.mark.asyncio
async def test_a_settled_rain_brings_back_a_rainy_day_she_lived_before(tmp_path, monkeypatch):
    """End to end: no message from him; a life event settles; the stimulus
    appraisal sees an old memory surfaced by that moment, framed as hers."""

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    import test_world_stimulus_life_intent as shared
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
    from companion_daemon.world_v2.recall_runtime import RecallCoordinator
    from test_character_prehistory import reapprove, reviewed_archive
    from test_world_stimulus_life_response import _ResponseHTTP, _model, _settled

    data = reviewed_archive().document.model_dump(mode="json")
    data.update(world_id=shared.WORLD, actor_ref=shared.ACTOR)
    data["records"][0].update(
        # Offline tests have no dense embedding, so the memory shares words
        # with the moment; production bge-m3 also links paraphrases.
        statement="高中有一次放学也是一阵短雨，雨已经停了我和同桌还躲在屋檐下聊天，鞋全湿了，两个人笑了一路。",
    )
    archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(data, ensure_ascii=False)))

    scheduled: list[dict[str, object]] = []
    original = RecallCoordinator.schedule_prefetch

    def recording(self, **kwargs):
        scheduled.append(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(RecallCoordinator, "schedule_prefetch", recording)
    provider = _ResponseHTTP(text="雨停了，忽然想起以前的事。")
    model = _model(provider)
    app = shared._build(tmp_path / "lived-recall.sqlite", model, reviewed_prehistory=archive)
    try:
        await _settled(app)
        await app.drain_background_once()

        lived = [
            item for item in scheduled
            if str(item["trigger_ref"]).startswith(
                ("event:life-aftermath:settlement:", "event:outcome-settlement:")
            )
        ]
        assert lived, [item["trigger_ref"] for item in scheduled]
        assert str(lived[0]["query_text"]).startswith("她刚经历：")
        assert "短雨" in str(lived[0]["query_text"])

        assert provider.stimulus_requests, "the settled moment must still reach her"
        material = json.loads(provider.stimulus_requests[0]["messages"][-1]["content"])
        materials = material["inner_life_snapshot"]["materials"]
        surfaced = materials.get("automatic_prefetch")
        assert surfaced is not None, sorted(materials)
        assert surfaced[AUTOMATIC_PREFETCH_FRAMING_KEY] == AUTOMATIC_PREFETCH_FRAMING
        texts = json.dumps(surfaced["items"], ensure_ascii=False)
        assert "屋檐下聊天" in texts
    finally:
        await app.aclose()
        await model.aclose()
