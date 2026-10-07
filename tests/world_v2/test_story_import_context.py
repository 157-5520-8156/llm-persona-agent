"""Reviewed incremental stories share identity without rewriting accepted history."""
from datetime import datetime, UTC
import json
import pytest

from companion_daemon.world_v2.character_prehistory import ReviewedPrehistoryArchive, digest
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from test_character_prehistory import ACTOR, START, reviewed_archive, started_ledger
from test_story_memory_structure import story_archive


def _reviewed(document):
    return ReviewedPrehistoryArchive(document=document, review=reviewed_archive().review.model_copy(
        update={'manifest_hash': digest(document.manifest())},
    ))


def test_import_rejects_stale_history_basis_but_exact_reimport_is_inert(tmp_path):
    from companion_daemon.world_v2.story_import import bind_import_context
    ledger = started_ledger(tmp_path / 'delta.sqlite')
    try:
        base = story_archive().document
        first = bind_import_context(base, accepted_archives=(), world_start_ref='world-start', world_start_payload_hash=digest({}),
                                    world_started_at=START)
        other = base.model_copy(update={'archive_id': 'prehistory-archive:other', 'records': tuple(
            record.model_copy(update={'record_id': record.record_id + '-other',
                'related_record_refs': (), 'story': record.story.model_copy(update={'relations': ()})})
            for record in base.records
        )})
        stale = bind_import_context(other, accepted_archives=(), world_start_ref='world-start', world_start_payload_hash=digest({}),
                                    world_started_at=START)
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR)
        rows = runtime.import_reviewed(_reviewed(first), created_at=START)
        before = ledger.project()
        assert runtime.import_reviewed(_reviewed(first), created_at=START) == rows
        with pytest.raises(ValueError, match='history basis'):
            runtime.import_reviewed(_reviewed(stale), created_at=START)
        assert ledger.project().semantic_hash == before.semantic_hash
        assert ledger.rebuild().semantic_hash == before.semantic_hash
    finally:
        ledger.close()


def test_two_stories_share_a_reviewed_event_but_keep_their_own_knowledge_and_links(tmp_path):
    from companion_daemon.world_v2.story_import import bind_import_context
    from companion_daemon.world_v2.character_prehistory import StoryEventMatchDraft, CrossStoryRelation
    from test_prehistory_memory_source import _read
    from test_prehistory_story_recall import _active_rows

    ledger = started_ledger(tmp_path / 'two-stories.sqlite')
    try:
        first = bind_import_context(story_archive().document, accepted_archives=(),
            world_start_ref='world-start', world_start_payload_hash=digest({}), world_started_at=START)
        first_active = _active_rows(ledger, _reviewed(first))
        old = first.records[0]
        text = '2022年她再次想起那次还伞，明白那是对方特意绕路送来的；此前她只记得对方追到了车站。'
        new_id = 'prehistory-record:later-understanding'
        when = datetime(2022, 1, 1, tzinfo=UTC)
        block = old.story.blocks[0].model_copy(update={'event_ref': 'event:recalled-umbrella',
            'text': text, 'source_quote': text, 'kind': 'historical_interpretation', 'subject_actor_ref': ACTOR})
        new = old.model_copy(update={'record_id': new_id, 'statement': text, 'related_record_refs': (),
            'occurred_from': when, 'occurred_until': when, 'time_precision': 'day',
            'story': old.story.model_copy(update={'known_from': when, 'known_until': when,
                                                 'blocks': (block,), 'relations': ()})})
        candidate = first.model_copy(update={'contract': 'character-prehistory-archive.2',
            'archive_id': 'prehistory-archive:second-story', 'import_context': None, 'records': (new,)})
        relation = CrossStoryRelation(
            left_record_id=old.record_id, right_record_id=new_id, relation_kind='explicit_callback',
            left_block_id=old.story.blocks[0].block_id, left_source_quote=old.story.blocks[0].source_quote,
            right_block_id=block.block_id, right_source_quote=text, bridge_source_quote=text,
            rationale='The new passage explicitly recalls the same umbrella event.',
        )
        second = bind_import_context(candidate, accepted_archives=(first,),
            world_start_ref='world-start', world_start_payload_hash=digest({}), world_started_at=START,
            event_matches=(StoryEventMatchDraft(event_ref=block.event_ref, prior_record_id=old.record_id,
                                                prior_event_ref=old.story.blocks[0].event_ref),),
            cross_story_links=(relation,))
        _active_rows(ledger, _reviewed(second))
        readings = {e.source_id: e.prehistory for item in _read(ledger).items for e in item.source_excerpts}
        assert readings[old.record_id].story.blocks[0].canonical_event_ref == readings[new_id].story.blocks[0].canonical_event_ref
        assert readings[old.record_id].story.known_from.year == 2015
        assert readings[new_id].story.known_from.year == 2022
        assert readings[new_id].related_record_refs == (old.record_id,)
        assert readings[new_id].story.relations[0].left_record_id == old.record_id
        assert next(r.record for r in ledger.project().prehistory_records if r.record.record_id == old.record_id) == old
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
        from test_prehistory_memory_source import _choice, _corpus
        from test_prehistory_story_recall import _query
        from companion_daemon.world_v2.recall_index import InMemoryRecallIndex, FeatureHashRecallEmbedding
        from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
        query = _query(ledger).model_copy(update={'knowledge_as_of': datetime(2021, 1, 1, tzinfo=UTC)})
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        historical = index.search(query).model_dump_json()
        assert new_id not in historical
        old_row = next(r for r in ledger.project().prehistory_records if r.record.record_id == old.record_id)
        _choice(ledger, prehistory_memory_binding(old_row), before=first_active[old.record_id],
                candidate_id=first_active[old.record_id].candidate_id, status='forgotten')
        current = json.dumps([item.model_dump(mode='json') for item in _read(ledger).items])
        assert new_id in current
        assert old.record_id not in current
        assert not _read(ledger, actor_ref='actor:other').items
    finally:
        ledger.close()


