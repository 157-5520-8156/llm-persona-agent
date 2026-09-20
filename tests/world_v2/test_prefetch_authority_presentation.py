"""New scoped snapshots show existing recall authority; saved views stay frozen."""
from copy import deepcopy
import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.contracts import _InteriorBinding
from companion_daemon.world_v2.character_interior.core import CharacterInterior
from companion_daemon.world_v2.character_interior.life_context_presentation import (
    EXPLICIT_PREFETCH_AUTHORITY_COMPILER_VERSIONS, LIFE_CONTEXT_COMPILER_VERSION,
    SCOPED_DIARY_COMPILER_VERSIONS, SOURCE_KIND_DIARY_COMPILER_VERSIONS,
)
from companion_daemon.world_v2.character_interior.ports import _PrefetchResult
from companion_daemon.world_v2.present_prompt import (
    expand_present_world_context, order_user_present_payload,
)
from test_character_interior_structured_role import StructuredCharacterRoleFaculty, _request, _result
from test_derived_material_redaction import _recreate
from test_life_context_presentation import compile_with, occurrence


AUTHORITY = {
    'authority': 'defeasible_interpretation',
    'epistemic_scope': 'private_interpretation',
    'memory_kind': 'reflective',
}


def test_current_compiler_is_registered_without_replacing_historical_versions():
    assert 'inner-life-snapshot-compiler.25' in SCOPED_DIARY_COMPILER_VERSIONS
    assert 'inner-life-snapshot-compiler.26' in SOURCE_KIND_DIARY_COMPILER_VERSIONS
    assert 'inner-life-snapshot-compiler.27' in EXPLICIT_PREFETCH_AUTHORITY_COMPILER_VERSIONS
    assert all(LIFE_CONTEXT_COMPILER_VERSION in versions for versions in (
        SCOPED_DIARY_COMPILER_VERSIONS, SOURCE_KIND_DIARY_COMPILER_VERSIONS,
        EXPLICIT_PREFETCH_AUTHORITY_COMPILER_VERSIONS,
    ))


def item():
    # Exact field shape of trial07's Experience recall through private_impressions.
    # Text/ref are synthetic; the producer's reflective authority is not upgraded.
    return {
        **AUTHORITY,
        'occurred_from': '2026-09-20T02:33:00Z',
        'occurred_to': '2026-09-20T03:03:00Z',
        'privacy_class': 'private',
        'source_ref': 'experience:character-life-response:fixture',
        'source_slice': 'private_impressions',
        'text': '这份不声张的认真，我想悄悄记住。',
    }


def snapshot_with_prefetch(version=None, *, scoped=True):
    snapshot = compile_with(*([occurrence(settled=True)] if scoped else []))
    snapshot = CharacterInterior._merge_prefetch(snapshot, _PrefetchResult(
        world_id=snapshot.world_id, actor_ref=snapshot.actor_ref, cursor=snapshot.cursor,
        content={'items': [item()]}, source_refs=(item()['source_ref'],),
    ))
    if version is not None:
        materials = deepcopy(dict(snapshot.materials))
        if version == '25':
            for row in materials['recent_self_experiences']['items'] + materials['week_diary']:
                row.pop('context_kind', None)
                row.pop('epistemic_scope', None)
        snapshot = _recreate(snapshot.model_copy(update={
            'snapshot_compiler': _InteriorBinding.available(f'inner-life-snapshot-compiler.{version}'),
        }), materials)
    return snapshot


@pytest.mark.parametrize('version', ['23', '25', '26', '27'])
def test_exact_version_selects_visible_authority_and_round_trip(version):
    snapshot = snapshot_with_prefetch(version)
    canonical = {'inner_life_snapshot': snapshot.model_view()}
    frozen = snapshot.model_dump_json()
    presented = order_user_present_payload(canonical)
    shown, = presented['inner_life_snapshot']['materials']['automatic_prefetch']['items']
    expected = item() if version == '27' else {k: v for k, v in item().items() if k not in AUTHORITY}
    assert shown == expected
    assert expand_present_world_context(presented)['inner_life_snapshot']['materials']['automatic_prefetch'] == {'items': [item()]}
    assert order_user_present_payload(expand_present_world_context(presented)) == presented
    assert order_user_present_payload(presented) == presented
    assert snapshot.model_dump_json() == frozen
    assert presented['inner_life_snapshot']['source_inventory'] == canonical['inner_life_snapshot']['source_inventory']


