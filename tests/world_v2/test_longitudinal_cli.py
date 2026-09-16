from __future__ import annotations

import asyncio
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import httpx
import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/run_world_v2_longitudinal_audit.py"


def _cli():
    spec = importlib.util.spec_from_file_location("longitudinal_audit_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", ["0", "-0.1", "nan", "inf", "101"])
def test_invalid_cost_limit_rejected_before_any_output(tmp_path, value):
    output = tmp_path / "fresh"
    with pytest.raises(SystemExit) as failure:
        _cli().parse_options(["--output", str(output), "--max-cost-cny", value])
    assert failure.value.code == 2
    assert not output.exists()


def test_real_provider_requires_explicit_opt_in(tmp_path):
    with pytest.raises(SystemExit):
        _cli().parse_options(["--output", str(tmp_path / "fresh"), "--model-mode", "real-provider"])


def test_life_candidate_review_requires_real_capture_profile(tmp_path):
    cli = _cli()
    with pytest.raises(SystemExit):
        cli.parse_options(["--output", str(tmp_path / "fresh"), "--require-life-candidate-review"])
    assert not cli.parse_options(["--output", str(tmp_path / "fresh")]).require_life_candidate_review


@pytest.mark.asyncio
async def test_life_candidate_gate_reaches_real_host_and_evidence_survives_restart(tmp_path, monkeypatch):
    import sqlite3
    import companion_daemon.world_v2.longitudinal_journey as runner
    from companion_daemon.world_v2.life_content_store import StoredLifeContent, life_content_payload_hash

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "fixture-debug-key")
    monkeypatch.setenv("DEEPSEEK_CHARACTER_THINKING_ENABLED", "false")

    def no_network(request):
        raise AssertionError("composition/restart must not call the provider")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(no_network))
    record = StoredLifeContent(content_ref="test:review-evidence", content_kind="raw_model_request",
                               text="pinned request", content_payload_hash=life_content_payload_hash("pinned request"))

    async def reopen(**kwargs):
        kwargs["output"].mkdir()
        clock = runner.JourneyClock(kwargs["journey"].started_at)
        assert kwargs['provenance']['life_candidate_review']['enabled'] is True
        for cycle in range(2):
            host = kwargs['host_factory'](kwargs['output'] / 'world.sqlite', clock, runner.CaptureDelivery(clock))
            role = host._semantic_chat.character_interior._registry.for_purpose('world_stimulus_appraisal')
            reviewer = role._life_source_reviewer
            assert role.requires_life_source_review
            assert reviewer.model.max_completion_tokens == 8192
            try:
                if cycle == 0:
                    reviewer.store.put_if_absent(record)
                assert reviewer.store.read_exact(content_ref=record.content_ref) == record
            finally:
                await host.aclose()
                await host.wait_for_shutdown_quiescence()
                await kwargs['close_resources']()
            assert reviewer.model.client.is_closed
            with pytest.raises(sqlite3.ProgrammingError):
                reviewer.store.read_exact(content_ref=record.content_ref)
        return {'completed': True}

    monkeypatch.setattr(runner, 'run_journey', reopen)
    cli = _cli()
    result = await cli.run(cli.parse_options([
        '--output', str(tmp_path / 'run'), '--model-mode', 'real-provider',
        '--allow-real-provider', '--require-life-candidate-review',
    ]))
    assert result['completed']


def test_whole_source_review_cannot_be_claimed_by_the_legacy_fixture(tmp_path):
    output = tmp_path / "fresh"
    cli = _cli()
    with pytest.raises(SystemExit):
        cli.parse_options(["--output", str(output), "--require-visible-source-review"])
    assert not output.exists()
    options = cli.parse_options([
        "--output", str(output), "--model-mode", "real-provider", "--allow-real-provider",
        "--require-visible-source-review",
    ])
    assert options.require_visible_source_review
    assert not cli.parse_options(["--output", str(output)]).require_visible_source_review
    with pytest.raises(SystemExit):
        cli.parse_options([
            "--output", str(output), "--model-mode", "real-provider", "--allow-real-provider",
            "--visible-author-tool-version", "2",
        ])


