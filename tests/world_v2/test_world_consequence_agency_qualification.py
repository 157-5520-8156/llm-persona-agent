"""Public two-author life chains, not a real-critic semantic qualification.

The historical hail counterexample remains unchanged in its separate tree.
World Author/critic verdicts here are explicit fixtures; character calls use
the installed DeepSeek adapter with MockTransport and real SQLite acceptance.
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest
import test_world_stimulus_life_intent as intent_fixture
from test_life_development_runtime import _novel_origin_review, _seed_clock, _SequenceModel
from test_world_author_request_audit import _json, _ReceivedAuthor
from test_world_consequence_aftermath import (
    _aftermath,
    _assert_published_result,
    _NoCharacterCalls,
    _settled_author_cohort,
)
from test_world_consequence_producer import _advance, _assert_original_requests, _draft
from test_world_stimulus_life_intent import ACTOR, NOW, WORLD, _build, _http_result, _model
from test_world_stimulus_life_response import _ResponseHTTP, _settled

from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.life_content import LifeContentCompiler
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_capability import (
    ProjectionLifeCapabilityManifestCompiler,
)
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.recall_corpus import RecallCorpusCompiler, RecallCorpusSources
from companion_daemon.world_v2.recall_index import RecallCursor
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_life_context import WorldLifeContextCompiler
from companion_daemon.world_v2.world_turn_runtime import InboundTurn

INTENTION = "我想试着把桌面上的两本书叠在一起。"
ATTEMPT_RESULTS = ("两本书叠稳了，没有滑落。", "上面的书滑到一旁，两本书没有叠稳。")


class _AttemptAuthor(_ReceivedAuthor):
    def __init__(self, store, wake, source_ref):
        super().__init__(store, ())
        self.wake, self.source_ref, self.draft = wake, source_ref, None

    async def complete(self, messages, *, temperature=0.2):
        user = json.loads(messages[-1]["content"])
        bindings = user["execution_authority"]["execution_bindings"]
        if not bindings:
            self.outputs.append(_json({"decision": "no_op"}))
        else:
            binding = next(item for item in bindings if item["source_event_ref"] == self.source_ref)
            value = _draft(self.wake)
            value.update(
                premise="桌面上的书受到支撑与重力的影响。",
                location_ref=None,
                location_capability_ref=None,
                privacy_class="private",
            )
            value["claim_declarations"][0]["summary"] = "桌面与书之间的支撑产生客观变化。"
            for outcome, text in zip(value["outcomes"], ATTEMPT_RESULTS, strict=True):
                outcome.update(privacy_class="private", visual_evidence=None)
                outcome["world_consequence"] = {
                    "contract": "world-consequence.2",
                    "environment_text": "周围没有新的扰动。",
                    "authorized_attempt_result": {"text": text, "execution_binding": binding},
                }
            self.draft = value
            self.outputs.append(_json(value))
        return await super().complete(messages, temperature=temperature)


class _QualificationHTTP(_ResponseHTTP):
    async def __call__(self, request):
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"])
        if material.get("inner_turn", {}).get("purpose") is not None:
            return await super().__call__(request)
        self.requests.append(body)
        self.chat_requests.append(body)
        value = {
            "result_kind": "decision",
            "appraisal_draft": {
                "appraise": False,
                "brief_rationale": "这次询问不需要新的评价。",
                "behavior_tendency": "choose_own_response",
                "stance": "present",
                "display_strategy": "model_owned",
                "confidence": 6000,
            },
            "expression_draft": {
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "我想接住这次询问。",
                    "attended_source_refs": [],
                },
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "嗯，我在听。"}],
                "stance": "present",
                "brief_rationale": "先回应这次询问。",
                "world_claims": [],
            },
        }
        properties = body["tools"][0]["function"]["parameters"]["properties"]
        if set(properties) == {"result_kind", "payload_json"}:
            value = {
                "result_kind": "reply_only",
                "payload_json": json.dumps(
                    {
                        "messages": ["嗯，我在听。"],
                        "meaning_of_this": "我看见了这次询问。",
                        "my_state": "我想接住这次询问。",
                        "world_claims": [],
                    },
                    ensure_ascii=False,
                ),
            }
        return _http_result(body, {key: value.get(key) for key in properties})


def _cursor(state):
    return ProjectionCursor(
        world_revision=state.world_revision,
        deliberation_revision=state.deliberation_revision,
        ledger_sequence=state.ledger_sequence,
    )


async def _bootstrap(path):
    provider = _ResponseHTTP(fault="provider_failure")
    model = _model(provider)
    app = _build(path, model)
    try:
        assert app.export_replay_evidence().projection.plans == ()
        assert provider.requests == []
    finally:
        await app.aclose()
        await model.aclose()


def _paired_read(path, state, *, settlement_ref, response_ref, response_text, environment):
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        compiler = LifeContentCompiler(store=store)
        read = compiler.compile(
            projection=state,
            cursor=_cursor(state),
            actor_ref=ACTOR,
            viewer_privacy_ceiling="private",
        )
        (experience,) = read.experience_items
        assert experience.content.text is None
        assert experience.content.world_consequence.environment.text == environment
        assert experience.content.world_consequence.authorized_attempt_result is None
        response = experience.content.character_response
        assert response.source_event_ref == response_ref
        assert response.response_text == response_text
        assert response.epistemic_scope == "private_interpretation_not_world_fact"
        worlds = WorldLifeContextCompiler(life_content=compiler).compile(
            projection=state,
            cursor=_cursor(state),
            actor_ref=ACTOR,
            viewer_privacy_ceiling="private",
        )
        (world,) = worlds
        assert world.source.authority_event_ref == settlement_ref
        corpus = RecallCorpusCompiler().compile(
            sources=RecallCorpusSources(
                recent_experiences=read.experience_items, world_life=worlds
            ),
            cursor=RecallCursor(**_cursor(state).model_dump()),
            actor_ref=ACTOR,
            subject_refs=(ACTOR,),
        )
        assert [item.text for item in corpus if item.memory_kind == "episodic"] == [environment]
        reflective = [item for item in corpus if item.memory_kind == "reflective"]
        assert [item.text for item in reflective] == (
            [] if response_text is None else [response_text]
        )
        assert all(item.authority == "defeasible_interpretation" for item in reflective)
        return read, corpus
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("response_text", [None, "这一阵冰雹让我想安静地待一会儿。"])
async def test_unplanned_environment_gets_its_own_role_reading_in_chat_and_recall(
    tmp_path,
    monkeypatch,
    response_text,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "unplanned.sqlite"
    await _bootstrap(path)
    settlement_ref, raw_world = await _settled_author_cohort(
        path,
        world_id=WORLD,
        start=NOW + timedelta(minutes=1),
    )
    environment = json.loads(raw_world)["environment_text"]
    provider = _QualificationHTTP(text=response_text)
    model = _model(provider)
    app = _build(path, model)
    try:
        before = app.export_replay_evidence().projection
        assert before.plans == before.experiences == ()
        assert not any(
            ref.event_type.startswith("Activity") for ref in before.committed_world_event_refs
        )
        await app.drain_background_once()
        after = app.export_replay_evidence()
        responses = [
            row.event
            for row in after.events
            if row.event.event_type == "CharacterLifeResponseRecorded"
        ]
        assert len(responses) == len(provider.stimulus_requests) == 1
        response = responses[0]
        (experience,) = after.projection.experiences
        (binding,) = experience.values.source_bindings
        assert experience.authority_contract_version == "experience.2"
        assert binding.settlement.authority_event_ref == settlement_ref
        assert binding.response_event_ref == response.event_id
        assert binding.response.response_text == response_text
        assert after.projection.plans == ()
        read, corpus = _paired_read(
            path,
            after.projection,
            settlement_ref=settlement_ref,
            response_ref=response.event_id,
            response_text=response_text,
            environment=environment,
        )
        earlier_chat_calls = len(provider.chat_requests)
        outcome = await app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id="read-hail",
                text="刚才发生了什么，你自己怎么看？",
                observed_at=after.projection.logical_time,
                trace_id="trace:read-hail",
            )
        )
        assert outcome.status == "action_authorized"
        assert [
            request["tools"][0]["function"]["name"]
            for request in provider.chat_requests[earlier_chat_calls:]
        ] == ["character_inbound_initial_v1", "character_inbound_compact_gate_v2"]
        actual = json.loads(provider.chat_requests[-1]["messages"][-1]["content"])
        entries = actual["inner_life_snapshot"]["materials"]["recent_self_experiences"]["items"]
        paired = next(
            item for item in entries if item.get("experience_id") == experience.experience_id
        )
        assert paired["content"]["world_consequence"]["environment"]["text"] == environment
        assert paired["content"]["character_response"]["response_text"] == response_text
        assert "她及时收回了手账" not in json.dumps(actual, ensure_ascii=False)
    finally:
        await app.aclose()
        await model.aclose()

    cold_provider = _ResponseHTTP(fault="provider_failure")
    cold_model = _model(cold_provider)
    cold_app = _build(path, cold_model)
    try:
        state = cold_app.export_replay_evidence().projection
        cold_read, cold_corpus = _paired_read(
            path,
            state,
            settlement_ref=settlement_ref,
            response_ref=response.event_id,
            response_text=response_text,
            environment=environment,
        )
        assert cold_read == read
        assert [item.text for item in cold_corpus] == [item.text for item in corpus]
        assert cold_provider.requests == []
    finally:
        await cold_app.aclose()
        await cold_model.aclose()


@pytest.mark.asyncio
async def test_actual_started_role_attempt_reaches_production_authority_and_world_settlement(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    monkeypatch.setattr(intent_fixture, "INTENTION", INTENTION)
    path = tmp_path / "attempt.sqlite"
    provider = _ResponseHTTP(text=None, intent="choose")
    model = _model(provider)
    app = _build(path, model, ecology=True)
    try:
        await _settled(app)
        await app.drain_background_once()
        before = app.export_replay_evidence().projection
        (plan,) = before.plans
        assert plan.status == "planned"
        started_at = before.logical_time + timedelta(seconds=1)
        await app.tick(
            tick_id="start-books",
            logical_time_from=before.logical_time,
            logical_time_to=started_at,
            observed_at=started_at,
            trace_id="trace:start-books",
            causation_id="clock:start-books",
            correlation_id="books",
            reason="test_clock",
        )
        state = app.export_replay_evidence().projection
        (active,) = state.plans
        assert active.plan_id == plan.plan_id and active.status == "active"
        assert active.authority_origin.accepted_event_type == "ActivityStarted"
        source_ref = active.authority_origin.accepted_event_ref
        assert len(provider.stimulus_requests) == len(provider.lifecycle_requests) == 1
        assert INTENTION in json.dumps(provider.lifecycle_requests[0], ensure_ascii=False)
        prior_experiences = state.experiences
    finally:
        await app.aclose()
        await model.aclose()

    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        wake = _seed_clock(
            ledger,
            event_id="event:clock:attempt-author",
            logical_time_from=started_at,
            logical_time=started_at + timedelta(seconds=1),
        )
        catalog = ReviewedLifeSeedCatalog.from_yaml(
            path=Path(__file__).resolve().parents[2] / "configs/world_seed.yaml",
            chronology=LocalChronology("Asia/Shanghai"),
        )
        author = _AttemptAuthor(store, wake, source_ref)
        focused = _SequenceModel(
            model="fixture:stipulated-supported-critic",
            outputs=(_novel_origin_review(decision="supported"),),
        )
        runtime = LifeDevelopmentRuntime(
            ledger=ledger,
            content_store=store,
            world_author=author,
            character_interior=_SequenceModel(model="fixture:unused-choice", outputs=()),
            source_closure_reviewer=None,
            novel_origin_critic=focused,
            capsule_compiler=context_capsule_compiler_from_ledger(
                ledger=ledger,
                life_content_store=store,
                relevance_scope=ContextRelevanceScope(actor_ref=ACTOR),
            ),
            capability_manifest_compiler=ProjectionLifeCapabilityManifestCompiler(
                owner_actor_ref=ACTOR,
                catalog=catalog,
                content_store=store,
            ),
            owner_actor_ref=ACTOR,
        )
        result = await _advance(runtime, wake)
        assert len(author.received) == 1
        actual = json.loads(json.loads(author.received[0])[-1]["content"])
        bindings = actual["execution_authority"]["execution_bindings"]
        assert actual["capability_manifest"]["version"] == "life-development-capability.production.3"
        assert actual["capability_manifest"]["outcome_contract"] == "world-consequence.2"
        assert [item["source_event_ref"] for item in bindings] == [source_ref]
        (material,) = actual["execution_materials"]
        assert material["execution_binding"] == bindings[0]
        assert material["authorized_intention"]["text"] == INTENTION
        assert material["authorized_intention"]["model_result_ref"]
        assert source_ref in actual["capability_manifest"]["anchor_refs"]
        assert result.status == "occurrence_committed"
        assert focused.calls == 1
        reviewed = json.loads(focused.messages[0][-1]["content"])
        assert reviewed["pinned_authority"]["execution_authority"] == {
            "authority": actual["execution_authority"],
            "execution_materials": actual["execution_materials"],
        }
        assert [item["world_consequence"] for item in reviewed["reviewed_surface"]["outcomes"]] == [
            item["world_consequence"] for item in author.draft["outcomes"]
        ]
        _assert_original_requests(ledger, store, author)
        assert ledger.project().experiences == prior_experiences
        due = _seed_clock(
            ledger,
            event_id="event:clock:attempt-due",
            logical_time_from=wake.logical_time,
            logical_time=wake.logical_time + timedelta(minutes=20),
        )
        character = _NoCharacterCalls()
        closed = await _advance(_aftermath(ledger, store, character), due)
        assert closed.status == "settled" and closed.experience_id is None
        occurrence = next(
            item
            for item in ledger.project().world_occurrences
            if item.occurrence_id == result.occurrence_id
        )
        raw = _assert_published_result(ledger, store, occurrence, author.draft)
        consequence = json.loads(raw)
        assert consequence["authorized_attempt_result"]["execution_binding"] == bindings[0]
        assert consequence["authorized_attempt_result"]["text"] in ATTEMPT_RESULTS
        assert character.calls == 0
        assert ledger.project().experiences == prior_experiences
        assert (await _advance(_aftermath(ledger, store, character), due)).status == "no_op"
        assert len(author.received) == focused.calls == 1
    finally:
        store.close()
        ledger.close()
