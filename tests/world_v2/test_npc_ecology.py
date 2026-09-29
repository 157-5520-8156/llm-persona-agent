from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.life_aftermath_runtime import LifeAftermathRuntime
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore,
    SQLiteImmutableLifeContentStore,
    StoredLifeContent,
    life_content_payload_hash,
)
from companion_daemon.world_v2.npc_ecology import NpcEcology, NpcSocialWorldSnapshot
from companion_daemon.world_v2.occurrence_content_coordinator import (
    OccurrenceContentCoordinator,
)
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_projection import (
    WORLD_ID,
    commit,
    evidence,
    event,
    seed_through_proposal,
    register_operator_observations,
    settlement_batch,
)
from companion_daemon.world_v2.schemas import NpcProjection


class _Model:
    def __init__(self, payload: dict[str, object]) -> None:
        self.model = "test-npc-role"
        self.payload = payload
        self.calls: list[list[dict[str, str]]] = []
        self.json_calls: list[list[dict[str, str]]] = []

    async def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        del messages, temperature
        raise AssertionError("NPC ecology must use the provider JSON-output interface")

    async def complete_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.2
    ) -> str:
        del temperature
        self.calls.append(messages)
        self.json_calls.append(messages)
        return json.dumps(self.payload, ensure_ascii=False)


class _HttpFailureModel(_Model):
    async def complete_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.2
    ) -> str:
        del temperature
        self.calls.append(messages)
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("busy", request=request, response=response)


class _ProjectionRejectingPrivateAffectReads:
    """Make a protagonist Affect read fail even when hidden behind getattr."""

    def __init__(self, projection: object) -> None:
        self._projection = projection

    @property
    def affect_episodes(self) -> object:
        raise AssertionError("NPC Ecology cannot read protagonist private Affect")

    def __getattr__(self, name: str) -> object:
        return getattr(self._projection, name)


class _LedgerRejectingPrivateAffectReads:
    def __init__(self, ledger: WorldLedger) -> None:
        self._ledger = ledger

    def project_at(self, cursor: ProjectionCursor) -> object:
        return _ProjectionRejectingPrivateAffectReads(self._ledger.project_at(cursor))

    def __getattr__(self, name: str) -> object:
        return getattr(self._ledger, name)


class _FailOnceLedger:
    """Fail one durable write at the storage boundary, then permit recovery."""

    def __init__(self, ledger, event_type: str) -> None:
        self._ledger = ledger
        self._event_type = event_type

    def commit_at_cursor(self, events, **kwargs):
        if any(item.event_type == self._event_type for item in events):
            self._event_type = ""
            raise OSError("injected ledger write failure")
        return self._ledger.commit_at_cursor(events, **kwargs)

    def __getattr__(self, name: str) -> object:
        return getattr(self._ledger, name)


def _actor(decision: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "decision": decision,
        "npc_ref": "npc:lin",
        "impulse_summary": "她忙完后想起下午一起泡茶时没有聊完的事。",
        "inner_state_summary": "实习作品集还有点乱，她既想自己理清，也愿意再见面聊聊。",
        "source_refs": ["clock-life"],
        "relationship_to_protagonist": {
            "trust_bp": 5200,
            "closeness_bp": 4300,
            "respect_bp": 5100,
            "reliability_bp": 4800,
            "mutuality_bp": 3900,
            "repair_confidence_bp": 5000,
            "friction_bp": 300,
            "tension_bp": 700,
        },
        "current_goal_summaries": ["把实习作品集整理出一个敢拿给别人看的版本。"],
    }
    if decision == "propose":
        payload["proposal"] = {
            "timing": "now",
            "premise": "林在厨房忙完后，想顺势提起作品集。",
            "participant_refs": ["npc:lin"],
            "location_ref": "room:kitchen",
            "duration_minutes": 25,
            "visibility": "personal",
        }
    return payload


def _world() -> dict[str, object]:
    return {
        "decision": "accept",
        "outcomes": [
            {"text": "林对着页面理了一阵，改清楚了最卡的一处。", "privacy": "personal"},
            {"text": "刚说到一半林临时有事，只约好之后再接着看。", "privacy": "personal"},
        ],
    }


def _world_plan() -> dict[str, object]:
    return {
        "decision": "accept",
        "outcomes": [],
    }


