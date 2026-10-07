"""Exact compact story readings must retain their past-world source capability."""
import json

import pytest

from companion_daemon.world_v2.prehistory_claim_authority import prehistory_claim_bindings
from companion_daemon.world_v2.recall_index import FeatureHashRecallEmbedding, InMemoryRecallIndex
from companion_daemon.world_v2.recall_runtime import RecallCoordinator, verify_trusted_recall_trace
from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
from companion_daemon.world_v2.recall_model_reading import interior_recall_item
from companion_daemon.world_v2.visible_source_runtime import compile_requirement
from companion_daemon.world_v2.character_interior.inbound_author import _with_visible_recall_traces
from companion_daemon.world_v2.model_facing_context import compact_chat_model_facing_context
from test_character_prehistory import ACTOR, started_ledger
from test_story_memory_structure import story_archive
from test_prehistory_story_recall import _active_rows, _query
from test_prehistory_memory_source import _read, _corpus
from test_prehistory_chat_context import _capsule
from test_visible_source_composer import _request


@pytest.mark.parametrize('packed', [False, True])
@pytest.mark.parametrize('mode', ['prefetch', 'character_pull'])
def test_presented_compact_story_keeps_reviewed_claim_permission(tmp_path, packed, mode):
    ledger = started_ledger(tmp_path / 'compact-claims.sqlite')
    coordinator = None
    try:
        active = _active_rows(ledger, story_archive())
        capsule = _capsule(ledger, actor=ACTOR, trigger='fixture:compact-native-claims')
        original = _request(capsule)
        request = original.model_copy(update={'visible_source_requirement_json':
            compile_requirement(request=original, capsule=capsule)})
        query = _query(ledger).model_copy(update={'query_text': '还伞'})
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.15')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        coordinator = RecallCoordinator.from_built_index(index=index, cursor=query.cursor,
            actor_ref=ACTOR, subject_refs=(ACTOR,), logical_time=query.at, trigger_ref=request.trigger_ref)
        if mode == 'prefetch':
            trusted = coordinator.prefetch(query_text='还伞',
                link_refs=(active['prehistory-record:umbrella'].candidate_id,),
                accessibility_seed='fixture:compact-native-claims', expected_cursor=query.cursor,
                trigger_ref=request.trigger_ref)
        else:
            trusted = coordinator.recall(request=CharacterRecallRequest(query_text='还伞',
                link_refs=(active['prehistory-record:umbrella'].candidate_id,)),
                accessibility_seed='fixture:compact-native-claims', expected_cursor=query.cursor,
                trigger_ref=request.trigger_ref)
        audit = verify_trusted_recall_trace(trusted)
        assert any(h.document.source_item_ref == 'prehistory-record:umbrella' for h in audit.hits)
        value = json.loads(request.model_content_json)
        value['slices'].pop('active_memory_candidates', None)
        reading = {'items': [interior_recall_item(h.document, index_version=audit.index_version)
                             for h in audit.hits]}
        materials = ({'automatic_prefetch': reading} if mode == 'prefetch' else
                     {'selected_recall': {'content': reading}})
        if packed:
            from companion_daemon.world_v2.shared_string_view import pack_shared_strings
            materials = pack_shared_strings(materials)
        value['inner_life_snapshot'] = {'materials': materials,
            'snapshot_compiler': {'availability': 'available', 'value': 'inner-life-snapshot-compiler.29'}}
        request = _with_visible_recall_traces(request.model_copy(update={
            'model_content_json': compact_chat_model_facing_context(json.dumps(value))}), trusted)
        refs = prehistory_claim_bindings(request)
        assert 'prehistory-record:umbrella' in refs
        assert not prehistory_claim_bindings(request.model_copy(update={'visible_source_recall_traces': ()}))
        changed = json.loads(request.model_content_json)
        changed['inner_life_snapshot']['materials'] = {}
        assert not prehistory_claim_bindings(request, context=changed)
        changed = json.loads(request.model_content_json)
        if packed:
            from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
            changed['inner_life_snapshot']['materials'] = unpack_shared_strings(
                changed['inner_life_snapshot']['materials'])
        changed_reading = (changed['inner_life_snapshot']['materials']['automatic_prefetch']
            if mode == 'prefetch' else changed['inner_life_snapshot']['materials']['selected_recall']['content'])
        for item in changed_reading['items']:
            item['text'] += '她其实去了别的地方。'
        assert not prehistory_claim_bindings(request, context=changed)
    finally:
        if coordinator:
            coordinator.close()
        ledger.close()


