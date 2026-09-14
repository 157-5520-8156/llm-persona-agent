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
    for version in ('7', '8', '9'):
        prepared = preparation(version)
        assert verify_candidate_meaning_preparation(PreparedCandidateMeaning(prepared.payload_json))['contract'] == f'visible-candidate-meaning.{version}'
        body = json.loads(prepared.request()['messages'][1]['content'])
        assert set(body) == {'contract', 'visible_beats'}
    assert preparation('8').sha256 != preparation('7').sha256


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


@pytest.mark.parametrize('version', ['10', 8, None, []])
def test_unknown_complete_wire_fails_before_provider(version):
    with pytest.raises(ValueError):
        preparation(version)
