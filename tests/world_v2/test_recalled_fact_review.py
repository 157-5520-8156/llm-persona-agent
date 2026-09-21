"""Real Fact/recall/Core/review/receipt wiring with offline model judgments."""

from dataclasses import replace
import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_wire import _ExpressionDraftWire
from companion_daemon.world_v2.character_interior.production import compose_production_character_interior
from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy, SliceBudget
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.production_turn_application import build_sqlite_world_v2_turn_application
from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.visible_independent_review_runtime import IndependentVisibleReviewer
from companion_daemon.world_v2.visible_source_runtime import (
    requirement_table, result_recall_audits, verify_recorded_candidate,
)
from companion_daemon.world_v2.visible_recall_sources import supplement_recalled_sources
from test_production_turn_application import (
    NOW, _build_application, _config, _DeliveredTransport, _DraftChatModel,
    _FactChat, _Identities, _Router,
)
from test_whole_candidate_author import _decision, _inbound
from test_world_stimulus_life_intent import _http_result


VALUE = "乌龙茶"
OBSERVATION = "我最近很喜欢喝乌龙茶，昨天还给同事送了一杯。"
REPLY = "你喜欢喝乌龙茶。"


async def _seed_fact(path, transport):
    """Produce the source Observation, accepted Fact and hashes through runtime."""
    app = _build_application(
        path=path, config=replace(_config(), character_memory_enabled=False),
        identities=_Identities(), router=_Router(),
        inbound_author=_ExpressionDraftWire(model=_DraftChatModel()), fact_model=_FactChat(),
        transport=transport, now=NOW,
    )
    try:
        assert (await app.respond(replace(
            _inbound(), platform_message_id="message:fact-source", text=OBSERVATION,
        ))).status == "action_authorized"
        background = await app.drain_background_once()
        assert background is not None and background.work_status == "accepted"
        assert (await app.drain_actions_once()).status == "settled"
        facts = app.export_replay_evidence().projection.facts
        assert len(facts) == 1
        assert facts[0].values.subject_ref == "user:user.1"
        assert facts[0].values.predicate_code == "preference.likes"
        return facts[0]
    finally:
        await app.aclose()


def _omit_original_memory_slices(monkeypatch):
    """Exercise normal bounded Capsule omission; the resolver corpus stays full."""
    import companion_daemon.world_v2.production_turn_application as production
    original = production.context_capsule_compiler_from_ledger

    def bounded(**kwargs):
        policy = kwargs.get("policy") or ContextCapsuleBudgetPolicy()
        kwargs["policy"] = policy.model_copy(update={
            name: SliceBudget(max_items=0, max_fields=8, max_characters=300)
            for name in ("relevant_facts", "active_memory_candidates")
        })
        kwargs["policy"] = kwargs["policy"].model_copy(update={
            "recent_dialogue": SliceBudget(max_items=1, max_fields=32, max_characters=6000),
        })
        return original(**kwargs)

    monkeypatch.setattr(production, "context_capsule_compiler_from_ledger", bounded)


def _json_response(body, value):
    if "tool_choice" in body:
        return _http_result(body, value)
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": json.dumps(value)},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
    })


