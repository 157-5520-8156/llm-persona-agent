"""A persisted interjection must reach the role before old unsent beats."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.longitudinal_journey import CaptureDelivery, JourneyClock
from companion_daemon.world_v2.platform_host import WorldV2PlatformHost
from companion_daemon.world_v2.qq_c2c_host import QQC2CHost, qq_c2c_target
from companion_daemon.world_v2.qq_c2c_transport import QQC2CPlatformTransport
from companion_daemon.world_v2.qq_ingress_policy import QQIngressFragment, SQLiteQQIngressStore
from companion_daemon.world_v2.visible_independent_review_configuration import GroundedVisibleReviewer
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from test_production_turn_application import _Router, _config
from test_slim_atomic_author import _CurrentSlimHTTP, _slim
from world_v2_application import build_sqlite_world_v2_test_application, compose_fixture_character_interior


class _Identities:
    def resolve(self, *, platform, platform_user_id):
        assert (platform, platform_user_id) == ("qq", "10001")
        return "user:10001", qq_c2c_target("10001")


class _BlockedFirstCapture(CaptureDelivery):
    def __init__(self, clock):
        super().__init__(clock)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send_text(self, recipient_id, text):
        if not self.records:
            self.started.set()
            await self.release.wait()
        return await super().send_text(recipient_id, text)


@pytest.mark.asyncio
@pytest.mark.parametrize("restart_before_observation", [False, True])
@pytest.mark.parametrize("new_input", ["content", "two_contents", "commit_race", "claimed_recovery", "other_recipient", "control", "none"])
@pytest.mark.parametrize("cadence_mode", ["off", "later"])
async def test_pending_sqlite_ingress_holds_unsent_old_beats_until_observation(tmp_path, monkeypatch, restart_before_observation, new_input, cadence_mode):
    if cadence_mode == "later" and new_input != "content":
        pytest.skip("later gate audit uses the exact content interjection")
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "interruption.sqlite"
    now = datetime.now(UTC)
    clock = JourneyClock(now)
    capture = _BlockedFirstCapture(clock)
    first_payload = {**_slim(), "messages": ["先说这一句", "旧尾句一", "旧尾句二"]}
    if cadence_mode == "later":
        first_payload["later"] = 5
    http = _CurrentSlimHTTP(first_payload=first_payload)
    models = [DeepSeekChatModel("offline", "https://fixture.invalid", "fixture",
                               thinking_enabled=False, transport=httpx.MockTransport(http)) for _ in range(2)]
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(update={"private_turn_state_mode": "required"})
    author = _InboundCharacterAuthor(
        flash_model=models[0], expression_capabilities=capabilities,
        require_explicit_authored_decision_fields=True, whole_candidate_mode=True,
        atomic_tool_envelope_version="slim2", visible_source_review_model=GroundedVisibleReviewer(source_model=models[1]),
        visible_source_review_version="25",
    )
    async def advance(seconds):
        clock.current += timedelta(seconds=seconds)
        await asyncio.sleep(0)

    def build():
        app = build_sqlite_world_v2_test_application(
            path=path, config=replace(_config(), reply_target=qq_c2c_target("10001"),
                                      expression_capabilities=capabilities, expression_episode_mode="off",
                                      expression_action_kinds=capabilities.action_kinds, visible_source_review_required=True),
            identities=_Identities(), router=_Router(), character_interior=compose_fixture_character_interior(inbound_author=author),
            transport=QQC2CPlatformTransport(delivery=capture, recipients_by_target={qq_c2c_target("10001"): "10001"}, now=clock.now),
            now=now,
        )
        store = SQLiteQQIngressStore(path)
        host = QQC2CHost(host=WorldV2PlatformHost(application=app), recipient_id="10001", canonical_user_id="10001",
                         ingress_store=store, ingress_now=clock.now, ingress_sleep=advance,
                         action_due_now=clock.now, action_due_sleep=clock.timer_sleep)
        return app, store, host

    app, store, host = build()
    first = asyncio.create_task(host.inbound_text(message_id="first", recipient_id="10001", text="我想说一件事", observed_at=now))
    try:
        if cadence_mode == "later":
            await asyncio.wait_for(first, timeout=5)
            first_action = next(a for a in app._ledger.project().actions if a.expression_plan_id and not a.dependencies)
            current = await app.current_logical_time()
            due = current + timedelta(seconds=6)
            await app.tick(tick_id="tick:interruption", logical_time_from=current, logical_time_to=due,
                           observed_at=due, trace_id="trace:tick", causation_id="test:tick",
                           correlation_id="test:tick", reason="test", run_life_ecology=False)
            clock.current = due
            first = asyncio.create_task(app.drain_action(first_action.action_id))
        await asyncio.wait_for(capture.started.wait(), timeout=5)
        fragment = QQIngressFragment(source_event_id="newer", recipient_id=("10002" if new_input == "other_recipient" else "10001"),
                                     observed_at=clock.now(), content_shape=("control" if new_input == "control" else "text"),
                                     continuity_signal="unknown", text=(None if new_input == "control" else "我还没说完"),
                                     control_kind=("typing_started" if new_input == "control" else None))
        prepared = None
        if new_input == "other_recipient":
            store.submit(fragment, received_at=clock.now())
        elif new_input not in {"none", "commit_race"}:
            prepared = await host._prepare_inbound_fragment(fragment)
        if new_input == "commit_race":
            original_commit = app._ledger.commit

            def insert_before_dispatch_commit(events, **kwargs):
                if any(e.event_type == "ActionDispatchStarted" for e in events) and store.submission("newer") is None:
                    store.submit(fragment, received_at=clock.now())
                return original_commit(events, **kwargs)

            monkeypatch.setattr(app._ledger, "commit", insert_before_dispatch_commit)
        if new_input == "two_contents":
            extra = QQIngressFragment(source_event_id="newer-two", recipient_id="10001", observed_at=clock.now(),
                                      content_shape="text", continuity_signal="unknown", text="还有一件事")
            await host._prepare_inbound_fragment(extra)
            assert store.submission("newer-two").state == "pending"
        if new_input not in {"none", "commit_race"}:
            assert store.submission("newer").state == "pending"
        capture.release.set()
        await asyncio.wait_for(first, timeout=5)
        if new_input == "commit_race":
            assert store.submission("newer").state == "pending"
            prepared = await host._prepare_inbound_fragment(fragment)
        if new_input in {"none", "other_recipient", "control"}:
            assert [r["text"] for r in capture.records] == ["先说这一句", "旧尾句一", "旧尾句二"]
            return
        assert [r["text"] for r in capture.records] == ["先说这一句"]
        before = app._ledger.project()
        assert len([a for a in before.actions if a.expression_plan_id and a.state in {"authorized", "scheduled", "claimed"}]) == 2
        if new_input == "claimed_recovery":
            # Model a durable legacy claim whose owner died before dispatch.
            # The new ingress fence must preserve that exact lease on a
            # blocked reclaim, including across a cold app reopen.
            from companion_daemon.world_v2.action_pump import ActionPump

            runtime = app._turns._runtime
            pump = ActionPump(ledger=app._ledger, executor=runtime._action_executor,
                              settle=runtime.settle, owner_id=runtime._action_pump_owner)
            tail = next(a for a in before.actions if a.expression_plan_id and a.dependencies
                        and any(p.action_id in a.dependencies and p.state == "provider_accepted" for p in before.actions))
            if tail.state == "authorized":
                await pump._schedule(action=tail, projection=before)
                before = app._ledger.project()
                tail = next(a for a in before.actions if a.action_id == tail.action_id)
            claimed = await pump._claim_or_reclaim(action=tail, projection=before)
            assert claimed is not None
            current = await app.current_logical_time()
            due = current + timedelta(seconds=121)
            await app.tick(tick_id="tick:expired-claim", logical_time_from=current, logical_time_to=due,
                           observed_at=due, trace_id="trace:expired-claim", causation_id="test:claim",
                           correlation_id="test:claim", reason="test", run_life_ecology=False)
            clock.current = due
            before = app._ledger.project()
        if restart_before_observation:
            await host.aclose()
            app, store, host = build()
            assert store.submission("newer").state == "pending"
            prepared = await host._prepare_inbound_fragment(fragment)
            if new_input == "two_contents":
                assert store.submission("newer-two").state == "pending"
                await host._prepare_inbound_fragment(extra)
        # The passive scheduler uses the same atomic ingress fence.
        outcome = (await app.drain_action(claimed.action_id) if new_input == "claimed_recovery"
                   else await app.drain_actions_once())
        assert outcome.status == "deferred_ingress"
        if new_input == "claimed_recovery":
            retained = next(a for a in app._ledger.project().actions if a.action_id == claimed.action_id)
            assert retained.state == "claimed" and retained.claim_lease == claimed.claim_lease
        assert [r["text"] for r in capture.records] == ["先说这一句"]
        if cadence_mode == "later":
            wake_result = await host._wake_due_actions()
            assert wake_result.status == "deferred_ingress"
        assert prepared is not None
        await asyncio.wait_for(host._process_prepared_inbound_fragment(prepared), timeout=5)
        projection = app._ledger.project()
        assert any(p.process_kind == "expression_reconsideration" for p in projection.trigger_processes)
        assert [r["text"] for r in capture.records].count("先说这一句") == 1
        assert all(r["text"] not in {"旧尾句一", "旧尾句二"} for r in capture.records)
        # The newly recorded Observation opens the existing role-owned gates;
        # it never silently releases the previously authored tail.
        old_ids = [a.action_id for a in before.actions if a.expression_plan_id and a.state in {"authorized", "scheduled", "claimed"}]
        for action_id in old_ids:
            assert (await app.drain_action(action_id)).status == "idle"
        if cadence_mode == "off" and new_input == "content":
            from companion_daemon.world_v2.expression_reconsideration_runtime import ExpressionReconsiderationRuntime

            class ContinueReviewer:
                async def review(self, **_kwargs):
                    return "continue"

            app._character_interior._background_driver._reconsideration = ExpressionReconsiderationRuntime(
                ledger=app._ledger, owner_id="worker:test-continue", reviewer=ContinueReviewer(),
            )
            # Ordinary scheduler action/background lanes, not a targeted test
            # call, must eventually send the role-continued existing tail.
            for _ in range(8):
                await host.drain(max_action_units=8, max_background_units=8)
                if all(any(r["text"] == t for r in capture.records) for t in ("旧尾句一", "旧尾句二")):
                    break
            assert [r["text"] for r in capture.records].count("旧尾句一") == 1
            assert [r["text"] for r in capture.records].count("旧尾句二") == 1
        if new_input == "claimed_recovery":
            from companion_daemon.world_v2.expression_reconsideration_runtime import ExpressionReconsiderationRuntime

            class ResumeReviewer:
                async def review(self, **_kwargs):
                    return "continue"

            reconsider = ExpressionReconsiderationRuntime(ledger=app._ledger, owner_id="worker:test-recovery", reviewer=ResumeReviewer())
            assert (await reconsider.drain_one()).status == "continued"
            assert (await reconsider.drain_one()).status == "continued"
            assert (await app.drain_action(claimed.action_id)).status == "settled"
            assert (await app.drain_action(claimed.action_id)).status in {"owned_elsewhere", "idle"}
            assert [r["text"] for r in capture.records].count("旧尾句一") == 1
        if cadence_mode == "later":
            old_due_ids = {a.action_id for a in before.actions if a.action_id in old_ids and a.not_before is not None}
            assert old_due_ids
            wake_projection = await host._action_due_wake_projection()
            assert old_due_ids.isdisjoint({a.action_id for a in wake_projection.actions})

            # The same role may explicitly continue. The ordinary host drain
            # runs its background reconsideration and refreshes the timer;
            # no test-only manual timer refresh or fabricated text is used.
            from companion_daemon.world_v2.expression_reconsideration_runtime import ExpressionReconsiderationRuntime

            class ContinueReviewer:
                async def review(self, **_kwargs):
                    return "continue"

            app._character_interior._background_driver._reconsideration = ExpressionReconsiderationRuntime(
                ledger=app._ledger, owner_id="worker:test-reconsider", reviewer=ContinueReviewer(),
            )
            await host.drain(max_action_units=0, max_background_units=64)
            after = await host._action_due_wake_projection()
            assert old_due_ids.issubset({a.action_id for a in after.actions})
            assert host._action_due_wake._task is not None
            await asyncio.sleep(0)
            clock.advance(clock.now() + timedelta(seconds=1))
            for _ in range(100):
                if host.action_due_wake_diagnostics()["wake_count"]:
                    break
                await asyncio.sleep(0.01)
            assert host.action_due_wake_diagnostics()["wake_count"] >= 1
            # This later plan also has the independent stale-later guard after
            # the new Observation. A continue decision does not bypass it.
            assert all(r["text"] not in {"旧尾句一", "旧尾句二"} for r in capture.records)
    finally:
        capture.release.set()
        await host.aclose()
        await asyncio.gather(*(model.aclose() for model in models))


@pytest.mark.asyncio
async def test_qq_expression_without_ingress_store_retains_existing_dispatch(tmp_path, monkeypatch):
    import test_slim_atomic_author as fixture

    now = datetime.now(UTC)
    clock = JourneyClock(now)
    capture = CaptureDelivery(clock)
    transport = QQC2CPlatformTransport(delivery=capture, recipients_by_target={qq_c2c_target("10001"): "10001"}, now=clock.now)
    handler = _CurrentSlimHTTP(first_payload={**_slim(), "messages": ["甲", "乙", "丙"]})
    original_config = fixture._config
    monkeypatch.setattr(fixture, "_Identities", _Identities)
    monkeypatch.setattr(fixture, "_config", lambda: replace(original_config(), reply_target=qq_c2c_target("10001")))
    async with fixture._application(tmp_path, monkeypatch, handler, tool_version="slim2", review_version="25", transport=transport) as app:
        with app._ledger._thread_lock:
            assert app._ledger._connection.execute("SELECT 1 FROM sqlite_master WHERE name='world_v2_qq_ingress_fragments'").fetchone() is None
        result = await app.respond(InboundTurn(platform="qq", platform_user_id="10001", platform_message_id="no-store",
                                               text="我在", observed_at=now, trace_id="trace:no-store"))
        assert result.status == "action_authorized"
        for action_id in result.authorized_action_ids:
            assert (await app.drain_action(action_id)).status == "settled"
        assert [r["text"] for r in capture.records] == ["甲", "乙", "丙"]


@pytest.mark.asyncio
@pytest.mark.parametrize("restart_before_observation", [False, True])
@pytest.mark.parametrize("typing_first", [False, True])
@pytest.mark.parametrize("interruption_kind", ["content", "other_recipient", "control", "none"])
async def test_new_ingress_before_first_dispatch_fences_the_entire_old_plan(tmp_path, monkeypatch, restart_before_observation, typing_first, interruption_kind):
    """A later QQ fragment wins the SQLite transaction before the first handoff."""
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "first-beat-race.sqlite"
    now = datetime.now(UTC)
    clock = JourneyClock(now)
    capture = CaptureDelivery(clock)
    messages = ([{"typing": True}] if typing_first else []) + ["过时首句", "过时尾句"]
    handler = _CurrentSlimHTTP(first_payload={**_slim(), "messages": messages})
    models = [DeepSeekChatModel("offline", "https://fixture.invalid", "fixture",
                               thinking_enabled=False, transport=httpx.MockTransport(handler)) for _ in range(2)]
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(update={"private_turn_state_mode": "required"})
    author = _InboundCharacterAuthor(
        flash_model=models[0], expression_capabilities=capabilities,
        require_explicit_authored_decision_fields=True, whole_candidate_mode=True,
        atomic_tool_envelope_version="slim2", visible_source_review_model=GroundedVisibleReviewer(source_model=models[1]),
        visible_source_review_version="25",
    )

    async def advance(seconds):
        clock.current += timedelta(seconds=seconds)
        await asyncio.sleep(0)

    def build():
        app = build_sqlite_world_v2_test_application(
            path=path, config=replace(_config(), reply_target=qq_c2c_target("10001"),
                                      expression_capabilities=capabilities, expression_episode_mode="off",
                                      expression_action_kinds=capabilities.action_kinds, visible_source_review_required=True),
            identities=_Identities(), router=_Router(), character_interior=compose_fixture_character_interior(inbound_author=author),
            transport=QQC2CPlatformTransport(delivery=capture, recipients_by_target={qq_c2c_target("10001"): "10001"}, now=clock.now),
            now=now,
        )
        store = SQLiteQQIngressStore(path)
        host = QQC2CHost(host=WorldV2PlatformHost(application=app), recipient_id="10001", canonical_user_id="10001",
                         ingress_store=store, ingress_now=clock.now, ingress_sleep=advance,
                         action_due_now=clock.now, action_due_sleep=clock.timer_sleep)
        return app, store, host

    app, store, host = build()
    fragment = QQIngressFragment(source_event_id="newer", recipient_id=("10002" if interruption_kind == "other_recipient" else "10001"),
                                 observed_at=clock.now(), content_shape=("control" if interruption_kind == "control" else "text"),
                                 continuity_signal="unknown", text=(None if interruption_kind == "control" else "我还没有说完"),
                                 control_kind=("typing_started" if interruption_kind == "control" else None))
    original_commit = app._ledger.commit

    def insert_before_dispatch_commit(events, **kwargs):
        if interruption_kind != "none" and any(e.event_type == "ActionDispatchStarted" for e in events) and store.submission("newer") is None:
            store.submit(fragment, received_at=clock.now())
        return original_commit(events, **kwargs)

    monkeypatch.setattr(app._ledger, "commit", insert_before_dispatch_commit)
    try:
        await asyncio.wait_for(host.inbound_text(message_id="first", recipient_id="10001", text="我想说件事", observed_at=now), timeout=5)
        if interruption_kind != "content":
            assert [r["kind"] for r in capture.records] == (["typing"] if typing_first else []) + ["text", "text"]
            assert [r["text"] for r in capture.records if r["kind"] == "text"] == ["过时首句", "过时尾句"]
            return
        assert store.submission("newer").state == "pending"
        assert capture.records == [], "no stale head may start after new input won the transaction"
        before = app._ledger.project()
        old_ids = [a.action_id for a in before.actions if a.expression_plan_id and a.state in {"authorized", "scheduled", "claimed"}]
        assert len(old_ids) == len(messages)
        if not restart_before_observation and not typing_first:
            # A held expression must not starve an independent Action; the
            # real generic pump may skip the held head and commit another
            # effect with its own reservation and receipt.
            from companion_daemon.world_v2.action_pump import ActionPump
            from companion_daemon.world_v2.schemas import BudgetReservation, ProviderReceipt, WorldEvent

            independent = next(a for a in before.actions if a.action_id == old_ids[0]).model_copy(update={
                "action_id": "action:independent", "expression_plan_id": None, "expression_beat_id": None,
                "dependencies": (), "idempotency_key": "independent-action", "budget_reservation_id": "reservation:independent",
                "correlation_id": "independent", "state": "authorized", "claim_lease": None,
            })
            reservation = BudgetReservation(reservation_id="reservation:independent", account_id="account:world-v2:chat",
                                            action_id=independent.action_id, category="chat", amount_limit=1)

            def event(kind, payload):
                return WorldEvent.from_payload(schema_version="world-v2.1", event_id="event:test:" + kind,
                    world_id=app._ledger.world_id, event_type=kind, logical_time=before.logical_time, created_at=clock.now(),
                    actor="system:test", source="test", trace_id="trace:independent", causation_id="test:independent",
                    correlation_id="independent", idempotency_key="test:independent:" + kind, payload=payload)

            app._ledger.commit((event("BudgetReserved", {"reservation": reservation.model_dump(mode="json")}),
                                event("ActionAuthorized", {"action": independent.model_dump(mode="json")})),
                               expected_world_revision=before.world_revision, expected_deliberation_revision=before.deliberation_revision)

            class IndependentExecutor:
                async def dispatch(self, action):
                    assert action.action_id == independent.action_id
                    return ProviderReceipt(provider_receipt_id="receipt:independent", action_id=action.action_id,
                        idempotency_key=action.idempotency_key, provider="test", provider_ref="provider:independent",
                        status="delivered", artifact_refs=(), cost_actual=0, received_at=clock.now(), raw_payload_hash="sha256:independent")

                async def lookup_result(self, _action):
                    return None

            runtime = app._turns._runtime
            pump = ActionPump(ledger=app._ledger, executor=IndependentExecutor(), settle=runtime.settle,
                              owner_id=runtime._action_pump_owner)
            independent_result = await pump.drain_once()
            assert independent_result.status == "settled" and independent_result.action_id == independent.action_id
            assert capture.records == []
        if restart_before_observation:
            await host.aclose()
            app, store, host = build()
            assert store.submission("newer").state == "pending"
        # Passive scheduler and targeted callers see the same atomic fence.
        assert (await app.drain_action(old_ids[0])).status == "deferred_ingress"
        assert capture.records == []
        prepared = await host._prepare_inbound_fragment(fragment)
        await asyncio.wait_for(host._process_prepared_inbound_fragment(prepared), timeout=5)
        assert len([p for p in app._ledger.project().trigger_processes if p.process_kind == "expression_reconsideration"]) == len(messages)
        for action_id in old_ids:
            assert (await app.drain_action(action_id)).status == "idle"
        assert all(r["text"] not in {"过时首句", "过时尾句"} for r in capture.records)
    finally:
        await host.aclose()
        await asyncio.gather(*(model.aclose() for model in models))
