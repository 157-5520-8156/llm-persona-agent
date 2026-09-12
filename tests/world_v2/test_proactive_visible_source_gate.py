"""Required complete visible review at the public QQ proactive boundary.

Only the external author, reviewer and QQ delivery are offline fixtures. The
real host creates the Observation, delayed opportunity, Proposal and audit.
An empty authored claim list cannot excuse an unreviewed visible statement.
"""

import asyncio
from datetime import timedelta
import hashlib
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
SOURCE_PROBLEM = "原材料没有这段已经发生经历的依据"
HISTORY_TEXT = "我之前说想先把自己的想法说完整。"
CLAIM_TEXTS = (HISTORY_TEXT, SAFE_TEXTS[0])


def _install_historical_claim_capability(monkeypatch):
    from companion_daemon.world_v2.proactive_action import _CharacterInteriorProactiveTransport

    original = _CharacterInteriorProactiveTransport._capability_from_parts

    def historical_capability(self, **kwargs):
        manifest = original(self, **kwargs)
        payload = dict(manifest.payload)
        payload.pop("world_claim_source_lanes")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return type(manifest).model_validate({
            **manifest.model_dump(), "payload_json": raw,
            "payload_hash": "sha256:" + hashlib.sha256(raw.encode()).hexdigest(),
        })

    monkeypatch.setattr(_CharacterInteriorProactiveTransport, "_capability_from_parts", historical_capability)


