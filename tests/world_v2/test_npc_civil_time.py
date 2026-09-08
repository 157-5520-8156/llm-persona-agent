"""NPC local time from public runtime to the provider request, without network."""

from datetime import datetime, timedelta
import json
from pathlib import Path

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.biographical_timeline_authority import (
    BiographicalTimelineConfiguredPayload,
)
from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.npc_ecology import NpcEcology, NpcEcologyStimulus
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.occurrence_content_coordinator import OccurrenceContentCoordinator
from test_life_projection import commit, event
from test_npc_ecology import _actor, _runtime


@pytest.fixture(autouse=True)
def disable_debug_ledger(monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")


def _timeline(ledger):
    value = BiographicalTimelineConfiguredPayload.from_yaml(
        path=Path("configs/world_seed.yaml"), timezone_name="Asia/Shanghai"
    )
    assert value is not None
    source = event(
        "npc-local-time:timeline",
        "BiographicalTimelineConfigured",
        value.model_dump(mode="json"),
        at=ledger.project().logical_time,
    )
    commit(ledger, [source])
    return source


def _clock(ledger, instant, ordinal=0):
    source = event(
        f"npc-local-time:clock:{ordinal}",
        "ClockAdvanced",
        {
            "logical_time_from": ledger.project().logical_time.isoformat(),
            "logical_time_to": instant.isoformat(),
        },
        at=instant,
    )
    commit(ledger, [source])
    return source


class _FaultySourceReader:
    """The ledger remains authoritative while its exact event reader is unavailable."""

    def __init__(self, ledger, ref, fault):
        self.ledger = ledger
        self.ref = ref
        self.fault = fault

    def lookup_event_commit(self, ref):
        located = self.ledger.lookup_event_commit(ref)
        if ref != self.ref:
            return located
        if self.fault.endswith("read_error"):
            raise OSError("fixture source reader unavailable")
        if self.fault.endswith("bad_hash"):
            source, commit_record = located
            return source.model_copy(update={"payload_hash": "0" * 64}), commit_record
        return None

    def __getattr__(self, name):
        return getattr(self.ledger, name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utc,local",
    [
        ("2026-09-08T03:07:00+00:00", "2026-09-08T11:07:00+08:00"),
        ("2026-09-08T20:07:00+00:00", "2026-09-09T04:07:00+08:00"),
    ],
)
async def test_npc_request_binds_local_time_and_keeps_model_authored_state(utc, local):
    ledger, store, _actor_model, world, _old = _runtime(_actor("no_op"), {"decision": "no_op"})
    timeline = _timeline(ledger)
    clock = _clock(ledger, datetime.fromisoformat(utc))
    requests = []
    inner = "This is my own private thought, preserved verbatim."

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        payload = json.loads(body["messages"][1]["content"])
        result = _actor("no_op") | {
            "npc_ref": payload["authority"]["selected_npc_ref"],
            "source_refs": payload["authority"]["input_event_refs"],
            "inner_state_summary": inner,
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(result)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            },
        )

    provider = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(respond),
    )
    runtime = NpcEcology(
        ledger=ledger,
        content_store=store,
        occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=store),
        actor_model=provider,
        world_author=world,
        protagonist_actor_ref="actor:companion",
        catalog=ReviewedLifeSeedCatalog.from_yaml(
            path=Path("configs/world_seed.yaml"),
            chronology=LocalChronology(timezone_name="Asia/Shanghai"),
        ),
        decision_opportunity_mass_bp=10_000,
    )
    location = ledger.project().npcs[0].current_location_ref
    try:
        for ordinal in range(2):
            if ordinal:
                clock = _clock(ledger, datetime.fromisoformat(utc) + timedelta(minutes=40), ordinal)
            outcome = await runtime.advance_once(
                wake_event_ref=clock.event_id,
                trace_id="trace:npc-time",
                correlation_id="correlation:npc-time",
            )
            assert outcome.status == "state_advanced", outcome
            assert len(requests) == ordinal + 1
            context = json.loads(requests[-1]["messages"][1]["content"])
            profile = context["npc_actor_profile"]
            now = profile["now"]
            assert now["logical_time"] == clock.logical_time.isoformat()
            assert now["current_location_ref"] == location
            civil = now["civil_time"]
            assert civil["contract"] == "npc-civil-time.1"
            assert civil["status"] == "available"
            assert civil["timezone_name"] == "Asia/Shanghai"
            assert (
                civil["local_time"]
                == (datetime.fromisoformat(local) + timedelta(minutes=40 * ordinal)).isoformat()
            )
            assert {item["authority_event_ref"] for item in civil["source_bindings"]} == {
                clock.event_id,
                timeline.event_id,
            }
            for binding in civil["source_bindings"]:
                source = ledger.lookup_event_commit(binding["authority_event_ref"])[0]
                assert binding["authority_payload_hash"] == source.payload_hash
                assert binding["field_path"] in ("/logical_time_to", "/timezone_name")
                assert binding["authority_scope"] == "exact_time_field_only"
            assert timeline.event_id in context["authority"]["input_event_refs"]
            assert profile["my_last_state"] == (inner if ordinal else None)
            assert not any(
                field in requests[-1]["messages"][1]["content"]
                for field in ('"birth_date"', '"academic"', '"document"', '"baseline_context_tags"')
            )
        assert world.calls == []
        assert ledger.project().npcs[0].current_location_ref == location
        assert ledger.project() == ledger.rebuild()
        replay = await runtime.advance_once(
            wake_event_ref=clock.event_id,
            trace_id="trace:repeat",
            correlation_id="correlation:repeat",
        )
        assert replay.status in ("already_considered", "not_due")
        assert len(requests) == 2
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault,reason",
    [
        ("missing_timeline", "timeline_missing"),
        ("unreadable_timeline", "timeline_unreadable"),
        ("unreadable_clock", "clock_unreadable"),
        ("timeline_read_error", "timeline_unreadable"),
        ("clock_read_error", "clock_unreadable"),
        ("timeline_bad_hash", "timeline_invalid"),
        ("clock_bad_hash", "clock_invalid"),
        ("catalog_mismatch", "catalog_timezone_mismatch"),
    ],
)
async def test_npc_request_marks_unavailable_civil_time_without_guessing(fault, reason):
    ledger, store, actor, world, _old = _runtime(_actor("no_op"), {"decision": "no_op"})
    timeline = None if fault == "missing_timeline" else _timeline(ledger)
    clock = _clock(ledger, datetime.fromisoformat("2026-09-08T03:07:00+00:00"))
    viewed_ledger = ledger
    if "read" in fault or fault.endswith("bad_hash"):
        viewed_ledger = _FaultySourceReader(
            ledger, clock.event_id if "clock" in fault else timeline.event_id, fault
        )
    catalog = (
        ReviewedLifeSeedCatalog.from_yaml(
            path=Path("configs/world_seed.yaml"), chronology=LocalChronology(timezone_name="UTC")
        )
        if fault == "catalog_mismatch"
        else None
    )
    actor.payload = _actor("no_op") | {"source_refs": [clock.event_id]}
    runtime = NpcEcology(
        ledger=viewed_ledger,
        content_store=store,
        occurrence_content=OccurrenceContentCoordinator(ledger=viewed_ledger, store=store),
        actor_model=actor,
        world_author=world,
        protagonist_actor_ref="actor:companion",
        catalog=catalog,
        decision_opportunity_mass_bp=10_000,
    )
    result = await runtime.advance_once(
        wake_event_ref=clock.event_id,
        trace_id="trace:time-unavailable",
        correlation_id="correlation:time-unavailable",
    )
    assert result.status == "state_advanced", result
    assert len(actor.calls) == 1
    context = json.loads(actor.calls[0][1]["content"])
    now = context["npc_actor_profile"]["now"]
    assert now["logical_time"] == clock.logical_time.isoformat()
    assert now["current_location_ref"] == ledger.project().npcs[0].current_location_ref
    assert now["civil_time"] == {
        "contract": "npc-civil-time.1",
        "status": "unavailable",
        "reason_code": reason,
        "timezone_name": None,
        "local_time": None,
        "source_bindings": [],
    }
    assert world.calls == []