def _runtime(
    actor_payload: dict[str, object],
    world_payload: dict[str, object],
    *,
    actor_model: _Model | None = None,
    ledger_path: Path | None = None,
):
    ledger = (
        WorldLedger.in_memory(world_id=WORLD_ID, accepted_batch_issuer=AcceptedLedgerBatchIssuer())
        if ledger_path is None
        else SQLiteWorldLedger(
            path=ledger_path, world_id=WORLD_ID, accepted_batch_issuer=AcceptedLedgerBatchIssuer()
        )
    )
    seed_through_proposal(ledger, event_visibility="personal")
    store = (
        InMemoryImmutableLifeContentStore()
        if ledger_path is None
        else SQLiteImmutableLifeContentStore(path=str(ledger_path), world_id=WORLD_ID)
    )
    descriptor = "林，角色在当前生活里认识的人。"
    store.put_if_absent(
        StoredLifeContent(
            content_ref=ledger.project().npcs[0].stable_identity_ref,
            content_kind="provisional_npc_introduction",
            content_payload_hash=life_content_payload_hash(descriptor),
            text=descriptor,
        )
    )
    actor = actor_model or _Model(actor_payload)
    world = _Model(world_payload)
    runtime = NpcEcology(
        ledger=ledger,
        content_store=store,
        occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=store),
        actor_model=actor,
        world_author=world,
        protagonist_actor_ref="actor:companion",
        decision_opportunity_mass_bp=10_000,
    )
    return ledger, store, actor, world, runtime


@pytest.mark.asyncio
async def test_npc_provider_503_is_not_reported_as_invalid_output() -> None:
    unavailable = _HttpFailureModel(_actor("no_op"))
    _ledger, _store, actor, world, runtime = _runtime(
        _actor("no_op"),
        {"decision": "no_op"},
        actor_model=unavailable,
    )

    result = await runtime.advance_once(
        wake_event_ref="clock-life", trace_id="trace", correlation_id="correlation"
    )

    assert result.status == "technical_failure"
    assert result.reason_code == "npc_ecology.actor_provider_http_503"
    assert len(actor.calls) == 1
    assert world.calls == []


@pytest.mark.asyncio
async def test_npc_no_op_still_advances_private_state_effect_once() -> None:
    ledger, store, actor, world, runtime = _runtime(_actor("no_op"), {"decision": "no_op"})

    first = await runtime.advance_once(
        wake_event_ref="clock-life", trace_id="trace", correlation_id="correlation"
    )
    second = await runtime.advance_once(
        wake_event_ref="clock-life", trace_id="trace", correlation_id="correlation"
    )

    assert first.status == "state_advanced"
    assert second.status in {"already_considered", "not_due"}
    assert len(actor.calls) == 1
    assert world.calls == []
    actor_context = json.loads(actor.calls[0][1]["content"])
    assert actor_context["authority"]["selected_npc_ref"] == "npc:lin"
    assert "identities" not in actor_context["public_world"]
    assert "protagonist_affect_context_json" not in actor.calls[0][1]["content"]
    assert '"json_schema"' in actor.calls[0][0]["content"]
    assert '"example_json"' not in actor.calls[0][0]["content"]
    state = ledger.project().npcs[0].subjective_state
    assert state is not None
    assert state.relationship_to_subject.closeness_bp == 4300
    assert store.read_exact(content_ref=state.inner_state_content_ref).text.startswith("实习作品集")


def test_npc_snapshot_never_reads_or_surfaces_protagonist_private_affect() -> None:
    ledger, _store, _actor_model, _world_model, runtime = _runtime(
        _actor("no_op"), {"decision": "no_op"}
    )
    projection = ledger.project()
    runtime._ledger = _LedgerRejectingPrivateAffectReads(ledger)  # type: ignore[assignment]

    snapshot = runtime.snapshot(
        ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
    )

    assert isinstance(snapshot, NpcSocialWorldSnapshot)
    assert set(NpcSocialWorldSnapshot.model_fields) == {
        "cursor",
        "logical_time",
        "identities",
        "available_npc_refs",
        "available_location_refs",
        "recent_occurrence_refs",
        "civil_time",
    }


def test_npc_snapshot_can_compile_one_explicit_actor_capsule() -> None:
    ledger, _store, _actor_model, _world_model, runtime = _runtime(
        _actor("no_op"), {"decision": "no_op"}
    )
    projection = ledger.project()

    focused = runtime.snapshot(
        ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        ),
        focus_npc_ref="npc:lin",
    )

    assert tuple(item.npc_ref for item in focused.identities) == ("npc:lin",)
    assert focused.available_npc_refs == ("npc:lin",)


def test_npc_focused_snapshot_omits_unrelated_occurrence_refs(monkeypatch) -> None:
    ledger, _store, _actor_model, _world_model, runtime = _runtime(
        _actor("no_op"), {"decision": "no_op"}
    )
    projection = ledger.project()
    assert projection.world_occurrences
    anchor = projection.world_occurrences[0]
    unrelated = anchor.model_copy(
        update={
            "occurrence_id": "occurrence:mei",
            "participant_refs": ("npc:mei",),
        }
    )
    focused_projection = projection.model_copy(
        update={"world_occurrences": (anchor, unrelated)}
    )
    monkeypatch.setattr(
        runtime,
        "_ledger",
        SimpleNamespace(project_at=lambda _cursor: focused_projection),
    )

    focused = runtime.snapshot(
        ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        ),
        focus_npc_ref="npc:lin",
    )

    assert focused.recent_occurrence_refs == (anchor.occurrence_id,)


