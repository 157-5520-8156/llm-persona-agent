"""Typed settlement producer -> pinned Capsule -> compact view -> .28 snapshot.

All provider transports are local fixtures. Labels add no execution/perception
facts and never alter a source Capsule or a previously stored snapshot.
"""
from copy import deepcopy
import json

import pytest
import pytest_asyncio

from companion_daemon.world_v2.character_interior.contracts import _InteriorBinding
from companion_daemon.world_v2.character_interior.life_context_presentation import SETTLED_WORLD_SCOPE
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    _is_typed_settled_world_item, compile_inner_life_snapshot, source_envelopes_from_capsule,
)
from companion_daemon.world_v2.life_content import LifeContentCompiler
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.model_facing_context import _semantic_value
from companion_daemon.world_v2.present_prompt import order_user_present_payload
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_life_context import WorldLifeContextCompiler, WorldLifeContextItem
from test_current_activity_context import current_context
from test_derived_material_redaction import _recreate
from test_world_stimulus_life_intent import ACTOR, WORLD, _build, _model
from test_world_stimulus_life_response import _ResponseHTTP, _settled


@pytest_asyncio.fixture
async def settled_chain(tmp_path, monkeypatch):
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    path = tmp_path / 'typed-settlement.sqlite'
    provider = _ResponseHTTP()
    model = _model(provider)
    app = _build(path, model)
    ledger = store = None
    try:
        settlement = await _settled(app)
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
        store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
        state = ledger.project()
        cursor = ProjectionCursor(world_revision=state.world_revision,
            deliberation_revision=state.deliberation_revision, ledger_sequence=state.ledger_sequence)
        worlds = WorldLifeContextCompiler(life_content=LifeContentCompiler(store=store)).compile(
            projection=state, cursor=cursor, actor_ref=ACTOR, viewer_privacy_ceiling='private')
        world = next(item for item in worlds if isinstance(item, WorldLifeContextItem))
        capsule, context, snapshot = current_context(ledger, store, actor_ref=ACTOR)
        yield settlement, world, capsule, context, snapshot, ledger, store
    finally:
        if store is not None:
            store.close()
        if ledger is not None:
            ledger.close()
        await app.aclose()
        await model.aclose()


def world_item(chain):
    _, world, _, context, *_ = chain
    return next(item for item in context['slices']['world_life']['items']
                if item['source_ref'] == world.occurrence_id)


@pytest.mark.asyncio
async def test_real_settlement_without_discriminator_reaches_both_display_lanes(settled_chain):
    settlement, world, capsule, context, snapshot, ledger, store = settled_chain
    assert world.source.authority_event_ref == settlement.event_id
    assert 'context_kind' not in world.model_dump(mode='json')
    assert world.content.world_consequence.authorized_attempt_result is None
    assert world.content.character_response is None
    before = capsule.model_dump_json()
    source = world_item(settled_chain)
    assert 'context_kind' not in source['value']  # This is the real .26/.27 missed branch.
    assert 'result_payload_hash' not in source['value']  # Production chat compaction ran.
    frozen_item = next(item for item in capsule.world_life.items if item.item_ref == world.occurrence_id)
    assert 'context_kind' not in json.loads(frozen_item.payload_json)
    assert snapshot.snapshot_compiler.value == 'inner-life-snapshot-compiler.28'
    displayed = snapshot.model_view()['materials']
    entry = next(item for item in displayed['recent_self_experiences']['items']
                 if item['source_ref'] == world.occurrence_id)
    reading = next(row for day in displayed['week_diary'] for row in day.get('readings', [])
                   if row['source_ref'] == world.occurrence_id)
    for item in (entry, reading):
        assert item['context_kind'] == 'settled_world_occurrence'
        assert item['epistemic_scope'] == SETTLED_WORLD_SCOPE
        assert item['settled_at'] == source['value']['settled_at']
    assert entry['content'] == _semantic_value(world.content.model_dump(mode='json'))
    assert reading['world_consequence'] == entry['content']['world_consequence']
    assert 'authorized_attempt_result' not in reading['world_consequence']
    assert 'character_response' not in reading
    assert entry['participant_refs'] == list(world.participant_refs)
    assert entry['location_ref'] == world.location_ref
    assert entry['privacy_class'] == world.privacy_class
    assert world.content.world_consequence.environment.text not in displayed.get('lived_moment', '')
    bound = next(row for row in snapshot.source_inventory
                 if row.source_ref == world.occurrence_id and row.scope == 'recent_self_experiences')
    assert any(binding.ref == settlement.event_id and binding.immutable_hash == settlement.payload_hash
               for binding in bound.authority_bindings)
    assert capsule.model_dump_json() == before
    assert ledger.project().world_revision == snapshot.cursor.world_revision
    # Recompilation is deterministic, without changing the Capsule's complete raw evidence.
    again = compile_inner_life_snapshot(context, source_envelopes=source_envelopes_from_capsule(capsule))
    assert again.model_dump_json() == snapshot.model_dump_json()


