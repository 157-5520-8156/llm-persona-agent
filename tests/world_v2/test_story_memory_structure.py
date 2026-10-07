"""Story structure through reviewed import, retained Memory and actual Recall packets.

Review/retention choices are explicit fixtures, not model-quality evidence.
"""
from datetime import UTC, datetime
import json

from companion_daemon.world_v2.character_prehistory import ReviewedPrehistoryArchive, digest
from companion_daemon.world_v2.prehistory_life_materials import LifeMaterialsDraft, export_actor_archive
from companion_daemon.world_v2.prehistory_story_links import (
    PrehistoryStoryLinkArtifact, apply_story_links,
)
from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
from companion_daemon.world_v2.recall_index import FeatureHashRecallEmbedding, InMemoryRecallIndex
from companion_daemon.world_v2.recall_runtime import (
    RecallCoordinator, recall_evidence_json, verify_trusted_recall_trace,
)
from test_character_prehistory import ACTOR, START, reviewed_archive, started_ledger
from test_life_projection import WORLD_ID
from test_prehistory_memory_source import _corpus, _read
from test_prehistory_story_links import _materials, _draft, STORY
from test_prehistory_story_recall import _active_rows, _query


def story_archive():
    raw = _materials().model_dump_json().replace('agent:companion', ACTOR)
    materials = LifeMaterialsDraft.model_validate_json(raw).model_copy(update={
        'world_id': WORLD_ID, 'world_started_at': START,
    })
    # She only learns the later message in 2020, about an event in 2016.
    later = materials.recollections[1].model_copy(update={
        'known_from': datetime(2020, 1, 1, tzinfo=UTC),
        'known_until': datetime(2020, 12, 31, tzinfo=UTC),
    })
    materials = materials.model_copy(update={'recollections': (materials.recollections[0], later)})
    draft = _draft(materials).model_copy(update={'actor_ref': ACTOR})
    linked = apply_story_links(materials, actor_ref=ACTOR, draft=draft, narrative_text=STORY)
    artifact = PrehistoryStoryLinkArtifact(
        request_hash='a' * 64, source_artifact_ref=materials.source_artifact_ref,
        source_materials_hash=digest(materials), linked_materials_hash=digest(linked), draft=draft,
    )
    document = export_actor_archive(
        linked, ACTOR, 'prehistory-archive:structured-story',
        preserve_story_structure=True, story_links=artifact,
    )
    return ReviewedPrehistoryArchive(document=document, review=reviewed_archive().review.model_copy(
        update={'manifest_hash': digest(document.manifest())},
    ))


def test_import_replay_and_real_recall_preserve_story_event_and_knowledge(tmp_path):
    ledger = started_ledger(tmp_path / 'story.sqlite')
    coordinator = None
    try:
        archive = story_archive()
        active = _active_rows(ledger, archive)
        assert archive.document.contract == 'character-prehistory-archive.2'
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
        documents = _corpus(ledger, _read(ledger))
        query = _query(ledger)
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(cursor=query.cursor, documents=documents)
        coordinator = RecallCoordinator.from_built_index(
            index=index, cursor=query.cursor, actor_ref=ACTOR, subject_refs=(ACTOR,),
            logical_time=query.at, trigger_ref='fixture:story',
        )
        trace = coordinator.recall(
            request=CharacterRecallRequest(query_text='还伞', link_refs=(
                active['prehistory-record:umbrella'].candidate_id,
            )), accessibility_seed='fixture:story', expected_cursor=query.cursor,
            trigger_ref='fixture:story',
        )
        packet = json.loads(recall_evidence_json(verify_trusted_recall_trace(trace)))
        stories = [item['prehistory']['story'] for item in packet['candidates']]
        later = next(item for item in stories if item['known_from'].startswith('2020'))
        assert later['blocks'][0]['event_ref'] == 'event:callback'
        assert later['blocks'][0]['occurred_from'].startswith('2016')
        assert later['blocks'][0]['source_locator'] == 'source-segment:0002'
        assert any(edge['relation_kind'] == 'later_disclosure'
                   for item in stories for edge in item['relations'])
        assert '另一人私下想法' not in json.dumps(packet, ensure_ascii=False)
        assert 'bridge_source_quote' not in json.dumps(packet)
        assert 'source_quote"' not in json.dumps(packet)
        assert all(item['coverage'] == 'bounded_record_material' for item in stories)
    finally:
        if coordinator:
            coordinator.close()
        ledger.close()


