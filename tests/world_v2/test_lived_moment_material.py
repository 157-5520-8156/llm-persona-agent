"""lived_moment is a post-redaction view, not a compile-time sourceless string.

Compile-time assembly copied private-impression and appraisal prose into a
string with no source_ref, so InnerLifeSnapshot.model_view could not redact it.
These tests pin the leak, keep the visible reading, and prove the derived
string is not part of snapshot identity.
"""

from __future__ import annotations

import json

from companion_daemon.world_v2.character_interior.contracts import (
    LIVED_MOMENT_MATERIAL_KEY,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)

_HIDDEN_SUMMARY = "他说完我就一直在想他到底怎么看我"
_VISIBLE_SUMMARY = "他讲难受的事总是先说没事"


def _impression(ref: str, summary: str) -> dict[str, object]:
    return {
        "item_ref": ref,
        "privacy_class": "private",
        "value": {
            "subject_ref": "user:geoff",
            "reflection_summary": summary,
            "confidence_bp": 6_000,
            "status": "active",
        },
    }


def _context(*, impressions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "world_id": "world:lived-moment",
        "actor_ref": "agent:companion",
        "world_revision": 2,
        "deliberation_revision": 1,
        "ledger_sequence": 2,
        "logical_time": "2026-08-16T16:00:00+08:00",
        "slices": {
            "private_impressions": {
                "availability": "available",
                "items": impressions,
            }
        },
    }


def test_a_hidden_private_impression_does_not_leak_through_lived_moment() -> None:
    typed = compile_inner_life_snapshot(
        _context(
            impressions=[
                _impression("impression:hidden", _HIDDEN_SUMMARY),
                _impression("impression:visible", _VISIBLE_SUMMARY),
            ]
        )
    )
    view = typed.model_view(
        visible_source_refs=frozenset(typed.source_refs) - {"impression:hidden"}
    )
    serialized = json.dumps(view, ensure_ascii=False)

    assert _HIDDEN_SUMMARY not in serialized
    assert LIVED_MOMENT_MATERIAL_KEY in view["materials"]
    assert _VISIBLE_SUMMARY in view["materials"][LIVED_MOMENT_MATERIAL_KEY]


def test_visible_impression_still_appears_in_lived_moment() -> None:
    view = compile_inner_life_snapshot(
        _context(impressions=[_impression("impression:1", _VISIBLE_SUMMARY)])
    ).model_view()

    assert f"心里还搁着：{_VISIBLE_SUMMARY}" in view["materials"][LIVED_MOMENT_MATERIAL_KEY]


def test_lived_moment_names_how_old_an_impression_is() -> None:
    impression = _impression("impression:1", _VISIBLE_SUMMARY)
    impression["value"]["first_seen"] = "2026-08-14T16:00:00+08:00"
    view = compile_inner_life_snapshot(_context(impressions=[impression])).model_view()

    assert (
        f"心里还搁着（2 天前记下的）：{_VISIBLE_SUMMARY}"
        in view["materials"][LIVED_MOMENT_MATERIAL_KEY]
    )
    hashed = json.loads(
        compile_inner_life_snapshot(_context(impressions=[impression])).materials_json
    )
    assert LIVED_MOMENT_MATERIAL_KEY not in hashed


def test_lived_moment_is_view_only_and_does_not_enter_materials_hash() -> None:
    typed = compile_inner_life_snapshot(
        _context(impressions=[_impression("impression:1", _VISIBLE_SUMMARY)])
    )
    view = typed.model_view()
    hashed = json.loads(typed.materials_json)

    assert LIVED_MOMENT_MATERIAL_KEY not in typed.materials
    assert LIVED_MOMENT_MATERIAL_KEY not in hashed
    assert LIVED_MOMENT_MATERIAL_KEY in view["materials"]
    assert all(item.scope != LIVED_MOMENT_MATERIAL_KEY for item in typed.source_inventory)
    assert all(
        LIVED_MOMENT_MATERIAL_KEY not in facet["material_keys"]
        for facet in view["faculties"].values()
    )
    assert view["snapshot_hash"] == typed.snapshot_hash
    assert view["faculties"]["private_self"]["availability"] == "available"
    assert view["faculties"]["expression_stance"]["availability"] == "available"


