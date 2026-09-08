"""Installed whole-candidate source review before public Action authorization."""

import asyncio
import json
from dataclasses import replace
from contextlib import asynccontextmanager
import sqlite3

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)
from test_whole_candidate_author import BEATS, _decision, _inbound
from test_world_stimulus_life_intent import _http_result
from test_production_turn_application import _config, _Identities, _Router, _DeliveredTransport, NOW


@pytest.mark.asyncio
async def test_complete_candidate_with_empty_claims_is_reviewed_before_any_action(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    requests = []
    review_started = asyncio.Event()
    release = asyncio.Event()

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert not body.get("stream")
        name = body["tool_choice"]["function"]["name"]
        if name == "character_inbound_initial_v1":
            value = _decision()
            return _http_result(
                body,
                {
                    key: value.get(key)
                    for key in body["tools"][0]["function"]["parameters"]["properties"]
                },
            )
        review_started.set()
        await release.wait()
        return _http_result(
            body,
            {
                "contract": "visible-beat-source-verdict.1",
                "decisions": [
                    {
                        "beat_index": i,
                        "verdict": "source_free",
                        "semantic_role": "commitment",
                        "subject_role": "companion",
                        "source_ref_indexes": [],
                    }
                    for i in range(len(BEATS))
                ],
            },
        )

    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(respond),
    )
    author = _InboundCharacterAuthor(
        flash_model=model, whole_candidate_mode=True, visible_source_review_model=model
    )
    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "world.sqlite",
        config=replace(_config(), visible_source_review_required=True),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(inbound_author=author),
        transport=_DeliveredTransport(),
        now=NOW,
    )
    task = asyncio.create_task(app.respond(_inbound()))
    try:
        await asyncio.wait_for(review_started.wait(), 3)
        assert not app.export_replay_evidence().projection.actions
        release.set()
        outcome = await task
        assert outcome.status == "action_authorized", outcome
        evidence = app.export_replay_evidence()
        assert len(requests) == 2
        assert tuple(item.text for item in evidence.projection.stored_message_payloads) == BEATS
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        app.close()
        await model.aclose()


class _ReviewHTTP:
    def __init__(self, verdicts):
        self.verdicts = iter(verdicts)
        self.requests = []
        self.authors = 0
        self.reviews = 0

    async def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        assert not body.get("stream")
        name = body["tool_choice"]["function"]["name"]
        if name == "character_inbound_initial_v1":
            self.authors += 1
            assert "visible_source_requirement_json" not in json.dumps(body)
            assert "source_table_json" not in json.dumps(body)
            authored = _decision()
            if self.authors > 1:
                authored["expression_draft"]["beats"][0]["text"] = "我想重新把自己的想法说完整。"
            return _http_result(
                body,
                {
                    key: authored.get(key)
                    for key in body["tools"][0]["function"]["parameters"]["properties"]
                },
            )
        self.reviews += 1
        verdict = next(self.verdicts)
        decisions = [
            {
                "beat_index": i,
                "verdict": "source_free",
                "semantic_role": "commitment",
                "subject_role": "companion",
                "source_ref_indexes": [],
            }
            for i in range(len(BEATS))
        ]
        if verdict == "unclosed":
            decisions[0].update(verdict="unclosed", semantic_role="external_proposition")
        if verdict == "invalid":
            decisions.pop()
        return _http_result(
            body, {"contract": "visible-beat-source-verdict.1", "decisions": decisions}
        )


@asynccontextmanager
async def _app(path, handler):
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore

    usage = WorldV2UsageStore(path=str(path.with_name("usage.sqlite")))
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(handler),
        usage_observer=usage.record,
    )
    author = _InboundCharacterAuthor(
        flash_model=model, whole_candidate_mode=True, visible_source_review_model=model
    )
    app = build_sqlite_world_v2_test_application(
        path=path,
        config=replace(_config(), visible_source_review_required=True),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(inbound_author=author),
        transport=_DeliveredTransport(),
        now=NOW,
    )
    try:
        yield app
    finally:
        app.close()
        await model.aclose()


def _audits(app):
    return [
        RecordedModelResultAudit.model_validate_json(row.audit_json)
        for row in app.export_replay_evidence().projection.model_result_audits
    ]


