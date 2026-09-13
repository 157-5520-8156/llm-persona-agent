"""Settled life reaches visible review through the original public source pin.

Only the provider transport is mocked.  The occurrence, response, Experience,
Capsule and review packet use their installed production consumers.
"""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.production import (
    compose_production_character_interior,
)
from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
from companion_daemon.world_v2.character_life_experience_runtime import (
    CharacterLifeExperienceRuntime,
)
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.production_turn_application import (
    WorldV2TurnApplicationConfig, build_sqlite_world_v2_turn_application,
)
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from companion_daemon.world_v2.visible_source_closure_protocol import (
    compact_source_reference_table, visible_source_closure_messages,
)
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from test_character_life_experience_runtime import _accepted_response, _cursor
from test_current_activity_context import current_context
from test_visible_source_composer import _request
from test_world_stimulus_life_intent import ACTOR, WORLD, _http_result
from test_production_turn_application import _DeliveredTransport, _Identities, _Router
from test_whole_candidate_author import _decision


class _LifeChatHTTP:
    """Fixed fixture verdict for an exact environmental sentence and source."""

    def __init__(self, settlement_ref, *, invalid_action=False, repeat_invalid=False, invalid_scope="past_world", later=False):
        self.settlement_ref = settlement_ref
        self.invalid_action = invalid_action
        self.repeat_invalid = repeat_invalid
        self.invalid_scope = invalid_scope
        self.later = later
        self.authors = []
        self.reviews = []
        self.usages = []

    async def __call__(self, request):
        body = json.loads(request.content)
        user = json.loads(body["messages"][-1]["content"])
        name = body["tool_choice"]["function"]["name"]
        if name.startswith("character_inbound_"):
            assert name in {"character_inbound_initial_v3", "character_inbound_final_atomic_v3"}
            assert "accepted_intention.text remains an intention" in body["messages"][0]["content"]
            assert "use only the exact accepted_intention.text" not in body["messages"][0]["content"]
            self.authors.append(body)
            candidate = _decision()
            candidate["expression_draft"]["beats"] = [
                {"modality": "text", "text": "刚才那阵短雨停了。"}
            ]
            candidate["expression_draft"]["world_claims"] = [{
                "claim_text": "一阵短雨已经停了。", "scope": "past_world",
                "source_refs": [self.settlement_ref],
            }]
            if self.later:
                candidate["expression_draft"].update(
                    timing_choice="later", delay_seconds=60, expires_after_seconds=600, stance="defer",
                )
            if self.invalid_action and (len(self.authors) == 1 or self.repeat_invalid):
                text = "我刚才把书收好了。" if self.invalid_scope == "past_world" else "我现在正在图书馆收书。"
                candidate["expression_draft"]["beats"][0]["text"] = text
                candidate["expression_draft"]["world_claims"][0].update(claim_text=text, scope=self.invalid_scope)
            return _http_result(body, {"result": candidate})
        assert name == "visible_beat_source_verdict_v4"
        self.reviews.append(body)
        rows = [
            dict(zip(group["columns"], cells, strict=True))
            for group in user["source_reference_tables"] for cells in group["rows"]
        ]
        source = next((row for row in rows if (
            row["source_ref"] == self.settlement_ref
            and row["support_eligibility"] == "eligible"
        )), None)
        # This fixture's semantic verdict is fixed for the two authored cases;
        # presence of a valid ref is deliberately insufficient to close it.
        supported = source is not None and user["visible_beats"][0]["text"] == "刚才那阵短雨停了。"
        decision = {
            "beat_index": 0, "verdict": "closed" if supported else "unclosed",
            "semantic_role": "external_proposition", "subject_role": "companion",
        }
        if supported:
            decision.update(
                first_source_ref_index=source["source_ref_index"],
                additional_source_ref_indexes=[],
            )
        return _http_result(body, {
            "contract": "visible-beat-source-verdict.4", "decisions": [decision],
            "rejections": [] if supported else [{
                "beat_index": 0, "char_start": 0,
                "char_end": len(user["visible_beats"][0]["text"]),
                "related_source_ref_indexes": [],
                "source_problem": "原始环境来源不能证明这句事实或角色已经完成的新动作。",
            }],
        })