async def _run_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    *,
    pause_before_acceptance=False,
    pause_after_role_preparation=False,
    external_cancel=False,
    review_version="1",
    review_required=None,
    authored_outputs=None,
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
    timing_failure = scenario in {"timing_reselect", "timing_twice", "silence_reselect", "silence_twice"}
    source_claim_scenario = scenario in {
        "valid_claim", "scope_claim_reselect", "scope_claim_twice",
        "mixed_claim_reselect", "mixed_claim_twice",
    }
    expected_texts = CLAIM_TEXTS if source_claim_scenario else SAFE_TEXTS
    legacy_whole = scenario == "legacy_whole"
    if review_required is None:
        review_required = not legacy_whole
    if not review_required:
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
    provider_blocked = asyncio.Event()
    provider_cancelled = asyncio.Event()

    async def wait_for_cancellation():
        provider_blocked.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            provider_cancelled.set()
            raise

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
        if scenario == "author_deadline" and len(proactive_requests) == 2:
            # The production deadline or explicit host shutdown ends this call.
            await wait_for_cancellation()
        if len(proactive_requests) == 1:
            # The fixture's novel counterexample was never source context.
            # A correction may subsequently quote the rejected candidate.
            assert UNSOURCED_TEXT not in json.dumps(body, ensure_ascii=False)
        texts = (
            SAFE_TEXTS
            if scenario in {"source_free", "legacy_whole"} or timing_failure
            or (scenario in {"reselect", "bad_claim_reselect", "empty_claim_reselect"}
                and len(proactive_requests) == 2)
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
        if source_claim_scenario:
            packet = json.loads(body["messages"][-1]["content"])
            source = next(item for item in packet["citeable_sources"]["items"]
                          if item["ref"].startswith("dialogue:expression:")
                          and item["ref"].endswith(":1"))
            invalid = scenario != "valid_claim" and (
                len(proactive_requests) == 1 or scenario.endswith("twice")
            )
            texts = CLAIM_TEXTS
            payload["world_claims"] = [{
                "claim_text": HISTORY_TEXT,
                "scope": "current_world" if invalid and scenario.startswith("scope_")
                         else "shared_history",
                "source_refs": [source["ref"]],
            }]
            if invalid and scenario.startswith("mixed_"):
                texts = (HISTORY_TEXT, UNSOURCED_TEXT)
                payload["world_claims"].append({
                    "claim_text": UNSOURCED_TEXT, "scope": "past_world",
                    "source_refs": ["event:user:chengdu:not-in-context"],
                })
            payload["beats"] = [{"modality": "text", "text": text} for text in texts]
        if scenario in {"bad_claim_twice", "bad_claim_reselect"} and texts != SAFE_TEXTS:
            payload["world_claims"] = [
                {
                    "claim_text": "对方之前说去成都看熊猫",
                    "scope": "counterpart_history",
                    "source_refs": ["event:user:chengdu:not-in-context"],
                }
            ]
        if scenario in {"empty_claim_twice", "empty_claim_reselect"} and texts != SAFE_TEXTS:
            payload["world_claims"] = [{
                "claim_text": UNSOURCED_TEXT,
                "scope": "past_world",
                "source_refs": [],
            }]
        if timing_failure:
            payload["response_expectation"] = {
                "hoped_response": "想听你说一句", "pressure_bp": 2000,
                "importance_bp": 3000, "wait_seconds": 40_123,
                "expires_after_seconds": 99_876,
            }
            if len(proactive_requests) == 1 or scenario.endswith("_twice"):
                if scenario.startswith("silence_"):
                    payload["timing_choice"] = "silent"
                else:
                    payload.update(timing_choice="later", delay_seconds=259_200,
                                   expires_after_seconds=345_600)
        # Both attempts are complete role decisions. The host must give the
        # same author one reselection, and must not synthesize character silence.
        authored = {
            "status": "decision",
            "summary": "角色 fixture 选择现在发出这一条消息。",
            "attended_source_refs": [],
            "decision": {
                "source_refs": [_ProactiveRoleScript._capability_source_ref(body["messages"])],
                "payload": payload,
            },
            "recall_query": None,
            "proposals": [],
        }
        if authored_outputs is not None:
            authored_outputs.append(json.dumps(authored, ensure_ascii=False))
        return _http_result(body, authored)

    async def review_http(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        reviewer_requests.append(body)
        assert body["tool_choice"]["function"]["name"] == f"visible_beat_source_verdict_v{review_version}"
        packet = json.loads(body["messages"][-1]["content"])
        texts = tuple(beat["text"] for beat in packet["visible_beats"])
        assert texts in (BEATS, SAFE_TEXTS, (UNSOURCED_TEXT,), CLAIM_TEXTS,
                         (HISTORY_TEXT, UNSOURCED_TEXT)), texts
        if texts != BEATS:
            capture_limits()
            assert not any(
                a.kind == "proactive_message"
                for a in host.export_replay_evidence().projection.actions
            ), "candidate escaped before review"
            if scenario == "review_deadline" or (
                scenario == "review_deadline_second" and len(reviewer_requests) == 3
            ):
                await wait_for_cancellation()
        unclosed = texts == (UNSOURCED_TEXT,)
        # Exact scripted provider verdicts, not a production prose classifier.
        verdict = {
                "contract": f"visible-beat-source-verdict.{review_version}",
                **({"rejections": [
                    {
                        "beat_index": index, "char_start": 0, "char_end": len(text),
                        "related_source_ref_indexes": [], "source_problem": SOURCE_PROBLEM,
                    }
                    for index, text in enumerate(texts)
                    if unclosed
                ]} if review_version in {"2", "3", "4"} else {}),
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
            }
        if review_version in {"3", "4"}:
            for decision in verdict["decisions"]:
                assert decision.pop("source_ref_indexes") == []
        if source_claim_scenario and texts != BEATS:
            assert review_version == "4"
            refs = [dict(zip(table["columns"], row, strict=True))
                    for table in packet["source_reference_tables"] for row in table["rows"]]
            source = next(row for row in refs
                          if row["source_ref"].startswith("dialogue:expression:")
                          and row["source_ref"].endswith(":1"))
            assert source["subject_role"] == "companion"
            verdict["decisions"][0] = {
                "beat_index": 0, "verdict": "closed", "semantic_role": "external_proposition",
                "subject_role": "companion", "first_source_ref_index": source["source_ref_index"],
                "additional_source_ref_indexes": [],
            }
            if texts[1] == UNSOURCED_TEXT:
                verdict["decisions"][1] = {
                    "beat_index": 1, "verdict": "unclosed", "semantic_role": "external_proposition",
                    "subject_role": "companion",
                }
                verdict["rejections"] = [{
                    "beat_index": 1, "char_start": 0, "char_end": len(UNSOURCED_TEXT),
                    "related_source_ref_indexes": [], "source_problem": SOURCE_PROBLEM,
                }]
        return _http_result(body, verdict)

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
        visible_source_review_required=review_required,
        visible_source_review_model=reviewer if review_required else None,
        visible_source_review_version=review_version,
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
        assert initial_review_count == int(review_required)
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
        if external_cancel:
            task = asyncio.create_task(host.drain(max_action_units=8, max_background_units=2))
            await asyncio.wait_for(provider_blocked.wait(), timeout=10)
            task.cancel("external-stop")
            with pytest.raises(asyncio.CancelledError, match="external-stop"):
                await task
            # The host owns and shields its scheduler task. Cancelling this
            # waiter leaves that task running until explicit aclose below.
            assert not provider_cancelled.is_set()
        elif pause_after_role_preparation:
            from companion_daemon.world_v2.character_interior.core import CharacterInterior

            class CrashAfterRolePreparation(BaseException):
                pass

            checkpoint = CharacterInterior._checkpoint_turn

            def crash_after_checkpoint(self, **kwargs):
                recorded = checkpoint(self, **kwargs)
                if kwargs["durable"][0].purpose == "proactive_contact":
                    raise CrashAfterRolePreparation()
                return recorded

            with monkeypatch.context() as patch:
                patch.setattr(CharacterInterior, "_checkpoint_turn", crash_after_checkpoint)
                with pytest.raises(CrashAfterRolePreparation):
                    await host.drain(max_action_units=8, max_background_units=2)
        elif pause_before_acceptance:
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
    if pause_after_role_preparation:
        assert not proactive_actions
        assert not any(row.proposal_id.startswith("proposal:proactive:")
                       for row in evidence.projection.proposal_audits)
        return evidence, proactive_requests, reviewer_requests, delivery
    if external_cancel:
        assert provider_cancelled.is_set(), "host shutdown must cancel its provider task"
        assert not proactive_actions
        assert not any(
            item.process_kind == "proactive_action_deliberation" and item.state == "terminal"
            for item in evidence.projection.trigger_processes
        )
        with sqlite3.connect(tmp_path / "world.sqlite") as connection:
            rows = connection.execute(
                "SELECT state, terminal_result_json FROM world_v2_character_interior_turns "
                "WHERE purpose = 'proactive_contact'"
            ).fetchall()
            billing = connection.execute(
                "SELECT billing_state FROM world_v2_model_usage WHERE billing_state != 'known'"
            ).fetchall()
            (event_count,) = connection.execute(
                "SELECT COUNT(*) FROM world_v2_events"
            ).fetchone()
        assert rows and all(state != "terminal" and result is None for state, result in rows)
        assert billing == [("unknown",)]
        # Closing really cancelled the provider, and appended no World Event;
        # the pre-close Action/TriggerProcess/ModelResult snapshot is unchanged.
        assert event_count == len(evidence.events)
        # External cancellation must not become a model outcome or character silence.
        assert len(evidence.projection.model_result_audits) == 2
        assert all(text != UNSOURCED_TEXT for _recipient, text in delivery.sent)
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
        return evidence, proactive_requests, reviewer_requests, delivery
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
            "timing_reselect", "timing_twice", "silence_reselect", "silence_twice",
            "reselect",
            "bad_claim_twice",
            "bad_claim_reselect",
            "empty_claim_twice", "empty_claim_reselect",
            "scope_claim_reselect", "scope_claim_twice", "mixed_claim_reselect", "mixed_claim_twice",
            "author_timeout",
            "author_deadline",
            "review_deadline_second",
            "review_invalid_second",
        }
        else 1
    )
    expected_reviews = {
        "timing_reselect": 1, "timing_twice": 0,
        "silence_reselect": 1, "silence_twice": 0,
        "bad_claim_twice": 0,
        "bad_claim_reselect": 1,
        "empty_claim_twice": 0,
        "empty_claim_reselect": int(review_required),
        "valid_claim": int(review_required),
        "scope_claim_reselect": int(review_required), "scope_claim_twice": 0,
        "mixed_claim_reselect": int(review_required), "mixed_claim_twice": 0,
        "author_timeout": 1,
        "author_deadline": 1,
        "prepare_error": 0,
    }.get(scenario, expected_count)
    assert len(proactive_requests) == expected_count, {
        "review_required": review_required,
        "proactive_actions": [(action.kind, action.state) for action in proactive_actions],
    }
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
    assert len(usage_rows) == 1 + initial_review_count + expected_count + expected_reviews
    known_rows = [row for row in usage_rows if row[3] == "known"]
    assert len(known_rows) == len(usage_rows) - (
        1
        if scenario in {
            "author_timeout", "author_deadline", "review_deadline", "review_deadline_second"
        }
        else 0
    )
    assert all(row[1:4] == (100, 100, "known") for row in known_rows)
    if scenario in {
        "author_timeout", "author_deadline", "review_deadline", "review_deadline_second"
    }:
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
    if review_required:
        assert len([row for row in paid if row.usage is not None]) == len(known_rows)
        assert len({row.model_call_id for row in paid if row.usage is not None}) == len(known_rows)
    # The legacy reviewer=None path does not yet project physical proactive
    # subcall audits. Its durable usage/reservations are checked above; this
    # wire regression must not claim that separate audit gap is closed.
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
    if scenario in {"source_free", "reselect", "bad_claim_reselect", "empty_claim_reselect", "timing_reselect", "silence_reselect", "valid_claim", "scope_claim_reselect", "mixed_claim_reselect"}:
        assert len(proactive_actions) == len(expected_texts)
        assert tuple(text for _recipient, text in delivery.sent)[-2:] == expected_texts
        assert all(action.state == "provider_accepted" for action in proactive_actions)
        from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate

        refs = {a.intent_ref.split(":intent:")[0] for a in proactive_actions}
        (audit,) = (a for a in evidence.projection.proposal_audits if a.proposal_id in refs)
        if review_required:
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
    if scenario in {"author_timeout", "author_deadline"}:
        assert failed_audit.failure_code == "authored_subcall_timeout"
        assert failed_audit.usage is None
    if scenario in {"review_deadline", "review_deadline_second"}:
        assert failed_audit.failure_code == "source_review_timeout"
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
@pytest.mark.parametrize("review_version", ("2", "3", "4"))
@pytest.mark.parametrize(
    "scenario",
    ("source_free", "reselect", "reject_twice", "review_invalid_second", "review_deadline_second"),
)
async def test_versioned_proactive_review_and_reselection_use_actual_protocol(
    tmp_path, monkeypatch, scenario, review_version
):
    evidence, authors, reviews, _delivery = await _run_scenario(
        tmp_path, monkeypatch, scenario, review_version=review_version
    )
    assert all(
        body["tool_choice"]["function"]["name"] == f"visible_beat_source_verdict_v{review_version}"
        for body in reviews
    )
    if scenario != "source_free":
        assert len(authors) == 2
        correction_body = json.dumps(authors[1]["messages"], ensure_ascii=False)
        assert SOURCE_PROBLEM in correction_body
        assert UNSOURCED_TEXT[:10] in correction_body
        corrected_user = json.loads(authors[1]["messages"][1]["content"])
        detail = corrected_user["correction"]["failure_detail"]
        feedback = json.loads(detail.split("\n", 1)[1])
        assert feedback["contract"] == "visible-source-rejection-feedback.2"
        (row,) = feedback["rows"]
        assert dict(zip(feedback["columns"], row, strict=True)) == {
            "beat_index": 0, "start": 0, "end": len(UNSOURCED_TEXT),
            "excerpt_prefix": row[3], "source_problem": SOURCE_PROBLEM,
            "related_source_ref_indexes": [],
        }
        assert UNSOURCED_TEXT.startswith(row[3]) and row[3] != UNSOURCED_TEXT
    if scenario in {"source_free", "reselect"}:
        (parent,) = (
            RecordedModelResultAudit.model_validate_json(row.audit_json)
            for row in evidence.projection.model_result_audits
            if (lineage := RecordedModelResultAudit.model_validate_json(
                row.audit_json
            ).character_interior_lineage) is not None
            and lineage.purpose == "proactive_contact"
        )
        receipt = json.loads(parent.visible_source_review_json)["receipt"]
        assert receipt["contract"] == f"visible-source-review-receipt.{review_version}"
        assert json.loads(receipt["prepared_json"])["contract"] == f"visible-source-review-request.{review_version}"
        verdict = json.loads(receipt["raw_verdict"])
        assert verdict["contract"] == f"visible-beat-source-verdict.{review_version}"
        assert verdict["rejections"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ("author_deadline", "review_deadline", "review_deadline_second"))
