"""Actual selected Fact/Dialogue proof reaches focused review, not chat."""

import json
import hashlib
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy, SliceBudget
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_context import compile_life_review_context
from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentCapabilityManifest,
    parse_world_author_draft,
)
from companion_daemon.world_v2.life_development_source_closure import (
    life_development_novel_origin_messages,
)
from companion_daemon.world_v2.life_review_identity import (
    SOURCE_BOUND_NOVEL_EVIDENCE_PACKET_CONTRACT,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_fact_member_withdrawal import _FactModel, _record, _retain, _runtime
from test_interaction_fact_trigger_runtime import WORLD_ID
from test_life_development_audit_context_recovery import (
    _HTTP,
    _advance,
    _catalog,
    _composition,
    _model,
    _LegacyManifest,
    _CurrentManifest,
)
from test_life_development_runtime import NOW, OWNER, _seed_clock


class _CapturedHTTP(_HTTP):
    def __init__(self, wake, *, legacy=False):
        super().__init__(wake, legacy=legacy)
        self.wires = []

    def __call__(self, request):
        self.wires.append(request.content)
        return super().__call__(request)


def _source_compiler(ledger, store, observation, *, policy=None):
    return context_capsule_compiler_from_ledger(
        ledger=ledger,
        life_content_store=store,
        relevance_scope=ContextRelevanceScope(
            actor_ref=OWNER,
            related_subject_refs=(observation.actor,),
        ),
        policy=policy,
    )


@asynccontextmanager
async def _source_case(tmp_path, monkeypatch, *, version="4", extra=False, policy=None):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    issuer = AcceptedLedgerBatchIssuer()
    path = tmp_path / "sources.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    fact_model = _FactModel()
    observation = _record(ledger, 1, "用户决定取消周五的报告，仍保留周四的约定。")
    fact_model.results.append(_retain(observation.text))
    assert (await _runtime(ledger, issuer, fact_model).drain_one()).work_status == "accepted"
    if extra:
        for index, actor in ((2, observation.actor), (3, "user:unrelated")):
            item = _record(ledger, index, f"额外测试事实 {index}", actor=actor)
            fact_model.results.append(_retain(item.text))
            assert (
                await _runtime(ledger, issuer, fact_model).drain_one()
            ).work_status == "accepted"
    wake = _seed_clock(
        ledger,
        logical_time=NOW + timedelta(minutes=1),
        logical_time_from=ledger.project().logical_time,
    )
    provider = _CapturedHTTP(wake, legacy=version == "2")
    model = _model(provider)
    try:
        catalog = _catalog(tmp_path)
        manifest = {"2": _LegacyManifest, "3": _CurrentManifest}.get(version)
        runtime = _composition(
            ledger,
            store,
            catalog,
            model,
            compiler=manifest(catalog=catalog, store=store) if manifest else None,
        )
        runtime._capsule_compiler = _source_compiler(ledger, store, observation, policy=policy)
        capsule = runtime._capsule_compiler.compile_for_deliberation(
            query_from_projection(ledger.project(), actor_ref=OWNER, trigger_ref=wake.event_id)
        ).capsule
        yield SimpleNamespace(
            ledger=ledger,
            store=store,
            path=path,
            issuer=issuer,
            observation=observation,
            wake=wake,
            provider=provider,
            model=model,
            catalog=catalog,
            runtime=runtime,
            capsule=capsule,
        )
    finally:
        await model.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_public_fact_and_dialogue_proof_reaches_actual_focused_http(tmp_path, monkeypatch):
    async with _source_case(tmp_path, monkeypatch) as case:
        capsule, observation, provider = case.capsule, case.observation, case.provider
        assert capsule.provenance_kind == "trusted_resolver_compiled"
        chat = json.loads(capsule.model_content_json)
        assert capsule.relevant_facts.items and capsule.recent_dialogue.items
        for name in ("relevant_facts", "recent_dialogue"):
            assert all("source_bindings" not in item for item in chat["slices"][name]["items"])

        result = await _advance(case.runtime, case.wake)
        assert result.status == "occurrence_committed", result
        assert len(provider.requests) == 3
        print("actual_http_bytes", [len(wire) for wire in provider.wires])
        focused = json.loads(provider.requests[2]["messages"][1]["content"])
        assert (
            focused["evidence_packet_binding"]["contract"]
            == SOURCE_BOUND_NOVEL_EVIDENCE_PACKET_CONTRACT
        )
        evidence = focused["pinned_authority"]["existing_world_evidence"]["slices"]
        for name in ("relevant_facts", "recent_dialogue"):
            selected = getattr(capsule, name).items
            actual = evidence[name]
            assert [item["item_ref"] for item in actual] == [item.item_ref for item in selected]
            for item, original in zip(actual, selected, strict=True):
                assert item["authority_scope"] == "exact_source_bound_existing_truth"
                assert item["value"] == json.loads(original.payload_json)
                assert item["source_bindings"] == [
                    b.model_dump(mode="json") for b in original.source_bindings
                ]
                assert item["value_hash"] == original.value_hash
                assert item["source_hash"] == original.source_hash
        fact = evidence["relevant_facts"][0]["value"]
        assert fact["subject_ref"] == observation.actor
        assert fact["source_observation_id"] == observation.observation_id
        assert fact["observation_event_ref"] == "event:observation:member:1"
        assert fact["source_excerpt"] == observation.text
        assert capsule.model_content_json == json.dumps(
            chat, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )


@pytest.mark.asyncio
async def test_projection_keeps_selected_members_and_ignores_later_or_unrelated_state(
    tmp_path, monkeypatch
):
    policy = ContextCapsuleBudgetPolicy(
        relevant_facts=SliceBudget(max_items=1, max_fields=128, max_characters=8000),
        recent_dialogue=SliceBudget(max_items=1, max_fields=128, max_characters=8000),
    )
    async with _source_case(tmp_path, monkeypatch, extra=True, policy=policy) as case:
        original = json.loads(case.capsule.model_content_json)
        view = compile_life_review_context(case.capsule)
        for name in ("relevant_facts", "recent_dialogue"):
            lane = view["slices"][name]
            assert len(lane["items"]) == 1
            assert [item["item_ref"] for item in lane["items"]] == [
                item.item_ref for item in getattr(case.capsule, name).items
            ]
            assert "user:unrelated" not in json.dumps(lane)
            assert [item["privacy_class"] for item in lane["items"]] == [
                item.privacy_class for item in getattr(case.capsule, name).items
            ]
        for name in set(view["slices"]) - {"relevant_facts", "recent_dialogue"}:
            assert view["slices"][name] == original["slices"][name]
        _seed_clock(
            case.ledger,
            event_id="event:clock:later",
            logical_time=case.wake.logical_time + timedelta(minutes=1),
            logical_time_from=case.ledger.project().logical_time,
        )
        assert compile_life_review_context(case.capsule) == view


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["value_hash", "source_hash", "compiler_result_tag"])
async def test_exact_review_rejects_corrupt_original_capsule(tmp_path, monkeypatch, fault):
    async with _source_case(tmp_path, monkeypatch) as case:
        capsule = case.capsule
        if fault == "compiler_result_tag":
            capsule = capsule.model_copy(update={fault: "0" * 64})
        else:
            lane = capsule.relevant_facts
            corrupt = lane.items[0].model_copy(update={fault: "0" * 64})
            capsule = capsule.model_copy(
                update={
                    "relevant_facts": lane.model_copy(update={"items": (corrupt,)}),
                }
            )
        with pytest.raises(ValueError):
            compile_life_review_context(capsule)
        case.runtime._capsule_compiler = SimpleNamespace(
            compile_for_deliberation=lambda _query: SimpleNamespace(capsule=capsule),
        )
        result = await _advance(case.runtime, case.wake)
        assert result.status == "technical_failure"
        assert result.reason_code == "life_development.novel_origin_context_unavailable"
        assert len(case.provider.requests) == 2  # Author and general; no focused call.
        assert case.ledger.project().world_occurrences == ()