@pytest.mark.asyncio
async def test_missing_clock_is_no_npc_opportunity_and_does_not_call_a_model():
    _ledger, _store, actor, world, runtime = _runtime(_actor("no_op"), {"decision": "no_op"})
    result = await runtime.advance_once(
        wake_event_ref="absent-clock",
        trace_id="trace:absent-clock",
        correlation_id="correlation:absent-clock",
    )
    assert result.reason_code == "npc_ecology.wake_missing"
    assert actor.calls == world.calls == []
    empty = runtime.snapshot(
        ProjectionCursor(world_revision=0, deliberation_revision=0, ledger_sequence=0)
    )
    assert empty.civil_time.status == "unavailable"
    assert empty.civil_time.reason_code == "clock_missing"


@pytest.mark.asyncio
async def test_time_provenance_survives_the_existing_32_reference_budget():
    ledger, _store, actor, _world, runtime = _runtime(_actor("no_op"), {"decision": "no_op"})
    timeline = _timeline(ledger)
    clocks = []
    for ordinal in range(32):
        now = ledger.project().logical_time + timedelta(minutes=1)
        source = event(
            f"zz:npc-time:clock:{ordinal:02d}",
            "ClockAdvanced",
            {
                "logical_time_from": ledger.project().logical_time.isoformat(),
                "logical_time_to": now.isoformat(),
            },
            at=now,
        )
        commit(ledger, [source])
        clocks.append(source)
    actor.payload = _actor("no_op") | {"source_refs": [timeline.event_id, clocks[-1].event_id]}
    projection = ledger.project()
    result = await runtime.advance(
        NpcEcologyStimulus(
            cursor=ProjectionCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            ),
            wake_event_ref=clocks[-1].event_id,
            source_event_refs=tuple(source.event_id for source in clocks),
            epoch_ref="test:npc-time:bounded-sources",
        )
    )
    assert result.status == "state_advanced", result
    assert len(actor.calls) == 1
    context = json.loads(actor.calls[0][1]["content"])
    refs = context["authority"]["input_event_refs"]
    assert len(refs) == 32
    assert timeline.event_id in refs
    assert clocks[-1].event_id in refs
    state = ledger.project().npcs[0].subjective_state
    assert timeline.event_id in state.source_event_refs
    assert clocks[-1].event_id in state.source_event_refs