def test_as_of_excludes_later_disclosure_and_its_link_even_in_historical_mode(tmp_path):
    ledger = started_ledger(tmp_path / 'past.sqlite')
    coordinator = None
    try:
        active = _active_rows(ledger, story_archive())
        query = _query(ledger)
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        coordinator = RecallCoordinator.from_built_index(
            index=index, cursor=query.cursor, actor_ref=ACTOR, subject_refs=(ACTOR,),
            logical_time=query.at, trigger_ref='fixture:past',
        )
        trace = coordinator.recall(
            request=CharacterRecallRequest(
                query_text='还伞', link_refs=(active['prehistory-record:umbrella'].candidate_id,),
                knowledge_as_of=datetime(2018, 1, 1, tzinfo=UTC), include_historical=True,
            ), accessibility_seed='fixture:past', expected_cursor=query.cursor,
            trigger_ref='fixture:past',
        )
        packet = json.loads(recall_evidence_json(verify_trusted_recall_trace(trace)))
        assert len(packet['candidates']) == 1
        assert 'prehistory-record:later-message' not in json.dumps(packet)
        assert '后来她说' not in json.dumps(packet, ensure_ascii=False)
        assert packet['candidates'][0]['prehistory']['story']['relations'] == []
    finally:
        if coordinator:
            coordinator.close()
        ledger.close()


def test_occurrence_filter_uses_event_time_not_later_knowledge(tmp_path):
    ledger = started_ledger(tmp_path / 'occurrence.sqlite')
    try:
        active = _active_rows(ledger, story_archive())
        query = _query(ledger).model_copy(update={
            'query_text': '后来消息',
            'link_refs': (active['prehistory-record:later-message'].candidate_id,),
            'occurred_from': datetime(2016, 1, 1, tzinfo=UTC),
            'occurred_to': datetime(2016, 12, 31, 23, 59, 59, tzinfo=UTC),
        })
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        assert any(hit.document.source_item_ref == 'prehistory-record:later-message'
                   for hit in index.search(query).hits)
    finally:
        ledger.close()


def test_forgetting_removes_story_edges_from_memory_and_recall(tmp_path):
    from test_prehistory_memory_source import _choice
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding

    ledger = started_ledger(tmp_path / 'forget.sqlite')
    try:
        active = _active_rows(ledger, story_archive())
        row = next(row for row in ledger.project().prehistory_records
                   if row.record.record_id == 'prehistory-record:later-message')
        _choice(ledger, prehistory_memory_binding(row),
                before=active[row.record.record_id], status='forgotten',
                candidate_id=active[row.record.record_id].candidate_id)
        reading = _read(ledger)
        assert 'prehistory-record:later-message' not in json.dumps([
            item.model_dump(mode='json') for item in reading.items
        ])
        assert _read(ledger, actor_ref='actor:other').items == ()
        assert _read(ledger, privacy='public').items == ()
    finally:
        ledger.close()