def test_npc_snapshot_rejects_an_unknown_explicit_actor_capsule() -> None:
    ledger, _store, _actor_model, _world_model, runtime = _runtime(
        _actor("no_op"), {"decision": "no_op"}
    )
    projection = ledger.project()

    with pytest.raises(ValueError, match="source-closed NPC identity"):
        runtime.snapshot(
            ProjectionCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            ),
            focus_npc_ref="npc:missing",
        )


@pytest.mark.asyncio
async def test_npc_advance_fails_closed_when_active_identity_content_is_missing() -> None:
    ledger, _store, actor, _world_model, runtime = _runtime(
        _actor("no_op"), {"decision": "no_op"}
    )
    runtime._store = InMemoryImmutableLifeContentStore()

    result = await runtime.advance_once(
        wake_event_ref="clock-life", trace_id="trace", correlation_id="correlation"
    )

    assert result.status == "rejected"
    assert result.reason_code == "npc_ecology.actor_identity_unavailable"
    assert result.npc_ref == "npc:lin"
    assert actor.calls == []


@pytest.mark.asyncio
async def test_npc_advance_request_contains_only_the_selected_actor_capsule() -> None:
    ledger, store, actor, _world_model, runtime = _runtime(
        _actor("no_op"), {"decision": "no_op"}
    )
    register_operator_observations(ledger, "operator:npc-mei")
    mei = NpcProjection(
        npc_id="mei",
        entity_revision=1,
        stable_identity_ref="identity:npc:mei",
        known_trait_refs=("trait:careful",),
        privacy_class="personal",
        current_location_ref="room:mei",
    )
    commit(
        ledger,
        [
            event(
                "npc-mei-registered",
                "NpcRegistered",
                {
                    "change_id": "change:npc-mei-registered",
                    "transition_id": "transition:npc-mei-registered",
                    "expected_entity_revision": 0,
                    "evidence_refs": [
                        evidence(
                            "operator:npc-mei",
                            "operator_observation",
                            "current_fact",
                        )
                    ],
                    "policy_refs": ["policy:life-v1"],
                    "npc": mei.model_dump(mode="json"),
                },
            )
        ],
    )
    store.put_if_absent(
        StoredLifeContent(
            content_ref="identity:npc:mei",
            content_kind="provisional_npc_introduction",
            content_payload_hash=life_content_payload_hash("梅，角色在生活里认识的人。"),
            text="梅，角色在生活里认识的人。",
        )
    )

    result = await runtime.advance_once(
        wake_event_ref="clock-life", trace_id="trace", correlation_id="correlation"
    )

    assert result.status == "state_advanced"
    actor_payload = json.loads(actor.calls[0][1]["content"])
    assert actor_payload["authority"]["selected_npc_ref"] == "npc:lin"
    assert actor_payload["public_world"]["available_npc_refs"] == ["npc:lin"]
    assert actor_payload["public_world"]["available_location_refs"] == ["room:kitchen"]
    assert "identity:npc:mei" not in actor.calls[0][1]["content"]
    assert "protagonist_relationship" not in actor_payload["npc_actor_profile"]
    assert "shared_history_with_her" in actor_payload["npc_actor_profile"]


@pytest.mark.asyncio
async def test_npc_impulse_is_separately_adjudicated_and_enters_event_machine() -> None:
    ledger, _store, actor, world, runtime = _runtime(_actor("propose"), _world())

    result = await runtime.advance_once(
        wake_event_ref="clock-life", trace_id="trace", correlation_id="correlation"
    )

    assert result.status == "occurrence_committed"
    assert len(actor.calls) == 1
    assert len(world.calls) == 1
    world_payload = json.loads(world.calls[0][1]["content"])
    assert set(world_payload["world_capabilities"]) == {
        "participant_refs",
        "location_refs",
        "allowed_outcome_privacy",
    }
    assert world_payload["world_capabilities"]["participant_refs"] == ["npc:lin"]
    assert "affect" not in world.calls[0][1]["content"].lower()
    occurrence = next(
        item
        for item in ledger.project().world_occurrences
        if item.occurrence_id == result.occurrence_id
    )
    assert occurrence.status == "active"
    assert occurrence.participant_refs == ("npc:lin",)
    assert len(occurrence.candidate_outcomes) == 2
    assert ledger.project().npcs[0].subjective_state is not None


