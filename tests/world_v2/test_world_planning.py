from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from companion_daemon.world_v2.world_planning import WorldPlanningDraft, expand_planning

ZONE = ZoneInfo("Asia/Shanghai")

def at(day, hour=0):
    return datetime(2026, 10, day, hour, tzinfo=ZONE)


def test_week_publication_iterator_keeps_128_domains_plus_midweek_revision(monkeypatch):
    from types import SimpleNamespace as NS
    from companion_daemon.world_v2 import life_content_reading, occurrence_result_content_runtime
    from companion_daemon.world_v2.world_planning import iter_planning_publications, planning_index, planning_publications
    # A source-reader fixture: canonical publication/permission validation has
    # separate negative coverage. Exercise selection and revision lineage here.
    bodies, occurrences = {}, []
    for i in range(129):
        key = i if i < 128 else 0
        ref = f"occurrence:{i}"
        value = planning(planning_key=f"domain-{key}", supersedes_occurrence_ref="occurrence:0" if i == 128 else None)
        bodies[ref] = value
        candidate = NS(candidate_result_ref=f"result:{i}", result_contract="world-consequence.3",
                       world_planning_index=planning_index(value), privacy_class="private", content_ref=f"content:{i}")
        occurrences.append(NS(status="settled", participant_refs=("actor",), occurrence_id=ref,
            settled_outcome_ref=candidate.candidate_result_ref, candidate_outcomes=(candidate,),
            settlement_world_revision=i+1, settled_at=at(16, 10) if i == 128 else at(12), visibility="private",
            result_payload_ref=f"payload:{i}", settlement_event_ref=f"event:{i}", settlement_payload_hash="a"*64))
    monkeypatch.setattr(occurrence_result_content_runtime, "require_published_world_consequence", lambda **kw: None)
    monkeypatch.setattr(life_content_reading, "selected_world_consequence",
                        lambda **kw: NS(world_planning=bodies[kw['occurrence'].occurrence_id]))
    options = dict(projection=NS(logical_time=at(18), world_occurrences=occurrences), store=object(), actor_ref="actor",
                   starts_at=at(12), ends_at=at(19), user_channel_limited_content_refs=frozenset())
    values = tuple(iter_planning_publications(**options))
    assert len(values) == 129
    assert next(p for p in values if p.occurrence_ref == "occurrence:0").effective_until == at(16, 10)
    assert len(planning_publications(**options, limit=8)) == 8
    with pytest.raises(ValueError, match="bounded"):
        planning_publications(**options, limit=257)


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