def test_structured_history_cannot_smuggle_runtime_event_with_old_knowledge(tmp_path):
    import pytest
    from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime

    ledger = started_ledger(tmp_path / 'future.sqlite')
    try:
        archive = story_archive()
        raw = archive.model_dump(mode='json')
        # Intention can concern the future, but occurrence is when intention
        # was held, not when its imagined outcome might happen.
        block = raw['document']['records'][0]['story']['blocks'][0]
        block.update(kind='intention', occurred_from=START.isoformat(), occurred_until=START.isoformat())
        from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
        document = PrehistoryArchiveDocument.model_validate_json(json.dumps(raw['document']))
        raw['review']['manifest_hash'] = digest(document.manifest())
        archive = ReviewedPrehistoryArchive.model_validate_json(json.dumps(raw))
        before = ledger.project().semantic_hash
        with pytest.raises(ValueError, match='runtime|World start|story'):
            PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
                archive, created_at=START,
            )
        assert ledger.project().semantic_hash == before
    finally:
        ledger.close()


def test_frozen_three_line_oracle_survives_structured_export():
    from pathlib import Path
    import hashlib

    root = Path(__file__).resolve().parents[2]
    oracle = json.loads((root / 'fixtures/world_v2/long_story_oracle_v1.json').read_text())
    for key in ('source', 'materials'):
        assert hashlib.sha256((root / oracle[key + '_path']).read_bytes()).hexdigest() == oracle[key + '_sha256']
    materials = LifeMaterialsDraft.model_validate_json((root / oracle['materials_path']).read_text())
    archive = export_actor_archive(materials, oracle['actor_ref'], 'prehistory-archive:oracle',
                                   preserve_story_structure=True)
    records = {row.record_id: row for row in archive.records}
    for line in oracle['lines']:
        events = {block.event_ref for ref in line['records'] for block in records[ref].story.blocks}
        assert set(line['required_events']) <= events
    all_blocks = {block.block_id for record in archive.records for block in record.story.blocks}
    assert not set(oracle['actor_private_block_ids']) & all_blocks
    for perspective in oracle['perspectives']:
        before = export_actor_archive(materials, oracle['actor_ref'], 'prehistory-archive:oracle-past',
                                      as_of=datetime.fromisoformat(perspective['knowledge_as_of']),
                                      preserve_story_structure=True)
        ids = {record.record_id for record in before.records}
        assert set(perspective['must_include']) <= ids
        assert not set(perspective['must_exclude']) & ids


def test_legacy_archive_bytes_and_review_hashes_do_not_change():
    from pathlib import Path
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument

    root = Path(__file__).resolve().parents[2]
    path = root / 'fixtures/world_v2/celia_lifeline_prehistory_unreviewed.json'
    raw = json.loads(path.read_text())
    archive = PrehistoryArchiveDocument.model_validate_json(path.read_text())
    assert archive.model_dump(mode='json') == raw
    assert digest(archive) == digest(raw)
    assert all('story' not in record.model_dump(mode='json') for record in archive.records)


def test_cli_exports_new_structure_without_reusing_old_review(tmp_path):
    from scripts.prepare_life_materials_prehistory import main
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument

    materials = _materials()
    source = tmp_path / 'materials.json'
    source.write_text(materials.model_dump_json())
    output = tmp_path / 'archive.json'
    main(['--materials', str(source), '--actor-ref', materials.actors[0].actor_ref,
          '--archive-id', 'prehistory-archive:cli-structured', '--output', str(output),
          '--preserve-story-structure'])
    archive = PrehistoryArchiveDocument.model_validate_json(output.read_text())
    assert archive.contract == 'character-prehistory-archive.2'
    assert archive.records[0].story.blocks[0].source_quote == '她忘带伞，他追到车站把伞还给她。'
    assert 'review' not in json.loads(output.read_text())