@pytest.mark.asyncio
async def test_typed_label_does_not_bypass_actor_or_display_privacy(settled_chain):
    _, world, _, _, snapshot, ledger, store = settled_chain
    visible = frozenset(ref for ref in snapshot.source_refs if ref != world.occurrence_id)
    redacted = json.dumps(snapshot.model_view(visible_source_refs=visible), ensure_ascii=False)
    assert world.content.world_consequence.environment.text not in redacted
    _, _, other = current_context(ledger, store, actor_ref='actor:unrelated')
    assert world.occurrence_id not in other.source_refs
    assert world.content.world_consequence.environment.text not in other.materials_json


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['unknown_shape', 'claimed_kind', 'missing_required', 'wrong_ref',
                                  'wrong_value_hash', 'changed_visible', 'changed_binding'])
async def test_no_settlement_classification_from_unknown_malformed_or_substituted_value(settled_chain, fault):
    _, world, capsule, _, _, _, _ = settled_chain
    item = deepcopy(world_item(settled_chain))
    envelopes = deepcopy(source_envelopes_from_capsule(capsule))
    if fault == 'unknown_shape':
        item['value'] = {'occurrence_id': world.occurrence_id, 'settled_at': '2026-08-16T14:00:00+08:00',
                         'context_kind': 'settled_world_occurrence'}
        envelopes = None
    elif fault == 'claimed_kind':
        item['value'] = {**world.model_dump(mode='json'), 'context_kind': 'settled_world_occurrence'}
        envelopes = None
    elif fault == 'missing_required':
        item['value'] = world.model_dump(mode='json')
        item['value'].pop('source')
        envelopes = None
    elif fault == 'wrong_ref':
        item['source_ref'] = 'occurrence:someone-else'
        item['value'] = world.model_dump(mode='json')
    elif fault == 'wrong_value_hash':
        envelopes[world.occurrence_id]['value_hash'] = '0' * 64
    elif fault == 'changed_visible':
        item['value']['content']['world_consequence']['environment']['text'] = 'A substituted event.'
    elif fault == 'changed_binding':
        envelopes[world.occurrence_id]['value']['source']['authority_payload_hash'] = 'e' * 64
    assert not _is_typed_settled_world_item(item, source_envelopes=envelopes)


@pytest.mark.asyncio
@pytest.mark.parametrize('version', ['23', '25', '26', '27'])
async def test_recorded_versions_do_not_gain_missing_settled_labels(settled_chain, version):
    _, world, _, _, current, _, _ = settled_chain
    materials = deepcopy(dict(current.materials))
    for row in materials['recent_self_experiences']['items'] + materials['week_diary']:
        if row.get('source_ref') == world.occurrence_id:
            row.pop('context_kind', None)
            row.pop('epistemic_scope', None)
    saved = _recreate(current.model_copy(update={'snapshot_compiler': _InteriorBinding.available(
        'inner-life-snapshot-compiler.' + version)}), materials)
    frozen = saved.model_dump_json()
    first = order_user_present_payload({'inner_life_snapshot': saved.model_view()})
    restored = type(saved).model_validate_json(frozen)
    second = order_user_present_payload({'inner_life_snapshot': restored.model_view()})
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)
    assert 'settled_world_occurrence' not in json.dumps(first, ensure_ascii=False)
    assert saved.model_dump_json() == frozen