async def test_production_outer_deadline_preserves_completed_proactive_audits(
    tmp_path, monkeypatch, scenario
):
    await _run_scenario(tmp_path, monkeypatch, scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ("author_deadline", "review_deadline"))
async def test_external_cancellation_and_close_leave_proactive_turn_unfinished(
    tmp_path, monkeypatch, scenario
):
    await _run_scenario(tmp_path, monkeypatch, scenario, external_cancel=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("review_version", ("1", "2", "3", "4"))
@pytest.mark.parametrize(
    "legacy_claim_lanes,pause_before_acceptance",
    [(False, False), (False, True), (True, True)],
    ids=["committed-current", "audited-current", "audited-legacy"],
)
async def test_reviewed_proactive_cold_replay_has_no_new_calls_or_duplicate_actions(
    tmp_path, monkeypatch, pause_before_acceptance, review_version, legacy_claim_lanes,
):
    with monkeypatch.context() as first_run:
        if legacy_claim_lanes:
            _install_historical_claim_capability(first_run)
        before, authors, reviews, delivery = await _run_scenario(
            tmp_path, monkeypatch, "source_free", pause_before_acceptance=pause_before_acceptance,
            review_version=review_version,
        )
    authored_manifest = json.loads(authors[0]["messages"][-1]["content"])["capability_manifest"]
    assert ("world_claim_source_lanes" in authored_manifest["payload"]) is not legacy_claim_lanes

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
        visible_source_review_version=review_version,
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
@pytest.mark.parametrize("review_required", [False, True])
async def test_old_prepared_proactive_choice_cannot_bypass_new_claim_lanes(
    tmp_path, monkeypatch, review_required,
):
    historical_raw = []
    with monkeypatch.context() as first_run:
        _install_historical_claim_capability(first_run)
        before, _, _, delivery = await _run_scenario(
            tmp_path, monkeypatch, "source_free", review_version="4" if review_required else "1",
            review_required=review_required, pause_after_role_preparation=True,
            authored_outputs=historical_raw,
        )

    def stored_turns():
        with sqlite3.connect(tmp_path / "world.sqlite") as connection:
            return connection.execute(
                "SELECT inner_turn_id, request_hash, capability_hash, state, "
                "authored_state_json, authored_state_hash, terminal_result_json "
                "FROM world_v2_character_interior_turns WHERE purpose='proactive_contact'"
            ).fetchall()

    (old,) = stored_turns()
    assert old[3] == "checkpointed" and old[4] and old[5] and old[6] is None
    assert "world_claim_source_lanes" not in old[4]
    authors, reviews = [], []

    async def provider_http(request):
        body = json.loads(request.content)
        packet = json.loads(body["messages"][-1]["content"])
        name = body["tool_choice"]["function"]["name"]
        if name == "character_role_proactive_contact_v1":
            authors.append(packet)
            assert len(authors) <= 2
            assert packet["capability_manifest"]["payload"]["world_claim_source_lanes"]["contract"] == "proactive-world-claim-source-lanes.1"
            authored = json.loads(historical_raw[0])
            authored["decision"]["source_refs"] = [
                _ProactiveRoleScript._capability_source_ref(body["messages"])
            ]
            if len(authors) == 1:
                authored["decision"]["payload"].update(
                    beats=[{"modality": "text", "text": UNSOURCED_TEXT}],
                    world_claims=[{
                        "claim_text": UNSOURCED_TEXT, "scope": "past_world",
                        "source_refs": ["event:user:chengdu:not-in-context"],
                    }],
                )
            else:
                assert packet["correction"]["failure_code"] == "role_result_schema_invalid"
                assert "world_claims[0].source_refs" in packet["correction"]["failure_detail"]
            return _http_result(body, authored)
        assert review_required and name == "visible_beat_source_verdict_v4"
        reviews.append(packet)
        assert tuple(beat["text"] for beat in packet["visible_beats"]) == SAFE_TEXTS
        return _http_result(body, {
            "contract": "visible-beat-source-verdict.4", "rejections": [],
            "decisions": [{
                "beat_index": index, "verdict": "source_free",
                "semantic_role": "commitment", "subject_role": "companion",
            } for index in range(2)],
        })

    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore

    model = DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(provider_http),
        usage_observer=WorldV2UsageStore(path=str(tmp_path / "world.sqlite")).record,
    )
    host = build_qq_c2c_host(
        settings=Settings(
            _env_file=None, database_path=tmp_path / "world.sqlite", PRIMARY_USER_ID="geoff",
            WORLD_V2_EXPRESSION_EPISODE_MODE="off", WORLD_V2_TEXT_ENDPOINT_ENABLED=False,
        ),
        recipient_id="10001", bootstrap_at=NOW, model=model,
        world_support_model=FakeCompanionModel(), visible_source_review_required=review_required,
        visible_source_review_model=model if review_required else None,
        visible_source_review_version="4" if review_required else "1",
        delivery=delivery, use_configured_recall_embedding=False,
    )
    try:
        assert host.export_replay_evidence().projection.semantic_hash == before.projection.semantic_hash
        await host._semantic_chat.character_interior._drain_proactive_once()
        await host.drain(max_action_units=8, max_background_units=0)
        evidence = host.export_replay_evidence()
        assert len(authors) == 2 and len(reviews) == int(review_required)
        assert authors[0]["inner_turn"] == authors[1]["inner_turn"]
        assert authors[0]["inner_life_snapshot"] == authors[1]["inner_life_snapshot"]
        assert authors[0]["capability_manifest"] == authors[1]["capability_manifest"]
        assert old in stored_turns(), "new request rewrote a historical checkpoint"
        (new,) = (row for row in stored_turns() if row[0] != old[0])
        assert new[1] != old[1] and new[2] != old[2] and new[3] == "terminal"
        # The ordinary prepared snapshot stores the immutable capability
        # binding hash, while required review additionally carries author wire.
        binding = json.loads(json.loads(new[4])["snapshot"]["capability_scope"]["payload_json"])
        assert binding["payload_hash"] == authors[0]["capability_manifest"]["payload_hash"]
        if review_required:
            # Reviewed wire omits its host-only requirement; its complete
            # capability and author-body binding is checked by receipt replay.
            assert "world_claim_source_lanes" in new[4]
        else:
            manifest = _manifest_from_role_packet(authors[0])
            canonical = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False,
                                   sort_keys=True, separators=(",", ":"))
            assert new[2] == hashlib.sha256(canonical.encode()).hexdigest()
        actions = [a for a in evidence.projection.actions if a.kind == "proactive_message"]
        assert len(actions) == 2 and all(a.state == "provider_accepted" for a in actions)
        assert tuple(text for _, text in delivery.sent[-2:]) == SAFE_TEXTS
        assert all(text != UNSOURCED_TEXT for _, text in delivery.sent)
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
    finally:
        await host.aclose()
        await model.aclose()


