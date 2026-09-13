"""Real Interior turn store and ledger, with explicit offline character choices."""
from datetime import timedelta
import json

import httpx
import pytest

from companion_daemon.world_v2.character_interior import CharacterInterior
from companion_daemon.world_v2.character_interior.turn_store import open_sqlite_character_interior_turn_store
from companion_daemon.world_v2.prehistory_memory_decision import decision_event_id
from companion_daemon.world_v2.prehistory_memory_runtime import PrehistoryMemoryRuntime
from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_character_interior import _Projection, _author_lineage
from test_prehistory_memory_source import _install, _read
from test_character_prehistory import ACTOR, START, started_ledger
from test_life_projection import WORLD_ID, commit, event
from test_memory_candidate_authority import salience


class Projection(_Projection):
    async def project(self, *, subject):
        value = await super().project(subject=subject)
        value["logical_time"] = subject.logical_time
        value["situation"]["source_refs"] = subject.source_refs
        return value


class Role:
    name = "fixture-character-retention"
    purposes = ("fact_memory_retention",)

    def __init__(self, *, retain=True, fail=False):
        self.retain, self.fail = retain, fail
        self.calls = []

    async def experience(self, request):
        raise AssertionError("history initialization must not author a lived Experience")

    async def consider(self, request):
        self.calls.append(request)
        if self.fail:
            raise TimeoutError("fixture provider unavailable")
        manifest = request.capability_manifest
        payload = {"contract": "character-interior-fact-memory-retention.1", "retain": self.retain}
        if self.retain:
            payload.update(cue_kind="identity", retention_rationales=["identity_relevance"],
                           salience=salience().model_dump(mode="json", exclude={"matrix_digest", "matrix_version"}))
        return {"status": "decision", "summary": "fixture personal retention choice",
            "attended_source_refs": request.subject_source_refs, "proposals": (),
            "author_lineage": _author_lineage(request),
            "decision": {"contract": "character-interior-purpose-decision.1", "purpose": request.purpose,
                "capability_ref": manifest.capability_ref, "capability_payload_hash": manifest.payload_hash,
                "source_refs": list(manifest.source_refs), "payload": payload}}


def interior(path, role):
    return CharacterInterior(projection=Projection(), role=role,
        turn_store=open_sqlite_character_interior_turn_store(path=path, world_id=WORLD_ID))


