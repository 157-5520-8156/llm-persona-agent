from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.world_outlook import (
    WorldOutlookDraft,
    OutlookSource,
    opportunity_states,
)
from companion_daemon.world_v2.world_outlook_runtime import WorldOutlookRuntime
from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore
from test_life_development_runtime import (
    WORLD_ID,
    OWNER,
    NOW,
    _seed_clock,
    _manifest,
    _projection_cursor,
)


def draft_value(wake, *, parent=None):
    return {
        "contract": "world-outlook-draft.1",
        "world_id": WORLD_ID,
        "actor_ref": OWNER,
        "parent_revision": parent,
        "revise_reason": "从共同当前条件组织三个尚未发生的事项。",
        "review_at": (NOW + timedelta(days=1)).isoformat(),
        "horizon_end": (NOW + timedelta(days=21)).isoformat(),
        "threads": [
            {
                "thread_id": name,
                "situation": text,
                "open_question": "下一步由实际条件与参与者选择决定。",
            }
            for name, text in [
                ("books", "一场新旧书交换的外部筹备可能。"),
                ("study", "既有学习与时间安排。"),
                ("friends", "熟人各自的安排可能产生交集。"),
            ]
        ],
        "opportunities": [
            {
                "opportunity_id": name,
                "thread_refs": threads,
                "external_possibility": text,
                "execution": {
                    "kind": "environment",
                    "text": text,
                    "privacy_class": "private",
                    "duration_minutes": 30,
                },
                "lane": "world",
                "opens_at": NOW.isoformat(),
                "closes_at": (NOW + timedelta(days=7)).isoformat(),
                "basis_refs": [wake.event_id],
                "conditions": [],
                "after_opportunities": [],
            }
            for name, threads, text in [
                (
                    "notice",
                    ["books", "friends"],
                    "社区发布周六下午旧书交换的公告，摊位报名尚未截止。",
                ),
                ("calendar", ["study"], "图书馆通知：周六自习区检修，开放时间改为傍晚六点以后。"),
                (
                    "materials",
                    ["books", "study"],
                    "旧书交换主办方新增学习资料专区，接受未填写的练习册和参考书。",
                ),
            ]
        ],
    }


class Author:
    model = "fixture:outlook-world-author"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["_history_material", "story_history"])
async def test_fresh_outlook_request_preparation_yields_to_foreground(tmp_path, monkeypatch, phase):
    import asyncio
    from contextvars import ContextVar
    import threading

    ledger, wake, _, author, runtime = setup(tmp_path, lambda w: [
        {"decision": "revise", "draft": draft_value(w)}])
    monkeypatch.setattr(type(ledger), "blocks_event_loop", property(lambda self: True))
    started, finished = threading.Event(), threading.Event()
    authority = ContextVar("outlook_test_request_authority", default=None)
    authority.set(wake.event_id)
    original = getattr(runtime, phase)

    def delayed(*args, **kwargs):
        assert authority.get() == wake.event_id
        started.set()
        threading.Event().wait(.25)
        value = original(*args, **kwargs)
        finished.set()
        return value

    monkeypatch.setattr(runtime, phase, delayed)

    async def foreground():
        while not started.is_set():
            await asyncio.sleep(.001)
        return not finished.is_set()

    ping = asyncio.create_task(foreground())
    try:
        before = ledger.project()
        result = await runtime.refresh(projection=before, context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
        assert result["status"] == "ready" and len(author.calls) == 1
        assert ledger.project() == before
        assert await asyncio.wait_for(ping, 1), "Fresh Outlook preparation blocked the foreground loop"
    finally:
        if not ping.done():
            ping.cancel()
        await asyncio.gather(ping, return_exceptions=True)
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_count,fail_read", [(1, False), (2, False), (1, True)])
async def test_cancelled_outlook_preparation_joins_and_records_no_paid_call(tmp_path, monkeypatch, cancel_count, fail_read):
    import asyncio
    import threading

    ledger, wake, store, author, runtime = setup(tmp_path, lambda w: [])
    monkeypatch.setattr(type(ledger), "blocks_event_loop", property(lambda self: True))
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = runtime._history_material

    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(3)
        finished.set()
        if fail_read:
            raise ValueError("immutable history unavailable during cancellation")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "_history_material", delayed)
    before = ledger.project()
    job = asyncio.create_task(runtime.refresh(projection=before, context={},
        manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger))))
    try:
        async def wait_started():
            while not started.is_set():
                await asyncio.sleep(.001)

        await asyncio.wait_for(wait_started(), 1)
        for _ in range(cancel_count):
            job.cancel()
            await asyncio.sleep(0)
        assert not job.done(), "Store shutdown must wait until the source reader finishes"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert finished.is_set() and not author.calls and ledger.project() == before
        assert runtime.health()["failure_code"] == "outlook_preparation_cancelled"
        operations = store._db.execute("SELECT operation_id FROM world_outlook_operations").fetchall()
        assert len(operations) == 1
        operation_id = operations[0][0]
        saved = store.operation(operation_id)
        assert saved["state"] == "technical_failure"
        assert saved["payload"]["code"] == "outlook_preparation_cancelled"
        assert not store.attempts(operation_id)
    finally:
        release.set()
        await asyncio.gather(job, return_exceptions=True)
        runtime.close()


@pytest.mark.asyncio
async def test_fresh_outlook_pin_changed_during_preparation_does_not_buy_author(tmp_path, monkeypatch):
    import asyncio
    import threading

    ledger, wake, store, author, runtime = setup(tmp_path, lambda w: [
        {"decision": "revise", "draft": draft_value(w)}])
    monkeypatch.setattr(type(ledger), "blocks_event_loop", property(lambda self: True))
    original = runtime._history_material
    started, release = threading.Event(), threading.Event()

    def gated(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "_history_material", gated)
    state = ledger.project()
    task = asyncio.create_task(runtime.refresh(projection=state, context={},
        manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger))))
    try:
        async def wait_started():
            while not started.is_set():
                await asyncio.sleep(.001)

        await asyncio.wait_for(wait_started(), 1)
        _seed_clock(ledger, event_id="clock:foreground:while-preparing",
            logical_time=state.logical_time + timedelta(seconds=1), logical_time_from=state.logical_time)
        fresh = ledger.project()
        release.set()
        assert (await asyncio.wait_for(task, 3))["status"] == "stale"
        assert not author.calls
        assert store._db.execute("SELECT COUNT(*) FROM world_outlook_operations WHERE kind='author_call'").fetchone()[0] == 0
        assert ledger.project() == fresh and store.head() is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        runtime.close()


