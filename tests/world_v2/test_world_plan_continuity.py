"""Explicit role choices on existing plans, never host semantic deduplication."""

from datetime import timedelta
import json
import pytest
from companion_daemon.world_v2.schemas import (
    DueWindow,
    EvidenceRef,
    OutcomeObservation,
    WorldOccurrenceProjection,
)
from companion_daemon.world_v2.occurrence_content_coordinator import (
    OccurrenceContentCommitRequest,
    OutcomeCandidateContent,
)
from test_world_stimulus_life_intent import (
    _RoleHTTP,
    _model,
    _build,
    _clock,
    _http_result,
    WORLD,
    ACTOR,
)


class PlanRole(_RoleHTTP):
    def __init__(self):
        super().__init__()
        self.operation = None
        self.plan_id = None
        self.candidate = None
        self.other_stimulus_requests = []

    async def __call__(self, request):
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"])
        purpose = material.get("inner_turn", {}).get("purpose")
        cap = material.get("capability_manifest", {})
        if (
            purpose == "world_stimulus_appraisal"
            and cap["payload"].get("process_kind") == "plan_disruption_appraisal"
        ):
            self.other_stimulus_requests.append(body)
            return _http_result(
                body,
                {
                    "status": "no_change",
                    "summary": "我没有额外的新安排。",
                    "attended_source_refs": cap["source_refs"],
                    "recall_query": None,
                    "decision": None,
                    "proposals": [
                        {
                            "proposal_type": "world_stimulus_appraisal_result",
                            "decision": "no_change",
                            "brief_rationale": "此刻无需另作改变。",
                            "behavior_tendency": "自行考虑。",
                            "stance": "平常",
                            "display_strategy": "withhold",
                            "confidence": 7000,
                            "meaning_candidates": None,
                            "attribution": None,
                            "severity": None,
                            "expiry": None,
                            "affect_transition": None,
                            "relationship_signal": None,
                            "aspiration_transition": None,
                            "experience_transition": None,
                            "life_intent": None,
                        }
                    ],
                },
            )
        if purpose == "outcome_selection":
            return _http_result(
                body,
                {
                    "status": "decision",
                    "summary": "选择已观察到的结果。",
                    "attended_source_refs": cap["source_refs"],
                    "recall_query": None,
                    "proposals": [],
                    "decision": {
                        "source_refs": cap["source_refs"],
                        "payload": {
                            "selected_token": self.candidate,
                            "adopt_proposed_life_direction": False,
                            "character_life_direction": None,
                        },
                    },
                },
            )
        if purpose == "world_stimulus_appraisal" and self.operation:
            self.stimulus_requests.append(body)
            management = cap["payload"].get("world_plan_management")
            assert management, (
                "Existing Plan responses are not offered; only another new life_intent is available"
            )
            option = next(x for x in management["plans"] if x["plan_id"] == self.plan_id)
            choice = {
                "operation": self.operation,
                "plan_id": option["plan_id"],
                "source_event_ref": self.source_ref,
            }
            if self.operation == "revise":
                choice["replacement"] = {
                    "execution_scope": "self_directed",
                    "intention": "改为稍后整理另一件自己的事情。",
                    "start_after_seconds": 300,
                    "duration_seconds": 180,
                    "importance_bp": 5000,
                }
            return _http_result(
                body,
                {
                    "status": "transition",
                    "summary": "我重新考虑了这项已有安排。",
                    "attended_source_refs": [self.source_ref],
                    "recall_query": None,
                    "decision": None,
                    "proposals": [
                        {
                            "proposal_type": "world_stimulus_appraisal_result",
                            "decision": "activate" if self.appraise else "no_change",
                            "brief_rationale": "这是我对原计划的选择。",
                            "behavior_tendency": "按自己的想法安排。",
                            "stance": "平常",
                            "display_strategy": "withhold",
                            "confidence": 7000,
                            "meaning_candidates": [
                                {"meaning": "我在重新考虑自己的安排。", "confidence": 7000}
                            ]
                            if self.appraise
                            else None,
                            "attribution": "situation" if self.appraise else None,
                            "severity": 3000 if self.appraise else None,
                            "expiry": None,
                            "affect_transition": None,
                            "relationship_signal": None,
                            "aspiration_transition": None,
                            "experience_transition": None,
                            "life_intent": None,
                            "plan_response": choice,
                        }
                    ],
                },
            )
        return await super().__call__(request)