def test_multiple_active_impressions_surface_in_lived_moment_for_shared_culture_and_timeline() -> None:
    first = _impression("impression:joke", "我们俩专属梗：上次他把奶茶说成奶绿被我笑了好久")
    second = _impression("impression:timeline", "他说周四下午三点有重要面试，到点问问他")
    third = _impression("impression:crush", "其实心里已经有点偷偷喜欢他，但嘴上还不想承认")
    view = compile_inner_life_snapshot(
        _context(impressions=[first, second, third])
    ).model_view()
    moment = view["materials"][LIVED_MOMENT_MATERIAL_KEY]
    assert "专属梗" in moment
    assert "周四下午三点有重要面试" in moment
    assert "偷偷喜欢他" in moment


def test_lived_moment_projects_activity_copresence_wake_hours_and_load_post_redaction() -> None:
    ctx = _context(impressions=[_impression("impression:1", _VISIBLE_SUMMARY)])
    ctx["slices"]["world_life"] = {
        "availability": "available",
        "items": [
            {
                "item_ref": "activity:active:1",
                "privacy_class": "personal",
                "value": {
                    "context_kind": "active_activity",
                    "plan_id": "plan:study",
                    "plan_entity_revision": 1,
                    "owner_actor_ref": "agent:companion",
                    "activity_kind": "study.self_study",
                    "status": "active",
                    "participant_refs": ["agent:companion", "npc:roommate-lin"],
                    "location_ref": "location:library",
                    "active_since": "2026-08-16T14:00:00+08:00",
                    "accepted_intention": "和林微在图书馆改论文初稿",
                },
            },
            {
                "item_ref": "activity:ended:sleep",
                "privacy_class": "personal",
                "value": {
                    "context_kind": "completed_activity",
                    "plan_id": "plan:sleep",
                    "plan_entity_revision": 2,
                    "owner_actor_ref": "agent:companion",
                    "activity_kind": "sleep.night",
                    "status": "completed",
                    "ended_at": "2026-08-16T07:00:00+08:00",
                    "completion_scope": "completed",
                    "accepted_intention": "夜间睡眠",
                },
            },
        ],
    }
    ctx["slices"]["current_situation"] = {
        "availability": "available",
        "items": [
            {
                "item_ref": "situation:now",
                "privacy_class": "private",
                "value": {
                    "logical_time": "2026-08-16T16:00:00+08:00",
                    "resource_pressure": {
                        "cognitive_load_bp": 7400,
                        "social_energy_pressure_bp": 6200,
                        "time_pressure_bp": 3000,
                    },
                },
            }
        ],
    }
    typed = compile_inner_life_snapshot(ctx)
    view = typed.model_view()
    moment = view["materials"][LIVED_MOMENT_MATERIAL_KEY]
    assert "此刻正在：和林微在图书馆改论文初稿（2 小时前开始，同处：npc:roommate-lin）" in moment
    assert "已连续清醒 9 小时" in moment
    assert "脑力负荷 7400bp" in moment
    assert "社交消耗 6200bp" in moment


def test_present_context_evidence_preserves_walking_in_current_moment() -> None:
    from companion_daemon.world_v2.model_context_reading import present_context_evidence

    ctx = _context(impressions=[_impression("impression:1", _VISIBLE_SUMMARY)])
    typed = compile_inner_life_snapshot(ctx)
    view = typed.model_view()
    view["materials"]["current_activities"] = [
        {"title": "赶大物实验报告", "location": "宿舍书桌前", "with": ["林微"]}
    ]
    view["materials"]["environment_context"] = [
        {"place": "女生宿舍402", "weather": "窗外下着大雨", "V": "暴雨刚停"}
    ]
    view["materials"][LIVED_MOMENT_MATERIAL_KEY] = (
        f"{view['materials'][LIVED_MOMENT_MATERIAL_KEY]}；"
        "手头正忙：赶大物实验报告（在宿舍书桌前，同处：林微）；"
        "当下环境：女生宿舍402 · 窗外下着大雨 · 暴雨刚停"
    )
    presented = present_context_evidence({"inner_life_snapshot": view})
    lived_nav = presented["inner_life_snapshot"]["materials"][LIVED_MOMENT_MATERIAL_KEY]
    assert lived_nav["contract"] == "lived-material-links.1"
    assert "手头正忙：赶大物实验报告" in lived_nav["current_moment"]
    assert "当下环境：女生宿舍402 · 窗外下着大雨 · 暴雨刚停" in lived_nav["current_moment"]