@pytest.mark.asyncio
async def test_unqualified_keep_reselects_with_the_same_author_before_origin_review(tmp_path):
    ledger, wake, store, author, runtime = setup(tmp_path, lambda w: [
        {"decision": "revise", "draft": draft_value(w)}])
    manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger))

    class Critic:
        model = "fixture:unoffered-keep-authority"
        calls = 0
        async def complete(self, messages, **kwargs):
            self.calls += 1
            return json.dumps({"review": {"decision": "supported", "reason": "External fixture only."}})

    critic = Critic()
    try:
        before = ledger.project()
        assert (await runtime.refresh(projection=before, context={}, manifest=manifest))["status"] == "ready"
        value = draft_value(wake)
        value["parent_revision"] = store.head()[0]
        author.outputs = [{"decision": "keep", "review_at": value["review_at"], "reason": "Use old future."},
            {"decision": "revise", "draft": value}]
        runtime._source_critic = critic
        assert (await runtime.refresh(projection=before, context={}, manifest=manifest))["status"] == "ready"
        assert len(author.calls) == 3 and critic.calls == 1
        assert "keep was not offered" in author.calls[2][-1]["content"]
        assert ledger.project() == before
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("prepare_details", [False, True])
async def test_private_script_is_source_checked_before_it_can_become_world_fact(tmp_path, prepare_details):
    bad = "值班人员把申请连同角色已经补交的活动方案摆上桌面。"
    good = "值班人员审阅原申请，活动方案尚待申请人自行补交。"

    class Critic:
        model = "fixture:existing-focused-origin-critic"
        def __init__(self):
            self.calls = []
        async def complete(self, messages, **kwargs):
            self.calls.append(messages)
            text = json.dumps(messages, ensure_ascii=False)
            unsupported = bad in text
            return json.dumps({"review": {"decision": "unsupported" if unsupported else "supported",
                "reason": "No accepted character execution proves a supplement." if unsupported else "Only external conditions.",
                "unsupported_outcome_prerequisites": [{"prose_path": "outcomes.0.world_consequence.environment_text",
                    "violation_kinds": ["imported_current_or_prior_prerequisite", "completed_character_experience"],
                    "exact_fragments": ["角色已经补交的活动方案"]}] if unsupported else []}})

    def outputs(wake):
        first = draft_value(wake)
        first["opportunities"] = first["opportunities"][:1]
        first["opportunities"][0]["execution"]["text"] = bad
        first["opportunities"][0]["execution"]["prepare_details"] = prepare_details
        second = json.loads(json.dumps(first))
        second["opportunities"][0]["execution"]["text"] = good
        return [{"decision": "revise", "draft": first}, {"decision": "revise", "draft": second}]

    ledger, wake, store, author, runtime = setup(tmp_path, outputs)
    critic = Critic()
    runtime._source_critic = critic
    before = ledger.project()
    try:
        result = await runtime.refresh(projection=before, context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
        assert result["status"] == "ready", result
        assert store.head()[1].opportunities[0].execution.text == good
        assert len(author.calls) == 2 and len(critic.calls) == 2
        assert ledger.project() == before
        assert any("completed_character_experience" in json.dumps(m) for m in author.calls[-1:])
        assert "draft.opportunities[0].execution.text" in author.calls[-1][-1]["content"]
        assert store.head()[2]["execution_source_review"]["contract"] == "world-outlook-origin-review.1"
        from companion_daemon.world_v2.world_outlook import digest
        assert store.head()[2]["execution_source_review"]["records"][0]["request_hash"] == digest({
            "messages": critic.calls[-1], "temperature": 0.0})
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_legacy_private_scripts_wait_for_review_and_paid_review_is_reused(tmp_path):
    ledger, wake, store, _, runtime = setup(tmp_path, lambda w: [
        {"decision": "revise", "draft": draft_value(w)}])
    state = ledger.project()
    manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger))

    class Critic:
        model = "fixture:origin-recovery"
        calls = 0
        async def complete(self, messages, **kwargs):
            self.calls += 1
            assert self.calls == 1, "Reopening the exact paid review must not buy another verdict"
            return json.dumps({"review": {"decision": "supported", "reason": "External scripts only."}})

    try:
        assert (await runtime.refresh(projection=state, context={}, manifest=manifest))["status"] == "ready"
        assert runtime.ready()
        runtime._source_critic = Critic()
        assert runtime.ready() == ()
        assert runtime.view()["needs_revision"]
        draft = store.head()[1]
        proof = await runtime._review_executable_sources(candidate=draft, context={},
            manifest=manifest, projection=state)
        assert await runtime._review_executable_sources(candidate=draft, context={},
            manifest=manifest, projection=state) == proof
        assert runtime._source_critic.calls == 1
        assert ledger.project() == state
        assert not store.head()[2].get("execution_source_review"), "A standalone verdict must not silently install authority"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_unknown_origin_review_never_rebills_or_publishes_the_script(tmp_path):
    ledger, wake, store, _, runtime = setup(tmp_path, lambda w: [])
    state = ledger.project()
    manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger))

    class TimedOut:
        model = "fixture:unknown-origin-review"
        calls = 0
        async def complete(self, messages, **kwargs):
            self.calls += 1
            raise TimeoutError("original provider outcome unknown")

    runtime._source_critic = TimedOut()
    draft = WorldOutlookDraft.model_validate_json(json.dumps(draft_value(wake)))
    try:
        with pytest.raises(TimeoutError, match="outcome unknown"):
            await runtime._review_executable_sources(candidate=draft, context={}, manifest=manifest, projection=state)
        with pytest.raises(RuntimeError, match="outcome_unknown"):
            await runtime._review_executable_sources(candidate=draft, context={}, manifest=manifest, projection=state)
        assert runtime._source_critic.calls == 1
        assert store.head() is None and ledger.project() == state
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("obsolete", ["expired", "replaced_now"])
async def test_origin_review_does_not_rebuy_obsolete_publication_slots(tmp_path, monkeypatch, obsolete):
    from test_world_planning import planning
    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    marker = "OBSOLETE-SLOT-NOT-A-FUTURE-EXECUTION"
    old_end = NOW + timedelta(days=-1 if obsolete == "expired" else 30)
    old = planning(contract="world-planning.2", starts_on=(NOW-timedelta(days=30)).date().isoformat(),
        ends_on=old_end.date().isoformat(),
        detailed_through=(old_end if obsolete == "expired" else NOW+timedelta(days=7)).date().isoformat(),
        review_at=(NOW+timedelta(days=-2 if obsolete == "expired" else 6)).isoformat(),
        slots=[{"slot_id": "old", "title": "旧课", "detail": "旧安排", "weekdays": [0],
        "start_minute": 840, "duration_minutes": 60, "event_text": marker}])
    publication = SimpleNamespace(occurrence_ref="occurrence:old-publication", planning=old)
    monkeypatch.setattr(runtime, "_planning_publications", lambda _: (publication,))
    runtime._rolling_planning = True
    value = draft_value(wake)
    if obsolete == "replaced_now":
        replacement = planning(contract="world-planning.2", supersedes_occurrence_ref=publication.occurrence_ref,
            starts_on=(NOW-timedelta(days=30)).date().isoformat(), ends_on=(NOW+timedelta(days=30)).date().isoformat(),
            detailed_through=(NOW+timedelta(days=7)).date().isoformat(), review_at=(NOW+timedelta(days=6)).isoformat(),
            slots=[{"slot_id": "old", "title": "新课", "detail": "已修订", "weekdays": [0],
                    "start_minute": 840, "duration_minutes": 60, "event_text": "修订后的外部课程开始。"}])
        value["opportunities"][0]["execution"] = {"kind": "planning", "text": "公布修订安排。",
            "duration_minutes": 1, "planning": replacement.model_dump(mode="json")}

    class Critic:
        model = "fixture:bounded-active-script-scope"
        async def complete(self, messages, **kwargs):
            assert marker not in json.dumps(json.loads(messages[1]["content"])["reviewed_surface"])
            return json.dumps({"review": {"decision": "supported", "reason": "Only prospective active scripts."}})

    runtime._source_critic = Critic()
    before = ledger.project()
    try:
        await runtime._review_executable_sources(candidate=WorldOutlookDraft.model_validate_json(json.dumps(value)),
            context={}, manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)), projection=before)
        assert ledger.project() == before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_world_author_and_critic_share_only_the_existing_scoped_identity_source(tmp_path):
    from companion_daemon.world_v2.companion_identity import (CompanionIdentityFrame,
        companion_identity_source_ref, companion_identity_source_material)
    frame = CompanionIdentityFrame(companion_name="知栀", counterpart_name="用户",
        stable_identity_facts=("她的父母经营一家书店。",),
        background="UNSCOPED_BACKGROUND", daily_life=("UNSCOPED_DAILY_EPISODE",),
        speech_examples=("UNSCOPED_EXAMPLE",))
    ledger, wake, store, author, old = setup(tmp_path, lambda w: [
        {"decision": "revise", "draft": draft_value(w)}])

    class Critic:
        model = "fixture:scoped-deployment-identity"
        async def complete(self, messages, **kwargs):
            packet = json.loads(messages[1]["content"])
            source = packet["pinned_authority"]["configured_companion_identity"]
            assert source["source_ref"] == companion_identity_source_ref(frame, scope="world_background")
            assert source["material"] == companion_identity_source_material(frame, scope="world_background")
            assert source["material"]["stable_identity_facts"] == ["她的父母经营一家书店。"]
            assert source["subject_actor_ref"] == OWNER
            assert "UNSCOPED_" not in json.dumps(source)
            assert packet["pinned_authority"]["execution_authority"]["execution_bindings"] == []
            assert packet["pinned_authority"]["temporal_publication"]["contract"] == "world-outlook-script-publication.2"
            return json.dumps({"review": {"decision": "supported", "reason": "Standing setting is source-bound; no episode or new character action authorized."}})

    runtime = WorldOutlookRuntime(ledger=ledger, store=store, author=author, actor_ref=OWNER,
        source_critic=Critic(), identity_frame=frame)
    before = ledger.project()
    try:
        assert (await runtime.refresh(projection=before, context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger))))["status"] == "ready"
        from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
        packet = decode_outlook_request(author.calls[0][1]["content"])
        assert packet["configured_companion_identity"]["source_ref"] == companion_identity_source_ref(frame, scope="world_background")
        assert packet["configured_companion_identity"]["material"] == companion_identity_source_material(frame, scope="world_background")
        assert store.head()[2]["configured_identity_source_ref"] == companion_identity_source_ref(frame, scope="world_background")
        assert packet["private_origin_review"]["prior_future_qualified"] is False
        assert packet["private_origin_review"]["keep_offered"] is False
        assert packet["private_origin_review"]["publication_contract"] == "world-outlook-script-publication.2"
        assert "Duration does not authorize completed facts" in author.calls[0][0]["content"]
        assert not runtime.view()["needs_revision"]
        from companion_daemon.world_v2.world_outlook_history import OutlookHistoryOffer
        assert runtime._keep_is_offered(packet=packet, projection=before,
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)), prior_head=store.head(),
            npc_refs=(), sources={}, history_offer=OutlookHistoryOffer(items=()))
        accepted = store.head()
        runtime.close()
        reopened = SQLiteWorldOutlookStore(path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref=OWNER)
        runtime = WorldOutlookRuntime(ledger=ledger, store=reopened, author=author, actor_ref=OWNER,
            source_critic=Critic(), identity_frame=frame.model_copy(update={
                "stable_identity_facts": ("她的父母原来经营一家书店，现在已经歇业。",)}))
        assert runtime.view()["needs_revision"]
        assert not runtime._keep_is_offered(packet=packet, projection=before,
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)), prior_head=reopened.head(),
            npc_refs=(), sources={}, history_offer=OutlookHistoryOffer(items=()))
        assert reopened.head() == accepted
        assert len(author.calls) == 1
        assert ledger.project() == before
    finally:
        old._executor = None
        runtime.close()


