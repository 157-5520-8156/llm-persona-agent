"""Full batch -> successful receipt -> cold view -> later cognition boundary."""
import json
from datetime import timedelta

import pytest

from companion_daemon.world_v2.character_interior.snapshot_compiler import compile_inner_life_snapshot
from companion_daemon.world_v2.memory_consolidation_contract import (
    CONTRACT, source_texts, tool_contract, validate_payload,
)
from companion_daemon.world_v2.memory_consolidation_runtime import MemoryConsolidationRuntime
from companion_daemon.world_v2.memory_understanding import MemoryUnderstandingReader, install_understanding_context
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore, StoredLifeContent
from companion_daemon.world_v2.replay_evaluator import ReplayEvaluator
from character_interior import canonical_inner_decision
from test_memory_consolidation_runtime import seed_memories
from test_visible_selected_source_context import _sources
from test_life_development_runtime import _seed_clock


class Learner:
    def __init__(self, operation="retain", *, forget=False):
        self.operation, self.forget, self.calls = operation, forget, 0
        self.capabilities = []

    async def consider(self, opportunity):
        self.calls += 1
        cap = opportunity.capability_manifest.payload
        self.capabilities.append(cap)
        # Exercise the actual new provider schema, not just the reader.
        tool_contract(capability_payload=cap, source_refs=opportunity.source_refs, recall_allowed=False)
        quote = source_texts(cap["memories"][0]["reading"])[0][:30]
        updates = []
        if not self.forget:
            update = dict(operation=self.operation, predecessor_tokens=(
                cap["understanding_tokens"][:2] if self.operation == "merge"
                else cap["understanding_tokens"][:1] if self.operation in {"revise", "release"} else []),
                source_tokens=["m0"], facet="听对方说偏好", summary="我可以先听具体需求，再决定怎样接近。",
                confidence_bp=6500, evidence_quotes=[{"source_token":"m0", "quote":quote}])
            updates = [update]
            if self.operation == "retain":
                updates.append({**update, "facet":"自己的表达", "summary":"我也可以保留自己的不同想法。"})
        return canonical_inner_decision(opportunity, decision={
            "contract":"character-interior-purpose-decision.1", "purpose":opportunity.purpose,
            "source_refs":list(opportunity.source_refs),
            "capability_ref":opportunity.capability_manifest.capability_ref,
            "capability_payload_hash":opportunity.capability_manifest.payload_hash,
            "payload":{"contract":CONTRACT, "understandings":updates, "choices":[
                {"candidate_token":t,"disposition":"forget" if self.forget else "retain","next_review_hours":72}
                for t in cap["offered_tokens"]]},
        }, identity=f"understanding:{self.operation}:{self.calls}")


def clock(ledger, name, hours):
    now = ledger.project().logical_time
    _seed_clock(ledger, event_id="event:clock:"+name, logical_time=now+timedelta(hours=hours), logical_time_from=now)


def worker(ledger, store, model):
    return MemoryConsolidationRuntime(ledger=ledger, content_store=store, character_interior=model,
        actor_ref="agent:companion", owner_id="worker:memory-understanding")


