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
        value['inner_life_snapshot'] = {'materials': materials}
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