def setup(tmp_path, outputs):
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    store = SQLiteWorldOutlookStore(
        path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref=OWNER
    )
    author = Author(outputs(wake))
    runtime = WorldOutlookRuntime(ledger=ledger, store=store, author=author, actor_ref=OWNER)
    return ledger, wake, store, author, runtime


@pytest.mark.asyncio
async def test_private_three_thread_outlook_persists_without_world_facts_or_reauthoring(tmp_path):
    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )
    before = ledger.project()
    manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger))
    try:
        result = await runtime.refresh(projection=before, context={}, manifest=manifest)
        assert result["status"] == "ready"
        assert ledger.project() == before
        assert len(runtime.view()["threads"]) == 3
        assert len(runtime.ready()) == 3
        assert (await runtime.refresh(projection=before, context={}, manifest=manifest))[
            "status"
        ] == "unchanged"
        assert len(author.calls) == 1
        assert len(store.revisions()) == 1
    finally:
        runtime.close()
    reopened = SQLiteWorldOutlookStore(
        path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref=OWNER
    )
    try:
        assert reopened.head()[0] == result["revision"]
        foreign = SQLiteWorldOutlookStore(
            path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref="actor:other"
        )
        try:
            assert foreign.head() is None
        finally:
            foreign.close()
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_outlook_rejects_a_forged_hash_at_the_same_current_cursor(tmp_path):
    ledger, wake, store, _, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )
    try:
        forged = ledger.project().model_copy(update={"semantic_hash": "f" * 64})
        result = await runtime.refresh(projection=forged, context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
        assert result["status"] == "technical_failure"
        assert store.head() is None
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "state,expected",
    [
        ("planned", "waiting"),
        ("active", "waiting"),
        ("completed", "ready"),
        ("abandoned", "invalidated"),
    ],
)
def test_actual_choice_state_controls_only_dependent_branch(state, expected):
    wake = SimpleNamespace(event_id="event:clock")
    value = draft_value(wake)
    value["opportunities"][0]["conditions"] = [
        {
            "source_ref": "plan:chosen",
            "ready_statuses": ["completed"],
            "invalid_statuses": ["abandoned"],
        }
    ]
    draft = WorldOutlookDraft.model_validate_json(json.dumps(value))
    sources = {
        "plan:chosen": OutlookSource(
            source_ref="plan:chosen", source_kind="plan", status=state, token="a" * 64
        )
    }
    states = opportunity_states(draft, now=NOW, sources=sources, offered=set(), produced=set())
    assert states["notice"] == expected
    assert states["calendar"] == states["materials"] == "ready"


def test_schedule_or_source_change_invalidates_shared_opportunity_without_inventing_a_choice():
    value = draft_value(SimpleNamespace(event_id="clock"))
    value["opportunities"][2]["conditions"] = [
        {"source_ref": "plan:time", "unchanged_token": "a" * 64}
    ]
    draft = WorldOutlookDraft.model_validate_json(json.dumps(value))
    source = OutlookSource(
        source_ref="plan:time", source_kind="plan", status="planned", token="b" * 64
    )
    states = opportunity_states(
        draft, now=NOW, sources={"plan:time": source}, offered=set(), produced=set()
    )
    assert states == {"notice": "ready", "calendar": "ready", "materials": "invalidated"}