@pytest.mark.asyncio
async def test_solo_npc_occurrence_settles_without_opening_protagonist_appraisal() -> None:
    class MustNotConsider:
        async def consider(self, _opportunity):  # pragma: no cover - hard assertion
            raise AssertionError("a solo NPC event is not protagonist private experience")

    ledger, store, actor_model, _world_model, runtime = _runtime(
        _actor("propose"), _world()
    )
    # Retire the fixture's earlier occurrence so LifeAftermath reaches the
    # NPC-owned occurrence created below rather than that unrelated seed.
    commit(ledger, settlement_batch())
    npc_start = ledger.project().logical_time + timedelta(minutes=10)
    commit(
        ledger,
        [
            event(
                "clock-solo-npc-start",
                "ClockAdvanced",
                {
                    "logical_time_from": ledger.project().logical_time.isoformat(),
                    "logical_time_to": npc_start.isoformat(),
                },
                at=npc_start,
            )
        ],
    )
    actor_model.payload["source_refs"] = ["clock-solo-npc-start"]
    opened = await runtime.advance_once(
        wake_event_ref="clock-solo-npc-start",
        trace_id="trace:solo-npc",
        correlation_id="correlation:solo-npc",
    )
    occurrence = next(
        item
        for item in ledger.project().world_occurrences
        if item.occurrence_id == opened.occurrence_id
    )
    assert occurrence.participant_refs == ("npc:lin",)
    due = occurrence.time_window.closes_at + timedelta(seconds=1)
    commit(
        ledger,
        [
            event(
                "clock-solo-npc-settle",
                "ClockAdvanced",
                {
                    "logical_time_from": ledger.project().logical_time.isoformat(),
                    "logical_time_to": due.isoformat(),
                },
                at=due,
            )
        ],
    )
    aftermath = LifeAftermathRuntime(
        ledger=ledger,
        catalog=SimpleNamespace(),
        occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=store),
        content_store=store,
        owner_actor_ref="actor:companion",
        character_interior=MustNotConsider(),
        experience_memory_lifecycle=SimpleNamespace(_ledger=ledger),
    )

    settled = await aftermath.advance_once(
        wake_event_ref="clock-solo-npc-settle",
        trace_id="trace:solo-npc-settle",
        correlation_id="correlation:solo-npc-settle",
    )

    assert settled.status == "settled"
    assert not any(
        item.process_kind == "npc_world_appraisal"
        and item.source_evidence_ref
        == next(
            occurrence.settlement_event_ref
            for occurrence in ledger.project().world_occurrences
            if occurrence.occurrence_id == opened.occurrence_id
        )
        for item in ledger.project().trigger_processes
    )


@pytest.mark.asyncio
async def test_npc_cannot_bind_protagonist_without_character_decision() -> None:
    actor_payload = _actor("propose")
    actor_payload["proposal"] = {
        **actor_payload["proposal"],
        "participant_refs": ["actor:companion", "npc:lin"],
    }
    ledger, _store, actor, world, runtime = _runtime(actor_payload, _world())

    result = await runtime.advance_once(
        wake_event_ref="clock-life", trace_id="trace", correlation_id="correlation"
    )

    assert result.status == "technical_failure"
    assert result.reason_code == "npc_ecology.actor_invalid_after_repair"
    assert len(actor.calls) == 2
    assert "actor_participant_authority_failed" in actor.calls[1][-1]["content"]
    assert world.calls == []
    assert not any(
        "actor:companion" in item.participant_refs
        for item in ledger.project().world_occurrences
        if item.occurrence_id.startswith("occurrence:npc-ecology:")
    )


@pytest.mark.asyncio
async def test_world_author_gets_one_exact_reselection_then_technical_failure() -> None:
    bad_actor = _actor("propose")
    bad_actor["proposal"] = {
        **bad_actor["proposal"],
        "location_ref": "location:invented",
    }
    ledger, _store, _actor_model, world_model, runtime = _runtime(bad_actor, _world())

    result = await runtime.advance_once(
        wake_event_ref="clock-life", trace_id="trace", correlation_id="correlation"
    )

    assert result.status == "technical_failure"
    assert result.reason_code == "npc_ecology.actor_invalid_after_repair"
    assert len(_actor_model.calls) == 2
    assert "actor_location_closure_failed" in _actor_model.calls[1][-1]["content"]
    assert world_model.calls == []
    assert not any(
        item.occurrence_id.startswith("occurrence:npc-ecology:")
        for item in ledger.project().world_occurrences
    )

    retry = await runtime.advance_once(
        wake_event_ref="clock-life",
        trace_id="trace:retry",
        correlation_id="correlation:retry",
    )
    assert retry.status == "technical_failure"
    assert len(_actor_model.calls) == 4
    assert world_model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ambient_mass, actor_cap", [(0, 0), (10_000, 8)])
