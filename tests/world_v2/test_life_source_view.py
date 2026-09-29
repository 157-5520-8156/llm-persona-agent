"""Original Life selection and actual HTTP input survive durable preparation."""

from copy import deepcopy
import json
import sqlite3

import pytest

from companion_daemon.world_v2.character_interior.core import _restore_prepared_turn, _InteriorTechnicalError
from companion_daemon.world_v2.character_interior.life_source_origin import LifeSourceOrigin, canonical
from companion_daemon.world_v2.character_interior.life_source_view import LifeSourceView, prepare_life_source_view
from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty
from test_world_stimulus_life_response import _ResponseHTTP, _build, _model, _settled


async def _prepared(tmp_path, monkeypatch, *, fault=None, ecology=False, reference_wire=False):
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    captured = []
    original = StructuredCharacterRoleFaculty.experience

    async def capture(self, request):
        if request.purpose == 'world_stimulus_appraisal':
            captured.append(request)
        return await original(self, request)

    monkeypatch.setattr(StructuredCharacterRoleFaculty, 'experience', capture)
    from companion_daemon.world_v2.character_interior.turn_store import open_sqlite_character_interior_turn_store
    import test_world_stimulus_life_intent as fixture

    path = tmp_path / 'life-source.sqlite'
    turns_path = tmp_path / 'turns.sqlite'
    store = open_sqlite_character_interior_turn_store(path=turns_path, world_id=fixture.WORLD)
    compose = fixture.compose_production_character_interior

    def durable_composition(**kwargs):
        return compose(**kwargs, turn_store=store, reference_wire=reference_wire)

    monkeypatch.setattr(fixture, 'compose_production_character_interior', durable_composition)
    provider = _ResponseHTTP(text='有点想听听窗外的声音。', fault=fault)
    model = _model(provider)
    app = _build(path, model, ecology=ecology)
    try:
        await _settled(app)
        await app.drain_background_once()
    finally:
        await app.aclose()
        await model.aclose()
        store.close()
    with sqlite3.connect(turns_path.as_uri() + '?mode=ro', uri=True) as conn:
        rows = conn.execute("SELECT authored_state_json FROM world_v2_character_interior_turns WHERE purpose = 'world_stimulus_appraisal' AND authored_state_json IS NOT NULL").fetchall()
    assert len(rows) == 1
    assert len(captured) == len(provider.stimulus_requests) == (2 if fault == "missing_once" else 1)
    return json.loads(rows[0][0]), captured[-1], provider


@pytest.mark.asyncio
async def test_actual_life_request_and_original_capsule_survive_close_and_restore(tmp_path, monkeypatch):
    payload, request, provider = await _prepared(tmp_path, monkeypatch)
    result, snapshot, _, _ = _restore_prepared_turn(canonical(payload), purpose='world_stimulus_appraisal')
    view = result.life_source_view
    assert view is not None and snapshot.life_source_origin is not None
    assert view.verify_request(request) == view
    assert json.loads(view.messages_json) == provider.stimulus_requests[0]['messages']
    assert json.loads(view.provider_controls_json)['tools'] == provider.stimulus_requests[0]['tools']
    assert view.provider_request_hash == result.author_lineage.request_hash
    assert snapshot.life_source_origin.capsule() == request.snapshot.life_source_origin.capsule()
    assert 'life_source_origin' not in view.messages_json
    assert 'capsule_json' not in view.messages_json
    assert view.write_authority is False
    assert view.semantic_coverage == view.source_permission_coverage == 'not_assessed'
    assert snapshot.snapshot_hash == request.snapshot.snapshot_hash
    # The origin changes identity, not the character-visible material.
    provisional = snapshot.model_copy(update={'life_source_origin': None})
    from companion_daemon.world_v2.character_interior.life_source_origin import digest
    sha = digest(canonical(provisional._identity_material()))
    legacy = provisional.model_copy(update={'snapshot_id': 'inner-life-snapshot:sha256:' + sha, 'snapshot_hash': sha})
    legacy.identity_and_inventory_are_complete()
    assert legacy.model_view()['materials'] == snapshot.model_view()['materials']
    assert legacy.snapshot_hash != snapshot.snapshot_hash
    assert 'life_source_origin' not in legacy.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['drop_view', 'system_message', 'rendered_material', 'tools', 'source_table', 'unmatched_refs', 'origin', 'author_hash'])
async def test_durable_source_preparation_rejects_substitution(tmp_path, monkeypatch, fault):
    payload, _, _ = await _prepared(tmp_path, monkeypatch)
    view = payload['result']['life_source_view']
    if fault == 'drop_view':
        del payload['result']['life_source_view']
    elif fault in {'system_message', 'rendered_material'}:
        messages = json.loads(view['messages_json'])
        if fault == 'system_message':
            messages[0]['content'] += ' Changed.'
        else:
            data = json.loads(messages[1]['content'])
            data['inner_life_snapshot']['materials']['invented'] = '她已经走完一圈。'
            messages[1]['content'] = canonical(data)
        view['messages_json'] = canonical(messages)
    elif fault == 'tools':
        data = json.loads(view['provider_controls_json'])
        data['tools'] = []
        view['provider_controls_json'] = canonical(data)
    elif fault == 'source_table':
        data = json.loads(view['source_table_json'])
        data['source_materials'].clear()
        view['source_table_json'] = canonical(data)
    elif fault == 'unmatched_refs':
        view['unmatched_visible_source_refs'] = ['source:invented']
    elif fault == 'origin':
        payload['snapshot']['life_source_origin']['capsule_sha256'] = '0' * 64
    else:
        payload['result']['author_lineage']['request_hash'] = 'sha256:' + '0' * 64
    with pytest.raises(_InteriorTechnicalError, match='invalid_durable_turn_checkpoint'):
        _restore_prepared_turn(canonical(payload), purpose='world_stimulus_appraisal')


