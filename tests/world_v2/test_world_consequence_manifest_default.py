"""Production manifest migration at actual HTTP and durable recovery boundaries.

Provider responses are explicit fixtures, not semantic critic qualification.
"""

import hashlib
import json

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
from companion_daemon.world_v2.life_development_draft import LifeDevelopmentLocationCapability
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_production import _open_life_seed
from test_life_development_runtime import (
    OWNER, WORLD_ID, _SequenceModel, _location_bound_world_draft,
    _novel_origin_review, _seed_clock,
)
from test_world_consequence_producer import _assert_occurrence, _assert_review_input


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _HTTP:
    def __init__(self, wake):
        self.wake = wake
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
                _json({key: value for key, value in user["capability_manifest"]["location_capabilities"][0].items()
                       if key != "capability_ref"})
            )
            value = json.loads(_location_bound_world_draft(
                wake=self.wake, capability=capability,
                timing={"mode": "now", "duration_minutes": 20},
                privacy_class="shareable", causal_authority="world_contingency",
                outcome_resolution_authority="world_contingency",
            ))
            value["premise"] = "公园开始降下冰雹。"
            value["claim_declarations"][0]["summary"] = "公园出现冰雹天气。"
            for outcome in value["outcomes"]:
                outcome.pop("text")
                outcome["world_consequence"] = {
                    "contract": "world-consequence.2", "environment_text": "冰雹打断了一截树枝。",
                }
            self.draft = value
            raw = _json(value)
        return httpx.Response(200, json={
            "id": "offline-manifest", "model": wire["model"],
            "choices": [{"message": {"role": "assistant", "content": raw}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        })


def _composition(ledger, store, catalog, model, *, focused=True, compiler=None):
    capsule = context_capsule_compiler_from_ledger(
        ledger=ledger, life_content_store=store,
        relevance_scope=ContextRelevanceScope(actor_ref=OWNER),
    )
    return LifeDevelopmentRuntime(
        ledger=ledger, content_store=store, world_author=model,
        character_interior=_SequenceModel(model="unused-character", outputs=()),
        source_closure_reviewer=None, novel_origin_critic=model if focused else None,
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


@pytest.mark.asyncio
async def test_production_manifest_requests_current_consequences_through_http_and_acceptance(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "current.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    provider = _HTTP(wake)
    model = _model(provider)
    try:
        result = await _advance(_composition(ledger, store, _catalog(tmp_path), model), wake)
        user = json.loads(provider.requests[0]["messages"][1]["content"])
        assert user["capability_manifest"]["outcome_contract"] == "world-consequence.2"
        assert user["capability_manifest"]["version"] == "life-development-capability.production.3"
        _assert_occurrence(ledger, store, result, provider.draft)
        assert len(provider.requests) == 2  # author + existing focused lane only
        _assert_review_input(provider.requests[1]["messages"], user, provider.draft, focused=True)
        raw_messages = _json(provider.requests[0]["messages"])
        request_hash = hashlib.sha256(raw_messages.encode()).hexdigest()
        assert store.read_exact(content_ref="content:world-author-request:" + request_hash).text == raw_messages
        audits = [json.loads(item.audit_json) for item in ledger.project().model_result_audits]
        assert len(audits) == 3  # general closure retains its deterministic audit
        assert any(item["model_id"] == "deterministic:life-source-closure" for item in audits)
    finally:
        await model.aclose()
        store.close()
        ledger.close()
