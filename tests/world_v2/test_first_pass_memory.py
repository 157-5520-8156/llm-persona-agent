"""Current literal evidence must survive scene expansion of contextual cues."""
from datetime import UTC, datetime
import pytest

from companion_daemon.world_v2.character_prehistory import ReviewedPrehistoryArchive, digest
from companion_daemon.world_v2.recall_index import InMemoryRecallIndex
from test_character_prehistory import started_ledger
from test_prehistory_story_recall import _active_rows, _query
from test_prehistory_memory_source import _read, _corpus
from test_scene_recall import _two_lines


class _AmbiguousQuestionEmbedding:
    version = 'fixture-ambiguous-current-question.1'
    dimensions = 3

    def embed(self, texts):
        return tuple((1., 0., 0.) if text == '熟悉的手工往事' else
                     (0., 1., 0.) if text == '杭州' else
                     (.95, 0., .1) if '风筝' in text else
                     (.1, .35, .93) if '杭州' in text else
                     (0., .99, .1) if '另一间屋子' in text else
                     (0., 0., 1.) for text in texts)


@pytest.mark.parametrize('policy,present', [('17',False), ('18',True)])
def test_literal_hit_survives_context_scene_and_semantic_distractor(tmp_path, policy, present):
    original = _two_lines()
    extra = original.document.records[-1].model_copy(update={
        'record_id': 'prehistory-record:scene-distractor',
        'statement': '同伴坐下看了看另一间屋子。',
        'occurred_from': datetime(2020, 1, 8, tzinfo=UTC),
        'occurred_until': datetime(2020, 1, 8, tzinfo=UTC),
    })
    records = list(original.document.records)
    records[0] = records[0].model_copy(update={
        'related_record_refs': tuple(r.record_id for r in records[1:6])})
    document = original.document.model_copy(update={'records': (*records, extra)})
    archive = ReviewedPrehistoryArchive(document=document,
        review=original.review.model_copy(update={'manifest_hash': digest(document.manifest())}))
    ledger = started_ledger(tmp_path/'first-pass.sqlite')
    try:
        _active_rows(ledger, archive)
        query = _query(ledger).model_copy(update={
            'query_text': '熟悉的手工往事', 'lexical_text': '杭州', 'limit': 3})
        index = InMemoryRecallIndex(embedding=_AmbiguousQuestionEmbedding(),
            policy_version='world-v2-recall-index.hybrid.'+policy)
        documents = _corpus(ledger, _read(ledger))
        from companion_daemon.world_v2.recall_model_reading import interior_recall_item
        assert all(interior_recall_item(d,index_version='world-v2-recall-index.hybrid.18') ==
                   interior_recall_item(d,index_version='world-v2-recall-index.hybrid.17') for d in documents)
        index.rebuild(cursor=query.cursor, documents=documents)
        result = index.search(query)
        assert ('prehistory-record:scene-entry-6' in {h.document.source_item_ref for h in result.hits}) is present
        assert len(result.hits) <= 6
        assert all(h.document.source_bindings for h in result.hits)
    finally:
        ledger.close()


def test_old_speech_is_presented_as_speech_without_changing_its_words():
    from companion_daemon.world_v2.model_context_reading import present_dialogue_reports
    packet = {'inner_life_snapshot': {'materials': {
        'conversation': ['我（12 天前）：我按新表把自己那栏核了一遍。'],
        'recent_dialogue': [{'speaker':'companion','text':'我按新表把自己那栏核了一遍。'}],
    }}, 'personal_action_evidence': {'results': []}}
    view = present_dialogue_reports(packet)
    conversation = view['inner_life_snapshot']['materials']['conversation']
    assert conversation['authority'] == 'recorded_utterance_only'
    assert conversation['lines'] == packet['inner_life_snapshot']['materials']['conversation']
    assert view['inner_life_snapshot']['materials']['recent_dialogue'] == packet['inner_life_snapshot']['materials']['recent_dialogue']
    assert view['personal_action_evidence']['results'] == []
    assert isinstance(packet['inner_life_snapshot']['materials']['conversation'], list)
    assert present_dialogue_reports(view) == view
