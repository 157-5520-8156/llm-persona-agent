from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from companion_daemon.world_v2.world_planning import WorldPlanningDraft, expand_planning

ZONE = ZoneInfo("Asia/Shanghai")

def at(day, hour=0):
    return datetime(2026, 10, day, hour, tzinfo=ZONE)


def planning(**changes):
    return WorldPlanningDraft.model_validate_json(__import__('json').dumps({
        "planning_key": "autumn-classes", "title": "秋季学期课程安排", "summary": "学校已公布的课程安排。",
        "starts_on": "2026-10-01", "ends_on": "2027-01-15",
        "slots": [{"slot_id": "literature", "title": "文学课", "detail": "按课程安排授课。",
                   "weekdays": [0], "start_minute": 14 * 60, "duration_minutes": 120}], **changes,
    }))


def expand(value, start=12, end=19, **kwargs):
    return expand_planning(value, world_id="test", starts_at=at(start), ends_at=at(end), effective_from=at(1), **kwargs)


def test_recurrence_is_known_schedule_not_attendance():
    value = planning()
    item, = expand(value)
    assert item.starts_at == at(12, 14) and item.ends_at == at(12, 16)
    assert item.status == "scheduled"
    assert "不证明角色参加" in value.public_text()


def test_move_preserves_identity_and_is_visible_in_replacement_week():
    original, = expand(planning())
    moved = planning(exceptions=[{"slot_id": "literature", "original_date": "2026-10-12", "action": "reschedule",
                                 "new_start": at(20, 10).isoformat(), "duration_minutes": 90, "explanation": "调课通知。"}])
    assert expand(moved) == ()
    items = expand(moved, 19, 26)
    changed = next(i for i in items if i.original_start == original.original_start)
    assert changed.instance_ref == original.instance_ref and changed.starts_at == at(20, 10)


def test_cancel_retains_instance_and_publication_does_not_backfill_experience():
    cancelled = planning(exceptions=[{"slot_id": "literature", "original_date": "2026-10-12", "action": "cancel", "explanation": "本次停课。"}])
    assert expand(cancelled)[0].status == "cancelled"
    assert expand_planning(cancelled, world_id="test", starts_at=at(12), ends_at=at(19), effective_from=at(13)) == ()


def test_expansion_stays_bounded_even_for_two_years():
    value = planning(ends_on="2028-09-30")
    with pytest.raises(ValueError, match="bounded window"):
        expand_planning(value, world_id="test", starts_at=at(1), ends_at=datetime(2028,9,30,tzinfo=ZONE), effective_from=at(1))
    assert len(expand(value)) == 1


@pytest.mark.parametrize("changes", [{"slots": [] ,"exceptions": [{"slot_id": "unknown", "original_date": "2026-10-12", "action": "cancel", "explanation": "invalid"}]},
                                     {"ends_on": "2035-01-01"}])
def test_invalid_planning_is_rejected(changes):
    with pytest.raises(ValueError):
        planning(**changes)


@pytest.mark.asyncio
async def test_outlook_publishes_planning_into_real_role_context_without_attendance(tmp_path, monkeypatch):
    import json
    from datetime import timedelta
    from test_world_outlook_closed_loop import _WorldAuthor, _Role, _build
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
    from companion_daemon.world_v2.world_turn_runtime import InboundTurn
    from companion_daemon.world_v2.world_planning import planning_publications

    class Planner(_WorldAuthor):
        async def complete(self, messages, **kwargs):
            packet = decode_outlook_request(messages[1]["content"])
            raw = json.loads(await super().complete(messages, **kwargs))
            if not packet["parent_revision"]:
                node = raw["draft"]["opportunities"][0]
                value = planning(slots=[{"slot_id": "friday", "title": "周五文学课", "detail": "由学校安排的文学课程。",
                                        "weekdays": [4], "start_minute": 840, "duration_minutes": 120}])
                node["external_possibility"] = "学校即将公布本学期的课程安排。"
                node["execution"] = {"kind": "planning", "text": "学校公布课程安排。", "planning": value.model_dump(mode="json"),
                                     "duration_minutes": 1, "privacy_class": "private"}
                raw["draft"]["opportunities"] = [node]
            return json.dumps(raw, ensure_ascii=False)

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    now = at(16, 10)
    role, author = _Role(), Planner()
    app, model = _build(tmp_path / "planning-app.sqlite", "world:planning-test", now, role, author)
    try:
        for minute in (1, 2, 3, 4):
            previous = await app.current_logical_time()
            key = f"planning-{minute}"
            await app.tick(tick_id=key, logical_time_from=previous, logical_time_to=now + timedelta(minutes=minute),
                           observed_at=now + timedelta(minutes=minute), trace_id=key, causation_id="clock", correlation_id=key,
                           reason="planning-qualification", run_life_ecology=False)
            await app.advance_life_ecology_once(wake_event_ref="event:trigger:clock:" + key, trace_id=key, correlation_id=key)
            for _ in range(3):
                if await app.drain_background_once() is None:
                    break
        state = app._ledger.project()
        values = planning_publications(projection=state, store=app._life_content_store, actor_ref="actor:companion",
                                       user_channel_limited_content_refs=frozenset())
        assert len(values) == 1, app.world_outlook_health()
        assert values[0].planning.title == "秋季学期课程安排"
        published = next(o for o in state.world_occurrences if o.occurrence_id == values[0].occurrence_ref)
        assert planning_publications(projection=state, store=app._life_content_store, actor_ref="actor:companion",
            user_channel_limited_content_refs=frozenset({published.candidate_outcomes[0].content_ref})) == ()
        from companion_daemon.world_v2.dashboard_home_snapshot import DashboardHomeSnapshotModule
        from companion_daemon.world_v2.dashboard_agenda import week_start_date
        module = DashboardHomeSnapshotModule(ledger=app._ledger, deployment_id="test", boot_id="test",
            life_actor_ref="actor:companion", life_privacy_ceiling="private", life_content_store=app._life_content_store)
        before = app.export_replay_evidence()
        snapshot = await module.capture(week=week_start_date(now.date()))
        agenda = snapshot.sections.overview_life.data.agenda
        items = [e for e in agenda.entries if e.kind == "planning"]
        assert {e.time_kind for e in items} == {"period", "timed"}
        assert any(e.title == "周五文学课" and e.related for e in items)
        assert app.export_replay_evidence() == before
        experiences = [e for e in agenda.entries if e.kind == "experience"]
        assert experiences and all(e.content_state == "read" for e in experiences)
        assert all(e.ends_at < at(16, 14) for e in experiences), "experiencing a notice is not attending its future class"
        assert not state.plans
        assert not any(e.event_type == "ActivityStarted" for e in state.committed_world_event_refs)
        await app.respond(InboundTurn(platform="test", platform_user_id="user.1", platform_message_id="planning-question",
                                     text="你今天下午有什么安排？", observed_at=state.logical_time, trace_id="planning-question"))
        request = next(v for v in reversed(role.materials) if not v.get("inner_turn", {}).get("purpose"))
        arrangements = request["inner_life_snapshot"]["materials"].get("world_planning")
        assert arrangements, request["inner_life_snapshot"]["materials"].keys()
        text = json.dumps(arrangements, ensure_ascii=False)
        assert "周五文学课" in text and "published_external_arrangement_not_attendance" in text
        instance = arrangements[0]["world_planning"]["upcoming"][0]
        assert datetime.fromisoformat(instance["starts_at"]).astimezone(ZONE).hour == 14
        assert datetime.fromisoformat(instance["ends_at"]).astimezone(ZONE).hour == 16
    finally:
        await app.aclose()
        await model.aclose()