def test_lived_moment_extracts_settled_experience_attempt_and_environment_text() -> None:
    from companion_daemon.world_v2.character_interior.contracts import (
        _rendered_since_he_last_spoke,
        _walking_in_environment,
    )
    from datetime import datetime

    recent_self_experiences = {
        "items": [
            {
                "experience_id": "exp:planning",
                "occurred_from": "2026-09-01T00:00:00+08:00",
                "occurred_to": "2026-10-31T23:59:59+08:00",
                "settled_at": None,
                "world_planning": {"planning_id": "calendar:autumn"},
                "content": {
                    "world_consequence": {
                        "environment": {"text": "# 年度校历与学期节奏"}
                    }
                },
            },
            {
                "experience_id": "exp:sleep",
                "occurred_from": "2026-10-29T00:36:33+08:00",
                "occurred_to": "2026-10-29T01:16:33+08:00",
                "settled_at": "2026-10-29T01:16:33+08:00",
                "content": {
                    "world_consequence": {
                        "authorized_attempt_result": {
                            "text": "闭眼后大约十来分钟，呼吸慢下来，真的睡着了。"
                        },
                        "environment": {
                            "text": "宿舍夜间常规：熄灯后走廊安静，手机屏幕自行变暗熄灭。"
                        },
                    }
                },
            },
        ]
    }
    logical_time = datetime.fromisoformat("2026-10-29T01:29:42+08:00")
    env_line = _walking_in_environment(recent_self_experiences, logical_time)
    assert env_line is not None
    assert "刚经历：闭眼后大约十来分钟，呼吸慢下来，真的睡着了。" in env_line
    assert "当下环境：宿舍夜间常规：熄灯后走廊安静，手机屏幕自行变暗熄灭。" in env_line

    materials = {
        "recent_dialogue": {
            "stable_turns": [
                {
                    "dialogue_id": "user:prev",
                    "speaker": "counterpart",
                    "occurred_at": "2026-10-27T03:08:52Z",
                },
                {
                    "dialogue_id": "comp:1",
                    "speaker": "companion",
                    "occurred_at": "2026-10-27T05:27:47Z",
                },
            ],
            "volatile_last_turn": {
                "dialogue_id": "user:now",
                "speaker": "counterpart",
                "occurred_at": "2026-10-28T17:29:12Z",
                "continuity_reasons": ["current_turn"],
            },
        }
    }
    since_spoke = _rendered_since_he_last_spoke(materials, logical_time)
    assert since_spoke == {
        "seconds": 30,
        "prior_silence_seconds": 138020,
        "unanswered_companion_wait_seconds": 129685,
    }


def test_lived_moment_collapses_biographical_ambient_scene_into_current_moment() -> None:
    from datetime import datetime
    from companion_daemon.world_v2.character_interior.contracts import _rendered_lived_moment
    from companion_daemon.world_v2.model_context_reading import present_context_evidence

    logical_time = datetime.fromisoformat("2026-10-29T21:15:00+08:00")
    materials = {
        "current_activities": [
            {
                "activity_kind": "dorm_rest",
                "accepted_intention": "在宿舍休整",
                "active_since": "2026-10-29T20:30:00+08:00",
            }
        ],
        "biographical_context": {
            "season": "autumn",
            "current_residence_context_tags": ["residence:campus_dorm"],
            "settled_biographical_coordinates": [
                {
                    "coordinate_ref": "coord:dorm-roommate",
                    "category": "residence",
                    "summary": "和室友林晚同住校内女生宿舍四人间，平时晚上常各自在桌前看书或整理笔记。",
                    "privacy_class": "personal",
                    "authority_event_ref": "event:coord:1",
                },
                {
                    "coordinate_ref": "coord:withheld",
                    "category": "personal",
                    "summary": "绝不应出现的隐藏坐标",
                    "privacy_class": "withhold",
                    "authority_event_ref": "event:coord:2",
                },
            ],
            "active_life_arcs": [
                {
                    "arc_id": "arc:midterm",
                    "context_summary": "正处在秋学期期中周，桌上摊着专业课讲义。",
                    "source_refs": ["event:arc:1"],
                }
            ],
        },
    }
    moment = _rendered_lived_moment(materials, logical_time)
    assert moment is not None
    assert "此刻正在：在宿舍休整" in moment
    assert "眼前环境余光：校内宿舍起居空间，秋季晚间时段" in moment
    assert "和室友林晚同住校内女生宿舍四人间" in moment
    assert "近期脉络：正处在秋学期期中周" in moment
    assert "绝不应出现的隐藏坐标" not in moment

    snapshot = {
        "contract": "inner-life-snapshot.1",
        "materials": {**materials, "lived_moment": moment},
    }
    presented = present_context_evidence({"inner_life_snapshot": snapshot})["inner_life_snapshot"]["materials"]
    assert "眼前环境余光：校内宿舍起居空间" in presented["lived_moment"]["current_moment"]
    assert "和室友林晚同住校内女生宿舍四人间" in presented["lived_moment"]["current_moment"]