@pytest.mark.asyncio
async def test_unclosed_allows_one_same_role_reselection_and_reviews_whole_replacement(tmp_path):
    http = _ReviewHTTP(["unclosed", "pass"])
    async with _app(tmp_path / "world.sqlite", http) as app:
        outcome = await app.respond(_inbound())
        assert outcome.status == "action_authorized", outcome
        assert (http.authors, http.reviews) == (2, 2)
        texts = tuple(
            item.text for item in app.export_replay_evidence().projection.stored_message_payloads
        )
        assert texts == ("我想重新把自己的想法说完整。", BEATS[1])
        assert BEATS[0] not in texts
        audits = _audits(app)
        reviews = [row for row in audits if row.route.reason_code == "validation.source_review"]
        assert len(reviews) == 2
        assert len({row.model_call_id for row in reviews}) == 2
        assert all(row.usage is not None for row in reviews)
        # Each actual provider invocation contributes its own usage once.
        assert len([row for row in audits if row.usage is not None]) == 4
        assert len({row.model_call_id for row in audits if row.usage is not None}) == 4
        with sqlite3.connect(tmp_path / "usage.sqlite") as connection:
            rows = connection.execute(
                "SELECT purpose, prompt_tokens, completion_tokens, billing_state, reservation_id FROM world_v2_model_usage"
            ).fetchall()
            reservations = connection.execute(
                "SELECT reservation_id,status FROM world_v2_model_reservations"
            ).fetchall()
        assert len(rows) == 4
        assert sum(row[0] == "source_review" for row in rows) == 2
        assert all(row[1:4] == (100, 100, "known") for row in rows)
        assert len({row[4] for row in rows}) == 4
        assert len(reservations) == 4 and all(row[1] == "settled" for row in reservations)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdicts,counts", [(["unclosed", "unclosed"], (2, 2)), (["invalid"], (1, 1))]
)
async def test_semantic_exhaustion_and_technical_review_failure_authorize_no_action(
    tmp_path, verdicts, counts
):
    http = _ReviewHTTP(verdicts)
    async with _app(tmp_path / "world.sqlite", http) as app:
        outcome = await app.respond(_inbound())
        assert outcome.status != "action_authorized"
        assert (http.authors, http.reviews) == counts
        assert not app.export_replay_evidence().projection.actions
        audits = _audits(app)
        assert any(
            row.failure_code in {"paired_expression_reselection_invalid", "source_review_exception"}
            for row in audits
        )
        assert (
            len([row for row in audits if row.route.reason_code == "validation.source_review"])
            == counts[1]
        )


@pytest.mark.asyncio
async def test_cold_proposal_recovery_rechecks_receipt_before_effect_once_without_new_calls(
    tmp_path, monkeypatch
):
    from companion_daemon.world_v2.expression_plan_atomic_recorder import (
        ExpressionPlanAtomicRecorder,
    )
    from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate

    path = tmp_path / "world.sqlite"
    http = _ReviewHTTP(["pass"])

    class CrashAfterAudit(BaseException):
        pass

    def crash(*args, **kwargs):
        raise CrashAfterAudit()

    async with _app(path, http) as app:
        with monkeypatch.context() as patch:
            patch.setattr(ExpressionPlanAtomicRecorder, "prepare_batch", crash)
            with pytest.raises(CrashAfterAudit):
                await app.respond(_inbound())
        projection = app.export_replay_evidence().projection
        assert not projection.actions
        audit = next(row for row in projection.proposal_audits if row.proposal_kind == "decision")
        receipt_hash = verify_recorded_candidate(
            audit=audit, model_result_audits=projection.model_result_audits
        )
        assert (http.authors, http.reviews) == (1, 1)
    cold = _ReviewHTTP([])
    async with _app(path, cold) as app:
        outcome = await app.respond(_inbound())
        assert outcome.status == "action_authorized", outcome
        repeated = await app.respond(_inbound())
        assert repeated.authorized_action_ids == outcome.authorized_action_ids
        projection = app.export_replay_evidence().projection
        assert len(projection.actions) == 2
        assert cold.requests == []
        audit = next(row for row in projection.proposal_audits if row.proposal_kind == "decision")
        assert (
            verify_recorded_candidate(
                audit=audit, model_result_audits=projection.model_result_audits
            )
            == receipt_hash
        )
        events = app.export_replay_evidence().events
        manifest = [
            item.event.payload()
            for item in events
            if item.event.event_type == "AcceptanceRecorded"
            and item.event.payload().get("manifest_version") == "expression-plan-acceptance.2"
        ]
        assert len(manifest) == 1
        assert manifest[0]["visible_source_review_hash"] == receipt_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["table", "aliases", "missing", "old_contract", "subcall"])