def _manifest_from_role_packet(packet):
    from companion_daemon.world_v2.character_interior.contracts import _InteriorCapabilityManifest

    view = packet["capability_manifest"]
    return _InteriorCapabilityManifest(
        capability_ref=view["capability_ref"], capability_kind=view["capability_kind"],
        payload_hash=view["payload_hash"], source_refs=tuple(view["source_refs"]),
        payload_json=json.dumps(view["payload"], ensure_ascii=False,
                                sort_keys=True, separators=(",", ":")),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper", ("source_table", "actual_alias", "claim_lanes", "capability", "purpose", "receipt")
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
    elif tamper in {"actual_alias", "claim_lanes"}:
        body = json.loads(carrier["author_request_json"])
        user = json.loads(body["messages"][1]["content"])
        if tamper == "actual_alias":
            user["citeable_sources"]["items"][0]["ref"] = "event:forged"
        else:
            user["capability_manifest"]["payload"].pop("world_claim_source_lanes")
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
    inherited = _Invocation(owner_task=asyncio.current_task())
    token = _INVOCATION.set(inherited)
    try:
        assert await asyncio.create_task(
            facade.complete_json([{"role": "user", "content": "child"}])
        ) == "ordinary:child"
        assert inherited.parameters is inherited.raw is inherited.usage is None
    finally:
        _INVOCATION.reset(token)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["timing_reselect", "timing_twice", "silence_reselect", "silence_twice"])