def test_existing_output_is_preserved(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "do-not-delete.txt"
    marker.write_text("existing evidence")
    with pytest.raises(SystemExit):
        _cli().parse_options(["--output", str(output)])
    assert marker.read_text() == "existing evidence"


def test_fixture_settings_ignore_ambient_provider_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret-never-use")
    monkeypatch.setenv("WORLD_V2_TEXT_ENDPOINT_ENABLED", "true")
    settings = _cli().experiment_settings(
        database=tmp_path / "world.sqlite",
        synthetic=True,
        max_cost_cny=0.5,
    )
    assert settings.deepseek_api_key is None
    assert settings.world_v2_text_endpoint_enabled is False
    assert settings.world_v2_recall_semantic_enabled is False
    assert settings.world_v2_external_perception_mode == "off"
    assert settings.world_v2_media_preview_enabled is False


def test_real_provider_trial_uses_one_budget_and_isolated_debug_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "fixture-debug-key")
    monkeypatch.setenv("MONTHLY_BUDGET_CNY", "80")
    settings = _cli().experiment_settings(
        database=tmp_path / "world.sqlite",
        synthetic=False,
        max_cost_cny=0.5,
    )
    assert (
        settings.monthly_budget_cny
        == settings.daily_budget_cny
        == settings.soft_daily_budget_cny
        == 0.5
    )
    assert settings.world_v2_recall_semantic_enabled is False
    assert settings.world_v2_text_endpoint_enabled is False


@pytest.mark.parametrize("self_review", [False, True])
@pytest.mark.parametrize("source_review", [False, True])
def test_real_life_review_profile_reports_configuration_without_enabling_it(
    tmp_path,
    monkeypatch,
    self_review,
    source_review,
):
    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "fixture-debug-key")
    monkeypatch.setenv("WORLD_V2_LIFE_SOURCE_REVIEW_ENABLED", str(source_review).lower())
    monkeypatch.setenv("WORLD_V2_LIFE_SELF_REVIEW_ALLOWED", str(self_review).lower())
    cli = _cli()
    settings = cli.experiment_settings(
        database=tmp_path / "world.sqlite",
        synthetic=False,
        max_cost_cny=0.5,
    )
    profile = cli.life_review_profile(settings, synthetic=False)
    available = source_review and self_review
    assert settings.world_v2_life_self_review_allowed is self_review
    assert settings.world_v2_life_source_review_enabled is source_review
    assert profile["status"] == (
        "configured_self_review" if available else "unavailable" if source_review else "disabled"
    )
    assert profile["model"] == (settings.deepseek_model if available else None)
    assert profile["general_source_closure"] == (
        "configured_world_author_self_review" if available else "unavailable"
    )
    assert profile["novel_origin_review"] == (
        "configured_world_author_self_review" if available else "unavailable"
    )
    assert profile["pending_candidate_policy"] == "model_semantic_review_required"
    assert profile["semantic_entailment_verified"] is False
    assert profile["independent_reviewer_qualified"] is False
    assert profile["richness_coverage"] == "requires_manual_evaluation"
    assert "fixture-debug-key" not in json.dumps(profile)