@pytest.mark.asyncio
async def test_failure_does_not_invent_choice_or_unlock_effect_dependency(tmp_path):
    def outputs(w):
        d = draft_value(w)
        d["opportunities"][2]["after_opportunities"] = ["notice"]
        return [{"decision": "revise", "draft": d}]

    ledger, wake, store, author, runtime = setup(tmp_path, outputs)
    try:
        await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        node = next(n for n in runtime.ready() if n["opportunity_id"] == "notice")
        op = runtime.begin_offer(node, wake_event_ref=wake.event_id)
        assert op is not None
        assert runtime.begin_offer(node, wake_event_ref=wake.event_id) is None
        runtime.finish_offer(
            op,
            node=node,
            result=SimpleNamespace(status="technical_failure", reason_code="executor.unavailable"),
            wake_event_ref=wake.event_id,
        )
        assert runtime.begin_offer(node, wake_event_ref="another-clock") is None
        view = runtime.view()
        assert (
            next(n for n in view["opportunities"] if n["opportunity_id"] == "notice")["state"]
            == "retry_wait"
        )
        assert (
            next(n for n in view["opportunities"] if n["opportunity_id"] == "materials")["state"]
            == "waiting"
        )
        assert not view["needs_revision"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_invalid_author_gets_one_correction_and_failure_is_not_a_character_noop(tmp_path):
    ledger, wake, store, author, runtime = setup(
        tmp_path,
        lambda w: [{"decision": "revise", "draft": {}}, {"decision": "revise", "draft": {}}],
    )
    try:
        before = ledger.project()
        result = await runtime.refresh(
            projection=before,
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        assert result["status"] == "technical_failure"
        assert len(author.calls) == 2
        assert store.head() is None and ledger.project() == before
        await runtime.refresh(
            projection=before,
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        assert len(author.calls) == 2
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_mixed_response_branches_get_precise_correction_without_accepting_extra_fields(tmp_path):
    ledger, wake, store, author, runtime = setup(tmp_path, lambda w: [
        {"decision": "revise", "draft": draft_value(w), "review_at": None, "reason": None},
        {"decision": "revise", "draft": draft_value(w)},
    ])
    before = ledger.project()
    try:
        result = await runtime.refresh(projection=before, context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
        assert result["status"] == "ready" and store.head() is not None
        assert len(author.calls) == 2 and ledger.project() == before
        correction = author.calls[1][-1]["content"]
        assert "revise requires exactly decision, draft" in correction
        assert "Do not copy keep fields onto revise" in correction
    finally:
        runtime.close()


def test_cycles_and_unknown_threads_are_rejected():
    value = draft_value(SimpleNamespace(event_id="clock"))
    value["opportunities"][0]["after_opportunities"] = ["materials"]
    value["opportunities"][2]["after_opportunities"] = ["notice"]
    with pytest.raises(ValueError, match="cycle"):
        WorldOutlookDraft.model_validate_json(json.dumps(value))


@pytest.mark.asyncio
async def test_external_author_drift_does_not_accept_future_draft(tmp_path):
    ledger, wake, store, author, runtime = setup(tmp_path, lambda w: [])

    class DriftingAuthor:
        model = "fixture:drifting-world"

        async def complete(self, messages, **kwargs):
            _seed_clock(
                ledger,
                event_id="clock:changed",
                logical_time=NOW + timedelta(minutes=1),
                logical_time_from=NOW,
            )
            return json.dumps({"decision": "revise", "draft": draft_value(wake)})

    runtime.author = DriftingAuthor()
    try:
        result = await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        assert result["status"] == "stale"
        assert store.head() is None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_revision_keeps_independent_old_node_and_historical_prefix_cannot_read_future_draft(
    tmp_path,
):
    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )
    try:
        original = ledger.project()
        await runtime.refresh(
            projection=original,
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        first = store.head()
        later = _seed_clock(
            ledger,
            event_id="clock:tomorrow",
            logical_time=NOW + timedelta(days=1),
            logical_time_from=NOW,
        )
        changed = first[1].model_dump(mode="json")
        changed.update(
            parent_revision=first[0],
            review_at=(NOW + timedelta(days=2)).isoformat(),
            revise_reason="另一天重新检查，保留未受影响的活动筹备。",
        )
        changed["opportunities"][1]["basis_refs"] = [later.event_id]
        changed["opportunities"][1]["external_possibility"] = "一个不同的外部时间安排，尚未发生。"
        author.outputs.append({"decision": "revise", "draft": changed})
        result = await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(later, pinned_cursor=_projection_cursor(ledger)),
        )
        assert result["status"] == "ready"
        assert len(store.revisions()) == 2
        assert store.head()[1].opportunities[0] == first[1].opportunities[0]
        assert runtime.view(projection=original)["revision"] == first[0]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_central_npc_statement_executes_without_any_npc_or_world_model(tmp_path):
    from test_npc_ecology import _runtime as npc_runtime, _actor, _cursor_for_test
    from companion_daemon.world_v2.occurrence_content_coordinator import (
        OccurrenceContentCoordinator,
    )
    from companion_daemon.world_v2.world_outlook_execution import WorldOutlookExecutor

    ledger, content, npc_model, world_model, npc = npc_runtime(
        _actor("no_op"), {"decision": "no_op"}
    )
    state = ledger.project()
    node_draft = draft_value(SimpleNamespace(event_id="clock-life"))
    node_draft.update(
        world_id=ledger.world_id,
        actor_ref="actor:companion",
        review_at=(state.logical_time + timedelta(days=1)).isoformat(),
        horizon_end=(state.logical_time + timedelta(days=21)).isoformat(),
    )
    for node in node_draft["opportunities"]:
        node.update(
            opens_at=state.logical_time.isoformat(),
            closes_at=(state.logical_time + timedelta(days=7)).isoformat(),
        )
    node_draft["opportunities"][0].update(
        lane="npc",
        npc_ref="npc:lin",
        execution={
            "kind": "npc_statement",
            "text": "周六我打算整理旧书，你有空的话可以来看看。",
            "privacy_class": "private",
            "duration_minutes": 30,
        },
    )
    planner = Author([{"decision": "revise", "draft": node_draft}])
    store = SQLiteWorldOutlookStore(
        path=tmp_path / "npc-outlook.sqlite", world_id=ledger.world_id, actor_ref="actor:companion"
    )
    runtime = WorldOutlookRuntime(
        ledger=ledger,
        store=store,
        author=planner,
        actor_ref="actor:companion",
        npc_refs_reader=lambda s: npc.snapshot(_cursor_for_test(ledger)).available_npc_refs,
        executor=WorldOutlookExecutor(
            ledger=ledger,
            occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=content),
            actor_ref="actor:companion",
        ),
    )
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentNpcCapability

    manifest = SimpleNamespace(
        npc_capabilities=(
            LifeDevelopmentNpcCapability(
                npc_ref="npc:lin",
                lifecycle_state="active",
                identity_content_ref="identity:lin",
                identity_summary="林，已有共同活动记录的熟人。",
                identity_payload_hash="a" * 64,
                authority_refs=("clock-life",),
            ),
        ),
        anchor_refs=("clock-life",),
    )
    try:
        assert (await runtime.refresh(projection=state, context={}, manifest=manifest))[
            "status"
        ] == "ready"
        node = next(n for n in runtime.ready() if n["opportunity_id"] == "notice")
        before = ledger.project()
        result = runtime.execute_offer(node, wake_event_ref="clock-life")
        assert result.status == "occurrence_committed"
        assert npc_model.calls == [] and world_model.calls == [] and len(planner.calls) == 1
        packet = json.loads(planner.calls[0][-1]["content"])
        assert packet["npc_capabilities"][0]["identity_summary"].startswith("林")
        assert ledger.project().npcs == before.npcs
        occurrence = next(
            o for o in ledger.project().world_occurrences if o.occurrence_id == result.occurrence_id
        )
        assert occurrence.status == "active"
        text = content.read_exact(content_ref=occurrence.candidate_outcomes[0].content_ref).text
        assert "speaker_report_only" in text and "周六" in text
        assert runtime.execute_offer(node, wake_event_ref="clock-life") is None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_life_ecology_dispatches_world_opportunity_through_existing_admission(tmp_path):
    from test_life_development_runtime import (
        _runtime,
        _SequenceModel,
        _location_bound_world_draft,
        _location_capability,
    )
    from test_life_ecology_runtime import _Media, _TriggerStore
    from companion_daemon.world_v2.life_ecology_runtime import (
        LifeEcologyRuntime,
        LifeEcologyAvailability,
    )

    ledger, wake, store, planner, outlook = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )
    try:
        await outlook.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        author = _SequenceModel(
            model="fixture:existing-world-author",
            outputs=(
                _location_bound_world_draft(
                    wake=wake,
                    capability=_location_capability(),
                    timing={"mode": "now", "duration_minutes": 30},
                    privacy_class="private",
                ),
            ),
        )
        character = _SequenceModel(model="fixture:character", outputs=())
        development, content = _runtime(
            ledger=ledger,
            wake=wake,
            world_author=author,
            character_interior=character,
            world_outlook=outlook,
        )
        from companion_daemon.world_v2.world_outlook_execution import WorldOutlookExecutor
        from companion_daemon.world_v2.occurrence_content_coordinator import (
            OccurrenceContentCoordinator,
        )

        outlook._executor = WorldOutlookExecutor(
            ledger=ledger,
            occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=content),
            actor_ref=OWNER,
        )
        ecology = LifeEcologyRuntime(
            ledger=ledger,
            trigger_store=_TriggerStore(),
            media_followup=_Media(),
            life_development_followup=development,
            availability=LifeEcologyAvailability(state="installed_and_active"),
            world_outlook=outlook,
        )
        result = await ecology.advance_once(
            wake_event_ref=wake.event_id, trace_id="outlook", correlation_id="outlook"
        )
        assert result.life_development_followup_status == "occurrence_committed"
        assert result.prepaid_outlook_followup_status == "occurrence_committed"
        occurrences = ledger.project().world_occurrences
        assert len(occurrences) == 2
        assert sum(o.occurrence_id.startswith("occurrence:world-outlook:") for o in occurrences) == 1
        assert character.calls == 0
        assert len(store.offers()) == 1 and store.offers()[0]["state"] == "considered"
        assert store.offers()[0]["payload"]["effect_refs"]
        assert author.calls == 1
    finally:
        outlook.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("preparation_failed", [False, True])