def _assert_historical_alias(requirement, source_document):
    """A historical payload fixture tests alias/scope, not a new ledger event."""
    from types import SimpleNamespace
    from companion_daemon.world_v2.character_interior import CharacterInterior
    from companion_daemon.world_v2.character_interior.ports import _RecallResult
    from companion_daemon.world_v2.character_interior.production import _CoordinatorRecallPort
    from companion_daemon.world_v2.character_interior.snapshot_compiler import compile_inner_life_snapshot
    from companion_daemon.world_v2.deliberation import ModelInput
    from companion_daemon.world_v2.fact_recall_item import HistoricalFactRecallItem
    from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
    from companion_daemon.world_v2.recall_corpus import RecallCorpusCompiler, RecallCorpusSources
    from companion_daemon.world_v2.recall_index import InMemoryRecallIndex, RecallCursor
    from companion_daemon.world_v2.recall_runtime import RecallCoordinator, verify_trusted_recall_trace
    from companion_daemon.world_v2.visible_recall_sources import bind_presented_recalled_facts
    from companion_daemon.world_v2.visible_fact_value_readings import (
        compile_fact_value_reading, require_fact_value_selection,
    )
    from test_recall_short_cues import LexicalOnly

    source = source_document.accepted_fact
    fact = HistoricalFactRecallItem(**source.model_dump(exclude={"status"}),
        valid_from=source.updated_at, valid_to=NOW)
    request = ModelInput.model_validate_json(json.loads(requirement)["original_input_json"])
    content = json.loads(request.model_content_json)
    snapshot = compile_inner_life_snapshot(content)
    cursor = RecallCursor(**snapshot.cursor.model_dump())
    subjects = tuple(sorted({snapshot.actor_ref, *source_document.subject_refs}))
    documents = RecallCorpusCompiler().compile(
        cursor=cursor, actor_ref=snapshot.actor_ref, subject_refs=subjects,
        sources=RecallCorpusSources(historical_facts=(fact,), authority_bindings=source_document.source_bindings),
    )
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=cursor, documents=documents)
    coordinator = RecallCoordinator.from_built_index(
        index=index, cursor=cursor, actor_ref=snapshot.actor_ref, subject_refs=subjects,
        logical_time=NOW, trigger_ref=request.trigger_ref,
    )
    try:
        trace = coordinator.recall(request=CharacterRecallRequest(query_text=VALUE, include_historical=True),
            expected_cursor=cursor, trigger_ref=request.trigger_ref, accessibility_seed="historical-alias")
        recalled = _RecallResult.model_validate(_CoordinatorRecallPort._trace_result(trace,
            request=SimpleNamespace(world_id=snapshot.world_id, actor_ref=snapshot.actor_ref, cursor=snapshot.cursor),
            trace_field="recall_trace_json"))
        compound_ref = documents[0].source_item_ref
        assert compound_ref != fact.accepted_fact_event_ref
        assert recalled.source_refs == (fact.accepted_fact_event_ref,)
        merged = CharacterInterior._merge_recall(snapshot, recalled)
        assert fact.accepted_fact_event_ref in merged.source_refs
        assert compound_ref not in merged.source_refs
        content["inner_life_snapshot"] = merged.model_view()
        owned = bind_presented_recalled_facts(request.model_copy(update={
            "model_content_json": json.dumps(content), "visible_source_requirement_json": requirement,
            "visible_source_recall_traces": (trace,),
        }))
        messages = _ExpressionDraftWire(model=object())._messages(
            request=owned, quick_recovery=False, failure_code=None)
        user = json.loads(messages[1]["content"])
        item = user["inner_life_snapshot"]["materials"]["selected_recall"]["content"]["items"][0]
        assert item["source_ref"] == fact.accepted_fact_event_ref
        assert item["source_ref"] in user["inner_life_snapshot"]["source_refs"]
        boundaries = user["expression_hard_boundaries"]
        aliases = boundaries["source_ref_aliases"]
        assert fact.accepted_fact_event_ref in {
            aliases.get(ref, ref) for ref in boundaries["world_claim_source_refs"]["counterpart_history"]}
        table, used = supplement_recalled_sources(table=requirement_table(requirement),
            audits=(verify_trusted_recall_trace(trace),), author_request_json=json.dumps({"messages": messages}))
        assert used
        row = next(row for row in table.source_references() if row["source_ref"] == fact.accepted_fact_event_ref)
        reading = compile_fact_value_reading(row)
        assert reading["fact_context"]["status"] == "historical"
        assert reading["fact_context"]["valid_from"] == fact.model_dump(mode="json")["valid_from"]
        assert reading["fact_context"]["valid_to"] == fact.model_dump(mode="json")["valid_to"]
        selection = dict(reading=reading, quoted_value=VALUE, subject_ref=fact.subject_ref)
        assert require_fact_value_selection(**selection, claim_scope="historical_accepted_fact")
        with pytest.raises(ValueError, match="subject/status permission"):
            require_fact_value_selection(**selection, claim_scope="accepted_fact")
    finally:
        coordinator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("recall_mode,review_version,wrong_subject", [
    ("prefetch", "22", False), ("pull", "8", False), ("prefetch", "22", True),
])
async def test_recalled_fact_outside_capsule_has_native_permission_review_and_cold_receipt(
    tmp_path, monkeypatch, recall_mode, review_version, wrong_subject,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "world.sqlite"
    transport = _DeliveredTransport()
    seeded_fact = await _seed_fact(path, transport)
    fact_ref = seeded_fact.fact_id
    delivered_before = len(transport.bodies)
    _omit_original_memory_slices(monkeypatch)
    calls, author_readings, source_requests, callback_failures = [], [], [], []

    async def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        packet = json.loads(body["messages"][1]["content"])
        name = body.get("tool_choice", {}).get("function", {}).get("name", "json_source_review")
        if name.startswith("character_inbound_"):
            if recall_mode == "pull" and name == "character_inbound_initial_v3":
                return _http_result(body, {"result": {
                    "result_kind": "recall", "private_turn_state": {
                        "contract": "private-turn-state.1", "inner_state_summary": "想确认那条偏好。",
                        "attended_source_refs": [],
                    }, "recall_request": {"query_text": VALUE, "memory_kinds": ["semantic"], "limit": 4},
                }})
            materials = packet["inner_life_snapshot"]["materials"]
            recalled = (materials["selected_recall"]["content"] if recall_mode == "pull"
                        else materials["automatic_prefetch"])
            fact = next(item for item in recalled["items"] if item["source_ref"] == fact_ref)
            author_readings.append(fact)
            assert fact["subject_refs"] == ["user:user.1"]
            assert fact["text"] == VALUE
            assert OBSERVATION not in json.dumps(fact, ensure_ascii=False)
            boundaries = packet["expression_hard_boundaries"]
            aliases = boundaries["source_ref_aliases"]
            allowed = {aliases.get(ref, ref) for ref in
                       boundaries["world_claim_source_refs"]["counterpart_history"]}
            assert fact_ref in allowed
            assert seeded_fact.values.assertion_binding.source_ref not in allowed
            assert {ref.ref_id for ref in seeded_fact.values.source_evidence_refs}.isdisjoint(allowed)
            value = _decision()
            value["expression_draft"]["beats"] = [{"modality": "text", "text": REPLY}]
            value["expression_draft"]["world_claims"] = [{
                "claim_text": REPLY, "scope": "counterpart_history", "source_refs": [fact_ref],
            }]
            return _http_result(body, {"result": value})
        if name.startswith("interpret_visible_candidate_complete_"):
            return _http_result(body, {"contract": packet["contract"], "decisions": [{
                "beat_index": 0, "reading_complete": True, "unresolved_details": [],
                "meanings": [{"proposition": "用户喜欢喝乌龙茶", "mode": "actual_event_or_state",
                              "subject_role": "counterpart"}],
                "presuppositions": [], "questions": [], "hypothetical_conditions": [],
            }]})
        source_requests.append(packet)
        if review_version == "8":
            rows = [dict(zip(table["columns"], row, strict=True))
                    for table in packet["source_reference_tables"] for row in table["rows"]]
            row = next(row for row in rows if row["source_ref"] == fact_ref)
            return _http_result(body, {"contract": "visible-beat-source-verdict.8", "decisions": [{
                "beat_index": 0, "verdict": "closed", "semantic_role": "external_proposition",
                "subject_role": "counterpart", "first_source_ref_index": row["source_ref_index"],
                "additional_source_ref_indexes": [],
            }], "rejections": []})
        cards = unpack_shared_strings(packet["source_materials"])
        card = next((card for card in cards if card["material"].get("kind") == "accepted_fact_value"), None)
        assert card is not None, [(item["material"].get("kind"), item["material"].get("lane"))
                                  for item in cards]
        assert card["material"]["accepted_fact"]["accepted_value"] == VALUE
        assert card["material"]["accepted_fact"]["subject_ref"] == "user:user.1"
        assert OBSERVATION not in json.dumps(cards, ensure_ascii=False)
        facts = packet["fixed_facts"]
        assert facts and all(fact["eligible_fact_value_ids"] for fact in facts)
        return _json_response(body, {
            "contract": packet["output_contract"]["contract"],
            "fact_decisions": [{
                "fact_id": fact["fact_id"], "record_dependency": "record_bound",
                "source_support": True, "reading_ids": [],
                "fact_value_selections": [{
                    "reading_id": fact["eligible_fact_value_ids"][0], "quoted_value": VALUE,
                    "claim_scope": "accepted_fact",
                    "subject_ref": "agent:companion" if wrong_subject else "user:user.1",
                }], "explanation": "Offline fixture selects the exact accepted predicate value.",
            } for fact in facts],
            "beat_decisions": [{"beat_index": 0, "review_complete": True,
                "unaccounted_record_bound_assertions": [], "blocking_scope_ambiguities": [],
                "non_record_expressions": []}],
        })

    async def capture_failures(request):
        try:
            return await respond(request)
        except Exception as exc:
            callback_failures.append(exc)
            raise

    models = [DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", name,
        thinking_enabled=False, transport=httpx.MockTransport(capture_failures),
    ) for name in ("deepseek-v4-flash", "deepseek-v4-pro")]
    reviewer = (IndependentVisibleReviewer(
        meaning_models=(models[0], models[1]), source_model=models[1],
        source_response_mode="json_object", scope_permission_context=True,
    ) if review_version == "22" else models[1])
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(update={"private_turn_state_mode": "required"})
    interior = compose_production_character_interior(
        flash_model=models[0], thinking_model=None, source_closure_model=None,
        report_relative_source_closure_model=None, source_closure_reselection_lane=None,
        expression_episode_observer_model=None, flash_model_id=models[0].model, thinking_model_id=None,
        expression_capabilities=capabilities,
        identity_frame=CompanionIdentityFrame(companion_name="小满", counterpart_name="用户"),
        whole_candidate_mode=True, visible_source_review_model=reviewer,
        atomic_tool_envelope_version="3", visible_source_review_version=review_version,
    )
    app = build_sqlite_world_v2_turn_application(
        path=path, config=replace(_config(), expression_capabilities=capabilities,
            visible_source_review_required=True, expression_episode_mode="off", character_memory_enabled=False),
        identities=_Identities(), router=_Router(), character_interior=interior,
        transport=transport, now=NOW,
    )
    try:
        before = app.export_replay_evidence().projection
        outcome = await app.respond(replace(_inbound(),
            text="你还记得我喜欢的乌龙茶吗？" if recall_mode == "prefetch" else "你还记得我的偏好吗？"))
        delivery = await app.drain_actions_once()
        projection = app.export_replay_evidence().projection
        (tmp_path / "native-requests.json").write_text(json.dumps(calls, ensure_ascii=False))
        assert callback_failures == []
        assert author_readings and source_requests
        if wrong_subject:
            assert outcome.status == "deferred"
            assert delivery is not None and delivery.status == "idle"
            assert projection.actions == before.actions
            assert transport.bodies[delivered_before:] == []
            assert app._ledger.rebuild().semantic_hash == projection.semantic_hash
            return
        assert outcome.status == "action_authorized"
        assert delivery is not None and delivery.status == "settled"
        assert transport.bodies[delivered_before:] == [REPLY]
        parent = next(audit for row in projection.model_result_audits
                      if (audit := RecordedModelResultAudit.model_validate_json(row.audit_json))
                      .visible_source_review_json is not None)
        evidence = json.loads(parent.visible_source_review_json)
        original = requirement_table(evidence["requirement_json"])
        assert fact_ref not in {row["source_ref"] for row in original.source_references()}
        assert OBSERVATION not in original.payload_json
        audits = result_recall_audits(parent)
        documents = [hit.document for trace in audits for hit in trace.hits
                     if hit.document.source_item_ref == fact_ref]
        assert documents and documents[0].subject_refs == ("user:user.1",)
        assert documents[0].text == OBSERVATION
        assert documents[0].source_bindings
        for mutation in ("absent_trace", "unshown", "changed_value", "changed_subject"):
            author_request = json.loads(evidence["author_request_json"])
            user = json.loads(author_request["messages"][1]["content"])
            materials = user["inner_life_snapshot"]["materials"]
            for name in ("automatic_prefetch", "selected_recall"):
                selected = materials.get(name, {})
                if name == "selected_recall":
                    selected = selected.get("content", {})
                for item in tuple(selected.get("items", [])):
                    if item.get("source_ref") != fact_ref:
                        continue
                    if mutation == "unshown":
                        selected["items"].remove(item)
                    elif mutation == "changed_value":
                        item["text"] = "咖啡"
                    elif mutation == "changed_subject":
                        item["subject_refs"] = ["agent:companion"]
            author_request["messages"][1]["content"] = json.dumps(user, ensure_ascii=False)
            rejected, _ = supplement_recalled_sources(
                table=original, audits=() if mutation == "absent_trace" else audits,
                author_request_json=json.dumps(author_request),
            )
            assert fact_ref not in {row["source_ref"] for row in rejected.source_references()}
        prepared = json.loads(evidence["receipt"]["prepared_json"])
        table = json.loads(prepared["source_table_json"])
        material = next(entry["material"] for entry in table["source_materials"]
                        if entry["material"].get("item", {}).get("item_ref") == fact_ref)
        assert material["lane"] == "relevant_facts"
        assert material["authority"] == "accepted_fact_with_observation_source"
        assert material["item"]["value"]["source_excerpt"] == OBSERVATION
        assert material["item"]["value"]["subject_ref"] == "user:user.1"
        assert material["item"]["source_bindings"] == [
            binding.model_dump(mode="json") for binding in documents[0].source_bindings]
        if recall_mode == "prefetch":
            _assert_historical_alias(evidence["requirement_json"], documents[0])
        candidates = [row for row in projection.proposal_audits if row.model_call_id == parent.model_call_id]
        assert candidates
        for candidate in candidates:
            assert verify_recorded_candidate(audit=candidate, model_result_audits=projection.model_result_audits)
        assert app._ledger.rebuild().semantic_hash == projection.semantic_hash
        await app.aclose()
        cold = SQLiteWorldLedger(path=path, world_id=_config().world_id)
        try:
            replayed = cold.project()
            assert replayed.semantic_hash == projection.semantic_hash
            for candidate in replayed.proposal_audits:
                if candidate.model_call_id == parent.model_call_id:
                    assert verify_recorded_candidate(audit=candidate, model_result_audits=replayed.model_result_audits)
        finally:
            cold.close()
    finally:
        await app.aclose()
        for model in models:
            await model.aclose()
