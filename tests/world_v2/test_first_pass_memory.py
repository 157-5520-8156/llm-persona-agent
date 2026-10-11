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
    from companion_daemon.world_v2.model_context_reading import present_context_evidence
    packet = {'inner_life_snapshot': {'materials': {
        'conversation': ['我（12 天前）：我按新表把自己那栏核了一遍。'],
        'recent_dialogue': [{'speaker':'companion','text':'我按新表把自己那栏核了一遍。'}],
    }}, 'personal_action_evidence': {'results': []}}
    view = present_context_evidence(packet)
    conversation = view['inner_life_snapshot']['materials']['conversation']
    assert conversation['authority'] == 'recorded_utterance_only'
    assert conversation['lines'] == packet['inner_life_snapshot']['materials']['conversation']
    assert view['inner_life_snapshot']['materials']['recent_dialogue'] == packet['inner_life_snapshot']['materials']['recent_dialogue']
    assert view['personal_action_evidence']['results'] == []
    assert isinstance(packet['inner_life_snapshot']['materials']['conversation'], list)
    assert present_context_evidence(view) == view


def test_literal_entry_in_same_scene_survives_sibling_expansion(tmp_path):
    from test_scene_recall import _native_scene_records

    class SameSceneEmbedding:
        version = 'fixture-same-scene.1'
        dimensions = 3

        def embed(self, texts):
            return tuple((0., 1., 0.) if text == '杭州新家桌子笔插' else
                         (0., .35, .93) if '杭州' in text else (1., 0., 0.) for text in texts)

    def authored(raw):
        scene, block = raw['scenes'][0], raw['scenes'][0]['blocks'][0]
        raw['scenes'] = [{**scene, 'blocks': [
            {**block, 'block_id': f'p{i}', 'text':
                '杭州的新书桌上放着旧笔插。' if i == 7 else f'她修补风筝第{i}处边角。'}
            for i in range(8)]}]
        raw['links'] = []
        return raw

    ledger = started_ledger(tmp_path/'same-scene.sqlite')
    try:
        active = _native_scene_records(ledger, transform=authored)
        query = _query(ledger).model_copy(update={
            'query_text': '熟悉的手工往事', 'lexical_text': '杭州新家桌子笔插', 'limit': 3,
            'link_refs': (active['prehistory-record:fixture-native.p0'].candidate_id,)})
        index = InMemoryRecallIndex(embedding=SameSceneEmbedding(),
            policy_version='world-v2-recall-index.hybrid.18')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        assert {'prehistory-record:fixture-native.p0', 'prehistory-record:fixture-native.p7'} <= {
            h.document.source_item_ref for h in result.hits}
        assert len(result.hits) <= 6
    finally:
        ledger.close()


def test_literal_window_replaces_context_window_of_same_record(tmp_path):
    from test_scene_recall import _native_scene_records

    class WindowEmbedding:
        version = 'fixture-current-literal-window.1'
        dimensions = 3

        def embed(self, texts):
            return tuple((0., 1., 0.) if text == '杭州新家桌子笔插' else
                         (0., .35, .93) if '杭州' in text else (1., 0., 0.) for text in texts)

    def authored(raw):
        scene, block = raw['scenes'][0], raw['scenes'][0]['blocks'][0]
        raw['scenes'] = [{**scene, 'blocks': [{**block, 'block_id': 'long',
            'text': '背景开头。' + '普通细节。' * 210 + '杭州的新书桌上放着旧笔插。'}]}]
        raw['links'] = []
        return raw

    ledger = started_ledger(tmp_path/'same-record.sqlite')
    try:
        active = _native_scene_records(ledger, transform=authored)
        query = _query(ledger).model_copy(update={
            'query_text': '熟悉的手工往事', 'lexical_text': '杭州新家桌子笔插', 'limit': 3,
            'link_refs': (active['prehistory-record:fixture-native.long'].candidate_id,)})
        index = InMemoryRecallIndex(embedding=WindowEmbedding(),
            policy_version='world-v2-recall-index.hybrid.18')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        assert len(result.hits) == 1
        assert '杭州' in result.hits[0].document.text
        assert result.hits[0].document.source_window_start > 0
        assert result.hits[0].document.source_bindings
    finally:
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('version', ['1', '2', '3', 'slim'])
async def test_encoded_author_wire_preserves_dialogue_authority(version):
    import json
    from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
    from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
    from test_character_interior_inbound_author import _request
    from test_inbound_correction_prefix import _CaptureCompiler, _CapturedRequest

    provider = _CaptureCompiler()
    author = _InboundCharacterAuthor(flash_model=provider, atomic_tool_envelope_version=version,
        whole_candidate_mode=True, visible_source_review_model=provider)
    request = _request(revision=3, call='call:encoded-speech')
    packet = json.loads(request.model_content_json)
    lines = ['我（12 天前）：我按新表核了自己的班次。' * 4] * 2
    packet['inner_life_snapshot'] = {'materials': {'conversation': lines,
        'recent_dialogue': [{'speaker': 'companion', 'text': lines[0]}]}}
    request = request.model_copy(update={'model_content_json': json.dumps(packet, ensure_ascii=False)})
    with pytest.raises(_CapturedRequest):
        await author._propose_appraisal(request)
    displayed = json.loads(provider.parameters['messages'][1]['content'])
    materials = displayed['inner_life_snapshot']['materials']
    assert materials['contract'] == 'shared-string-view.1'
    decoded = unpack_shared_strings(materials)
    assert decoded['recent_dialogue'] == packet['inner_life_snapshot']['materials']['recent_dialogue']
    if version in {'1', '2'}:
        assert decoded['conversation'] == lines
    else:
        assert decoded['conversation']['authority'] == 'recorded_utterance_only'
        assert decoded['conversation']['lines'] == lines
