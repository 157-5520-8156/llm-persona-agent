"""Week reads reuse audited plans and installed outlook, without advancing either."""
from datetime import timedelta
import json

import pytest
from starlette.datastructures import QueryParams

from companion_daemon.world_v2.dashboard_agenda import requested_week, week_start_date
from companion_daemon.world_v2.dashboard_home_snapshot import DashboardHomeSnapshotModule
from companion_daemon.world_v2.world_outlook import WorldOutlookDraft
from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore
from test_day_open_life_intent_runtime import ACTOR, INTENT, _accept, _choice, _cursor, _record, _seed
from test_world_outlook import draft_value


@pytest.mark.asyncio
async def test_week_reads_original_agenda_and_outlook_and_separates_truth(tmp_path):
    ledger, first = _seed(tmp_path / "calendar.sqlite")
    proposal, cursor = _record(ledger, *_choice(ledger, first, agenda=[INTENT, {
        **INTENT, "intention": "晚一点整理照片。", "start_after_seconds": 3600,
    }]))
    _accept(ledger, proposal, cursor)
    store = SQLiteWorldOutlookStore(path=tmp_path / "calendar.sqlite", world_id=ledger.world_id, actor_ref=ACTOR)
    value = draft_value(first)
    value["actor_ref"] = ACTOR
    value["world_id"] = ledger.world_id
    value["opportunities"][1]["after_opportunities"] = [value["opportunities"][0]["opportunity_id"]]
    store.install(WorldOutlookDraft.model_validate_json(json.dumps(value)), evidence={"cursor": _cursor(ledger).model_dump()})
    module = DashboardHomeSnapshotModule(ledger=ledger, deployment_id="test", boot_id="test",
                                        life_actor_ref=ACTOR, life_privacy_ceiling="private", outlook_reader=store.head)
    before = ledger.export_replay_evidence()
    week = week_start_date(first.logical_time.date())
    snapshot = await module.capture(week=week)
    agenda = snapshot.sections.overview_life.data.agenda
    assert agenda.week_start == week and agenda.timezone == "Asia/Shanghai"
    assert {e.detail for e in agenda.entries if e.kind == "plan"} == {INTENT["intention"], "晚一点整理照片。"}
    candidates = [e for e in agenda.entries if e.kind == "outlook"]
    assert len(candidates) == 3 and all("未定" in e.status for e in candidates)
    assert any(e.related for e in candidates)
    assert not any(e.kind == "world" for e in agenda.entries)
    encoded = agenda.model_dump_json()
    for plan in before.projection.plans:
        assert plan.plan_id not in encoded
    assert "source_ref" not in encoded and "revision" not in encoded
    assert await module.capture(week=week) == snapshot
    other = await module.capture(week=week + timedelta(days=28))
    assert other.sections.overview_life.data.agenda.entries == ()
    assert other.snapshot_hash != snapshot.snapshot_hash
    assert ledger.export_replay_evidence() == before
    store.close()
    ledger.close()


@pytest.mark.asyncio
async def test_outlook_changes_on_same_ledger_cursor_invalidate_week_cache(tmp_path):
    ledger, first = _seed(tmp_path / "head.sqlite")
    store = SQLiteWorldOutlookStore(path=tmp_path / "head.sqlite", world_id=ledger.world_id, actor_ref=ACTOR)
    module = DashboardHomeSnapshotModule(ledger=ledger, deployment_id="test", boot_id="test",
                                        life_actor_ref=ACTOR, life_privacy_ceiling="private", outlook_reader=store.head)
    before = await module.capture()
    value = draft_value(first)
    value["actor_ref"] = ACTOR
    value["world_id"] = ledger.world_id
    store.install(WorldOutlookDraft.model_validate_json(json.dumps(value)), evidence={"cursor": _cursor(ledger).model_dump()})
    after = await module.capture()
    assert before.cursor == after.cursor and before.snapshot_hash != after.snapshot_hash
    assert before.sections.overview_life.data.agenda.outlook_state == "empty"
    assert after.sections.overview_life.data.agenda.outlook_state == "ready"
    store.close()
    ledger.close()


@pytest.mark.parametrize("query", ["week=2026-99-10", "week=20261012", "week=2026-10-12&week=2026-10-19",
                                   "week=2026-10-12&actor_ref=other", "cursor=3", "week=9999-12-31"])
def test_week_filter_rejects_invalid_or_authority_parameters(query):
    with pytest.raises(ValueError):
        requested_week(QueryParams(query))


def test_week_filter_is_only_a_normalized_local_date():
    assert str(requested_week(QueryParams("week=2026-10-16"))) == "2026-10-12"
    assert requested_week(QueryParams()) is None


@pytest.mark.asyncio
async def test_week_output_and_immutable_reads_stay_bounded_as_history_grows(tmp_path):
    from companion_daemon.world_v2.dashboard_agenda import ENTRY_LIMIT, compile_dashboard_agenda

    ledger, first = _seed(tmp_path / "growth.sqlite")
    proposal, cursor = _record(ledger, *_choice(ledger, first))
    _accept(ledger, proposal, cursor)
    state = ledger.project()
    plan, = state.plans
    # Only the display compiler receives this large projection. Invalid copied
    # identities cannot acquire original intention text through the real reader.
    many = tuple(plan.model_copy(update={"plan_id": f"plan:day-open-life-intent:{i:064x}"}) for i in range(10_000))
    state = state.model_copy(update={"plans": many})
    reads = []
    original = ledger.lookup_event_commit
    ledger.lookup_event_commit = lambda ref: (reads.append(ref), original(ref))[1]
    before = ledger.project().semantic_hash
    agenda = compile_dashboard_agenda(ledger=ledger, projection=state, cursor=_cursor(ledger),
                                      store=None, actor_ref=ACTOR,
                                      week_start=week_start_date(first.logical_time.date()))
    assert agenda.known_count == 10_000 and agenda.truncated
    assert len(agenda.entries) == ENTRY_LIMIT and len(reads) <= ENTRY_LIMIT
    assert len(agenda.model_dump_json()) < 100_000
    assert all(e.content_state == "unavailable" for e in agenda.entries)
    assert ledger.project().semantic_hash == before
    ledger.close()


@pytest.mark.asyncio
async def test_outlook_privacy_ceiling_is_explicit_and_withhold_never_leaks(tmp_path):
    ledger, first = _seed(tmp_path / "privacy.sqlite")
    value = draft_value(first)
    value.update(world_id=ledger.world_id, actor_ref=ACTOR)
    value["opportunities"][0]["execution"]["privacy_class"] = "withhold"
    value["opportunities"][0]["external_possibility"] = "WITHHELD_CANARY"
    draft = WorldOutlookDraft.model_validate_json(json.dumps(value))
    head = ("revision", draft, {"cursor": _cursor(ledger).model_dump()})
    for ceiling, expected in (("shareable", 0), ("private", 2)):
        module = DashboardHomeSnapshotModule(ledger=ledger, deployment_id="test", boot_id="test",
                                            life_actor_ref=ACTOR, life_privacy_ceiling=ceiling,
                                            outlook_reader=lambda **kwargs: head)
        agenda = (await module.capture()).sections.overview_life.data.agenda
        assert len(agenda.entries) == expected
        assert "WITHHELD_CANARY" not in agenda.model_dump_json()
        assert agenda.withheld_count == 3 - expected
    ledger.close()