async def test_proactive_timing_conflicts_use_one_role_reselection(tmp_path, monkeypatch, scenario):
    evidence, authors, _reviews, _delivery = await _run_scenario(
        tmp_path, monkeypatch, scenario, review_version="4"
    )
    assert len(authors) == 2
    initial = json.loads(authors[0]["messages"][-1]["content"])
    corrected = json.loads(authors[1]["messages"][-1]["content"])
    correction = corrected["correction"]
    assert "correction" not in initial
    for key in ("inner_turn", "inner_life_snapshot", "capability_manifest"):
        assert corrected[key] == initial[key]
    assert correction["failure_code"] == "role_result_schema_invalid"
    expected_detail = "silent expression" if scenario.startswith("silence_") else "delay_seconds"
    assert expected_detail in correction["failure_detail"]
    if scenario.endswith("_reselect"):
        # The model chose a valid new now expression and this exact hope window.
        plans = [json.loads(row.proposal_json) for row in evidence.projection.proposal_audits
                 if row.proposal_id.startswith("proposal:proactive:")]
        assert len(plans) == 1
        change = next(change for change in plans[0]["proposed_changes"]
                      if change["kind"] == "expression_plan_transition")
        expectation = json.loads(change["payload"]["canonical_json"])["response_expectation"]
        assert expectation["wait_seconds"] == 40_123
        assert expectation["expires_after_seconds"] == 99_876


