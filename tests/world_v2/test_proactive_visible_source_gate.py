"""Required complete visible review at the public QQ proactive boundary.

Only the external author, reviewer and QQ delivery are offline fixtures. The
real host creates the Observation, delayed opportunity, Proposal and audit.
An empty authored claim list cannot excuse an unreviewed visible statement.
"""

from datetime import timedelta
import json
from pathlib import Path
import socket
import sqlite3

import httpx
import pytest

import companion_daemon.config as config_module
from companion_daemon.config import Settings
from companion_daemon.llm import DeepSeekChatModel, FakeCompanionModel
from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
from test_delayed_trigger_proactive_host_qualification import (
    NOW,
    _DeliveredQQ,
    _ProactiveRoleScript,
)
from test_whole_candidate_author import BEATS, _decision
from test_world_stimulus_life_intent import _http_result


SAFE_TEXTS = ("我想和你说句话。", "这会儿有点想念你。")

UNSOURCED_TEXT = "我刚刚在冥王星签收了编号 PX-UNSOURCED-772 的包裹。"


async def _run_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, *, pause_before_acceptance=False
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    monkeypatch.setattr(config_module, "_macos_launchctl_env", lambda _name: None)

    def no_network(*_args, **_kwargs):
        raise AssertionError("network access prohibited in offline source-gate test")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    if scenario == "prepare_error":
        import companion_daemon.world_v2.character_interior.proactive_visible_review as gate

        def invalid_preparation(**_kwargs):
            raise ValueError("offline host preparation failure")

        monkeypatch.setattr(
            gate, "prepare_proactive_visible_source_author_request", invalid_preparation
        )
    legacy_whole = scenario == "legacy_whole"
    if legacy_whole:
        import companion_daemon.world_v2.semantic_chat_composition as composition

        compose = composition.compose_production_character_interior

        def whole_without_required(**kwargs):
            assert kwargs["whole_candidate_mode"] is False
            assert kwargs["visible_source_review_model"] is None
            return compose(**{**kwargs, "whole_candidate_mode": True})

        monkeypatch.setattr(
            composition, "compose_production_character_interior", whole_without_required
        )
    proactive_requests = []
    reviewer_requests = []
    proactive_limits = []

    def capture_limits():
        from companion_daemon.world_v2.deliberation import (
            _ATTEMPT_DEADLINE,
            _INTERACTIVE_TURN_BUDGET,
        )
        from companion_daemon.llm import _MODEL_CALL_META

        meta = _MODEL_CALL_META.get()
        proactive_limits.append(
            (
                _ATTEMPT_DEADLINE.get(),
                id(_INTERACTIVE_TURN_BUDGET.get()),
                meta.get("world_id"),
                meta.get("turn_id"),
            )
        )

    async def author_http(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body["tool_choice"]["function"]["name"]
        if name == "character_inbound_initial_v1":
            authored = _decision()
            fields = body["tools"][0]["function"]["parameters"]["properties"]
            return _http_result(body, {key: authored.get(key) for key in fields})

        assert name == "character_role_proactive_contact_v1", name
        assert "source_table_json" not in json.dumps(body)
        assert "visible_source_requirement_json" not in json.dumps(body)
        proactive_requests.append(body)
        capture_limits()
        assert len(proactive_requests) <= 2, "more than one same-role reselection"
        if scenario == "author_timeout" and len(proactive_requests) == 2:
            raise TimeoutError("offline corrected author timeout")
        if len(proactive_requests) == 1:
            # The fixture's novel counterexample was never source context.
            # A correction may subsequently quote the rejected candidate.
            assert UNSOURCED_TEXT not in json.dumps(body, ensure_ascii=False)
        texts = (
            SAFE_TEXTS
            if scenario in {"source_free", "legacy_whole"}
            or (scenario in {"reselect", "bad_claim_reselect"} and len(proactive_requests) == 2)
            else (UNSOURCED_TEXT,)
        )
        payload = {
            "timing_choice": "now",
            "cadence": "conversational",
            "beats": [{"modality": "text", "text": text} for text in texts],
            "stance": "主动分享",
            "brief_rationale": "角色 fixture 选择主动联系。",
            "impulse_summary": "角色 fixture 自主选择表达。",
            "confidence": 7000,
            "world_claims": [],
        }
        if scenario in {"bad_claim_twice", "bad_claim_reselect"} and texts != SAFE_TEXTS:
            payload["world_claims"] = [
                {
                    "claim_text": "对方之前说去成都看熊猫",
                    "scope": "counterpart_history",
                    "source_refs": ["event:user:chengdu:not-in-context"],
                }
            ]
        # Both attempts are complete role decisions. The host must give the
        # same author one reselection, and must not synthesize character silence.
        return _http_result(
            body,
            {
                "status": "decision",
                "summary": "角色 fixture 选择现在发出这一条消息。",
                "attended_source_refs": [],
                "decision": {
                    "source_refs": [_ProactiveRoleScript._capability_source_ref(body["messages"])],
                    "payload": payload,
                },
                "recall_query": None,
                "proposals": [],
            },
        )

    async def review_http(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        reviewer_requests.append(body)
        packet = json.loads(body["messages"][-1]["content"])
        texts = tuple(beat["text"] for beat in packet["visible_beats"])
        assert texts in (BEATS, SAFE_TEXTS, (UNSOURCED_TEXT,)), texts
        if texts != BEATS:
            capture_limits()
            assert not any(
                a.kind == "proactive_message"
                for a in host.export_replay_evidence().projection.actions
            ), "candidate escaped before review"
        unclosed = texts == (UNSOURCED_TEXT,)
        # Exact scripted provider verdicts, not a production prose classifier.
        return _http_result(
            body,
            {
                "contract": "visible-beat-source-verdict.1",
                "decisions": [
                    {
                        "beat_index": index,
                        "verdict": "unclosed" if unclosed else "source_free",
                        "semantic_role": "external_proposition" if unclosed else "commitment",
                        "subject_role": "companion",
                        "source_ref_indexes": [],
                    }
                    for index in range(
                        0
                        if (
                            scenario == "invalid"
                            or (scenario == "review_invalid_second" and len(reviewer_requests) == 3)
                        )
                        and texts != BEATS
                        else len(texts)
                    )
                ],
            },
        )

    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore

    usage = WorldV2UsageStore(path=str(tmp_path / "world.sqlite"))
    author = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(author_http),
        usage_observer=usage.record,
    )
    reviewer = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(review_http),
        usage_observer=usage.record,
    )
    delivery = _DeliveredQQ()
    host = build_qq_c2c_host(
        settings=Settings(
            _env_file=None,
            database_path=tmp_path / "world.sqlite",
            PRIMARY_USER_ID="geoff",
            WORLD_V2_EXPRESSION_EPISODE_MODE="off",
            WORLD_V2_TEXT_ENDPOINT_ENABLED=False,
        ),
        recipient_id="10001",
        bootstrap_at=NOW,
        model=author,
        world_support_model=FakeCompanionModel(),
        visible_source_review_required=not legacy_whole,
        visible_source_review_model=None if legacy_whole else reviewer,
        delivery=delivery,
        use_configured_recall_embedding=False,
    )
    try:
        inbound = await host.inbound_text(
            message_id="message:proactive-visible-source-gate",
            recipient_id="10001",
            text="我先去忙一会儿。",
            observed_at=NOW,
        )
        initial_review_count = len(reviewer_requests)
        assert initial_review_count == (0 if legacy_whole else 1)
        assert inbound.status == "action_authorized", inbound

        due = NOW + timedelta(hours=12, seconds=1)
        await host.tick(
            tick_id="tick:proactive-visible-source-gate:due",
            logical_time_from=NOW,
            logical_time_to=due,
            observed_at=due,
            reason="offline_source_gate_regression",
            run_life_ecology=False,
        )
        if pause_before_acceptance:
            from companion_daemon.world_v2.expression_plan_atomic_recorder import (
                ExpressionPlanAtomicRecorder,
            )

            class CrashAfterAudit(BaseException):
                pass

            def crash(*_args, **_kwargs):
                raise CrashAfterAudit()

            with monkeypatch.context() as patch:
                patch.setattr(ExpressionPlanAtomicRecorder, "prepare_batch", crash)
                with pytest.raises(CrashAfterAudit):
                    await host.drain(max_action_units=8, max_background_units=2)
        else:
            await host.drain(max_action_units=8, max_background_units=2)
        evidence = host.export_replay_evidence()
    finally:
        await host.aclose()
        await author.aclose()
        await reviewer.aclose()

    proactive_actions = tuple(
        action for action in evidence.projection.actions if action.kind == "proactive_message"
    )
    proactive_review_count = len(reviewer_requests) - initial_review_count
    assert proactive_requests, "public tick/drain must reach the real proactive author"
    if legacy_whole:
        assert len(proactive_requests) == 1 and proactive_review_count == 0
        assert len(proactive_actions) == len(SAFE_TEXTS)
        assert tuple(text for _recipient, text in delivery.sent)[-2:] == SAFE_TEXTS
        assert all(action.state == "provider_accepted" for action in proactive_actions)
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
        return evidence, proactive_requests, reviewer_requests, delivery
    expected_count = (
        2
        if scenario
        in {
            "reject_twice",
            "reselect",
            "bad_claim_twice",
            "bad_claim_reselect",
            "author_timeout",
            "review_invalid_second",
        }
        else 1
    )
    expected_reviews = {
        "bad_claim_twice": 0,
        "bad_claim_reselect": 1,
        "author_timeout": 1,
        "prepare_error": 0,
    }.get(scenario, expected_count)
    assert len(proactive_requests) == expected_count
    assert proactive_review_count == expected_reviews
    assert len(proactive_limits) == expected_count + expected_reviews
    assert len(set(proactive_limits)) == 1, "correction or review replaced its original limits"
    assert proactive_limits[0][0] is not None, (
        "proactive provider calls require the original attempt deadline"
    )
    with sqlite3.connect(tmp_path / "world.sqlite") as connection:
        usage_rows = connection.execute(
            "SELECT purpose, prompt_tokens, completion_tokens, billing_state, reservation_id FROM world_v2_model_usage"
        ).fetchall()
        reservations = connection.execute(
            "SELECT reservation_id,status FROM world_v2_model_reservations"
        ).fetchall()
    assert len(usage_rows) == 2 + expected_count + expected_reviews
    known_rows = [row for row in usage_rows if row[3] == "known"]
    assert len(known_rows) == len(usage_rows) - (1 if scenario == "author_timeout" else 0)
    assert all(row[1:4] == (100, 100, "known") for row in known_rows)
    if scenario == "author_timeout":
        assert [row[3] for row in usage_rows if row[3] != "known"] == ["unknown"]
    assert len({row[4] for row in usage_rows}) == len(usage_rows)
    assert len(reservations) == len(usage_rows)
    billing = {row[4]: row[3] for row in usage_rows}
    assert all(
        state == ("billing_unknown" if billing[ref] == "unknown" else "settled")
        for ref, state in reservations
    )
    paid = [
        RecordedModelResultAudit.model_validate_json(row.audit_json)
        for row in evidence.projection.model_result_audits
    ]
    assert len([row for row in paid if row.usage is not None]) == len(known_rows)
    assert len({row.model_call_id for row in paid if row.usage is not None}) == len(known_rows)
    assert UNSOURCED_TEXT not in {
        payload.text for payload in evidence.projection.stored_message_payloads
    }
    assert all(text != UNSOURCED_TEXT for _recipient, text in delivery.sent)
    if pause_before_acceptance:
        assert not proactive_actions
        assert any(
            a.proposal_id.startswith("proposal:proactive:")
            for a in evidence.projection.proposal_audits
        )
        return evidence, proactive_requests, reviewer_requests, delivery
    if scenario in {"source_free", "reselect", "bad_claim_reselect"}:
        assert len(proactive_actions) == len(SAFE_TEXTS)
        assert tuple(text for _recipient, text in delivery.sent)[-2:] == SAFE_TEXTS
        assert all(action.state == "provider_accepted" for action in proactive_actions)
        from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate

        refs = {a.intent_ref.split(":intent:")[0] for a in proactive_actions}
        (audit,) = (a for a in evidence.projection.proposal_audits if a.proposal_id in refs)
        assert verify_recorded_candidate(
            audit=audit, model_result_audits=evidence.projection.model_result_audits
        )
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
        return evidence, proactive_requests, reviewer_requests, delivery
    assert proactive_actions == (), {
        "inbound_review_count": initial_review_count,
        "proactive_author_count": len(proactive_requests),
        "proactive_review_count": proactive_review_count,
        "unauthorized_proactive_actions": [
            (action.kind, action.state, action.action_id) for action in proactive_actions
        ],
    }

    # Existing durable public contracts distinguish technical failure from a
    # Character Decision to stay silent, and bind the terminal to its audit.
    (process,) = (
        item
        for item in evidence.projection.trigger_processes
        if item.process_kind == "proactive_action_deliberation" and item.state == "terminal"
    )
    assert str(process.runtime_outcome_ref).startswith("proactive:deliberation-failed:")
    failed_result_ref = str(process.runtime_outcome_ref).removeprefix(
        "proactive:deliberation-failed:"
    )
    (failed_audit,) = (
        RecordedModelResultAudit.model_validate_json(item.audit_json)
        for item in evidence.projection.model_result_audits
        if item.model_result_ref == failed_result_ref
    )
    assert failed_audit.failure_code is not None
    if scenario in {"invalid", "review_invalid_second", "prepare_error"}:
        assert failed_audit.failure_code == "source_review_exception"
    if scenario == "author_timeout":
        assert failed_audit.failure_code == "authored_subcall_timeout"
        assert failed_audit.usage is None
    assert failed_audit.outcome != "winner"
    assert evidence.projection.semantic_hash == evidence.replay.semantic_hash

    return evidence, proactive_requests, reviewer_requests, delivery


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    (
        "reject_twice",
        "source_free",
        "reselect",
        "invalid",
        "bad_claim_twice",
        "bad_claim_reselect",
        "legacy_whole",
        "author_timeout",
        "review_invalid_second",
        "prepare_error",
    ),
)
async def test_required_proactive_review_public_host(tmp_path, monkeypatch, scenario):
    await _run_scenario(tmp_path, monkeypatch, scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_before_acceptance", (False, True))
async def test_reviewed_proactive_cold_replay_has_no_new_calls_or_duplicate_actions(
    tmp_path, monkeypatch, pause_before_acceptance
):
    before, authors, reviews, delivery = await _run_scenario(
        tmp_path, monkeypatch, "source_free", pause_before_acceptance=pause_before_acceptance
    )

    async def forbidden(request):
        raise AssertionError("cold replay made a new provider call")

    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(forbidden),
    )
    host = build_qq_c2c_host(
        settings=Settings(
            _env_file=None,
            database_path=tmp_path / "world.sqlite",
            PRIMARY_USER_ID="geoff",
            WORLD_V2_EXPRESSION_EPISODE_MODE="off",
            WORLD_V2_TEXT_ENDPOINT_ENABLED=False,
        ),
        recipient_id="10001",
        bootstrap_at=NOW,
        model=model,
        world_support_model=FakeCompanionModel(),
        visible_source_review_required=True,
        visible_source_review_model=model,
        delivery=delivery,
        use_configured_recall_embedding=False,
    )
    try:
        assert (
            host.export_replay_evidence().projection.semantic_hash
            == before.projection.semantic_hash
        )
        sent = tuple(delivery.sent)
        # Resume only the installed production proactive owner. The general
        # background scheduler may legitimately begin a separate fact task.
        interior = host._semantic_chat.character_interior
        if pause_before_acceptance:
            await interior._drain_proactive_once()
        await host.drain(max_action_units=8, max_background_units=0)
        after = host.export_replay_evidence()
        if pause_before_acceptance:
            assert tuple(delivery.sent) == (*sent, *(("10001", text) for text in SAFE_TEXTS))
            assert len(after.projection.actions) == len(before.projection.actions) + 2
        else:
            assert tuple(delivery.sent) == sent
            assert tuple(a.action_id for a in after.projection.actions) == tuple(
                a.action_id for a in before.projection.actions
            )
        if pause_before_acceptance:
            # This QQ fixture returns provider acceptance only. The following
            # due pump must truthfully close the missing terminal receipt as
            # unknown before a stable projection can be asserted.
            assert all(
                a.state == "provider_accepted"
                for a in after.projection.actions
                if a.kind == "proactive_message"
            )
            await interior._drain_proactive_once()
            await host.drain(max_action_units=8, max_background_units=0)
            settled = host.export_replay_evidence()
            delta = settled.events[len(after.events) :]
            assert sum(item.event.event_type == "ActionUnknown" for item in delta) == 2
            assert not any(
                item.event.event_type in {"ActionAuthorized", "ModelResultRecorded"}
                for item in delta
            )
            assert all(
                a.state == "unknown"
                for a in settled.projection.actions
                if a.kind == "proactive_message"
            )
            assert tuple(delivery.sent) == (*sent, *(("10001", text) for text in SAFE_TEXTS))
            assert settled.projection.semantic_hash == settled.replay.semantic_hash
            after = settled
        await interior._drain_proactive_once()
        await host.drain(max_action_units=8, max_background_units=0)
        repeated = host.export_replay_evidence()
        assert repeated.projection.semantic_hash == after.projection.semantic_hash, [
            (item.event.event_type, item.event.payload())
            for item in repeated.events[len(after.events) :]
        ]
        assert after.projection.semantic_hash == after.replay.semantic_hash
        assert len(authors) == 1 and len(reviews) == 2
    finally:
        await host.aclose()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper", ("source_table", "actual_alias", "capability", "purpose", "receipt")
)
async def test_proactive_receipt_cannot_replace_original_capability_or_author_body(
    tmp_path, monkeypatch, tamper
):
    evidence, _, _, _ = await _run_scenario(tmp_path, monkeypatch, "source_free")
    from companion_daemon.world_v2.visible_source_runtime import (
        canonical,
        verify_recorded_candidate,
    )

    (row,) = (
        row
        for row in evidence.projection.model_result_audits
        if (
            lineage := RecordedModelResultAudit.model_validate_json(
                row.audit_json
            ).character_interior_lineage
        )
        is not None
        and lineage.purpose == "proactive_contact"
    )
    parent = json.loads(row.audit_json)
    carrier = json.loads(parent["visible_source_review_json"])
    if tamper == "source_table":
        requirement = json.loads(carrier["requirement_json"])
        table = json.loads(requirement["source_table_json"])
        table["pin"]["world_id"] = "world:forged"
        requirement["source_table_json"] = canonical(table)
        carrier["requirement_json"] = canonical(requirement)
    elif tamper == "actual_alias":
        body = json.loads(carrier["author_request_json"])
        user = json.loads(body["messages"][1]["content"])
        user["citeable_sources"]["items"][0]["ref"] = "event:forged"
        body["messages"][1]["content"] = canonical(user)
        carrier["author_request_json"] = canonical(body)
    elif tamper == "capability":
        parent["character_interior_lineage"]["capability_ref"] = (
            "proactive-reviewed-turn-capability:sha256:" + "f" * 64
        )
    elif tamper == "receipt":
        carrier["receipt"]["review"]["response_hash"] = "f" * 64
    else:
        parent["character_interior_lineage"]["purpose"] = "expression_reconsideration"
    parent["visible_source_review_json"] = canonical(carrier)
    forged = row.model_copy(update={"audit_json": canonical(parent)})
    audits = tuple(
        forged if item is row else item for item in evidence.projection.model_result_audits
    )
    (proposal,) = (
        a for a in evidence.projection.proposal_audits if a.model_result_ref == row.model_result_ref
    )
    with pytest.raises(ValueError):
        verify_recorded_candidate(audit=proposal, model_result_audits=audits)