async def test_waiting_outlook_does_not_disable_independent_life_authoring(tmp_path, preparation_failed):
    from types import SimpleNamespace
    from test_life_ecology_runtime import _Media, _TriggerStore
    from companion_daemon.world_v2.life_ecology_runtime import (
        LifeEcologyRuntime, LifeEcologyAvailability,
    )

    def outputs(wake):
        draft = draft_value(wake)
        for node in draft["opportunities"]:
            node["opens_at"] = (wake.logical_time + timedelta(hours=1)).isoformat()
            node["closes_at"] = (wake.logical_time + timedelta(hours=2)).isoformat()
        return [{"decision": "revise", "draft": draft}]

    class Development:
        def __init__(self):
            self.calls = []

        async def prepare_outlook_once(self, **kwargs):
            return ({"status": "technical_failure", "failure_code": "retained_failure"}
                    if preparation_failed else {"status": "unchanged"})

        async def advance_once(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(status="no_op")

    ledger, wake, store, author, outlook = setup(tmp_path, outputs)
    development = Development()
    try:
        await outlook.refresh(projection=ledger.project(), context={},
                              manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
        assert not outlook.ready()
        ecology = LifeEcologyRuntime(
            ledger=ledger, trigger_store=_TriggerStore(), media_followup=_Media(),
            life_development_followup=development,
            availability=LifeEcologyAvailability(state="installed_and_active"),
            world_outlook=outlook,
        )
        result = await ecology.advance_once(wake_event_ref=wake.event_id,
                                            trace_id="waiting", correlation_id="waiting")
        assert len(development.calls) == 1
        assert result.life_development_followup_status == "no_op"
        if preparation_failed:
            assert result.technical_failure_code == "outlook.retained_failure"
        assert not store.offers() and len(author.calls) == 1
        assert not ledger.project().world_occurrences
    finally:
        outlook.close()


@pytest.mark.asyncio
async def test_independent_life_author_receives_waiting_outlook_and_keeps_role_silence(tmp_path):
    from test_life_development_runtime import _runtime, _SequenceModel
    from test_life_ecology_runtime import _Media, _TriggerStore
    from companion_daemon.world_v2.life_ecology_runtime import LifeEcologyRuntime, LifeEcologyAvailability

    def outputs(wake):
        draft = draft_value(wake)
        for node in draft["opportunities"]:
            node["opens_at"] = (wake.logical_time + timedelta(hours=1)).isoformat()
            node["closes_at"] = (wake.logical_time + timedelta(hours=2)).isoformat()
        return [{"decision": "revise", "draft": draft}]

    ledger, wake, store, planner, outlook = setup(tmp_path, outputs)
    author = _SequenceModel(model="fixture:world-author", outputs=(json.dumps({"decision": "no_op"}),))
    character = _SequenceModel(model="fixture:character", outputs=())
    development, _ = _runtime(ledger=ledger, wake=wake, world_author=author,
                              character_interior=character, world_outlook=outlook)
    try:
        await outlook.refresh(projection=ledger.project(), context={},
                              manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
        ecology = LifeEcologyRuntime(ledger=ledger, trigger_store=_TriggerStore(), media_followup=_Media(),
            life_development_followup=development, world_outlook=outlook,
            availability=LifeEcologyAvailability(state="installed_and_active"))
        result = await ecology.advance_once(wake_event_ref=wake.event_id,
                                            trace_id="independent", correlation_id="independent")
        assert result.life_development_followup_status == "no_op" and author.calls == 1
        request = json.loads(author.messages[0][1]["content"])
        assert request["private_world_outlook"]["revision"] == outlook.view()["revision"]
        assert character.calls == 0 and not ledger.project().world_occurrences
        assert not store.offers()  # The future script remains pending, never skipped or executed early.
        later = _seed_clock(ledger, event_id="clock:independent-cooldown",
                            logical_time=wake.logical_time + timedelta(seconds=1),
                            logical_time_from=wake.logical_time)
        await ecology.advance_once(wake_event_ref=later.event_id,
                                   trace_id="cooldown", correlation_id="cooldown")
        assert author.calls == 1 and not store.offers()
    finally:
        outlook.close()


def install_executor(ledger, runtime):
    from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore
    from companion_daemon.world_v2.occurrence_content_coordinator import (
        OccurrenceContentCoordinator,
    )
    from companion_daemon.world_v2.world_outlook_execution import WorldOutlookExecutor

    content = InMemoryImmutableLifeContentStore()
    coordinator = OccurrenceContentCoordinator(ledger=ledger, store=content)
    runtime._executor = WorldOutlookExecutor(
        ledger=ledger, occurrence_content=coordinator, actor_ref=OWNER
    )
    return coordinator


@pytest.mark.asyncio
async def test_one_author_batch_executes_three_interwoven_events_without_replanning(tmp_path):
    def outputs(w):
        draft = draft_value(w)
        draft["opportunities"][2]["after_opportunities"] = ["notice"]
        return [{"decision": "revise", "draft": draft}]

    ledger, wake, store, author, runtime = setup(tmp_path, outputs)
    install_executor(ledger, runtime)
    try:
        await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        for key in ("notice", "calendar", "materials"):
            node = next(n for n in runtime.ready() if n["opportunity_id"] == key)
            assert (
                runtime.execute_offer(node, wake_event_ref=wake.event_id).status
                == "occurrence_committed"
            )
            assert not runtime.view()["needs_revision"]
            assert (
                await runtime.refresh(
                    projection=ledger.project(),
                    context={},
                    manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
                )
            )["status"] == "unchanged"
        assert len(author.calls) == 1
        assert len(ledger.project().world_occurrences) == 3
        assert len(store.offers()) == 3
        assert runtime.ready() == ()
        assert not any(
            e.event.event_type == "ModelResultCaptured"
            for e in ledger.export_replay_evidence().events
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_at", ["proposal", "occurrence", "activation"])
async def test_interrupted_script_delivery_recovers_once_without_model_calls(
    tmp_path, monkeypatch, crash_at
):
    class Crash(BaseException):
        pass

    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )
    coordinator = install_executor(ledger, runtime)
    try:
        await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        node = runtime.ready()[0]
        original_commit = ledger.commit_at_cursor
        original_content = coordinator.commit
        original_finish = runtime.finish_offer

        def content_crash(request):
            raise Crash()

        def activation_crash(events, **kwargs):
            if events[0].event_type == "WorldOccurrenceActivated":
                raise Crash()
            return original_commit(events, **kwargs)

        def receipt_crash(*args, **kwargs):
            raise Crash()

        if crash_at == "proposal":
            monkeypatch.setattr(coordinator, "commit", content_crash)
        elif crash_at == "occurrence":
            monkeypatch.setattr(ledger, "commit_at_cursor", activation_crash)
        else:
            monkeypatch.setattr(runtime, "finish_offer", receipt_crash)
        with pytest.raises(Crash):
            runtime.execute_offer(node, wake_event_ref=wake.event_id)
        monkeypatch.setattr(coordinator, "commit", original_content)
        monkeypatch.setattr(ledger, "commit_at_cursor", original_commit)
        monkeypatch.setattr(runtime, "finish_offer", original_finish)
        store.close()
        runtime.store = SQLiteWorldOutlookStore(
            path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref=OWNER
        )
        assert runtime.reconcile_accepted_offers(wake_event_ref=wake.event_id) == 1
        assert runtime.reconcile_accepted_offers(wake_event_ref=wake.event_id) == 0
        assert runtime.execute_offer(node, wake_event_ref=wake.event_id) is None
        assert len(ledger.project().world_occurrences) == 1
        assert ledger.project().world_occurrences[0].status == "active"
        assert len(author.calls) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_withdrawn_execution_does_not_unlock_dependent_story(tmp_path, monkeypatch):
    from companion_daemon.world_v2.schemas import EvidenceRef, WorldEvent
    from companion_daemon.world_v2.life_events import WorldOccurrenceTerminalPayload
    from companion_daemon.world_v2.event_identity import domain_idempotency_key

    def outputs(w):
        draft = draft_value(w)
        draft["opportunities"][2]["after_opportunities"] = ["notice"]
        return [{"decision": "revise", "draft": draft}]

    ledger, wake, store, author, runtime = setup(tmp_path, outputs)
    install_executor(ledger, runtime)
    try:
        await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        node = next(n for n in runtime.ready() if n["opportunity_id"] == "notice")
        original_commit = ledger.commit_at_cursor

        def fail_activation(events, **kwargs):
            if events[0].event_type == "WorldOccurrenceActivated":
                raise RuntimeError("activation unavailable")
            return original_commit(events, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(ledger, "commit_at_cursor", fail_activation)
            assert (
                runtime.execute_offer(node, wake_event_ref=wake.event_id).status
                == "technical_failure"
            )
        occurrence = ledger.project().world_occurrences[0]
        payload = WorldOccurrenceTerminalPayload(
            change_id="cancel-script",
            transition_id="cancel-script",
            expected_entity_revision=occurrence.entity_revision,
            occurrence_id=occurrence.occurrence_id,
            effective_at=NOW,
            reason_ref=wake.event_id,
            evidence_refs=(
                EvidenceRef(
                    ref_id=wake.event_id,
                    evidence_type="committed_world_event",
                    claim_purpose="life_transition",
                    source_world_revision=1,
                    immutable_hash=wake.payload_hash,
                ),
            ),
        ).model_dump(mode="json")
        event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id="cancel-script",
            world_id=WORLD_ID,
            event_type="WorldOccurrenceCancelled",
            logical_time=NOW,
            created_at=NOW,
            actor="worker:test",
            source="test",
            trace_id="test",
            causation_id=wake.event_id,
            correlation_id="test",
            idempotency_key=domain_idempotency_key(
                event_type="WorldOccurrenceCancelled", world_id=WORLD_ID, payload=payload
            ),
            payload=payload,
        )
        ledger.commit_at_cursor((event,), expected_cursor=_projection_cursor(ledger))
        states = {n["opportunity_id"]: n["state"] for n in runtime.view()["opportunities"]}
        assert states == {"notice": "invalidated", "calendar": "ready", "materials": "invalidated"}
        assert runtime.view()["needs_revision"]
        assert len(author.calls) == 1
    finally:
        runtime.close()


class ProductionAuthor:
    model = "fixture:central-world-author"

    def __init__(self):
        self.calls = []
        self.origin_reviews = []
        self.life_calls = []

    async def complete(self, messages, **kwargs):
        from datetime import datetime

        from companion_daemon.world_v2.world_outlook_request import decode_outlook_request

        try:
            life_packet = json.loads(messages[1]["content"])
        except json.JSONDecodeError:
            life_packet = {}
        if "capability_manifest" in life_packet:
            # This fixture isolates prepaid script execution. Independent Life
            # authoring still receives its real request and explicitly no-ops.
            self.life_calls.append(life_packet)
            return json.dumps({"decision": "no_op"})
        packet = decode_outlook_request(messages[-1]["content"])
        if packet.get("evidence_packet_binding", {}).get("contract") == "world-outlook-execution-source-packet.1":
            self.origin_reviews.append(packet)
            return json.dumps({"review": {"decision": "supported", "reason": "Explicit fixture scripts announce external notices only."}})
        self.calls.append(packet)
        assert packet["contract"] == "world-outlook-request.1"
        now = datetime.fromisoformat(packet["logical_time"])
        draft = draft_value(SimpleNamespace(event_id=packet["basis_refs"][0]))
        draft.update(
            world_id=packet["world_id"],
            actor_ref=packet["actor_ref"],
            review_at=(now + timedelta(days=1)).isoformat(),
            horizon_end=(now + timedelta(days=21)).isoformat(),
        )
        for n in draft["opportunities"]:
            n.update(
                opens_at=(now + timedelta(minutes=5)).isoformat(),
                closes_at=(now + timedelta(days=7)).isoformat(),
            )
        draft["threads"][0]["open_question"] = "PRIVATE_FUTURE_SENTINEL"
        if "rolling_preparation" in packet:
            draft["long_term"] = [{"stage_id": "ordinary-life", "starts_on": now.date().isoformat(),
                "ends_on": (now + timedelta(days=365)).date().isoformat(), "situation": "PRIVATE_FUTURE_SENTINEL"}]
        from test_story_outlook_replanning import apply_story_contract
        return json.dumps({"decision": "revise", "draft": apply_story_contract(draft, packet)})


def production_application(tmp_path, author):
    from test_life_development_production import (
        _open_life_seed,
        _Identities,
        _Router,
        _MainModel,
        _Transport,
        _NoOpDayOpenRole,
        _NeverCharacterModel,
        NOW as PRODUCTION_NOW,
    )
    from world_v2_application import (
        build_sqlite_world_v2_test_application,
        compose_fixture_character_interior,
        compose_fixture_character_purpose,
    )
    from companion_daemon.world_v2.production_turn_application import (
        WorldV2TurnApplicationConfig,
        LifeEcologyComposition,
    )

    npc = _NeverCharacterModel()
    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "production.sqlite",
        config=WorldV2TurnApplicationConfig(
            world_id="world:outlook-production",
            companion_actor_ref=OWNER,
            reply_target="user:user.1",
            action_pump_owner="pump:outlook",
            character_memory_enabled=False,
            world_outlook_enabled=True,
            npc_ecology_enabled=True,
            life_ecology=LifeEcologyComposition.production_v1(
                seed_catalog_path=_open_life_seed(tmp_path / "seed.yaml")
            ),
        ),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_MainModel(),
            purpose_faculties=(
                compose_fixture_character_purpose(
                    purpose="activity_lifecycle_choice", provider=_NoOpDayOpenRole()
                ),
            ),
        ),
        transport=_Transport(),
        life_world_author_model=author,
        npc_actor_model=npc,
        now=PRODUCTION_NOW,
    )
    return app, npc, PRODUCTION_NOW


async def production_tick(app, previous, current, key):
    await app.tick(
        tick_id=key,
        logical_time_from=previous,
        logical_time_to=current,
        observed_at=current,
        trace_id=key,
        causation_id="scheduler:test",
        correlation_id=key,
        reason="outlook-test",
        run_life_ecology=False,
    )
    return await app.advance_life_ecology_once(
        wake_event_ref="event:trigger:clock:" + key, trace_id=key, correlation_id=key
    )


@pytest.mark.asyncio
async def test_opt_in_production_composition_executes_frozen_batch_and_cold_reopens(tmp_path):
    author = ProductionAuthor()
    app, npc, now = production_application(tmp_path, author)
    try:
        await production_tick(app, now, now + timedelta(minutes=10), "first")
        assert app.world_outlook_health()["status"] == "ready"
        assert len(author.calls) == 1 and npc.calls == 0
        assert len(author.life_calls) == 1
        assert len(author.origin_reviews) == 1
        before = app.export_replay_evidence()
        assert all(
            "PRIVATE_FUTURE_SENTINEL" not in e.event.model_dump_json() for e in before.events
        )
        for minute in (15, 16, 17):
            result = await production_tick(
                app,
                now + timedelta(minutes=10 if minute == 15 else minute - 1),
                now + timedelta(minutes=minute),
                "step" + str(minute),
            )
            assert result.prepaid_outlook_followup_status == "occurrence_committed"
            assert result.life_development_followup_status is None
        assert len(author.calls) == 1 and npc.calls == 0
        assert len(author.life_calls) == 1
        assert len(author.origin_reviews) == 1
        evidence = app.export_replay_evidence()
        assert sum(e.event.event_type == "WorldOccurrenceCommitted" for e in evidence.events) == 3
    finally:
        await app.aclose()
    reopened, npc, _ = production_application(tmp_path, author)
    try:
        repeat = await reopened.advance_life_ecology_once(
            wake_event_ref="event:trigger:clock:step17", trace_id="reopen", correlation_id="reopen"
        )
        assert repeat.status == "joined_existing"
        assert len(author.calls) == 1 and npc.calls == 0
        assert len(author.life_calls) == 1
        assert len(reopened.export_replay_evidence().events) == len(evidence.events)
    finally:
        await reopened.aclose()


@pytest.mark.asyncio
async def test_async_shutdown_waits_for_central_author_and_its_ecology_continuation(tmp_path):
    import asyncio

    class SlowAuthor(ProductionAuthor):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def complete(self, messages, **kwargs):
            self.started.set()
            await self.release.wait()
            return await super().complete(messages, **kwargs)

    author = SlowAuthor()
    app, _, now = production_application(tmp_path, author)
    task = asyncio.create_task(production_tick(app, now, now + timedelta(minutes=10), "shutdown"))
    await asyncio.wait_for(author.started.wait(), 5)
    closing = asyncio.create_task(app.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    with pytest.raises(RuntimeError, match="await application.aclose"):
        app.close()
    author.release.set()
    await asyncio.wait_for(task, 5)
    await asyncio.wait_for(closing, 5)
    assert len(author.calls) == 1


@pytest.mark.asyncio
async def test_provider_budget_failure_is_recorded_once_without_fake_execution(tmp_path):
    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [RuntimeError("budget_denied")]
    )
    try:
        before = ledger.project()
        for _ in range(2):
            result = await runtime.refresh(
                projection=before,
                context={},
                manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
            )
            assert result["status"] == "technical_failure"
        assert len(author.calls) == 1
        assert ledger.project() == before
        assert store.offers() == () and store.head() is None
    finally:
        runtime.close()


def test_private_npc_script_cannot_be_authored_with_weaker_visibility():
    from test_npc_ecology import _runtime as npc_runtime, _actor
    from companion_daemon.world_v2.world_outlook import source_catalog, validate_draft

    ledger, _, _, _, _ = npc_runtime(_actor("no_op"), {"decision": "no_op"})
    state = ledger.project()
    value = draft_value(SimpleNamespace(event_id="clock-life"))
    value.update(
        world_id=ledger.world_id,
        review_at=(state.logical_time + timedelta(days=1)).isoformat(),
        horizon_end=(state.logical_time + timedelta(days=21)).isoformat(),
    )
    value["opportunities"] = [value["opportunities"][0]]
    node = value["opportunities"][0]
    node.update(
        lane="npc",
        npc_ref="npc:lin",
        opens_at=state.logical_time.isoformat(),
        closes_at=(state.logical_time + timedelta(days=7)).isoformat(),
    )
    node["execution"].update(kind="npc_statement", privacy_class="public")
    draft = WorldOutlookDraft.model_validate_json(json.dumps(value))
    with pytest.raises(ValueError, match="weakens source privacy"):
        validate_draft(
            draft,
            projection=state,
            actor_ref=OWNER,
            parent_revision=None,
            basis_refs=("clock-life",),
            npc_refs=("npc:lin",),
            sources=source_catalog(state, actor_ref=OWNER, npc_refs=("npc:lin",)),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_constraints", [None, ("Changed configuration after the paid request.",), ()])
async def test_saved_author_result_resumes_install_after_crash_without_rebilling(
    tmp_path, monkeypatch, replacement_constraints
):
    class Crash(BaseException):
        pass

    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )
    if replacement_constraints is not None:
        runtime = WorldOutlookRuntime(ledger=ledger, store=store, author=author, actor_ref=OWNER,
                                      authoring_constraints=("Original configured background.",))
    original = store.install

    def crash_install(*args, **kwargs):
        raise Crash()

    try:
        monkeypatch.setattr(store, "install", crash_install)
        with pytest.raises(Crash):
            await runtime.refresh(
                projection=ledger.project(),
                context={},
                manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
            )
        monkeypatch.setattr(store, "install", original)
        store.close()
        runtime.store = SQLiteWorldOutlookStore(
            path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref=OWNER
        )
        if replacement_constraints is not None:
            runtime = WorldOutlookRuntime(ledger=ledger, store=runtime.store, author=author, actor_ref=OWNER,
                                          authoring_constraints=replacement_constraints)
        result = await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        assert result["status"] == "ready"
        assert len(author.calls) == 1
        assert runtime.store.head() is not None
        if replacement_constraints is not None:
            from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
            original_packet = decode_outlook_request(author.calls[0][1]["content"])
            assert runtime.store.head()[2]["authoring_constraints_hash"] == original_packet["authoring_constraints"]["digest"]
            assert runtime.view()["needs_revision"] is True
            author.outputs.append({"decision": "keep", "review_at": (NOW + timedelta(days=2)).isoformat(),
                                   "reason": "Keep the unchanged opportunities under the new configuration."})
            updated = await runtime.refresh(projection=ledger.project(), context={},
                                            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
            assert updated["status"] == "ready" and len(author.calls) == 2
            new_packet = decode_outlook_request(author.calls[1][1]["content"])
            assert new_packet.get("authoring_constraints", {}).get("digest") != original_packet["authoring_constraints"]["digest"]
            assert runtime.store.head()[2].get("authoring_constraints_hash") == new_packet.get("authoring_constraints", {}).get("digest")
        assert len(ledger.project().world_occurrences) == 0
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_ecology_cannot_activate_expired_script_after_interrupted_delivery(
    tmp_path, monkeypatch
):
    from test_life_ecology_runtime import _Media, _TriggerStore
    from companion_daemon.world_v2.life_ecology_runtime import (
        LifeEcologyRuntime,
        LifeEcologyAvailability,
    )

    class Crash(BaseException):
        pass

    def outputs(w):
        draft = draft_value(w)
        draft["opportunities"][0]["closes_at"] = (NOW + timedelta(minutes=5)).isoformat()
        return [{"decision": "revise", "draft": draft}]

    ledger, wake, store, author, runtime = setup(tmp_path, outputs)
    install_executor(ledger, runtime)
    original = ledger.commit_at_cursor

    def crash_activation(events, **kwargs):
        if events[0].event_type == "WorldOccurrenceActivated":
            raise Crash()
        return original(events, **kwargs)

    try:
        await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        node = runtime.ready()[0]
        monkeypatch.setattr(ledger, "commit_at_cursor", crash_activation)
        with pytest.raises(Crash):
            runtime.execute_offer(node, wake_event_ref=wake.event_id)
        monkeypatch.setattr(ledger, "commit_at_cursor", original)
        later = _seed_clock(
            ledger,
            event_id="clock:expired",
            logical_time=NOW + timedelta(minutes=10),
            logical_time_from=NOW,
        )
        ecology = LifeEcologyRuntime(
            ledger=ledger,
            trigger_store=_TriggerStore(),
            media_followup=_Media(),
            availability=LifeEcologyAvailability(state="installed_and_active"),
            world_outlook=runtime,
        )
        await ecology.advance_once(
            wake_event_ref=later.event_id, trace_id="expired", correlation_id="expired"
        )
        assert all(o.status != "active" for o in ledger.project().world_occurrences)
        assert not store.offers()[0]["payload"]["effect_refs"]
        assert (
            next(
                n
                for n in runtime.view()["opportunities"]
                if n["opportunity_id"] == node["opportunity_id"]
            )["state"]
            == "expired"
        )
        assert len(author.calls) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_unknown_correction_outcome_is_not_sent_again_after_restart(tmp_path):
    class Crash(BaseException):
        pass

    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": {}}]
    )
    original = author.complete

    async def crash_on_correction(messages, **kwargs):
        if len(author.calls) == 1:
            author.calls.append(messages)
            raise Crash()
        return await original(messages, **kwargs)

    author.complete = crash_on_correction
    try:
        with pytest.raises(Crash):
            await runtime.refresh(
                projection=ledger.project(),
                context={},
                manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
            )
        store.close()
        runtime.store = SQLiteWorldOutlookStore(
            path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref=OWNER
        )
        result = await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        assert result["failure_code"] == "outlook_author_outcome_unknown"
        assert len(author.calls) == 2 and runtime.store.head() is None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_recovered_author_response_keeps_original_model_identity(tmp_path, monkeypatch):
    class Crash(BaseException):
        pass

    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )

    def crash_install(*args, **kwargs):
        raise Crash()

    original = store.install
    try:
        monkeypatch.setattr(store, "install", crash_install)
        with pytest.raises(Crash):
            await runtime.refresh(
                projection=ledger.project(),
                context={},
                manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
            )
        monkeypatch.setattr(store, "install", original)
        replacement = Author([])
        replacement.model = "fixture:new-author-never-called"
        runtime.author = replacement
        result = await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        assert result["status"] == "ready"
        assert store.head()[2]["model"] == author.model
        assert len(author.calls) == 1 and replacement.calls == []
    finally:
        runtime.close()