@pytest.mark.asyncio
@pytest.mark.parametrize("review_required", [False, True])
@pytest.mark.parametrize("scenario", ["empty_claim_reselect", "empty_claim_twice"])
async def test_proactive_empty_source_claim_reaches_same_role_correction(
    tmp_path, monkeypatch, review_required, scenario,
):
    authored_outputs = []
    evidence, authors, _reviews, _delivery = await _run_scenario(
        tmp_path, monkeypatch, scenario, review_version="4" if review_required else "1",
        review_required=review_required, authored_outputs=authored_outputs,
    )
    initial = json.loads(authors[0]["messages"][-1]["content"])
    corrected = json.loads(authors[1]["messages"][-1]["content"])
    assert "correction" not in initial
    for key in ("inner_turn", "inner_life_snapshot", "capability_manifest"):
        assert corrected[key] == initial[key]
    correction = corrected["correction"]
    assert correction["failure_code"] == "role_result_schema_invalid"
    assert "world claim scope requires matching source refs" in correction["failure_detail"]
    # The same author must replace the unsupported assertion, not just erase
    # its declaration while leaving the visible assertion in place.
    raw_claim = json.loads(authored_outputs[0])["decision"]["payload"]["world_claims"][0]
    assert raw_claim == {"claim_text": UNSOURCED_TEXT, "scope": "past_world", "source_refs": []}
    if scenario.endswith("reselect"):
        replacement = json.loads(authored_outputs[1])["decision"]["payload"]
        assert tuple(beat["text"] for beat in replacement["beats"]) == SAFE_TEXTS
        assert replacement["world_claims"] == []
    paid = [RecordedModelResultAudit.model_validate_json(row.audit_json)
            for row in evidence.projection.model_result_audits]
    rejected_hash = hashlib.sha256(authored_outputs[0].encode()).hexdigest()
    rejected = [row for row in paid if row.response_hash == rejected_hash]
    if review_required:
        assert rejected, "the original invalid provider response hash must remain auditable"
        assert len(rejected) == (2 if scenario.endswith("twice") else 1)
        assert all(row.usage is not None for row in rejected)
        assert any(row.failure_code is not None for row in rejected)