def test_delta_review_requires_every_import_dimension_and_binds_the_basis():
    from companion_daemon.world_v2.story_import import bind_import_context
    from companion_daemon.world_v2.prehistory_authoring import (
        PrehistoryImportAssessment, semantic_review_request, bind_semantic_review, package_reviewed,
    )
    from test_prehistory_authoring import brief
    doc = bind_import_context(story_archive().document, accepted_archives=(),
        world_start_ref='world-start', world_start_payload_hash=digest({}), world_started_at=START)
    b = brief().model_copy(update={'draft_source_ref': doc.source_artifact_ref,
                                  'world_start_payload_hash': digest({})})
    request = semantic_review_request(b, doc)
    assert request['contract'] == 'prehistory-semantic-review-request.3'
    raw = {'contract': 'prehistory-import-assessment.3',
           'records': [{'record_index': i, 'verdict': 'approve', 'rationale': 'Fixture source review.'}
                       for i, _ in enumerate(doc.records)],
           'story_links': [], 'cross_record_findings': [], 'event_matches': [], 'cross_story_links': []}
    result = bind_semantic_review(request, PrehistoryImportAssessment.model_validate_json(json.dumps(raw)),
                                  reviewer_ref='reviewer:independent', reviewed_at=START)
    assert result.contract == 'prehistory-creation-review.2'
    assert package_reviewed(b, doc, result, review_artifact_ref='fixture:bound').review.manifest_hash == digest(doc.manifest())
    del raw['cross_story_links']
    with pytest.raises(ValueError):
        PrehistoryImportAssessment.model_validate_json(json.dumps(raw))


def test_coverage_review_cannot_approve_an_empty_plan_that_omits_a_sourced_callback():
    from companion_daemon.world_v2.story_import import bind_import_context
    from companion_daemon.world_v2.prehistory_authoring import (
        PrehistoryCoverageAssessment, semantic_review_request, bind_semantic_review, package_reviewed,
    )
    from test_prehistory_authoring import brief
    prior = reviewed_archive().document
    doc = bind_import_context(story_archive().document, accepted_archives=(prior,),
        world_start_ref='world-start', world_start_payload_hash=digest({}), world_started_at=START,
        require_coverage_review=True)
    b = brief().model_copy(update={'draft_source_ref': doc.source_artifact_ref,
        'world_start_payload_hash': digest({}), 'accepted_archives': (prior,)})
    packet = semantic_review_request(b, doc)
    assert packet['contract'] == 'prehistory-semantic-review-request.4'
    value = {'contract': 'prehistory-import-assessment.4',
        'records': [{'record_index': i, 'verdict': 'approve', 'rationale': 'Fixture judgment.'} for i, _ in enumerate(doc.records)],
        'story_links': [], 'cross_record_findings': [], 'event_matches': [], 'cross_story_links': [],
        'missing_connections': [{'new_record_id': doc.records[0].record_id,
            'prior_record_id': prior.records[0].record_id,
            'new_source_quote': doc.records[0].story.blocks[0].source_quote,
            'prior_source_quote': prior.records[0].statement,
            'rationale': 'Fixture model flags an omitted relationship, not a host inference.'}]}
    result = bind_semantic_review(packet, PrehistoryCoverageAssessment.model_validate_json(json.dumps(value)),
        reviewer_ref='reviewer:fixture', reviewed_at=START)
    assert result.decision == 'rejected'
    with pytest.raises(ValueError, match='rejected draft'):
        package_reviewed(b, doc, result, review_artifact_ref='fixture:review')
    value['missing_connections'][0]['prior_source_quote'] = '不存在的证据'
    with pytest.raises(ValueError, match='endpoint source evidence'):
        bind_semantic_review(packet, PrehistoryCoverageAssessment.model_validate_json(json.dumps(value)),
            reviewer_ref='reviewer:fixture', reviewed_at=START)