@pytest.mark.asyncio
async def test_original_proactive_capability_rejects_old_model_audit_during_replay(
    tmp_path, monkeypatch
):
    evidence, _, _, _ = await _run_scenario(tmp_path, monkeypatch, "source_free")
    from companion_daemon.world_v2.visible_source_runtime import canonical, digest
    from companion_daemon.world_v2.reducers import ReducerState, reduce_event

    state = ReducerState()
    for item in evidence.events:
        event = item.event
        payload = event.payload()
        if (
            event.event_type == "ModelResultRecorded"
            and payload.get("audit_contract") == "model-result-audit.9"
        ):
            original = RecordedModelResultAudit.model_validate_json(payload["audit_json"])
            if original.character_interior_lineage.purpose == "proactive_contact":
                body = original.model_dump(mode="json")
                body.pop("visible_source_review_json")
                payload.update(
                    audit_contract="model-result-audit.7",
                    audit_json=canonical(body),
                    audit_hash=digest(canonical(body)),
                )
                raw = canonical(payload)
                changed = event.model_copy(
                    update={"payload_json": raw, "payload_hash": digest(raw)}
                )
                with pytest.raises(
                    ValueError, match="required visible review capability cannot downgrade"
                ):
                    reduce_event(state, changed)
                return
        state = reduce_event(state, event)
    pytest.fail("public proactive chain did not record the required original capability")