@pytest.mark.asyncio
@pytest.mark.parametrize("review_required", [False, True])
@pytest.mark.parametrize("scenario", [
    "valid_claim", "scope_claim_reselect", "scope_claim_twice",
    "mixed_claim_reselect", "mixed_claim_twice",
])
async def test_proactive_source_lane_and_mixed_claims_require_role_reselection(
    tmp_path, monkeypatch, scenario, review_required,
):
    from companion_daemon.world_v2.character_interior import core

    snapshot_keys = []
    snapshot_key = core._snapshot_cache_key

    def record_snapshot_key(subject):
        key = snapshot_key(subject)
        if subject.purpose == "proactive_contact":
            snapshot_keys.append(key)
        return key

    monkeypatch.setattr(core, "_snapshot_cache_key", record_snapshot_key)
    raws = []
    evidence, authors, reviews, _delivery = await _run_scenario(
        tmp_path, monkeypatch, scenario, review_version="4" if review_required else "1",
        review_required=review_required, authored_outputs=raws,
    )
    raw_payloads = [json.loads(raw)["decision"]["payload"] for raw in raws]
    if not review_required:
        assert len(snapshot_keys) >= 2, "the public owner must prepare and use its snapshot"
        assert len(set(snapshot_keys)) == 1, "new claim metadata broke the prepared snapshot identity"
    initial = json.loads(authors[0]["messages"][-1]["content"])
    lanes = initial["capability_manifest"]["payload"]["world_claim_source_lanes"]
    assert lanes["contract"] == "proactive-world-claim-source-lanes.1"
    if not review_required:
        _manifest_from_role_packet(initial)
        tampered = json.loads(json.dumps(initial))
        tampered["capability_manifest"]["payload"].pop("world_claim_source_lanes")
        with pytest.raises(ValueError, match="payload hash is invalid"):
            _manifest_from_role_packet(tampered)
    ref = raw_payloads[0]["world_claims"][0]["source_refs"][0]
    assert ref in lanes["source_refs_by_scope"]["shared_history"]
    assert ref not in lanes["source_refs_by_scope"]["current_world"]
    if scenario != "valid_claim":
        corrected = json.loads(authors[1]["messages"][-1]["content"])
        for key in ("inner_turn", "inner_life_snapshot", "capability_manifest"):
            assert corrected[key] == initial[key]
        assert corrected["correction"]["failure_code"] == "role_result_schema_invalid"
        assert "outside its semantic source lane" in corrected["correction"]["failure_detail"]
        index = 1 if scenario.startswith("mixed_") else 0
        assert f"world_claims[{index}].source_refs" in corrected["correction"]["failure_detail"]
        if review_required:
            rejected_hash = hashlib.sha256(raws[0].encode()).hexdigest()
            audits = [RecordedModelResultAudit.model_validate_json(row.audit_json)
                      for row in evidence.projection.model_result_audits]
            rejected = [row for row in audits if row.response_hash == rejected_hash]
            assert len(rejected) == (2 if scenario.endswith("twice") else 1)
            assert all(row.usage is not None and row.failure_code is not None for row in rejected)
    if scenario.endswith("twice"):
        assert raw_payloads[0] == raw_payloads[1]
        assert len(reviews) == int(review_required), "only the initial inbound may reach the reviewer"
    else:
        expected_claims = raw_payloads[-1]["world_claims"]
        assert len(expected_claims) == 1
        assert expected_claims[0]["scope"] == "shared_history"
        assert expected_claims[0]["claim_text"] == HISTORY_TEXT
        if review_required:
            assert json.loads(reviews[-1]["messages"][-1]["content"])["world_claims"] == expected_claims
        (audit,) = [row for row in evidence.projection.proposal_audits
                    if row.proposal_id.startswith("proposal:proactive:")]
        plan = next(change["payload"]["canonical_json"]
                    for change in json.loads(audit.proposal_json)["proposed_changes"]
                    if change["kind"] == "expression_plan_transition")
        assert json.loads(plan)["world_claims"] == expected_claims
        if scenario.startswith("scope_"):
            first, second = raw_payloads
            assert first["world_claims"][0]["scope"] == "current_world"
            assert second == {**first, "world_claims": [
                {**first["world_claims"][0], "scope": "shared_history"},
            ]}, "only the role authored this changed semantic scope"
