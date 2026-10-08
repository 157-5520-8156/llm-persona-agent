"""Native episode source -> immutable import -> actor-scoped reading."""
import json

from test_native_story_compiler import source_value, authoring_brief
from test_character_prehistory import ACTOR, START, started_ledger
from companion_daemon.world_v2.native_story import NativeStorySource, compile_native_story
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime


def episode_source():
    raw = source_value()
    raw['contract'] = 'native-story-source.2'
    raw['episodes'] = [{'episode_ref': 'story-episode:fixture-boat-week', 'title': '一次试纸船的星期',
        'revision': 1, 'record_refs': ['prehistory-record:fixture-native.boat-seen']}]
    return raw


def test_native_episode_import_replay_and_source_identity(tmp_path):
    source = NativeStorySource.model_validate_json(json.dumps(episode_source()))
    package = compile_native_story(source, brief=authoring_brief(), archive_id='prehistory-archive:episode')
    assert len(package.document.records) == 1
    assert package.document.episodes[0].record_refs == ('prehistory-record:fixture-native.boat-seen',)
    assert package.compilation.compiler_version == 'native-story-compiler.2'
    ledger = started_ledger(tmp_path / 'episode.sqlite')
    try:
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR)
        rows = runtime.import_compiled(package, created_at=START)
        before = ledger.project()
        assert before.prehistory_archives[0].manifest.episodes == package.document.episodes
        assert runtime.import_compiled(package, created_at=START) == rows
        assert ledger.rebuild().semantic_hash == before.semantic_hash
        assert not before.memory_candidates
    finally:
        ledger.close()


def test_episode_navigation_exposes_only_retained_members_and_never_author_title(tmp_path):
    from test_prehistory_memory_source import _choice, _read, _corpus
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
    from companion_daemon.world_v2.recall_index import InMemoryRecallIndex, FeatureHashRecallEmbedding
    from companion_daemon.world_v2.recall_model_reading import interior_recall_item
    from test_prehistory_story_recall import _query
    ledger = started_ledger(tmp_path / 'read-episode.sqlite')
    try:
        raw = episode_source()
        raw['episodes'][0]['title'] = '秘密标题：尚未告知的结局'
        package = compile_native_story(NativeStorySource.model_validate_json(json.dumps(raw)),
            brief=authoring_brief(), archive_id='prehistory-archive:episode')
        row, = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_compiled(package, created_at=START)
        assert not _read(ledger).items
        source = prehistory_memory_binding(row)
        pending = _choice(ledger, source)
        active = _choice(ledger, source, before=pending, status='active')
        docs = _corpus(ledger, _read(ledger))
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(), policy_version='world-v2-recall-index.hybrid.17')
        query = _query(ledger).model_copy(update={'query_text': '纸船'})
        index.rebuild(cursor=query.cursor, documents=docs)
        result = index.search(query)
        item = interior_recall_item(result.hits[0].document, index_version=result.index_version)
        assert item['episode_navigation'][0]['episode_ref'].startswith('story-episode-view:')
        assert '秘密标题' not in json.dumps(item, ensure_ascii=False)
        assert '秘密标题' not in _read(ledger).model_dump_json()
        _choice(ledger, source, before=active, status='forgotten')
        assert not _corpus(ledger, _read(ledger))
    finally:
        ledger.close()


def test_episode_detail_pages_follow_authored_order_and_respect_knowledge_time(tmp_path):
    from datetime import datetime, UTC
    from test_prehistory_memory_source import _choice, _read, _corpus
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
    from companion_daemon.world_v2.recall_index import InMemoryRecallIndex, FeatureHashRecallEmbedding, RecallQuery
    from companion_daemon.world_v2.recall_runtime import RecallCoordinator, verify_trusted_recall_trace
    from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
    from test_prehistory_story_recall import _query
    raw = episode_source()
    scene = raw['scenes'][0]
    raw['scenes'] = [{**scene, 'event_ref': f'event:day-{i}',
        'occurred_from': f'2015-05-{i+1:02d}T00:00:00Z', 'occurred_until': f'2015-05-{i+1:02d}T23:59:59Z',
        'blocks': [{**scene['blocks'][0], 'block_id': f'day-{i}', 'text': f'第{i+1}天她试过一张纸。'}]} for i in range(8)]
    raw['episodes'][0]['record_refs'] = [f'prehistory-record:fixture-native.day-{i}' for i in range(8)]
    ledger = started_ledger(tmp_path / 'episode-pages.sqlite')
    coordinator = None
    try:
        package = compile_native_story(NativeStorySource.model_validate_json(json.dumps(raw)),
            brief=authoring_brief(), archive_id='prehistory-archive:episode')
        rows = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_compiled(package, created_at=START)
        for row in rows:
            source = prehistory_memory_binding(row)
            key = 'memory:' + row.record.record_id
            pending = _choice(ledger, source, candidate_id=key)
            _choice(ledger, source, before=pending, status='active', candidate_id=key)
        query = _query(ledger).model_copy(update={'query_text': '纸'})
        docs = _corpus(ledger, _read(ledger))
        episode_ref = docs[0].prehistory.episodes[0].episode_ref
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(), policy_version='world-v2-recall-index.hybrid.17')
        index.rebuild(cursor=query.cursor, documents=docs)
        coordinator = RecallCoordinator.from_built_index(index=index, cursor=query.cursor,
            actor_ref=ACTOR, subject_refs=(ACTOR,), logical_time=query.at, trigger_ref='episode:pages')
        request = CharacterRecallRequest.model_validate_json(json.dumps({'query_text': '继续看这段经过',
            'episode_window': {'episode_ref': episode_ref, 'mode': 'detail'}, 'limit': 3}))
        first = verify_trusted_recall_trace(coordinator.recall(request=request,
            accessibility_seed='episode:pages', expected_cursor=query.cursor, trigger_ref='episode:pages'))
        assert [h.document.source_item_ref for h in first.hits] == raw['episodes'][0]['record_refs'][:3]
        later_request = CharacterRecallRequest.model_validate_json(json.dumps({**request.model_dump(mode='json'),
            'episode_window': {'episode_ref': episode_ref, 'mode': 'detail', 'after_record_ref': first.hits[-1].document.source_item_ref}}))
        later = verify_trusted_recall_trace(coordinator.recall(request=later_request,
            accessibility_seed='episode:pages', expected_cursor=query.cursor, trigger_ref='episode:pages'))
        assert [h.document.source_item_ref for h in later.hits] == raw['episodes'][0]['record_refs'][3:6]
        past = RecallQuery.model_validate_json(json.dumps({**query.model_dump(mode='json'),
            'episode_window': {'episode_ref': episode_ref, 'mode': 'overview'},
            'knowledge_as_of': datetime(2015,5,3,tzinfo=UTC).isoformat()}))
        result = index.search(past)
        assert {h.document.source_item_ref for h in result.hits} == set(raw['episodes'][0]['record_refs'][:2])
    finally:
        if coordinator:
            coordinator.close()
        ledger.close()


