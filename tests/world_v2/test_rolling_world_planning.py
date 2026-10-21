"""Rolling preparation through the existing author, clock, publications and role context."""
from datetime import datetime, timedelta
import json

import pytest

from companion_daemon.world_v2.world_planning import WorldPlanningDraft, expand_planning
from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
from test_world_planning import at
from test_world_outlook_closed_loop import _build, _Role, FUTURE


def semester(now, **changes):
    value = {
        "contract": "world-planning.2", "planning_key": "school-year", "title": "学校阶段安排",
        "summary": "正常教学后进入实训，十一月有已公布的活动周。",
        "starts_on": "2026-10-01", "ends_on": "2027-09-30",
        "detailed_through": (now + timedelta(days=7)).date().isoformat(),
        "review_at": (now + timedelta(days=7)).isoformat(),
        "slots": [
            {"slot_id": "class", "title": "文学课", "detail": "普通教学周的课程。", "weekdays": [0, 2],
             "start_minute": 840, "duration_minutes": 90, "event_text": "教学楼的文学课程按安排开始。", "prepare_details": True},
            {"slot_id": "training", "title": "校内实训", "detail": "实训周的工作坊。", "weekdays": [0, 2],
             "start_minute": 840, "duration_minutes": 120, "event_text": "实训中心的工作坊开始开放。"},
        ],
        "phases": [
            {"phase_id": "training", "starts_on": "2026-10-26", "ends_on": "2026-10-30",
             "title": "实训周", "summary": "本周实训替代普通课程。", "slot_ids": ["training"]},
            {"phase_id": "sports", "starts_on": "2026-11-02", "ends_on": "2026-11-08",
             "title": "学校运动会", "summary": "活动周没有常规课程，具体活动安排待进一步公布。", "slot_ids": []},
        ],
        **changes,
    }
    if value["contract"] == "world-planning.3":
        value["slots"] = [{**slot, "prepare_details": True} for slot in value["slots"]]
    return WorldPlanningDraft.model_validate_json(json.dumps(value, ensure_ascii=False))


def test_phase_replaces_weekly_classes_without_expanding_undetailed_future():
    p = semester(at(16, 10), detailed_through="2026-11-10")
    def week(day):
        return expand_planning(p, world_id="phase", starts_at=day, ends_at=day + timedelta(days=7), effective_from=at(16))
    assert [i.title for i in week(at(19))] == ["文学课", "文学课"]
    assert [i.title for i in week(at(26))] == ["校内实训", "校内实训"]
    assert week(datetime.fromisoformat("2026-11-02T00:00:00+08:00")) == ()
    assert len(week(datetime.fromisoformat("2026-11-09T00:00:00+08:00"))) == 1
    assert week(datetime.fromisoformat("2026-12-07T00:00:00+08:00")) == ()
    assert p.preparation_due() == at(23, 10)  # authored review precedes detail exhaustion


def test_rule_time_edit_keeps_identity_and_cannot_reoffer_started_instance():
    from companion_daemon.world_v2.world_planning import validate_planning_revision
    old = semester(at(16, 10))
    revised = semester(at(16, 10), slots=[{**s.model_dump(mode="json"), "start_minute": 960} for s in old.slots])
    args = dict(world_id="test", starts_at=at(19), ends_at=at(20), effective_from=at(16))
    assert expand_planning(old, **args)[0].instance_ref == expand_planning(revised, **args)[0].instance_ref
    # A claimed but unactivated event can still be rescheduled at its exact opening.
    validate_planning_revision(old, revised, logical_time=at(19, 14))
    with pytest.raises(ValueError, match="already elapsed"):
        validate_planning_revision(old, revised, logical_time=at(19, 14), executed_instances={("class", at(19).date())})


