"""Production Life must ask a model whether an exact source entails a claim.

Verdicts are explicit offline provider fixtures. These tests verify authority,
request/usage auditing and recovery; they do not qualify a real critic's accuracy.
"""

import json
from datetime import timedelta


import pytest

from companion_daemon.config import Settings
from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
from test_qq_c2c_host_migration import (
    NOW, _Delivery, _NamedCompactReviewNoCallModel,
    _SelectingLifeEcologyModel, _SupportingLifeSourceReviewer,
)
CLAIM = "昨天她买下了这家旧书店，产权登记在她名下。"


class _SourceReviewer(_SupportingLifeSourceReviewer):
    def __init__(self, *, reject=True):
        self.calls = []
        self.reject = reject

    async def complete(self, messages, *, temperature=0.0):
        focused = "focused novel-origin critic" in messages[0]["content"]
        self.calls.append(("focused" if focused else "general", messages))
        if not focused and self.reject and CLAIM in messages[1]["content"]:
            packet = json.loads(messages[1]["content"])
            events = packet["pinned_source_evidence"]["cited_committed_events"]
            assert len(events) == 1 and events[0]["event_type"] == "ClockAdvanced"
            assert CLAIM not in json.dumps(events, ensure_ascii=False)
            return json.dumps({
                "decision": "unsupported", "unsupported_claim_ids": ["local:claim:walk"],
                "reason": "The cited clock event does not establish the property purchase.",
            })
        return await super().complete(messages, temperature=temperature)


class _WorldAuthor(_SelectingLifeEcologyModel):
    def __init__(self, reviewer, *, repair=False):
        self.reviewer = reviewer
        self.repair = repair
        self.authored = []
        self.author_requests = []

    async def complete(self, messages, *, temperature=0.2):
        system = messages[0]["content"]
        if "source-closure reviewer" in system or "focused novel-origin critic" in system:
            return await self.reviewer.complete(messages, temperature=temperature)
        raw = await super().complete(messages, temperature=temperature)
        if "You are the World Author" in system:
            value = json.loads(raw)
            context = json.loads(messages[1]["content"])
            ref = context["capability_manifest"]["anchor_refs"][0]
            if not self.repair or not self.authored:
                value["premise"] = CLAIM
                value["claim_declarations"][0].update(
                    summary=CLAIM, scope="existing_world", source_refs=[ref]
                )
            self.author_requests.append(messages)
            self.authored.append(value)
            return json.dumps(value, ensure_ascii=False)
        return raw


@pytest.mark.asyncio
@pytest.mark.parametrize("self_review", [False, True])
@pytest.mark.parametrize("repair", [False, True])
async def test_clock_cannot_authorize_existing_history_even_with_configured_focused_review(
    tmp_path, monkeypatch, self_review, repair
):
    import companion_daemon.world_v2.life_development_runtime as runtime
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    monkeypatch.setattr(runtime.LifeDevelopmentRuntime, "_resolve_occasion_draw",
                        lambda self, **kwargs: runtime.LIFE_DEVELOPMENT_OPPORTUNITY_REF)
    reviewer = _SourceReviewer()
    author = _WorldAuthor(reviewer, repair=repair)
    settings = Settings(
        _env_file=None, database_path=tmp_path / "world.sqlite",
        WORLD_V2_LIFE_SOURCE_REVIEW_ENABLED=self_review,
        WORLD_V2_LIFE_SELF_REVIEW_ALLOWED=self_review,
    )
    host = build_qq_c2c_host(
        settings=settings, recipient_id="10001", bootstrap_at=NOW,
        model=author, world_support_model=author,
        source_closure_model=_NamedCompactReviewNoCallModel("conversation-not-used"),
        life_source_closure_model=None if self_review else reviewer,
        delivery=_Delivery(), use_configured_recall_embedding=False,
    )
    try:
        assert host._semantic_chat.life_source_runtime_isolation == (
            "self_review_operator_approved" if self_review else "independent"
        )
        await host.tick(
            tick_id="semantic-review-counterexample", logical_time_from=NOW,
            logical_time_to=NOW + timedelta(minutes=1),
            observed_at=NOW + timedelta(minutes=1), reason="offline_semantic_review",
        )
        projection = host.export_replay_evidence().projection
        assert len(projection.plans) == (1 if repair else 0)
        assert not projection.world_occurrences
        assert not projection.experiences
        # An identical rejected rewrite reuses its exact unsupported verdict;
        # a different, legal novel environment must receive both real reviews.
        assert [lane for lane, _messages in reviewer.calls] == (
            ["general", "general", "focused"] if repair else ["general"]
        )
        assert len(author.authored) == 2, "the same World Author gets only one source correction"
        original, correction = author.author_requests
        assert correction[:-2] == original
        assert json.loads(correction[-2]["content"]) == author.authored[0]
        assert json.loads(correction[-1]["content"])["source_closure_failure"]["unsupported_claim_ids"] == ["local:claim:walk"]
        assert original[1]["content"] == correction[1]["content"]
        manifest = json.loads(original[1]["content"])["capability_manifest"]
        assert manifest["semantic_source_review_version"] == "1"
        for _lane, request in reviewer.calls:
            assert ("Independence from the author authority is not established" in request[0]["content"]) == self_review
            assert ("You are an independent" in request[0]["content"]) != self_review
        audits = [json.loads(item.audit_json) for item in projection.model_result_audits]
        assert not any(str(item.get("model_id")).startswith("deterministic:") for item in audits)
    finally:
        await host.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("committed", [False, True])