@pytest.mark.parametrize("crash_event_type", [None, "WorldOccurrenceCommitted", "WorldOccurrenceActivated"])
async def test_npc_can_form_its_own_future_plan_without_binding_protagonist(
    tmp_path, ambient_mass: int, actor_cap: int, crash_event_type: str | None
) -> None:
    actor_payload = _actor("propose")
    actor_payload["proposal"] = {
        "timing": "later",
        "premise": "林想周末留一段完整时间整理作品集。",
        "participant_refs": ["npc:lin"],
        "location_ref": "room:kitchen",
        "duration_minutes": 90,
        "visibility": "personal",
        "activity_kind": "整理实习作品集",
        "scheduled_start_after_minutes": 180,
        "importance_bp": 7200,
    }
    ledger_path = tmp_path / "npc-plan.sqlite"
    ledger, store, actor, world, runtime = _runtime(
        actor_payload, _world_plan(), ledger_path=ledger_path
    )
    commit(ledger, settlement_batch())

    result = await runtime.advance_once(
        wake_event_ref="clock-life", trace_id="trace", correlation_id="correlation"
    )

    assert result.status == "plan_committed"
    assert len(actor.calls) == 1
    assert len(world.calls) == 1

    plan = ledger.project().plans[-1]
    assert plan.owner_actor_ref == "npc:lin"
    assert plan.participant_refs == ("npc:lin",)
    assert plan.activity_kind == "整理实习作品集"
    assert plan.status == "planned"

    due = plan.scheduled_window.opens_at
    commit(
        ledger,
        [
            event(
                "clock-plan-due",
                "ClockAdvanced",
                {
                    "logical_time_from": ledger.project().logical_time.isoformat(),
                    "logical_time_to": due.isoformat(),
                },
                at=due,
            )
        ],
    )
    actor.payload["source_refs"] = ["clock-plan-due"]
    actor.payload["proposal"] = {
        "timing": "now",
        "premise": "林按计划开始整理作品集。",
        "participant_refs": ["npc:lin"],
        "location_ref": "room:kitchen",
        "duration_minutes": 90,
        "visibility": "personal",
    }
    world.payload = _world()

    due_result = await runtime.advance_once(
        wake_event_ref="clock-plan-due",
        trace_id="trace:due",
        correlation_id="correlation:due",
    )

    assert due_result.status == "state_advanced"
    assert due_result.reason_code == "npc_ecology.due_plan_started"
    projected_plan = next(item for item in ledger.project().plans if item.plan_id == plan.plan_id)
    assert projected_plan.status == "active"
    assert len(actor.calls) == 1
    assert len(world.calls) == 1
    rejected = await runtime.advance_once(
        wake_event_ref=projected_plan.authority_origin.accepted_event_ref,
        trace_id="trace:invalid-wake", correlation_id="correlation:invalid-wake",
    )
    assert rejected.reason_code == "npc_ecology.wake_not_exact_clock"
    assert len(world.calls) == 1

    # Restart at the accepted, active plan. Its existing intent must reach a
    # World-authored consequence without asking the NPC to invent another plan.
    before_restart = ledger.project()
    store.close()
    ledger.close()
    ledger = SQLiteWorldLedger(
        path=ledger_path, world_id=WORLD_ID, accepted_batch_issuer=AcceptedLedgerBatchIssuer()
    )
    store = SQLiteImmutableLifeContentStore(path=str(ledger_path), world_id=WORLD_ID)
    assert ledger.project() == before_restart
    restarted = NpcEcology(
        ledger=ledger,
        content_store=store,
        occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=store),
        actor_model=actor,
        world_author=world,
        protagonist_actor_ref="actor:companion",
        decision_opportunity_mass_bp=ambient_mass,
        weekly_actor_decision_cap=actor_cap,
    )
    assert restarted.has_due_work(projection=ledger.project())
    resolution_wake_ref = "clock-plan-due"
    if crash_event_type is not None:
        storage = _FailOnceLedger(ledger, crash_event_type)
        interrupted = NpcEcology(
            ledger=storage, content_store=store,
            occurrence_content=OccurrenceContentCoordinator(ledger=storage, store=store),
            actor_model=actor, world_author=world, protagonist_actor_ref="actor:companion",
        )
        with pytest.raises(OSError, match="injected ledger write failure"):
            await interrupted.advance_once(
                wake_event_ref="clock-plan-due", trace_id="trace:interrupt", correlation_id="correlation:interrupt"
            )
        assert len(world.calls) == 2
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(
            path=ledger_path, world_id=WORLD_ID, accepted_batch_issuer=AcceptedLedgerBatchIssuer()
        )
        store = SQLiteImmutableLifeContentStore(path=str(ledger_path), world_id=WORLD_ID)
        resumed_at = due + timedelta(hours=2)
        resolution_wake_ref = "clock-plan-recovery"
        commit(ledger, [event(
            resolution_wake_ref, "ClockAdvanced",
            {"logical_time_from": ledger.project().logical_time.isoformat(),
             "logical_time_to": resumed_at.isoformat()}, at=resumed_at,
        )])
        restarted = NpcEcology(
            ledger=ledger, content_store=store,
            occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=store),
            actor_model=actor, world_author=world, protagonist_actor_ref="actor:companion",
            decision_opportunity_mass_bp=ambient_mass, weekly_actor_decision_cap=actor_cap,
        )
    opened = await restarted.advance_once(
        wake_event_ref=resolution_wake_ref, trace_id="trace:resolve", correlation_id="correlation:resolve"
    )
    assert opened.status == ("occurrence_committed" if crash_event_type is None else "recovered"), opened
    occurrence = next(
        item for item in ledger.project().world_occurrences
        if item.occurrence_id == opened.occurrence_id
    )
    assert occurrence.trigger_ref == plan.plan_id
    assert occurrence.time_window.opens_at == due
    assert occurrence.time_window.closes_at == due + timedelta(minutes=90)
    assert occurrence.activated_at == due
    assert occurrence.participant_refs == ("npc:lin",)
    assert all(item.causal_authority == "world_contingency" for item in occurrence.candidate_outcomes)
    assert len(actor.calls) == 1
    assert len(world.calls) == 2
    resolution = json.loads(world.calls[-1][1]["content"])
    assert resolution["active_plan"]["plan_id"] == plan.plan_id
    assert resolution["npc_actor_decision"]["proposal"]["timing"] == "later"
    assert resolution["npc_actor_decision"]["proposal"]["premise"] == "林想周末留一段完整时间整理作品集。"

    await restarted.advance_once(
        wake_event_ref=resolution_wake_ref, trace_id="trace:duplicate", correlation_id="correlation:duplicate"
    )
    assert len(actor.calls) == 1
    assert len(world.calls) == 2

    settled_at = max(occurrence.time_window.closes_at, ledger.project().logical_time) + timedelta(seconds=1)
    commit(ledger, [event(
        "clock-plan-finished", "ClockAdvanced",
        {"logical_time_from": ledger.project().logical_time.isoformat(),
         "logical_time_to": settled_at.isoformat()}, at=settled_at,
    )])

    class MustNotConsider:
        async def consider(self, _opportunity):
            raise AssertionError("the protagonist did not witness this NPC plan")

    aftermath = LifeAftermathRuntime(
        ledger=ledger, catalog=SimpleNamespace(), content_store=store,
        occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=store),
        owner_actor_ref="actor:companion", character_interior=MustNotConsider(),
        experience_memory_lifecycle=SimpleNamespace(_ledger=ledger),
    )
    settled = await aftermath.advance_once(
        wake_event_ref="clock-plan-finished", trace_id="trace:settle", correlation_id="correlation:settle"
    )
    assert settled.status == "settled"
    completed = await restarted.advance_once(
        wake_event_ref="clock-plan-finished", trace_id="trace:complete", correlation_id="correlation:complete"
    )
    assert completed.status == "plan_completed"
    assert next(item for item in ledger.project().plans if item.plan_id == plan.plan_id).status == "completed"
    assert len(actor.calls) == 1
    assert len(world.calls) == 2
    assert ledger.export_replay_evidence().replay == ledger.project()
    repeated = await restarted.advance_once(
        wake_event_ref="clock-plan-finished", trace_id="trace:repeat", correlation_id="correlation:repeat"
    )
    assert repeated.status == "already_considered"
    assert len(actor.calls) == 1
    assert len(world.calls) == 2
    store.close()
    ledger.close()