def _planning_draft(wake, supersedes=None):
    draft = draft_value(wake)
    node = draft['opportunities'][0]
    node['execution'] = {'kind':'planning', 'text':'学校发布课程安排。', 'privacy_class':'private', 'duration_minutes':1,
        'planning': {'contract':'world-planning.1','planning_key':'autumn-courses','title':'秋季学期课程',
            'summary':'学校安排的课程，不代表角色出席。','starts_on':NOW.date().isoformat(),
            'ends_on':(NOW+timedelta(days=100)).date().isoformat(),'timezone_name':'Asia/Shanghai',
            'slots':[{'slot_id':'literature','title':'文学课','detail':'学校安排的文学课。','weekdays':[0,2],
                'start_minute':840,'duration_minutes':90}], 'exceptions':[], 'supersedes_occurrence_ref':supersedes}}
    draft['opportunities']=[node]
    return draft

@pytest.mark.asyncio
async def test_planning_invalid_predecessor_is_corrected_before_install(tmp_path):
    ledger,wake,store,author,runtime = setup(tmp_path, lambda w:[
        {'decision':'revise','draft':_planning_draft(w,'occurrence:old-ordinary-notice')},
        {'decision':'revise','draft':_planning_draft(w)}])
    before=ledger.project()
    try:
        result=await runtime.refresh(projection=before,context={},manifest=_manifest(wake,pinned_cursor=_projection_cursor(ledger)))
        assert result['status']=='ready'
        assert len(author.calls)==2
        assert 'supersede its exact current publication' in author.calls[1][-1]['content']
        assert store.head()[1].opportunities[0].execution.planning.supersedes_occurrence_ref is None
        assert ledger.project()==before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_deployment_background_constrains_authoring_without_becoming_fact_authority(tmp_path):
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request

    ledger, wake, store, author, _ = setup(tmp_path, lambda w: [{'decision':'revise','draft':draft_value(w)}])
    constraints = ('Configured character studies Chinese language and literature.',)
    runtime = WorldOutlookRuntime(ledger=ledger,store=store,author=author,actor_ref=OWNER,
                                  authoring_constraints=constraints)
    try:
        before = ledger.project()
        result = await runtime.refresh(projection=before,context={},manifest=_manifest(wake,pinned_cursor=_projection_cursor(ledger)))
        assert result['status']=='ready'
        packet = decode_outlook_request(author.calls[0][1]['content'])
        assert packet['authoring_constraints']['stable_identity_facts'] == list(constraints)
        assert packet['authoring_constraints']['authority']=='generation_constraints_only'
        assert packet['basis_refs']==[wake.event_id]
        assert not packet['condition_sources'] and not packet['history_offer']['items']
        assert ledger.project()==before
        assert store.head()[2]['authoring_constraints_hash'] == packet['authoring_constraints']['digest']
        from companion_daemon.world_v2.world_outlook import validate_draft
        invalid = draft_value(wake)
        invalid['opportunities'][0]['basis_refs'] = [constraints[0]]
        with pytest.raises(ValueError, match='lacks offered basis'):
            validate_draft(WorldOutlookDraft.model_validate_json(json.dumps(invalid)), projection=before,
                           actor_ref=OWNER, parent_revision=None, basis_refs=(wake.event_id,), sources={}, npc_refs=())
    finally:
        runtime.close()