@pytest.mark.asyncio
async def test_distinct_facets_merge_only_by_role_choice_then_withdrawal_invalidates_cold_view(tmp_path):
    async with _sources(tmp_path, extra=True) as case:
        ledger = case.ledger
        seed_memories(ledger)
        store = SQLiteImmutableLifeContentStore(path=tmp_path/"content.sqlite", world_id=ledger.world_id)
        original = ledger.project().facts
        model = Learner()
        assert (await worker(ledger,store,model).drain_one()).work_status == "reviewed"
        read = MemoryUnderstandingReader(ledger=ledger,content_store=store,actor_ref="agent:companion")
        first = read.read(ledger.project())
        assert len(first.active)==2  # Same evidence, separate meanings: host does not auto-merge.
        assert first.acknowledged and not first.suppressions
        assert (await worker(ledger,store,model).drain_one()).status=="idle"
        assert model.calls==1
        context = install_understanding_context({"world_id":ledger.world_id,"actor_ref":"agent:companion",
            "logical_time":ledger.project().logical_time.isoformat(),"slices":{}}, first)
        snapshot = compile_inner_life_snapshot(context)
        assert len(snapshot.materials["memory_understandings"])==2
        inventory = [i for i in snapshot.source_inventory if i.scope=="memory_understandings"]
        assert all(i.authority_scope=="defeasible_interpretation" and i.authority_bindings for i in inventory)
        assert any("memory_understandings" in f.content.get("material_keys",[]) for f in snapshot.facet_views if f.name=="private_self")

        clock(ledger,"merge",72)
        merge = Learner("merge")
        assert (await worker(ledger,store,merge).drain_one()).work_status=="reviewed"
        cold = MemoryUnderstandingReader(ledger=ledger,content_store=store,actor_ref="agent:companion").read(ledger.project())
        assert len(cold.versions)==3 and len(cold.active)==1
        assert set(cold.active[0].predecessor_refs)=={v.version_ref for v in first.active}
        assert {v.read_status for v in cold.recallable}=={"active","superseded"}
        from companion_daemon.world_v2.recall_corpus import RecallCorpusSources,RecallCorpusCompiler
        from companion_daemon.world_v2.recall_index import RecallCursor,RecallSourceBinding,InMemoryRecallIndex,FeatureHashRecallEmbedding,RecallQuery
        p=ledger.project()
        bindings=tuple(RecallSourceBinding(source_kind="committed_event",authority_type=e.event_type,
            ref=e.event_id,source_world_revision=e.world_revision,immutable_hash=e.payload_hash)
            for e in p.committed_world_event_refs)+tuple(RecallSourceBinding(source_kind="immutable_payload",
            authority_type="TriggerProcessCompleted",ref=v.receipt_ref,source_world_revision=v.receipt_world_revision,
            immutable_hash=v.receipt_hash) for v in cold.recallable)
        cursor=RecallCursor(world_revision=p.world_revision,deliberation_revision=p.deliberation_revision,ledger_sequence=p.ledger_sequence)
        # Duplicate receipt bindings from two distinct views are canonicalized.
        bindings=tuple({(b.source_kind,b.ref):b for b in bindings}.values())
        docs=RecallCorpusCompiler().compile(cursor=cursor,actor_ref="agent:companion",subject_refs=("agent:companion",),
            sources=RecallCorpusSources(memory_understandings=cold.active,
                historical_memory_understandings=tuple(v for v in cold.recallable if v.read_status!="active"),
                authority_bindings=bindings))
        index=InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(cursor=cursor,documents=docs)
        def query(historical):
            return RecallQuery(query_text=first.active[1].summary,cursor=cursor,actor_ref="agent:companion",
                subject_refs=("agent:companion",),viewer_privacy_ceiling="private",at=p.logical_time,
                include_historical=historical,limit=6,accessibility_seed="standing-history")
        assert any(h.document.status=="superseded" for h in index.search(query(True)).hits)
        assert all(h.document.status=="active" for h in index.search(query(False)).hits)
        assert not cold.suppressions
        assert ledger.project().facts==original
        assert not MemoryUnderstandingReader(ledger=ledger,content_store=store,actor_ref="npc:other").read(ledger.project()).active

        clock(ledger,"forget",72)
        forget = Learner(forget=True)
        assert (await worker(ledger,store,forget).drain_one()).work_status=="reviewed"
        revoked = read.read(ledger.project())
        assert not revoked.active and len(revoked.versions)==3
        assert any(reason=="source_changed_or_withdrawn" for _,reason in revoked.suppressions)
        assert not install_understanding_context({"slices":{}},revoked)["slices"]["memory_understandings"]["items"]
        assert ReplayEvaluator().evaluate(evidence=ledger.export_replay_evidence()).passed
        store.close()


@pytest.mark.asyncio
async def test_paid_pending_batch_is_inert_and_crash_recovery_does_not_repay(tmp_path,monkeypatch):
    async with _sources(tmp_path, extra=True) as case:
        ledger=case.ledger
        seed_memories(ledger)
        store=SQLiteImmutableLifeContentStore(path=tmp_path/"content.sqlite",world_id=ledger.world_id)
        model=Learner()
        runtime=worker(ledger,store,model)
        original=runtime._persist_choice
        calls=0
        def crash(*args):
            nonlocal calls
            calls+=1
            if calls==2:
                raise OSError("controlled crash before complete receipt")
            return original(*args)
        monkeypatch.setattr(runtime,"_persist_choice",crash)
        with pytest.raises(OSError):
            await runtime.drain_one()
        read=MemoryUnderstandingReader(ledger=ledger,content_store=store,actor_ref="agent:companion")
        assert not read.read(ledger.project()).versions
        assert (await worker(ledger,store,model).drain_one()).work_status=="reviewed"
        assert model.calls==1 and len(read.read(ledger.project()).active)==2
        assert ReplayEvaluator().evaluate(evidence=ledger.export_replay_evidence()).passed
        store.close()


@pytest.mark.asyncio
async def test_sidecar_tamper_never_becomes_an_accepted_understanding(tmp_path,monkeypatch):
    async with _sources(tmp_path, extra=True) as case:
        ledger=case.ledger
        seed_memories(ledger)
        store=SQLiteImmutableLifeContentStore(path=tmp_path/"content.sqlite",world_id=ledger.world_id)
        assert (await worker(ledger,store,Learner()).drain_one()).work_status=="reviewed"
        original=store.read_exact
        def replaced(*,content_ref):
            body=original(content_ref=content_ref)
            if content_ref.startswith("memory-review-decision:"):
                value=json.loads(body.text)
                value["batch"]["understandings"][0]["summary"]="没有角色作者依据的假理解"
                text=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))
                import hashlib
                return StoredLifeContent(content_ref=content_ref,content_kind=body.content_kind,
                    text=text,content_payload_hash=hashlib.sha256(text.encode()).hexdigest())
            return body
        monkeypatch.setattr(store,"read_exact",replaced)
        read=MemoryUnderstandingReader(ledger=ledger,content_store=store,actor_ref="agent:companion").read(ledger.project())
        assert not read.active and not read.acknowledged and read.suppressions
        store.close()