@pytest.mark.asyncio
async def test_fixture_factory_constructs_real_host_without_external_clients(tmp_path, monkeypatch):
    import companion_daemon.world_v2.longitudinal_journey as runner

    def reject_client(*args, **kwargs):
        raise AssertionError("fixture must not construct external HTTP clients")

    monkeypatch.setattr(httpx, "AsyncClient", reject_client)
    monkeypatch.setattr(httpx, "Client", reject_client)
    captured = {}

    async def build_only(**kwargs):
        captured.update(kwargs)
        kwargs["output"].mkdir()
        clock = runner.JourneyClock(kwargs["journey"].started_at)
        delivery = runner.CaptureDelivery(clock)
        host = kwargs["host_factory"](kwargs["output"] / "world.sqlite", clock, delivery)
        try:
            policy = host._interactive_turn_budget_policy
            assert policy.clock is time.monotonic
            assert policy.wall_clock == clock.presentation_now
            assert policy.total_seconds == 12
            budget = policy.start(processing_started_at=clock.presentation_now())
            assert 10 < budget.author_remaining() <= 11
            assert host.export_replay_evidence() is not None
        finally:
            await host.aclose()
        return {"completed": True, "synthetic": True, "stop_reason": "fixture_test"}

    monkeypatch.setattr(runner, "run_journey", build_only)
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "scenario_id": "fixture-build",
                "started_at": "2026-09-01T00:00:00+00:00",
                "duration_minutes": 10,
                "turns": [],
                "restart_minutes": [],
            }
        )
    )
    cli = _cli()
    options = cli.parse_options(["--scenario", str(scenario), "--output", str(tmp_path / "run")])
    result = await cli.run(options)
    assert result["completed"] is True
    assert captured["synthetic"] is True
    assert captured["provenance"]["context_input_verification"] == "unverified"
    assert "no generated rich life" in captured["provenance"]["fixture_limitations"]
    assert (
        captured["provenance"]["scenario_sha256"]
        == hashlib.sha256(scenario.read_bytes()).hexdigest()
    )
    assert len(captured["provenance"]["code"]["head"]) == 40
    assert type(captured["provenance"]["code"]["tracked_dirty"]) is bool
    assert captured["provenance"]["models"]["character"] == "longitudinal-fixture.1"


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_total,hedge_after,legacy_total,expected_total", [
    ("12", "1.3", None, 12.0),
    ("8", "0.7", None, 8.0),
    ("8", "0.7", "9", 9.0),
])
async def test_real_cli_installs_and_records_the_configured_timing_policy(
    tmp_path, monkeypatch, configured_total, hedge_after, legacy_total, expected_total,
):
    """Exercise the real runner factory; changing env must reach the actual host."""
    import companion_daemon.world_v2.longitudinal_journey as runner

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "fixture-debug-key")
    monkeypatch.setenv("DEEPSEEK_CHARACTER_THINKING_ENABLED", "false")
    monkeypatch.setenv("WORLD_V2_INTERACTIVE_HEDGE_ENABLED", "true")
    monkeypatch.setenv("WORLD_V2_INTERACTIVE_HEDGE_AFTER_SECONDS", hedge_after)
    monkeypatch.setenv("WORLD_V2_INTERACTIVE_TURN_BUDGET_SECONDS", configured_total)
    if legacy_total is None:
        monkeypatch.delenv("DSH_INTERACTIVE_TURN_BUDGET_SECONDS", raising=False)
    else:
        monkeypatch.setenv("DSH_INTERACTIVE_TURN_BUDGET_SECONDS", legacy_total)

    def unexpected_request(_request):
        raise AssertionError("policy qualification must not issue a provider request")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(unexpected_request))

    async def build_only(**kwargs):
        kwargs["output"].mkdir()
        clock = runner.JourneyClock(kwargs["journey"].started_at)
        host = kwargs["host_factory"](
            kwargs["output"] / "world.sqlite", clock, runner.CaptureDelivery(clock)
        )
        try:
            policy = host._interactive_turn_budget_policy
            assert policy.total_seconds == expected_total
            assert policy.hedge_after_seconds == float(hedge_after)
            assert policy.clock is time.monotonic
            assert policy.sleep is asyncio.sleep
            assert policy.wall_clock == clock.presentation_now
            profile = kwargs["provenance"]["interactive_timing_policy"]
            assert profile["total_seconds"] == policy.total_seconds
            assert profile["hedge_after_seconds"] == policy.hedge_after_seconds
            assert profile["speculative_hedge_enabled"] is True
            assert profile["legacy_total_override"] is (legacy_total is not None)
            assert profile["clock_scope"] == "real_provider_deadline_with_virtual_presentation"
        finally:
            await host.aclose()
            await kwargs["close_resources"]()
        return {"completed": False, "stop_reason": "policy_checked_without_model_calls"}

    monkeypatch.setattr(runner, "run_journey", build_only)
    scenario = tmp_path / "policy.json"
    scenario.write_text(json.dumps({
        "scenario_id": "timing-policy", "started_at": "2026-09-13T02:00:00+00:00",
        "duration_minutes": 1, "turns": [],
    }))
    cli = _cli()
    result = await cli.run(cli.parse_options([
        "--scenario", str(scenario), "--output", str(tmp_path / "run"),
        "--model-mode", "real-provider", "--allow-real-provider",
    ]))
    assert result["stop_reason"] == "policy_checked_without_model_calls"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_total", ["nan", "0", "1", "invalid"])