async def test_cold_history_stays_frozen_but_pending_deterministic_success_is_not_authority(
    tmp_path, monkeypatch, historical, committed,
):
    import companion_daemon.world_v2.life_development_runtime as module
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.life_development_deterministic_closure import (
        closure_review_raw, evaluate_general_source_closure,
    )
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from test_life_development_runtime import (
        WORLD_ID, NOW as LIFE_NOW, _SequenceModel, _runtime,
        _novel_book_exchange_draft, _source_closure_review, _seed_clock,
    )

    path = tmp_path / "cold.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    draft = _novel_book_exchange_draft(wake=wake)
    if historical:
        draft["premise"] = CLAIM
        draft["claim_declarations"][0].update(
            summary=CLAIM, scope="existing_world", source_refs=[wake.event_id],
        )
    author = _SequenceModel(model="fixture:author", outputs=(json.dumps(draft),))
    reviewer = _SequenceModel(model="fixture:general", outputs=(
        _source_closure_review(decision="supported"),
    ))
    character = _SequenceModel(model="fixture:character", outputs=(json.dumps({
        "decision": "accept", "intention_summary": "我打算去看看新出现的书摊。",
        "importance_bp": 3600, "participant_refs": [],
        "opens_at": (LIFE_NOW + timedelta(hours=2)).isoformat(),
        "closes_at": (LIFE_NOW + timedelta(hours=4)).isoformat(),
    }),))
    runtime, _ = _runtime(ledger=ledger, wake=wake, store=store,
                          world_author=author, character_interior=character,
                          source_closure_reviewer=reviewer)
    async def advance(current):
        return await current.advance_once(wake_event_ref=wake.event_id,
                                          trace_id="trace:semantic-cold", correlation_id="cold")

    async def old_existence_review(*, messages, draft, cited_events):
        parsed = evaluate_general_source_closure(draft=draft, cited_events=cited_events)
        return module._LifeDevelopmentModelRun(
            model_id="deterministic:life-source-closure", parsed=parsed,
            attempts=(module._LifeDevelopmentAttempt(
                request_hash=module._messages_hash(messages), raw_output=closure_review_raw(parsed),
                status="proposal_validated",
            ),),
        )

    def interrupted(**kwargs):
        raise InterruptedError("fixture: after durable deliberations, before Plan")

    try:
        with monkeypatch.context() as patch:
            if historical:
                patch.setattr(runtime, "_source_closure_review", old_existence_review)
                # Reproduce the actual pre-repair request compiler. Neither the
                # saved author manifest nor review bytes claimed semantic authority.
                for name in ("life_development_source_closure_messages", "life_development_novel_origin_messages"):
                    original = getattr(module, name)
                    def old_messages(*, _original=original, **kwargs):
                        kwargs.pop("reviewer_is_independent", None)
                        return _original(**kwargs)
                    patch.setattr(module, name, old_messages)
            if committed:
                result = await advance(runtime)
                assert result.status == "plan_committed"
            else:
                patch.setattr(runtime, "_commit_character_plan", interrupted)
                with pytest.raises(InterruptedError, match="before Plan"):
                    await advance(runtime)
        before = ledger.export_replay_evidence()
        original_audits = {item.event_ref: item.audit_json for item in before.projection.model_result_audits}
        assert bool(before.projection.plans) == committed
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        cold_author = _SequenceModel(model="fixture:author", outputs=())
        cold_character = _SequenceModel(model="fixture:character", outputs=())
        cold_reviewer = _SequenceModel(model="fixture:general", outputs=(
            _source_closure_review(decision="unsupported", unsupported_claim_ids=("local:claim:book-exchange",)),
        ) if historical and not committed else ())
        cold, _ = _runtime(ledger=ledger, wake=wake, store=store, world_author=cold_author,
                           character_interior=cold_character, source_closure_reviewer=cold_reviewer)
        recovered = await advance(cold)
        assert cold_author.calls == cold_character.calls == 0
        assert cold_reviewer.calls == (1 if historical and not committed else 0)
        if historical and not committed:
            assert recovered.status == "technical_failure"
            assert recovered.reason_code == "life_development.source_closure_rejected"
            assert not ledger.project().plans
            assert not ledger.project().world_occurrences
        else:
            assert recovered.status == "plan_committed"
            assert len(ledger.project().plans) == 1
        for item in ledger.project().model_result_audits:
            if item.event_ref in original_audits:
                assert item.audit_json == original_audits[item.event_ref]
        if committed:
            assert ledger.export_replay_evidence() == before
        else:
            again = ledger.export_replay_evidence()
            assert (await advance(cold)).status == recovered.status
            assert ledger.export_replay_evidence() == again
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", ["supported", "invalid_then_supported", "invalid_twice", "budget_denied"])
async def test_general_review_actual_http_usage_wire_correction_and_cold_failure(
    tmp_path, monkeypatch, wire,
):
    import sqlite3
    import httpx
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from test_life_development_runtime import WORLD_ID, _SequenceModel, _seed_clock, _source_closure_review, _novel_origin_review
    from test_world_consequence_producer import _runtime, _draft, _advance

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "wire.sqlite"
    usage_path = tmp_path / "usage.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    usage = WorldV2UsageStore(path=str(usage_path), background_daily_budget_cny=0.0 if wire == "budget_denied" else 100.0)
    wake = _seed_clock(ledger)
    requests = []
    def provider(request):
        value = json.loads(request.content)
        requests.append(value)
        assert wire != "budget_denied", "denied Life review reached HTTP"
        invalid = wire == "invalid_twice" or (wire == "invalid_then_supported" and len(requests) == 1)
        content = '{"decision":"not_a_verdict"}' if invalid else _source_closure_review(decision="supported")
        return httpx.Response(200, json={
            "model": value["model"], "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 111, "completion_tokens": 37, "total_tokens": 148},
        })
    reviewer = DeepSeekChatModel("offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
                                thinking_enabled=False, transport=httpx.MockTransport(provider),
                                usage_observer=usage.record)
    author = _SequenceModel(model="fixture:author", outputs=(json.dumps(_draft(wake)),))
    focused = _SequenceModel(model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),))
    try:
        result = await _advance(_runtime(ledger, store, wake, author, reviewer, focused), wake)
        succeeded = wire in {"supported", "invalid_then_supported"}
        assert result.status == ("occurrence_committed" if succeeded else "technical_failure")
        assert len(requests) == {"supported": 1, "invalid_then_supported": 2, "invalid_twice": 2, "budget_denied": 0}[wire]
        assert focused.calls == int(succeeded)
        assert bool(ledger.project().world_occurrences) == succeeded
        if len(requests) == 2:
            assert requests[1]["messages"][:-2] == requests[0]["messages"]
            assert "not_a_verdict" in requests[1]["messages"][-2]["content"]
            assert "invalid" in requests[1]["messages"][-1]["content"]
        with sqlite3.connect(usage_path) as db:
            rows = db.execute("SELECT purpose,status,prompt_tokens,completion_tokens,error FROM world_v2_model_usage").fetchall()
        assert len(rows) == max(1, len(requests))
        assert all(row[0] == "life_development_source_closure_review" for row in rows)
        if wire == "budget_denied":
            assert rows == [("life_development_source_closure_review", "budget_denied", 0, 0, "background_daily_budget_exceeded")]
            assert result.reason_code == "life_development.source_closure_reviewer_unavailable"
        else:
            assert all(row[2:4] == (111, 37) for row in rows)
        audits = [json.loads(item.audit_json) for item in ledger.project().model_result_audits]
        actual = [item for item in audits if item.get("model_id") == "deepseek-v4-flash" or item.get("attempted_model_id") == "deepseek-v4-flash"]
        assert len(actual) == max(1, len(requests))
        assert not any(str(item.get("model_id")).startswith("deterministic:") for item in audits)
        before = ledger.export_replay_evidence()
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        cold_author = _SequenceModel(model="fixture:author", outputs=())
        cold_focused = _SequenceModel(model="fixture:focused", outputs=())
        cold = await _advance(_runtime(ledger, store, wake, cold_author, reviewer, cold_focused), wake)
        assert cold.status == result.status
        assert cold_focused.calls == cold_author.calls == 0
        assert ledger.export_replay_evidence() == before
        with sqlite3.connect(usage_path) as db:
            assert db.execute("SELECT COUNT(*) FROM world_v2_model_usage").fetchone()[0] == len(rows)
    finally:
        await reviewer.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_production_nonledger_policy_material_reaches_real_general_reviewer(tmp_path, monkeypatch):
    import companion_daemon.world_v2.life_development_runtime as runtime
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    monkeypatch.setattr(runtime.LifeDevelopmentRuntime, "_resolve_occasion_draw",
                        lambda self, **kwargs: runtime.LIFE_DEVELOPMENT_OPPORTUNITY_REF)
    class PolicyAuthor(_SelectingLifeEcologyModel):
        manifest = None
        ref = None
        async def complete(self, messages, *, temperature=0.2):
            raw = await super().complete(messages, temperature=temperature)
            if "You are the World Author" not in messages[0]["content"]:
                return raw
            self.manifest = json.loads(messages[1]["content"])["capability_manifest"]
            capability = self.manifest["location_capabilities"][0]
            self.ref = capability["authority_refs"][0]
            value = json.loads(raw)
            claim_id = "local:claim:policy"
            value["claim_declarations"].append({
                "claim_id": claim_id, "summary": "地点能力登记有开放时段。",
                "scope": "existing_world", "subject_scope": "world_environment",
                "source_refs": [self.ref],
            })
            value["premise_claim_refs"].append(claim_id)
            return json.dumps(value, ensure_ascii=False)
    reviewer = _SourceReviewer(reject=False)
    author = PolicyAuthor()
    host = build_qq_c2c_host(
        settings=Settings(_env_file=None, database_path=tmp_path / "policy.sqlite"),
        recipient_id="10001", bootstrap_at=NOW, model=author,
        source_closure_model=_NamedCompactReviewNoCallModel("conversation-not-used"),
        life_source_closure_model=reviewer, delivery=_Delivery(),
        use_configured_recall_embedding=False,
    )
    try:
        await host.tick(tick_id="typed-policy", logical_time_from=NOW,
                        logical_time_to=NOW + timedelta(minutes=1),
                        observed_at=NOW + timedelta(minutes=1), reason="offline_typed_material")
        assert len(host.export_replay_evidence().projection.plans) == 1
        assert [lane for lane, _ in reviewer.calls] == ["general", "focused"]
        packet = json.loads(reviewer.calls[0][1][1]["content"])
        evidence = packet["pinned_source_evidence"]
        assert evidence["cited_committed_events"] == []
        assert evidence["cited_pinned_materials"] == [{
            "source_ref": author.ref, "authority_kind": "reviewed_location_catalog_policy",
            "materials": [item for item in author.manifest["location_capabilities"]
                          if author.ref in item["authority_refs"]],
        }]
        assert author.manifest["pinned_source_materials_version"] == "3"
        assert author.manifest["semantic_source_review_version"] == "1"
    finally:
        await host.aclose()