def test_lived_moment_surfaces_life_weight_affect_carryover_and_upcoming_class_schedule() -> None:
    from datetime import datetime
    from companion_daemon.world_v2.affect_target_bounds import (
        DEEP_DECAY_OBJECT_REF,
        STANDARD_DECAY_OBJECT_REF,
        SUSTAINED_DECAY_OBJECT_REF,
        select_decay_object_ref,
    )
    from companion_daemon.world_v2.character_interior.contracts import _rendered_lived_moment
    from companion_daemon.world_v2.model_context_reading import present_context_evidence

    assert select_decay_object_ref(1800) == STANDARD_DECAY_OBJECT_REF
    assert select_decay_object_ref(3200) == SUSTAINED_DECAY_OBJECT_REF
    assert select_decay_object_ref(5200) == DEEP_DECAY_OBJECT_REF

    logical_time = datetime.fromisoformat("2026-11-02T09:30:00+08:00")
    materials = {
        "biographical_context": {
            "season": "autumn",
            "settled_biographical_coordinates": [
                {
                    "coordinate_ref": "coord:family-store",
                    "category": "family",
                    "summary": "家里小店旺季临时缺人手，周日傍晚前得给家里个准话。",
                    "privacy_class": "private",
                    "shared_in_chat": False,
                    "authority_event_ref": "event:coord:store",
                }
            ],
        },
        "affect": [
            {
                "episode_id": "affect:1",
                "status": "active",
                "components": [
                    {"dimension": "hurt", "intensity_bp": 4600},
                    {"dimension": "anxiety", "intensity_bp": 2800},
                ],
            }
        ],
        "world_planning": [
            {
                "world_planning": {
                    "title": "秋学期专业课表",
                    "upcoming": [
                        {
                            "instance_ref": "planning-instance:1",
                            "title": "中国现当代文学专题",
                            "detail": "文学院 302 教室",
                            "starts_at": "2026-11-02T10:00:00+08:00",
                            "ends_at": "2026-11-02T11:35:00+08:00",
                            "status": "scheduled",
                        }
                    ],
                }
            }
        ],
    }
    moment = _rendered_lived_moment(materials, logical_time)
    assert moment is not None
    assert "近期身上悬着的处境：家里小店旺季临时缺人手，周日傍晚前得给家里个准话（私下处境，未在聊天提过）" in moment
    assert "此刻情绪余波：委屈/受伤(4600bp)、悬心/不安(2800bp)" in moment
    assert "今日近期课表/日程：10:00–11:35 中国现当代文学专题（即将开始）" in moment

    snapshot = {
        "contract": "inner-life-snapshot.1",
        "materials": {**materials, "lived_moment": moment},
    }
    presented = present_context_evidence({"inner_life_snapshot": snapshot})["inner_life_snapshot"]["materials"]
    current_moment = presented["lived_moment"]["current_moment"]
    assert "近期身上悬着的处境：" in current_moment
    assert "此刻情绪余波：委屈/受伤(4600bp)" in current_moment
    assert "今日近期课表/日程：10:00–11:35 中国现当代文学专题（即将开始）" in current_moment

    afternoon_time = datetime.fromisoformat("2026-11-02T16:13:00+08:00")
    materials["world_planning"][0]["world_planning"]["upcoming"] = [
        {
            "instance_ref": "planning-instance:morning",
            "title": "周一现代文学史",
            "detail": "文学院 201",
            "starts_at": "2026-11-02T08:00:00+08:00",
            "ends_at": "2026-11-02T09:30:00+08:00",
            "status": "scheduled",
        },
        {
            "instance_ref": "planning-instance:afternoon",
            "title": "周一古代文论",
            "detail": "文学院 305",
            "starts_at": "2026-11-02T14:00:00+08:00",
            "ends_at": "2026-11-02T15:30:00+08:00",
            "status": "scheduled",
        },
    ]
    afternoon_moment = _rendered_lived_moment(materials, afternoon_time)
    assert afternoon_moment is not None
    assert (
        "今日近期课表/日程：08:00–09:30 周一现代文学史（今日较早时段）｜14:00–15:30 周一古代文论（刚结束）"
        in afternoon_moment
    )