async def test_invalid_timing_policy_fails_before_capture_or_provider(
    tmp_path, monkeypatch, legacy_total,
):
    import companion_daemon.world_v2.longitudinal_journey as runner
    import httpx

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid timing must fail before clients or journey startup")

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "offline-policy-test")
    monkeypatch.setenv("DSH_INTERACTIVE_TURN_BUDGET_SECONDS", legacy_total)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", forbidden)
    monkeypatch.setattr(runner, "run_journey", forbidden)
    scenario = tmp_path / "policy-invalid.json"
    scenario.write_text(json.dumps({
        "scenario_id": "timing-invalid", "started_at": "2026-09-13T02:00:00+00:00",
        "duration_minutes": 1, "turns": [],
    }))
    cli = _cli()
    output = tmp_path / "run"
    with pytest.raises(ValueError):
        await cli.run(cli.parse_options([
            "--scenario", str(scenario), "--output", str(output),
            "--model-mode", "real-provider", "--allow-real-provider",
        ]))
    assert not output.exists()


@pytest.mark.asyncio
async def test_adaptive_dialogue_reads_delivered_reply_before_next_input_and_can_wait(tmp_path):
    cli = _cli()
    scenario = tmp_path / "adaptive.json"
    scenario.write_text(
        json.dumps(
            {
                "scenario_id": "adaptive-dialogue",
                "started_at": "2026-09-08T10:00:00+08:00",
                "duration_minutes": 10,
                "turns": [],
            }
        )
    )
    observations = []

    async def decide(observation):
        observations.append(observation)
        if len(observations) == 1:
            return {"id": "first", "at_minutes": 0, "text": "今天怎么样？"}
        if len(observations) == 2:
            return {"wait_until_minutes": 2}
        if len(observations) == 3:
            visible = [
                record["text"]
                for seen in observations
                for row in seen["steps"]
                for record in row["deliveries"]
                if record["kind"] == "text"
            ]
            assert visible, "The next input must be chosen after reading an actual delivery"
            assert observation["elapsed_minutes"] == 2
            return {"id": "follow-up", "at_minutes": 2, "text": "刚才看到你说：" + visible[-1]}
        return None

    output = tmp_path / "run"
    manifest = await cli.run(
        cli.parse_options(
            [
                "--scenario",
                str(scenario),
                "--output",
                str(output),
                "--interactive",
            ]
        ),
        next_command=decide,
    )
    assert manifest["stop_reason"] == "operator_stopped"
    assert not manifest["completed"]
    assert manifest["turns_consumed"] == manifest["turns_requested"] == 2
    assert manifest["interaction_mode"] == "adaptive"
    assert manifest["replay"]["replay_hash_matches"]
    rows = [json.loads(line) for line in (output / "timeline.jsonl").read_text().splitlines()]
    inputs = [row for row in rows if row["kind"] == "inbound"]
    assert [row["turn_id"] for row in inputs] == ["first", "follow-up"]
    assert inputs[-1]["user_text"].startswith("刚才看到你说：")
    commands = [
        json.loads(line) for line in (output / "operator-commands.jsonl").read_text().splitlines()
    ]
    assert commands[1]["command"] == {"wait_until_minutes": 2}
    assert "operator-commands.jsonl" in manifest["artifacts"]


@pytest.mark.parametrize("interactive", [True, False])
def test_cli_prints_a_final_observation_only_for_adaptive_runs(
    tmp_path, monkeypatch, capsys, interactive
):
    scenario = tmp_path / "adaptive-end.json"
    scenario.write_text(
        json.dumps(
            {
                "scenario_id": "adaptive-end",
                "started_at": "2026-09-08T10:00:00+08:00",
                "duration_minutes": 2,
                "turns": [],
            }
        )
    )
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b'{"wait_until_minutes":2}\n')
    os.close(write_fd)
    output = tmp_path / "run"
    with os.fdopen(read_fd, "rb") as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        exit_code = _cli().main(
            ["--scenario", str(scenario), "--output", str(output)]
            + (["--interactive"] if interactive else [])
        )
        os.fstat(stream.fileno())

    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    saved = json.loads((output / "manifest.json").read_text())
    if not interactive:
        assert len(printed) == 1
        assert "operator_final_observation" not in printed[0]
        assert "operator_final_observation" not in saved
        return
    assert len(printed) == 2  # Initial command request, then a terminal summary.
    assert printed[0]["operator_observation"]["elapsed_minutes"] == 0
    final = printed[-1]["operator_final_observation"]
    assert final["elapsed_minutes"] == 2
    assert final["steps"][-1]["kind"] == "final"
    assert saved["operator_final_observation"] == final