def test_new_planning_requires_event_start_detail_without_changing_old_publications():
    import json
    from companion_daemon.world_v2.world_planning import expand_planning
    base = planning().model_dump(mode="json")
    base["slots"][0]["event_text"] = "教室按课表开放。"
    legacy = WorldPlanningDraft.model_validate_json(json.dumps({**base, "contract": "world-planning.2",
        "detailed_through": "2026-10-26", "review_at": at(19).isoformat()}))
    assert legacy.slots[0].prepare_details is False
    upgraded = {**legacy.model_dump(mode="json"), "contract": "world-planning.3"}
    with pytest.raises(ValueError, match="detail"):
        WorldPlanningDraft.model_validate_json(json.dumps(upgraded))
    upgraded["slots"][0]["prepare_details"] = True
    current = WorldPlanningDraft.model_validate_json(json.dumps(upgraded))
    before = expand_planning(legacy, world_id="test", starts_at=at(12), ends_at=at(19), effective_from=at(1))
    after = expand_planning(current, world_id="test", starts_at=at(12), ends_at=at(19), effective_from=at(1))
    assert before[0].instance_ref == after[0].instance_ref


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
                if "rolling_preparation" in packet:
                        value = planning(contract="world-planning.3", detailed_through="2026-10-30",
                        review_at=at(21, 10).isoformat(), slots=[{**value.slots[0].model_dump(mode="json"),
                            "event_text": "学校的周五文学课程开始授课。", "prepare_details": True}])
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
        # A current revision uses the exact published planning, whereas the
        # previously offered first edition retains its original null predecessor.
        from companion_daemon.world_v2.world_outlook import WorldOutlookDraft
        runtime = app._life_ecology._world_outlook
        original_draft = runtime.store.head()[1]
        runtime._validate_offered_nodes(original_draft, projection=state)
        revised = original_draft.model_dump(mode="json")
        new_node = revised["opportunities"][0]
        new_node["opportunity_id"] += "-revision"
        with pytest.raises(ValueError, match="exact current publication"):
            runtime._validate_offered_nodes(WorldOutlookDraft.model_validate_json(json.dumps(revised)), projection=state)
        new_node["execution"]["planning"]["supersedes_occurrence_ref"] = values[0].occurrence_ref
        runtime._validate_offered_nodes(WorldOutlookDraft.model_validate_json(json.dumps(revised)), projection=state)
        rewritten = original_draft.model_dump(mode="json")
        rewritten["opportunities"][0]["execution"]["planning"]["title"] = "不能重写已发布的节点"
        with pytest.raises(ValueError, match="cannot be rewritten"):
            runtime._validate_offered_nodes(WorldOutlookDraft.model_validate_json(json.dumps(rewritten)), projection=state)
        assert not state.plans
        assert not any(e.event_type == "ActivityStarted" for e in state.committed_world_event_refs)
        # Follow the same published source through Life's actual purpose view,
        # not only the ordinary chat view checked below.
        from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
        from companion_daemon.world_v2.context_resolver import query_from_projection
        from companion_daemon.world_v2.character_interior.life_source_readings import prepare_life_source_readings
        from companion_daemon.world_v2.character_interior.life_source_review import prepare_review
        from test_life_fact_readings import _fact_view
        from test_life_fact_value_display import candidate

        clock_ref = next(e.event_id for e in reversed(state.committed_world_event_refs) if e.event_type == "ClockAdvanced")
        compiler = context_capsule_compiler_from_ledger(ledger=app._ledger, life_content_store=app._life_content_store)
        capsule = compiler.compile(query_from_projection(state, actor_ref="actor:companion", trigger_ref=clock_ref))
        old_view, old_snapshot = await _fact_view(capsule, review_contract="life-source-review.21")
        old_bytes = old_view.model_dump_json()
        assert "world_planning" not in old_view.author_payload()["inner_life_snapshot"]["materials"]
        view, life_snapshot = await _fact_view(capsule, review_contract="life-source-review.22")
        assert view.author_payload()["inner_life_snapshot"]["materials"]["world_planning"]
        readings = prepare_life_source_readings(view=view, snapshot=life_snapshot).as_dict()
        arrangements = [r for r in readings["readings"] if r["source_family"] == "published_world_planning"]
        assert arrangements and all(r["permissions"] == [["external_arrangement", "general"]] for r in arrangements), readings["excluded"]
        assert all(r["value"]["world_planning"]["epistemic_scope"] == "published_external_arrangement_not_attendance" for r in arrangements)
        from copy import deepcopy
        from companion_daemon.world_v2.character_interior.life_source_readings import _planning_reading
        from companion_daemon.world_v2.selected_source_composer import compile_selected_source_table
        row = next(r for r in compile_selected_source_table(capsule=capsule, include_subjective_history=True,
            include_lifecycle_states=True).source_references() if r['source_ref'] == values[0].occurrence_ref)
        shown = view.author_payload()['inner_life_snapshot']
        altered = deepcopy(shown)
        altered['materials']['world_planning'][0]['world_planning']['title'] = '未公布的另一个课表'
        assert _planning_reading(row, rendered=altered, snapshot=life_snapshot)[0] is None
        with pytest.raises(ValueError, match='actor, time or settlement'):
            _planning_reading(row, rendered=shown, snapshot=life_snapshot.model_copy(update={'actor_ref': 'actor:other'}))
        broken = deepcopy(row)
        broken['review_material']['item']['source_bindings'][0]['immutable_hash'] = '0' * 64
        with pytest.raises(ValueError, match='actor, time or settlement'):
            _planning_reading(broken, rendered=shown, snapshot=life_snapshot)
        assert not any(r['source_family'] == 'published_world_planning' for r in
            prepare_life_source_readings(view=old_view, snapshot=old_snapshot).as_dict()['readings'])
        prepared, _ = prepare_review(candidate_json=candidate(), provider_raw=candidate(), view=view, snapshot=life_snapshot)
        assert json.loads(json.loads(prepared)["request"]["messages"][1]["content"])["contract"] == "life-source-review.22"
        old_view.verify_snapshot(old_snapshot)
        assert old_view.model_dump_json() == old_bytes
        assert app._ledger.project() == state
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
