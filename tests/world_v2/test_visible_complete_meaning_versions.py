"""Coverage is an explicit model assertion, not inferred from missing fields."""
import json

import pytest

from companion_daemon.world_v2.visible_candidate_meaning import (
    PreparedCandidateMeaning, prepare_candidate_meaning, verify_candidate_meaning_preparation,
)


def preparation(version='8'):
    return prepare_candidate_meaning(beats=('刚修好啦。',), compact=True, explicit_questions=True,
        question_conditions=True, beat_conditions=True, require_complete_reading=True,
        closing_tail_transport=True, complete_reading_version=version)


def test_previous_compiler_bytes_stay_frozen_and_both_versions_recompile():
    assert preparation('7').sha256 == 'd2a4f78026839bfe797009d032e760b5598dfe13d14546ab676694c3fa966b97'
    for version in ('7', '8', '9', '10', '11', '12', '13'):
        prepared = preparation(version)
        assert verify_candidate_meaning_preparation(PreparedCandidateMeaning(prepared.payload_json))['contract'] == f'visible-candidate-meaning.{version}'
        body = json.loads(prepared.request()['messages'][1]['content'])
        assert set(body) == {'contract', 'visible_beats'}
    assert preparation('8').sha256 != preparation('7').sha256
    assert preparation('9').sha256 == '39698854903107bdb6971d60cf7e747496fff0eef7316ecbc5b1631d93b553e8'
    assert preparation('10').sha256 == '72f08d61a81501c58fc14d35edf4c26544aae6cf32fad5a58104230b07be7b46'
    assert preparation('11').sha256 == 'd2fdc90fac37469ddb3f731d90c5f43ed23d37d72af109a49bc320bc78dfd17d'


@pytest.mark.parametrize('fault', [None, 'missing_complete', 'missing_unresolved', 'empty_meaning', 'wrong_version'])
def test_semantic_coverage_still_requires_explicit_positive_bounded_response(fault):
    response = {'contract': 'visible-candidate-meaning.8', 'decisions': [{
        'beat_index': 0, 'reading_complete': True, 'unresolved_details': [],
        'meanings': [{'proposition': '未指明对象的修理已经完成。', 'mode': 'actual_event_or_state', 'subject_role': 'none'}],
        'questions': [], 'hypothetical_conditions': [],
    }]}
    if fault == 'missing_complete':
        del response['decisions'][0]['reading_complete']
    elif fault == 'missing_unresolved':
        del response['decisions'][0]['unresolved_details']
    elif fault == 'empty_meaning':
        response['decisions'][0]['meanings'] = []
    elif fault == 'wrong_version':
        response['contract'] = 'visible-candidate-meaning.7'
    raw = json.dumps(response, ensure_ascii=False)
    if fault:
        with pytest.raises(ValueError):
            preparation().inspect_response(raw)
    else:
        result = preparation().inspect_response(raw)
        assert len(result['facts']) == 1
        assert result['facts'][0]['mode'] == 'actual_event_or_state'
        assert result['receipt_authority'] is False
        assert result['semantic_qualification'] == 'unproven'


@pytest.mark.parametrize('version', ['14', 8, None, []])
def test_unknown_complete_wire_fails_before_provider(version):
    with pytest.raises(ValueError):
        preparation(version)


def test_auto_selection_is_pinned_and_does_not_change_reader_content_or_schema():
    forced = preparation('11')
    auto = prepare_candidate_meaning(beats=('刚修好啦。',), compact=True, explicit_questions=True,
        question_conditions=True, beat_conditions=True, require_complete_reading=True,
        closing_tail_transport=True, complete_reading_version='11', tool_selection_mode='auto')
    verify_candidate_meaning_preparation(auto)
    request = auto.request()
    assert request['tool_choice'] == 'auto'
    request['tool_choice'] = forced.request()['tool_choice']
    assert request == forced.request()
    assert auto.sha256 != forced.sha256
    # A pin claiming the old selection while carrying the new request cannot replay.
    packet = json.loads(auto.payload_json)
    del packet['tool_selection_mode']
    with pytest.raises(ValueError, match='fixed compiler'):
        verify_candidate_meaning_preparation(PreparedCandidateMeaning(json.dumps(packet)))


@pytest.mark.parametrize('version,mode', [('10', 'auto'), ('11', 'required'), ('11', None)])
def test_auto_selection_cannot_reinterpret_historical_readers(version, mode):
    with pytest.raises(ValueError):
        prepare_candidate_meaning(beats=('原句',), compact=True, explicit_questions=True,
            question_conditions=True, beat_conditions=True, require_complete_reading=True,
            complete_reading_version=version, tool_selection_mode=mode)