@pytest.mark.asyncio
async def test_fixture_public_journey_does_not_expire_virtual_ingress_deadline(
    tmp_path, monkeypatch
):
    import companion_daemon.world_v2.longitudinal_journey  # noqa: F401

    def reject_client(*args, **kwargs):
        raise AssertionError("fixture must not construct external HTTP clients")

    monkeypatch.setattr(httpx, "AsyncClient", reject_client)
    monkeypatch.setattr(httpx, "Client", reject_client)
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "scenario_id": "fixture-deadline",
                "started_at": "2026-09-01T00:00:00+00:00",
                "duration_minutes": 3,
                "restart_minutes": [],
                "turns": [{"id": "first", "at_minutes": 0, "text": "早，今天想随便聊聊。"}],
            }
        )
    )
    output = tmp_path / "run"
    cli = _cli()
    result = await cli.run(
        cli.parse_options(
            [
                "--scenario",
                str(scenario),
                "--output",
                str(output),
                "--max-wall-seconds",
                "30",
            ]
        )
    )
    assert result["completed"] is True
    events = [json.loads(line) for line in (output / "evidence.jsonl").read_text().splitlines()]
    audits = [
        json.loads(json.loads(event["payload_json"])["audit_json"])
        for event in events
        if event["event_type"] == "ModelResultRecorded"
    ]
    assert audits
    assert all(audit.get("failure_code") is None for audit in audits)
    assert any(audit["status"] == "provider_completed" for audit in audits)
    rows = [json.loads(line) for line in (output / "timeline.jsonl").read_text().splitlines()]
    assert sum(delivery["kind"] == "text" for row in rows for delivery in row["deliveries"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("required_review", [False, True])
async def test_real_cli_captures_actual_provider_body_and_closes_injected_clients(
    tmp_path, monkeypatch, required_review
):
    import companion_daemon.llm as llm
    import companion_daemon.world_v2.longitudinal_journey as runner

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "fixture-debug-key")
    monkeypatch.setenv("DEEPSEEK_CHARACTER_THINKING_ENABLED", "true")
    clients = []
    actual_model = llm.DeepSeekChatModel

    def observed_model(*args, **kwargs):
        client = actual_model(*args, **kwargs)
        clients.append(client)
        return client

    def respond(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
            },
        )

    monkeypatch.setattr(llm, "DeepSeekChatModel", observed_model)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond))

    async def build_and_call(**kwargs):
        kwargs["output"].mkdir()
        clock = runner.JourneyClock(kwargs["journey"].started_at)
        host = kwargs["host_factory"](
            kwargs["output"] / "world.sqlite", clock, runner.CaptureDelivery(clock)
        )
        try:
            assert len(clients) == (4 if required_review else 3)
            assert clients[0].max_completion_tokens == 4096
            assert clients[1].thinking_enabled is True
            assert clients[1].max_completion_tokens == 900
            assert clients[2].max_completion_tokens == 4096
            if required_review:
                assert clients[3].max_completion_tokens == 4096
                assert kwargs["provenance"]["visible_source_review"] == {
                    "policy": "visible-source-review-required.1",
                    "expression_episode_mode": "off",
                    "review_model": clients[3].model,
                    "qualification": "requires_evaluation_of_actual_records",
                }
            else:
                assert "visible_source_review" not in kwargs["provenance"]
            with llm.model_call_scope("inbound_turn"):
                result = await clients[0].complete_json(
                    [{"role": "user", "content": "计划改到周二"}]
                )
            assert json.loads(result) == {"ok": True}
            if required_review:
                with llm.model_call_scope("source_review"):
                    review, _ = await clients[3].complete_json_with_usage(
                        [{"role": "user", "content": "独立审核完整候选消息"}]
                    )
                assert json.loads(review) == {"ok": True}
            _, records = kwargs["model_input_capture"].read_since()
            requests = [record for record in records if record["kind"] == "request"]
            assert len(requests) == (2 if required_review else 1)
            assert json.loads(requests[0]["model_content_json"])["messages"] == [
                {"role": "user", "content": "计划改到周二"}
            ]
            if required_review:
                assert requests[1]["model_role"] == "visible_source_review"
                assert json.loads(requests[1]["model_content_json"])["messages"] == [
                    {"role": "user", "content": "独立审核完整候选消息"}
                ]
            assert host.usage_budget_health()["daily_cost_cny"] > 0
        finally:
            await host.aclose()
            await host.wait_for_shutdown_quiescence()
            await kwargs["close_resources"]()
        assert all(client.client.is_closed for client in clients)
        return {"completed": True, "synthetic": False, "stop_reason": "test_complete"}

    monkeypatch.setattr(runner, "run_journey", build_and_call)
    cli = _cli()
    result = await cli.run(
        cli.parse_options(
            [
                "--output",
                str(tmp_path / "run"),
                "--model-mode",
                "real-provider",
                "--allow-real-provider",
                *(["--require-visible-source-review"] if required_review else []),
            ]
        )
    )
    assert result["completed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("recall_first", [False, True])
@pytest.mark.parametrize("tool_version", ["1", "2", "3"])
async def test_required_review_cli_host_holds_complete_candidate_until_review(
    tmp_path, monkeypatch, recall_first, tool_version
):
    import companion_daemon.world_v2.longitudinal_journey as runner
    from test_whole_candidate_author import BEATS, _decision
    from test_world_stimulus_life_intent import _http_result

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "fixture-debug-key")
    monkeypatch.setenv("DEEPSEEK_CHARACTER_THINKING_ENABLED", "false")
    requests = []
    review_started = asyncio.Event()
    release_review = asyncio.Event()

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert not body.get("stream")
        name = body["tool_choice"]["function"]["name"]
        if name in {
            f"character_inbound_initial_v{tool_version}",
            f"character_inbound_after_recall_v{tool_version}",
        }:
            assert "visible_source_requirement_json" not in json.dumps(body)
            authored = _decision()
            if recall_first and len(requests) == 1:
                authored = {
                    "result_kind": "recall",
                    "private_turn_state": {
                        "contract": "private-turn-state.1",
                        "inner_state_summary": "我想先确认之前的对话，再说自己的想法。",
                        "attended_source_refs": [],
                    },
                    "recall_request": {
                        "query_text": "之前的对话", "memory_kinds": ["episodic", "semantic"],
                        "limit": 4,
                    },
                }
            schema = body["tools"][0]["function"]["parameters"]
            if tool_version != "1":
                instruction = body["messages"][0]["content"]
                assert instruction.index(f"ATOMIC TOOL ENVELOPE V{tool_version}:") > instruction.index("FORCED TOOL TRANSPORT")
                assert "Its arguments must include result_kind." not in instruction
                assert set(schema["properties"]) == {"result"}
                variants = schema["properties"]["result"].get("anyOf", [schema["properties"]["result"]])
                selected = next(
                    branch for branch in variants
                    if branch["properties"]["result_kind"]["enum"] == [authored["result_kind"]]
                )
                return _http_result(body, {
                    "result": {key: authored.get(key) for key in selected["properties"]},
                })
            return _http_result(body, {key: authored.get(key) for key in schema["properties"]})
        review_started.set()
        await release_review.wait()
        return _http_result(body, {
            "contract": "visible-beat-source-verdict.1",
            "decisions": [
                {"beat_index": i, "verdict": "source_free", "semantic_role": "commitment",
                 "subject_role": "companion", "source_ref_indexes": []}
                for i in range(len(BEATS))
            ],
        })

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond))

    async def run_inbound(**kwargs):
        kwargs["output"].mkdir()
        clock = runner.JourneyClock(kwargs["journey"].started_at)
        delivery = runner.CaptureDelivery(clock)
        host = kwargs["host_factory"](kwargs["output"] / "world.sqlite", clock, delivery)
        task = asyncio.create_task(host.inbound_text(
            message_id="whole-review-cli", recipient_id=runner.RECIPIENT,
            text="你想怎么说？", observed_at=clock.now(),
        ))
        try:
            await asyncio.wait_for(review_started.wait(), 5)
            assert not host.export_replay_evidence().projection.actions
            assert not [row for row in delivery.records if row["kind"] == "text"]
            release_review.set()
            result = await task
            assert result.status == "action_authorized", result
            evidence = host.export_replay_evidence()
            assert tuple(row.text for row in evidence.projection.stored_message_payloads) == BEATS
            _, capture = kwargs["model_input_capture"].read_since()
            captured_requests = [row for row in capture if row["kind"] == "request"]
            assert [row["model_role"] for row in captured_requests] == [
                "flash", *(["flash"] if recall_first else []), "visible_source_review",
            ]
            assert len(requests) == (3 if recall_first else 2)
            author_requests = requests[:-1]
            assert [
                json.loads(body["messages"][1]["content"])["recall_available"]
                for body in author_requests
            ] == [True, *([False] if recall_first else [])]
            if tool_version != "1":
                from companion_daemon.usage_metrics import estimate_provider_request_reserve_cny

                assert kwargs["provenance"]["visible_author_tool_version"] == tool_version
                # Use the same conservative final-wire estimator as actual admission.
                # The complete author still fits the default isolated allowance.
                assert estimate_provider_request_reserve_cny(request_payload=requests[0]) < 0.5
            else:
                assert "visible_author_tool_version" not in kwargs["provenance"]
            assert host.usage_budget_health()["daily_cost_cny"] > 0
        finally:
            release_review.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await host.aclose()
            await host.wait_for_shutdown_quiescence()
            await kwargs["close_resources"]()
        reopened = runner.cold_evidence(kwargs["output"] / "world.sqlite")
        assert tuple(row.text for row in reopened.projection.stored_message_payloads) == BEATS
        replay = runner.ReplayEvaluator().evaluate(evidence=reopened)
        assert replay.passed and replay.replay_hash_matches
        assert len(requests) == (3 if recall_first else 2)
        return {"completed": True}

    monkeypatch.setattr(runner, "run_journey", run_inbound)
    cli = _cli()
    assert (await cli.run(cli.parse_options([
        "--output", str(tmp_path / "run"), "--model-mode", "real-provider",
        "--allow-real-provider", "--require-visible-source-review",
        "--visible-author-tool-version", tool_version,
        *( ["--max-cost-cny", "0.60"] if tool_version == "1" else [] ),
    ])))["completed"]