def test_role_can_expand_an_exact_long_record_window_without_new_source_access(tmp_path):
    from companion_daemon.world_v2.recall_model_reading import interior_recall_item
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
    from test_prehistory_memory_source import _choice
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding

    ledger = started_ledger(tmp_path / 'expand.sqlite')
    coordinator = None
    try:
        archive = story_archive()
        raw = archive.document.model_dump(mode='json')
        raw['records'][0]['statement'] = '还伞的经过。' + '她记得那段具体经历。' * 110 + '末尾她把伞带回家。'
        document = PrehistoryArchiveDocument.model_validate_json(json.dumps(raw))
        archive = ReviewedPrehistoryArchive(document=document, review=archive.review.model_copy(
            update={'manifest_hash': digest(document.manifest())},
        ))
        active = _active_rows(ledger, archive)
        query = _query(ledger)
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        coordinator = RecallCoordinator.from_built_index(
            index=index, cursor=query.cursor, actor_ref=ACTOR, subject_refs=(ACTOR,),
            logical_time=query.at, trigger_ref='fixture:expand',
        )
        request = CharacterRecallRequest.model_validate_json(json.dumps({
            'query_text': '继续读这段经历',
            'story_window': {'record_ref': 'prehistory-record:umbrella', 'start': 416},
        }))
        trace = coordinator.recall(request=request, accessibility_seed='fixture:expand',
                                   expected_cursor=query.cursor, trigger_ref='fixture:expand')
        audit = verify_trusted_recall_trace(trace)
        hit, = audit.hits
        assert hit.document.text == raw['records'][0]['statement'][416:896]
        item = interior_recall_item(hit.document, index_version=audit.index_version)
        assert item['story_window']['next'] == {'record_ref': 'prehistory-record:umbrella', 'start': 832}
        assert item['story_window']['complete_record'] is False
        row = next(row for row in ledger.project().prehistory_records
                   if row.record.record_id == 'prehistory-record:umbrella')
        _choice(ledger, prehistory_memory_binding(row), before=active[row.record.record_id],
                status='forgotten', candidate_id=active[row.record.record_id].candidate_id)
        after_query = _query(ledger)
        index.rebuild(cursor=after_query.cursor, documents=_corpus(ledger, _read(ledger)))
        assert index.search(after_query.model_copy(update={'story_window': request.story_window})).hits == ()
    finally:
        if coordinator:
            coordinator.close()
        ledger.close()


def test_budget_limited_story_keeps_only_eligible_continuation_coordinates(tmp_path):
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
    from companion_daemon.world_v2.prehistory_story import StoryWindowRequest

    ledger = started_ledger(tmp_path / 'frontier.sqlite')
    try:
        archive = story_archive()
        raw = archive.document.model_dump(mode='json')
        # Enough retained scenes to exceed the fixed six-hit envelope. Keep
        # exact source/knowledge fixtures; synthetic names are not story facts.
        first = raw['records'][0]
        first['story']['relations'] = []
        records = []
        for i in range(8):
            record = json.loads(json.dumps(first))
            record['record_id'] = f'prehistory-record:chain-{i}'
            record['statement'] = f'还伞的第{i}段。'
            record['related_record_refs'] = [f'prehistory-record:chain-{j}' for j in (i-1, i+1) if 0 <= j < 8]
            records.append(record)
        raw['records'] = records
        document = PrehistoryArchiveDocument.model_validate_json(json.dumps(raw))
        package = ReviewedPrehistoryArchive(document=document, review=archive.review.model_copy(
            update={'manifest_hash': digest(document.manifest())},
        ))
        active = _active_rows(ledger, package)
        query = _query(ledger).model_copy(update={'query_text': '还伞',
            'link_refs': (active['prehistory-record:chain-0'].candidate_id,)})
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        returned = {hit.document.source_item_ref for hit in result.hits}
        frontier = {ref for hit in result.hits for ref in hit.document.prehistory.related_record_refs} - returned
        assert frontier, 'A bounded graph read must expose a legal continuation, not silently terminate the story.'
        next_ref = sorted(frontier)[0]
        continued = index.search(query.model_copy(update={
            'story_window': StoryWindowRequest(record_ref=next_ref, start=0),
        }))
        assert len(continued.hits) == 1 and continued.hits[0].document.source_item_ref == next_ref
    finally:
        ledger.close()


