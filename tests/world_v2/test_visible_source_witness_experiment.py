"""Evidence-coordinate checks and the explicit limit of self-classified scopes."""

from copy import deepcopy
import hashlib
import json

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.world_v2.context_capsule import ResolvedSourceBinding, source_bindings_hash
from companion_daemon.world_v2.visible_source_closure_protocol import compact_source_reference_table
from companion_daemon.world_v2.visible_source_witness_experiment import (
    CONTRACT,
    prepare_witness_experiment,
)

TEXT = "我上午就坐在那一片，难怪那么安静"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sources():
    binding = ResolvedSourceBinding(
        source_kind="committed_event",
        authority_type="MessagePayloadStored",
        ref="event:said",
        source_world_revision=1,
        immutable_hash="a" * 64,
    )
    value = {
        "dialogue_id": "dialogue:said",
        "speaker": "companion",
        "speaker_ref": "agent:companion",
        "text": TEXT,
        "occurred_at": "2026-09-14T05:01:00Z",
        "delivery_state": "delivered",
        "sequence": 1,
        "privacy_class": "private",
        "source_claims": [
            {
                "authority_event_ref": "event:said",
                "authority_world_revision": 1,
                "authority_payload_hash": "a" * 64,
            }
        ],
    }
    item = {
        "item_ref": "dialogue:said",
        "privacy_class": "private",
        "source_hash": source_bindings_hash((binding,)),
        "value_hash": hashlib.sha256(_json(value).encode()).hexdigest(),
        "source_bindings": [binding.model_dump(mode="json")],
        "value": value,
    }
    entry = {
        "kind": "pinned_context_item",
        "lane": "recent_dialogue",
        "authority": "companion_expression_record",
        "actor_ref": "agent:companion",
        "availability": "available",
        "privacy_class": "private",
        "source_refs": ["event:said", "dialogue:said"],
        "item": item,
    }
    return compact_source_reference_table(
        {"subjects": {"companion_actor_ref": "agent:companion"}, "entries": [entry]}
    )