@pytest.mark.parametrize("version", ["2", "3"])
def test_versioned_author_cannot_be_installed_without_metered_review(version):
    from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor

    with pytest.raises(ValueError, match="explicit metered source reviewer"):
        _InboundCharacterAuthor(
            flash_model=object(), whole_candidate_mode=True, atomic_tool_envelope_version=version,
        )


@pytest.mark.asyncio
async def test_fixture_uses_current_slim_inbound_contract():
    from companion_daemon.world_v2.longitudinal_fixture_model import LongitudinalFixtureModel

    model = LongitudinalFixtureModel()
    raw = await model.complete([{"role": "system", "content": "meaning_of_this my_state messages"}])
    result = json.loads(raw)
    assert set(result) == {"messages", "meaning_of_this", "my_state"}
    assert result["messages"]


@pytest.mark.asyncio
async def test_fixture_stream_usage_is_valid_offline_evidence():
    from companion_daemon.world_v2.deliberation import ModelUsageProvenance
    from companion_daemon.world_v2.longitudinal_fixture_model import LongitudinalFixtureModel

    chunks = []
    raw, usage = await LongitudinalFixtureModel().complete_json_stream_with_usage(
        [{"role": "system", "content": "meaning_of_this my_state messages"}],
        on_text_delta=chunks.append,
    )
    validated = ModelUsageProvenance.model_validate(usage)
    assert validated.transport == "offline_fixture"
    assert validated.token_provenance == "offline_estimated"
    assert "".join(chunks) == raw


