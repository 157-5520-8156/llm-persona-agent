import json

import pytest

from companion_daemon.world_v2.grounded_review_wire import decode
from companion_daemon.world_v2.visible_grounded_review import (
    PROTOCOL_V2, GroundedReviewWireFailure, prepare_grounded_review,
    record_grounded_review_receipt, GroundedVisibleReviewReceipt,
)
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from test_visible_grounded_review import _record_args
from test_visible_selected_source_context import _sources
from test_visible_source_review_receipt import _candidate


def response(parts):
    return {"beat_decisions": [{"beat_index": 0, "review_complete": True,
        "segments": [{"kind": "non_record", "text": text, "rationale": "本轮回应"} for text in parts],
        "unresolved_details": []}]}


@pytest.mark.parametrize("original,parts", [
    ("知道了 明天你忙你的", ["知道了", "明天你忙你的"]),
    ("嗯\n\t你先忙。 ", ["嗯", "你先忙。"]),
])
def test_only_source_whitespace_at_segment_boundaries_is_bound_losslessly(original, parts):
    decoded = decode(json.dumps(response(parts)), beats=[{"beat_index": 0, "text": original}])
    assert "".join(x["text"] for x in decoded["beat_decisions"][0]["non_record_expressions"]) == original


@pytest.mark.parametrize("parts", [["知道了", "你忙"], ["知道了", "知道了 你忙。"], ["你忙。", "知道了"], ["知道了"]])
def test_omitted_punctuation_words_overlap_or_reordering_cannot_be_repaired(parts):
    with pytest.raises(ValueError):
        decode(json.dumps(response(parts)), beats=[{"beat_index": 0, "text": "知道了 你忙。"}])


@pytest.mark.asyncio
async def test_native_receipt_replays_and_does_not_upgrade_a_historical_request(tmp_path):
    async with _sources(tmp_path) as case:
        candidate = _candidate(case, texts=("嗯 你先忙。",))
        table = compile_visible_source_table(request=case.request, capsule=case.capsule)
        old = prepare_grounded_review(candidate=candidate, source_table=table,
                                      source_ref_aliases={}, protocol=PROTOCOL_V2)
        new = prepare_grounded_review(candidate=candidate, source_table=table,
                                      source_ref_aliases={}, protocol=PROTOCOL_V2, ordered_native=True)
    value = response(["嗯", "你先忙。"])
    with pytest.raises(GroundedReviewWireFailure):
        old.inspect_response(json.dumps(value))
    receipt = record_grounded_review_receipt(**_record_args(new, value))
    assert receipt.beat_outcomes == ("source_free",)
    assert GroundedVisibleReviewReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    assert "wire_carrier" not in old.as_dict()
    assert old.request_hash != new.request_hash


@pytest.mark.asyncio
async def test_compilation_cache_never_reuses_a_verdict_or_mutable_evidence(tmp_path):
    from companion_daemon.world_v2.visible_grounded_review import _compiled_grounded_bytes, _verified_prepared_bytes
    async with _sources(tmp_path) as case:
        table = compile_visible_source_table(request=case.request, capsule=case.capsule)
        candidate = _candidate(case, texts=('嗯 你先忙。',))
        original = prepare_grounded_review(candidate=candidate, source_table=table,
            source_ref_aliases={}, protocol=PROTOCOL_V2, ordered_native=True, faithful_proposition=True)
        raw = response(['嗯', '你先忙。'])
        receipt = record_grounded_review_receipt(**_record_args(original, raw))
        mutable = original.as_dict()
        mutable['catalog'].clear()
        again = prepare_grounded_review(candidate=candidate, source_table=table,
            source_ref_aliases={}, protocol=PROTOCOL_V2, ordered_native=True, faithful_proposition=True)
        assert again.payload_json == original.payload_json
        changed = prepare_grounded_review(candidate=_candidate(case, texts=('另一个原句。',)), source_table=table,
            source_ref_aliases={}, protocol=PROTOCOL_V2, ordered_native=True, faithful_proposition=True)
        with pytest.raises(GroundedReviewWireFailure):
            changed.inspect_response(json.dumps(raw))
        uncertain = response(['嗯 你先忙。'])
        uncertain['beat_decisions'][0]['review_complete'] = False
        assert original.inspect_response(json.dumps(uncertain))['inconclusive']
        _compiled_grounded_bytes.cache_clear()
        _verified_prepared_bytes.cache_clear()
        assert GroundedVisibleReviewReceipt.model_validate_json(receipt.model_dump_json()) == receipt


def test_subjective_proof_elision_preserves_prose_and_readable_fields():
    from companion_daemon.world_v2.grounded_material_presentation import present_material_cards
    cards = [{'material': {'authority': 'accepted_subjective_history_not_external_fact',
        'item': {'value': {'meaning': '我还是有点不高兴。', 'entity_revision': 4,
                          'evidence_refs': [{'immutable_hash': 'opaque-proof'}]}}},
        'readings': [{'field': '/item/value/meaning', 'reading_id': 'one'}]}]
    shown = present_material_cards(cards)
    assert shown[0]['material']['item']['value'] == {'meaning': '我还是有点不高兴。'}
    assert cards[0]['material']['item']['value']['entity_revision'] == 4
    cards[0]['readings'].append({'field': '/item/value/entity_revision', 'reading_id': 'revision'})
    assert present_material_cards(cards) == cards


