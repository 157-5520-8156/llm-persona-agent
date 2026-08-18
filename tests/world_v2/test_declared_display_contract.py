from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion_daemon.media_eligibility import (
    MediaEligibilityRouter,
    MediaLaneRecommendation,
)
from companion_daemon.media_suggestive_lane import SUGGESTIVE_PRIVATE_LANE
from companion_daemon.world_v2.declared_display_contract import (
    DECLARED_DISPLAY_RECORDED,
    DECLARED_DISPLAY_WITHDRAWN,
    DeclaredDisplayRecordedPayload,
    DeclaredDisplayWithdrawnPayload,
)
from companion_daemon.world_v2.declared_display_runtime import (
    DeclaredDisplayCommand,
    DeclaredDisplayRuntime,
)
from companion_daemon.world_v2.event_catalog import event_contract
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.present_prompt import compile_slim_consider_payload
from companion_daemon.world_v2.reducers import ReducerState, reduce_event
from companion_daemon.world_v2.schemas import CommittedWorldEventRef, WorldEvent
from companion_daemon.world_v2.event_media_planner_adapter import EventMediaPlannerAdapter


NOW = datetime(2026, 8, 18, 3, tzinfo=UTC)
WORLD = "world:declared-display"
OBSERVATION_ID = "event:observation:inbound-1"


def _observation() -> WorldEvent:
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=OBSERVATION_ID,
        event_type="ObservationRecorded",
        world_id=WORLD,
        logical_time=NOW,
        created_at=NOW,
        actor="user:1",
        source="test:declared-display",
        trace_id="trace:declared-display",
        causation_id="cause:obs",
        correlation_id="correlation:declared-display",
        idempotency_key="observation:declared-display",
        payload={"observation_id": "obs:1", "actor": "user:1"},
    )


def _state_with_observation(source: WorldEvent) -> ReducerState:
    return ReducerState(
        logical_time=source.logical_time,
        committed_world_event_refs=(
            CommittedWorldEventRef(
                event_id=source.event_id,
                event_type=source.event_type,
                world_revision=1,
                payload_hash=source.payload_hash,
                logical_time=source.logical_time,
            ),
        ),
    )


def _recorded_payload(source: WorldEvent, *, intent: str = "sexual_suggestive"):
    return DeclaredDisplayRecordedPayload(
        source_event_ref=source.event_id,
        source_event_payload_hash=source.payload_hash,
        source_event_type="ObservationRecorded",
        recipient_ref="user:1",
        media_intent=intent,  # type: ignore[arg-type]
        declared_at=NOW,
    )


def test_declared_display_is_a_catalogued_declaration_wire() -> None:
    payload = _recorded_payload(_observation())
    assert event_contract(DECLARED_DISPLAY_RECORDED).payload_model is type(payload)
    assert event_contract(DECLARED_DISPLAY_WITHDRAWN).payload_model is DeclaredDisplayWithdrawnPayload
    assert event_contract(DECLARED_DISPLAY_RECORDED).producer == "declared_display_acceptance"
    assert event_contract(DECLARED_DISPLAY_WITHDRAWN).allowed_predecessors == (
        DECLARED_DISPLAY_RECORDED,
        "ObservationRecorded",
    )


def test_reducer_accepts_a_source_bound_display_declaration() -> None:
    source = _observation()
    payload = _recorded_payload(source)
    event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:declared-display:1",
        event_type=DECLARED_DISPLAY_RECORDED,
        world_id=WORLD,
        logical_time=NOW,
        created_at=NOW,
        actor="worker:declared-display",
        source="test",
        trace_id="trace:declared-display",
        causation_id=source.event_id,
        correlation_id="correlation:declared-display",
        idempotency_key="declared-display:1",
        payload=payload.model_dump(mode="json"),
    )
    reduced = reduce_event(_state_with_observation(source), event)
    assert reduced.committed_world_event_refs[-1].event_id == event.event_id
    assert reduced.committed_world_event_refs[-1].event_type == DECLARED_DISPLAY_RECORDED


def test_reducer_rejects_a_display_declaration_with_stale_source_hash() -> None:
    source = _observation()
    payload = _recorded_payload(source).model_dump(mode="json")
    payload["source_event_payload_hash"] = "b" * 64
    event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:declared-display:stale",
        event_type=DECLARED_DISPLAY_RECORDED,
        world_id=WORLD,
        logical_time=NOW,
        created_at=NOW,
        actor="worker:declared-display",
        source="test",
        trace_id="trace:declared-display",
        causation_id=source.event_id,
        correlation_id="correlation:declared-display",
        idempotency_key="declared-display:stale",
        payload=payload,
    )
    with pytest.raises(ValueError, match="declared display source is not current"):
        reduce_event(_state_with_observation(source), event)