@pytest.mark.asyncio
async def test_fixture_fact_batch_decides_each_supplied_observation():
    from companion_daemon.world_v2.fact_draft_adapter import FactObservationProposalAdapter
    from companion_daemon.world_v2.longitudinal_fixture_model import LongitudinalFixtureModel

    raw = await LongitudinalFixtureModel().complete(
        [
            {
                "role": "system",
                "content": FactObservationProposalAdapter._system_contract(batch=True),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "observations": [
                            {"observation_id": "observation:first"},
                            {"observation_id": "observation:second"},
                        ]
                    }
                ),
            },
        ]
    )
    assert json.loads(raw) == {
        "decisions": [
            {"observation_id": "observation:first", "result": {"retain": False}},
            {"observation_id": "observation:second", "result": {"retain": False}},
        ]
    }


@pytest.mark.asyncio
async def test_fixture_required_tool_checks_selected_name_and_preserves_role_schema():
    from companion_daemon.world_v2.character_interior.structured_role import _WireRoleResult
    from companion_daemon.world_v2.longitudinal_fixture_model import LongitudinalFixtureModel

    model = LongitudinalFixtureModel()
    payload = {
        "inner_turn": {"purpose": "private_impression_reflection"},
        "wire_contract": {"allowed_statuses": ["no_change", "transition"]},
    }
    messages = [{"role": "user", "content": json.dumps(payload)}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "fixture_role_contract",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "result": _WireRoleResult.model_json_schema(),
                    },
                },
            },
        }
    ]
    with pytest.raises(ValueError, match="does not match"):
        await model.complete_json(messages, tools=tools, tool_choice="auto")
    raw = await model.complete_json(
        messages,
        tools=tools,
        tool_choice={
            "type": "function",
            "function": {"name": "fixture_role_contract"},
        },
    )
    result = _WireRoleResult.model_validate(json.loads(raw)["result"])
    assert result.status == "no_change"
    assert result.proposals == []


