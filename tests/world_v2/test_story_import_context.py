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


@pytest.mark.parametrize('existing_delta', [False, True])
@pytest.mark.parametrize('long_source', [False, True])
def test_two_stories_share_a_reviewed_event_but_keep_their_own_knowledge_and_links(tmp_path, existing_delta, long_source):
    from companion_daemon.world_v2.story_import import bind_import_context
    from companion_daemon.world_v2.character_prehistory import StoryEventMatchDraft, CrossStoryRelation
    from test_prehistory_memory_source import _read
    from test_prehistory_story_recall import _active_rows

    ledger = started_ledger(tmp_path / 'two-stories.sqlite')
    try:
        first = bind_import_context(story_archive().document, accepted_archives=(),
            world_start_ref='world-start', world_start_payload_hash=digest({}), world_started_at=START)
        if not existing_delta:
            first = story_archive().document
        if long_source:
            first = first.model_copy(update={'records': (first.records[0].model_copy(update={
                'statement': '她记得那次还伞的具体经过。' * 65,
            }), *first.records[1:])})
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
        assert new_id in readings[old.record_id].related_record_refs
        assert any(edge.right_record_id == new_id and edge.authority is not None
                   for edge in readings[old.record_id].story.relations)
        assert next(r.record for r in ledger.project().prehistory_records if r.record.record_id == old.record_id) == old
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
        from test_prehistory_memory_source import _choice, _corpus
        from test_prehistory_story_recall import _query
        from companion_daemon.world_v2.recall_index import InMemoryRecallIndex, FeatureHashRecallEmbedding
        from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
        query = _query(ledger).model_copy(update={'knowledge_as_of': datetime(2021, 1, 1, tzinfo=UTC)})
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        corpus = _corpus(ledger, _read(ledger))
        index.rebuild(cursor=query.cursor, documents=corpus)
        from companion_daemon.world_v2.recall_index import RecallQuery, RecallDocument
        window_query = _query(ledger).model_dump(mode='json')
        window_query['story_window'] = {'record_ref': old.record_id, 'start': 0}
        window_result = index.search(RecallQuery.model_validate_json(json.dumps(window_query)))
        old_window = next(hit.document for hit in window_result.hits
                          if hit.document.source_item_ref.split(':window:')[0] == old.record_id)
        assert new_id in old_window.prehistory.related_record_refs
        assert len(old_window.source_bindings) == 3
        assert (old_window.source_window_start is not None) == long_source
        assert RecallDocument.model_validate_json(old_window.model_dump_json()) == old_window
        extra_authority = next(edge.authority.event_ref for edge in old_window.prehistory.story.relations
                               if edge.authority is not None)
        from test_prehistory_chat_context import _capsule
        from test_visible_source_composer import _request
        from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
        from companion_daemon.world_v2.recall_runtime import (
            RecallCoordinator, augment_model_content_with_recall, verify_trusted_recall_trace, recall_evidence_json,
        )
        from companion_daemon.world_v2.character_interior.inbound_author import _with_visible_recall_traces
        from companion_daemon.world_v2.prehistory_claim_authority import prehistory_claim_bindings
        request = _request(_capsule(ledger, actor=ACTOR, trigger='fixture:cross-story-proof'))
        coordinator = RecallCoordinator.from_built_index(index=index, cursor=query.cursor, actor_ref=ACTOR,
            subject_refs=(ACTOR,), logical_time=query.at, trigger_ref=request.trigger_ref)
        try:
            trace = coordinator.recall(request=CharacterRecallRequest.model_validate_json(json.dumps({
                'query_text': '还伞', 'story_window': {'record_ref': old.record_id, 'start': 0}})),
                accessibility_seed='fixture:cross-story', expected_cursor=query.cursor, trigger_ref=request.trigger_ref)
            visible = json.loads(augment_model_content_with_recall(request.model_content_json,
                verify_trusted_recall_trace(trace)))
            visible['slices'].pop('active_memory_candidates', None)
            audit = verify_trusted_recall_trace(trace)
            evidence = json.loads(recall_evidence_json(audit))
            visible.setdefault('inner_life_snapshot', {})['materials'] = {'automatic_prefetch': {'items': [
                {**candidate, 'source_ref': hit.document.source_item_ref}
                for candidate, hit in zip(evidence['candidates'], audit.hits, strict=True)
            ]}}
            recalled_request = _with_visible_recall_traces(request.model_copy(update={
                'model_content_json': json.dumps(visible, ensure_ascii=False), 'visible_source_requirement_json': None,
            }), trace)
            claim_bindings = prehistory_claim_bindings(recalled_request)
            assert extra_authority in {b.ref for b in claim_bindings[old_window.source_item_ref]}
        finally:
            coordinator.close()
        closed = _corpus(ledger, _read(ledger), omitted=(extra_authority,))
        assert not any(d.source_item_ref.split(':window:')[0] == old.record_id for d in closed)
        historical = index.search(query).model_dump_json()
        assert new_id not in historical
        for hit in index.search(query).hits:
            assert len(hit.document.source_bindings) == 2
            assert RecallDocument.model_validate_json(hit.document.model_dump_json()) == hit.document
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


def test_structured_retention_replays_the_pre_p2_opportunity_bytes(tmp_path):
    from pathlib import Path
    from companion_daemon.world_v2.prehistory_memory_decision import initialization_opportunity
    from companion_daemon.world_v2.schemas import ProjectionCursor
    ledger = started_ledger(tmp_path / 'legacy-retention.sqlite')
    try:
        package = story_archive()
        rows = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(package, created_at=START)
        state = ledger.project()
        row = next(r for r in rows if r.record.record_id == package.document.records[0].record_id)
        actual = initialization_opportunity(world_id=ledger.world_id, row=row, archive=state.prehistory_archives[0],
            cursor=ProjectionCursor(world_revision=state.world_revision,
                deliberation_revision=state.deliberation_revision, ledger_sequence=state.ledger_sequence))
        # Captured with dbc0db32's original reader, before derived canonical
        # coordinates/edge proofs were added to current Recall readings.
        golden = Path(__file__).parents[1] / 'support/fixtures/prehistory-structured-retention-opportunity.json'
        assert actual.model_dump(mode='json') == json.loads(golden.read_text())
    finally:
        ledger.close()