@pytest.mark.asyncio
async def test_ambient_wake_without_occasion_draw_skips_actor_call() -> None:
    ledger, _store, actor, _world_model, runtime = _runtime(
        _actor("no_op"),
        {"decision": "no_op"},
    )
    runtime._decision_opportunity_mass_bp = 0

    result = await runtime.advance_once(
        wake_event_ref="clock-life",
        trace_id="trace",
        correlation_id="correlation",
    )

    assert result.status == "no_op"
    assert result.reason_code == "npc_ecology.occasion_not_drawn"
    assert actor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection_kind", ["critic", "world_changed"])
async def test_rejected_plan_consequence_remains_retryable(rejection_kind: str) -> None:
    actor_payload = _actor("propose")
    actor_payload["proposal"].update({
        "timing": "later", "activity_kind": "整理作品集",
        "scheduled_start_after_minutes": 30, "importance_bp": 5000,
    })
    ledger, store, actor, world, runtime = _runtime(actor_payload, _world_plan())
    await runtime.advance_once(wake_event_ref="clock-life", trace_id="trace", correlation_id="correlation")
    plan = ledger.project().plans[-1]
    due = plan.scheduled_window.opens_at
    commit(ledger, [event(
        "clock-rejected-result", "ClockAdvanced",
        {"logical_time_from": ledger.project().logical_time.isoformat(),
         "logical_time_to": due.isoformat()}, at=due,
    )])
    await runtime.advance_once(
        wake_event_ref="clock-rejected-result", trace_id="trace:start", correlation_id="correlation:start"
    )
    world.payload = _world()
    critic = _Model({})  # Two invalid critic responses reject the candidate.

    class ChangesWorld(_Model):
        async def complete_json(self, messages, *, temperature=0.2):
            changed_at = due + timedelta(minutes=1)
            commit(ledger, [event(
                "clock-world-changed", "ClockAdvanced",
                {"logical_time_from": ledger.project().logical_time.isoformat(),
                 "logical_time_to": changed_at.isoformat()}, at=changed_at,
            )])
            return await super().complete_json(messages, temperature=temperature)

    guarded = NpcEcology(
        ledger=ledger, content_store=store,
        occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=store),
        actor_model=actor,
        world_author=world if rejection_kind == "critic" else ChangesWorld(_world()),
        protagonist_actor_ref="actor:companion",
        user_channel_critic=critic if rejection_kind == "critic" else None,
    )
    rejected = await guarded.advance_once(
        wake_event_ref="clock-rejected-result", trace_id="trace:rejected", correlation_id="correlation:rejected"
    )
    assert rejected.status == ("technical_failure" if rejection_kind == "critic" else "stale_prefix")
    assert not any(item.trigger_ref == plan.plan_id for item in ledger.project().world_occurrences)
    assert next(item for item in ledger.project().plans if item.plan_id == plan.plan_id).status == "active"
    recovered = await runtime.advance_once(
        wake_event_ref="clock-rejected-result" if rejection_kind == "critic" else "clock-world-changed",
        trace_id="trace:retry", correlation_id="correlation:retry"
    )
    assert recovered.status == "occurrence_committed"
    assert len(actor.calls) == 1