@pytest.mark.asyncio
async def test_outlook_reuses_verified_old_head_until_history_generation_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    app, model = _build(tmp_path / "verified-head.sqlite", "world:verified-head",
                        at(16, 10), _Role(), RollingAuthor())
    try:
        for minute in (1, 2, 3, 4):
            await tick(app, at(16, 10) + timedelta(minutes=minute), f"verified-head-{minute}")
        runtime = app._life_ecology._world_outlook
        old_cursor = runtime.store.head()[2]["cursor"]
        original = app._ledger.project_at
        calls = []

        def counted(cursor):
            if cursor.model_dump(mode="json") == old_cursor:
                calls.append(cursor)
            return original(cursor)

        monkeypatch.setattr(app._ledger, "project_at", counted)
        runtime._verified_head_source = None
        runtime.view()
        assert calls
        first = len(calls)
        runtime.view()
        assert len(calls) == first
        generation = app._ledger.verified_history_generation()
        monkeypatch.setattr(app._ledger, "verified_history_generation", lambda: generation + 1)
        runtime.view()
        assert len(calls) > first
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_fresh_outlook_does_not_replay_its_just_verified_pin_and_due_read_stays_off_loop(tmp_path, monkeypatch):
    import sys
    import threading
    app, model = _build(tmp_path / "fresh-pin.sqlite", "world:fresh-pin", at(16, 10), _Role(), RollingAuthor())
    runtime = app._life_ecology._world_outlook
    original = app._ledger.project_at

    def guarded(cursor):
        if sys._getframe(1).f_code.co_name == "view":
            head = runtime.store.head()
            assert head is None or cursor.model_dump() != head[2]["cursor"], "fresh pin replayed genesis"
        return original(cursor)

    monkeypatch.setattr(app._ledger, "project_at", guarded)
    try:
        for minute in (1, 2, 3):
            await tick(app, at(16, 10) + timedelta(minutes=minute), f"fresh-pin-{minute}")
        runtime.view()
        event_loop_thread = threading.get_ident()
        calls = []

        def due():
            calls.append(threading.get_ident())
            assert calls[-1] != event_loop_thread
            return None

        monkeypatch.setattr(app._life_ecology, "next_outlook_due", due)
        await app.life_ecology_next_due()
        assert calls
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_five_domains_and_dense_weeks_keep_all_authoritative_schedules(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")

    class Domains:
        model = "fixture:five-domains"
        calls = 0

        async def complete(self, messages, **kwargs):
            p = decode_outlook_request(messages[1]["content"])
            now = datetime.fromisoformat(p["logical_time"])
            self.calls += 1
            known = {v["planning_key"] for v in p["rolling_preparation"]["known_publications"]}
            nodes = [{k: v for k, v in n.items() if k != "state"} for n in p["current_outlook"]["opportunities"]
                     if n["execution"]["planning"]["planning_key"] not in known]
            pending = {n["execution"]["planning"]["planning_key"] for n in nodes}
            for i in range(5):
                key = "domain-" + str(i)
                if key in known | pending or len(nodes) == 4:
                    continue
                plan = semester(now, contract="world-planning.3", planning_key=key, phases=[], detailed_through="2026-10-30", slots=[{
                    "slot_id": "s" + str(j), "title": "课", "detail": "课程", "weekdays": list(range(7)),
                    "start_minute": 840, "duration_minutes": 30, "event_text": "课程开放"} for j in range(14)])
                nodes.append({"opportunity_id": key, "thread_refs": ["ordinary"], "external_possibility": "公布安排",
                    "lane": "world", "basis_refs": p["basis_refs"][:1], "opens_at": now.isoformat(),
                    "closes_at": (now + timedelta(days=2)).isoformat(),
                    "execution": {"kind": "planning", "text": "公布安排", "planning": plan.model_dump(mode="json"), "duration_minutes": 1}})
            value = {
                "world_id": p["world_id"], "actor_ref": p["actor_ref"], "parent_revision": p["parent_revision"],
                "revise_reason": "分批准备", "review_at": (now + timedelta(days=7)).isoformat(),
                "horizon_end": (now + timedelta(days=30)).isoformat(),
                "threads": [{"thread_id": "ordinary", "situation": "日常事务", "open_question": "各领域如何推进"}],
                "long_term": [{"stage_id": "domain-" + str(i), "starts_on": now.date().isoformat(),
                    "ends_on": (now + timedelta(days=365)).date().isoformat(), "situation": "一个独立外部领域",
                    "planning_key": "domain-" + str(i)} for i in range(5)], "opportunities": nodes}
            from test_story_outlook_replanning import apply_story_contract
            return json.dumps({"decision": "revise", "draft": apply_story_contract(value, p)}, ensure_ascii=False)

    now = at(16, 10)
    app, model = _build(tmp_path / "domains.sqlite", "world:domains", now, _Role(), Domains())
    try:
        for minute in range(1, 18):
            await tick(app, now + timedelta(minutes=minute), f"domains-{minute}")
        runtime = app._life_ecology._world_outlook
        pubs = runtime._planning_publications(app._ledger.project())
        assert len(pubs) == 5, runtime.health()
        # All 98 weekly instances in each published domain remain accessible;
        # the scheduler's bounded working set is not the display's full week.
        from companion_daemon.world_v2.dashboard_home_snapshot import DashboardHomeSnapshotModule
        from companion_daemon.world_v2.dashboard_agenda import week_start_date
        module = DashboardHomeSnapshotModule(ledger=app._ledger, deployment_id="test", boot_id="pages",
            life_actor_ref="actor:companion", life_privacy_ceiling="private", life_content_store=app._life_content_store)
        first = (await module.capture(week=week_start_date(at(19).date()))).sections.overview_life.data.agenda
        entries = []
        for page in range(first.page_count):
            agenda = (await module.capture(week=first.week_start, page=page)).sections.overview_life.data.agenda
            assert agenda.planning_state == "ready" and len(agenda.entries) <= 96
            entries.extend(e for e in agenda.entries if e.kind == "planning")
        assert len(entries) == 5 * 98 + 5
        scheduled = runtime.scheduled()
        assert {n["thread_refs"][0] for n in scheduled} == {"planning:domain-" + str(i) for i in range(5)}
        assert len(scheduled) == 5 * 96
        assert not runtime.view()["rolling_preparation"]["due"]
        assert runtime.next_due() == at(16, 14)
        for minute in (0, 1):
            await tick(app, at(16, 14) + timedelta(minutes=minute), f"dense-start-{minute}")
            if minute == 0:
                assert runtime.next_due() == at(16, 14) + timedelta(seconds=1)
        effects = [o for o in runtime.store.offers() if o["state"] == "considered"
                   and o["payload"].get("origin", {}).get("planning_instance_origin")]
        assert len(effects) >= 16
        assert len({o["payload"]["origin"]["planning_instance_origin"]["instance_ref"] for o in effects}) == len(effects)
        assert not app._ledger.project().plans
    finally:
        await app.aclose()
        await model.aclose()


class RollingAuthor:
    model = "fixture:rolling-world-author"

    def __init__(self):
        self.calls = []
        self.failures_remaining = 0
        self.cancel_monday = False
        self.review_days = 7
        self.reschedule_monday = False

    async def complete(self, messages, **kwargs):
        packet = decode_outlook_request(messages[1]["content"])
        self.calls.append(packet)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise TimeoutError("fixture provider timeout")
        now = datetime.fromisoformat(packet["logical_time"])
        published = packet["rolling_preparation"]["published"]
        due = packet["rolling_preparation"]["due"]
        current = packet["current_outlook"]
        if (published and not due and not self.cancel_monday
                and all(datetime.fromisoformat(n["closes_at"]) > now for n in current["opportunities"])):
            return json.dumps({"decision": "keep", "review_at": (now + timedelta(days=1)).isoformat(), "reason": "原安排仍有效"})
        p = semester(now, contract="world-planning.3", **({"supersedes_occurrence_ref": published[0]["occurrence_ref"]} if published else {}))
        if self.cancel_monday:
            p = semester(now, contract="world-planning.3", supersedes_occurrence_ref=published[0]["occurrence_ref"], exceptions=[{
                "slot_id": "class", "original_date": "2026-10-19", "action": "cancel", "explanation": "学校临时停课通知。"}])
        if self.reschedule_monday:
            p = semester(now, contract="world-planning.3", supersedes_occurrence_ref=published[0]["occurrence_ref"], exceptions=[{
                "slot_id": "class", "original_date": "2026-10-19", "action": "reschedule",
                "new_start": at(19, 16).isoformat(), "duration_minutes": 90, "explanation": "学校临时调课通知。"}])
        node = {"opportunity_id": "publish-" + str(len(self.calls)), "thread_refs": ["school"],
                "external_possibility": "学校公布阶段安排", "lane": "world", "basis_refs": packet["basis_refs"][:1],
                "opens_at": now.isoformat(), "closes_at": (now + timedelta(days=2)).isoformat(),
                "execution": {"kind": "planning", "text": "学校公布阶段安排", "privacy_class": "private",
                              "duration_minutes": 1, "planning": p.model_dump(mode="json")}}
        draft = {"world_id": packet["world_id"], "actor_ref": packet["actor_ref"],
                 "parent_revision": packet["parent_revision"], "revise_reason": "滚动补齐安排",
                 "review_at": (now + timedelta(days=self.review_days)).isoformat(), "horizon_end": (now + timedelta(days=30)).isoformat(),
                 "threads": [{"thread_id": "school", "situation": "学校生活", "open_question": "角色自行决定行动"}],
                 "long_term": [{"stage_id": "year", "starts_on": now.date().isoformat(),
                    "ends_on": (now + timedelta(days=365)).date().isoformat(), "planning_key": "school-year", "situation": FUTURE}],
                 "opportunities": [node]}
        from test_story_outlook_replanning import apply_story_contract
        return json.dumps({"decision": "revise", "draft": apply_story_contract(draft, packet)}, ensure_ascii=False)


def course_detail_draft(wake, *, hidden, closing_offset):
    from test_world_consequence_producer import _draft

    value = _draft(wake)
    value.update(authored_subject_ref="actor:companion", location_ref=None,
                 location_capability_ref=None, privacy_class="private")
    for outcome in value["outcomes"]:
        outcome.update(experienced_by_ref="actor:companion", privacy_class="private", visual_evidence=None)
    claim = [value["claim_declarations"][0]["claim_id"]]
    value["event_details"] = [
        {"after_seconds": 0, "text": "教室开始开放。", "claim_refs": claim},
        {"after_seconds": 120, "text": hidden, "claim_refs": claim},
        {"after_seconds": closing_offset, "text": "本节课临近结束。", "claim_refs": claim},
    ]
    return json.dumps(value, ensure_ascii=False)


@pytest.mark.asyncio
async def test_new_course_prepares_full_detail_at_activation_and_releases_only_opening(tmp_path, monkeypatch):
    from companion_daemon.world_v2.life_content import LifeContentCompiler
    from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentProposalReader
    from companion_daemon.world_v2.character_interior.snapshot_compiler import _experience_entry
    from companion_daemon.world_v2.world_life_context import WorldLifeContextCompiler
    from companion_daemon.world_v2.schemas import ProjectionCursor
    from companion_daemon.world_v2.world_outlook_runtime import cursor_of
    from test_day_open_world_consequence_material import _author_runtime
    from test_life_development_runtime import _SequenceModel, _novel_origin_review
    from test_world_author_request_audit import _ReceivedAuthor

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    app, model = _build(tmp_path / "automatic-course.sqlite", "world:automatic-course",
                        at(16, 10), _Role(), RollingAuthor())
    try:
        for minute in (1, 2, 3, 4):
            await tick(app, at(16, 10) + timedelta(minutes=minute), f"course-plan-{minute}")
        runtime = app._life_ecology._world_outlook
        experiences_before = len(app._ledger.project().experiences)
        wake = await tick(app, at(19, 14), "course-start", ecology=False)
        hidden = "PRIVATE_CLASS_DETAIL：教学楼外传来临时广播。"
        author = _ReceivedAuthor(app._life_content_store, [course_detail_draft(
            app._ledger.lookup_event_commit(wake)[0], hidden=hidden, closing_offset=89 * 60)])
        development = _author_runtime(app._ledger, app._life_content_store, author,
            actor="actor:companion", critic=_SequenceModel(
                model="fixture:detail-review", outputs=(_novel_origin_review(decision="supported"),)))
        development._world_outlook = runtime
        app._life_ecology._life_development_followup = development

        result = await app.advance_life_ecology_once(wake_event_ref=wake,
                                                     trace_id="course-start", correlation_id="course-start")
        assert result.status not in {"failed_safe", "rejected"}, result
        active = [o for o in app._ledger.project().world_occurrences
                  if o.status == "active" and o.event_detail_program is not None]
        assert len(active) == 1
        assert active[0].event_detail_program.offsets == (0, 120, 89 * 60)
        assert len(author.received) == 1
        assert development.pending_world_event_detail_ref(required_planning_only=True) is None
        state = app._ledger.project()
        phase = next(o for o in state.world_occurrences
                     if o.status == "active" and o.occurrence_id != active[0].occurrence_id
                     and o.trigger_ref.startswith("event:world-outlook:execution:")
                     and app._ledger.lookup_event_commit(o.trigger_ref)[0].payload()
                        .get("central_authorship", {}).get("event_detail_origin", {}).get("phase") == 0)
        from test_world_consequence_aftermath import _aftermath, _NoCharacterCalls
        aftermath = _aftermath(app._ledger, app._life_content_store, _NoCharacterCalls())
        published_instance = next(o for o in state.world_occurrences
                                  if o.status == "active" and o.activation_authority == "world_outlook"
                                  and app._ledger.lookup_event_commit(o.trigger_ref)[0].payload()
                                     .get("central_authorship", {}).get("planning_instance_origin"))
        assert aftermath._is_external_arrangement_instance(published_instance)
        assert aftermath._is_external_arrangement_instance(active[0])
        assert aftermath._is_external_arrangement_instance(phase)
        premise = LifeDevelopmentProposalReader(ledger=app._ledger, content_store=app._life_content_store)
        visible = premise.read_active_occurrence(occurrence_id=phase.occurrence_id,
            expected_cursor=ProjectionCursor(**cursor_of(state)), actor_ref="actor:companion",
            viewer_privacy_ceiling="private")
        assert visible is not None and visible.premise.text == "教室开始开放。"
        assert visible.participation_scope == "external_event_not_character_attendance_or_presence"
        role_reading = _experience_entry({"source_ref": phase.occurrence_id,
            "value": visible.model_dump(mode="json")}, lane="world_life", include_source_kind=True)
        assert role_reading["participation_scope"] == visible.participation_scope
        context = WorldLifeContextCompiler(
            life_content=LifeContentCompiler(store=app._life_content_store),
            active_occurrence_reader=premise,
        ).compile(projection=state, actor_ref="actor:companion",
                  cursor=ProjectionCursor(**cursor_of(state)), viewer_privacy_ceiling="private")
        assert "教室开始开放" in json.dumps([item.model_dump(mode="json") for item in context], ensure_ascii=False)
        assert hidden not in json.dumps([item.model_dump(mode="json") for item in context], ensure_ascii=False)
        assert not state.plans and len(state.experiences) == experiences_before

        due_wake = await tick(app, at(19, 14) + timedelta(minutes=2), "course-next-phase", ecology=False)
        assert runtime.event_details.advance(wake_event_ref=due_wake) == 1
        state = app._ledger.project()
        second = next(o for o in state.world_occurrences if o.status == "active"
                      and o.trigger_ref.startswith("event:world-outlook:execution:")
                      and app._ledger.lookup_event_commit(o.trigger_ref)[0].payload()
                         .get("central_authorship", {}).get("event_detail_origin", {}).get("phase") == 1)
        next_visible = premise.read_active_occurrence(occurrence_id=second.occurrence_id,
            expected_cursor=ProjectionCursor(**cursor_of(state)), actor_ref="actor:companion",
            viewer_privacy_ceiling="private")
        assert next_visible is not None and next_visible.premise.text == hidden
        from test_world_consequence_producer import _advance as advance_aftermath
        settled_wake = await tick(app, at(19, 14) + timedelta(minutes=3), "course-phase-settled", ecology=False)
        await advance_aftermath(aftermath, app._ledger.lookup_event_commit(settled_wake)[0])
        state = app._ledger.project()
        first = next(o for o in state.world_occurrences if o.occurrence_id == phase.occurrence_id)
        assert first.status == "settled"
        assert not any(trigger.process_kind == "npc_world_appraisal"
                       and trigger.source_evidence_ref == first.settlement_event_ref
                       for trigger in state.trigger_processes)
        assert len(state.experiences) == experiences_before
        settled_context = WorldLifeContextCompiler(
            ledger=app._ledger, life_content=LifeContentCompiler(store=app._life_content_store),
        ).compile(projection=state, actor_ref="actor:companion",
                  cursor=ProjectionCursor(**cursor_of(state)), viewer_privacy_ceiling="private")
        first_reading = next(item for item in settled_context
                             if getattr(item, "occurrence_id", None) == first.occurrence_id)
        assert first_reading.participation_scope == "external_event_not_character_attendance_or_presence"
        role_reading = _experience_entry({"source_ref": first.occurrence_id,
            "value": first_reading.model_dump(mode="json")}, lane="world_life", include_source_kind=True)
        assert role_reading["participation_scope"] == first_reading.participation_scope
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_required_course_detail_retries_before_its_window_ends_after_two_failures(tmp_path, monkeypatch):
    from test_day_open_world_consequence_material import _author_runtime
    from test_life_development_runtime import _SequenceModel, _novel_origin_review
    from test_world_author_request_audit import _ReceivedAuthor

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    app, model = _build(tmp_path / "course-retry.sqlite", "world:course-retry",
                        at(16, 10), _Role(), RollingAuthor())
    try:
        for minute in (1, 2, 3, 4):
            await tick(app, at(16, 10) + timedelta(minutes=minute), f"retry-plan-{minute}")
        runtime = app._life_ecology._world_outlook
        author = _ReceivedAuthor(app._life_content_store,
                                 [TimeoutError("fixture timeout one"), TimeoutError("fixture timeout two")])
        development = _author_runtime(app._ledger, app._life_content_store, author,
            actor="actor:companion", critic=_SequenceModel(
                model="fixture:detail-review", outputs=(_novel_origin_review(decision="supported"),)))
        development._world_outlook = runtime
        app._life_ecology._life_development_followup = development
        wake = await tick(app, at(19, 14), "retry-course-start", ecology=False)
        for attempt in (1, 2):
            result = await app.advance_life_ecology_once(
                wake_event_ref=wake, trace_id=f"retry-course-{attempt}",
                correlation_id=f"retry-course-{attempt}")
            assert result.status == "failed_safe" and result.technical_failure_code.startswith("event_details.")
            source = development.pending_world_event_detail_ref(required_planning_only=True)
            assert source
            now = app._ledger.project().logical_time
            due = app._life_ecology._required_detail_retry_due(source_ref=source, logical_time=now)
            assert now < due <= now + timedelta(minutes=15)
            assert app._life_ecology.next_outlook_due() <= due
            if attempt == 1:
                unrelated_wake = await tick(app, now + timedelta(minutes=1),
                                            "retry-course-unrelated", ecology=False)
                await app.advance_life_ecology_once(wake_event_ref=unrelated_wake,
                    trace_id="retry-course-unrelated", correlation_id="retry-course-unrelated")
                assert len(author.received) == 1, "an unrelated clock must not spend the course retry early"
                wake = await tick(app, due, "retry-course-second", ecology=False)
        third_wake = await tick(app, due, "retry-course-third", ecology=False)
        # The valid replacement uses the third current Clock as its authored
        # proposal anchor while keeping the original class start and offset.
        author.outputs.append(course_detail_draft(app._ledger.lookup_event_commit(third_wake)[0],
            hidden="课程后段的新情况。", closing_offset=89 * 60))
        recovered = await app.advance_life_ecology_once(
            wake_event_ref=third_wake, trace_id="retry-course-third",
            correlation_id="retry-course-third")
        assert recovered.status not in {"failed_safe", "rejected"}, recovered
        assert any(o.event_detail_program is not None
                   and o.event_detail_program.capability.required_program
                   for o in app._ledger.project().world_occurrences)
        assert len(author.received) == 3
        receipt = runtime.store.operation(app._life_ecology._required_detail_retry_operation(source))
        assert receipt["state"] == "considered" and receipt["payload"]["failure_count"] == 2
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_late_new_course_cannot_start_without_time_for_required_program(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    app, model = _build(tmp_path / "late-course.sqlite", "world:late-course",
                        at(16, 10), _Role(), RollingAuthor())
    try:
        for minute in (1, 2, 3, 4):
            await tick(app, at(16, 10) + timedelta(minutes=minute), f"late-plan-{minute}")
        runtime = app._life_ecology._world_outlook
        wake = await tick(app, at(19, 15) + timedelta(minutes=29), "late-course", ecology=False)
        node = next(n for n in runtime.ready() if n["opportunity_id"].startswith("planning-"))
        result = runtime.execute_offer(node, wake_event_ref=wake)
        assert result.status == "rejected" and result.reason_code == "planning_detail_window_too_short"
        assert not runtime._executor.has_effect(node)
        assert not any(o.trigger_ref.startswith("event:world-outlook:execution:")
                       and o.time_window.closes_at == at(19, 15) + timedelta(minutes=30)
                       for o in app._ledger.project().world_occurrences)
    finally:
        await app.aclose()
        await model.aclose()


async def tick(app, now, key, *, ecology=True):
    before = await app.current_logical_time()
    await app.tick(tick_id=key, logical_time_from=before, logical_time_to=now, observed_at=now,
                   trace_id=key, causation_id="clock", correlation_id=key, reason="rolling-planning-test", run_life_ecology=False)
    if ecology:
        await app.advance_life_ecology_once(wake_event_ref="event:trigger:clock:" + key, trace_id=key, correlation_id=key)
        for _ in range(5):
            if await app.drain_background_once() is None:
                break
    return "event:trigger:clock:" + key


@pytest.mark.asyncio
async def test_published_next_month_phase_enters_chat_and_clock_executes_one_external_event(tmp_path, monkeypatch):
    from companion_daemon.world_v2.world_turn_runtime import InboundTurn
    from companion_daemon.world_v2.dashboard_home_snapshot import DashboardHomeSnapshotModule
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    now, author, role = at(16, 10), RollingAuthor(), _Role()
    app, model = _build(tmp_path / "rolling.sqlite", "world:rolling", now, role, author)
    try:
        for minute in (1, 2, 3, 4):
            await tick(app, now + timedelta(minutes=minute), f"publish-{minute}")
        runtime = app._life_ecology._world_outlook
        assert len(runtime._planning_publications(app._ledger.project())) == 1, runtime.health()
        assert runtime.scheduled() and all(n["state"] == "waiting" for n in runtime.scheduled())
        await app.respond(InboundTurn(platform="test", platform_user_id="user.1", platform_message_id="next-month",
            text="你下个月准备干啥呀？", observed_at=await app.current_logical_time(), trace_id="next-month"))
        request = next(r for r in reversed(role.materials) if not r.get("inner_turn", {}).get("purpose"))
        material = json.dumps(request["inner_life_snapshot"]["materials"], ensure_ascii=False)
        assert "学校运动会" in material and "活动周没有常规课程" in material
        assert FUTURE not in material
        module = DashboardHomeSnapshotModule(ledger=app._ledger, deployment_id="test", boot_id="test",
            life_actor_ref="actor:companion", life_privacy_ceiling="private", life_content_store=app._life_content_store,
            outlook_reader=runtime.store.head)
        snapshot = await module.capture(week=datetime.fromisoformat("2026-11-02").date())
        agenda = snapshot.sections.overview_life.data.agenda
        assert any(e.title == "学校运动会" for e in agenda.entries)
        assert not any(e.title == "文学课" for e in agenda.entries)
        before = len(app._ledger.project().experiences)
        wake = await tick(app, at(19, 14), "monday-class", ecology=False)
        node = next(n for n in runtime.ready() if n["opportunity_id"].startswith("planning-"))
        result = runtime.execute_offer(node, wake_event_ref=wake)
        assert result.status == "occurrence_committed"
        assert runtime.execute_offer(node, wake_event_ref=wake) is None
        occurrence = next(o for o in app._ledger.project().world_occurrences if o.occurrence_id == result.occurrence_id)
        assert occurrence.time_window.closes_at == at(19, 15) + timedelta(minutes=30)
        assert len(app._ledger.project().experiences) == before
        assert not app._ledger.project().plans, "external classes must not choose attendance"
        # The schedule-derived event uses the SAME detail authoring/review and timed release.
        from test_world_consequence_producer import _draft
        from test_world_author_request_audit import _ReceivedAuthor
        from test_day_open_world_consequence_material import _author_runtime
        from test_life_development_runtime import _SequenceModel, _novel_origin_review
        from companion_daemon.world_v2.life_content import LifeContentCompiler
        from companion_daemon.world_v2.schemas import ProjectionCursor
        from companion_daemon.world_v2.world_outlook_runtime import cursor_of
        event = app._ledger.lookup_event_commit(wake)[0]
        value = _draft(event)
        value.update(authored_subject_ref="actor:companion", location_ref=None, location_capability_ref=None, privacy_class="private")
        for outcome in value["outcomes"]:
            outcome.update(experienced_by_ref="actor:companion", privacy_class="private", visual_evidence=None)
        canary = "PRIVATE_CLASS_DETAIL：教室广播响起一段音乐。"
        value["event_details"] = [
            {"after_seconds": 0, "text": "教室开始开放。",
             "claim_refs": [value["claim_declarations"][0]["claim_id"]]},
            {"after_seconds": 120, "text": canary,
             "claim_refs": [value["claim_declarations"][0]["claim_id"]]},
            {"after_seconds": 89 * 60, "text": "本节课临近结束。",
             "claim_refs": [value["claim_declarations"][0]["claim_id"]]},
        ]
        detail_author = _ReceivedAuthor(app._life_content_store, [json.dumps(value, ensure_ascii=False)])
        detail_development = _author_runtime(app._ledger, app._life_content_store, detail_author, actor="actor:companion",
            critic=_SequenceModel(model="fixture:detail-review", outputs=(_novel_origin_review(decision="supported"),)))
        detail_development._world_outlook = runtime
        source = detail_development.pending_world_event_detail_ref()
        assert source == "event:activate:" + result.occurrence_id
        prepared = await detail_development.advance_world_event_detail_once(event_ref=source,
            wake_event_ref=wake, trace_id="class-details", correlation_id="class-details")
        assert prepared.status == "occurrence_committed", prepared
        assert runtime.event_details.advance(wake_event_ref=wake) == 1
        state = app._ledger.project()
        reading = LifeContentCompiler(store=app._life_content_store).compile(cursor=ProjectionCursor(**cursor_of(state)),
            actor_ref="actor:companion", viewer_privacy_ceiling="private", projection=state)
        assert canary not in reading.model_dump_json()
        due_wake = await tick(app, at(19, 14) + timedelta(minutes=2), "class-detail-due", ecology=False)
        assert runtime.event_details.advance(wake_event_ref=due_wake) == 1
        assert runtime.event_details.advance(wake_event_ref=due_wake) == 0
        assert len(detail_author.received) == 1
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_claimed_old_event_does_not_resume_after_same_time_reschedule(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    now, author = at(16, 10), RollingAuthor()
    app, model = _build(tmp_path / "interrupted.sqlite", "world:interrupted", now, _Role(), author)
    try:
        for minute in (1, 2, 3, 4):
            await tick(app, now + timedelta(minutes=minute), f"initial-{minute}")
        runtime = app._life_ecology._world_outlook
        wake = await tick(app, at(19, 13) + timedelta(minutes=59), "notice-start", ecology=False)
        old = next(n for n in runtime.scheduled() if datetime.fromisoformat(n["opens_at"]) == at(19, 14))
        from companion_daemon.world_v2.world_outlook import OutlookOpportunity
        from companion_daemon.world_v2.world_outlook_runtime import cursor_of
        from companion_daemon.world_v2.schemas import ProjectionCursor
        prior = runtime._planning_publications(app._ledger.project())[0]
        changed = semester(at(19, 14), supersedes_occurrence_ref=prior.occurrence_ref, exceptions=[{
            "slot_id": "class", "original_date": "2026-10-19", "action": "reschedule",
            "new_start": at(19, 16).isoformat(), "duration_minutes": 90, "explanation": "临时调课"}])
        publication = OutlookOpportunity.model_validate_json(json.dumps({
            **{k: v for k, v in old.items() if k != "state"}, "opportunity_id": "same-time-revision",
            "opens_at": (at(19, 13) + timedelta(minutes=59)).isoformat(),
            "execution": {"kind": "planning", "text": "新通知", "duration_minutes": 1,
                          "planning": changed.model_dump(mode="json")}}))
        prior_occurrence = next(o for o in app._ledger.project().world_occurrences if o.occurrence_id == prior.occurrence_ref)
        origin = app._ledger.lookup_event_commit(prior_occurrence.trigger_ref)[0].payload()["central_authorship"]
        result = runtime._executor.execute(node=publication.model_dump(mode="json"), wake_event_ref=wake,
            origin=origin, expected_cursor=ProjectionCursor(**cursor_of(app._ledger.project())))
        # At 14:00 the old event is claimed before another worker settles the 13:59 notice.
        wake = await tick(app, at(19, 14), "old-start", ecology=False)
        claimed = runtime.begin_offer(old, wake_event_ref=wake)
        assert claimed
        await app._life_ecology._aftermath_followup.advance_once(wake_event_ref=wake,
            trace_id="notice-settlement", correlation_id="notice-settlement")
        assert runtime._planning_publications(app._ledger.project())[0].occurrence_ref == result.occurrence_id
        runtime.reconcile_accepted_offers(wake_event_ref=wake)
        old_op = runtime.store.operation(claimed)
        assert old_op["state"] == "technical_failure"
        assert old_op["payload"]["result_reason"] == "outlook_interrupted_delivery_conditions_changed"
        from companion_daemon.world_v2.world_outlook_execution import occurrence_ref
        assert not any(o.occurrence_id == occurrence_ref(app._ledger.world_id, "actor:companion", old["opportunity_id"])
                       for o in app._ledger.project().world_occurrences)
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_published_cancellation_survives_restart_and_prior_clock_work_cannot_execute(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path, now, author = tmp_path / "cancel.sqlite", at(16, 10), RollingAuthor()
    author.review_days = 1
    app, model = _build(path, "world:cancel", now, _Role(), author)
    try:
        for minute in (1, 2, 3, 4):
            await tick(app, now + timedelta(minutes=minute), f"initial-{minute}")
        runtime = app._life_ecology._world_outlook
        old_node = next(n for n in runtime.scheduled() if datetime.fromisoformat(n["opens_at"]) == at(19, 14))
        author.cancel_monday = True
        for minute in (1, 2, 3, 4):
            await tick(app, at(17, 11) + timedelta(minutes=minute), f"revise-{minute}")
        assert all(n["opportunity_id"] != old_node["opportunity_id"] for n in runtime.scheduled())
        assert runtime._planning_publications(app._ledger.project())[0].planning.exceptions
    finally:
        await app.aclose()
        await model.aclose()
    app, model = _build(path, "world:cancel", at(17, 11) + timedelta(minutes=4), _Role(), author)
    try:
        wake = await tick(app, at(19, 14), "cancelled-class-time", ecology=False)
        runtime = app._life_ecology._world_outlook
        assert runtime.execute_offer(old_node, wake_event_ref=wake) is None
        assert not any(n["opportunity_id"] == old_node["opportunity_id"] for n in runtime.scheduled())
        assert not any(e.event_type == "ActivityStarted" for e in app._ledger.project().committed_world_event_refs)
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_detail_coverage_automatically_requests_renewal_and_timeout_is_retained(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    now, author, role = at(16, 10), RollingAuthor(), _Role()
    app, model = _build(tmp_path / "renewal.sqlite", "world:renewal", now, role, author)
    try:
        for minute in (1, 2, 3, 4):
            await tick(app, now + timedelta(minutes=minute), f"initial-{minute}")
        runtime = app._life_ecology._world_outlook
        before = runtime._planning_publications(app._ledger.project())[0]
        assert before.planning.detailed_through.isoformat() == "2026-10-23"
        # Real settlements can keep postponing the generic life cadence. That
        # cooldown must not postpone independently due planning maintenance.
        monkeypatch.setattr(app._life_ecology._trigger_store, "next_consideration_at", lambda: at(25))
        # The seven-day author review has NOT arrived. Detailed coverage itself wakes preparation.
        assert runtime.next_due() <= at(21)
        author.failures_remaining = 1
        await tick(app, at(21), "coverage-due", ecology=False)
        assert runtime.view()["rolling_preparation"]["due"][0]["planning_key"] == "school-year"
        previous_calls = len(author.calls)
        for minute in range(1, 6):
            await tick(app, at(21) + timedelta(minutes=minute), f"timeout-{minute}")
        assert len(author.calls) == previous_calls + 1
        assert runtime.next_due() == at(21, 1), "same-input failures wait for their existing retry epoch"
        assert any(p["rolling_preparation"]["due"] for p in author.calls[previous_calls:])
        # Existing per-hour retry identities preserve a failed operation; a new clock retries.
        for minute in range(1, 7):
            await tick(app, at(21, 2) + timedelta(minutes=minute), f"retry-{minute}")
        after = runtime._planning_publications(app._ledger.project())[0]
        assert after.planning.detailed_through > before.planning.detailed_through, runtime.health()
        assert after.planning.supersedes_occurrence_ref == before.occurrence_ref
        assert app._ledger.lookup_event_commit(before.source_event_ref) is not None
        records = runtime.store._db.execute("SELECT payload FROM world_outlook_attempts").fetchall()
        assert any("TimeoutError" in str(r) for r in records)
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_planning_bootstrap_and_publication_survive_unrelated_aftermath_failure(tmp_path, monkeypatch):
    from companion_daemon.world_v2.life_aftermath_runtime import LifeAftermathModelFailure
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    now, author = at(16, 10), RollingAuthor()
    app, model = _build(tmp_path / "independent.sqlite", "world:independent", now, _Role(), author)
    try:
        ecology = app._life_ecology
        runtime = ecology._world_outlook
        monkeypatch.setattr(ecology._trigger_store, "next_consideration_at", lambda: at(31))
        async def unavailable(**kwargs):
            raise LifeAftermathModelFailure("unrelated role outcome unavailable", failure_code="fixture_unavailable")
        monkeypatch.setattr(ecology._aftermath_followup, "advance_once", unavailable)
        monkeypatch.setattr(ecology, "_background_budget_paused", lambda: True)
        await tick(app, now + timedelta(minutes=1), "paused")
        assert not author.calls
        assert ecology.next_outlook_due() is None, "a paused model budget must not create a busy clock"
        monkeypatch.setattr(ecology, "_background_budget_paused", lambda: False)
        assert await app.life_ecology_next_due() <= now + timedelta(minutes=2)
        for minute in (2, 3, 4):
            await tick(app, now + timedelta(minutes=minute), f"independent-{minute}")
            if minute == 2:
                draft = runtime.store.head()[1]
                state = app._ledger.project()
                assert not runtime._preparation(state, draft)["due"]
                assert runtime._preparation(state, draft, {draft.opportunities[0].opportunity_id: "invalidated"})["due"], "an invalidated publication cannot suppress missing coverage"
                # The accepted document is prepaid. Pause new model spending;
                # its ordinary publication and settlement must still finish.
                monkeypatch.setattr(ecology, "_background_budget_paused", lambda: True)
        assert len(author.calls) == 1
        assert len(runtime._planning_publications(app._ledger.project())) == 1
        assert not app._ledger.project().plans, "maintenance cannot choose a role Plan"
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_waiting_replacement_does_not_reopen_the_old_preparation_deadline(tmp_path):
    now = at(21)
    app, model = _build(tmp_path / "waiting.sqlite", "world:waiting", now, _Role(), RollingAuthor())
    try:
        runtime = app._life_ecology._world_outlook
        # Preparation has already accepted a replacement awaiting publication;
        # the old published document still exists until that transition.
        view = {"needs_revision": False, "review_at": at(28).isoformat(),
                "rolling_preparation": {"due": [], "outline_due": False, "published": [
                    {"occurrence_ref": "old", "planning": semester(at(16)).model_dump(mode="json")} ]}}
        for _ in range(3):
            assert runtime.next_preparation_due(projection=app._ledger.project(), view=view) == at(28)
        assert runtime.store.head() is None, "deadline reads cannot install or claim authoring"
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_prepaid_publication_failure_is_durable_even_when_model_budget_paused(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path, now = tmp_path / "paused.sqlite", at(16, 10)
    app, model = _build(path, "world:paused", now, _Role(), RollingAuthor())
    try:
        ecology = app._life_ecology
        monkeypatch.setattr(ecology, "_background_budget_paused", lambda: True)
        async def broken_publication(**kwargs):
            raise ValueError("publication content unavailable")
        monkeypatch.setattr(ecology._aftermath_followup, "advance_planning_once", broken_publication)
        wake = await tick(app, now + timedelta(minutes=1), "paused-failure", ecology=False)
        result = await app.advance_life_ecology_once(wake_event_ref=wake, trace_id="paused-failure", correlation_id="paused-failure")
        assert result.status == "failed_safe" and result.technical_failure_code.startswith("prepaid.")
        with sqlite3.connect(path) as db:
            row = db.execute("SELECT state,outcome FROM world_v2_life_ecology_leases WHERE wake_event_ref=?", (wake,)).fetchone()
        assert row[0] == "completed" and result.technical_failure_code in row[1]
        assert not ecology._life_development_followup._world_outlook.author.calls
    finally:
        await app.aclose()
        await model.aclose()
