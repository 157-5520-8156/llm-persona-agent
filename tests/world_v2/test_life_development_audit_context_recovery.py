"""Production manifest migration at actual HTTP and durable recovery boundaries.

Provider responses are explicit fixtures, not semantic critic qualification.
"""

import json
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_capability import ProjectionLifeCapabilityManifestCompiler
from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentCapabilityManifest, LifeDevelopmentLocationCapability,
)
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy, SliceBudget
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.schemas import BudgetAccount, WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from current_activity_fixture import accepted_current_activity, CURRENT_ACTIVITY_INTENTION
from test_life_development_production import _open_life_seed
from test_life_development_runtime import (
    OWNER, WORLD_ID, _SequenceModel, _location_bound_world_draft,
    _novel_origin_review, _source_closure_review, _seed_clock, _commit_at_head,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _HTTP:
    def __init__(self, wake, *, legacy=False):
        self.wake = wake
        self.legacy = legacy
        self.requests = []
        self.draft = None
        self.reject_new_calls = False

    def __call__(self, request):
        wire = json.loads(request.content)
        self.requests.append(wire)
        if self.reject_new_calls:
            raise httpx.ConnectError("fixture: no new author response is available", request=request)
        user = json.loads(wire["messages"][1]["content"])
        if "review_contract" in user:
            raw = (
                _novel_origin_review(decision="supported")
                if "focused novel-origin critic" in wire["messages"][0]["content"]
                else _source_closure_review(decision="supported")
            )
        else:
            capability = LifeDevelopmentLocationCapability.model_validate_json(
                _json({key: value for key, value in user["capability_manifest"]["location_capabilities"][0].items()
                       if key != "capability_ref"})
            )
            value = json.loads(_location_bound_world_draft(
                wake=self.wake, capability=capability,
                timing={"mode": "now", "duration_minutes": 20},
                privacy_class=capability.privacy_class, causal_authority="world_contingency",
                outcome_resolution_authority="world_contingency",
            ))
            value["premise"] = "公园开始降下冰雹。"
            value["claim_declarations"][0]["summary"] = "公园出现冰雹天气。"
            for outcome in value["outcomes"]:
                outcome.pop("text")
                if self.legacy:
                    outcome["text"] = "冰雹打断了一截树枝。"
                else:
                    outcome["world_consequence"] = {
                        "contract": "world-consequence.2", "environment_text": "冰雹打断了一截树枝。",
                    }
            self.draft = value
            raw = _json(value)
        return httpx.Response(200, json={
            "id": "offline-manifest", "model": wire["model"],
            "choices": [{"message": ({"tool_calls": [{"type": "function", "function": {"name": wire["tools"][0]["function"]["name"], "arguments": raw}}]} if wire.get("tools") else {"role": "assistant", "content": raw})}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        })


def _composition(ledger, store, catalog, model, *, focused=True, compiler=None):
    capsule = context_capsule_compiler_from_ledger(
        ledger=ledger, life_content_store=store,
        relevance_scope=ContextRelevanceScope(actor_ref=OWNER),
        policy=ContextCapsuleBudgetPolicy(
            world_life=SliceBudget(max_items=5, max_fields=192, max_characters=22_000),
        ),
    )
    return LifeDevelopmentRuntime(
        ledger=ledger, content_store=store, world_author=model,
        character_interior=_SequenceModel(model="unused-character", outputs=()),
        source_closure_reviewer=model, novel_origin_critic=model if focused else None,
        capsule_compiler=capsule,
        capability_manifest_compiler=compiler or ProjectionLifeCapabilityManifestCompiler(
            owner_actor_ref=OWNER, catalog=catalog, content_store=store,
        ),
        owner_actor_ref=OWNER,
    )


def _catalog(tmp_path):
    return ReviewedLifeSeedCatalog.from_yaml(
        path=_open_life_seed(tmp_path / "seed.yaml"), chronology=LocalChronology("Asia/Shanghai"),
    )


class _LegacyManifest:
    """Explicit production.2 historical fixture, never a fresh author default."""

    def __init__(self, *, catalog, store):
        self.compiler = ProjectionLifeCapabilityManifestCompiler(
            owner_actor_ref=OWNER, catalog=catalog, content_store=store,
        )

    def compile(self, **kwargs):
        value = self.compiler.compile(**kwargs).model_dump(mode="json", round_trip=True)
        value.pop("outcome_contract", None)
        value.pop("execution_intention_sources_version", None)
        value.pop("semantic_source_review_version", None)
        value["version"] = "life-development-capability.production.2"
        return LifeDevelopmentCapabilityManifest.model_validate_json(_json(value))


class _CurrentManifest(_LegacyManifest):
    def compile(self, **kwargs):
        value = super().compile(**kwargs).model_dump(mode="json", round_trip=True)
        value["outcome_contract"] = "world-consequence.2"
        value["version"] = "life-development-capability.production.3"
        return LifeDevelopmentCapabilityManifest.model_validate_json(_json(value))


def _model(provider):
    return DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(provider),
    )


async def _advance(runtime, wake):
    return await runtime.advance_once(
        wake_event_ref=wake.event_id, trace_id="trace:manifest-default",
        correlation_id="correlation:manifest-default",
    )


@asynccontextmanager
async def _interrupted_author(tmp_path, monkeypatch, *, legacy=True, active=False):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "legacy.sqlite"
    if active:
        ledger, store, _, _ = await accepted_current_activity(sqlite_path=path)
        previous = ledger.project().logical_time
        wake = _seed_clock(ledger, event_id="event:clock:audit-recovery",
                           logical_time=previous + timedelta(minutes=1), logical_time_from=previous)
    else:
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        wake = _seed_clock(ledger)
    world_id = ledger.world_id
    provider = _HTTP(wake, legacy=legacy)
    model = _model(provider)
    catalog = _catalog(tmp_path)
    try:
        runtime = _composition(
            ledger, store, catalog, model,
            compiler=(_LegacyManifest if legacy else _CurrentManifest)(catalog=catalog, store=store),
        )
        query = query_from_projection(ledger.project(), actor_ref=OWNER, trigger_ref=wake.event_id)
        original_capsule = runtime._capsule_compiler.compile_for_deliberation(query).capsule
        if active:
            assert CURRENT_ACTIVITY_INTENTION in original_capsule.model_content_json
        commit = ledger.commit_at_cursor

        def interrupt_before_effect(events, **kwargs):
            if any(event.event_type == "ProposalRecorded"
                   and event.payload().get("proposal_kind") == "life_development" for event in events):
                raise InterruptedError("fixture: original author and reviews are durable")
            return commit(events, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(ledger, "commit_at_cursor", interrupt_before_effect)
            with pytest.raises(InterruptedError, match="original author and reviews"):
                await _advance(runtime, wake)
        assert len(provider.requests) == 3
        original_user = json.loads(provider.requests[0]["messages"][1]["content"])
        assert ("outcome_contract" not in original_user["capability_manifest"]) == legacy
        original_audits = tuple(item.audit_json for item in ledger.project().model_result_audits)
        assert ledger.project().world_occurrences == ()
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=world_id)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=world_id)
        runtime = _composition(ledger, store, catalog, model,
                               compiler=_CurrentManifest(catalog=catalog, store=store))
        yield SimpleNamespace(
            ledger=ledger, store=store, runtime=runtime, provider=provider, wake=wake,
            query=query, original_capsule=original_capsule, original_audits=original_audits,
            original_user=original_user,
        )
    finally:
        await model.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("active", [False, True])