def test_episode_author_continuation_preserves_previous_members_and_exact_old_source():
    import pytest
    from companion_daemon.world_v2.native_story import NativeStoryBatch
    from companion_daemon.world_v2.native_story_runner import merge_native_batch
    source = NativeStorySource.model_validate_json(json.dumps(episode_source()))
    block = source_value()['scenes'][0]['blocks'][0]
    raw = {'entities': [], 'scenes': [{**source_value()['scenes'][0], 'event_ref': 'event:next-day',
        'blocks': [{**block, 'block_id':'next-day','text':'次日她把盆里的水倒掉。'}]}],
        'links': [], 'legacy_links': [], 'open_questions': [],
        'episodes': [{**episode_source()['episodes'][0], 'record_refs': [
            'prehistory-record:fixture-native.boat-seen','prehistory-record:fixture-native.next-day']}]}
    header=source.model_dump(mode='json',exclude={'episodes','scenes','entities','links','legacy_links','open_questions'})
    merged=merge_native_batch(header,source,NativeStoryBatch.model_validate_json(json.dumps(raw)))
    assert merged.scenes[0]==source.scenes[0]
    assert merged.episodes[0].record_refs[-1]=='prehistory-record:fixture-native.next-day'
    raw['episodes'][0]['record_refs'].reverse()
    # With a single old member its relative order is unchanged; dropping it is not permitted.
    raw['episodes'][0]['record_refs'].remove('prehistory-record:fixture-native.boat-seen')
    with pytest.raises(ValueError,match='episode prefix'):
        merge_native_batch(header,source,NativeStoryBatch.model_validate_json(json.dumps(raw)))


def test_episode_revision_links_old_memory_without_reimport_and_pins_new_authority(tmp_path):
    import pytest
    from companion_daemon.world_v2.character_prehistory import digest
    from test_prehistory_memory_source import _choice, _read, _corpus
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
    from companion_daemon.world_v2.recall_index import InMemoryRecallIndex, FeatureHashRecallEmbedding
    from test_prehistory_story_recall import _query
    first = compile_native_story(NativeStorySource.model_validate_json(json.dumps(episode_source())),
        brief=authoring_brief(), archive_id='prehistory-archive:episode-first')
    raw=episode_source()
    raw['source_id']='fixture-next'
    raw['scenes'][0]['event_ref']='event:second-day'
    raw['scenes'][0]['blocks']=[{**raw['scenes'][0]['blocks'][0],'block_id':'followup','text':'几天后她拿出了另一张纸。'}]
    raw['episodes'][0].update(revision=2,predecessor_hash=digest(first.document.episodes[0]),
        record_refs=['prehistory-record:fixture-native.boat-seen','prehistory-record:fixture-next.followup'])
    second_brief=authoring_brief().model_copy(update={'accepted_archives':(first.document,)})
    second=compile_native_story(NativeStorySource.model_validate_json(json.dumps(raw)),brief=second_brief,
        archive_id='prehistory-archive:episode-second')
    ledger=started_ledger(tmp_path/'revision.sqlite')
    try:
        runtime=PrehistoryArchiveRuntime(ledger=ledger,owner_actor_ref=ACTOR)
        old,=runtime.import_compiled(first,created_at=START)
        source=prehistory_memory_binding(old)
        pending=_choice(ledger,source)
        _choice(ledger,source,before=pending,status='active')
        runtime.import_compiled(second,created_at=START)
        assert len(ledger.project().prehistory_records)==2
        doc,=_corpus(ledger,_read(ledger))
        assert len(doc.source_bindings)==3  # old record + old archive + new membership archive
        assert doc.prehistory.episodes[0].position==0  # unretained second member cannot leak a count
        query=_query(ledger).model_copy(update={'query_text':'纸船'})
        index=InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(),policy_version='world-v2-recall-index.hybrid.17')
        index.rebuild(cursor=query.cursor,documents=(doc,))
        assert len(index.search(query).hits[0].document.source_bindings)==3
        assert ledger.rebuild().semantic_hash==ledger.project().semantic_hash
        stale=compile_native_story(NativeStorySource.model_validate_json(json.dumps({**raw,'source_id':'stale-next',
            'episodes':[{**raw['episodes'][0],'record_refs':['prehistory-record:fixture-native.boat-seen','prehistory-record:stale-next.followup']}]})),
            brief=second_brief,archive_id='prehistory-archive:episode-stale')
        with pytest.raises(ValueError):
            runtime.import_compiled(stale,created_at=START)
    finally:
        ledger.close()