async def test_cold_verifier_rejects_rehashed_receipt_against_original_host_and_provider_anchors(
    tmp_path, mutation
):
    from companion_daemon.world_v2.visible_source_runtime import (
        canonical,
        digest,
        verify_recorded_candidate,
    )
    from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
    from companion_daemon.world_v2.visible_source_review_receipt import (
        prepare_visible_source_review,
        record_visible_source_review,
        VisibleSourceReviewReceipt,
    )
    from companion_daemon.world_v2.proposal_envelope import DecisionProposal

    http = _ReviewHTTP(["pass"])
    async with _app(tmp_path / "world.sqlite", http) as app:
        assert (await app.respond(_inbound())).status == "action_authorized"
        projection = app.export_replay_evidence().projection
    audit = next(row for row in projection.proposal_audits if row.proposal_kind == "decision")
    original = next(
        row
        for row in projection.model_result_audits
        if row.model_result_ref == audit.model_result_ref
    )
    parent = RecordedModelResultAudit.model_validate_json(original.audit_json)
    value = json.loads(parent.visible_source_review_json)
    rows = list(projection.model_result_audits)
    receipt = VisibleSourceReviewReceipt.model_validate_json(canonical(value["receipt"]))
    prepared = json.loads(receipt.prepared_json)
    requirement = json.loads(value["requirement_json"])
    if mutation in {"table", "aliases"}:
        table = json.loads(requirement["source_table_json"])
        aliases = prepared["source_ref_aliases"]
        if mutation == "table":
            table["logical_time"] = "2099-01-01T00:00:00+00:00"
            requirement["source_table_json"] = canonical(table)
            value["requirement_json"] = canonical(requirement)
        else:
            aliases = {**aliases, "S999": "invented:source"}
        replacement = prepare_visible_source_review(
            candidate=DecisionProposal.model_validate_json(audit.proposal_json),
            source_table=VisibleSourceTable(payload_json=canonical(table)),
            source_ref_aliases=aliases,
        )
        # Even a self-consistent passing replacement receipt cannot replace the
        # original Core capability or the actual author's immutable request hash.
        review = receipt.review.model_copy(
            update={"request_hash": replacement.as_dict()["request_hash"]}
        )
        rewritten = record_visible_source_review(
            prepared=replacement,
            author=receipt.author,
            review=review,
            raw_verdict=receipt.raw_verdict,
        )
        value["receipt"] = rewritten.model_dump(mode="json")
        for index, row in enumerate(rows):
            recorded = RecordedModelResultAudit.model_validate_json(row.audit_json)
            if recorded.model_call_id == review.model_call_id:
                changed = recorded.model_copy(update={"request_hash": review.request_hash})
                rows[index] = row.model_copy(
                    update={
                        "audit_json": changed.model_dump_json(),
                        "request_hash": review.request_hash,
                    }
                )
    if mutation == "subcall":
        rows = [row for row in rows if row.model_call_id != receipt.review.model_call_id]
    new_parent = parent.model_copy(
        update={
            "visible_source_review_json": None
            if mutation in {"missing", "old_contract"}
            else canonical(value)
        }
    )
    for index, row in enumerate(rows):
        if row.model_result_ref == original.model_result_ref:
            body = new_parent.model_dump_json()
            rows[index] = row.model_copy(
                update={
                    "audit_json": body,
                    "audit_hash": digest(body),
                    **(
                        {"audit_contract": "model-result-audit.8"}
                        if mutation == "old_contract"
                        else {}
                    ),
                }
            )
    with pytest.raises(ValueError):
        verify_recorded_candidate(audit=audit, model_result_audits=tuple(rows))