def test_quotes_and_operation_references_are_exact_not_model_self_attestation():
    payload={"contract":CONTRACT,"choices":[{"candidate_token":"m0","disposition":"retain","next_review_hours":72}],
        "understandings":[{"operation":"retain","predecessor_tokens":[],"source_tokens":["m0"],
            "facet":"表达","summary":"我觉得被听见很重要。","confidence_bp":6000,
            "evidence_quotes":[{"source_token":"m0","quote":"假定没有发生的事"}]}]}
    with pytest.raises(ValueError,match="exact memory"):
        validate_payload(payload,{"m0"},source_tokens={"m0"},predecessor_tokens=set(),
            readings={"m0":{"source_excerpts":[{"text":"有人安静地听我说完。"}]}})


def test_new_wire_has_one_source_selection_and_explicit_empty_update_array():
    from companion_daemon.world_v2.memory_consolidation_contract import UNDERSTANDING_CONTRACT
    cap={"understanding_contract":UNDERSTANDING_CONTRACT,"understanding_source_tokens":["m0"],"understanding_tokens":[]}
    compiled=tool_contract(capability_payload=cap,source_refs=("event:source",),recall_allowed=False)
    text=json.dumps(compiled.provider_tools)
    assert '"source_tokens"' not in text
    assert '"evidence_quotes"' in text and '"understandings"' in text
    payload={"contract":CONTRACT,"choices":[{"candidate_token":"m0","disposition":"retain","next_review_hours":24}],
             "understandings":[{"operation":"retain","predecessor_tokens":[],"facet":"表达","summary":"我在想。",
                 "confidence_bp":5000,"evidence_quotes":[{"source_token":"m0","quote":"有人"},{"source_token":"m0","quote":"听我说完"}]}]}
    validate_payload(payload,{"m0"},source_tokens={"m0"},predecessor_tokens=set(),
                     readings={"m0":{"source_excerpts":[{"text":"有人安静地听我说完。"}]}})


@pytest.mark.asyncio
async def test_later_public_character_turn_receives_the_accepted_standing_view(tmp_path):
    from companion_daemon.world_v2.character_interior import CharacterInterior
    from companion_daemon.world_v2.character_interior.authority import _DeferredInteriorAuthority
    from companion_daemon.world_v2.character_interior.production import _LedgerCapsuleInteriorProjection
    from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty
    from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
    async with _sources(tmp_path,extra=True) as case:
        ledger=case.ledger
        seed_memories(ledger)
        store=SQLiteImmutableLifeContentStore(path=tmp_path/"content.sqlite",world_id=ledger.world_id)
        learner=Learner()
        for n in range(4):
            if n:
                clock(ledger,"standing-eight:"+str(n),72)
            assert (await worker(ledger,store,learner).drain_one()).work_status=="reviewed"
        clock(ledger,"actual-core",72)
        class WitnessModel:
            model="offline-understanding-witness"
            supports_required_tool_choice=True
            calls=0
            async def complete_json(self,messages,*,temperature=0.8,tools,tool_choice):
                self.calls+=1
                packet=json.loads(messages[-1]["content"])
                cap=packet["capability_manifest"]["payload"]
                # Provider receives the same author-owned standing versions
                # through its pinned capability, even with duplicate context removed.
                assert "我也可以保留自己的不同想法。" in json.dumps(cap,ensure_ascii=False)
                return json.dumps({"status":"decision","summary":"我可以保留自己的不同想法。",
                        "attended_source_refs":["m0","u0"],"decision":{"payload":{
                        "choices":[{"candidate_token":t,"disposition":"retain","next_review_hours":72}
                                   for t in cap["offered_tokens"]],"understandings":[]}},
                    "recall_query":None,"proposals":[]},ensure_ascii=False)
            async def complete(self,messages,**kwargs):
                return await self.complete_json(messages,**kwargs)
        model=WitnessModel()
        projection=_LedgerCapsuleInteriorProjection(ledger=ledger,
            capsules=context_capsule_compiler_from_ledger(ledger=ledger,life_content_store=store),
            companion_actor_ref="agent:companion",
            memory_understanding_reader=MemoryUnderstandingReader(ledger=ledger,content_store=store,actor_ref="agent:companion"))
        core=CharacterInterior(projection=projection,role=StructuredCharacterRoleFaculty(model=model,model_id=model.model),
            authority=_DeferredInteriorAuthority())
        outcome=await worker(ledger,store,core).drain_one()
        assert outcome.work_status=="reviewed" and model.calls==1, (core.runtime_health().get("last_failure_code"), model.calls)
        assert not MemoryUnderstandingReader(ledger=ledger,content_store=store,actor_ref="agent:companion").read(ledger.project()).suppressions
        store.close()