def _response(text="我之前说过上午坐在那里。", scope="utterance_record"):
    return {
        "contract": CONTRACT,
        "decisions": [
            {
                "beat_index": 0,
                "parts": [
                    {
                        "text": text,
                        "claim_scope": scope,
                        "subject_role": "companion",
                        "verdict": "closed",
                        "support_explanation": "记录证明说过这句话；不证明坐下行为。",
                        "witnesses": [
                            {
                                "source_ref_index": 0,
                                "pointer": "/item/value/text",
                                "quote": TEXT,
                                "use": "direct",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_speech_recollection_remains_available_with_no_release_authority():
    raw = _response()
    sources = _sources()
    before = _json(sources)
    prepared = prepare_witness_experiment(
        beats=(raw["decisions"][0]["parts"][0]["text"],), sources=sources
    )
    request = prepared.request()
    Draft202012Validator(request["tools"][0]["function"]["parameters"]).validate(raw)
    result = prepared.inspect_response(_json(raw))
    assert result["model_verdicts"] == ["closed"]
    assert result["receipt_authority"] is False and result["semantic_qualification"] == "unproven"
    packet = json.loads(request["messages"][1]["content"])
    assert len(packet["source_materials"]) == 1  # both refs share the exact body
    assert sum(len(table["rows"]) for table in packet["source_reference_tables"]) == 2
    assert _json(sources) == before


def test_declared_actual_occurrence_cannot_be_closed_by_a_prior_utterance():
    raw = _response(TEXT, "external_fact")
    prepared = prepare_witness_experiment(beats=(TEXT,), sources=_sources())
    with pytest.raises(ValueError, match="scope exceeds"):
        prepared.inspect_response(_json(raw))
    # Deliberately expose the remaining failure mode: the model can misclassify
    # the sentence itself. No structural check is advertised as semantic proof.
    raw["decisions"][0]["parts"][0]["claim_scope"] = "utterance_record"
    result = prepared.inspect_response(_json(raw))
    assert result["model_verdicts"] == ["closed"]
    assert result["semantic_qualification"] == "unproven"
    assert result["receipt_authority"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "quote",
        "index",
        "pointer",
        "metadata_pointer",
        "container_pointer",
        "context_only",
        "missing_witness",
        "omitted_text",
        "duplicate_part",
        "missing_beat",
        "free_with_sources",
    ],
)
def test_nonsemantic_forgery_and_incomplete_coverage_are_rejected(fault):
    raw = _response()
    text = raw["decisions"][0]["parts"][0]["text"]
    part = raw["decisions"][0]["parts"][0]
    witness = part["witnesses"][0]
    if fault == "quote":
        witness["quote"] = "没有出现在记录中的话"
    elif fault == "index":
        witness["source_ref_index"] = 99
    elif fault == "pointer":
        witness["pointer"] = "/item/value/missing"
    elif fault == "metadata_pointer":
        witness["pointer"] = "/authority"
    elif fault == "container_pointer":
        witness["pointer"] = "/item/value"
    elif fault == "context_only":
        witness["use"] = "context"
    elif fault == "missing_witness":
        part["witnesses"] = []
    elif fault == "omitted_text":
        part["text"] = text[:-1]
    elif fault == "duplicate_part":
        raw["decisions"][0]["parts"].append(deepcopy(part))
    elif fault == "missing_beat":
        raw["decisions"][0]["beat_index"] = 1
    else:
        part.update(claim_scope="source_free", verdict="source_free")
    prepared = prepare_witness_experiment(beats=(text,), sources=_sources())
    with pytest.raises(ValueError):
        prepared.inspect_response(_json(raw))


def test_private_clause_does_not_hide_an_unclosed_external_clause():
    raw = _response()
    part = raw["decisions"][0]["parts"][0]
    part.update(text="我有点担心，", claim_scope="source_free", verdict="source_free", witnesses=[])
    raw["decisions"][0]["parts"].append(
        {
            "text": "我上午坐在那里。",
            "claim_scope": "external_fact",
            "subject_role": "companion",
            "verdict": "unclosed",
            "witnesses": [],
            "support_explanation": "缺少行为依据。",
        }
    )
    prepared = prepare_witness_experiment(
        beats=("我有点担心，我上午坐在那里。",), sources=_sources()
    )
    assert prepared.inspect_response(_json(raw))["model_verdicts"] == ["unclosed"]
    duplicate = _json(raw).replace('"beat_index":0', '"beat_index":0,"beat_index":0')
    with pytest.raises(ValueError, match="duplicate"):
        prepared.inspect_response(duplicate)


@pytest.mark.parametrize("hidden", ["withhold", "unavailable"])
def test_unprojected_hidden_source_bodies_are_not_sent(hidden):
    rows = deepcopy(_sources())
    field = "privacy_class" if hidden == "withhold" else "availability"
    rows[0]["review_material"][field] = hidden
    with pytest.raises(ValueError, match="privacy-projected"):
        prepare_witness_experiment(beats=(TEXT,), sources=rows)


def test_a_proof_hash_removed_from_model_view_cannot_be_used_as_a_quote():
    raw = _response()
    witness = raw["decisions"][0]["parts"][0]["witnesses"][0]
    witness.update(pointer="/item/value/source_claims/0/authority_payload_hash", quote="a" * 64)
    text = raw["decisions"][0]["parts"][0]["text"]
    prepared = prepare_witness_experiment(beats=(text,), sources=_sources())
    with pytest.raises(ValueError, match="does not resolve"):
        prepared.inspect_response(_json(raw))


def test_a_direct_quote_does_not_allow_an_actor_swap():
    raw = _response()
    part = raw["decisions"][0]["parts"][0]
    prepared = prepare_witness_experiment(beats=(part["text"],), sources=_sources())
    part["subject_role"] = "counterpart"
    with pytest.raises(ValueError, match="subject differs"):
        prepared.inspect_response(_json(raw))


def test_boolean_is_not_an_original_integer_source_index():
    rows = deepcopy(_sources())
    rows[0]["source_ref_index"] = False
    with pytest.raises(ValueError, match="ordered original source table"):
        prepare_witness_experiment(beats=(TEXT,), sources=rows)


@pytest.mark.parametrize("source_index", [0, 1])
def test_full_packet_pointer_binds_the_references_shared_material(source_index):
    raw = _response()
    part = raw["decisions"][0]["parts"][0]
    part["witnesses"][0].update(
        source_ref_index=source_index, pointer="/source_materials/0/item/value/text"
    )
    prepared = prepare_witness_experiment(beats=(part["text"],), sources=_sources())
    result = prepared.inspect_response(_json(raw))
    assert result["model_verdicts"] == ["closed"]
    assert result["inspection_contract"] == "visible-source-witness-inspection.2"
    assert (
        result["readings"]["decisions"][0]["parts"][0]["witnesses"][0]["pointer"]
        == "/source_materials/0/item/value/text"
    )
    assert result["receipt_authority"] is False


@pytest.mark.parametrize("index", ["1", "00", "-1", "true", "", "０"])
def test_packet_pointer_cannot_select_another_or_ambiguous_material(index):
    raw = _response()
    part = raw["decisions"][0]["parts"][0]
    part["witnesses"][0].update(
        source_ref_index=1, pointer=f"/source_materials/{index}/item/value/text"
    )
    prepared = prepare_witness_experiment(beats=(part["text"],), sources=_sources())
    with pytest.raises(ValueError, match="selected material"):
        prepared.inspect_response(_json(raw))


@pytest.mark.parametrize("length", [512, 513, 1024, 1025])
def test_explanation_bound_remains_finite_without_changing_the_model_request(length):
    raw = _response()
    part = raw["decisions"][0]["parts"][0]
    prepared = prepare_witness_experiment(beats=(part["text"],), sources=_sources())
    part["support_explanation"] = "a" * length
    if length <= 1024:
        assert prepared.inspect_response(_json(raw))["structural_validation"] == "passed"
    else:
        with pytest.raises(ValueError):
            prepared.inspect_response(_json(raw))


def test_witness_request_declares_the_forced_tool_contract_without_changing_evidence():
    from companion_daemon.world_v2.visible_source_closure_protocol import (
        visible_source_closure_messages,
    )

    sources = _sources()
    legacy = visible_source_closure_messages(
        visible_beats=(TEXT,), world_claims=(), source_references=sources, version="8"
    )
    legacy_packet = json.loads(legacy[1]["content"])
    legacy_before = _json(legacy)
    prepared = prepare_witness_experiment(beats=(TEXT,), sources=sources)
    request = prepared.request()
    packet = json.loads(request["messages"][1]["content"])
    function = request["tools"][0]["function"]
    schema_contract = function["parameters"]["properties"]["contract"]
    assert packet["output_contract"]["contract"] in schema_contract["enum"]
    assert packet["output_contract"] == {
        "contract": CONTRACT,
        "authority": "experimental_no_receipt_or_action_authority",
    }
    assert request["tool_choice"]["function"]["name"] == function["name"]
    assert {k: v for k, v in packet.items() if k != "output_contract"} == {
        k: v for k, v in legacy_packet.items() if k != "output_contract"
    }
    assert _json(legacy) == legacy_before


def test_relative_pointer_transport_removes_the_second_material_selection():
    raw = _response()
    text = raw['decisions'][0]['parts'][0]['text']
    prepared = prepare_witness_experiment(
        beats=(text,), sources=_sources(), relative_pointer_choices=True,
    )
    request = prepared.request()
    schema = request['tools'][0]['function']['parameters']
    pointer = schema['properties']['decisions']['items']['properties']['parts']['items']['properties']['witnesses']['items']['properties']['pointer']
    assert '/item/value/text' in pointer['enum']
    assert not any(p.startswith('/source_materials/') for p in pointer['enum'])
    Draft202012Validator(schema).validate(raw)
    assert prepared.inspect_response(_json(raw))['model_verdicts'] == ['closed']
    raw['decisions'][0]['parts'][0]['witnesses'][0]['pointer'] = '/source_materials/0/item/value/text'
    assert list(Draft202012Validator(schema).iter_errors(raw))
    with pytest.raises(ValueError, match='relative pointer choice'):
        prepared.inspect_response(_json(raw))


def test_relative_pointer_mode_keeps_all_source_values_and_old_request_replayable():
    sources = _sources()
    old = prepare_witness_experiment(beats=(TEXT,), sources=sources)
    before = old.payload_json
    new = prepare_witness_experiment(beats=(TEXT,), sources=sources, relative_pointer_choices=True)
    a, b = (json.loads(p.payload_json) for p in (old, new))
    for key in ('sources', 'shown_materials', 'material_indexes', 'beats'):
        assert a[key] == b[key]
    assert old.payload_json == before
    assert a['request']['tool_choice'] != b['request']['tool_choice']
    assert '/item/value/source_claims/0/authority_payload_hash' not in b['relative_pointer_choices']


def test_pointer_choices_preserve_json_pointer_escaping_and_source_resolution():
    from companion_daemon.world_v2.visible_source_witness_experiment import _relative_pointer_choices, _reading
    material = {'item': {'value': {'a/b~c': ['quoted', True, None], 'empty': ''}}}
    paths = _relative_pointer_choices([material])
    assert '/item/value/a~1b~0c/0' in paths
    assert '/item/value/a~1b~0c/1' in paths
    assert '/item/value/empty' not in paths
    for path in paths:
        assert _reading(material, path)[0]