@pytest.mark.asyncio
async def test_original_required_capability_rejects_model_audit_downgrade_during_replay(tmp_path):
    from companion_daemon.world_v2.visible_source_runtime import canonical, digest
    from companion_daemon.world_v2.reducers import ReducerState, reduce_event

    http = _ReviewHTTP(["pass"])
    async with _app(tmp_path / "world.sqlite", http) as app:
        assert (await app.respond(_inbound())).status == "action_authorized"
        events = app.export_replay_evidence().events
    state = ReducerState()
    for item in events:
        event = item.event
        payload = event.payload()
        if (
            event.event_type == "ModelResultRecorded"
            and payload.get("audit_contract") == "model-result-audit.9"
        ):
            original = RecordedModelResultAudit.model_validate_json(payload["audit_json"])
            assert original.character_interior_lineage.capability_ref.startswith(
                "inbound-reviewed-turn-capability:"
            )
            body = original.model_dump(mode="json")
            body.pop("visible_source_review_json")
            payload.update(
                audit_contract="model-result-audit.7",
                audit_json=canonical(body),
                audit_hash=digest(canonical(body)),
            )
            raw = canonical(payload)
            changed = event.model_copy(update={"payload_json": raw, "payload_hash": digest(raw)})
            with pytest.raises(
                ValueError, match="required visible review capability cannot downgrade"
            ):
                reduce_event(state, changed)
            return
        state = reduce_event(state, event)
    pytest.fail("public chain did not record the required audit")


def test_required_deployment_does_not_upgrade_legacy_pending_minimal_reply():
    from test_minimal_reply_acceptance import _audit, _account, _policy
    from companion_daemon.world_v2.minimal_reply_acceptance import (
        derive_minimal_reply_material,
        MinimalReplyAcceptanceError,
    )
    from companion_daemon.world_v2.schemas import ProjectionCursor

    audit = _audit()
    with pytest.raises(MinimalReplyAcceptanceError, match="required_whole_review_unavailable"):
        derive_minimal_reply_material(
            audit=audit,
            cursor=ProjectionCursor(
                world_revision=audit.evaluated_world_revision,
                deliberation_revision=0,
                ledger_sequence=0,
            ),
            world_id="world:test",
            policy=_policy().model_copy(update={"visible_source_review_required": True}),
            account=_account(),
            logical_time=NOW,
            created_at=NOW,
            trace_id="trace:required",
            correlation_id="correlation:required",
        )


@pytest.mark.asyncio
async def test_core_recall_keeps_original_requirement_and_reviews_only_complete_final(tmp_path):
    class RecallHTTP(_ReviewHTTP):
        async def __call__(self, request):
            body = json.loads(request.content)
            name = body["tool_choice"]["function"]["name"]
            if name == "character_inbound_initial_v1" and self.authors == 0:
                self.authors += 1
                self.requests.append(body)
                value = {
                    "result_kind": "recall",
                    "private_turn_state": {
                        "contract": "private-turn-state.1",
                        "inner_state_summary": "我想先确认之前的对话。",
                        "attended_source_refs": [],
                    },
                    "recall_request": {
                        "query_text": "之前的对话",
                        "memory_kinds": ["episodic", "semantic"],
                        "limit": 4,
                    },
                }
                return _http_result(
                    body,
                    {
                        key: value.get(key)
                        for key in body["tools"][0]["function"]["parameters"]["properties"]
                    },
                )
            if name == "character_inbound_after_recall_v1":
                self.authors += 1
                self.requests.append(body)
                value = _decision()
                return _http_result(
                    body,
                    {
                        key: value.get(key)
                        for key in body["tools"][0]["function"]["parameters"]["properties"]
                    },
                )
            return await super().__call__(request)

    http = RecallHTTP(["pass"])
    async with _app(tmp_path / "world.sqlite", http) as app:
        outcome = await app.respond(_inbound())
        assert outcome.status == "action_authorized", outcome
        assert (http.authors, http.reviews) == (2, 1)
        audits = _audits(app)
        winner = next(row for row in audits if row.visible_source_review_json is not None)
        assert winner.recall_trace is not None
        value = json.loads(winner.visible_source_review_json)
        table = json.loads(json.loads(value["requirement_json"])["source_table_json"])
        final = json.loads(http.requests[1]["messages"][-1]["content"])
        assert table["pin"]["capsule_id"] == final["request"]["capsule_id"]
        assert final["request"]["evaluated_world_revision"] == table["pin"]["world_revision"]
        # The additional Recall remains a separate role-owned retrieval record;
        # the original selected source table does not gain new source members.
        assert winner.recall_trace.mode == "character_pull"
        with sqlite3.connect(tmp_path / "usage.sqlite") as connection:
            rows = connection.execute(
                "SELECT prompt_tokens,completion_tokens,billing_state FROM world_v2_model_usage"
            ).fetchall()
        assert rows == [(100, 100, "known")] * 3
