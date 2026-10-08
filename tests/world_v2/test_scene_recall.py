"""Scene retrieval through retained Memory and source-closed public Recall."""
from datetime import datetime, UTC
import json

import pytest

from companion_daemon.world_v2.character_prehistory import ReviewedPrehistoryArchive, digest
from companion_daemon.world_v2.recall_index import InMemoryRecallIndex
from test_character_prehistory import started_ledger, reviewed_archive
from test_prehistory_story_recall import _active_rows, _query
from test_prehistory_memory_source import _read, _corpus


class _TwoTopicEmbedding:
    """External vector fixture: two distinct direct matches, other graph context."""
    version = 'fixture-two-story-entry.1'
    dimensions = 3

    def embed(self, texts):
        return tuple((1.0, 0.0, 0.0) if '风筝' in text else
                     (0.9, 0.4, 0.0) if '杭州' in text else (0.0, 0.0, 1.0)
                     for text in texts)


def _two_lines():
    original = reviewed_archive()
    sample = original.document.records[0]
    texts = ['她自己做了一只青色风筝。', '她拿纸补好了边角。', '后来她和同伴重新量了一根竹条。',
             '她收起没用完的胶水。', '她把多余的纸分给同伴。', '同伴把剪刀还给她。',
             '她搬到杭州后把一个旧笔插放在新书桌上。']
    refs = [f'prehistory-record:scene-entry-{i}' for i in range(len(texts))]
    records = []
    for i, text in enumerate(texts):
        neighbours = tuple(refs[j] for j in (i-1, i+1) if 0 <= j < 6) if i < 6 else ()
        at = datetime(2020, 1, i + 1, tzinfo=UTC)
        records.append(sample.model_copy(update={'record_id': refs[i], 'statement': text,
            'occurred_from': at, 'occurred_until': at, 'time_precision': 'day',
            'related_record_refs': neighbours}))
    doc = original.document.model_copy(update={'records': tuple(records)})
    return ReviewedPrehistoryArchive(document=doc,
        review=original.review.model_copy(update={'manifest_hash': digest(doc.manifest())}))