def test_runtime_binds_recipient_from_observation_actor_not_the_command() -> None:
    source = _observation()
    commits: list[tuple[object, object, str]] = []
    projection = SimpleNamespace(
        world_revision=2,
        deliberation_revision=0,
        ledger_sequence=2,
        logical_time=NOW,
        committed_world_event_refs=(
            CommittedWorldEventRef(
                event_id=source.event_id,
                event_type=source.event_type,
                world_revision=1,
                payload_hash=source.payload_hash,
                logical_time=NOW,
            ),
        ),
    )
    ledger = SimpleNamespace(
        world_id=WORLD,
        project=lambda: projection,
        lookup_event_commit=lambda event_id: (source, None) if event_id == source.event_id else None,
        commit_at_cursor=lambda events, expected_cursor, commit_id: commits.append(
            (events, expected_cursor, commit_id)
        ),
    )
    DeclaredDisplayRuntime(ledger=ledger).declare(
        DeclaredDisplayCommand(
            command_id="command:display:1",
            source_event_ref=source.event_id,
            media_intent="sexual_suggestive",
        ),
        logical_time=NOW,
        created_at=NOW,
        actor="worker:declared-display",
        trace_id="trace:declared-display",
        correlation_id="correlation:declared-display",
    )
    event = commits[0][0][0]
    payload = event.payload()
    assert event.event_type == DECLARED_DISPLAY_RECORDED
    assert payload["recipient_ref"] == "user:1"
    assert payload["media_intent"] == "sexual_suggestive"
    assert payload["kind"] == "recipient_directed"
    assert event.causation_id == source.event_id
    assert domain_idempotency_key(
        event_type=DECLARED_DISPLAY_RECORDED, world_id=WORLD, payload=payload
    ) == event.idempotency_key


def test_runtime_withdraw_reuses_the_same_observation_binding() -> None:
    source = _observation()
    commits: list[tuple[object, object, str]] = []
    projection = SimpleNamespace(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=3,
        logical_time=NOW,
        committed_world_event_refs=(
            CommittedWorldEventRef(
                event_id=source.event_id,
                event_type=source.event_type,
                world_revision=1,
                payload_hash=source.payload_hash,
                logical_time=NOW,
            ),
        ),
    )
    ledger = SimpleNamespace(
        world_id=WORLD,
        project=lambda: projection,
        lookup_event_commit=lambda event_id: (source, None) if event_id == source.event_id else None,
        commit_at_cursor=lambda events, expected_cursor, commit_id: commits.append(
            (events, expected_cursor, commit_id)
        ),
    )
    DeclaredDisplayRuntime(ledger=ledger).declare(
        DeclaredDisplayCommand(
            command_id="command:display:withdraw",
            source_event_ref=source.event_id,
            withdraw=True,
        ),
        logical_time=NOW,
        created_at=NOW,
        actor="worker:declared-display",
        trace_id="trace:declared-display",
        correlation_id="correlation:declared-display",
        prior_event_ref="event:declared-display:1",
    )
    event = commits[0][0][0]
    payload = event.payload()
    assert event.event_type == DECLARED_DISPLAY_WITHDRAWN
    assert payload["recipient_ref"] == "user:1"
    assert payload["prior_event_ref"] == "event:declared-display:1"


def test_slim_declared_display_lands_in_private_turn_state() -> None:
    compiled = compile_slim_consider_payload(
        {
            "messages": ["就给你看个影子吧，别的先不说"],
            "felt": "想给他看，但只到这里",
            "declared_display": "sexual_suggestive",
        }
    )
    assert compiled is not None
    state = compiled["expression_draft"]["private_turn_state"]
    assert state["declared_display"] == "sexual_suggestive"


def test_slim_declared_display_ignores_a_model_authored_recipient_ref() -> None:
    compiled = compile_slim_consider_payload(
        {
            "messages": ["今晚就算了"],
            "felt": "不想给看",
            "declared_display": {
                "media_intent": "withdraw",
                "recipient_ref": "user:forged",
            },
        }
    )
    assert compiled is not None
    state = compiled["expression_draft"]["private_turn_state"]
    assert state["declared_display"] == "withdraw"
    assert "recipient_ref" not in state


