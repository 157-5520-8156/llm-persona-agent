"""Source pins and exact coverage are mechanical; verdicts remain model-authored."""
import json
from pathlib import Path

import pytest

from companion_daemon.world_v2.story_semantic_probe import (
    semantic_probe_request, bind_semantic_probe, StorySemanticAssessment,
)
from test_story_memory_structure import story_archive


def packet():
    root = Path(__file__).parents[2]
    oracle = json.loads((root / 'fixtures/world_v2/interwoven_stories/semantic-oracle.json').read_text())
    sources = {s['source_id']: (root / s['path']).read_text() for s in oracle['sources']}
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
    doc = PrehistoryArchiveDocument.model_validate_json(story_archive().document.model_dump_json().replace(
        'actor:companion', oracle['source_owner']))
    return semantic_probe_request(oracle=oracle, sources=sources, documents=(doc,)), oracle, sources, doc


def test_oracle_is_pinned_to_unchanged_sources_and_exact_quote_locations():
    request, oracle, sources, doc = packet()
    assert len(request['cases']) == 18
    assert request['actor_readable_records'][0]['reader_actor_ref'] == oracle['source_owner']
    assert request['actor_readable_records'][0]['statement'] == doc.records[0].statement
    sources['a'] += ' 后来她们和解了。'
    with pytest.raises(ValueError, match='source hash'):
        semantic_probe_request(oracle=oracle, sources=sources, documents=(doc,))
    _, oracle, sources, doc = packet()
    oracle['cases'][0]['evidence'][0]['start_char'] += 1
    with pytest.raises(ValueError, match='exact source span'):
        semantic_probe_request(oracle=oracle, sources=sources, documents=(doc,))


def test_probe_never_approves_missing_cases_or_invented_evidence():
    request, *_ = packet()
    rows = [{'case_id': c['case_id'], 'verdict': 'unresolved', 'rationale': 'Fixture lacks comparable content.',
             'candidate_record_refs': [], 'candidate_quotes': []} for c in request['cases']]
    def assessment(values):
        return StorySemanticAssessment.model_validate_json(json.dumps({'judgments': values}))
    result = bind_semantic_probe(request, assessment(rows))
    assert result['all_cases_satisfied'] is False
    assert result['counts'] == {'satisfied': 0, 'violated': 0, 'unresolved': 18}
    with pytest.raises(ValueError, match='every selected case'):
        bind_semantic_probe(request, assessment(rows[:-1]))
    rows[0]['candidate_record_refs'] = ['prehistory-record:invented']
    with pytest.raises(ValueError, match='unknown candidate'):
        bind_semantic_probe(request, assessment(rows))
    rows[0]['candidate_record_refs'] = [request['documents'][0]['records'][0]['record_id']]
    rows[0]['candidate_quotes'] = ['原文中不存在的引句']
    with pytest.raises(ValueError, match='not in the cited'):
        bind_semantic_probe(request, assessment(rows))