def test_explicit_uncapped_trial_keeps_default_cap_and_overrides_ambient_caps(tmp_path, monkeypatch):
    cli = _cli()
    args = ['--output', str(tmp_path / 'fresh')]
    assert cli.parse_options(args).max_cost_cny == 0.5
    options = cli.parse_options([*args, '--no-cost-cap'])
    assert options.max_cost_cny is None
    with pytest.raises(SystemExit):
        cli.parse_options([*args, '--no-cost-cap', '--max-cost-cny', '1'])
    monkeypatch.setenv('DEEPSEEK_DEBUG_API_KEY', 'fixture-key')
    for name in ('MONTHLY_BUDGET_CNY', 'DAILY_BUDGET_CNY', 'SOFT_DAILY_BUDGET_CNY', 'WORLD_V2_BACKGROUND_DAILY_BUDGET_CNY'):
        monkeypatch.setenv(name, '1')
    settings = cli.experiment_settings(database=tmp_path / 'world.sqlite', synthetic=False, max_cost_cny=options.max_cost_cny)
    assert settings.monthly_budget_cny is None
    assert settings.daily_budget_cny is None
    assert settings.soft_daily_budget_cny is None
    assert settings.world_v2_background_daily_budget_cny == 0.0
    assert settings.world_v2_text_endpoint_enabled is False
    assert settings.world_v2_media_preview_enabled is False

    from companion_daemon.world_v2.model_usage_budget import usage_store_for_settings
    store = usage_store_for_settings(settings)
    for purpose in ("source_review", "world_life"):
        reservation = store.admit_provider_call(
            purpose=purpose, actor="agent:companion", provider="deepseek",
            model="deepseek-v4-flash", prompt_characters=1, estimated_cny=101,
        )
        assert reservation.startswith("reservation:")
    import sqlite3
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM world_v2_model_reservations").fetchone()[0] == 2


def test_source_reasoning_requires_explicit_scoped_review_profile(tmp_path):
    cli = _cli()
    base = ['--output', str(tmp_path / 'run'), '--model-mode', 'real-provider',
            '--allow-real-provider', '--require-visible-source-review']
    assert not cli.parse_options(base).visible_source_review_thinking
    with pytest.raises(SystemExit):
        cli.parse_options(base + ['--visible-source-review-thinking'])
    options = cli.parse_options(base + ['--visible-source-review-version', '18', '--visible-source-review-thinking'])
    assert options.visible_source_review_thinking