@asynccontextmanager
async def _chat_application(path, provider, *, now):
    model = DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(provider),
        usage_observer=provider.usages.append,
    )
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
        update={"private_turn_state_mode": "required"}
    )
    interior = compose_production_character_interior(
        flash_model=model, thinking_model=None, source_closure_model=None,
        report_relative_source_closure_model=None, source_closure_reselection_lane=None,
        expression_episode_observer_model=None, flash_model_id=model.model,
        thinking_model_id=None, expression_capabilities=capabilities,
        identity_frame=CompanionIdentityFrame(companion_name="小满", counterpart_name="用户"),
        whole_candidate_mode=True, visible_source_review_model=model,
        atomic_tool_envelope_version="3", visible_source_review_version="4",
    )
    app = build_sqlite_world_v2_turn_application(
        path=path,
        config=WorldV2TurnApplicationConfig(
            world_id=WORLD, companion_actor_ref=ACTOR, reply_target="user:user.1",
            action_pump_owner="pump:visible-settled-life", character_memory_enabled=False,
            expression_capabilities=capabilities, visible_source_review_required=True,
        ),
        identities=_Identities(), router=_Router(), character_interior=interior,
        transport=_DeliveredTransport(), now=now,
    )
    try:
        yield app
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_public_settled_life_visible_to_author_is_transported_to_v4_review(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "settled-visible.sqlite"
    settlement_ref, response_ref = await _accepted_response(
        path, "雨停了，我觉得心里松了一点。"
    )
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        experience_id = CharacterLifeExperienceRuntime(
            ledger=ledger, content_store=store, owner_actor_ref=ACTOR,
        ).accept(
            world_id=WORLD, audit_cursor=_cursor(ledger),
            response_event_ref=response_ref,
        )
        capsule, _, snapshot = current_context(ledger, store, actor_ref=ACTOR)
        paired = next(
            item for item in snapshot.materials["recent_self_experiences"]["items"]
            if item.get("experience_id") == experience_id
        )
        assert paired["content"]["world_consequence"]["environment"]["text"] == (
            "一阵短雨已经停了。"
        )
        assert paired["content"]["character_response"]["source_event_ref"] == response_ref
        table = compile_visible_source_table(request=_request(capsule), capsule=capsule)
        packet = json.loads(visible_source_closure_messages(
            visible_beats=("刚才那阵短雨停了。",), world_claims=(),
            source_references=table.source_references(), version="4",
        )[1]["content"])
        rows = [
            dict(zip(group["columns"], cells, strict=True))
            for group in packet["source_reference_tables"] for cells in group["rows"]
        ]
        assert any(row["source_ref"] == settlement_ref for row in rows), (
            "author's settled life source disappeared before full visible review",
            table.as_dict()["unsupported_source_kinds"],
        )
        source_rows = table.source_references()
        settlement_row = next(row for row in source_rows if row["source_ref"] == settlement_ref)
        assert settlement_row["support_eligibility"] == "eligible"
        assert settlement_row["support_subject_ref"] == ACTOR
        assert settlement_row["settled_life_support"]["status"] == "settled"
        assert packet["settled_life_support_contract"]["contract"] == "visible-settled-life-source.1"
        _assert_typed_life_boundaries(settlement_row["review_material"], settlement_ref)

        # A composite's private author is visible as a reading, never fact
        # authority. The author's exact-source manifest makes the same split.
        from companion_daemon.world_v2.expression_draft import world_claim_source_refs_by_scope
        _, context, _ = current_context(ledger, store, actor_ref=ACTOR)
        scopes = world_claim_source_refs_by_scope(context=context)
        assert settlement_ref in scopes["past_world"]
        assert all(experience_id not in refs and response_ref not in refs for refs in scopes.values())
        readings = [row for row in source_rows if (
            row["review_material"].get("lane") == "recent_experiences"
        )]
        assert readings and all(row["support_eligibility"] == "baseline_only" for row in readings)
        for version in ("3", "4"):
            wire = json.loads(visible_source_closure_messages(
                visible_beats=("刚才那阵短雨停了。",), world_claims=(),
                source_references=source_rows, version=version,
            )[1]["content"])
            assert wire["settled_life_support_contract"]["contract"] == "visible-settled-life-source.1"

        # Public reader at the real earlier cursor: pending outcomes cannot
        # become source rows merely because their private candidate exists.
        from companion_daemon.world_v2.context_resolver import query_from_projection
        from companion_daemon.world_v2.ledger_context_resolver import (
            ContextRelevanceScope, context_capsule_compiler_from_ledger,
        )
        history = ledger.export_replay_evidence().events
        before = next(index for index, row in enumerate(history) if row.event.event_id == settlement_ref)
        pending = ledger.project_at(history[before - 1].cursor)
        class HistoricalLedger:
            def __getattr__(self, name):
                return getattr(ledger, name)

            def project(self):
                return pending

        pending_capsule = context_capsule_compiler_from_ledger(
            ledger=HistoricalLedger(), life_content_store=store,
            relevance_scope=ContextRelevanceScope(actor_ref=ACTOR),
        ).compile(query_from_projection(pending, actor_ref=ACTOR, trigger_ref="event:pending-test"))
        pending_table = compile_visible_source_table(request=_request(pending_capsule), capsule=pending_capsule)
        assert not any(row.get("settled_life_support") for row in pending_table.source_references())
        other, _, _ = current_context(ledger, store, actor_ref="actor:other")
        other_table = compile_visible_source_table(request=_request(other), capsule=other)
        assert not any(row.get("settled_life_support") for row in other_table.source_references())
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_public_v3_to_v4_host_can_say_exact_settled_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "visible-life-host.sqlite"
    settlement_ref, response_ref = await _accepted_response(path, "我觉得安静了一些。")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        CharacterLifeExperienceRuntime(
            ledger=ledger, content_store=store, owner_actor_ref=ACTOR,
        ).accept(
            world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref,
        )
        now = ledger.project().logical_time
    finally:
        store.close()
        ledger.close()
    provider = _LifeChatHTTP(settlement_ref)
    async with _chat_application(path, provider, now=now) as app:
        outcome = await app.respond(InboundTurn(
            platform="test", platform_user_id="user.1", platform_message_id="life-return",
            text="刚才外面怎么样？", observed_at=now, trace_id="trace:visible-life-return",
        ))
        original = json.loads(provider.authors[0]["messages"][-1]["content"])
        assert original["inner_life_snapshot"]["materials"]["recent_self_experiences"]["items"]
        assert provider.reviews, "test must reach the original full v4 review seam"
        assert outcome.status == "action_authorized", outcome
        assert (len(provider.authors), len(provider.reviews)) == (1, 1)
        evidence = app.export_replay_evidence()
        assert [payload.text for payload in evidence.projection.stored_message_payloads] == [
            "刚才那阵短雨停了。"
        ]
        _assert_real_audit_requires_original_observation(evidence)
        assert len(provider.usages) == 2
        for action_id in outcome.authorized_action_ids:
            await app.drain_action(action_id)
        completed = app.export_replay_evidence()
        assert all(action.state == "delivered" for action in completed.projection.actions)
    async with _chat_application(path, provider, now=now) as restarted:
        assert restarted.export_replay_evidence().events == completed.events
        duplicate = await restarted.respond(InboundTurn(
            platform="test", platform_user_id="user.1", platform_message_id="life-return",
            text="刚才外面怎么样？", observed_at=now, trace_id="trace:visible-life-return",
        ))
        assert set(duplicate.authorized_action_ids) <= set(outcome.authorized_action_ids)
        assert (len(provider.authors), len(provider.reviews), len(provider.usages)) == (1, 1, 2)
        assert restarted.export_replay_evidence().projection.actions == completed.projection.actions


def _assert_typed_life_boundaries(material, settlement_ref):
    from companion_daemon.world_v2.context_capsule import ResolvedSourceBinding, source_bindings_hash
    from companion_daemon.world_v2.visible_life_source import settled_life_source_support

    assert settled_life_source_support(material, settlement_ref) is not None
    subjects = {"companion_actor_ref": ACTOR, "counterpart_actor_ref": "user:user.1"}
    def refreshed(value):
        item = value["item"]
        item["value_hash"] = sha256(json.dumps(
            item["value"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        item["source_hash"] = source_bindings_hash(tuple(
            ResolvedSourceBinding.model_validate(row) for row in item["source_bindings"]
        ))
        return value

    for fault in ("actor", "availability", "withhold", "pending", "descriptor", "source_hash", "value_hash", "role_response"):
        bad = deepcopy(material)
        item = bad["item"]
        content = item["value"]["content"]
        if fault == "actor":
            bad["actor_ref"] = "actor:other"
        elif fault == "availability":
            bad["availability"] = "unavailable"
        elif fault == "withhold":
            bad["privacy_class"] = item["privacy_class"] = item["value"]["privacy_class"] = "withhold"
        elif fault == "pending":
            content["world_consequence"]["environment"]["epistemic_scope"] = "candidate_world_environment"
        elif fault == "descriptor":
            content["descriptor_event_ref"] = "event:unrelated-descriptor"
        elif fault == "role_response":
            content["character_response"] = {
                "source_event_ref": "event:private-response", "actor_ref": ACTOR,
                "response_text": "我已经收好了书。", "truncated": False,
                "epistemic_scope": "private_interpretation_not_world_fact",
            }
        refreshed(bad)
        if fault == "source_hash":
            item["source_hash"] = "0" * 64
        elif fault == "value_hash":
            item["value_hash"] = "0" * 64
        assert settled_life_source_support(bad, settlement_ref) is None, fault
        rows = compact_source_reference_table({"entries": (bad,), "subjects": subjects})
        assert all(row["support_eligibility"] == "baseline_only" for row in rows), fault
    assert settled_life_source_support(material, "event:unrelated") is None

    # A stricter selected privacy floor is legal; a weaker floor is not.
    private_floor = deepcopy(material)
    private_floor["item"]["value"]["privacy_class"] = "shareable"
    private_floor["item"]["value"]["content"]["privacy_class"] = "shareable"
    assert settled_life_source_support(refreshed(private_floor), settlement_ref) is not None
    weaker_floor = deepcopy(material)
    weaker_floor["privacy_class"] = weaker_floor["item"]["privacy_class"] = "shareable"
    assert settled_life_source_support(weaker_floor, settlement_ref) is None


def _assert_real_audit_requires_original_observation(evidence):
    from companion_daemon.world_v2.expression_plan_acceptance import (
        ExpressionPlanAcceptanceError, ExpressionPlanBudgetPolicy, derive_expression_plan_material,
    )
    from companion_daemon.world_v2.schemas import BudgetAccount, Observation, ProjectionCursor

    audit = next(row for row in evidence.projection.proposal_audits if (
        any(change["kind"] == "expression_plan_transition" for change in json.loads(row.proposal_json)["proposed_changes"])
    ))
    event = next(row.event for row in evidence.events if row.event.event_id == audit.trigger_ref)
    observation = Observation.model_validate_json(event.payload_json)
    kwargs = dict(
        audit=audit,
        cursor=ProjectionCursor(world_revision=audit.evaluated_world_revision, deliberation_revision=0, ledger_sequence=0),
        world_id=WORLD,
        policy=ExpressionPlanBudgetPolicy(
            account_id="account:world-v2:chat", amount_limit_per_action=10, actor=ACTOR,
            allowed_targets=("user:user.1",), recovery_policy="effect_once", visible_source_review_required=True,
        ),
        account=BudgetAccount(account_id="account:world-v2:chat", category="chat", window_id="test", limit=1000),
        logical_time=observation.logical_time, created_at=observation.created_at,
        trace_id="trace:offline-guard", correlation_id="correlation:offline-guard",
        source_observation=observation, model_result_audits=evidence.projection.model_result_audits,
    )
    assert derive_expression_plan_material(**kwargs).visible_source_review_hash
    for changes in ({"actor": "user:other"}, {"text": "不同的消息"}, {"observation_id": "observation:other"}, {"world_id": "world:other"}):
        with pytest.raises(ExpressionPlanAcceptanceError, match="event_share_claim_invalid"):
            derive_expression_plan_material(**{**kwargs, "source_observation": observation.model_copy(update=changes)})
    with pytest.raises(ExpressionPlanAcceptanceError, match="event_share_claim_invalid"):
        derive_expression_plan_material(**{**kwargs, "source_observation": None})
    with pytest.raises(ExpressionPlanAcceptanceError, match="visible_source_review_unavailable"):
        derive_expression_plan_material(**{**kwargs, "model_result_audits": ()})
    from companion_daemon.world_v2.proposal_envelope import CanonicalTypedPayload, validate_proposal_envelope
    from companion_daemon.world_v2.proposal_audit_schemas import canonical_json
    proposal = validate_proposal_envelope(json.loads(audit.proposal_json))

    def changed_audit(changed):
        return audit.model_copy(update={
            "trigger_ref": changed.trigger_ref, "proposal_hash": changed.proposal_hash,
            "proposal_json": canonical_json(changed.model_dump(mode="json")),
        })

    # Merely presenting a different trigger or an event-share intent does not
    # carry a verified ordinary inbound receipt into that new candidate.
    for changed in (
        proposal.model_copy(update={"trigger_ref": "event:unrelated-trigger"}),
        proposal.model_copy(update={"action_intents": tuple(
            intent.model_copy(update={"kind": "proactive_message"}) for intent in proposal.action_intents
        )}),
    ):
        with pytest.raises(ExpressionPlanAcceptanceError, match="visible_source_review_unavailable"):
            derive_expression_plan_material(**{**kwargs, "audit": changed_audit(changed)})

    changes = []
    for change in proposal.proposed_changes:
        if change.kind == "expression_plan_transition":
            payload = change.payload.value()
            payload.pop("visible_source_review_policy")
            change = change.model_copy(update={"payload": CanonicalTypedPayload.from_value(
                payload_schema="expression_plan_transition.v1", value=payload,
            )})
        changes.append(change)
    unreviewed = proposal.model_copy(update={"proposed_changes": tuple(changes)})
    with pytest.raises(ExpressionPlanAcceptanceError, match="event_share_claim_invalid"):
        derive_expression_plan_material(**{
            **kwargs, "audit": changed_audit(unreviewed), "model_result_audits": (),
            "policy": kwargs["policy"].model_copy(update={"visible_source_review_required": False}),
        })


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_invalid", [False, True])
@pytest.mark.parametrize("invalid_scope", ["past_world", "current_world"])
async def test_exact_settlement_does_not_close_invented_character_action(tmp_path, monkeypatch, repeat_invalid, invalid_scope):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "settled-action-scope.sqlite"
    settlement_ref, _ = await _accepted_response(path, "我刚才把书收好了。")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    now = ledger.project().logical_time
    ledger.close()
    provider = _LifeChatHTTP(
        settlement_ref, invalid_action=True, repeat_invalid=repeat_invalid, invalid_scope=invalid_scope,
    )
    async with _chat_application(path, provider, now=now) as app:
        before = app.export_replay_evidence()
        result = await app.respond(InboundTurn(
            platform="test", platform_user_id="user.1", platform_message_id="invalid-life-action",
            text="后来呢？", observed_at=now, trace_id="trace:invalid-life-action",
        ))
        assert (len(provider.authors), len(provider.reviews), len(provider.usages)) == (2, 2, 4)
        original = json.loads(provider.authors[0]["messages"][-1]["content"])
        corrected = json.loads(provider.authors[1]["messages"][-1]["content"])
        correction = corrected["role_result_correction"]["coordinate"]
        assert correction["failure_detail"]
        assert original["inner_life_snapshot"] == corrected["inner_life_snapshot"]
        assert original["expression_hard_boundaries"] == corrected["expression_hard_boundaries"]
        reviewed = json.loads(provider.reviews[0]["messages"][-1]["content"])
        bad_text = "我刚才把书收好了。" if invalid_scope == "past_world" else "我现在正在图书馆收书。"
        assert reviewed["visible_beats"][0]["text"] == bad_text
        assert reviewed["world_claims"][0] == {
            "claim_text": bad_text, "scope": invalid_scope, "source_refs": [settlement_ref],
        }
        after = app.export_replay_evidence()
        assert after.events[:len(before.events)] == before.events
        if repeat_invalid:
            assert result.status == "deferred" and not result.authorized_action_ids
            assert after.projection.actions == before.projection.actions
            assert not after.projection.stored_message_payloads
        else:
            assert result.status == "action_authorized"
            assert [item.text for item in after.projection.stored_message_payloads] == ["刚才那阵短雨停了。"]


@pytest.mark.asyncio
async def test_audited_settled_reply_cold_recovers_without_new_author_or_review(tmp_path, monkeypatch):
    from companion_daemon.world_v2.expression_plan_atomic_recorder import ExpressionPlanAtomicRecorder
    from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "settled-audited-cold.sqlite"
    settlement_ref, _ = await _accepted_response(path, "我觉得雨后的空气很舒服。")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    now = ledger.project().logical_time
    ledger.close()
    provider = _LifeChatHTTP(settlement_ref)
    inbound = InboundTurn(
        platform="test", platform_user_id="user.1", platform_message_id="cold-life-reply",
        text="刚才天气怎么样？", observed_at=now, trace_id="trace:cold-life-reply",
    )

    class CrashBeforeAcceptance(BaseException):
        pass

    def crash(*args, **kwargs):
        raise CrashBeforeAcceptance()

    async with _chat_application(path, provider, now=now) as app:
        with monkeypatch.context() as patch:
            patch.setattr(ExpressionPlanAtomicRecorder, "prepare_batch", crash)
            with pytest.raises(CrashBeforeAcceptance):
                await app.respond(inbound)
        audited = app.export_replay_evidence()
        assert not audited.projection.actions
        audit = next(row for row in audited.projection.proposal_audits if (
            "visible_source_review_policy" in row.proposal_json
        ))
        original_receipt = verify_recorded_candidate(
            audit=audit, model_result_audits=audited.projection.model_result_audits,
        )
        assert (len(provider.authors), len(provider.reviews), len(provider.usages)) == (1, 1, 2)
    async with _chat_application(path, provider, now=now) as rebuilt:
        assert rebuilt.export_replay_evidence().events == audited.events
        result = await rebuilt.respond(inbound)
        assert result.status == "action_authorized"
        recovered = rebuilt.export_replay_evidence()
        assert recovered.events[:len(audited.events)] == audited.events
        assert recovered.projection.model_result_audits == audited.projection.model_result_audits
        assert (len(provider.authors), len(provider.reviews), len(provider.usages)) == (1, 1, 2)
        assert verify_recorded_candidate(
            audit=audit, model_result_audits=recovered.projection.model_result_audits,
        ) == original_receipt
        assert len(recovered.projection.actions) == 1


@pytest.mark.asyncio
async def test_settled_reply_keeps_role_chosen_later_time_through_social_acceptance(tmp_path, monkeypatch):
    import companion_daemon.world_v2.social_action_worker as worker_module
    from companion_daemon.world_v2.expression_plan_acceptance import ExpressionPlanAcceptanceError
    original_derive = worker_module.derive_social_deferred_material
    calls = []

    def capture_derive(**kwargs):
        calls.append(kwargs)
        return original_derive(**kwargs)

    monkeypatch.setattr(worker_module, "derive_social_deferred_material", capture_derive)
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "settled-later.sqlite"
    settlement_ref, _ = await _accepted_response(path, "我喜欢雨后的安静。")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    now = ledger.project().logical_time
    ledger.close()
    provider = _LifeChatHTTP(settlement_ref, later=True)
    async with _chat_application(path, provider, now=now) as app:
        result = await app.respond(InboundTurn(
            platform="test", platform_user_id="user.1", platform_message_id="later-life-reply",
            text="一会儿有空再跟我说说天气吧。", observed_at=now, trace_id="trace:later-life-reply",
        ))
        evidence = app.export_replay_evidence()
        assert result.status == "deferred", result
        assert (len(provider.authors), len(provider.reviews), len(provider.usages)) == (1, 1, 2)
        (action,) = evidence.projection.actions
        assert action.kind == "followup"
        assert action.not_before == now + timedelta(seconds=60)
        assert action.expires_at == now + timedelta(seconds=600)
        assert len(evidence.projection.commitments) == 1
        assert len(evidence.projection.threads) == 1
        (kwargs,) = calls
        observation = kwargs["original_observation"]
        for changes in (
            {"actor": "user:other"}, {"text": "不同的消息"},
            {"observation_id": "observation:other"}, {"world_id": "world:other"},
        ):
            with pytest.raises(ExpressionPlanAcceptanceError, match="source_observation_invalid"):
                original_derive(**{**kwargs, "original_observation": observation.model_copy(update=changes)})
        with pytest.raises(ExpressionPlanAcceptanceError, match="event_share_claim_invalid"):
            original_derive(**{**kwargs, "original_observation": None})
        with pytest.raises(ExpressionPlanAcceptanceError, match="visible_source_review_unavailable"):
            original_derive(**{**kwargs, "model_result_audits": ()})
        with pytest.raises(ValueError, match="source event does not match proposal trigger"):
            original_derive(**{**kwargs, "source_observation_event_ref": "event:unbound-observation"})
