"""Language context preserves selected record scope without granting truth."""
import hashlib
import json

import pytest

import companion_daemon.world_v2.visible_dialogue_reading_context as context
from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable, compile_visible_source_table
from test_visible_selected_source_context import _sources
from test_visible_source_review_receipt import _candidate


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def rehash(table):
    # Tests alter prepared material, not authenticated World history. The caller
    # must still compare this table with its original audit before use.
    for m in table['source_materials']:
        m['material_identity'] = hashlib.sha256(canonical(m['material']).encode()).hexdigest()
    for r in table['source_references']:
        r['material_identity'] = table['source_materials'][r['material_index']]['material_identity']
    for key, field in (('materials_hash', 'source_materials'), ('table_hash', 'source_references')):
        table[key] = hashlib.sha256(canonical(table[field]).encode()).hexdigest()
    return VisibleSourceTable(canonical(table))


@pytest.mark.asyncio
async def test_original_selected_speaker_text_and_bindings_survive_bounded_view(tmp_path, monkeypatch):
    async with _sources(tmp_path, extra=True) as case:
        table = compile_visible_source_table(request=case.request, capsule=case.capsule)
        candidate = _candidate(case)
        def compile_view(source=table):
            return context.compile_dialogue_reading_context(candidate=candidate, source_table=source, source_ref_aliases={})
        view = compile_view()
        assert view['source_table_sha256'] == table.payload_hash
        assert view['bindings']
        assert view['model_context']['complete_history'] is False
        assert sorted(b['sequence'] for b in view['bindings']) == [b['sequence'] for b in view['bindings']]
        for binding, utterance in zip(view['bindings'], view['model_context']['utterances'], strict=True):
            original = table.as_dict()['source_materials'][binding['material_index']]['material']['item']['value']
            assert utterance == {k: original[k] for k in ('speaker', 'delivery_state', 'text')}
        monkeypatch.setattr(context, 'MAX_ITEMS', 1)
        short = compile_view()
        assert short['bindings'] == view['bindings'][-1:]
        assert short['model_context']['selection_truncated'] == (len(view['bindings']) > 1)
        monkeypatch.setattr(context, 'MAX_TEXT_BYTES', 1)
        assert compile_view()['model_context']['utterances'] == []
        assert compile_view()['model_context']['selection_truncated'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['actor', 'withhold', 'future', 'changed_bytes'])
async def test_context_rejects_misbound_or_unavailable_selected_material(tmp_path, fault):
    async with _sources(tmp_path) as case:
        table = compile_visible_source_table(request=case.request, capsule=case.capsule).as_dict()
        selected = next(m['material'] for m in table['source_materials'] if m['material'].get('lane') == 'recent_dialogue')
        if fault == 'actor':
            selected['item']['value']['speaker_ref'] = 'user:someone-else'
        elif fault == 'withhold':
            selected['privacy_class'] = 'withhold'
        elif fault == 'future':
            selected['item']['value']['source_claims'][0]['authority_world_revision'] = table['pin']['world_revision'] + 1
        else:
            selected['item']['value']['text'] += ' altered'
        prepared = VisibleSourceTable(canonical(table)) if fault == 'changed_bytes' else rehash(table)
        with pytest.raises(ValueError):
            context.compile_dialogue_reading_context(candidate=_candidate(case), source_table=prepared, source_ref_aliases={})