@pytest.mark.parametrize('tamper', ['snapshot', 'canonical', 'biography', 'world_start'])
def test_import_basis_cannot_be_reapproved_after_tampering(tmp_path, tamper):
    from companion_daemon.world_v2.story_import import bind_import_context
    from companion_daemon.world_v2.character_prehistory import StoryEventMatchDraft, PrehistoryArchiveDocument
    ledger = started_ledger(tmp_path / 'tamper.sqlite')
    try:
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR)
        first = story_archive().document
        runtime.import_reviewed(_reviewed(first), created_at=START)
        record = first.records[0].model_copy(update={'record_id': 'prehistory-record:second',
            'related_record_refs': (), 'story': first.records[0].story.model_copy(update={'relations': ()})})
        candidate = first.model_copy(update={'archive_id': 'prehistory-archive:second', 'records': (record,)})
        doc = bind_import_context(candidate, accepted_archives=(first,), world_start_ref='world-start',
            world_start_payload_hash=digest({}), world_started_at=START, event_matches=(StoryEventMatchDraft(
                event_ref=record.story.blocks[0].event_ref, prior_record_id=first.records[0].record_id,
                prior_event_ref=first.records[0].story.blocks[0].event_ref),))
        value = doc.model_dump(mode='json')
        context = value['import_context']
        if tamper == 'snapshot':
            context['existing_records'][0]['record']['statement'] += ' 未经接受的旧史。'
        elif tamper == 'canonical':
            context['event_matches'][0]['canonical_event_ref'] = 'history-event:' + 'a' * 64
        elif tamper == 'biography':
            context['biography_events'] = [{'event_ref': 'event:unaccepted', 'payload_hash': 'b' * 64}]
        else:
            context['world_start_payload_hash'] = 'c' * 64
        changed = PrehistoryArchiveDocument.model_validate_json(json.dumps(value))
        before = ledger.project().semantic_hash
        with pytest.raises(ValueError):
            runtime.import_reviewed(_reviewed(changed), created_at=START)
        assert ledger.project().semantic_hash == before
    finally:
        ledger.close()


def test_complete_replay_revalidates_manifest_against_new_records():
    from companion_daemon.world_v2.story_import import bind_import_context, validate_complete_import
    from companion_daemon.world_v2.character_prehistory import StoryEventMatchDraft
    first = story_archive().document
    old = first.records[0]
    new = old.model_copy(update={'record_id': 'prehistory-record:second', 'related_record_refs': (),
        'story': old.story.model_copy(update={'relations': ()})})
    candidate = first.model_copy(update={'archive_id': 'prehistory-archive:second', 'records': (new,)})
    doc = bind_import_context(candidate, accepted_archives=(first,), world_start_ref='world-start',
        world_start_payload_hash=digest({}), world_started_at=START, event_matches=(StoryEventMatchDraft(
            event_ref=new.story.blocks[0].event_ref, prior_record_id=old.record_id,
            prior_event_ref=old.story.blocks[0].event_ref),))
    manifest = doc.manifest()
    bad = manifest.model_copy(update={'import_context': manifest.import_context.model_copy(update={
        'event_matches': (manifest.import_context.event_matches[0].model_copy(update={'event_ref': 'event:absent'}),)})})
    with pytest.raises(ValueError, match='unknown source identity'):
        validate_complete_import(bad, doc.records)
