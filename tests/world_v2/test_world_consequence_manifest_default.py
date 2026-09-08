"""Production manifest migration at actual HTTP and durable recovery boundaries.

Provider responses are explicit fixtures, not semantic critic qualification.
"""

import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_context import compile_life_decision_context
from companion_daemon.world_v2.life_development_capability import (
    ProjectionLifeCapabilityManifestCompiler,
)
from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentCapabilityManifest,
    LifeDevelopmentLocationCapability,
    parse_world_author_draft,
)
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.life_development_source_closure import (
    life_development_source_closure_messages,
)
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_production import _open_life_seed
from test_life_development_runtime import (
    NOW,
    OWNER,
    WORLD_ID,
    _SequenceModel,
    _location_bound_world_draft,
    _novel_origin_review,
    _seed_clock,
)
from test_world_consequence_producer import _assert_occurrence, _assert_review_input
from test_world_stimulus_life_intent import _NoExternalActions, _http_result
from test_world_stimulus_life_response import _ResponseHTTP
from test_production_turn_application import _Identities, _Router


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _HTTP:
    def __init__(self, wake, *, legacy=False, causal_authority="world_contingency"):
        self.wake = wake
        self.legacy = legacy
        self.causal_authority = causal_authority
        self.requests = []
        self.draft = None

    def __call__(self, request):
        wire = json.loads(request.content)
        self.requests.append(wire)
        user = json.loads(wire["messages"][1]["content"])
        if "review_contract" in user:
            raw = _novel_origin_review(decision="supported")
        else:
            capability = LifeDevelopmentLocationCapability.model_validate_json(
                _json(
                    {
                        key: value
                        for key, value in user["capability_manifest"]["location_capabilities"][
                            0
                        ].items()
                        if key != "capability_ref"
                    }
                )
            )
            value = json.loads(
                _location_bound_world_draft(
                    wake=self.wake,
                    capability=capability,
                    timing={"mode": "now", "duration_minutes": 20},
                    privacy_class="shareable",
                    causal_authority=self.causal_authority,
                    outcome_resolution_authority=self.causal_authority,
                )
            )
            value["premise"] = "公园开始降下冰雹。"
            value["claim_declarations"][0]["summary"] = "公园出现冰雹天气。"
            for outcome in value["outcomes"]:
                outcome.pop("text")
                if self.legacy:
                    outcome["text"] = "冰雹打断了一截树枝。"
                else:
                    outcome["world_consequence"] = {
                        "contract": "world-consequence.2",
                        "environment_text": "冰雹打断了一截树枝。",
                    }
            self.draft = value
            raw = _json(value)
        return httpx.Response(
            200,
            json={
                "id": "offline-manifest",
                "model": wire["model"],
                "choices": [
                    {"message": {"role": "assistant", "content": raw}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            },
        )


def _capsule_compiler(ledger, store):
    return context_capsule_compiler_from_ledger(
        ledger=ledger,
        life_content_store=store,
        relevance_scope=ContextRelevanceScope(actor_ref=OWNER),
    )


def _composition(ledger, store, catalog, model, *, focused=True, compiler=None):
    return LifeDevelopmentRuntime(
        ledger=ledger,
        content_store=store,
        world_author=model,
        character_interior=_SequenceModel(model="unused-character", outputs=()),
        source_closure_reviewer=None,
        novel_origin_critic=model if focused else None,
        capsule_compiler=_capsule_compiler(ledger, store),
        capability_manifest_compiler=compiler
        or ProjectionLifeCapabilityManifestCompiler(
            owner_actor_ref=OWNER,
            catalog=catalog,
            content_store=store,
        ),
        owner_actor_ref=OWNER,
    )


def _catalog(tmp_path):
    return ReviewedLifeSeedCatalog.from_yaml(
        path=_open_life_seed(tmp_path / "seed.yaml"),
        chronology=LocalChronology("Asia/Shanghai"),
    )


class _LegacyManifest:
    """Explicit production.2 historical fixture, never a fresh author default."""

    def __init__(self, *, catalog, store):
        self.compiler = ProjectionLifeCapabilityManifestCompiler(
            owner_actor_ref=OWNER,
            catalog=catalog,
            content_store=store,
        )

    def compile(self, **kwargs):
        value = self.compiler.compile(**kwargs).model_dump(mode="json", round_trip=True)
        value.pop("outcome_contract", None)
        value["version"] = "life-development-capability.production.2"
        return LifeDevelopmentCapabilityManifest.model_validate_json(_json(value))


def _model(provider):
    return DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )


async def _advance(runtime, wake):
    return await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:manifest-default",
        correlation_id="correlation:manifest-default",
    )