def test_slim_omitted_declared_display_does_not_invent_a_grant() -> None:
    compiled = compile_slim_consider_payload(
        {"messages": ["今天不想发照片 你就想象一下吧"], "felt": "不想给看"}
    )
    assert compiled is not None
    assert "declared_display" not in compiled["expression_draft"]["private_turn_state"]


def test_slim_null_declared_display_is_omission_not_failure() -> None:
    compiled = compile_slim_consider_payload(
        {
            "messages": ["今天就这样"],
            "felt": "这一轮不声明",
            "declared_display": None,
        }
    )
    assert compiled is not None
    assert "declared_display" not in compiled["expression_draft"]["private_turn_state"]


def test_slim_invalid_declared_display_is_a_visible_failure() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_DECLARED_DISPLAY_INVALID

    with pytest.raises(ValueError, match=SLIM_DECLARED_DISPLAY_INVALID) as caught:
        compile_slim_consider_payload(
            {
                "messages": ["给你看一张"],
                "felt": "想给他看",
                "declared_display": "nsfw",
            }
        )
    assert "宿主不会替你改成一个合法值" in str(caught.value)


def test_eligibility_and_freeze_reread_pass_for_a_ledger_backed_suggestive_declaration() -> None:
    """End-to-end to the pre-render gate: declare → reduce → freeze pointer.

    Production still cannot emit an adult still (relationship remains stranger,
    candidates remain 0).  This constructs the missing world facts on an
    isolated ledger slice and writes the evidence under output/declared-display/.
    """

    from test_adult_media_authorization import _authorize, _p3_world

    ledger, candidate = _p3_world(
        stage="lover", with_adult_grants=True, display_intent="sexual_suggestive"
    )
    recorded_ref = next(
        item
        for item in ledger.project().committed_world_event_refs
        if item.event_type == DECLARED_DISPLAY_RECORDED
    )
    located = ledger.lookup_event_commit(recorded_ref.event_id)
    assert located is not None
    recorded, _commit = located
    source_located = ledger.lookup_event_commit(recorded.payload()["source_event_ref"])
    assert source_located is not None
    source, _source_commit = source_located
    reduced = reduce_event(_state_with_observation(source), recorded)
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    dumped = compiled.snapshot.image_event_snapshot.model_dump(mode="json")
    display = dumped["relationship_media_context"]["declared_display"]
    basis = EventMediaPlannerAdapter._p3_basis(dumped)
    decision = MediaEligibilityRouter().classify_recommendation(
        family="character_media",
        privacy_ceiling="intimate",
        expression_charge_ceiling="veiled",
        event_snapshot=dumped,
        private_expression_basis=basis,
        recipient_ref="user:1",
        recommendation=MediaLaneRecommendation(
            lane=SUGGESTIVE_PRIVATE_LANE,
            recipient_access="recipient_exclusive",
            attraction_expression="sexual_suggestive",
        ),
        selected_expression_charge="charged",
        selected_capture_mode="character_front_camera",
        selected_share_intent="intimate_signal",
        selected_privacy="intimate",
        selected_address_mode="direct_recipient",
        selected_interaction_bid="invite_desire",
        selected_attraction_mechanism="private_trust",
        selected_coverage_mode="private_apparel",
    )
    freeze_expected = "sexual_suggestive"
    freeze_actual = str(display.get("media_intent") or "")
    freeze_pass = freeze_actual == freeze_expected
    report = {
        "acceptance_event": {
            "event_id": reduced.committed_world_event_refs[-1].event_id,
            "event_type": reduced.committed_world_event_refs[-1].event_type,
            "payload": recorded.payload(),
        },
        "authorized_lane": opportunity.media_lane,
        "eligibility": {
            "allowed": decision.allowed,
            "lane": decision.lane,
            "reason": decision.reason,
        },
        "freeze_reread": {
            "pointer": "/relationship_media_context/declared_display",
            "expected_media_intent": freeze_expected,
            "actual_media_intent": freeze_actual,
            "pass": freeze_pass,
            "failure_code": None if freeze_pass else "private_render_intent_evidence_missing",
        },
    }
    out = Path("output/declared-display")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        __import__("json").dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert opportunity.media_lane == "suggestive_private"
    assert decision.allowed
    assert decision.lane == SUGGESTIVE_PRIVATE_LANE
    assert freeze_pass
    assert reduced.committed_world_event_refs[-1].event_type == DECLARED_DISPLAY_RECORDED