def test_repeated_reschedule_keeps_original_identity_after_original_date_passed():
    original, = expand(planning())
    revised = planning(exceptions=[{"slot_id": "literature", "original_date": "2026-10-12", "action": "reschedule",
                                   "new_start": at(21, 10).isoformat(), "duration_minutes": 90, "explanation": "再次调课。"}])
    items = expand_planning(revised, world_id="test", starts_at=at(19), ends_at=at(26), effective_from=at(19))
    changed = next(i for i in items if i.original_start == original.original_start)
    assert changed.instance_ref == original.instance_ref and changed.starts_at == at(21, 10)


def test_revision_cannot_resurrect_elapsed_occurrence_but_can_move_pending_replacement():
    from companion_daemon.world_v2.world_planning import validate_planning_revision
    exception = {"slot_id": "literature", "original_date": "2026-10-12", "action": "reschedule",
                 "new_start": at(21, 10).isoformat(), "duration_minutes": 90, "explanation": "调课。"}
    current = planning(exceptions=[exception])
    with pytest.raises(ValueError, match="already elapsed"):
        validate_planning_revision(planning(), current, logical_time=at(19))
    pending = planning(exceptions=[{**exception, "new_start": at(20, 10).isoformat()}])
    validate_planning_revision(pending, current, logical_time=at(19))


def test_cancel_moved_instance_keeps_its_cancelled_actual_window():
    from companion_daemon.world_v2.world_planning import validate_planning_revision
    moved = {"slot_id": "literature", "original_date": "2026-10-12", "action": "reschedule",
             "new_start": at(20, 10).isoformat(), "duration_minutes": 90, "explanation": "调课。"}
    prior = planning(exceptions=[moved])
    cancelled = planning(exceptions=[{**moved, "action": "cancel", "explanation": "本次停课。"}])
    validate_planning_revision(prior, cancelled, logical_time=at(19))
    values = expand_planning(cancelled, world_id="test", starts_at=at(19), ends_at=at(26), effective_from=at(19))
    item = next(v for v in values if v.original_start == at(12,14))
    assert item.status == "cancelled" and item.starts_at == at(20,10)
    assert item.instance_ref == expand(prior,19,26)[1].instance_ref
    invalid = planning(exceptions=[{k:v for k,v in {**moved,"action":"cancel"}.items() if k not in {"new_start","duration_minutes"}}])
    with pytest.raises(ValueError, match="exact prior window"):
        validate_planning_revision(prior, invalid, logical_time=at(19))


def test_removing_an_exception_cannot_resurrect_an_advanced_class():
    from companion_daemon.world_v2.world_planning import validate_planning_revision
    prior = planning(exceptions=[{"slot_id":"literature","original_date":"2026-10-19","action":"reschedule",
                                 "new_start":at(16,10).isoformat(),"duration_minutes":90,"explanation":"提前上课。"}])
    with pytest.raises(ValueError, match="already elapsed"):
        validate_planning_revision(prior, planning(), logical_time=at(18))