@pytest.mark.asyncio
async def test_production_manifest_requests_current_consequences_through_http_and_acceptance(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "current.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    provider = _HTTP(wake)
    model = _model(provider)
    try:
        capsule = (
            _capsule_compiler(ledger, store)
            .compile_for_deliberation(
                query_from_projection(ledger.project(), actor_ref=OWNER, trigger_ref=wake.event_id)
            )
            .capsule
        )
        result = await _advance(_composition(ledger, store, _catalog(tmp_path), model), wake)
        user = json.loads(provider.requests[0]["messages"][1]["content"])
        assert user["capability_manifest"]["outcome_contract"] == "world-consequence.2"
        assert user["capability_manifest"]["version"] == "life-development-capability.production.3"
        _assert_occurrence(ledger, store, result, provider.draft)
        assert len(provider.requests) == 2  # author + existing focused lane only
        _assert_review_input(provider.requests[1]["messages"], user, provider.draft, focused=True)
        raw_messages = _json(provider.requests[0]["messages"])
        request_hash = hashlib.sha256(raw_messages.encode()).hexdigest()
        assert (
            store.read_exact(content_ref="content:world-author-request:" + request_hash).text
            == raw_messages
        )
        audits = [json.loads(item.audit_json) for item in ledger.project().model_result_audits]
        assert len(audits) == 3  # general closure retains its deterministic audit
        proposal = ledger.lookup_event_commit(result.proposal_event_ref)[0].payload()
        manifest = LifeDevelopmentCapabilityManifest.model_validate_json(
            _json(proposal["world_author_deliberation"]["capability_manifest"])
        )
        general_messages = life_development_source_closure_messages(
            context=compile_life_decision_context(capsule),
            manifest=manifest,
            draft=parse_world_author_draft(
                raw=_json(provider.draft), manifest=manifest, logical_time=wake.logical_time
            ),
            cited_events=(),
            execution_authority={
                "authority": user["execution_authority"],
                "execution_materials": user["execution_materials"],
            },
        )
        _assert_review_input(general_messages, user, provider.draft, focused=False)
        general_hash = hashlib.sha256(_json(general_messages).encode()).hexdigest()
        assert [
            (item["model_id"], item["request_hash"])
            for item in audits
            if item["model_id"] == "deterministic:life-source-closure"
        ] == [
            ("deterministic:life-source-closure", general_hash),
        ]
    finally:
        await model.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_current_production_manifest_cannot_accept_without_focused_critic(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "missing-critic.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    provider = _HTTP(wake)
    model = _model(provider)
    try:
        result = await _advance(
            _composition(ledger, store, _catalog(tmp_path), model, focused=False), wake
        )
        assert result.status == "technical_failure"
        assert result.reason_code == "life_development.world_consequence_critic_not_configured"
        assert len(provider.requests) == 1
        assert ledger.project().world_occurrences == ()
        assert ledger.project().plans == ()
    finally:
        await model.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_fresh_author_cannot_downgrade_to_legacy_outcome_text(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "fresh-downgrade.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    provider = _HTTP(wake, legacy=True)
    model = _model(provider)
    try:
        result = await _advance(_composition(ledger, store, _catalog(tmp_path), model), wake)
        assert result.status == "technical_failure"
        assert result.reason_code == "life_development.world_author_unavailable"
        assert len(provider.requests) == 2
        correction = json.loads(provider.requests[1]["messages"][-1]["content"])
        assert correction["validation_failure"]["code"] == "outcome_contract_mismatch"
        assert correction["capability_manifest"]["outcome_contract"] == "world-consequence.2"
        assert ledger.project().world_occurrences == ()
        assert ledger.project().plans == ()
    finally:
        await model.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_original_legacy_author_and_reviews_recover_without_new_http_or_protocol_upgrade(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "legacy.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    provider = _HTTP(wake, legacy=True)
    model = _model(provider)
    catalog = _catalog(tmp_path)
    try:
        commit = ledger.commit_at_cursor

        def interrupt_before_effect(events, **kwargs):
            if any(
                event.event_type == "ProposalRecorded"
                and event.payload().get("proposal_kind") == "life_development"
                for event in events
            ):
                raise InterruptedError("fixture: original author and reviews are durable")
            return commit(events, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(ledger, "commit_at_cursor", interrupt_before_effect)
            with pytest.raises(InterruptedError, match="original author and reviews"):
                await _advance(
                    _composition(
                        ledger,
                        store,
                        catalog,
                        model,
                        compiler=_LegacyManifest(catalog=catalog, store=store),
                    ),
                    wake,
                )
        assert len(provider.requests) == 2
        original_user = json.loads(provider.requests[0]["messages"][1]["content"])
        assert "outcome_contract" not in original_user["capability_manifest"]
        original_audits = tuple(item.audit_json for item in ledger.project().model_result_audits)
        assert ledger.project().world_occurrences == ()
        store.close()
        ledger.close()

        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        result = await _advance(_composition(ledger, store, catalog, model), wake)
        assert result.status == "occurrence_committed", result
        assert len(provider.requests) == 2
        assert (
            tuple(item.audit_json for item in ledger.project().model_result_audits)
            == original_audits
        )
        proposal = ledger.lookup_event_commit(result.proposal_event_ref)[0].payload()
        original_manifest = proposal["world_author_deliberation"]["capability_manifest"]
        visible_manifest = original_user["capability_manifest"]
        restored_manifest = LifeDevelopmentCapabilityManifest.model_validate_json(
            _json(original_manifest)
        )
        # Audit storage omits computed capability_ref; the public model dump
        # restores it from the original fields, never from the new compiler.
        assert restored_manifest.model_dump(mode="json") == {
            key: value for key, value in visible_manifest.items() if key != "manifest_hash"
        }
        assert restored_manifest.outcome_contract is None
        assert restored_manifest.manifest_hash == visible_manifest["manifest_hash"]
        assert proposal["capability_manifest_hash"] == visible_manifest["manifest_hash"]
        assert all(
            item.result_contract is None
            for item in ledger.project().world_occurrences[0].candidate_outcomes
        )
    finally:
        await model.aclose()
        store.close()
        ledger.close()


class _PlanHTTP(_HTTP):
    def __init__(self, wake):
        super().__init__(wake, causal_authority="character_choice")
        self.role_requests = []
        self.lifecycle_choice = "start"
        self.response = _ResponseHTTP(text="我觉得这段安静很难得。")

    async def __call__(self, request):
        wire = json.loads(request.content)
        material = json.loads(wire["messages"][-1]["content"])
        purpose = material.get("inner_turn", {}).get("purpose")
        if purpose == "world_stimulus_appraisal":
            return await self.response(request)
        if purpose is None:
            if self.draft is not None and "review_contract" not in material:
                self.requests.append(wire)
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": '{"decision":"no_op"}'},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 10,
                            "total_tokens": 110,
                        },
                    },
                )
            return super().__call__(request)
        self.role_requests.append(wire)
        capability = material["capability_manifest"]
        if purpose == "life_development_choice":
            payload = {
                "completion": {
                    "decision": "accept",
                    "intention_summary": "我想在公园安静地待十分钟。",
                    "importance_bp": 4300,
                    "opens_at": self.wake.logical_time.isoformat(),
                    "closes_at": (self.wake.logical_time + timedelta(minutes=10)).isoformat(),
                    "participant_refs": [],
                }
            }
        elif purpose == "activity_lifecycle_choice":
            prefix = (
                "begin an abstract planned activity"
                if self.lifecycle_choice == "start"
                else "finish the current abstract activity"
            )
            selected = next(
                (
                    item
                    for item in capability["payload"]["openings"]
                    if item["safe_summary"].startswith(prefix)
                ),
                None,
            )
            payload = (
                {"decision": "select", "selected_token": selected["opening_token"]}
                if selected
                else {"decision": "no_op"}
            )
        elif purpose == "outcome_selection":
            payload = {
                "selected_token": capability["payload"]["offered_tokens"][0],
                "adopt_proposed_life_direction": False,
                "character_life_direction": None,
            }
        else:
            raise AssertionError("unexpected character purpose: " + purpose)
        return _http_result(
            wire,
            {
                "status": "decision",
                "summary": "我选择这项变化。",
                "attended_source_refs": [],
                "recall_query": None,
                "proposals": [],
                "decision": {"source_refs": capability["source_refs"], "payload": payload},
            },
        )


def _plan_app(path, model, seed):
    from companion_daemon.world_v2.character_interior.production import (
        compose_production_character_interior,
    )
    from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
    from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
    from companion_daemon.world_v2.production_turn_application import (
        LifeEcologyComposition,
        WorldV2TurnApplicationConfig,
        build_sqlite_world_v2_turn_application,
    )

    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
        update={"private_turn_state_mode": "required"}
    )
    interior = compose_production_character_interior(
        flash_model=model,
        thinking_model=None,
        source_closure_model=None,
        report_relative_source_closure_model=None,
        source_closure_reselection_lane=None,
        expression_episode_observer_model=None,
        flash_model_id=model.model,
        thinking_model_id=None,
        expression_capabilities=capabilities,
        identity_frame=CompanionIdentityFrame(companion_name="小满", counterpart_name="用户"),
    )
    return build_sqlite_world_v2_turn_application(
        path=path,
        config=WorldV2TurnApplicationConfig(
            world_id=WORLD_ID,
            companion_actor_ref=OWNER,
            reply_target="user:user.1",
            action_pump_owner="pump:manifest-plan",
            character_memory_enabled=False,
            expression_capabilities=capabilities,
            life_ecology=LifeEcologyComposition.production_v1(seed_catalog_path=seed),
        ),
        identities=_Identities(),
        router=_Router(),
        character_interior=interior,
        transport=_NoExternalActions(),
        life_world_author_model=model,
        life_source_closure_reviewer=model,
        now=NOW,
    )


async def _tick(app, name, before, after):
    await app.tick(
        tick_id=name,
        logical_time_from=before,
        logical_time_to=after,
        observed_at=after,
        trace_id="trace:" + name,
        causation_id="clock:" + name,
        correlation_id="manifest-plan",
        reason="offline qualification",
        run_life_ecology=False,
    )
    return await app.advance_life_ecology_once(
        wake_event_ref="event:trigger:clock:" + name,
        trace_id="trace:" + name,
        correlation_id="manifest-plan",
    )


@pytest.mark.asyncio
async def test_current_production_character_choice_plan_preserves_consequences_after_start(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    selected_at = NOW + timedelta(minutes=1)
    provider = _PlanHTTP(
        SimpleNamespace(event_id="event:trigger:clock:choose-plan", logical_time=selected_at)
    )
    model = _model(provider)
    app = _plan_app(tmp_path / "plan.sqlite", model, _open_life_seed(tmp_path / "plan-seed.yaml"))
    try:
        result = await _tick(app, "choose-plan", NOW, selected_at)
        assert result.life_development_followup_status == "plan_committed", result
        evidence = app.export_replay_evidence()
        (plan,) = evidence.projection.plans
        assert plan.scheduled_window.opens_at == selected_at
        assert plan.scheduled_window.closes_at == selected_at + timedelta(minutes=10)
        (choice,) = [
            body
            for body in provider.role_requests
            if json.loads(body["messages"][-1]["content"])["inner_turn"]["purpose"]
            == "life_development_choice"
        ]
        opportunity = json.loads(choice["messages"][-1]["content"])["capability_manifest"][
            "payload"
        ]["external_opportunity"]
        assert all(
            item["world_consequence"]["contract"] == "world-consequence.2"
            for item in opportunity["outcomes"]
        )
        started_at = selected_at + timedelta(minutes=1)
        await _tick(app, "start-plan", selected_at, started_at)
        evidence = app.export_replay_evidence()
        assert evidence.projection.plans[0].status == "active"
        assert any(row.event.event_type == "ActivityStarted" for row in evidence.events)
        (occurrence,) = evidence.projection.world_occurrences
        assert all(
            item.result_contract == "world-consequence.2" for item in occurrence.candidate_outcomes
        )
        assert evidence.projection.experiences == ()
        provider.lifecycle_choice = "complete"
        await _tick(app, "finish-plan", started_at, plan.scheduled_window.closes_at)
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        assert evidence.projection.plans[0].status == "completed"
        (occurrence,) = evidence.projection.world_occurrences
        assert occurrence.status == "settled"
        outcome_requests = [
            body
            for body in provider.role_requests
            if json.loads(body["messages"][-1]["content"])["inner_turn"]["purpose"]
            == "outcome_selection"
        ]
        assert len(outcome_requests) == 1
        (experience,) = evidence.projection.experiences
        assert experience.authority_contract_version == "experience.2"
        (binding,) = experience.values.source_bindings
        assert binding.source_kind == "world_life_response"
        assert binding.settlement.authority_event_ref == occurrence.settlement_event_ref
        assert binding.response.response_text == "我觉得这段安静很难得。"
        assert len(provider.response.stimulus_requests) == 1
        types = [row.event.event_type for row in evidence.events]
        assert (
            types.count("CharacterLifeResponseRecorded") == types.count("ExperienceCommitted") == 1
        )
        before_calls = (
            len(provider.requests),
            len(provider.role_requests),
            len(provider.response.requests),
        )
        before = evidence.projection
        await app.aclose()
        app = _plan_app(tmp_path / "plan.sqlite", model, tmp_path / "plan-seed.yaml")
        await app.drain_background_once()
        assert app.export_replay_evidence().projection == before
        assert (
            len(provider.requests),
            len(provider.role_requests),
            len(provider.response.requests),
        ) == before_calls
    finally:
        await app.aclose()
        await model.aclose()
