"""Production manifest migration at actual HTTP and durable recovery boundaries.

Provider responses are explicit fixtures, not semantic critic qualification.
"""

import hashlib
import json

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
    OWNER,
    WORLD_ID,
    _SequenceModel,
    _location_bound_world_draft,
    _novel_origin_review,
    _seed_clock,
)
from test_world_consequence_producer import _assert_occurrence, _assert_review_input


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _HTTP:
    def __init__(self, wake, *, legacy=False):
        self.wake = wake
        self.legacy = legacy
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
                    causal_authority="world_contingency",
                    outcome_resolution_authority="world_contingency",
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
        assert original_manifest == {
            key: value for key, value in visible_manifest.items() if key != "manifest_hash"
        }
        assert proposal["capability_manifest_hash"] == visible_manifest["manifest_hash"]
        assert all(
            item.result_contract is None
            for item in ledger.project().world_occurrences[0].candidate_outcomes
        )
    finally:
        await model.aclose()
        store.close()
        ledger.close()
