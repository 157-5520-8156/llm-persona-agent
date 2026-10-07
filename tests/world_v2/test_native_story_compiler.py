"""Native authored history compiles without an invented reviewer grant."""
import json
import pytest

from test_character_prehistory import ACTOR, START, started_ledger
from test_prehistory_authoring import brief
from companion_daemon.world_v2.character_prehistory import digest


def source_value():
    return {'contract': 'native-story-source.1', 'source_id': 'fixture-native',
        'title': '同一只纸船', 'authored_at': START.isoformat(), 'author_ref': 'operator:fixture-author',
        'actor_ref': ACTOR, 'entities': [],
        'storylines': [{'line_id': 'family', 'title': '家里的小东西'}, {'line_id': 'play', 'title': '自己试试看'}],
        'scenes': [{'event_ref': 'event:native:boat', 'title': '纸船进水',
            'occurred_from': '2015-05-01T00:00:00Z', 'occurred_until': '2015-05-31T23:59:59Z',
            'line_refs': ['family', 'play'], 'participant_refs': [],
            'blocks': [{'block_id': 'boat-seen', 'kind': 'event', 'subject_actor_ref': None,
                'text': '她把纸船放进水盆，船头先湿了。她把湿纸翻开，没有继续叠。',
                'knowledge': 'experienced', 'known_from': None, 'known_until': None, 'privacy_class': 'personal'},
                {'block_id': 'not-heard', 'kind': 'background', 'subject_actor_ref': None,
                 'text': '父亲没有说出口的安排仍是她不知道的事。', 'knowledge': 'world_only',
                 'known_from': None, 'known_until': None, 'privacy_class': 'private'}]}],
        'links': [], 'legacy_links': [], 'open_questions': []}


def authoring_brief():
    b = brief()
    return b.model_copy(update={'actor_ref': ACTOR, 'world_start_payload_hash': digest({}),
        'world_id': __import__('test_life_projection').WORLD_ID, 'world_started_at': START,
        'world_start_event_ref': 'world-start'})


def test_reflection_alias_normalizes_metadata_without_rewriting_content_or_owner():
    from companion_daemon.world_v2.native_story import NativeStoryBlock
    raw = {**source_value()['scenes'][0]['blocks'][0], 'kind': 'self_reflection',
           'knowledge': 'self_reflection', 'subject_actor_ref': ACTOR}
    alias = NativeStoryBlock.model_validate_json(json.dumps(raw))
    normal = NativeStoryBlock.model_validate_json(json.dumps({**raw, 'kind': 'historical_interpretation'}))
    assert alias == normal and alias.text == raw['text'] and alias.subject_actor_ref == ACTOR
    with pytest.raises(ValueError):
        NativeStoryBlock.model_validate_json(json.dumps({**raw, 'knowledge': 'experienced'}))


def test_native_compile_import_and_replay_preserve_source_without_review(tmp_path):
    from companion_daemon.world_v2.native_story import NativeStorySource, compile_native_story
    from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
    source = NativeStorySource.model_validate_json(json.dumps(source_value()))
    b = authoring_brief()
    package = compile_native_story(source, brief=b, archive_id='prehistory-archive:native-fixture')
    assert package == compile_native_story(source, brief=b, archive_id='prehistory-archive:native-fixture')
    assert package.compilation.compiler_version == 'native-story-compiler.1'
    assert 'review' not in package.model_dump(mode='json')
    assert len(package.document.records) == 1
    assert '不知道的事' not in package.document.records[0].statement
    ledger = started_ledger(tmp_path / 'native.sqlite')
    try:
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR)
        rows = runtime.import_compiled(package, created_at=START)
        before = ledger.project()
        assert runtime.import_compiled(package, created_at=START) == rows
        assert before.semantic_hash == ledger.project().semantic_hash == ledger.rebuild().semantic_hash
        assert before.prehistory_archives[0].review is None
        assert before.prehistory_archives[0].compilation == package.compilation
        assert before.memory_candidates == ()
        from companion_daemon.world_v2.prehistory_memory_decision import initialization_opportunity
        from companion_daemon.world_v2.schemas import ProjectionCursor
        opportunity = initialization_opportunity(world_id=ledger.world_id, row=rows[0],
            archive=before.prehistory_archives[0], cursor=ProjectionCursor(world_revision=before.world_revision,
                deliberation_revision=before.deliberation_revision, ledger_sequence=before.ledger_sequence))
        assert 'no independent model review' in opportunity.capability_manifest.payload['initialization_scope']
    finally:
        ledger.close()