@pytest.mark.asyncio
async def test_weekly_actor_cap_skips_without_queueing() -> None:
    ledger, _store, actor, _world_model, runtime = _runtime(
        _actor("no_op"),
        {"decision": "no_op"},
    )
    runtime._weekly_actor_decision_cap = 0

    result = await runtime.advance_once(
        wake_event_ref="clock-life",
        trace_id="trace",
        correlation_id="correlation",
    )

    assert result.status == "no_op"
    assert result.reason_code == "npc_ecology.weekly_decision_cap_exceeded"
    assert actor.calls == []


@pytest.mark.asyncio
async def test_npc_actor_profile_carries_forward_prior_inner_state() -> None:
    first_inner = "她上次说作品集最卡的一处已经理清了，但还想再核对一遍。"
    first_payload = _actor("no_op")
    first_payload["inner_state_summary"] = first_inner
    ledger, _store, actor, _world_model, runtime = _runtime(
        first_payload,
        {"decision": "no_op"},
    )

    first = await runtime.advance_once(
        wake_event_ref="clock-life",
        trace_id="trace",
        correlation_id="correlation",
    )
    assert first.status == "state_advanced"

    next_time = ledger.project().logical_time + timedelta(hours=3)
    commit(
        ledger,
        [
            event(
                "clock-life-2",
                "ClockAdvanced",
                {
                    "logical_time_from": ledger.project().logical_time.isoformat(),
                    "logical_time_to": next_time.isoformat(),
                },
                at=next_time,
            )
        ],
    )
    actor.payload = _actor("no_op")
    actor.payload["source_refs"] = ["clock-life-2"]

    second = await runtime.advance_once(
        wake_event_ref="clock-life-2",
        trace_id="trace:2",
        correlation_id="correlation:2",
    )
    assert second.status == "state_advanced"
    assert len(actor.calls) == 2
    second_profile = json.loads(actor.calls[1][1]["content"])["npc_actor_profile"]
    assert second_profile["my_last_state"] == first_inner
    first_profile = json.loads(actor.calls[0][1]["content"])["npc_actor_profile"]
    assert second_profile["my_last_state_at"] == first_profile["now"]["logical_time"]
    assert second_profile["my_goals"] == first_payload["current_goal_summaries"]


def test_npc_request_schema_distinguishes_event_evidence_from_entity_coordinates():
    from jsonschema import Draft202012Validator, ValidationError

    ledger, store, _, _, runtime = _runtime(_actor("no_op"), {"decision": "no_op"})
    from companion_daemon.world_v2.npc_ecology import NpcEcologyStimulus
    stimulus = NpcEcologyStimulus(
        cursor=_cursor_for_test(ledger), wake_event_ref="clock-life",
        source_event_refs=("clock-life",), epoch_ref="source-vocabulary",
    )
    snapshot = runtime.snapshot(_cursor_for_test(ledger))
    prompt, payload = runtime._actor_request(stimulus=stimulus, snapshot=snapshot)
    contract = json.loads(prompt[prompt.index('{"json_schema"'):])["json_schema"]
    allowed = payload["authority"]["input_event_refs"]
    plans = payload["npc_actor_profile"]["plan_context"]
    assert plans
    for row in plans:
        original = next(plan for plan in ledger.project().plans if plan.plan_id == row["plan_ref"])
        assert row["owner_actor_ref"] == original.owner_actor_ref
        assert row["status"] == original.status
        assert row["plan_ref"] in payload["npc_actor_profile"]["open_plans"]
    assert contract["properties"]["source_refs"]["items"]["enum"] == list(allowed)
    schema = contract["properties"]["source_refs"]
    Draft202012Validator(schema).validate([allowed[0]])
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(["room:kitchen"])


def _cursor_for_test(ledger):
    projection = ledger.project()
    return ProjectionCursor(world_revision=projection.world_revision,
                            deliberation_revision=projection.deliberation_revision,
                            ledger_sequence=projection.ledger_sequence)