async def test_original_author_and_reviews_recover_at_exact_real_context_without_new_http(
    tmp_path, monkeypatch, legacy, active,
):
    async with _interrupted_author(tmp_path, monkeypatch, legacy=legacy, active=active) as case:
        compiler = case.runtime._capsule_compiler
        with pytest.raises(ValueError, match="historical Context resolution"):
            compiler.compile_for_deliberation(case.query)
        assert compiler.compile_for_audit_recovery(case.query) == case.original_capsule
        result = await _advance(case.runtime, case.wake)
        assert result.status == "occurrence_committed", result
        assert len(case.provider.requests) == 3
        assert tuple(item.audit_json for item in case.ledger.project().model_result_audits) == case.original_audits
        proposal = case.ledger.lookup_event_commit(result.proposal_event_ref)[0].payload()
        original_manifest = proposal["world_author_deliberation"]["capability_manifest"]
        authority = LifeDevelopmentCapabilityManifest.model_validate_json(_json(original_manifest))
        assert authority.manifest_hash == case.original_user["capability_manifest"]["manifest_hash"]
        assert authority.pinned_cursor == case.query.cursor
        assert authority.outcome_contract == (None if legacy else "world-consequence.2")
        assert authority.version == "life-development-capability.production." + ("2" if legacy else "3")
        assert proposal["world_author_deliberation"]["capsule_id"] == case.original_capsule.capsule_id
        assert all(item.result_contract == (None if legacy else "world-consequence.2")
                   for item in case.ledger.project().world_occurrences[0].candidate_outcomes)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["model_content_json", "capsule_id", "snapshot_hash", "deliberation_revision", "missing_prefix", "storage_failure"])