@pytest.mark.asyncio
async def test_empty_qualified_projection_does_not_create_fact_or_dialogue_evidence(
    tmp_path, monkeypatch
):
    async with _source_case(tmp_path, monkeypatch) as case:
        compiler = context_capsule_compiler_from_ledger(
            ledger=case.ledger,
            life_content_store=case.store,
            relevance_scope=ContextRelevanceScope(actor_ref=OWNER),
        )
        capsule = compiler.compile_for_deliberation(
            query_from_projection(
                case.ledger.project(),
                actor_ref=OWNER,
                trigger_ref=case.wake.event_id,
            )
        ).capsule
        view = compile_life_review_context(capsule)
        for name in ("relevant_facts", "recent_dialogue"):
            assert getattr(capsule, name).items == ()
            assert view["slices"][name] == json.loads(capsule.model_content_json)["slices"][name]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_projection", "baseline_only"])
async def test_qualified_packet_never_falls_back_to_slim_unproved_material(
    tmp_path, monkeypatch, fault
):
    async with _source_case(tmp_path, monkeypatch) as case:
        result = await _advance(case.runtime, case.wake)
        proposal = case.ledger.lookup_event_commit(result.proposal_event_ref)[0].payload()
        manifest = LifeDevelopmentCapabilityManifest.model_validate_json(
            json.dumps(
                proposal["world_author_deliberation"]["capability_manifest"],
            )
        )
        draft = parse_world_author_draft(
            raw=json.dumps(case.provider.draft),
            manifest=manifest,
            logical_time=case.wake.logical_time,
        )
        context = json.loads(case.capsule.model_content_json)
        if fault == "baseline_only":
            context["life_review_projection"] = compile_life_review_context(case.capsule)[
                "life_review_projection"
            ]
        with pytest.raises(ValueError, match="qualified Life review"):
            life_development_novel_origin_messages(context=context, manifest=manifest, draft=draft)
        assert len(case.provider.requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("version,wrong_packet", [("3", ".8"), ("4", ".7")])
async def test_public_acceptance_requires_manifest_exact_review_packet(
    tmp_path, monkeypatch, version, wrong_packet
):
    async with _source_case(tmp_path, monkeypatch, version=version) as case:
        commit = case.ledger.commit_at_cursor

        def changed_packet(events, **kwargs):
            altered = []
            for event in events:
                payload = event.payload()
                if (
                    event.event_type == "ProposalRecorded"
                    and payload.get("proposal_kind") == "life_development"
                ):
                    payload["world_author_novel_origin_evidence_packet_contract"] = (
                        "life-development-novel-origin-review-evidence-packet" + wrong_packet
                    )
                    raw = json.dumps(
                        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    )
                    event = event.model_copy(
                        update={
                            "payload_json": raw,
                            "payload_hash": hashlib.sha256(raw.encode()).hexdigest(),
                        }
                    )
                altered.append(event)
            return commit(tuple(altered), **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(case.ledger, "commit_at_cursor", changed_packet)
            with pytest.raises(ValueError, match="original manifest and review packets"):
                await _advance(case.runtime, case.wake)
        assert len(case.provider.requests) == 3
        assert case.ledger.project().world_occurrences == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2", "3", "4"])
async def test_original_proof_qualification_recovers_without_http_or_upgrading_audit(
    tmp_path, monkeypatch, version
):
    async with _source_case(tmp_path, monkeypatch, version=version) as case:
        commit = case.ledger.commit_at_cursor

        def interrupt_before_effect(events, **kwargs):
            if any(
                event.event_type == "ProposalRecorded"
                and event.payload().get("proposal_kind") == "life_development"
                for event in events
            ):
                raise InterruptedError("fixture: preserve original author and critic")
            return commit(events, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(case.ledger, "commit_at_cursor", interrupt_before_effect)
            with pytest.raises(InterruptedError, match="preserve original"):
                await _advance(case.runtime, case.wake)
        assert len(case.provider.requests) == 3
        focused = json.loads(case.provider.requests[2]["messages"][1]["content"])
        packet = focused["evidence_packet_binding"]["contract"]
        assert packet.endswith({"2": ".6", "3": ".7", "4": ".8"}[version])
        original_audits = tuple(
            item.audit_json for item in case.ledger.project().model_result_audits
        )
        assert all(
            item["authority_scope"]
            == (
                "exact_source_bound_existing_truth"
                if version == "4"
                else "capsule_bound_reviewer_baseline_only"
            )
            for item in focused["pinned_authority"]["existing_world_evidence"]["slices"][
                "relevant_facts"
            ]
        )
        case.store.close()
        case.ledger.close()
        ledger = SQLiteWorldLedger(path=case.path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=case.path, world_id=WORLD_ID)
        try:
            runtime = _composition(ledger, store, case.catalog, case.model)
            runtime._capsule_compiler = _source_compiler(ledger, store, case.observation)
            case.provider.reject_new_calls = True
            result = await _advance(runtime, case.wake)
            assert result.status == "occurrence_committed", result
            assert len(case.provider.requests) == 3
            assert (
                tuple(item.audit_json for item in ledger.project().model_result_audits)
                == original_audits
            )
            proposal = ledger.lookup_event_commit(result.proposal_event_ref)[0].payload()
            assert proposal["world_author_deliberation"]["capability_manifest"]["version"].endswith(
                "." + version
            )
            assert proposal["world_author_novel_origin_evidence_packet_contract"] == packet
            before_rebuild = ledger.project()
            ledger.rebuild()
            assert ledger.project() == before_rebuild
        finally:
            store.close()
            ledger.close()