@pytest.mark.asyncio
@pytest.mark.parametrize("retain", [True, False])
async def test_initialize_once_and_restart_without_reauthoring(tmp_path, retain):
    path = tmp_path / "world.sqlite"
    ledger = started_ledger(path)
    row = _install(ledger)
    role = Role(retain=retain)
    first = interior(path, role)
    runtime = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=first)
    try:
        assert (await runtime.advance_once())["status"] == "model_call_disabled"
        assert not role.calls
        outcome = await runtime.advance_once(allow_model_call=True)
        assert outcome["status"] == ("retained" if retain else "no_change"), outcome
        assert len(role.calls) == 1
        request = role.calls[0]
        assert request.capability_manifest.payload["source_kind"] == "character_prehistory"
        assert request.capability_manifest.payload["verified_source_text"] == row.record.statement
        assert bool(_read(ledger).items) == retain
        before = ledger.project()
        assert ledger.rebuild().semantic_hash == before.semantic_hash
        first._turn_store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        second_role = Role(fail=True)
        second = interior(path, second_role)
        try:
            restarted = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=second)
            assert (await restarted.advance_once(allow_model_call=True))["status"] == "idle"
            assert not second_role.calls
            assert ledger.project().semantic_hash == before.semantic_hash
        finally:
            second._turn_store.close()
    finally:
        first._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_paid_terminal_recovers_before_decision_audit_without_model(tmp_path, monkeypatch):
    path = tmp_path / "recover.sqlite"
    ledger = started_ledger(path)
    _install(ledger)
    role = Role()
    first = interior(path, role)
    runtime = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=first)
    def crash(*args, **kwargs):
        raise RuntimeError("fixture crash before decision audit")
    monkeypatch.setattr(runtime, "_record", crash)
    try:
        with pytest.raises(RuntimeError, match="before decision audit"):
            await runtime.advance_once(allow_model_call=True)
        assert len(role.calls) == 1 and not ledger.project().memory_candidates
        first._turn_store.close()
        later = START + timedelta(minutes=10)
        commit(ledger, [event("clock-before-memory-recovery", "ClockAdvanced", {
            "logical_time_from": START.isoformat(), "logical_time_to": later.isoformat(),
        }, at=later)])
        second_role = Role(fail=True)
        second = interior(path, second_role)
        try:
            restart = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=second)
            assert (await restart.advance_once())["status"] == "retained"
            assert not second_role.calls and _read(ledger).items
            assert ledger.project().memory_candidates[0].opened_at == later
            assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
        finally:
            second._turn_store.close()
    finally:
        first._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_technical_failure_is_durable_bounded_and_does_not_become_no_change(tmp_path):
    path = tmp_path / "failed.sqlite"
    ledger = started_ledger(path)
    row = _install(ledger)
    role = Role(fail=True)
    character = interior(path, role)
    runtime = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=character)
    try:
        for ordinal, elapsed in ((1, 0), (2, 30), (3, 150)):
            at = START + timedelta(seconds=elapsed)
            if elapsed:
                before = ledger.project().logical_time
                commit(ledger, [event(f"retry-clock-{ordinal}", "ClockAdvanced", {
                    "logical_time_from": before.isoformat(), "logical_time_to": at.isoformat(),
                }, at=at)])
            assert (await runtime.advance_once(allow_model_call=True))["status"] == "technical_failure"
            assert len(role.calls) == ordinal
            assert (await runtime.advance_once(allow_model_call=True))["status"] == ("retry_wait" if ordinal < 3 else "retry_exhausted")
            assert len(role.calls) == ordinal
        assert not ledger.project().memory_candidates
        source = prehistory_memory_binding(row)
        assert ledger.lookup_event_commit(decision_event_id(WORLD_ID, source)) is None
        assert all(ledger.lookup_event_commit(decision_event_id(WORLD_ID, source, failure_ordinal=i)) for i in range(1, 4))
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        character._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_after_open", [False, True])
async def test_recorded_choice_recovers_partial_memory_acceptance_without_call(tmp_path, monkeypatch, crash_after_open):
    path = tmp_path / "apply.sqlite"
    ledger = started_ledger(path)
    _install(ledger)
    role = Role()
    first = interior(path, role)
    runtime = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=first)
    original = runtime._lifecycle._record_and_accept
    def crash(**kwargs):
        if crash_after_open:
            original(**kwargs)
        raise RuntimeError("fixture crash during memory acceptance")
    monkeypatch.setattr(runtime._lifecycle, "_record_and_accept", crash)
    try:
        with pytest.raises(RuntimeError, match="during memory acceptance"):
            await runtime.advance_once(allow_model_call=True)
        assert len(role.calls) == 1
        assert len(ledger.project().memory_candidates) == int(crash_after_open)
        first._turn_store.close()
        second_role = Role(fail=True)
        second = interior(path, second_role)
        try:
            restarted = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=second)
            assert (await restarted.advance_once())["status"] == "retained"
            assert not second_role.calls and _read(ledger).items
            assert tuple(item.operation for item in ledger.project().memory_candidate_transitions) == ("open", "accept")
            assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
        finally:
            second._turn_store.close()
    finally:
        first._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_production_role_corrects_partial_sources_before_terminal_is_saved(tmp_path):
    from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty
    from test_character_interior_structured_role import _RequiredToolQueueModel, _result
    path = tmp_path / "structured.sqlite"
    ledger = started_ledger(path)
    row = _install(ledger)
    source = prehistory_memory_binding(row)
    refs = [row.accepted_event_ref, ledger.project().prehistory_archives[0].accepted_event_ref]
    model = _RequiredToolQueueModel(*(
        _result(status="decision", decision={"source_refs": selected, "payload": {"retain": False}})
        for selected in (refs[:1], refs)
    ))
    character = interior(path, StructuredCharacterRoleFaculty(model=model, model_id=model.model))
    runtime = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=character)
    try:
        result = await runtime.advance_once(allow_model_call=True)
        assert result["status"] == "no_change", result
        assert len(model.calls) == 2
        assert all(choice[1]["function"]["name"] == "character_role_fact_memory_retention_v1" for choice in model.tool_calls)
        recorded = runtime._recorded(source)
        assert recorded.result.author_lineage.attempt_ordinal == 1
        assert recorded.result.author_lineage.parent_model_call_id is not None
        assert recorded.result.decision["source_refs"] == refs
        assert len(character.completed_considerations_for_source(world_id=WORLD_ID, actor_ref=ACTOR,
            purpose="fact_memory_retention", source_ref=source.authority_event_ref)) == 1
        assert (await runtime.advance_once(allow_model_call=True))["status"] == "idle"
        assert len(model.calls) == 2 and not ledger.project().memory_candidates
    finally:
        character._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_public_application_initialization_reaches_existing_provider_seam(tmp_path, monkeypatch):
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
    from test_character_prehistory import reapprove, reviewed_archive
    import test_world_stimulus_life_intent as shared
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    data = reviewed_archive().document.model_dump(mode="json")
    data.update(world_id=shared.WORLD, actor_ref=shared.ACTOR)
    archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(data)))
    requests = []
    async def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        material = json.loads(body["messages"][-1]["content"])
        assert material["inner_turn"]["purpose"] == "fact_memory_retention"
        capability = material["capability_manifest"]
        assert capability["payload"]["source_kind"] == "character_prehistory"
        assert capability["payload"]["verified_source_text"] == archive.document.records[0].statement
        return shared._http_result(body, {"status": "decision", "summary": "fixture chooses to retain this history",
            "attended_source_refs": capability["source_refs"], "recall_query": None, "proposals": [],
            "decision": {"source_refs": capability["source_refs"], "payload": {"retain": True,
                "cue_kind": "identity", "retention_rationales": ["identity_relevance"],
                "salience": salience().model_dump(mode="json", exclude={"matrix_digest", "matrix_version"})}}})
    model = DeepSeekChatModel("offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(provider))
    app = shared._build(tmp_path / "public.sqlite", model, reviewed_prehistory=archive)
    try:
        assert (await app.initialize_prehistory_once())["status"] == "model_call_disabled"
        assert not requests
        outcome = await app.initialize_prehistory_once(allow_model_call=True)
        assert outcome["status"] == "retained", outcome
        assert len(requests) == 1
        projection = app.export_replay_evidence().projection
        assert projection.memory_candidates[0].values.source_bindings[0].source_kind == "prehistory"
        from companion_daemon.world_v2.context_resolver import query_from_projection
        from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
        from test_prehistory_chat_context import _table

        capsule = context_capsule_compiler_from_ledger(ledger=app._ledger).compile(
            query_from_projection(projection, actor_ref=shared.ACTOR,
                                  trigger_ref=projection.prehistory_records[0].accepted_event_ref),
        )
        remembered, = capsule.active_memory_candidates.items
        assert len(remembered.source_bindings) == 2
        assert archive.document.records[0].statement in capsule.model_content_json
        assert _table(capsule)["contract"] == "visible-source-row-table.4"
        assert (await app.initialize_prehistory_once(allow_model_call=True))["status"] == "idle"
        assert len(requests) == 1
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_foreign_character_cannot_initialize_another_private_archive(tmp_path):
    path = tmp_path / "foreign.sqlite"
    ledger = started_ledger(path)
    _install(ledger)
    role = Role()
    character = interior(path, role)
    runtime = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref="actor:other", character_interior=character)
    before = ledger.project()
    try:
        assert (await runtime.advance_once(allow_model_call=True))["status"] == "idle"
        assert not role.calls and ledger.project() == before
    finally:
        character._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_recording_cannot_repin_initialization_to_a_later_cursor(tmp_path):
    from companion_daemon.world_v2.prehistory_memory_decision import initialization_opportunity
    from companion_daemon.world_v2.schemas import ProjectionCursor
    path = tmp_path / "repin.sqlite"
    ledger = started_ledger(path)
    row = _install(ledger)
    role = Role()
    character = interior(path, role)
    runtime = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=character)
    try:
        source = prehistory_memory_binding(row)
        later = START + timedelta(minutes=1)
        commit(ledger, [event("later-init-pin", "ClockAdvanced", {
            "logical_time_from": START.isoformat(), "logical_time_to": later.isoformat(),
        }, at=later)])
        current = ledger.project()
        changed = initialization_opportunity(world_id=WORLD_ID, row=row, archive=current.prehistory_archives[0],
            cursor=ProjectionCursor(world_revision=current.world_revision,
                deliberation_revision=current.deliberation_revision, ledger_sequence=current.ledger_sequence))
        with pytest.raises(ValueError, match="exact import commit snapshot"):
            runtime._record(source, changed, 1, status="technical_failure", failure_code="fixture")
        assert ledger.project() == current and not role.calls
    finally:
        character._turn_store.close()
        ledger.close()