@pytest.mark.parametrize('constraints', [('x',) * 17, ('字' * 1400,)])
def test_authoring_constraints_are_bounded_configuration(tmp_path, constraints):
    ledger, _, store, author, runtime = setup(tmp_path, lambda w: [])
    try:
        with pytest.raises(ValueError, match='4096 bytes'):
            WorldOutlookRuntime(ledger=ledger,store=store,author=author,actor_ref=OWNER,authoring_constraints=constraints)
        assert not author.calls
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_cold_due_peek_defers_full_source_validation_to_background(tmp_path, monkeypatch):
    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )
    monkeypatch.setattr(ledger, "verified_history_generation", lambda: 0, raising=False)
    try:
        state = ledger.project()
        await runtime.refresh(
            projection=state,
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        runtime._verified_head_source = None

        def blocked(_cursor):
            raise AssertionError("cold source validation reached clock peek")

        monkeypatch.setattr(ledger, "project_at", blocked)
        assert runtime.next_due() == state.logical_time + timedelta(seconds=1)
        assert runtime._verified_head_source is None
        with pytest.raises(AssertionError, match="cold source validation"):
            runtime.prepare_source_history(projection=state)
        assert len(author.calls) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("shared_text", [False, True])
@pytest.mark.parametrize(
    "contract", [None, "world-outlook-script-publication.1", "world-outlook-script-publication.2"]
)
async def test_recurring_publication_has_a_pinned_start_binding_and_legacy_receipt_reuse(
    tmp_path, monkeypatch, contract, shared_text
):
    from test_world_planning import planning

    ledger, wake, store, _, runtime = setup(tmp_path, lambda w: [])
    published = planning(
        contract="world-planning.2",
        starts_on=NOW.date().isoformat(),
        ends_on=(NOW + timedelta(days=30)).date().isoformat(),
        detailed_through=(NOW + timedelta(days=7)).date().isoformat(),
        review_at=(NOW + timedelta(days=6)).isoformat(),
        slots=[
            {
                "slot_id": "writing",
                "title": "写作课",
                "detail": "教务安排",
                "weekdays": [4],
                "start_minute": 840,
                "duration_minutes": 90,
                "event_text": "同学们的复印件在课桌间传阅，教师在讲台旁逐段口头点评。",
            }
        ],
    )
    monkeypatch.setattr(
        runtime,
        "_planning_publications",
        lambda _: (
            SimpleNamespace(
                occurrence_ref="occurrence:source-bound-course-plan", planning=published
            ),
        ),
    )
    runtime._rolling_planning = True

    class Critic:
        model = "fixture:symbolic-publication-boundary"

        def __init__(self):
            self.packets = []

        async def complete(self, messages, **kwargs):
            self.packets.append(json.loads(messages[1]["content"]))
            return json.dumps(
                {
                    "review": {
                        "decision": "supported",
                        "reason": "Fixture captures adapter input only; not semantic judgement.",
                    }
                }
            )

    critic = Critic()
    runtime._source_critic = critic
    state = ledger.project()
    candidate = WorldOutlookDraft.model_validate_json(json.dumps(draft_value(wake)))
    manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger))
    if shared_text:
        candidate = candidate.model_copy(
            update={
                "opportunities": (
                    candidate.opportunities[0].model_copy(
                        update={
                            "execution": candidate.opportunities[0].execution.model_copy(
                                update={"text": published.slots[0].event_text}
                            )
                        }
                    ),
                    *candidate.opportunities[1:],
                )
            }
        )
    kwargs = {} if contract is None else {"publication_contract": contract}
    try:
        proof = await runtime._review_executable_sources(
            candidate=candidate, context={}, manifest=manifest, projection=state, **kwargs
        )
        again = await runtime._review_executable_sources(
            candidate=candidate, context={}, manifest=manifest, projection=state, **kwargs
        )
        assert proof == again and len(critic.packets) == 1 and ledger.project() == state
        timing = critic.packets[0]["pinned_authority"]["temporal_publication"]
        slot = timing["fields"][0 if shared_text else 3]
        assert timing["contract"] == (contract or "world-outlook-script-publication.1")
        if contract == "world-outlook-script-publication.2":
            assert slot["first_available_at_ref"] == "each_instance.starts_at"
            assert (
                (slot.get("first_available_at") == NOW.isoformat())
                if shared_text
                else "first_available_at" not in slot
            )
            assert "NEVER means the evaluated current cursor" in timing["rule"]
        else:
            assert (
                slot["first_available_at"] == (NOW.isoformat() if shared_text else None)
                and "first_available_at_ref" not in slot
            )
        assert timing["fields"][0]["first_available_at"] == NOW.isoformat()
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt_metadata", [False, True])
async def test_fresh_origin_reuses_pinned_registered_identity_and_real_script_privacy(tmp_path, corrupt_metadata):
    from test_world_consequence_authoring_context import _registered_identity_fixture
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request

    ledger, content, manifest, registration = await _registered_identity_fixture(tmp_path)
    state = ledger.project()
    clock = max(
        (r for r in state.committed_world_event_refs if r.event_type == "ClockAdvanced"),
        key=lambda r: r.world_revision,
    )
    wake = ledger.lookup_event_commit(clock.event_id)[0]
    value = draft_value(wake)
    value.update(
        world_id=ledger.world_id,
        actor_ref=manifest.owner_actor_ref,
        review_at=(state.logical_time + timedelta(days=1)).isoformat(),
        horizon_end=(state.logical_time + timedelta(days=21)).isoformat(),
    )
    for node in value["opportunities"]:
        node.update(
            opens_at=state.logical_time.isoformat(),
            closes_at=(state.logical_time + timedelta(days=7)).isoformat(),
        )
    value["opportunities"][0]["execution"]["text"] = manifest.npc_capabilities[0].identity_summary
    value["opportunities"][1]["execution"]["privacy_class"] = "public"
    author = Author([{"decision": "revise", "draft": value}])
    packets = []

    class Critic:
        model = "fixture:registered-identity-transport"

        async def complete(self, messages, **kwargs):
            packets.append(json.loads(messages[1]["content"]))
            return json.dumps(
                {
                    "review": {
                        "decision": "supported",
                        "reason": "Fixture captures exact source/policy transport only, not semantic-quality proof.",
                    }
                }
            )

    outlook_store = SQLiteWorldOutlookStore(
        path=tmp_path / "npc-outlook.sqlite",
        world_id=ledger.world_id,
        actor_ref=manifest.owner_actor_ref,
    )
    runtime = WorldOutlookRuntime(
        ledger=ledger,
        store=outlook_store,
        author=author,
        actor_ref=manifest.owner_actor_ref,
        content_store=content,
        source_critic=Critic(),
    )
    if corrupt_metadata:
        cap = manifest.npc_capabilities[0]
        manifest = manifest.model_copy(update={"npc_capabilities": (
            cap.model_copy(update={"identity_payload_hash": "f" * 64}),
        )})
    try:
        result = await runtime.refresh(
            projection=state,
            context={},
            manifest=manifest.model_copy(update={"max_future_days": 30, "max_window_minutes": 720}),
        )
        if corrupt_metadata:
            assert result == {"status": "technical_failure", "failure_code": "outlook_npc_identity_evidence_unavailable"}
            assert len(author.calls) == 1 and packets == []
            assert outlook_store.head() is None and ledger.project() == state
            # Readonly evidence cannot be repaired by buying another author.
            repeat = await runtime.refresh(projection=state, context={}, manifest=manifest.model_copy(update={"max_future_days": 30, "max_window_minutes": 720}))
            assert repeat["failure_code"] == result["failure_code"] and len(author.calls) == 1
            return
        assert result["status"] == "ready", result
        assert len(author.calls) == len(packets) == 1
        pinned = decode_outlook_request(author.calls[0][1]["content"])
        assert (
            pinned["private_origin_review"]["npc_identity_source_contract"]
            == "pinned-world-npc-identities.1"
        )
        assert (
            pinned["npc_capabilities"][0]["identity_summary"]
            == manifest.npc_capabilities[0].identity_summary
        )
        identities = packets[0]["pinned_authority"]["registered_npc_identities"]
        assert identities["items"][0]["authority_refs"] == [registration.event_id]
        assert identities["items"][0]["privacy_class"] == "personal"
        assert (
            identities["publication_privacy"]["outcomes.0.world_consequence.environment_text"]
            == "private"
        )
        assert (
            identities["publication_privacy"]["outcomes.1.world_consequence.environment_text"]
            == "public"
        )
        assert ledger.project() == state
        repeat = await runtime.refresh(
            projection=state,
            context={},
            manifest=manifest.model_copy(update={"max_future_days": 30, "max_window_minutes": 720}),
        )
        assert repeat["status"] == "unchanged" and len(author.calls) == len(packets) == 1
    finally:
        runtime.close()
        content.close()
        ledger.close()


