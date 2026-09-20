"""Reuse only exact compilation bytes; never cache source judgments or authority."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import pytest

import companion_daemon.world_v2.visible_contextual_source_review as compiler
from companion_daemon.world_v2.visible_candidate_meaning import PreparedCandidateMeaning
from companion_daemon.world_v2.visible_independent_meanings import IndependentMeaning
from companion_daemon.world_v2.visible_independent_review_receipt import (
    IndependentVisibleReviewReceipt, record_independent_visible_review,
    verify_independent_visible_review_receipt,
)
from test_private_cognition_scope import _v20_args
from test_visible_contextual_source_review import _meaning, _response
from test_visible_independent_review_receipt import _expected
from test_visible_source_witness_experiment import _sources


@pytest.fixture(autouse=True)
def empty_cache():
    compiler._cached_contextual_preparation.cache_clear()
    yield
    compiler._cached_contextual_preparation.cache_clear()


def _prepare(*, meanings=None, sources=None, **options):
    meaning = _meaning()
    return compiler.prepare_contextual_source_review(
        meanings=(meaning, meaning) if meanings is None else meanings,
        sources=_sources() if sources is None else sources, **options,
    )


@pytest.mark.parametrize(('options', 'sha256'), [
    ({}, '1db0421f392700c1dc6477bcb48db01543b5eafc0362533649c36c2db48ca2c3'),
    ({'scoped_coverage': True}, '281f8c7859971187f28a7ed54c57a91b0d5fad0551fced9c68c42b29dcdf0b87'),
    ({'scoped_coverage': True, 'fact_value_authority': True}, '28803572eb6384eef3feb354c232a581ba4ba846ee0e6b171be0a22dd9773812'),
    ({'scoped_coverage': True, 'fact_value_authority': True, 'private_cognition_scope': True}, 'eeca9248d3496bf3839c4ee2be421f284f2dda2603f4cb235eeacc99e803040d'),
    ({'scoped_coverage': True, 'fact_value_authority': True, 'private_cognition_scope': True,
      'response_mode': 'json_object', 'scope_permission_context': True}, '723228357c8f4783fe0a499bd353793083d078679de6bee662c40add23f30e7d'),
])
def test_preexisting_compiler_bytes_are_identical(options, sha256):
    # Recorded on 20c4815d before this cache was implemented, from public fixtures.
    first = _prepare(**options)
    second = _prepare(**options)
    assert first.sha256 == second.sha256 == sha256
    assert compiler._cached_contextual_preparation.cache_info().misses == 1
    assert compiler._cached_contextual_preparation.cache_info().hits == 1


def test_only_wire_bytes_are_shared_and_caller_mutation_cannot_poison_cache():
    sources = _sources()
    before = deepcopy(sources)
    first = _prepare(sources=sources)
    request = first.request()
    request['messages'][0]['content'] = 'not the original compiler'
    pin = json.loads(first.payload_json)
    pin['sources'].clear()
    second = _prepare(sources=sources)
    assert sources == before
    assert first is not second
    assert first.payload_json is second.payload_json
    assert second.request()['messages'][0]['content'] != request['messages'][0]['content']
    with pytest.raises(FrozenInstanceError):
        second.payload_json = '{}'
    with pytest.raises(ValueError, match='original compilation'):
        compiler.PreparedContextualSourceReview(json.dumps(pin)).inspect_response(json.dumps(_response(first)))


@pytest.mark.parametrize('change', ['source_body', 'source_order', 'meaning_raw', 'meaning_preparation', 'option', 'compiler', 'contract', 'instruction'])
def test_every_original_input_and_compiler_contract_partitions_cache(monkeypatch, change):
    original = _prepare()
    if change == 'source_body':
        sources = deepcopy(_sources())
        # This ordinary display field is part of the exact source body even
        # when changing it confers no additional source permission.
        sources[0]['review_material']['cache_test_original_value'] = 'different source value'
        changed = _prepare(sources=sources)
    elif change == 'source_order':
        changed = _prepare(sources=tuple(
            {**source, 'source_ref_index': index}
            for index, source in enumerate(reversed(_sources()))
        ))
    elif change == 'meaning_raw':
        meaning = _meaning()
        alternate = IndependentMeaning(meaning.preparation, meaning.raw_response + '\n')
        changed = _prepare(meanings=(meaning, alternate))
    elif change == 'meaning_preparation':
        meaning = _meaning(text='如果你真让我什么都不想，我大概三秒就破功。')
        changed = _prepare(meanings=(meaning, meaning))
    elif change == 'option':
        changed = _prepare(scoped_coverage=True)
    elif change == 'compiler':
        monkeypatch.setattr(compiler, '_COMPILER_CACHE_VERSION', 'contextual-source-preparation-compiler.test')
        changed = _prepare()
    elif change == 'contract':
        monkeypatch.setattr(compiler, 'CONTRACT', 'visible-contextual-source-review.test')
        changed = _prepare()
    else:
        monkeypatch.setattr(compiler, 'INSTRUCTION', compiler.INSTRUCTION + '合同说明变化。')
        changed = _prepare()
    info = compiler._cached_contextual_preparation.cache_info()
    assert info.misses == 2 and info.hits == 0
    assert changed.payload_json != original.payload_json or change == 'compiler'


@pytest.mark.parametrize('option', [
    'scoped_coverage', 'fact_value_authority', 'private_cognition_scope', 'record_dependency_scope', 'lifecycle_scope', 'source_use_display',
    'scope_permission_context', 'scope_subjective_history',
])
def test_bool_and_integer_options_do_not_share_valid_cache_entry(option):
    _prepare()
    with pytest.raises((TypeError, ValueError)):
        _prepare(**{option: 0})
    assert compiler._cached_contextual_preparation.cache_info().hits == 0
    assert compiler._cached_contextual_preparation.cache_info().misses == 1


@pytest.mark.parametrize('fault', ['raw', 'preparation', 'one_reader', 'mixed_beats'])
def test_invalid_meanings_are_never_admitted_by_an_existing_cache_entry(fault):
    _prepare()
    meaning = _meaning()
    if fault == 'raw':
        invalid = (meaning, IndependentMeaning(meaning.preparation, '{invalid'))
    elif fault == 'preparation':
        packet = json.loads(meaning.preparation.payload_json)
        packet['request']['messages'][0]['content'] = 'tampered'
        invalid = (meaning, IndependentMeaning(PreparedCandidateMeaning(json.dumps(packet)), meaning.raw_response))
    elif fault == 'one_reader':
        invalid = (meaning,)
    else:
        invalid = (meaning, _meaning(text='不相同的原句。'))
    for _ in range(2):
        with pytest.raises((TypeError, ValueError)):
            _prepare(meanings=invalid)
    assert compiler._cached_contextual_preparation.cache_info().hits == 0
    assert compiler._cached_contextual_preparation.cache_info().currsize == 1


def test_cache_has_eight_entries_and_evicts_original_complete_key():
    first = _prepare()
    for index in range(8):
        meaning = _meaning()
        alternate = IndependentMeaning(meaning.preparation, meaning.raw_response + ' ' * (index + 1))
        _prepare(meanings=(meaning, alternate))
    assert compiler._cached_contextual_preparation.cache_info().currsize == 8
    again = _prepare()
    assert again == first
    assert compiler._cached_contextual_preparation.cache_info().misses == 10
    assert compiler._cached_contextual_preparation.cache_info().hits == 0


def test_reordered_source_indexes_cannot_reuse_valid_source_compilation():
    _prepare()
    for _ in range(2):
        with pytest.raises(ValueError, match='complete ordered original source table'):
            _prepare(sources=tuple(reversed(_sources())))
    assert compiler._cached_contextual_preparation.cache_info().hits == 0
    assert compiler._cached_contextual_preparation.cache_info().currsize == 1


def test_source_judgments_and_whole_beat_coverage_are_never_cached():
    prepared = _prepare()
    accepted = _response(prepared)
    assert prepared.inspect_response(json.dumps(accepted))['beat_outcomes'] == ['source_free']
    rejected = deepcopy(accepted)
    rejected['beat_decisions'][0]['unaccounted_assertions'] = ['没有记录的既往行为']
    assert prepared.inspect_response(json.dumps(rejected))['beat_outcomes'] == ['unclosed']
    unsupported = _response(prepared, status='asserted')
    assert prepared.inspect_response(json.dumps(unsupported))['beat_outcomes'] == ['unclosed']
    missing = deepcopy(accepted)
    missing['beat_decisions'] = []
    with pytest.raises(ValueError, match='every original Beat'):
        prepared.inspect_response(json.dumps(missing))
    assert compiler._cached_contextual_preparation.cache_info().misses == 1
    assert compiler._cached_contextual_preparation.cache_info().hits == 4


@pytest.mark.asyncio
async def test_cold_and_warm_receipts_revalidate_identically_and_keep_invocation_joins(tmp_path):
    args = await _v20_args(tmp_path)
    receipt = record_independent_visible_review(**args)
    assert compiler._cached_contextual_preparation.cache_info().hits > 0
    compiler._cached_contextual_preparation.cache_clear()
    restored = IndependentVisibleReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    assert compiler._cached_contextual_preparation.cache_info().misses == 1
    assert verify_independent_visible_review_receipt(receipt=restored, **_expected(args)) == receipt
    altered = receipt.model_copy(update={
        'source_review': receipt.source_review.model_copy(update={'request_hash': '0' * 64}),
    })
    with pytest.raises(ValueError, match='original stage'):
        verify_independent_visible_review_receipt(receipt=altered, **_expected(args))
