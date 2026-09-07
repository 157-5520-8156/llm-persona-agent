from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