@pytest.mark.asyncio
async def test_elided_material_receipt_keeps_original_authority_and_cold_replay(tmp_path):
    from companion_daemon.world_v2.visible_grounded_review import (
        _compiled_grounded_bytes, _verified_prepared_bytes, _verify_bound_response,
    )
    async with _sources(tmp_path) as case:
        table = compile_visible_source_table(request=case.request, capsule=case.capsule)
        kw = dict(candidate=_candidate(case, texts=('嗯 你先忙。',)), source_table=table,
                  source_ref_aliases={}, protocol=PROTOCOL_V2, ordered_native=True, reference_wire=True)
        old = prepare_grounded_review(**kw)
        new = prepare_grounded_review(**kw, material_presentation='subjective-proof-elision.1')
        assert old.as_dict()['catalog'] == new.as_dict()['catalog']
        assert old.as_dict()['source_table_json'] == new.as_dict()['source_table_json']
        receipt = record_grounded_review_receipt(**_record_args(new, response(['嗯 你先忙。'])))
        _compiled_grounded_bytes.cache_clear()
        _verified_prepared_bytes.cache_clear()
        _verify_bound_response.cache_clear()
        assert GroundedVisibleReviewReceipt.model_validate_json(receipt.model_dump_json()) == receipt


@pytest.mark.asyncio
async def test_redundant_fact_id_keeps_exact_value_owner_and_scope_checks(tmp_path):
    from copy import deepcopy
    from test_visible_grounded_review import _prepare
    from companion_daemon.world_v2.proposal_envelope import DecisionProposal
    from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
    from companion_daemon.world_v2.visible_grounded_review import GroundedVisibleReviewRejected
    old = await _prepare(tmp_path, texts=('你保留了周四的约定。',), retained_value='保留周四的约定')
    pin = old.as_dict()
    prepared = prepare_grounded_review(
        candidate=DecisionProposal.model_validate_json(pin['candidate_json']),
        source_table=VisibleSourceTable(pin['source_table_json']), source_ref_aliases=pin['source_ref_aliases'],
        protocol=PROTOCOL_V2, ordered_native=True,
    )
    reading=next(r for r in prepared.as_dict()['catalog'] if r.get('source_family')=='accepted_fact_value')
    selection={'reading_id':reading['reading_id'],'quoted_value':'保留周四的约定','subject_ref':reading['source_owner_ref']}
    fact={'kind':'fact','text':'你保留了周四的约定。','proposition':'用户保留周四约定',
          'claim_scope':'accepted_fact','subject_role':'counterpart','source_support':True,
          'rationale':'所选精确事实值','reading_ids':[reading['reading_id']], 'fact_value_selections':[selection]}
    raw={'beat_decisions':[{'beat_index':0,'review_complete':True,'segments':[fact]}]}
    receipt=record_grounded_review_receipt(**_record_args(prepared,raw))
    assert receipt.beat_outcomes==('closed',)
    for field,bad in [('quoted_value','不存在的原文'),('subject_ref','actor:other')]:
        forged=deepcopy(raw)
        forged['beat_decisions'][0]['segments'][0]['fact_value_selections'][0][field]=bad
        with pytest.raises(GroundedVisibleReviewRejected):
            record_grounded_review_receipt(**_record_args(prepared,forged))
    duplicate=deepcopy(raw)
    duplicate['beat_decisions'][0]['segments'][0]['fact_value_selections'].append(selection)
    with pytest.raises(GroundedReviewWireFailure):
        prepared.inspect_response(json.dumps(duplicate))
    plain=deepcopy(raw)
    plain['beat_decisions'][0]['segments'][0]['fact_value_selections']=[]
    with pytest.raises(GroundedVisibleReviewRejected):
        record_grounded_review_receipt(**_record_args(prepared,plain))


def test_review_alias_decoding_preserves_prose_and_quotes_but_rejects_unknown_ids():
    from companion_daemon.world_v2.grounded_review_wire import expand_response_references
    from companion_daemon.world_v2.reference_wire import prepare_reference_view
    original='event:'+'a'*64
    view, bindings=prepare_reference_view({'source_ref':original, 'source_refs':[original]*20})
    alias=view['source_ref']
    raw={'rationale':'依据 '+alias+'，不是新事实', 'proposition':'其中'+alias+'只是文字',
         'quoted_value':alias, 'text':alias, 'reading_ids':['r1'],
         'fact_value_selections':[{'reading_id':'r1','subject_ref':alias,'quoted_value':alias}]}
    restored=expand_response_references(raw,bindings)
    for key in ('rationale','proposition','quoted_value','text'):
        assert restored[key]==raw[key]
    assert restored['fact_value_selections'][0]['subject_ref']==original
    assert restored['fact_value_selections'][0]['quoted_value']==alias
    with pytest.raises(ValueError,match='unresolved'):
        expand_response_references({'subject_ref':bindings['prefix']+'9999'},bindings)
