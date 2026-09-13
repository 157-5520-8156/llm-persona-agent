"""Habit context is fixed with the snapshot and never rendered as today's diary."""

import json
from pathlib import Path

import pytest

from companion_daemon.world_v2.character_interior import contracts
from companion_daemon.world_v2.character_interior.contracts import InnerLifeSnapshot
from companion_daemon.world_v2.character_interior.snapshot_compiler import compile_inner_life_snapshot
from companion_daemon.world_v2.day_skeleton import DaySlot, WorldDaySkeleton


CONTEXT = {
    "world_id": "world:routine-authority", "actor_ref": "agent:companion",
    "world_revision": 0, "deliberation_revision": 0, "ledger_sequence": 0,
    "logical_time": "2026-09-07T11:00:00+08:00", "slices": {},
}


def test_snapshot_view_does_not_reread_changed_routine_configuration(monkeypatch):
    first = WorldDaySkeleton((DaySlot("study", "看书", 9, 12, "图书馆"),), ())
    second = WorldDaySkeleton((DaySlot("walk", "散步", 20, 22, "校园"),), ())
    monkeypatch.setattr(contracts, "_DAY_SKELETON", first)
    snapshot = compile_inner_life_snapshot(CONTEXT)
    before = snapshot.model_view()
    monkeypatch.setattr(contracts, "_DAY_SKELETON", second)
    assert snapshot.model_view() == before
    restored = InnerLifeSnapshot.model_validate_json(snapshot.model_dump_json(), strict=True)
    assert restored.model_view() == before
    assert compile_inner_life_snapshot(CONTEXT).snapshot_id != snapshot.snapshot_id


def test_habits_remain_available_outside_the_dated_day_sheet(monkeypatch):
    monkeypatch.setattr(contracts, "_DAY_SKELETON", WorldDaySkeleton(
        (DaySlot("notes", "整理课程笔记", 14, 17, "学校"),), (),
    ))
    snapshot = compile_inner_life_snapshot(CONTEXT)
    materials = snapshot.model_view()["materials"]
    assert "今天是2026-09-07，当地11:00" in materials["day_sheet"]
    assert "整理课程笔记" not in materials["day_sheet"]
    background = materials["routine_background"]
    assert background["authority"] == "habit_background_not_a_plan_or_experience"
    assert background["daily_habits"][0]["title"] == "整理课程笔记"
    assert "logical_time" not in background and "today" not in background
    assert not snapshot.source_refs  # Habit data adds no factual citation authority.


def test_pre_upgrade_snapshot_identity_still_loads():
    raw = (Path(__file__).parent / "fixtures/inner_life_snapshot_before_pinned_routines.json").read_text()
    snapshot = InnerLifeSnapshot.model_validate_json(raw, strict=True)
    assert snapshot.snapshot_hash == "1b7aca1d3ec2271ee1108795d64ee1da3e8b9329b5f6638e32e020bda1828275"
    assert snapshot.model_dump(mode="json", exclude={"routine_background"}) == json.loads(raw)


def test_changing_a_pinned_habit_cannot_keep_the_original_identity():
    snapshot = compile_inner_life_snapshot(CONTEXT)
    raw = json.loads(snapshot.model_dump_json())
    raw["routine_background"]["daily_habits"][0]["title"] = "改写的习惯"
    with pytest.raises(ValueError, match="snapshot hash is invalid"):
        InnerLifeSnapshot.model_validate_json(json.dumps(raw), strict=True)
