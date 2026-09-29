"""Configured personality reaches private choices without becoming fact authority."""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
from companion_daemon.world_v2.character_interior.living_frame import configured_living_frame
from test_character_interior_structured_role import (
    StructuredCharacterRoleFaculty, _RequiredToolQueueModel, _request, _life_development_manifest,
)


def frame():
    return CompanionIdentityFrame(companion_name="测试角色", counterpart_name="用户",
        personality_frame="有自己的主意，会开玩笑。", background="喜欢书，家里经营书店。",
        daily_life=("常去图书馆。",), speech_examples=("聊天样例不应变成生活脚本。",))


@pytest.mark.asyncio
async def test_living_identity_is_shared_without_promoting_characterization_to_sources():
    request = await _request(purpose="life_development_choice", capability_manifest=_life_development_manifest())
    before = request.snapshot.model_dump_json()
    configured = configured_living_frame(frame())
    role = StructuredCharacterRoleFaculty(model=_RequiredToolQueueModel(), model_id="fixture", character_disposition=configured)
    messages = role._messages(request, contract=role._resolve_contract(request))
    value = json.loads(messages[1]["content"])
    # Biography belongs to source-bound identity/history readers. Pasting it
    # into every private choice turns family/major into a recurring agenda.
    assert "background" not in value["configured_character_disposition"]
    assert value["configured_character_disposition"]["personality"] == frame().personality_frame
    assert "habits" not in value["configured_character_disposition"]
    assert frame().speech_examples[0] not in messages[1]["content"]
    assert "self_directed" in value["living_choice_scope"]
    old = StructuredCharacterRoleFaculty(model=_RequiredToolQueueModel(), model_id="fixture")
    old_value = json.loads(old._messages(request, contract=old._resolve_contract(request))[1]["content"])
    for key in ("materials", "source_inventory", "source_refs"):
        assert value["inner_life_snapshot"][key] == old_value["inner_life_snapshot"][key]
    assert value["citeable_sources"] == old_value["citeable_sources"]
    assert value["capability_manifest"] == old_value["capability_manifest"]
    assert request.snapshot.model_dump_json() == before
    assert "configured_character_disposition" not in old_value


def test_disposition_is_frozen_and_bound_into_turn_identity():
    configured = configured_living_frame(frame())
    role = StructuredCharacterRoleFaculty(model=_RequiredToolQueueModel(), model_id="fixture", character_disposition=configured)
    identity = deepcopy(role.author_identity)
    configured["personality"] = "changed after composition"
    assert role.author_identity == identity
    changed = StructuredCharacterRoleFaculty(model=_RequiredToolQueueModel(), model_id="fixture", character_disposition=configured)
    assert changed.author_identity["configured_disposition_hash"] != identity["configured_disposition_hash"]


@pytest.mark.asyncio
async def test_execution_bounds_accept_ordinary_time_without_productive_goal(tmp_path, monkeypatch, build_app):
    from datetime import timedelta
    import httpx
    from companion_daemon.llm import DeepSeekChatModel
    from test_day_open_self_directed_intent import _DayOpenHTTP, INTENT
    from test_world_stimulus_life_intent import NOW
    from companion_daemon.world_v2.replay_evaluator import ReplayEvaluator
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    monkeypatch.setitem(INTENT, "intention", "懒得干什么，躺一会儿，想到再说。")
    provider = _DayOpenHTTP()
    model = DeepSeekChatModel("fixture", "https://fixture.invalid", "deepseek-v4-flash",
                             thinking_enabled=False, transport=httpx.MockTransport(provider))
    app = build_app(tmp_path / "ordinary-time.sqlite", model, ecology=True)
    try:
        at = NOW + timedelta(minutes=1)
        await app.tick(tick_id="ordinary-time", logical_time_from=NOW, logical_time_to=at,
                       observed_at=at, trace_id="ordinary-time", causation_id="clock:ordinary-time",
                       correlation_id="ordinary-time", reason="test_clock")
        packet = json.loads(provider.day_requests[0]["messages"][1]["content"])
        assert packet["configured_character_disposition"]["name"] == "小满"
        assert "living_choice_scope" in packet
        assert len(app.export_replay_evidence().projection.plans) == 1
        replay = ReplayEvaluator().evaluate(evidence=app.export_replay_evidence())
        assert replay.replay_hash_matches and not replay.findings
    finally:
        app.close()
        await model.aclose()


from test_day_open_self_directed_intent import build_app as build_app  # noqa: E402,F401