def test_native_source_cannot_redeclare_an_accepted_occurrence_under_new_records():
    from companion_daemon.world_v2.native_story import NativeStorySource, compile_native_story
    source = NativeStorySource.model_validate_json(json.dumps(source_value()))
    original = compile_native_story(source, brief=authoring_brief(), archive_id='prehistory-archive:first')
    changed = source.model_copy(update={'source_id': 'second-source'})
    b = authoring_brief().model_copy(update={'accepted_archives': (original.document,)})
    with pytest.raises(ValueError, match='already accepted occurrence'):
        compile_native_story(changed, brief=b, archive_id='prehistory-archive:second')


@pytest.mark.parametrize('mutation', ['source', 'receipt', 'actor', 'future_knowledge', 'foreign_private'])
def test_native_compile_cannot_forge_source_or_actor_knowledge(mutation):
    from companion_daemon.world_v2.native_story import NativeStorySource, compile_native_story
    from companion_daemon.world_v2.character_prehistory import CompiledPrehistoryArchive
    raw = source_value()
    if mutation in {'actor', 'future_knowledge', 'foreign_private'}:
        if mutation == 'actor':
            raw['actor_ref'] = 'actor:someone-else'
        elif mutation == 'future_knowledge':
            raw['scenes'][0]['blocks'][0].update(known_from='2014-01-01T00:00:00Z', known_until='2014-12-31T00:00:00Z')
        else:
            raw['scenes'][0]['blocks'][0].update(kind='historical_interpretation', subject_actor_ref='actor:someone-else')
        with pytest.raises(ValueError):
            compile_native_story(NativeStorySource.model_validate_json(json.dumps(raw)), brief=authoring_brief(),
                                 archive_id='prehistory-archive:native-fixture')
        return
    package = compile_native_story(NativeStorySource.model_validate_json(json.dumps(raw)), brief=authoring_brief(),
                                   archive_id='prehistory-archive:native-fixture')
    value = package.model_dump(mode='json')
    if mutation == 'source':
        original = json.loads(value['compilation']['source_json'])
        original['scenes'][0]['blocks'][0]['text'] += '她后来又编出了另一个事实。'
        value['compilation']['source_json'] = json.dumps(original)
    else:
        value['compilation']['manifest_hash'] = 'f' * 64
    with pytest.raises(ValueError):
        CompiledPrehistoryArchive.model_validate_json(json.dumps(value))


def test_native_story_reaches_public_memory_without_world_only_prose(tmp_path):
    from companion_daemon.world_v2.native_story import NativeStorySource, compile_native_story
    from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
    from test_prehistory_memory_source import _choice, _read, _corpus
    from test_prehistory_chat_context import _capsule
    raw = source_value()
    raw['scenes'][0]['blocks'].append({**raw['scenes'][0]['blocks'][0], 'block_id': 'boat-thought',
        'kind': 'historical_interpretation', 'subject_actor_ref': ACTOR,
        'knowledge': 'self_reflection', 'text': '她当时觉得下次可以换一张纸，没有决定什么时候再试。'})
    package = compile_native_story(NativeStorySource.model_validate_json(json.dumps(raw)), brief=authoring_brief(),
                                   archive_id='prehistory-archive:native-fixture')
    ledger = started_ledger(tmp_path / 'native-memory.sqlite')
    try:
        rows = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_compiled(package, created_at=START)
        for row in rows:
            binding = prehistory_memory_binding(row)
            key = 'memory:' + row.record.record_id
            pending = _choice(ledger, binding, candidate_id=key)
            _choice(ledger, binding, before=pending, status='active', candidate_id=key)
        reading = _read(ledger)
        assert len(reading.items) == 2
        assert '不知道的事' not in reading.model_dump_json()
        corpus = _corpus(ledger, reading)
        assert len(corpus) == 2
        identities = {b.canonical_event_ref for d in corpus for b in d.prehistory.story.blocks}
        assert len(identities) == 1
        assert any(d.prehistory.story.relations for d in corpus)
        assert '不知道的事' not in _capsule(ledger, actor=ACTOR, trigger='fixture:native').model_dump_json()
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        ledger.close()