@pytest.mark.asyncio
async def test_original_npc_root_validation_does_not_own_the_foreground_event_loop(
    tmp_path, monkeypatch
):
    import asyncio
    import threading
    from test_world_consequence_authoring_context import _registered_identity_fixture
    import companion_daemon.world_v2.world_consequence_authoring_context as roots

    ledger, content, manifest, _ = await _registered_identity_fixture(tmp_path)
    state = ledger.project()
    clock = max(
        (r for r in state.committed_world_event_refs if r.event_type == "ClockAdvanced"),
        key=lambda r: r.world_revision,
    )
    wake = ledger.lookup_event_commit(clock.event_id)[0]
    value = draft_value(wake)
    value.update(
        world_id=ledger.world_id,
        actor_ref=manifest.owner_actor_ref,
        review_at=(state.logical_time + timedelta(days=1)).isoformat(),
        horizon_end=(state.logical_time + timedelta(days=21)).isoformat(),
    )
    entered = threading.Event()
    released = threading.Event()
    original = roots.read_registered_npc_identity_sources

    def gated(**kwargs):
        entered.set()
        assert released.wait(1), (
            "Verified Root read blocked the foreground loop; it could not release the gate."
        )
        return original(**kwargs)

    monkeypatch.setattr(roots, "read_registered_npc_identity_sources", gated)

    class Critic:
        model = "fixture:async-root-boundary"

        async def complete(self, *args, **kwargs):
            return json.dumps(
                {"review": {"decision": "supported", "reason": "Stipulated transport only."}}
            )

    store = SQLiteWorldOutlookStore(
        path=tmp_path / "root-async.sqlite",
        world_id=ledger.world_id,
        actor_ref=manifest.owner_actor_ref,
    )
    runtime = WorldOutlookRuntime(
        ledger=ledger,
        store=store,
        author=Author([]),
        actor_ref=manifest.owner_actor_ref,
        content_store=content,
        source_critic=Critic(),
    )
    before = ledger.project()
    task = asyncio.create_task(
        runtime._review_executable_sources(
            candidate=WorldOutlookDraft.model_validate_json(json.dumps(value)),
            context={},
            manifest=manifest,
            projection=state,
            publication_contract="world-outlook-script-publication.2",
            npc_capabilities=[c.model_dump(mode="json") for c in manifest.npc_capabilities],
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        released.set()
        assert await task
        assert ledger.project() == before
    finally:
        released.set()
        if not task.done():
            await task
        runtime.close()
        content.close()
        ledger.close()
