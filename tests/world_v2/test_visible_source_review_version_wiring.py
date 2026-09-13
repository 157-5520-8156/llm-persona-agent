"""Explicit reviewer selection wiring; Versioned verdict semantics are tested separately."""

import json

import httpx
import pytest

from companion_daemon.config import Settings
from companion_daemon.llm import FakeCompanionModel
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
from companion_daemon.world_v2.semantic_chat_composition import build_semantic_chat_composition
from test_longitudinal_cli import _cli
from test_proactive_visible_source_gate import _run_scenario


class _MeteredFixture:
    async def complete_json_with_usage(self, *_args, **_kwargs):
        raise AssertionError("configuration test must not call a provider")


def _settings(tmp_path, **updates):
    return Settings.model_construct(
        database_path=tmp_path / "world.sqlite",
        world_v2_expression_episode_mode="off",
        **updates,
    )


@pytest.mark.parametrize("version", ["0", "9", "", 1, 2, 3, 4, 5, True, None, [], {}])
@pytest.mark.parametrize("entry", ["host", "composition", "author", "proactive"])
def test_unknown_review_versions_fail_at_each_entry(tmp_path, version, entry):
    with pytest.raises(ValueError, match="unsupported visible source review version"):
        if entry == "host":
            build_qq_c2c_host(
                settings=_settings(tmp_path), recipient_id="fixture",
                visible_source_review_version=version,
            )
        elif entry == "composition":
            build_semantic_chat_composition(
                settings=_settings(tmp_path), model_id_prefix="fixture",
                visible_source_review_version=version,
            )
        elif entry == "author":
            _InboundCharacterAuthor(
                flash_model=object(), visible_source_review_version=version,
            )
        else:
            from companion_daemon.world_v2.character_interior.proactive_visible_review import (
                ReviewedProactiveStructuredRoleFaculty,
            )

            ReviewedProactiveStructuredRoleFaculty(
                model=object(), model_id="fixture", reviewer=_MeteredFixture(),
                expression_capabilities=object(), visible_source_review_version=version,
            )
    assert not (tmp_path / "world.sqlite").exists()


@pytest.mark.parametrize("entry", ["host", "composition", "author"])
@pytest.mark.parametrize("review_version", ["2", "3", "4", "5", "6", "7", "8"])
def test_versioned_reviewer_cannot_enable_review_implicitly(tmp_path, entry, review_version):
    with pytest.raises(ValueError, match="explicit visible source review|whole-candidate"):
        if entry == "host":
            build_qq_c2c_host(
                settings=_settings(tmp_path), recipient_id="fixture",
                visible_source_review_version=review_version,
            )
        elif entry == "composition":
            build_semantic_chat_composition(
                settings=_settings(tmp_path), model_id_prefix="fixture",
                visible_source_review_version=review_version,
            )
        else:
            _InboundCharacterAuthor(
                flash_model=object(), visible_source_review_version=review_version,
            )
    assert not (tmp_path / "world.sqlite").exists()


@pytest.mark.parametrize("flag", [1, "yes"])
@pytest.mark.parametrize("review_version", ["2", "3", "4", "5", "6", "7", "8"])
def test_direct_composition_versioned_review_requires_true_boolean_before_model_setup(
    tmp_path, flag, review_version
):
    with pytest.raises(ValueError, match="explicit visible source review"):
        build_semantic_chat_composition(
            settings=_settings(tmp_path), model_id_prefix="fixture",
            visible_source_review_version=review_version, visible_source_review_required=flag,
        )
    assert not (tmp_path / "world.sqlite").exists()


