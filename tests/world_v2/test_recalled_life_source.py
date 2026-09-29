"""A settled source omitted from display still reaches recall and review."""
import json

import pytest
import pytest_asyncio

from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy, SliceBudget
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.model_facing_context import compact_model_facing_context
from companion_daemon.world_v2.recall_index import FeatureHashRecallEmbedding, InMemoryRecallIndex, RecallDocument, RecallCursor
from companion_daemon.world_v2.recall_model_reading import interior_recall_item
from companion_daemon.world_v2.recall_runtime import RecallCoordinator, verify_trusted_recall_trace
from companion_daemon.world_v2.schema_core import canonicalize_json_value
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.visible_recall_sources import supplement_recalled_sources
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from test_visible_source_composer import _request
from test_world_stimulus_life_intent import ACTOR, WORLD, _build, _model
from test_world_stimulus_life_response import _ResponseHTTP, _settled


@pytest_asyncio.fixture
async def recalled(tmp_path, monkeypatch):
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    path = tmp_path / 'source.sqlite'
    model = _model(_ResponseHTTP())
    app = _build(path, model)
    try:
        source = await _settled(app)
    finally:
        await app.aclose()
        await model.aclose()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        state = ledger.project()
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        recall = RecallCoordinator(index=index)
        policy = ContextCapsuleBudgetPolicy().model_copy(update={
            'world_life': SliceBudget(max_items=0, max_fields=8, max_characters=300),
            'recent_experiences': SliceBudget(max_items=0, max_fields=8, max_characters=300),
        })
        capsule = context_capsule_compiler_from_ledger(
            ledger=ledger, life_content_store=store, recall_coordinator=recall, policy=policy,
        ).compile(query_from_projection(state, actor_ref=ACTOR, trigger_ref=source.event_id))
        table = compile_visible_source_table(request=_request(capsule), capsule=capsule)
        assert '一阵短雨已经停了' not in table.payload_json
        cursor = RecallCursor(world_revision=state.world_revision, deliberation_revision=state.deliberation_revision,
                              ledger_sequence=state.ledger_sequence)
        trace = recall.prefetch(expected_cursor=cursor, query_text='一阵短雨已经停了',
                                accessibility_seed='life-test', trigger_ref=source.event_id)
        audit = verify_trusted_recall_trace(trace)
        assert audit.hits
        doc = next(h.document for h in audit.hits if h.document.settled_life is not None)
        reading = interior_recall_item(doc, index_version=audit.index_version)
        view = json.loads(compact_model_facing_context(json.dumps(canonicalize_json_value({
            'slices': {}, 'inner_life_snapshot': {'materials': {'reading': reading}},
        }), ensure_ascii=False)))['inner_life_snapshot']['materials']['reading']
        user = {'inner_life_snapshot': {'materials': {'automatic_prefetch': {'items': [view]}}}}
        author = json.dumps({'messages': [{'role': 'system', 'content': 'fixture'},
                                         {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}]})
        yield table, audit, doc, author
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('aliased', [False, True])
async def test_presented_life_reaches_review_with_exact_original_bindings(recalled, aliased):
    table, audit, doc, author = recalled
    if aliased:
        from companion_daemon.world_v2.reference_wire import prepare_reference_view
        value = json.loads(author)
        view, bindings = prepare_reference_view(json.loads(value['messages'][1]['content']))
        value['messages'][1]['content'] = json.dumps(view, ensure_ascii=False)
        value['identity_extras'] = {'reference_bindings': bindings}
        author = json.dumps(value, ensure_ascii=False)
    augmented, used = supplement_recalled_sources(table=table, audits=(audit,), author_request_json=author)
    assert used == (audit,)
    assert '一阵短雨已经停了' in augmented.payload_json
    rows = augmented.source_references()
    assert any(r.get('settled_life_support') for r in rows)
    assert 'settled_life' in interior_recall_item(doc)
    assert 'settled_life' not in interior_recall_item(doc, index_version='world-v2-recall-index.hybrid.9')
    legacy = doc.model_dump(mode='json', exclude={'settled_life_json', 'source_window_start'})
    assert 'settled_life_json' not in RecallDocument.model_validate_json(json.dumps(legacy)).model_dump(mode='json')


@pytest.mark.asyncio
async def test_unshown_or_old_policy_life_does_not_expand_review(recalled):
    table, audit, doc, author = recalled
    changed = json.loads(author)
    changed['messages'][1]['content'] = json.dumps({'inner_life_snapshot': {'materials': {}}})
    assert supplement_recalled_sources(table=table, audits=(audit,), author_request_json=json.dumps(changed)) == (table, ())
    old = audit.model_copy(update={'index_version': 'world-v2-recall-index.hybrid.9'})
    from companion_daemon.world_v2.recalled_life_source import presented_recalled_life
    assert presented_recalled_life(table=table, audits=(old,), materials={}) == ([], ())
    bad = audit.model_copy(update={'trigger_ref': 'different-trigger'})
    with pytest.raises(ValueError):
        supplement_recalled_sources(table=table, audits=(bad,), author_request_json=author)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['owner', 'hash', 'text', 'candidate', 'privacy'])
async def test_recalled_life_rejects_changed_source_carrier(recalled, fault):
    _, _, doc, _ = recalled
    value = doc.model_dump(mode='json')
    life = json.loads(value['settled_life_json'])
    if fault == 'owner':
        life['participant_refs'] = ['actor:other']
    elif fault == 'hash':
        life['source']['authority_payload_hash'] = 'f' * 64
    elif fault == 'text':
        value['text'] = '角色已外出买了伞。'
    elif fault == 'candidate':
        life['content']['world_consequence']['environment']['epistemic_scope'] = 'candidate_world_environment'
    else:
        life['privacy_class'] = 'withhold'
    value['settled_life_json'] = json.dumps(life)
    with pytest.raises(ValueError, match='recalled life'):
        RecallDocument.model_validate_json(json.dumps(value))