def test_legacy_related_record_can_be_read_but_has_no_historical_knowledge_proof(tmp_path):
    from test_prehistory_story_recall import _linked_archive
    from companion_daemon.world_v2.prehistory_story import StoryWindowRequest

    ledger = started_ledger(tmp_path / 'legacy-continuation.sqlite')
    try:
        _active_rows(ledger, _linked_archive())
        query = _query(ledger).model_copy(update={
            'query_text': '继续读',
            'story_window': StoryWindowRequest(record_ref='prehistory-record:quiet-video', start=0),
        })
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        hit, = index.search(query).hits
        assert '二十秒视频' in hit.document.text
        assert index.search(query.model_copy(update={
            'knowledge_as_of': datetime(2026, 1, 1, tzinfo=UTC),
        })).hits == ()
    finally:
        ledger.close()


def test_current_slim_transport_carries_story_reading_controls():
    from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts
    from companion_daemon.world_v2.expression_draft import qq_expression_capabilities
    from test_inbound_atomic_result_v2 import _recall

    payload = {key: value for key, value in _recall().items()
               if key in {'private_turn_state', 'recall_request'}}
    payload['recall_request'].update(
        knowledge_as_of='2018-01-01T00:00:00Z',
        story_window={'record_ref': 'prehistory-record:umbrella', 'start': 416},
    )
    for native in (False, True):
        contract = InboundToolContracts(native_slim_core=native).contract_for(
            phase='initial', capabilities=qq_expression_capabilities('napcat'),
            recall_allowed=True, atomic_envelope_version='slim', schema_dialect='deepseek-strict',
        )
        carrier = {'result_kind': 'recall', 'payload_json': json.dumps(payload)}
        wire = {'result': carrier} if native else carrier
        parsed = json.loads(contract.unwrap(json.dumps(wire)))
        request = CharacterRecallRequest.model_validate_json(json.dumps(parsed['recall_request']))
        assert request.story_window.start == 416
        assert request.knowledge_as_of == datetime(2018, 1, 1, tzinfo=UTC)


def test_compact_story_policy_keeps_semantics_and_source_detail_expandable(tmp_path):
    from companion_daemon.world_v2.recall_model_reading import interior_recall_item
    from companion_daemon.world_v2.prehistory_story import StoryWindowRequest
    ledger = started_ledger(tmp_path / 'compact-story.sqlite')
    try:
        active = _active_rows(ledger, story_archive())
        query = _query(ledger).model_copy(update={'query_text': '还伞', 'link_refs': (
            active['prehistory-record:umbrella'].candidate_id,
        )})
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.15')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        hit = next(hit for hit in result.hits if hit.document.source_item_ref == 'prehistory-record:umbrella')
        view = interior_recall_item(hit.document, index_version=result.index_version)
        assert view['prehistory']['story']['known_from'].startswith('2015')
        assert view['prehistory']['story']['blocks'][0]['event_ref'] == 'event:umbrella'
        assert 'archive_payload_hash' not in view['prehistory']
        assert hit.document.retrieval_text is None
        assert len(hit.document.source_bindings) == 2
        from companion_daemon.world_v2.recall_index import RecallResult
        assert RecallResult.model_validate_json(result.model_dump_json()) == result
        detail, = index.search(query.model_copy(update={'story_window': StoryWindowRequest(
            record_ref='prehistory-record:umbrella', start=0,
        )})).hits
        assert detail.document.prehistory.story.blocks[0].source_locator == 'source-segment:0001'
    finally:
        ledger.close()