@pytest.mark.asyncio
async def test_metered_facade_captures_each_task_and_preserves_cancellation():
    import asyncio
    from companion_daemon.world_v2.character_interior.proactive_visible_review import (
        _INVOCATION,
        _Invocation,
        _MeteredProactiveModel,
    )
    from test_proactive_action_production import _usage

    class Provider:
        def __init__(self):
            self.cancelled = False
            self.waiting = asyncio.Event()

        async def complete_json_with_usage(self, messages, **kwargs):
            text = messages[0]["content"]
            if text == "cancel":
                self.waiting.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            await asyncio.sleep(0)
            return text, _usage(provider="fixture", ordinal=len(text))

        async def complete_json(self, messages, **kwargs):
            return "ordinary:" + messages[0]["content"]

    provider = Provider()
    facade = _MeteredProactiveModel(provider)

    async def invoke(text):
        capture = _Invocation(owner_task=asyncio.current_task())
        token = _INVOCATION.set(capture)
        try:
            raw = await facade.complete_json([{"role": "user", "content": text}], temperature=0.5)
            return raw, capture
        finally:
            _INVOCATION.reset(token)

    first, second = await asyncio.gather(invoke("first"), invoke("second"))
    assert first[0] == first[1].raw == "first"
    assert second[0] == second[1].raw == "second"
    assert first[1] is not second[1]
    task = asyncio.create_task(invoke("cancel"))
    await provider.waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.cancelled
    assert _INVOCATION.get() is None
    assert (
        await facade.complete_json([{"role": "user", "content": "untouched"}])
        == "ordinary:untouched"
    )
