"""Trusted source preparation without a chat request or Life write authority."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy, SliceBudget
from companion_daemon.world_v2.selected_source_composer import compile_selected_source_table
from companion_daemon.world_v2.selected_source_context import compile_selected_fact_dialogue_context
from companion_daemon.world_v2.visible_review_context import compile_visible_selected_source_context
from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
from companion_daemon.world_v2.visible_source_review_receipt import compile_visible_candidate_material
from test_visible_selected_source_context import _sources
from test_visible_source_review_receipt import _candidate


@pytest.mark.asyncio
async def test_background_sources_do_not_invent_a_current_counterpart_or_chat_authority(tmp_path):
    async with _sources(tmp_path) as case:
        original, common = compile_selected_fact_dialogue_context(case.capsule)
        chat = compile_visible_selected_source_context(request=case.request, capsule=case.capsule)
        assert original == case.capsule
        assert common['entries'] == chat['entries']
        assert common['subjects'] == {'companion_actor_ref': case.capsule.actor_ref}
        assert chat['subjects']['counterpart_actor_ref'] == case.observation.actor
        pin = common['selected_source_projection']
        assert 'model_input_hash' not in pin
        assert pin['compiler_result_hash'] == case.capsule.compiler_result_hash
        table = compile_selected_source_table(capsule=case.capsule)
        payload = table.as_dict()
        assert payload['pin'] == pin
        assert payload['write_authority'] is False
        assert payload['author_view_binding'] == 'not_assessed'
        assert not isinstance(table, VisibleSourceTable)
        assert all(row['material']['kind'] != 'current_counterpart_report'
                   for row in payload['source_materials'])
        assert case.observation.text in table.payload_json
        with pytest.raises(FrozenInstanceError):
            table.payload_json = '{}'
        with pytest.raises(ValueError, match='typed decision and compiled source table'):
            compile_visible_candidate_material(candidate=_candidate(case), source_table=table, source_ref_aliases={})
        # Even an explicit rewrap cannot turn the generic contract into a chat table.
        with pytest.raises(ValueError, match='source table contract is unsupported'):
            compile_visible_candidate_material(
                candidate=_candidate(case), source_table=VisibleSourceTable(table.payload_json),
                source_ref_aliases={},
            )
        assert payload['contract'] == 'selected-source-row-table.1'
        payload['source_materials'].clear()
        assert table.as_dict()['source_materials']


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['compiler_result_tag', 'model_content_json', 'payload'])
async def test_shared_entry_revalidates_the_original_full_capsule(tmp_path, fault):
    async with _sources(tmp_path) as case:
        capsule = case.capsule
        if fault == 'payload':
            lane = capsule.relevant_facts
            changed = lane.items[0].model_copy(update={'payload_json': '{}'})
            capsule = capsule.model_copy(update={'relevant_facts': lane.model_copy(update={'items': (changed,)})})
        elif fault == 'model_content_json':
            capsule = capsule.model_copy(update={fault: capsule.model_content_json + ' '})
        else:
            capsule = capsule.model_copy(update={fault: '0' * 64})
        with pytest.raises(ValueError):
            compile_selected_source_table(capsule=capsule)


@pytest.mark.asyncio
async def test_shared_entry_never_adds_fact_members_omitted_by_the_original_selection(tmp_path):
    policy = ContextCapsuleBudgetPolicy(relevant_facts=SliceBudget(max_items=0, max_fields=128, max_characters=8000))
    async with _sources(tmp_path, extra=True, policy=policy) as case:
        assert not case.capsule.relevant_facts.items
        table = compile_selected_source_table(capsule=case.capsule).as_dict()
        assert table['pin']['source_selection']['relevant_facts']['item_refs'] == []
        assert not any(item['material'].get('lane') == 'relevant_facts'
                       for item in table['source_materials'])
        with pytest.raises(ValueError, match='typed Context Capsule'):
            compile_selected_source_table(capsule=SimpleNamespace(**case.capsule.model_dump()))


@pytest.mark.asyncio
async def test_shared_entry_preserves_withheld_source_rejection(tmp_path):
    async with _sources(tmp_path, privacy='withhold') as case:
        with pytest.raises(ValueError, match='withheld'):
            compile_selected_source_table(capsule=case.capsule)


@pytest.mark.parametrize('value', [None, 0, 1, 'true'])
def test_subjective_selection_requires_an_explicit_boolean(value):
    with pytest.raises(TypeError, match='boolean'):
        compile_selected_source_table(capsule=None, include_subjective_history=value)


@pytest.mark.asyncio
async def test_life_settlement_is_read_without_fabricating_a_chat_request(tmp_path, monkeypatch):
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from test_current_activity_context import current_context
    from test_world_stimulus_life_intent import ACTOR, WORLD
    from test_world_stimulus_life_response import _ResponseHTTP, _build, _model, _settled

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "life.sqlite"
    provider = _ResponseHTTP(text=None)
    model = _model(provider)
    app = _build(path, model)
    try:
        source = await _settled(app)
    finally:
        await app.aclose()
        await model.aclose()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        capsule, _, _ = current_context(ledger, store, actor_ref=ACTOR)
        before = capsule.model_dump_json()
        table = compile_selected_source_table(capsule=capsule, include_subjective_history=True)
        rows = [r for r in table.source_references() if r['source_ref'] == source.event_id]
        assert rows
        assert any('一阵短雨已经停了。' in str(r['review_material']) for r in rows)
        assert table.as_dict()['subjects'] == {'companion_actor_ref': ACTOR}
        assert table.as_dict()['write_authority'] is False
        assert capsule.model_dump_json() == before
    finally:
        store.close()
        ledger.close()