def test_mixed_legacy_receipt_keeps_its_original_source_table(tmp_path):
    from test_prehistory_story_recall import _linked_archive
    from companion_daemon.world_v2.visible_recall_sources import supplement_recalled_prehistory
    from companion_daemon.world_v2.visible_source_runtime import requirement_table
    from companion_daemon.world_v2.schema_core import canonicalize_json_value

    ledger = started_ledger(tmp_path / 'mixed-legacy-claims.sqlite')
    coordinator = None
    try:
        legacy = _active_rows(ledger, _linked_archive())
        compact = _active_rows(ledger, story_archive())
        capsule = _capsule(ledger, actor=ACTOR, trigger='fixture:mixed-legacy-claims')
        request = _request(capsule)
        table = requirement_table(compile_requirement(request=request, capsule=capsule))
        query = _query(ledger)
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding(),
                                   policy_version='world-v2-recall-index.hybrid.15')
        index.rebuild(cursor=query.cursor, documents=_corpus(ledger, _read(ledger)))
        coordinator = RecallCoordinator.from_built_index(index=index, cursor=query.cursor,
            actor_ref=ACTOR, subject_refs=(ACTOR,), logical_time=query.at, trigger_ref=request.trigger_ref)
        trusted = coordinator.prefetch(query_text='蓝雨伞 还伞', limit=8,
            link_refs=(legacy['prehistory-record:blue-umbrella'].candidate_id,
                       compact['prehistory-record:umbrella'].candidate_id),
            accessibility_seed='fixture:mixed-legacy-claims', expected_cursor=query.cursor,
            trigger_ref=request.trigger_ref)
        audit = verify_trusted_recall_trace(trusted)
        shown = [interior_recall_item(h.document, index_version=audit.index_version) for h in audit.hits]
        assert {'prehistory-record:blue-umbrella', 'prehistory-record:umbrella'} <= {
            item['source_ref'] for item in shown}
        tables = {}
        for version in (28, 29):
            view = canonicalize_json_value({'slices': {}, 'inner_life_snapshot': {
                'snapshot_compiler': {'value': f'inner-life-snapshot-compiler.{version}'},
                'materials': {'automatic_prefetch': {'items': shown}}}})
            view = json.loads(compact_chat_model_facing_context(json.dumps(view)))
            result, used = supplement_recalled_prehistory(table=table, audits=(audit,),
                author_request_json=json.dumps({'messages': [{'content': ''}, {'content': json.dumps(view)}]}))
            assert used == (audit,)
            tables[version] = result.as_dict()
        def added_refs(table_value):
            return {wrapped['material']['item']['item_ref'] for wrapped in table_value['source_materials']
                    if wrapped['material'].get('lane') == 'recalled_prehistory'}
        assert 'prehistory-record:blue-umbrella' in added_refs(tables[28])
        assert 'prehistory-record:umbrella' not in added_refs(tables[28])
        assert 'prehistory-record:umbrella' in added_refs(tables[29])
        # This is the mixed audit that the old receipt really retained: its
        # legacy rows keep identical bytes, despite the new version's extra row.
        old_rows = {w['material']['item']['item_ref']: w['material']
                    for w in tables[28]['source_materials'] if w['material'].get('lane') == 'recalled_prehistory'}
        new_rows = {w['material']['item']['item_ref']: w['material']
                    for w in tables[29]['source_materials'] if w['material'].get('lane') == 'recalled_prehistory'}
        assert all(new_rows[ref] == row for ref, row in old_rows.items())
    finally:
        if coordinator:
            coordinator.close()
        ledger.close()