def test_rich_five_scene_chain_fits_existing_budgets_with_compact_policy(tmp_path):
    from test_prehistory_story_recall import _linked_archive
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
    from companion_daemon.world_v2.recall_model_reading import interior_recall_item
    from companion_daemon.world_v2.recall_index import RECALL_STORY_MODEL_READING_MAX_BYTES, RECALL_STORY_RESULT_MAX_BYTES
    ledger = started_ledger(tmp_path / 'rich-chain.sqlite')
    try:
        base = _linked_archive()
        raw = base.document.model_dump(mode='json')
        raw['contract'] = 'character-prehistory-archive.2'
        for row in raw['records']:
            row['story'] = {
                'actor_ref': ACTOR, 'source_artifact_ref': 'narrative#sha256=' + 'b' * 64,
                'known_from': row['occurred_from'], 'known_until': row['occurred_until'],
                'blocks': [{'block_id': f"block:{row['record_id']}-{i}",
                    'event_ref': f'event:scene-{i}', 'kind': 'event',
                    'text': row['statement'], 'source_quote': row['statement'],
                    'source_locator': f'source-segment:{i:04d}'} for i in range(8)],
            }
        document = PrehistoryArchiveDocument.model_validate_json(json.dumps(raw))
        _active_rows(ledger, ReviewedPrehistoryArchive(document=document,
            review=base.review.model_copy(update={'manifest_hash': digest(document.manifest())})))
        query = _query(ledger)
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.15')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        assert {h.document.source_item_ref for h in result.hits} == {r.record_id for r in document.records}
        def size(value):
            return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        assert interior_recall_item(result.hits[0].document, index_version=result.index_version)['prehistory']['location_ref'] == document.records[0].location_ref
        assert size({'items': [interior_recall_item(h.document, index_version=result.index_version)
                               for h in result.hits]}) <= RECALL_STORY_MODEL_READING_MAX_BYTES
        assert size([h.model_dump(mode='json') for h in result.hits]) <= RECALL_STORY_RESULT_MAX_BYTES
    finally:
        ledger.close()


def test_next_authoring_brief_preserves_accepted_structured_history(tmp_path):
    from pathlib import Path
    from prepare_character_prehistory import brief_from_database
    db = tmp_path / 'next-brief.sqlite'
    ledger = started_ledger(db)
    archive = story_archive()
    try:
        _active_rows(ledger, archive)
        brief = brief_from_database(database=db,
            profile_path=Path(__file__).resolve().parents[2] / 'configs/character.yaml',
            world_id=WORLD_ID, actor_ref=ACTOR, born_at=datetime(2005, 4, 12, tzinfo=UTC),
            author_ref='author:next-story', source_ref='fixture:next-story')
        accepted, = brief.accepted_archives
        assert accepted.contract == 'character-prehistory-archive.2'
        assert accepted.manifest() == archive.document.manifest()
    finally:
        ledger.close()


def test_role_can_use_the_presented_record_reference_as_a_recall_anchor(tmp_path):
    ledger = started_ledger(tmp_path / 'record-anchor.sqlite')
    try:
        _active_rows(ledger, story_archive())
        query = _query(ledger).model_copy(update={'query_text': 'quuxzzyy',
            'link_refs': ('prehistory-record:umbrella',)})
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.15')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        result = index.search(query)
        anchor = next(hit for hit in result.hits if hit.document.source_item_ref == 'prehistory-record:umbrella')
        assert 'structured' in anchor.match_channels and anchor.structured_score_bp > 0
    finally:
        ledger.close()


def test_role_recall_expands_pinned_source_aliases_before_validation():
    from companion_daemon.world_v2.character_interior.inbound_wire import parse_character_recall_request
    from companion_daemon.world_v2.expression_draft import SourceRefAliasTable, qq_expression_capabilities
    from test_character_interior_inbound_wire import _qq_request
    canonical = 'prehistory-record:umbrella'
    aliases = SourceRefAliasTable(entries=(('@r:145', canonical),), canonical_refs=frozenset({canonical}))
    request = parse_character_recall_request(
        json.dumps({'recall_request': {'query_text': '接着读', 'link_refs': ['@r:145'],
                                      'story_window': {'record_ref': '@r:145', 'start': 0}}}),
        request=_qq_request(), capabilities=qq_expression_capabilities('napcat').model_copy(
            update={'private_turn_state_mode': 'optional'}), source_ref_aliases=aliases,
    )
    assert request.link_refs == (canonical,) and request.story_window.record_ref == canonical