@pytest.mark.parametrize("entry", ["author", "proactive"])
@pytest.mark.parametrize("review_version", ["2", "3", "4", "5", "6", "7", "8"])
def test_versioned_reviewer_requires_metered_reviewer_even_with_v1_author(entry, review_version):
    with pytest.raises(ValueError, match="metered source reviewer"):
        if entry == "author":
            _InboundCharacterAuthor(
                flash_model=object(), whole_candidate_mode=True,
                visible_source_review_version=review_version,
            )
        else:
            from companion_daemon.world_v2.character_interior.proactive_visible_review import (
                ReviewedProactiveStructuredRoleFaculty,
            )

            ReviewedProactiveStructuredRoleFaculty(
                model=object(), model_id="fixture", reviewer=None,
                expression_capabilities=object(), visible_source_review_version=review_version,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("review_version", ["1", "2", "3", "4", "5", "6", "7", "8"])
async def test_public_host_forwards_review_version_to_inbound_and_proactive(
    tmp_path, monkeypatch, review_version
):
    from companion_daemon.world_v2 import visible_source_runtime as runtime
    from companion_daemon.world_v2.character_interior import proactive_visible_review as proactive
    import test_proactive_visible_source_gate as scenario

    reviewed = []
    baseline_review = runtime.review_candidate

    async def capture_review(*, review_version, **kwargs):
        reviewed.append(review_version)
        # This test stops the new-version seam here. The established v1 audit
        # still executes all host acceptance checks; this is not versioned wire proof.
        return await baseline_review(**kwargs)

    original_host = scenario.build_qq_c2c_host

    def selected_host(**kwargs):
        return original_host(**{**kwargs, "visible_source_review_version": review_version})

    monkeypatch.setattr(scenario, "build_qq_c2c_host", selected_host)
    monkeypatch.setattr(runtime, "review_candidate", capture_review)
    monkeypatch.setattr(proactive, "review_candidate", capture_review)
    evidence, author_requests, reviewer_requests, _delivery = await _run_scenario(
        tmp_path, monkeypatch, "source_free"
    )
    assert reviewed == [review_version, review_version]
    assert len(author_requests) == 1 and len(reviewer_requests) == 2
    assert any(action.kind == "proactive_message" for action in evidence.projection.actions)


@pytest.mark.parametrize("author_version", ["1", "2", "3"])
@pytest.mark.parametrize("review_version", ["1", "2", "3", "4", "5", "6", "7", "8"])
def test_review_version_is_independent_of_author_tool_version(
    tmp_path, monkeypatch, author_version, review_version
):
    import companion_daemon.world_v2.semantic_chat_composition as composition

    class Captured(Exception):
        pass

    def capture(**kwargs):
        assert kwargs["atomic_tool_envelope_version"] == author_version
        assert kwargs["visible_source_review_version"] == review_version
        assert kwargs["whole_candidate_mode"] is True
        raise Captured

    monkeypatch.setattr(composition, "compose_production_character_interior", capture)
    with pytest.raises(Captured):
        build_qq_c2c_host(
            settings=_settings(tmp_path), recipient_id="fixture",
            model=FakeCompanionModel(), visible_source_review_required=True,
            visible_source_review_model=_MeteredFixture(),
            visible_source_review_version=review_version,
            visible_author_tool_version=author_version,
        )


@pytest.mark.parametrize("change", ["stream", "shadow", "no_reviewer", "second_reviewer"])
@pytest.mark.parametrize("review_version", ["2", "3", "4", "5", "6", "7", "8"])
def test_versioned_reviewer_keeps_existing_host_deployment_checks(tmp_path, change, review_version):
    settings = _settings(tmp_path)
    reviewer = _MeteredFixture()
    kwargs = dict(
        settings=settings, recipient_id="fixture", visible_source_review_required=True,
        visible_source_review_model=reviewer, visible_source_review_version=review_version,
    )
    message = {
        "stream": "atomic expression", "shadow": "atomic expression", "no_reviewer": "metered provider",
        "second_reviewer": "second source reviewer",
    }[change]
    if change in {"stream", "shadow"}:
        kwargs["settings"] = settings.model_copy(update={"world_v2_expression_episode_mode": change})
    elif change == "no_reviewer":
        kwargs["visible_source_review_model"] = None
    else:
        kwargs["source_closure_model"] = _MeteredFixture()
    with pytest.raises(ValueError, match=message):
        build_qq_c2c_host(**kwargs)
    assert not (tmp_path / "world.sqlite").exists()


def test_cli_requires_explicit_review_and_retains_default_v1(tmp_path):
    cli = _cli()
    output = tmp_path / "fresh"
    assert cli.parse_options(["--output", str(output)]).visible_source_review_version == "1"
    for options in (
        ["--visible-source-review-version", "2"],
        ["--model-mode", "real-provider", "--allow-real-provider", "--visible-source-review-version", "2"],
        ["--visible-source-review-version", "3"],
        ["--model-mode", "real-provider", "--allow-real-provider", "--visible-source-review-version", "3"],
        ["--visible-source-review-version", "4"],
        ["--model-mode", "real-provider", "--allow-real-provider", "--visible-source-review-version", "4"],
        ["--visible-source-review-version", "5"],
        ["--model-mode", "real-provider", "--allow-real-provider", "--visible-source-review-version", "5"],
        ["--visible-source-review-version", "6"],
        ["--visible-source-review-version", "7"],
        ["--model-mode", "real-provider", "--allow-real-provider", "--require-visible-source-review", "--visible-source-review-version", "9"],
    ):
        with pytest.raises(SystemExit) as exc:
            cli.parse_options(["--output", str(output), *options])
        assert exc.value.code == 2
        assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("review_version", ["1", "2", "3", "4", "5", "6", "7", "8"])
async def test_cli_passes_selected_review_version_and_only_records_nondefault(
    tmp_path, monkeypatch, review_version
):
    import companion_daemon.world_v2.longitudinal_journey as runner
    import companion_daemon.world_v2.qq_c2c_host as host_module

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "offline-fixture-key")
    monkeypatch.setenv("DEEPSEEK_CHARACTER_THINKING_ENABLED", "false")

    def no_requests(_request):
        raise AssertionError("configuration-only CLI must not send a provider request")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_kwargs: httpx.MockTransport(no_requests))
    selected = []

    def host_capture(**kwargs):
        selected.append(kwargs["visible_source_review_version"])
        assert kwargs["visible_source_review_required"] is True
        assert kwargs["visible_author_tool_version"] == "3"
        assert callable(kwargs["visible_source_review_model"].complete_json_with_usage)
        return object()

    monkeypatch.setattr(host_module, "build_qq_c2c_host", host_capture)

    async def capture_run(**kwargs):
        if review_version == "1":
            assert "visible_source_review_version" not in kwargs["provenance"]
        else:
            assert kwargs["provenance"]["visible_source_review_version"] == review_version
        assert "offline-fixture-key" not in json.dumps(kwargs["provenance"])
        kwargs["output"].mkdir()
        clock = runner.JourneyClock(kwargs["journey"].started_at)
        try:
            kwargs["host_factory"](
                kwargs["output"] / "world.sqlite", clock, runner.CaptureDelivery(clock)
            )
        finally:
            await kwargs["close_resources"]()
        return {"completed": True}

    monkeypatch.setattr(runner, "run_journey", capture_run)
    cli = _cli()
    result = await cli.run(cli.parse_options([
        "--output", str(tmp_path / "run"), "--model-mode", "real-provider",
        "--allow-real-provider", "--require-visible-source-review",
        "--visible-source-review-version", review_version,
        "--visible-author-tool-version", "3",
    ]))
    assert result["completed"] and selected == [review_version]