@pytest.mark.parametrize('changes', [
    {'authority': 'retained_experience', 'epistemic_scope': 'recalled_episode', 'memory_kind': 'episodic'},
    {'authority': None, 'epistemic_scope': None, 'memory_kind': None},
    {},
])
def test_new_view_does_not_invent_or_upgrade_a_missing_or_different_authority(changes):
    original = {k: v for k, v in item().items() if k not in AUTHORITY}
    original.update(changes)
    canonical = {'inner_life_snapshot': {
        'snapshot_compiler': {'availability': 'available', 'value': 'inner-life-snapshot-compiler.27'},
        'materials': {'automatic_prefetch': {'items': [original]}},
    }}
    shown = order_user_present_payload(canonical)
    assert shown['inner_life_snapshot']['materials']['automatic_prefetch']['items'] == [original]
    assert expand_present_world_context(shown) == canonical


def test_existing_non_authority_compaction_remains_reversible_and_source_exact():
    original = {**item(), 'source_slice': 'recalled_emotional_associations', 'occurred_to': None}
    canonical = {'inner_life_snapshot': {
        'snapshot_compiler': {'availability': 'available', 'value': 'inner-life-snapshot-compiler.27'},
        'materials': {'automatic_prefetch': {'items': [original]}},
    }}
    shown = order_user_present_payload(canonical)
    entry, = shown['inner_life_snapshot']['materials']['automatic_prefetch']['items']
    assert entry == {k: v for k, v in original.items() if k not in {'source_slice', 'occurred_to'}}
    assert expand_present_world_context(shown) == canonical


def test_27_preserves_26_world_diary_and_hides_withheld_prefetch_before_presentation():
    current = snapshot_with_prefetch()
    assert current.snapshot_compiler.value == 'inner-life-snapshot-compiler.27'
    old26 = snapshot_with_prefetch('26')
    for lane in ('recent_self_experiences', 'week_diary', 'automatic_prefetch'):
        assert current.model_view()['materials'][lane] == old26.model_view()['materials'][lane]
    diary = current.model_view()['materials']['week_diary'][0]['readings'][0]
    assert diary['context_kind'] == 'settled_world_occurrence'
    assert diary['epistemic_scope'] == 'settled_world_occurrence_with_field_scoped_authority'
    visible = frozenset(ref for ref in current.source_refs if ref != item()['source_ref'])
    hidden = order_user_present_payload({'inner_life_snapshot': current.model_view(visible_source_refs=visible)})
    assert 'automatic_prefetch' not in hidden['inner_life_snapshot']['materials']
    assert item()['text'] not in json.dumps(hidden, ensure_ascii=False)
    plain = snapshot_with_prefetch(scoped=False)
    assert plain.snapshot_compiler.value == 'inner-life-snapshot-compiler.23'
    shown = order_user_present_payload({'inner_life_snapshot': plain.model_view()})
    assert not set(AUTHORITY).intersection(shown['inner_life_snapshot']['materials']['automatic_prefetch']['items'][0])


@pytest.mark.asyncio
@pytest.mark.parametrize('version', ['25', '26', '27'])
async def test_actual_role_http_payload_uses_the_saved_snapshot_version(monkeypatch, version):
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    captured = []
    raw = json.loads(_result(status='silent'))
    raw['attended_source_refs'] = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(raw)}}]})

    model = DeepSeekChatModel('fixture-key', 'https://api.deepseek.com', 'deepseek-v4-flash',
                             thinking_enabled=False, transport=httpx.MockTransport(handler))
    snapshot = snapshot_with_prefetch(version)
    request = (await _request()).model_copy(update={'snapshot': snapshot})
    try:
        result = await StructuredCharacterRoleFaculty(model=model, model_id=model.model).consider(request)
    finally:
        await model.aclose()
    assert result['status'] == 'silent' and len(captured) == 1
    payload = json.loads(captured[0]['messages'][1]['content'])
    shown, = payload['inner_life_snapshot']['materials']['automatic_prefetch']['items']
    assert shown == (item() if version == '27' else {k: v for k, v in item().items() if k not in AUTHORITY})
    assert expand_present_world_context(payload)['inner_life_snapshot']['materials']['automatic_prefetch']['items'] == [item()]