def test_story_expansion_keeps_a_second_direct_question_entry(tmp_path):
    ledger = started_ledger(tmp_path / 'two-entries.sqlite')
    try:
        _active_rows(ledger, _two_lines())
        query = _query(ledger).model_copy(update={'query_text': '风筝和杭州', 'limit': 2})
        docs = _corpus(ledger, _read(ledger))
        index = InMemoryRecallIndex(embedding=_TwoTopicEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.16')
        index.rebuild(cursor=query.cursor, documents=docs)
        result = index.search(query)
        refs = {hit.document.source_item_ref for hit in result.hits}
        assert 'prehistory-record:scene-entry-0' in refs
        assert 'prehistory-record:scene-entry-6' in refs
        assert len(result.hits) <= 6
        assert all(hit.document.source_bindings for hit in result.hits)
    finally:
        ledger.close()


class _ContextAndQuestionEmbedding:
    version = 'fixture-context-and-question.1'
    dimensions = 3

    def embed(self, texts):
        return tuple((1.0, 0.0, 0.0) if text == '熟悉的手工往事' or '风筝' in text else
                     (0.0, 1.0, 0.0) if text == '异地求学住处' or '杭州' in text else
                     (0.0, 0.0, 1.0) for text in texts)


class _PrimaryContextEmbedding:
    version = 'fixture-primary-context.1'
    dimensions = 3

    def embed(self, texts):
        return tuple((1., 0., 0.) if text == '背景甲' else
                     (.6, 0., .8) if '风筝' in text else
                     (0., 1., 0.) if text == '细节乙' or '杭州' in text else
                     (0., 0., 1.) for text in texts)


def test_question_focus_adds_an_entry_without_replacing_the_original_attention_anchor(tmp_path):
    ledger = started_ledger(tmp_path / 'preserve-primary.sqlite')
    try:
        _active_rows(ledger, _two_lines())
        query = _query(ledger).model_copy(update={'query_text': '背景甲', 'lexical_text': '细节乙', 'limit': 2})
        index = InMemoryRecallIndex(embedding=_PrimaryContextEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.16')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        assert result.hits[0].document.source_item_ref == 'prehistory-record:scene-entry-0'
        assert any(h.document.source_item_ref == 'prehistory-record:scene-entry-6' for h in result.hits)
    finally:
        ledger.close()


def test_literal_question_is_not_lost_inside_the_broader_attention_cue(tmp_path):
    ledger = started_ledger(tmp_path / 'literal-question.sqlite')
    try:
        _active_rows(ledger, _two_lines())
        query = _query(ledger).model_copy(update={'query_text': '熟悉的手工往事',
            'lexical_text': '异地求学住处', 'limit': 2})
        index = InMemoryRecallIndex(embedding=_ContextAndQuestionEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.16')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        refs = {h.document.source_item_ref for h in index.search(query).hits}
        assert {'prehistory-record:scene-entry-0', 'prehistory-record:scene-entry-6'} <= refs
    finally:
        ledger.close()


@pytest.mark.parametrize('condition', ['forgotten', 'other_actor', 'before_knowledge'])
def test_scene_completion_cannot_restore_ineligible_siblings(tmp_path, condition):
    from companion_daemon.world_v2.recall_index import FeatureHashRecallEmbedding
    from test_prehistory_memory_source import _choice
    ledger = started_ledger(tmp_path / f'scene-{condition}.sqlite')
    try:
        active = _native_scene_records(ledger)
        if condition == 'forgotten':
            speaker = active['prehistory-record:fixture-native.speaker']
            _choice(ledger, speaker.values.source_bindings[0], before=speaker, status='forgotten',
                    candidate_id=speaker.candidate_id)
        query = _query(ledger).model_copy(update={'query_text': '相册', 'limit': 1,
            'link_refs': (active['prehistory-record:fixture-native.album'].candidate_id,)})
        if condition == 'other_actor':
            query = query.model_copy(update={'actor_ref': 'actor:other', 'subject_refs': ('actor:other',)})
        if condition == 'before_knowledge':
            query = query.model_copy(update={'knowledge_as_of': datetime(2014, 1, 1, tzinfo=UTC)})
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.16')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        assert all(not h.document.source_item_ref.endswith('.speaker') for h in result.hits)
        assert all('父亲说，那时候你的手特别小。' not in h.document.text for h in result.hits)
    finally:
        ledger.close()


def _native_scene_records(ledger, transform=None):
    from test_native_story_compiler import source_value, authoring_brief
    from companion_daemon.world_v2.native_story import NativeStorySource, compile_native_story
    from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
    from test_prehistory_memory_source import _choice
    from test_character_prehistory import START
    raw = source_value()
    raw['scenes'][0]['blocks'] = [
        {**raw['scenes'][0]['blocks'][0], 'block_id': 'album', 'text': '她翻相册时问起自己出生的事。'},
        {**raw['scenes'][0]['blocks'][0], 'block_id': 'speaker', 'text': '父亲说，那时候你的手特别小。'},
    ]
    scene = raw['scenes'][0]
    for i in range(6):
        raw['scenes'].append({**scene, 'event_ref': f'event:album-other-{i}',
            'title': '同一天看另一页', 'blocks': [{**scene['blocks'][0],
                'block_id': f'other-{i}', 'text': f'她翻相册，看了第{i+1}页的相册照片。'}]})
        raw['links'].append({'left_block_id': 'album', 'right_block_id': f'other-{i}',
                            'relation_kind': 'episode_followup'})
    if transform is not None:
        raw = transform(raw)
    package = compile_native_story(NativeStorySource.model_validate_json(json.dumps(raw)),
        brief=authoring_brief(), archive_id='prehistory-archive:scene-qualification')
    rows = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=package.document.actor_ref).import_compiled(
        package, created_at=START)
    active = {}
    for row in rows:
        source = prehistory_memory_binding(row)
        key = 'memory:' + row.record.record_id
        pending = _choice(ledger, source, candidate_id=key)
        active[row.record.record_id] = _choice(ledger, source, candidate_id=key,
            before=pending, status='active')
    return active


class _CoreSceneEmbedding:
    version = 'fixture-complete-core-scenes.1'
    dimensions = 3

    def embed(self, texts):
        return tuple((.7, .7, 0.) if text.startswith('相邻主题') else
                     (1., 0., 0.) if text == '背景甲' or '题材甲' in text else
                     (0., 1., 0.) if text == '细节乙' or '题材乙' in text else
                     (0., 0., 1.) for text in texts)


def test_complete_main_and_question_scenes_before_filling_other_entries(tmp_path):
    def authored(raw):
        scene = raw['scenes'][0]
        block = scene['blocks'][0]
        raw['scenes'] = [
            {**scene, 'event_ref': 'event:core-a', 'blocks': [
                {**block, 'block_id': 'a0', 'text': '她还记得题材甲。'},
                {**block, 'block_id': 'a1', 'text': '父亲把照片递给她。'},
                {**block, 'block_id': 'a2', 'text': '母亲补充，那天其实下雨了。'}]},
            {**scene, 'event_ref': 'event:core-b', 'blocks': [
                {**block, 'block_id': 'b0', 'text': '她后来又想起题材乙。'},
                {**block, 'block_id': 'b1', 'text': '她把东西带进了新房间。'},
                {**block, 'block_id': 'b2', 'text': '柜子最后没有修好。'}]},
            *[{**scene, 'event_ref': f'event:extra-{i}', 'blocks': [
                {**block, 'block_id': f'extra-{i}', 'text': f'相邻主题{i}也提到了细节乙。'}]} for i in range(2)],
        ]
        raw['links'] = []
        return raw
    ledger = started_ledger(tmp_path / 'whole-main-scenes.sqlite')
    try:
        active = _native_scene_records(ledger, transform=authored)
        query = _query(ledger).model_copy(update={'query_text': '背景甲', 'lexical_text': '细节乙',
            'link_refs': (active['prehistory-record:fixture-native.a0'].candidate_id,), 'limit': 4})
        index = InMemoryRecallIndex(embedding=_CoreSceneEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.16')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        assert {h.document.source_item_ref for h in result.hits} == {
            'prehistory-record:fixture-native.' + ref for ref in ('a0', 'a1', 'a2', 'b0', 'b1', 'b2')}
    finally:
        ledger.close()


@pytest.mark.parametrize('forget_bridge', [False, True])
def test_relevant_later_scene_uses_only_retained_graph_bridges(tmp_path, forget_bridge):
    from companion_daemon.world_v2.recall_index import FeatureHashRecallEmbedding
    def authored(raw):
        scene, block = raw['scenes'][0], raw['scenes'][0]['blocks'][0]
        texts = [('她借出一只旧杯子。', '借走的人说以后送回来。'),
                 ('她清理了放杯子的抽屉。', '那天空出来一层。'),
                 ('她路过一家卖杯子的店。', '看过就走了。'),
                 ('后来旧杯子归还了，她接过归还的杯子。', '归还时杯沿有一道新划痕。')]
        raw['scenes'] = [{**scene, 'event_ref': f'event:cup-{i}', 'blocks': [
            {**block, 'block_id': f'cup-{i}-{j}', 'text': text} for j, text in enumerate(pair)]}
            for i, pair in enumerate(texts)]
        raw['links'] = [{'left_block_id': f'cup-{i}-0', 'right_block_id': f'cup-{i+1}-0',
                        'relation_kind': 'episode_followup'} for i in range(3)]
        return raw
    ledger = started_ledger(tmp_path / 'later-scene.sqlite')
    try:
        active = _native_scene_records(ledger, transform=authored)
        if forget_bridge:
            from test_prehistory_memory_source import _choice
            bridge = active['prehistory-record:fixture-native.cup-1-0']
            _choice(ledger, bridge.values.source_bindings[0], before=bridge, status='forgotten',
                    candidate_id=bridge.candidate_id)
        query = _query(ledger).model_copy(update={'query_text': '归还', 'limit': 1,
            'link_refs': (active['prehistory-record:fixture-native.cup-0-0'].candidate_id,)})
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.16')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        refs = {hit.document.source_item_ref for hit in result.hits}
        assert 'prehistory-record:fixture-native.cup-0-0' in refs
        if forget_bridge:
            assert 'prehistory-record:fixture-native.cup-3-0' not in refs
        else:
            assert {'prehistory-record:fixture-native.cup-3-0',
                    'prehistory-record:fixture-native.cup-3-1'} <= refs
        assert len(result.hits) <= 6
    finally:
        ledger.close()


def test_ordinary_recall_keeps_its_old_reading_and_scores():
    from test_recall_index import _documents, _query as ordinary_query, _SemanticFixtureEmbedding
    query = ordinary_query(lexical_text='雨夜回家时觉得很放松。')
    results = []
    for policy in ('world-v2-recall-index.hybrid.15', 'world-v2-recall-index.hybrid.16'):
        index = InMemoryRecallIndex(embedding=_SemanticFixtureEmbedding(), policy_version=policy)
        index.rebuild(cursor=query.cursor, documents=_documents())
        results.append(index.search(query))
    assert results[0].hits == results[1].hits


def test_oversized_related_scene_still_exposes_its_relevant_bounded_record(tmp_path):
    from companion_daemon.world_v2.recall_model_reading import interior_recall_item
    def authored(raw):
        scene, block = raw['scenes'][0], raw['scenes'][0]['blocks'][0]
        raw['scenes'] = [
            {**scene, 'event_ref': 'event:large-a', 'blocks': [
                {**block, 'block_id': f'a{i}', 'text': text} for i, text in enumerate(
                    ['她曾经谈起题材甲。', '父亲带来了旧照片。', '母亲也坐在旁边。'])]},
            {**scene, 'event_ref': 'event:large-b', 'blocks': [
                {**block, 'block_id': f'b{i}', 'text': text} for i, text in enumerate(
                    ['她后来想起题材乙。', '后来她回了一趟家。', '房间还在原处。', '柜子里放着书。'])]},
        ]
        raw['links'] = [{'left_block_id': 'a0', 'right_block_id': 'b0', 'relation_kind': 'episode_followup'}]
        return raw
    ledger = started_ledger(tmp_path / 'partial-scene.sqlite')
    try:
        active = _native_scene_records(ledger, transform=authored)
        query = _query(ledger).model_copy(update={'query_text': '细节乙', 'limit': 1,
            'link_refs': (active['prehistory-record:fixture-native.a0'].candidate_id,)})
        index = InMemoryRecallIndex(embedding=_CoreSceneEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.16')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        target = next(h.document for h in result.hits if h.document.source_item_ref.endswith('.b0'))
        reading = interior_recall_item(target, index_version=result.index_version)
        assert reading['reading_scope'] == 'selected_records_not_exhaustive_history'
        assert reading['unreturned_followups'] == 'not_established'
        assert target.prehistory.related_record_refs
        assert len(result.hits) <= 6
    finally:
        ledger.close()


@pytest.mark.parametrize('policy,keeps_speaker', [
    ('world-v2-recall-index.hybrid.15', False),
    ('world-v2-recall-index.hybrid.16', True),
])
@pytest.mark.parametrize('limit', [1, 2])
def test_same_scene_speaker_survives_competing_nearby_story_branches(tmp_path, policy, keeps_speaker, limit):
    from companion_daemon.world_v2.recall_index import FeatureHashRecallEmbedding
    from companion_daemon.world_v2.recall_runtime import RecallCoordinator, verify_trusted_recall_trace
    from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
    from test_character_prehistory import ACTOR
    ledger = started_ledger(tmp_path / 'same-scene.sqlite')
    coordinator = None
    try:
        active = _native_scene_records(ledger)
        query = _query(ledger)
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(), policy_version=policy)
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        coordinator = RecallCoordinator.from_built_index(index=index, cursor=query.cursor,
            actor_ref=ACTOR, subject_refs=(ACTOR,), logical_time=query.at, trigger_ref='fixture:scene-read')
        trace = coordinator.recall(request=CharacterRecallRequest(query_text='相册', limit=limit,
            link_refs=(active['prehistory-record:fixture-native.album'].candidate_id,)),
            accessibility_seed='fixture:scene-read', expected_cursor=query.cursor, trigger_ref='fixture:scene-read')
        audit = verify_trusted_recall_trace(trace)
        refs = {h.document.source_item_ref for h in audit.hits}
        assert ('prehistory-record:fixture-native.speaker' in refs) is keeps_speaker
        if keeps_speaker:
            speaker = next(h.document for h in audit.hits if h.document.source_item_ref.endswith('.speaker'))
            assert speaker.text == '父亲说，那时候你的手特别小。'
            assert len(speaker.source_bindings) == 2
            refs_in_order = [h.document.source_item_ref for h in audit.hits]
            assert abs(refs_in_order.index('prehistory-record:fixture-native.speaker') -
                       refs_in_order.index('prehistory-record:fixture-native.album')) == 1
        assert len(audit.hits) <= 6
    finally:
        if coordinator:
            coordinator.close()
        ledger.close()