@pytest.mark.asyncio
@pytest.mark.parametrize("review_version", ["1", "4", "5", "6", "7", "8"])
async def test_actual_cli_manifest_preserves_explicit_reviewer_identity_without_provider_calls(
    tmp_path, monkeypatch, review_version,
):
    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "offline-fixture-key")
    monkeypatch.setenv("DEEPSEEK_CHARACTER_THINKING_ENABLED", "false")
    requests = []

    def reject(request):
        requests.append(request)
        raise AssertionError("configuration-only journey must not call a provider")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_kwargs: httpx.MockTransport(reject))
    scenario = tmp_path / "bootstrap.json"
    scenario.write_text(json.dumps({
        "scenario_id": "reviewer-configuration", "started_at": "2026-09-09T10:00:00+08:00",
        "duration_minutes": 1, "turns": [],
    }))
    output = tmp_path / "journey"

    async def stop(_observation):
        return None

    cli = _cli()
    manifest = await cli.run(cli.parse_options([
        "--scenario", str(scenario), "--output", str(output),
        "--interactive",
        "--model-mode", "real-provider", "--allow-real-provider",
        "--require-visible-source-review", "--visible-author-tool-version", "3",
        "--visible-source-review-version", review_version,
    ]), next_command=stop)
    saved = json.loads((output / "manifest.json").read_text())
    assert saved == json.loads(json.dumps(manifest))
    assert saved["stop_reason"] == "operator_stopped"
    assert saved["provenance"]["visible_author_tool_version"] == "3"
    assert saved["provenance"]["visible_source_review"]["expression_episode_mode"] == "off"
    if review_version == "1":
        assert "visible_source_review_version" not in saved["provenance"]
    else:
        assert saved["provenance"]["visible_source_review_version"] == review_version
    assert requests == []
    assert "offline-fixture-key" not in json.dumps(saved)
    events = [json.loads(line) for line in (output / "evidence.jsonl").read_text().splitlines()]
    assert not any(item["event_type"] == "ModelResultRecorded" for item in events)


@pytest.mark.parametrize("entry", ["host", "composition", "author"])
def test_reviewer_v4_does_not_enable_an_author_v4(tmp_path, entry):
    with pytest.raises(ValueError, match="unsupported visible author tool version|unsupported atomic tool envelope version"):
        if entry == "author":
            _InboundCharacterAuthor(
                flash_model=object(), whole_candidate_mode=True,
                visible_source_review_model=_MeteredFixture(),
                visible_source_review_version="4", atomic_tool_envelope_version="4",
            )
        else:
            options = dict(
                settings=_settings(tmp_path), visible_source_review_required=True,
                visible_source_review_model=_MeteredFixture(),
                visible_source_review_version="4", visible_author_tool_version="4",
            )
            if entry == "host":
                build_qq_c2c_host(recipient_id="fixture", **options)
            else:
                build_semantic_chat_composition(model_id_prefix="fixture", **options)
    assert not (tmp_path / "world.sqlite").exists()