@pytest.mark.asyncio
async def test_new_request_cannot_reuse_another_subjects_actual_messages(tmp_path, monkeypatch):
    payload, request, _ = await _prepared(tmp_path, monkeypatch)
    view = LifeSourceView.model_validate_json(canonical(payload['result']['life_source_view']))
    moved = request.model_copy(update={'subject_ref': 'another:subject'})
    with pytest.raises(ValueError, match='another role request'):
        prepare_life_source_view(
            request=moved, messages=json.loads(view.messages_json),
            provider_controls=json.loads(view.provider_controls_json),
            provider_request_hash=view.provider_request_hash,
        )
    # Recomputed local origin bytes cannot be swapped into the pinned snapshot.
    origin = request.snapshot.life_source_origin
    altered = origin.model_copy(update={'capsule_json': origin.capsule_json + ' '})
    with pytest.raises(ValueError):
        altered.verify_snapshot(request.snapshot)
    with pytest.raises(ValueError):
        LifeSourceOrigin.from_capsule(deepcopy(origin.model_dump()))
    with pytest.raises(ValueError, match='unsupported Life source origin'):
        origin.model_copy(update={'contract': 'life-source-origin.unknown'}).verify_snapshot(request.snapshot)


@pytest.mark.asyncio
async def test_recall_and_prefetch_keep_origin_but_invalidate_the_previous_sent_view(tmp_path, monkeypatch):
    from companion_daemon.world_v2.character_interior.core import CharacterInterior
    from companion_daemon.world_v2.character_interior.ports import _PrefetchResult, _RecallResult

    payload, request, _ = await _prepared(tmp_path, monkeypatch)
    snapshot = request.snapshot
    view = LifeSourceView.model_validate_json(canonical(payload['result']['life_source_view']))
    coordinates = dict(world_id=snapshot.world_id, actor_ref=snapshot.actor_ref, cursor=snapshot.cursor)
    prefetched = _PrefetchResult(**coordinates, content={'items': [{'source_ref': 'memory:new-candidate', 'text': 'A retained candidate.'}]}, source_refs=('memory:new-candidate',))
    after_prefetch = CharacterInterior._merge_prefetch(snapshot, prefetched)
    recalled = _RecallResult(**coordinates, content={'items': [{'source_ref': 'memory:chosen', 'text': 'A selected recollection.'}]}, source_refs=('memory:chosen',))
    after_recall = CharacterInterior._merge_recall(snapshot, recalled)
    for changed in (after_prefetch, after_recall):
        assert changed.life_source_origin == snapshot.life_source_origin
        assert changed.snapshot_hash != snapshot.snapshot_hash
        with pytest.raises(ValueError, match='original snapshot'):
            view.verify_snapshot(changed)


@pytest.mark.asyncio
async def test_same_author_correction_keeps_origin_and_binds_its_actual_second_input(tmp_path, monkeypatch):
    payload, request, provider = await _prepared(tmp_path, monkeypatch, fault='missing_once')
    result, snapshot, _, _ = _restore_prepared_turn(canonical(payload), purpose='world_stimulus_appraisal')
    view = result.life_source_view
    assert request.correction_ordinal == result.author_lineage.attempt_ordinal == 1
    assert view.verify_request(request) == view
    assert json.loads(view.messages_json) == provider.stimulus_requests[1]['messages']
    first, second = [json.loads(r['messages'][1]['content']) for r in provider.stimulus_requests]
    correction = second.pop('correction')
    assert first == second
    assert correction['rejected_role_result']['raw_result'] not in snapshot.life_source_origin.capsule_json
    assert len(provider.stimulus_requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing_once"])
async def test_host_reference_bindings_survive_correction_and_durable_restore(tmp_path, monkeypatch, fault):
    payload, request, provider = await _prepared(tmp_path, monkeypatch, fault=fault, reference_wire=True)
    result, snapshot, _, _ = _restore_prepared_turn(canonical(payload), purpose="world_stimulus_appraisal")
    view = result.life_source_view
    assert view.verify_request(request) == view
    controls = json.loads(view.provider_controls_json)
    bindings = controls["identity_extras"]["reference_bindings"]
    sent = json.loads(provider.stimulus_requests[-1]["messages"][1]["content"])
    assert sent["reference_dictionary"]["contract"] == "opaque-reference-wire.3"
    assert "entries" not in sent["reference_dictionary"]
    assert bindings["entries"]
    assert view.author_payload()["inner_turn"]["trigger_ref"] == request.trigger_ref
    assert snapshot.snapshot_hash == request.snapshot.snapshot_hash
    corrupted = deepcopy(payload)
    bad_controls = json.loads(corrupted["result"]["life_source_view"]["provider_controls_json"])
    entries = bad_controls["identity_extras"]["reference_bindings"]["entries"]
    entries[next(iter(entries))] = "event:forged:" + "b" * 64
    corrupted["result"]["life_source_view"]["provider_controls_json"] = canonical(bad_controls)
    with pytest.raises(_InteriorTechnicalError, match="invalid_durable_turn_checkpoint"):
        _restore_prepared_turn(canonical(corrupted), purpose="world_stimulus_appraisal")
