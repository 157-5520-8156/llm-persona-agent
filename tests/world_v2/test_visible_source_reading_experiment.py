"""Reading IDs are exact source transport, never semantic qualification."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator, ValidationError as JsonSchemaValidationError
import pytest

from companion_daemon.world_v2.visible_source_reading_experiment import (
    CONTRACT, PreparedReadingExperiment, prepare_reading_experiment,
)
from test_visible_source_subject_authority import _report_sources
from test_visible_source_witness_experiment import _sources, _json, TEXT


def _reply(text, reading_id="r0", scope="utterance_record", role="companion"):
    return {"contract": CONTRACT, "decisions": [{"beat_index": 0, "parts": [{
        "text": text, "claim_scope": scope, "subject_role": role,
        "verdict": "closed", "reading_ids": [reading_id],
        "support_explanation": "The pinned reading supports only the recorded speech.",
    }]}]}


def test_one_reading_selects_exact_speech_and_preserves_full_material_and_proof():
    text = "我说过上午坐在那里。"
    prepared = prepare_reading_experiment(beats=(text,), sources=_sources())
    pin = json.loads(prepared.payload_json)
    base = json.loads(pin["witness_preparation_json"])
    assert len(pin["catalog"]) == 1
    assert pin["catalog"][0]["source_ref_indexes"] == [0, 1]
    assert pin["catalog"][0]["pointer"] == "/item/value/text"
    packet = json.loads(prepared.request()["messages"][1]["content"])
    assert [m["material"] for m in packet["source_materials"]] == base["shown_materials"]
    assert base["sources"] == list(_sources())
    result = prepared.inspect_response(_json(_reply(text)))
    selected = result["readings"]["decisions"][0]["parts"][0]["witnesses"][0]
    assert selected == {"source_ref_index": 0, "pointer": "/item/value/text", "quote": TEXT, "use": "direct"}
    assert result["receipt_authority"] is False
    assert result["semantic_qualification"] == "unproven"
    assert result["transport_readings"] == _reply(text)


@pytest.mark.parametrize("fault", ["unknown_id", "duplicate_id", "text", "omission", "extra_field", "scope", "subject"])
def test_transport_and_authority_faults_cannot_become_acceptance(fault):
    text = "我说过上午坐在那里。"
    prepared = prepare_reading_experiment(beats=(text,), sources=_sources())
    raw = _reply(text)
    part = raw["decisions"][0]["parts"][0]
    if fault == "unknown_id":
        part["reading_ids"] = ["r99"]
    elif fault == "duplicate_id":
        part["reading_ids"] *= 2
    elif fault == "text":
        part["text"] = text[:-1]
    elif fault == "omission":
        part["reading_ids"] = []
    elif fault == "extra_field":
        part["quote"] = TEXT
    elif fault == "scope":
        part["claim_scope"] = "external_fact"
    else:
        part["subject_role"] = "counterpart"
    with pytest.raises((ValueError, JsonSchemaValidationError)):
        prepared.inspect_response(_json(raw))


def test_baseline_alias_never_becomes_a_selectable_direct_reading():
    rows = list(deepcopy(_sources()))
    rows[0]["support_eligibility"] = "baseline_only"
    prepared = prepare_reading_experiment(beats=(TEXT,), sources=tuple(rows))
    pin = json.loads(prepared.payload_json)
    assert pin["catalog"][0]["source_ref_indexes"] == [1]
    assert json.loads(pin["witness_preparation_json"])["sources"] == rows


def test_catalog_substitution_is_rejected_even_with_a_schema_valid_selected_id():
    prepared = prepare_reading_experiment(beats=(TEXT,), sources=_sources())
    pin = json.loads(prepared.payload_json)
    pin["catalog"][0]["value"] = "a forged alternative reading"
    changed = PreparedReadingExperiment(_json(pin))
    with pytest.raises(ValueError, match="pinned compilation"):
        changed.inspect_response(_json(_reply(TEXT)))


@pytest.mark.parametrize("verdict", ["unclosed", "source_free"])
def test_negative_and_free_decisions_need_no_evidence_selection(verdict):
    prepared = prepare_reading_experiment(beats=(TEXT,), sources=_sources())
    raw = _reply(TEXT)
    part = raw["decisions"][0]["parts"][0]
    part.update(verdict=verdict, claim_scope="source_free" if verdict == "source_free" else "external_fact")
    part["reading_ids"] = []
    Draft202012Validator(prepared.request()["tools"][0]["function"]["parameters"]).validate(raw)
    assert prepared.inspect_response(_json(raw))["model_verdicts"] == [verdict]
    part["reading_ids"] = ["r0"]
    with pytest.raises(ValueError, match="tool schema"):
        prepared.inspect_response(_json(raw))
    # A false source-free classification still passes structure; this is why
    # this experiment has no production or receipt authority.


def test_counterpart_report_keeps_speaker_and_third_party_permissions_separate():
    text = "家里人来接你啦。"
    prepared = prepare_reading_experiment(beats=(text,), sources=_report_sources())
    result = prepared.inspect_response(_json(_reply(text, scope="report_uptake", role="other")))
    assert result["model_verdicts"] == ["closed"]
    assert result["transport_readings"]["decisions"][0]["parts"][0]["claim_scope"] == "report_uptake"
    for scope, role in [("report_uptake", "companion"), ("external_fact", "other")]:
        with pytest.raises(ValueError, match="tool schema"):
            prepared.inspect_response(_json(_reply(text, scope=scope, role=role)))


@pytest.mark.asyncio
@pytest.mark.parametrize("content_fields_only", [False, True])
async def test_native_environment_reading_cannot_grant_personal_presence(tmp_path, monkeypatch, content_fields_only):
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
    from test_character_life_experience_runtime import _accepted_response
    from test_current_activity_context import current_context
    from test_visible_source_composer import _request
    from test_world_stimulus_life_intent import ACTOR, WORLD

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "native.sqlite"
    await _accepted_response(path, "雨停了，我松了一口气。")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    try:
        store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
        capsule, _, _ = current_context(ledger, store, actor_ref=ACTOR)
        rows = compile_visible_source_table(request=_request(capsule), capsule=capsule).source_references()
        prepared = prepare_reading_experiment(beats=("雨停了。",), sources=rows, content_fields_only=content_fields_only)
        pin = json.loads(prepared.payload_json)
        if content_fields_only:
            old = prepare_reading_experiment(beats=("雨停了。",), sources=rows)
            old_pin = json.loads(old.payload_json)
            assert pin["witness_preparation_json"] == old_pin["witness_preparation_json"]
            old_readings = [r for r in old_pin["catalog"] if "/world_consequence/" in r["pointer"]]
            new_readings = [r for r in pin["catalog"] if "/world_consequence/" in r["pointer"]]
            assert len(old_readings) > len(new_readings)
            assert {r["pointer"] for r in new_readings} == {"/item/value/content/world_consequence/environment/text"}
            assert prepare_reading_experiment(beats=("雨停了。",), sources=rows).payload_json == old.payload_json
            # Restoring a previously selectable metadata scalar cannot create a
            # passing new receipt, even when its reading ID remains schema-valid.
            changed = deepcopy(pin)
            metadata = next(r for r in old_readings if not r["pointer"].endswith("/text"))
            changed["catalog"][0] = {**metadata, "reading_id": pin["catalog"][0]["reading_id"]}
            answer = {**_reply("雨停了。", new_readings[0]["reading_id"], "environment", "none"), "contract": pin["contract"]}
            with pytest.raises(ValueError, match="pinned compilation"):
                PreparedReadingExperiment(_json(changed)).inspect_response(_json(answer))
        reading = next(r for r in pin["catalog"] if r["pointer"] == "/item/value/content/world_consequence/environment/text")
        raw = _reply("雨停了。", reading["reading_id"], "environment", "none")
        raw["contract"] = pin["contract"]
        assert prepared.inspect_response(_json(raw))["model_verdicts"] == ["closed"]
        raw["decisions"][0]["parts"][0]["subject_role"] = "companion"
        with pytest.raises(ValueError, match="tool schema"):
            prepared.inspect_response(_json(raw))
    finally:
        ledger.close()
