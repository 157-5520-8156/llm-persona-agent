"""Actual selected Fact/Dialogue proof reaches focused review, not chat."""

import json
from datetime import timedelta

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_fact_member_withdrawal import _FactModel, _record, _retain, _runtime
from test_interaction_fact_trigger_runtime import WORLD_ID
from test_life_development_audit_context_recovery import (
    _HTTP, _advance, _catalog, _composition, _model,
)
from test_life_development_runtime import NOW, OWNER, _seed_clock


class _CapturedHTTP(_HTTP):
    def __init__(self, wake):
        super().__init__(wake)
        self.wires = []

    def __call__(self, request):
        self.wires.append(request.content)
        return super().__call__(request)


@pytest.mark.asyncio
async def test_public_fact_and_dialogue_proof_reaches_actual_focused_http(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    issuer = AcceptedLedgerBatchIssuer()
    path = tmp_path / "sources.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    fact_model = _FactModel()
    observation = _record(ledger, 1, "用户决定取消周五的报告，仍保留周四的约定。")
    fact_model.results.append(_retain(observation.text))
    assert (await _runtime(ledger, issuer, fact_model).drain_one()).work_status == "accepted"
    wake = _seed_clock(ledger, logical_time=NOW + timedelta(minutes=1),
                       logical_time_from=ledger.project().logical_time)
    provider = _CapturedHTTP(wake)
    model = _model(provider)
    try:
        runtime = _composition(ledger, store, _catalog(tmp_path), model)
        runtime._capsule_compiler = context_capsule_compiler_from_ledger(
            ledger=ledger, life_content_store=store,
            relevance_scope=ContextRelevanceScope(
                actor_ref=OWNER, related_subject_refs=(observation.actor,),
            ),
        )
        capsule = runtime._capsule_compiler.compile_for_deliberation(
            query_from_projection(ledger.project(), actor_ref=OWNER, trigger_ref=wake.event_id)
        ).capsule
        assert capsule.provenance_kind == "trusted_resolver_compiled"
        chat = json.loads(capsule.model_content_json)
        assert capsule.relevant_facts.items and capsule.recent_dialogue.items
        for name in ("relevant_facts", "recent_dialogue"):
            assert all("source_bindings" not in item for item in chat["slices"][name]["items"])

        await _advance(runtime, wake)
        assert len(provider.requests) == 2
        print("actual_http_bytes", [len(wire) for wire in provider.wires])
        focused = json.loads(provider.requests[1]["messages"][1]["content"])
        evidence = focused["pinned_authority"]["existing_world_evidence"]["slices"]
        for name in ("relevant_facts", "recent_dialogue"):
            selected = getattr(capsule, name).items
            actual = evidence[name]
            assert [item["item_ref"] for item in actual] == [item.item_ref for item in selected]
            for item, original in zip(actual, selected, strict=True):
                assert item["authority_scope"] == "exact_source_bound_existing_truth"
                assert item["value"] == json.loads(original.payload_json)
                assert item["source_bindings"] == [b.model_dump(mode="json") for b in original.source_bindings]
                assert item["value_hash"] == original.value_hash
                assert item["source_hash"] == original.source_hash
        fact = evidence["relevant_facts"][0]["value"]
        assert fact["subject_ref"] == observation.actor
        assert fact["source_observation_id"] == observation.observation_id
        assert fact["observation_event_ref"] == "event:observation:member:1"
        assert fact["source_excerpt"] == observation.text
        assert capsule.model_content_json == json.dumps(chat, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    finally:
        await model.aclose()
        store.close()
        ledger.close()