def test_npc_request_schema_exposes_the_same_now_later_boundary_as_acceptance():
    from jsonschema import Draft202012Validator, ValidationError
    from companion_daemon.world_v2.npc_ecology import NpcEcologyStimulus

    ledger, _, _, _, runtime = _runtime(_actor("no_op"), {"decision": "no_op"})
    stimulus = NpcEcologyStimulus(cursor=_cursor_for_test(ledger), wake_event_ref="clock-life",
                                 source_event_refs=("clock-life",), epoch_ref="timing-shape")
    prompt, _ = runtime._actor_request(stimulus=stimulus, snapshot=runtime.snapshot(_cursor_for_test(ledger)))
    schema = json.loads(prompt[prompt.index('{"json_schema"'):])["json_schema"]["$defs"]["NpcActorProposal"]
    validator = Draft202012Validator(schema)
    now = {"timing": "now", "premise": "泡一壶茶", "participant_refs": ["npc:lin"],
           "location_ref": "room:kitchen", "duration_minutes": 20, "visibility": "personal"}
    validator.validate(now)
    with pytest.raises(ValidationError):
        validator.validate({**now, "activity_kind": "tea", "importance_bp": 3500})
    with pytest.raises(ValidationError):
        validator.validate({**now, "timing": "later"})
    validator.validate({**now, "timing": "later", "activity_kind": "tea",
                        "importance_bp": 3500, "scheduled_start_after_minutes": 30})


def test_npc_elapsed_schedule_is_explicit_without_fabricating_plan_completion():
    from companion_daemon.world_v2.npc_ecology import NpcEcologyStimulus

    ledger, _, _, _, runtime = _runtime(_actor("no_op"), {"decision": "no_op"})
    plan = next(item for item in ledger.project().plans if item.plan_id == "plan-tea")
    after = plan.scheduled_window.closes_at + timedelta(hours=1)
    commit(ledger, [event("clock-after-window", "ClockAdvanced", {
        "logical_time_from": ledger.project().logical_time.isoformat(), "logical_time_to": after.isoformat(),
    }, at=after)])
    stimulus = NpcEcologyStimulus(cursor=_cursor_for_test(ledger), wake_event_ref="clock-after-window",
                                 source_event_refs=("clock-after-window",), epoch_ref="expired-schedule")
    _, payload = runtime._actor_request(stimulus=stimulus, snapshot=runtime.snapshot(_cursor_for_test(ledger)))
    row = next(item for item in payload["npc_actor_profile"]["plan_context"] if item["plan_ref"] == plan.plan_id)
    assert row["window_state"] == "elapsed"
    assert row["status"] == plan.status == "planned"
    assert row["owner_actor_ref"] == plan.owner_actor_ref


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["actor", "world"])
async def test_npc_privacy_floor_is_offered_and_rejected_before_domain_commit(lane):
    actor_payload, world_payload = _actor("propose"), _world()
    if lane == "actor":
        actor_payload["proposal"]["visibility"] = "public"
    else:
        for outcome in world_payload["outcomes"]:
            outcome["privacy"] = "public"
    ledger, _, actor, world, runtime = _runtime(actor_payload, world_payload)
    before = len(ledger.project().world_occurrences)
    result = await runtime.advance_once(wake_event_ref="clock-life", trace_id="trace", correlation_id="privacy-test")
    assert result.status == "technical_failure"
    assert len(ledger.project().world_occurrences) == before
    request = json.loads(actor.calls[0][1]["content"])
    assert request["authority"]["allowed_visibility"] == ["personal", "private", "withhold"]
    if lane == "actor":
        assert len(actor.calls) == 2 and not world.calls
    else:
        assert len(actor.calls) == 1 and len(world.calls) == 2
        assert json.loads(world.calls[0][1]["content"])["world_capabilities"]["allowed_outcome_privacy"] == ["personal", "private", "withhold"]


@pytest.mark.asyncio
async def test_pre_fix_weak_actor_choice_is_not_reauthored_or_silently_upgraded(monkeypatch):
    payload = _actor("propose")
    payload["proposal"]["visibility"] = "public"
    ledger, _, actor, world, runtime = _runtime(payload, _world())
    before = len(ledger.project().world_occurrences)
    # Reproduce an already-recorded actor choice admitted by the old gate.
    with monkeypatch.context() as old:
        old.setattr(runtime, "_validate_actor_decision", lambda *args, **kwargs: None)
        result = await runtime.advance_once(wake_event_ref="clock-life", trace_id="trace", correlation_id="old-actor")
    assert result.reason_code == "npc_ecology.stored_actor_visibility_invalid"
    again = await runtime.advance_once(wake_event_ref="clock-life", trace_id="trace", correlation_id="recover-old-actor")
    assert again.reason_code == result.reason_code
    assert len(actor.calls) == 1 and not world.calls
    assert len(ledger.project().world_occurrences) == before