async def test_recovery_rejects_unavailable_or_changed_original_context_without_reauthoring(tmp_path, monkeypatch, fault):
    async with _interrupted_author(tmp_path, monkeypatch) as case:
        before = case.ledger.export_replay_evidence()
        if fault in {"missing_prefix", "storage_failure"}:
            read = case.ledger.project_at
            def unavailable(cursor):
                if cursor == case.query.cursor:
                    if fault == "storage_failure":
                        raise OSError("fixture: original prefix storage unavailable")
                    raise ValueError("fixture: original prefix unavailable")
                return read(cursor)
            monkeypatch.setattr(case.ledger, "project_at", unavailable)
        else:
            compiler = case.runtime._capsule_compiler
            recover = compiler.compile_for_audit_recovery
            def changed(query):
                capsule = recover(query)
                value = getattr(capsule, fault)
                return capsule.model_copy(update={fault: value + 1 if isinstance(value, int) else value + "changed"})
            monkeypatch.setattr(compiler, "compile_for_audit_recovery", changed)
        result = await _advance(case.runtime, case.wake)
        assert result.status == "technical_failure"
        assert result.reason_code == "life_development.recovered_context_bytes_unavailable"
        assert len(case.provider.requests) == 3
        assert case.ledger.export_replay_evidence() == before


@pytest.mark.asyncio
async def test_later_budget_and_model_audits_cannot_enter_recovered_context_or_reuse_stale_author(tmp_path, monkeypatch):
    async with _interrupted_author(tmp_path, monkeypatch, active=True) as case:
        state = case.ledger.project()
        later_audit = state.model_result_audits[-1]
        budget = WorldEvent.from_payload(
            schema_version="world-v2.1", event_id="event:budget:after-author", world_id=case.ledger.world_id,
            event_type="BudgetAccountConfigured", logical_time=state.logical_time, created_at=state.logical_time,
            actor="operator:fixture", source="test", trace_id="trace:budget", causation_id=case.wake.event_id,
            correlation_id="correlation:budget", idempotency_key="key:budget:after-author",
            payload={"account": BudgetAccount(account_id="budget:after-author", category="audit",
                                              window_id="fixture", limit=1000).model_dump(mode="json")},
        )
        _commit_at_head(case.ledger, budget)
        compiler = case.runtime._capsule_compiler
        recovered = compiler.compile_for_audit_recovery(case.query)
        assert recovered == case.original_capsule
        assert "budget:after-author" not in recovered.model_content_json
        assert CURRENT_ACTIVITY_INTENTION in recovered.model_content_json
        resolver = compiler._resolver.for_audit_recovery(case.query)
        reader = resolver._ledger
        assert not hasattr(reader, "commit_at_cursor") and not hasattr(reader, "commit")
        assert reader.lookup_event_commit(budget.event_id) is None
        assert reader.lookup_event_commit(later_audit.event_ref) is None
        with pytest.raises(ValueError, match="audited prefix"):
            reader.project_at(query_from_projection(case.ledger.project(), actor_ref=OWNER,
                                                   trigger_ref=case.wake.event_id).cursor)
        case.provider.reject_new_calls = True
        result = await _advance(case.runtime, case.wake)
        assert result.status == "technical_failure"
        assert result.reason_code == "life_development.world_author_unavailable"
        assert len(case.provider.requests) > 3  # World changed; old successful author was not reused.
        assert case.ledger.project().world_occurrences == ()


def test_context_recovery_preserves_initial_world_time_authority_after_a_later_clock():
    from test_ledger_context_resolver import _event
    ledger = WorldLedger.in_memory(world_id="world:genesis-recovery")
    start = _event(ledger.world_id)
    _commit_at_head(ledger, start)
    compiler = context_capsule_compiler_from_ledger(
        ledger=ledger, relevance_scope=ContextRelevanceScope(actor_ref=OWNER),
    )
    query = query_from_projection(ledger.project(), actor_ref=OWNER, trigger_ref=start.event_id)
    original = compiler.compile_for_deliberation(query).capsule
    _seed_clock(ledger, logical_time_from=start.logical_time,
                logical_time=start.logical_time + timedelta(minutes=1))
    assert compiler.compile_for_audit_recovery(query) == original
