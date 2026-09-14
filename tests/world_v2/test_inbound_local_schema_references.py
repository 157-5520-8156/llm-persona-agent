"""Equivalent wire grammar and explicit wiring; no provider calls or live state."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.llm import FakeCompanionModel
from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts
from companion_daemon.world_v2.character_interior.local_schema_references import (
    expand_local_schema_references, factor_local_schema_references,
)
from companion_daemon.world_v2.expression_draft import qq_expression_capabilities
from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
from test_inbound_atomic_result_v3 import _branch_value
from test_visible_source_review_version_wiring import _MeteredFixture, _settings


def _contract(*, enabled=False, phase="initial", allowed=True, version="3", dialect="deepseek-strict"):
    return InboundToolContracts().contract_for(
        phase=phase, capabilities=qq_expression_capabilities("napcat"),
        recall_allowed=allowed, atomic_envelope_version=version,
        schema_dialect=dialect, use_schema_references=enabled,
    )


@pytest.mark.parametrize("phase,allowed", [("initial", True), ("initial", False), ("after_recall", False), ("final", False)])
def test_all_phase_grammars_expand_exactly_and_keep_decoder(phase, allowed):
    inline = _contract(phase=phase, allowed=allowed)
    compact = _contract(phase=phase, allowed=allowed, enabled=True)
    old = inline.provider_tools[0]["function"]["parameters"]
    new = compact.provider_tools[0]["function"]["parameters"]
    assert expand_local_schema_references(new) == old

    def check_union_branches(node):
        if isinstance(node, dict):
            for branch in node.get("anyOf", []):
                assert "type" in branch
                assert "$ref" not in branch
            for child in node.values():
                check_union_branches(child)
        elif isinstance(node, list):
            for child in node:
                check_union_branches(child)

    check_union_branches(new)
    assert len(json.dumps(new)) < len(json.dumps(old)) * .6
    assert compact.identity.schema_sha256 != inline.identity.schema_sha256
    assert compact.identity.contract_sha256 != inline.identity.contract_sha256
    assert compact.identity.capabilities_sha256 == inline.identity.capabilities_sha256
    assert compact.result_branch_fields() == inline.result_branch_fields()
    assert compact.provider_tool_choice == inline.provider_tool_choice
    before, after = Draft202012Validator(old), Draft202012Validator(new)
    for kind in ("decision", "recall"):
        value = {"result": _branch_value(kind)}
        assert before.is_valid(value) == after.is_valid(value) == (kind == "decision" or allowed)
        if kind == "decision" or allowed:
            raw = json.dumps(value)
            assert inline.unwrap(raw) == compact.unwrap(raw)
        # Required fields and foreign branch members remain rejected.
        for key in list(value["result"]):
            bad = deepcopy(value)
            del bad["result"][key]
            assert not before.is_valid(bad) and not after.is_valid(bad)
        bad = deepcopy(value)
        bad["result"]["foreign_branch"] = None
        assert not before.is_valid(bad) and not after.is_valid(bad)
        with pytest.raises(ValueError):
            compact.unwrap(json.dumps(bad))
    value = {"result": _branch_value("decision")}
    for timing in ("now", "later", "silent"):
        value["result"]["expression_draft"]["timing_choice"] = timing
        assert before.is_valid(value) == after.is_valid(value)
    value["result"]["appraisal_draft"]["confidence"] = "wrong type"
    assert not before.is_valid(value) and not after.is_valid(value)


def test_literals_are_never_treated_as_schemas_or_mutated():
    literal = {"$ref": "https://literal.example/", "$defs": {"x": {"type": "number"}}}
    repeated = {"type": "string", "description": "long description " * 30, "default": literal, "examples": [literal]}
    schema = {"type": "object", "properties": {"a": repeated, "b": repeated}, "enum": [literal]}
    frozen = json.dumps(schema)
    compact = factor_local_schema_references(schema)
    assert "$def" in compact
    assert json.dumps(schema) == frozen
    assert expand_local_schema_references(compact) == schema
    assert compact["enum"] == [literal]
    plain = {"type": "string"}
    assert factor_local_schema_references(plain) == plain
    assert factor_local_schema_references(plain) is not plain


@pytest.mark.parametrize("schema", [
    {"$ref": "https://remote.example/schema"}, {"$ref": "#/$def/missing"},
    {"$def": {"a": {"$ref": "#/$def/a"}}, "properties": {"x": {"$ref": "#/$def/a"}}},
    {"$def": {"a": {"type": "string"}}, "$ref": "#/$def/a", "type": "number"},
])
def test_external_unresolved_cyclic_or_sibling_references_fail_closed(schema):
    with pytest.raises(ValueError):
        expand_local_schema_references(schema)
    with pytest.raises(ValueError):
        factor_local_schema_references(schema)


@pytest.mark.parametrize("version,dialect", [("1", "standard"), ("1", "deepseek-strict"), ("2", "deepseek-strict"), ("3", "standard")])
def test_incompatible_contracts_cannot_enable_references(version, dialect):
    with pytest.raises(ValueError):
        _contract(enabled=True, version=version, dialect=dialect)


@pytest.mark.parametrize("flag", ["true", 1, None])
def test_schema_option_requires_boolean(flag):
    with pytest.raises(TypeError):
        _contract(enabled=flag)


@pytest.mark.parametrize("enabled", [False, True])
def test_host_passes_explicit_option_through_composition(tmp_path, monkeypatch, enabled):
    import companion_daemon.world_v2.semantic_chat_composition as composition
    from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor

    class Captured(Exception):
        pass

    def capture(**kwargs):
        assert kwargs["use_schema_references"] is enabled
        author = _InboundCharacterAuthor(**kwargs)
        assert author._use_schema_references is enabled
        raise Captured

    monkeypatch.setattr(composition, "compose_production_character_interior", capture)
    with pytest.raises(Captured):
        build_qq_c2c_host(
            settings=_settings(tmp_path), recipient_id="fixture", model=FakeCompanionModel(),
            visible_source_review_required=True, visible_source_review_model=_MeteredFixture(),
            visible_author_tool_version="3", visible_author_schema_references=enabled,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_cli_records_option_and_passes_to_host_without_network(tmp_path, monkeypatch, enabled):
    import httpx
    import companion_daemon.world_v2.longitudinal_journey as runner
    import companion_daemon.world_v2.qq_c2c_host as host_module
    from test_longitudinal_cli import _cli

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "offline-fixture-key")
    monkeypatch.setenv("DEEPSEEK_CHARACTER_THINKING_ENABLED", "false")

    def reject(_request):
        raise AssertionError("configuration test must not send requests")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(reject))
    observed = []

    def host_capture(**kwargs):
        observed.append(kwargs["visible_author_schema_references"])
        return object()

    async def capture_run(**kwargs):
        assert kwargs["provenance"].get("visible_author_schema_references", False) is enabled
        kwargs["output"].mkdir()
        clock = runner.JourneyClock(kwargs["journey"].started_at)
        try:
            kwargs["host_factory"](kwargs["output"] / "world.sqlite", clock, runner.CaptureDelivery(clock))
        finally:
            await kwargs["close_resources"]()
        return {"completed": True}

    monkeypatch.setattr(host_module, "build_qq_c2c_host", host_capture)
    monkeypatch.setattr(runner, "run_journey", capture_run)
    cli = _cli()
    await cli.run(cli.parse_options([
        "--output", str(tmp_path / "run"), "--model-mode", "real-provider",
        "--allow-real-provider", "--require-visible-source-review",
        "--visible-author-tool-version", "3",
        *(["--visible-author-schema-references"] if enabled else []),
    ]))
    assert observed == [enabled]


def test_cli_rejects_incompatible_option_before_creating_output(tmp_path):
    from test_longitudinal_cli import _cli
    output = tmp_path / "run"
    with pytest.raises(SystemExit):
        _cli().parse_options(["--output", str(output), "--visible-author-schema-references"])
    assert not output.exists()


def test_redundant_reference_type_requires_exact_definition_type():
    schema = {"$def": {"x": {"type": "string", "enum": ["yes"]}}, "anyOf": [{"$ref": "#/$def/x", "type": "string"}, {"type": "null"}]}
    assert expand_local_schema_references(schema) == {"anyOf": [{"type": "string", "enum": ["yes"]}, {"type": "null"}]}
    schema["anyOf"][0]["type"] = "object"
    with pytest.raises(ValueError, match="must match"):
        expand_local_schema_references(schema)
