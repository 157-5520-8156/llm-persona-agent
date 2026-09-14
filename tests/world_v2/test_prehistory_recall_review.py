"""Production Core -> chosen recall -> HTTP author/review -> recorded receipt.

All provider responses and transport receipts are offline fixtures.
"""
from dataclasses import replace
from copy import deepcopy
import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.production import compose_production_character_interior
from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.production_turn_application import build_sqlite_world_v2_turn_application
from companion_daemon.world_v2.visible_source_runtime import (
    verify_recorded_candidate, requirement_table, verify_evidence, result_recall_audits,
)
from companion_daemon.world_v2.visible_recall_sources import supplement_recalled_prehistory
from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit

from test_character_prehistory import reviewed_archive, reapprove
from test_memory_candidate_authority import salience
from test_production_turn_application import _config, _Identities, _Router, _DeliveredTransport, NOW
from test_whole_candidate_author import _decision, _inbound
from test_world_stimulus_life_intent import _http_result


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_version,review_version", [("1", "1"), ("3", "8")])
@pytest.mark.parametrize("recall_mode", ["pull", "prefetch"])
@pytest.mark.parametrize("correct_wrong_scope", [False, True, "always"])
async def test_actual_core_recall_supplies_presented_history_to_review_and_cold_receipt(tmp_path, monkeypatch, tool_version, review_version, recall_mode, correct_wrong_scope):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    config = _config()
    data = reviewed_archive().document.model_dump(mode="json")
    data.update(world_id=config.world_id, actor_ref=config.companion_actor_ref)
    # Both records share the original import cursor. Accepting the first must
    # not make the second depend on the now-newer live head.
    second = deepcopy(data["records"][0])
    second.update(record_id="prehistory-record:university-clubs", statement="上大学后进过文学社和摄影社。")
    data["records"].append(second)
    archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(data)))
    archive = archive.model_copy(update={"review": archive.review.model_copy(update={"reviewed_at": NOW})})
    statement = archive.document.records[0].statement
    requests = []
    authored_contexts = []
    def historical_decision(user, history):
        authored_contexts.append(user)
        value = _decision()
        value["expression_draft"]["beats"] = [{"modality": "text", "text": statement}]
        scope = "current_world" if correct_wrong_scope == "always" or (correct_wrong_scope and len(authored_contexts) == 1) else "past_world"
        value["expression_draft"]["world_claims"] = [{"claim_text": statement, "scope": scope, "source_refs": [history["source_ref"]]}]
        return value
    import companion_daemon.world_v2.visible_source_runtime as visible_runtime
    original_review = visible_runtime.review_candidate
    live_traces = []
    live_inputs = []
    async def capture_review(**kwargs):
        live_traces.append(kwargs["request"].visible_source_recall_traces)
        live_inputs.append(kwargs["request"])
        return await original_review(**kwargs)
    monkeypatch.setattr(visible_runtime, "review_candidate", capture_review)

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        name = body["tool_choice"]["function"]["name"]
        user = json.loads(body["messages"][-1]["content"])
        if name == "character_role_fact_memory_retention_v1":
            capability = user["capability_manifest"]
            return _http_result(body, {"status": "decision", "summary": "fixture retains old history",
                "attended_source_refs": capability["source_refs"], "recall_query": None, "proposals": [],
                "decision": {"source_refs": capability["source_refs"], "payload": {"retain": True,
                    "cue_kind": "identity", "retention_rationales": ["identity_relevance"],
                    "salience": salience().model_dump(mode="json", exclude={"matrix_digest", "matrix_version"})}}})
        if name == f"character_inbound_initial_v{tool_version}":
            if recall_mode == "prefetch":
                items = user["inner_life_snapshot"]["materials"]["automatic_prefetch"]["items"]
                history = next(item for item in items if item.get("epistemic_scope") == "character_prehistory")
                assert history["text"] == statement
                value = historical_decision(user, history)
                fields = body["tools"][0]["function"]["parameters"]["properties"]
                return _http_result(body, {"result": value} if tool_version == "3" else
                                    {key: value.get(key) for key in fields})
            value = {"result_kind": "recall", "private_turn_state": {
                "contract": "private-turn-state.1", "inner_state_summary": "我想回想一下高中校刊的事。",
                "attended_source_refs": [],
            }, "recall_request": {"query_text": "高中校刊核对稿件", "memory_kinds": ["episodic"], "limit": 4}}
        elif name == f"character_inbound_after_recall_v{tool_version}":
            selected = user["inner_life_snapshot"]["materials"]["selected_recall"]["content"]["items"]
            history = next(item for item in selected if item.get("epistemic_scope") == "character_prehistory")
            assert history["text"] == statement
            assert history["prehistory"]["entities"][0]["entity_ref"].startswith("history:")
            value = historical_decision(user, history)
        else:
            assert name == f"visible_beat_source_verdict_v{review_version}", name
            rows = (user["source_references"] if review_version == "1" else
                    [dict(zip(table["columns"], row, strict=True))
                     for table in user["source_reference_tables"] for row in table["rows"]])
            sources = [row for row in rows
                       if user["source_materials"][row["material_index"]].get("lane") == "recalled_prehistory"]
            assert sources, (len(live_traces[-1]), user["source_materials"])
            source = sources[0]
            assert source["support_eligibility"] == "eligible"
            material = user["source_materials"][source["material_index"]]
            assert material["item"]["value"]["text"] == statement
            return _http_result(body, {"contract": f"visible-beat-source-verdict.{review_version}", "decisions": [{
                "beat_index": 0, "verdict": "closed", "semantic_role": "external_proposition",
                "subject_role": "companion",
                **({"source_ref_indexes": [source["source_ref_index"]]} if review_version == "1" else
                   {"first_source_ref_index": source["source_ref_index"], "additional_source_ref_indexes": []}),
            }], **({"rejections": []} if review_version != "1" else {})})
        if tool_version == "3":
            return _http_result(body, {"result": value})
        fields = body["tools"][0]["function"]["parameters"]["properties"]
        return _http_result(body, {key: value.get(key) for key in fields})

    model = DeepSeekChatModel("offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
                             thinking_enabled=False, transport=httpx.MockTransport(respond))
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(update={"private_turn_state_mode": "required"})
    interior = compose_production_character_interior(
        flash_model=model, thinking_model=None, source_closure_model=None,
        report_relative_source_closure_model=None, source_closure_reselection_lane=None,
        expression_episode_observer_model=None, flash_model_id=model.model, thinking_model_id=None,
        expression_capabilities=capabilities,
        identity_frame=CompanionIdentityFrame(companion_name="小满", counterpart_name="用户"),
        whole_candidate_mode=True, visible_source_review_model=model,
        atomic_tool_envelope_version=tool_version, visible_source_review_version=review_version,
    )
    path = tmp_path / "world.sqlite"
    transport = _DeliveredTransport()
    app = build_sqlite_world_v2_turn_application(
        path=path, config=replace(config, expression_capabilities=capabilities,
            visible_source_review_required=True, expression_episode_mode="off", reviewed_prehistory=archive,
            character_memory_enabled=False),
        identities=_Identities(), router=_Router(), character_interior=interior,
        transport=transport, now=NOW,
    )
    try:
        retained = await app.initialize_prehistory_once(allow_model_call=True)
        assert retained["status"] == "retained", retained
        second_retained = await app.initialize_prehistory_once(allow_model_call=True)
        assert second_retained["status"] == "retained", second_retained
        assert second_retained["record_id"] != retained["record_id"]
        assert (await app.initialize_prehistory_once())["status"] == "idle"
        assert len(requests) == 2
        from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
        from companion_daemon.world_v2.schemas import ProjectionCursor
        row = app._ledger.project().prehistory_records[0]
        opportunity = app._prehistory_memory._opportunity(prehistory_memory_binding(row))
        projected = await app._character_interior._projection.project(subject=opportunity)
        assert projected.logical_time == opportunity.logical_time
        head = app._ledger.project()
        live_cursor = ProjectionCursor(world_revision=head.world_revision,
            deliberation_revision=head.deliberation_revision, ledger_sequence=head.ledger_sequence)
        for changed in (
            opportunity.model_copy(update={"cursor": live_cursor}),
            opportunity.model_copy(update={"actor_ref": "actor:someone-else"}),
            opportunity.model_copy(update={"source_refs": ()}),
            opportunity.model_copy(update={"capability_manifest": opportunity.capability_manifest.model_copy(
                update={"payload_json": json.dumps({**opportunity.capability_manifest.payload, "verified_source_text": "uncommitted story"})})}),
        ):
            with pytest.raises(ValueError):
                await app._character_interior._projection.project(subject=changed)
        # Returning the same source bytes after two writes does not call the
        # provider or authorize a new choice/action.
        assert len(requests) == 2
        outcome = await app.respond(replace(_inbound(), text=("你上学时有什么印象深的事？" if recall_mode == "pull" else "所以你今天也忙着校刊的事吗？")))
        delivery = await app.drain_actions_once()
        if correct_wrong_scope == "always":
            assert outcome.status == "deferred"
            assert delivery is not None and delivery.status == "idle"
            assert len(authored_contexts) == 2
            assert len(requests) == (5 if recall_mode == "pull" else 4)
            assert not any(body["tool_choice"]["function"]["name"].startswith("visible_beat_source")
                           for body in requests)
            assert transport.bodies == []
            failed = app.export_replay_evidence().projection
            assert failed.actions == () and failed.stored_message_payloads == ()
            assert app._ledger.rebuild().semantic_hash == failed.semantic_hash
            return
        assert delivery is not None and delivery.status == "settled"
        projection = app.export_replay_evidence().projection
        assert outcome.status == "action_authorized", [len(traces) for traces in live_traces]
        assert [request["tool_choice"]["function"]["name"] for request in requests] == [
            "character_role_fact_memory_retention_v1", "character_role_fact_memory_retention_v1",
            f"character_inbound_initial_v{tool_version}",
            *([f"character_inbound_after_recall_v{tool_version}"] if recall_mode == "pull" else []),
            *([f"character_inbound_after_recall_v{tool_version}" if recall_mode == "pull" else
               f"character_inbound_initial_v{tool_version}"] if correct_wrong_scope else []),
            f"visible_beat_source_verdict_v{review_version}",
        ]
        assert tuple(item.text for item in projection.stored_message_payloads) == (statement,)
        assert transport.bodies == [statement]
        assert len(authored_contexts) == (2 if correct_wrong_scope else 1)
        if correct_wrong_scope:
            first, corrected = authored_contexts
            correction = (corrected["role_result_correction"]["coordinate"] if tool_version == "3" else
                          corrected["inner_life_snapshot"]["role_result_correction"])
            assert "semantic source lane" in correction["failure_detail"]
            assert "current_world" in correction["failure_detail"]
            assert corrected["inner_life_snapshot"]["materials"] == first["inner_life_snapshot"]["materials"]
            for field in ("capsule_id", "trigger_ref", "evaluated_world_revision",
                          "evaluated_deliberation_revision", "evaluated_ledger_sequence"):
                assert corrected["request"][field] == first["request"][field]

        audits = [RecordedModelResultAudit.model_validate_json(row.audit_json)
                  for row in projection.model_result_audits]
        parent = next(audit for audit in audits if audit.visible_source_review_json is not None)
        evidence = json.loads(parent.visible_source_review_json)
        assert evidence["contract"] == "visible-source-runtime-evidence.2"
        from companion_daemon.world_v2.prehistory_claim_authority import prehistory_claim_bindings
        from companion_daemon.world_v2.expression_draft import world_claim_source_refs_by_scope
        owned = live_inputs[-1]
        historical = prehistory_claim_bindings(owned)
        record_ref = archive.document.records[0].record_id
        assert record_ref in historical
        allowed = world_claim_source_refs_by_scope(context=json.loads(owned.model_content_json), request=owned)
        assert record_ref in allowed["past_world"]
        assert all(record_ref not in allowed[scope] for scope in (
            "current_world", "counterpart_history", "shared_history", "stable_identity"))
        assert not prehistory_claim_bindings(owned.model_copy(update={"visible_source_recall_traces": ()}))
        hidden = json.loads(owned.model_content_json)
        hidden["inner_life_snapshot"]["materials"] = {}
        assert not prehistory_claim_bindings(owned, context=hidden)
        with pytest.raises(ValueError, match="original input pin"):
            prehistory_claim_bindings(owned.model_copy(update={"evaluated_ledger_sequence": 99999}))
        candidate = json.loads(json.loads(evidence["receipt"]["prepared_json"])["candidate_json"])
        actual_refs = {ref["ref_id"] for ref in candidate["evidence_refs"]}
        assert {binding.ref for binding in historical[record_ref]} <= actual_refs

        base = requirement_table(evidence["requirement_json"])
        assert "recalled_prehistory" not in base.payload_json
        assert statement not in base.payload_json
        candidates = [audit for audit in projection.proposal_audits if audit.model_call_id == parent.model_call_id]
        assert candidates
        for audit in candidates:
            assert verify_recorded_candidate(audit=audit, model_result_audits=projection.model_result_audits)
        assert app._ledger.rebuild().semantic_hash == projection.semantic_hash
        # An independently recorded trace is required even if all receipt
        # hashes are present; the review carrier is never its own authority.
        with pytest.raises(ValueError, match="independently recorded"):
            verify_evidence(raw=parent.visible_source_review_json, proposal=json.loads(json.loads(evidence["receipt"]["prepared_json"])["candidate_json"]),
                requirement=evidence["requirement_json"], author_call=parent.model_call_id,
                author_request_hash=parent.request_hash, subcalls=())
        restored, used = supplement_recalled_prehistory(table=base, audits=result_recall_audits(parent),
                                                       author_request_json=evidence["author_request_json"])
        assert used and restored.as_dict()["contract"] == "visible-source-row-table.5"
        with pytest.raises(ValueError, match="at most one"):
            supplement_recalled_prehistory(table=base, audits=(*used, *used, *used),
                                          author_request_json=evidence["author_request_json"])
        count = len(base.as_dict()["source_references"])
        assert restored.as_dict()["source_references"][:count] == base.as_dict()["source_references"]
        assert restored.as_dict()["pin"] == base.as_dict()["pin"]
        for mutation in ("removed", "changed_text", "changed_historical_identity"):
            changed = json.loads(evidence["author_request_json"])
            body = json.loads(changed["messages"][1]["content"])
            materials = body["inner_life_snapshot"]["materials"]
            selection = (materials["selected_recall"]["content"] if recall_mode == "pull"
                         else materials["automatic_prefetch"])
            if mutation == "removed":
                selection["items"] = []
            elif mutation == "changed_text":
                selection["items"][0]["text"] = "今天去了校刊编辑室。"
            else:
                selection["items"][0]["prehistory"]["entities"][0]["label"] = "现在的用户"
            changed["messages"][1]["content"] = json.dumps(body, ensure_ascii=False)
            unchanged, ignored = supplement_recalled_prehistory(table=base, audits=used,
                                                                author_request_json=json.dumps(changed))
            assert unchanged == base and ignored == ()
        for mutation in ("cursor", "actor", "trigger"):
            other = deepcopy(base.as_dict())
            if mutation == "cursor":
                other["pin"]["world_revision"] += 1
            elif mutation == "actor":
                other["subjects"]["companion_actor_ref"] = "actor:other"
            else:
                other["pin"]["trigger_ref"] = "event:other"
            with pytest.raises(ValueError, match="cursor and actor"):
                supplement_recalled_prehistory(table=VisibleSourceTable(payload_json=json.dumps(other)),
                    audits=used, author_request_json=evidence["author_request_json"])
        await app.aclose()
        cold = SQLiteWorldLedger(path=path, world_id=config.world_id)
        try:
            replayed = cold.project()
            assert replayed.semantic_hash == projection.semantic_hash
            for audit in replayed.proposal_audits:
                if audit.model_call_id == parent.model_call_id:
                    assert verify_recorded_candidate(audit=audit, model_result_audits=replayed.model_result_audits)
            assert len(requests) == (5 if recall_mode == "pull" else 4) + int(correct_wrong_scope)
        finally:
            cold.close()
    finally:
        await app.aclose()
        await model.aclose()