async def settlement(app, provider, key):
    from companion_daemon.world_v2.life_content_store import life_content_payload_hash

    now = await app.current_logical_time()
    opened, considered = now + timedelta(minutes=1), now + timedelta(minutes=2)
    provider.candidate = "candidate:plan-continuity:" + key
    occurrence = "occurrence:plan-continuity:" + key
    await app.advance(_clock("seed-" + key, now, opened))
    await app.commit_occurrence(
        OccurrenceContentCommitRequest(
            world_id=WORLD,
            occurrence=WorldOccurrenceProjection(
                occurrence_id=occurrence,
                entity_revision=1,
                trigger_ref="trigger:" + key,
                participant_refs=(ACTOR,),
                location_ref=None,
                time_window=DueWindow(
                    opens_at=opened, closes_at=considered + timedelta(minutes=10)
                ),
                candidate_outcome_refs=(provider.candidate,),
                visibility="private",
                status="committed",
            ),
            candidate_contents=(
                OutcomeCandidateContent(
                    candidate_result_ref=provider.candidate,
                    result_id="result:" + key,
                    result_payload_ref="payload:" + key,
                    result_payload_hash=life_content_payload_hash("一阵短雨已经停了。"),
                    privacy_class="private",
                    content_ref="content:" + key,
                    text="一阵短雨已经停了。",
                ),
            ),
            change_id="change:" + key,
            transition_id="transition:" + key,
            evidence_refs=(
                EvidenceRef(
                    ref_id="clock:" + opened.isoformat(),
                    evidence_type="clock_observation",
                    claim_purpose="current_fact",
                ),
            ),
            logical_time=opened,
            created_at=opened,
            actor="system:offline-world",
            source="test",
            trace_id=key,
            causation_id=key,
            correlation_id=key,
        )
    )
    await app.advance(_clock("activate-" + key, opened, considered))
    await app.record_outcome_observation(
        OutcomeObservation(
            schema_version="world-v2.1",
            observation_id="observation:" + key,
            world_id=WORLD,
            logical_time=considered,
            created_at=considered,
            trace_id=key,
            causation_id=key,
            correlation_id=key,
            occurrence_id=occurrence,
            source_kind="committed_world_event",
            source_refs=("event:trigger:clock:activate-" + key,),
            observed_payload_ref="sensor:" + key,
            observed_payload_hash="b" * 64,
            observed_at=considered,
            confidence_bp=9500,
        )
    )
    await app.drain_background_once()
    provider.source_ref = next(
        x.settlement_event_ref
        for x in app.export_replay_evidence().projection.world_occurrences
        if x.occurrence_id == occurrence
    )
    return provider.source_ref


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["continue", "revise", "end"])
@pytest.mark.parametrize("appraise", [False, True])
async def test_existing_plan_choice_does_not_create_an_unrelated_plan(
    tmp_path, monkeypatch, operation, appraise
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = PlanRole()
    model = _model(provider)
    app = _build(tmp_path / "plans.sqlite", model)
    try:
        await settlement(app, provider, "first")
        await app.drain_background_once()
        original = app.export_replay_evidence().projection.plans[0]
        provider.operation = operation
        provider.appraise = appraise
        provider.plan_id = original.plan_id
        await settlement(app, provider, "next")
        result = await app.drain_background_once()
        assert result.work_status != "technical_failure", result
        plans = app.export_replay_evidence().projection.plans
        if operation == "continue":
            assert plans == (original,)
        elif operation == "end":
            assert len(plans) == 1 and plans[0].status == "abandoned"
        else:
            assert len(plans) == 2
            assert next(p for p in plans if p.plan_id == original.plan_id).status == "abandoned"
            replacement = next(p for p in plans if p.plan_id != original.plan_id)
            assert replacement.supersedes_plan_id == original.plan_id
            assert replacement.status == "planned"
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_identical_new_intentions_remain_two_explicit_choices(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = PlanRole()
    model = _model(provider)
    app = _build(tmp_path / "new-choices.sqlite", model)
    try:
        for key in ("first", "second"):
            await settlement(app, provider, key)
            await app.drain_background_once()
        plans = app.export_replay_evidence().projection.plans
        assert len(plans) == 2 and all(
            p.status == "planned" and p.supersedes_plan_id is None for p in plans
        )
        assert (
            len({p.activity_kind for p in plans}) == 1
        )  # Same words do not authorize host deduplication.
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_recovery_reads_the_same_plan_response_without_rehashing_every_event(tmp_path, monkeypatch):
    from companion_daemon.world_v2.character_interior import world_stimulus as module
    from companion_daemon.world_v2.character_interior.world_stimulus import CharacterInteriorWorldStimulusRuntime
    from companion_daemon.world_v2.character_interior.world_stimulus import decode_recorded_proposal

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = PlanRole()
    model = _model(provider)
    app = _build(tmp_path / "recovery-work.sqlite", model)
    try:
        await settlement(app, provider, "first")
        await app.drain_background_once()
        provider.operation = "continue"
        provider.plan_id = app.export_replay_evidence().projection.plans[0].plan_id
        await settlement(app, provider, "next")
        await app.drain_background_once()
        state = app.export_replay_evidence().projection
        audit = next(a for a in reversed(state.proposal_audits)
                     if any(c.kind == "world_plan_response" for c in
                            getattr(decode_recorded_proposal(a), "proposed_changes", ())))
        original = module._digest
        hashed = []

        def count(value):
            hashed.append(value)
            return original(value)

        monkeypatch.setattr(module, "_digest", count)
        assert not CharacterInteriorWorldStimulusRuntime._world_life_intent_is_pending(state, audit=audit)
        assert hashed == [audit.proposal_id], "The immutable receipt identity must not be hashed per historical event"
        assert app.export_replay_evidence().projection == state
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["continue", "revise", "end"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_paid_plan_choice_recovers_after_audit_without_reauthoring(
    tmp_path, monkeypatch, operation, legacy
):
    from companion_daemon.world_v2.character_interior.world_stimulus import (
        CharacterInteriorWorldStimulusRuntime,
    )
    import hashlib

    original_manifest = CharacterInteriorWorldStimulusRuntime._manifest

    async def old_paid_manifest(self, **kwargs):
        value = await original_manifest(self, **kwargs)
        payload = json.loads(value.payload_json)
        payload.pop("plan_response_contract", None)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return value.model_copy(
            update={
                "payload_json": raw,
                "payload_hash": "sha256:" + hashlib.sha256(raw.encode()).hexdigest(),
            }
        )

    if legacy:
        monkeypatch.setattr(CharacterInteriorWorldStimulusRuntime, "_manifest", old_paid_manifest)
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = PlanRole()
    model = _model(provider)
    path = tmp_path / "recover.sqlite"
    app = _build(path, model)
    await settlement(app, provider, "first")
    await app.drain_background_once()
    original = app.export_replay_evidence().projection.plans[0]
    provider.operation = operation
    provider.plan_id = original.plan_id
    await settlement(app, provider, "later")
    original_commit = app._ledger.commit_at_cursor
    failed = []

    def crash_once(events, **kwargs):
        if events[0].event_type == "WorldPlanResponseRecorded" and not failed:
            failed.append(True)
            raise RuntimeError("offline crash between paid audit and Plan effects")
        return original_commit(events, **kwargs)

    monkeypatch.setattr(app._ledger, "commit_at_cursor", crash_once)
    await app.drain_background_once()
    before = app.export_replay_evidence()
    assert failed and before.projection.plans == (original,)
    assert len(before.projection.pending_plan_responses) == (
        0 if legacy and operation == "continue" else 1
    )
    if operation != "continue":
        from companion_daemon.world_v2.life_ecology_activity import ActivityOpeningCatalog

        wake = next(
            x.event.event_id
            for x in reversed(before.events)
            if x.event.event_type == "ClockAdvanced"
        )
        openings = ActivityOpeningCatalog(owner_actor_ref=ACTOR).openings_for(
            projection=before.projection, wake_event_ref=wake
        )
        assert all(item.plan_id != original.plan_id for item in openings.openings)
    calls = len(provider.stimulus_requests)
    failed_ids = {
        x.event.event_id for x in before.events if x.event.event_type == "ModelResultRecorded"
    }
    app.close()
    monkeypatch.setattr(CharacterInteriorWorldStimulusRuntime, "_manifest", original_manifest)
    reopened = _build(path, model)
    try:
        for _ in range(8):
            await reopened.drain_background_once()
            if not reopened.export_replay_evidence().projection.pending_plan_responses:
                break
        after = reopened.export_replay_evidence()
        assert not after.projection.pending_plan_responses
        assert len(provider.stimulus_requests) == calls
        assert failed_ids <= {x.event.event_id for x in after.events}
        receipts = [
            x.event for x in after.events if x.event.event_type == "WorldPlanResponseRecorded"
        ]
        assert len(receipts) == 1
        snapshot = after.projection.plans
        for _ in range(2):
            await reopened.drain_background_once()
        assert reopened.export_replay_evidence().projection.plans == snapshot
        assert len(provider.stimulus_requests) == calls
        ledger = reopened._ledger
        head = ledger.project()
        assert ledger.rebuild() == head
    finally:
        reopened.close()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["continue_actor", "end_hash", "end_partial", "revise_unlinked"])
async def test_plan_response_effects_reject_forged_or_partial_authority(
    tmp_path, monkeypatch, attack
):
    from companion_daemon.world_v2.schemas import WorldEvent, ProjectionCursor

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = PlanRole()
    model = _model(provider)
    app = _build(tmp_path / "forged.sqlite", model)
    try:
        await settlement(app, provider, "first")
        await app.drain_background_once()
        original = app.export_replay_evidence().projection.plans[0]
        provider.operation = attack.split("_")[0]
        provider.plan_id = original.plan_id
        await settlement(app, provider, "test")
        original_commit = app._ledger.commit_at_cursor
        captured = []

        def capture(events, **kwargs):
            if events[0].event_type == "WorldPlanResponseRecorded":
                captured.append(tuple(events))
                raise RuntimeError("offline hold before effect transaction")
            return original_commit(events, **kwargs)

        monkeypatch.setattr(app._ledger, "commit_at_cursor", capture)
        await app.drain_background_once()
        assert captured
        monkeypatch.setattr(app._ledger, "commit_at_cursor", original_commit)
        events = list(captured[0])
        if attack == "continue_actor":
            events[0] = events[0].model_copy(update={"actor": "actor:foreign"})
        elif attack == "end_partial":
            events = events[:1]
        else:
            index = -1 if attack == "revise_unlinked" else 0
            event = events[index]
            body = event.payload()
            if attack == "end_hash":
                body["response_hash"] = "f" * 64
            else:
                body["plan"]["supersedes_plan_id"] = None
            envelope = event.model_dump(exclude={"payload_json", "payload_hash"})
            events[index] = WorldEvent.from_payload(**envelope, payload=body)
        ledger = app._ledger
        before = ledger.project()
        cursor = ProjectionCursor(
            world_revision=before.world_revision,
            deliberation_revision=before.deliberation_revision,
            ledger_sequence=before.ledger_sequence,
        )
        with pytest.raises(ValueError):
            ledger.commit_at_cursor(tuple(events), expected_cursor=cursor, commit_id="forged")
        assert ledger.project() == before
        ledger.commit_at_cursor(captured[0], expected_cursor=cursor, commit_id="exact-role-choice")
        assert not ledger.project().pending_plan_responses
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_repeated_revisions_keep_one_live_intention_and_readable_calendar_history(
    tmp_path, monkeypatch
):
    from companion_daemon.world_v2.world_plan_management import reader_for
    from companion_daemon.world_v2.dashboard_life_intention import read_dashboard_life_intention
    from companion_daemon.world_v2.dashboard_agenda import compile_dashboard_agenda, week_start_date
    from companion_daemon.world_v2.schemas import ProjectionCursor

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = PlanRole()
    model = _model(provider)
    app = _build(tmp_path / "versions.sqlite", model)
    try:
        await settlement(app, provider, "original")
        await app.drain_background_once()
        predecessor = app.export_replay_evidence().projection.plans[0]
        ids = [predecessor.plan_id]
        for key in ("revision-one", "revision-two"):
            provider.operation = "revise"
            provider.plan_id = predecessor.plan_id
            await settlement(app, provider, key)
            for _ in range(6):
                result = await app.drain_background_once()
                assert result.work_status != "technical_failure", result
                if (
                    next(
                        p for p in app._ledger.project().plans if p.plan_id == predecessor.plan_id
                    ).status
                    == "abandoned"
                ):
                    break
            projection = app.export_replay_evidence().projection
            live = [p for p in projection.plans if p.status in {"planned", "active", "paused"}]
            assert len(live) == 1
            replacement = live[0]
            assert replacement.supersedes_plan_id == predecessor.plan_id
            assert replacement.plan_id not in ids
            material = reader_for(app._ledger, ACTOR).read_for_plan(plan_id=replacement.plan_id)
            assert material.character_intention == "改为稍后整理另一件自己的事情。"
            for p in projection.plans:
                assert (
                    read_dashboard_life_intention(ledger=app._ledger, projection=projection, plan=p)
                    is not None
                )
            cursor = ProjectionCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            )
            agenda = compile_dashboard_agenda(
                ledger=app._ledger,
                projection=projection,
                cursor=cursor,
                store=None,
                actor_ref=ACTOR,
                week_start=week_start_date(projection.logical_time.date()),
                privacy_ceiling="private",
            )
            rows = [e for e in agenda.entries if e.kind == "plan"]
            assert len(rows) == len(ids) + 1
            assert all(e.content_state == "read" for e in rows)
            predecessor = replacement
            ids.append(replacement.plan_id)
        assert app._ledger.rebuild() == app._ledger.project()
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,operation", [("active", "end"), ("paused", "revise")])
async def test_existing_lifecycle_heads_are_valid_plan_response_targets(
    tmp_path, monkeypatch, status, operation
):
    from companion_daemon.world_v2.life_ecology_contract import LifeEcologyRunKey

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = PlanRole()
    model = _model(provider)
    app = _build(tmp_path / "lifecycle.sqlite", model, ecology=True)
    try:
        await settlement(app, provider, "original")
        await app.drain_background_once()
        plan = app._ledger.project().plans[0]
        now = await app.current_logical_time()
        for choice, seconds in [("start", 1)] + ([("pause", 301)] if status == "paused" else []):
            provider.lifecycle_choice = choice
            later = now + timedelta(seconds=seconds)
            await app.advance(_clock(choice, now, later))
            wake = "event:trigger:clock:" + choice
            ecology = app._life_ecology
            claim = await ecology._trigger_store.claim_or_join(
                key=LifeEcologyRunKey(
                    world_id=WORLD,
                    wake_event_ref=wake,
                    catalog_version=ecology.availability().catalog_version,
                ),
                trace_id=choice,
                correlation_id="plan-response",
            )
            await app._life_ecology._advance_activity_once(
                wake_event_ref=wake,
                trigger_id=claim.trigger_id,
                logical_time=later,
                trace_id=choice,
                correlation_id="plan-response",
                renewed_plan_catalog=True,
            )
            now = later
        assert app._ledger.project().plans[0].status == status
        provider.lifecycle_choice = "no_op"
        provider.operation = operation
        provider.plan_id = plan.plan_id
        await settlement(app, provider, "changed")
        for _ in range(6):
            result = await app.drain_background_once()
            assert result.work_status != "technical_failure", result
            if (
                next(p for p in app._ledger.project().plans if p.plan_id == plan.plan_id).status
                == "abandoned"
            ):
                break
        projection = app._ledger.project()
        assert next(p for p in projection.plans if p.plan_id == plan.plan_id).status == "abandoned"
        live = [p for p in projection.plans if p.status in {"planned", "active", "paused"}]
        assert len(live) == (1 if operation == "revise" else 0)
        assert app._ledger.rebuild() == projection
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("crash", [False, True])
@pytest.mark.parametrize("operation", ["continue", "revise", "end"])
@pytest.mark.parametrize("text", [None, "我想先自己考虑一下。"])
async def test_existing_plan_registry_does_not_drop_world_life_response(
    tmp_path, monkeypatch, operation, text, crash
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    import test_world_stimulus_life_response as response_fixture

    provider = response_fixture._ResponseHTTP(intent="choose", text=text)
    model = _model(provider)
    app = _build(tmp_path / "world.sqlite", model)
    selected = []
    original = response_fixture._http_result

    def submit(body, authored):
        packet = json.loads(body["messages"][-1]["content"])
        cap = packet.get("capability_manifest", {}).get("payload", {})
        if selected and cap.get("world_life_response") and cap.get("world_plan_management"):
            authored["proposals"][0]["plan_response"] = {
                "operation": operation,
                "plan_id": selected[0],
                "source_event_ref": cap["world_life_response"]["source_event_refs"][0],
            }
            if operation == "revise":
                authored["proposals"][0]["plan_response"]["replacement"] = {
                    "execution_scope": "self_directed",
                    "intention": "稍后再处理我自己的另一项安排。",
                    "start_after_seconds": 300,
                    "duration_seconds": 180,
                    "importance_bp": 5000,
                }
        return original(body, authored)

    monkeypatch.setattr(response_fixture, "_http_result", submit)
    try:
        await response_fixture._settled(app, name="first")
        await app.drain_background_once()
        before = app.export_replay_evidence()
        selected.append(before.projection.plans[0].plan_id)
        provider.intent = "null"
        settled = await response_fixture._settled(app, name="second")
        failed = []
        if crash:
            commit = app._ledger.commit_at_cursor

            def crash_response(events, **kwargs):
                if events[0].event_type == "CharacterLifeResponseRecorded" and not failed:
                    failed.append(True)
                    raise ValueError(
                        "offline crash between original paid audit and response receipt"
                    )
                return commit(events, **kwargs)

            monkeypatch.setattr(app._ledger, "commit_at_cursor", crash_response)
        result = await app.drain_background_once()
        if crash:
            assert failed and result.work_status == "technical_failure"
            await app.aclose()
            app = _build(tmp_path / "world.sqlite", model)
            for _ in range(8):
                result = await app.drain_background_once()
                if any(
                    x.event.event_type == "CharacterLifeResponseRecorded"
                    and x.event.payload()["origin"]["source_event_ref"] == settled.event_id
                    for x in app.export_replay_evidence().events
                ):
                    break
            target_calls = [
                call
                for call in provider.stimulus_requests
                if settled.event_id
                in json.loads(call["messages"][-1]["content"])
                .get("capability_manifest", {})
                .get("payload", {})
                .get("world_life_response", {})
                .get("source_event_refs", [])
            ]
            assert len(target_calls) == 1
        assert result.work_status != "technical_failure", result
        after = app.export_replay_evidence()
        assert any(
            x.event.event_type == "CharacterLifeResponseRecorded"
            and x.event.payload()["origin"]["source_event_ref"] == settled.event_id
            for x in after.events
        )
        old = before.projection.plans[0]
        plans = after.projection.plans
        if operation == "continue":
            assert plans == (old,)
        elif operation == "end":
            assert len(plans) == 1 and plans[0].status == "abandoned"
        else:
            assert (
                len(plans) == 2
                and next(x for x in plans if x.plan_id != old.plan_id).supersedes_plan_id
                == old.plan_id
            )
        target_receipts = [
            x.event
            for x in after.events
            if x.event.event_type == "CharacterLifeResponseRecorded"
            and x.event.payload()["origin"]["source_event_ref"] == settled.event_id
        ]
        assert len(target_receipts) == 1
        from companion_daemon.world_v2.character_life_response_runtime import (
            CharacterLifeResponseRuntime,
        )
        from companion_daemon.world_v2.schemas import ProjectionCursor

        head = app._ledger.project()
        runtime = CharacterLifeResponseRuntime(
            ledger=app._ledger, owner_actor_ref=target_receipts[0].actor
        )
        original_pin = app._ledger.lookup_event_commit(
            target_receipts[0].payload()["origin"]["proposal_event_ref"]
        )[1]
        runtime.accept(
            world_id=app._ledger.world_id,
            audit_cursor=ProjectionCursor(
                world_revision=original_pin.world_revision,
                deliberation_revision=original_pin.deliberation_revision,
                ledger_sequence=original_pin.ledger_sequence,
            ),
            proposal_id=target_receipts[0].payload()["origin"]["proposal_id"],
        )
        assert app._ledger.project() == head
        assert app._ledger.rebuild() == head
    finally:
        await app.aclose()
        await model.aclose()
