"""Exact lifecycle source wiring; scripted model verdicts do not qualify semantics."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.activity_lifecycle_state import lifecycle_states_from_projection
from companion_daemon.world_v2.character_interior.contracts import _InteriorCapabilityManifest
from companion_daemon.world_v2.character_interior.life_source_origin import LifeSourceOrigin, canonical, digest
from companion_daemon.world_v2.character_interior.life_source_readings import prepare_life_source_readings
from companion_daemon.world_v2.character_interior.life_source_review import LifeSourceReviewer
from companion_daemon.world_v2.character_interior.life_source_state_readings import lifecycle_state_reading, SCOPE
from companion_daemon.world_v2.character_interior.ports import _InteriorRoleRequest, _InteriorRoleResult
from companion_daemon.world_v2.character_interior.snapshot_compiler import compile_inner_life_snapshot, source_envelopes_from_capsule
from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger_context_resolver import ContextRelevanceScope, context_capsule_compiler_from_ledger
from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.selected_source_composer import compile_selected_source_table
from current_activity_fixture import accepted_current_activity
from test_character_interior_structured_role import _world_stimulus_no_change_result
from test_life_fact_value_display import response
from test_world_consequence_authoring_context import _operator_transition
from test_world_stimulus_life_intent import _http_result, _model


def request_for(ledger, store):
    state = ledger.project()
    capsule = context_capsule_compiler_from_ledger(ledger=ledger, life_content_store=store,
        relevance_scope=ContextRelevanceScope(actor_ref='actor:companion')).compile(
        query_from_projection(state, actor_ref='actor:companion', trigger_ref='event:lifecycle-reading'))
    snapshot = compile_inner_life_snapshot(json.loads(capsule.model_content_json),
        source_envelopes=source_envelopes_from_capsule(capsule),
        life_source_origin=LifeSourceOrigin.from_capsule(capsule))
    payload = canonical({'contract': 'character-interior-world-stimulus-capability.1'})
    manifest = _InteriorCapabilityManifest(capability_ref='capability:lifecycle-reading',
        capability_kind='world_stimulus_appraisal', payload_json=payload,
        payload_hash='sha256:' + digest(payload), source_refs=(capsule.trigger_ref,))
    request = _InteriorRoleRequest(inner_turn_id='life:lifecycle-source', phase='experience',
        purpose='world_stimulus_appraisal', subject_ref=capsule.actor_ref, trigger_ref=capsule.trigger_ref,
        subject_source_refs=(capsule.trigger_ref,), snapshot=snapshot, capability_manifest=manifest)
    return capsule, request


class LifecycleHTTP:
    def __init__(self):
        self.calls = []

    async def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        packet = json.loads(body['messages'][1]['content'])
        if 'inner_turn' in packet:
            result = json.loads(_world_stimulus_no_change_result())
            result.update(summary='这项计划已经停下了。', attended_source_refs=[])
        else:
            choice = next(p for p in packet['permission_choices'] if p['claim_scope'] == 'activity_lifecycle')
            result = json.loads(response(packet, permission=choice['permission_id']))
        return _http_result(body, result)


@pytest.mark.asyncio
@pytest.mark.parametrize('event_type,status', [('ActivityAbandoned', 'abandoned'), ('ActivityPaused', 'paused')])
async def test_accepted_lifecycle_reaches_actual_author_review_and_cold_receipt(event_type, status, monkeypatch):
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    ledger, store, plan_id, _ = await accepted_current_activity()
    event_ref = _operator_transition(ledger, plan_id, event_type, 'event:lifecycle:state')
    capsule, request = request_for(ledger, store)
    assert SCOPE in request.snapshot.materials
    assert SCOPE not in request.snapshot.model_view()['materials']
    assert all(item['scope'] != SCOPE for item in request.snapshot.model_view()['source_inventory'])
    assert 'activity_lifecycle_state' in compile_selected_source_table(capsule=capsule).as_dict()['unsupported_source_kinds']
    provider = LifecycleHTTP()
    model = _model(provider)
    try:
        role = StructuredCharacterRoleFaculty(model=model, model_id=model.model,
            life_source_reviewer=LifeSourceReviewer(model=model, evidence_store=InMemoryImmutableLifeContentStore()))
        result = _InteriorRoleResult.model_validate(await role.experience(request))
    finally:
        await model.aclose()
    assert len(provider.calls) == 2
    view = result.life_source_view
    from companion_daemon.world_v2.character_interior.life_source_review import MINTED_CONTRACT
    assert view.review_contract == result.life_source_review.contract == MINTED_CONTRACT
    author = json.loads(provider.calls[0]['messages'][1]['content'])
    assert author['background_context_profile']['profile_id'] == 'stimulus_appraisal.lifecycle.2'
    assert author['inner_life_snapshot']['materials'][SCOPE][0]['status'] == status
    readings = prepare_life_source_readings(view=view, snapshot=request.snapshot)
    source = next(r for r in readings.as_dict()['readings'] if r['source_family'] == 'activity_lifecycle_state')
    assert source['value']['plan_id'] == plan_id and source['item_ref'] == event_ref
    assert source['value']['status'] == status
    assert source['permissions'] == [['activity_lifecycle', 'companion']]
    assert source['source_owner_ref'] == request.snapshot.actor_ref
    assert not {'accepted_intention', 'location_ref', 'result', 'perception'} & source['value'].keys()
    packet = json.loads(provider.calls[1]['messages'][1]['content'])
    displayed = next(r for r in packet['source_readings']['readings'] if r['item_ref'] == source['item_ref'])
    assert displayed['value'] == source['value']
    assert displayed['permissions'] == source['permissions']
    assert result.life_source_review.verify(result=result, snapshot=request.snapshot)
    cold = _InteriorRoleResult.model_validate_json(result.model_dump_json())
    assert cold.life_source_review.verify(result=cold, snapshot=request.snapshot)
    for scope in ('accepted_intention', 'authorized_attempt_result', 'environment', 'perception'):
        with pytest.raises(ValueError, match='field permission'):
            readings.require_reading(reading_id=source['reading_id'], claim_scope=scope,
                subject_role='companion', view=view, snapshot=request.snapshot)
    table = json.loads(view.source_table_json)
    row = next(r for r in table['source_references'] if r['source_ref'] == event_ref
        and table['source_materials'][r['material_index']]['material'].get('lane') == 'world_life')
    row = {**row, 'review_material': table['source_materials'][row['material_index']]['material']}
    for key, value in [('owner_actor_ref', 'actor:other'), ('plan_id', 'plan:other'),
                       ('plan_entity_revision', 99), ('status', 'completed'),
                       ('transitioned_at', '2099-01-01T00:00:00Z')]:
        changed = deepcopy(row)
        changed['review_material']['item']['value'][key] = value
        with pytest.raises(ValueError):
            lifecycle_state_reading(changed, rendered=author['inner_life_snapshot'], snapshot=request.snapshot)
    unseen = deepcopy(author['inner_life_snapshot'])
    unseen['materials'][SCOPE] = []
    assert lifecycle_state_reading(row, rendered=unseen, snapshot=request.snapshot)[0] is None
    # A source-table edit cannot survive the actual immutable-view consumer.
    altered = deepcopy(table)
    altered['source_materials'][row['material_index']]['material']['item']['value']['plan_id'] = 'plan:other'
    with pytest.raises(ValueError, match='original Capsule'):
        view.model_copy(update={'source_table_json': canonical(altered)}).verify_snapshot(request.snapshot)


@pytest.mark.asyncio
async def test_lifecycle_producer_rejects_wrong_owner_head_clock_privacy_and_cursor():
    ledger, _, plan_id, _ = await accepted_current_activity()
    _operator_transition(ledger, plan_id, 'ActivityPaused', 'event:paused')
    state = ledger.project()
    cursor = ProjectionCursor(world_revision=state.world_revision,
        deliberation_revision=state.deliberation_revision, ledger_sequence=state.ledger_sequence)
    args = dict(projection=state, actor_ref='actor:companion', cursor=cursor, viewer_privacy_ceiling='private')
    assert len(lifecycle_states_from_projection(**args)) == 1
    assert lifecycle_states_from_projection(**{**args, 'actor_ref': 'actor:other'}) == ()
    assert lifecycle_states_from_projection(**{**args, 'viewer_privacy_ceiling': 'shareable'}) == ()
    assert lifecycle_states_from_projection(**{**args, 'cursor': cursor.model_copy(update={'ledger_sequence': 0})}) == ()
    plan = state.plans[0]
    for key, value in [('owner_actor_ref', 'actor:other'), ('plan_id', 'plan:other'), ('entity_revision', 99)]:
        bad_plan = plan.model_copy(update={key: value})
        bad_state = state.model_copy(update={'plans': (bad_plan,)})
        # A wrong-owner request selecting that owner still cannot validate the old binding.
        with pytest.raises(ValueError):
            lifecycle_states_from_projection(**{**args, 'projection': bad_state,
                'actor_ref': value if key == 'owner_actor_ref' else args['actor_ref']})
    with pytest.raises(ValueError):
        lifecycle_states_from_projection(**{**args, 'projection': state.model_copy(update={
            'logical_time': plan.authority_origin.accepted_at - timedelta(seconds=1)})})
    _operator_transition(ledger, plan_id, 'ActivityResumed', 'event:resumed')
    resumed = ledger.project()
    assert lifecycle_states_from_projection(**{**args, 'projection': resumed,
        'cursor': cursor.model_copy(update={'world_revision': resumed.world_revision,
            'deliberation_revision': resumed.deliberation_revision, 'ledger_sequence': resumed.ledger_sequence})}) == ()


@pytest.mark.asyncio
async def test_lifecycle_attention_is_bounded_to_three_exact_recent_heads(monkeypatch):
    from companion_daemon.world_v2.schemas import plan_authority_binding_hash, plan_authority_projection_hash

    ledger, _, plan_id, _ = await accepted_current_activity()
    _operator_transition(ledger, plan_id, 'ActivityAbandoned', 'event:ended')
    state = ledger.project()
    original = state.plans[0]
    event = next(e for e in state.committed_world_event_refs if e.event_id == 'event:ended')
    plans, events = [], []
    for index in range(8):
        at = state.logical_time - timedelta(minutes=index)
        event_ref = f'event:ended:{index}'
        item = original.model_copy(update={'plan_id': f'plan:ended:{index}', 'last_transitioned_at': at})
        projection_hash = plan_authority_projection_hash(item)
        origin = original.authority_origin.model_copy(update={
            'accepted_event_ref': event_ref, 'accepted_world_revision': index + 1,
            'accepted_at': at, 'transition_id': f'transition:{index}',
            'authority_projection_hash': projection_hash,
            'binding_hash': plan_authority_binding_hash(plan_id=item.plan_id,
                owner_actor_ref=item.owner_actor_ref, entity_revision=item.entity_revision,
                transition_id=f'transition:{index}', event_type='ActivityAbandoned',
                accepted_event_ref=event_ref, accepted_world_revision=index + 1,
                accepted_payload_hash=event.payload_hash, accepted_at=at, projection_hash=projection_hash),
        })
        plans.append(item.model_copy(update={'authority_origin': origin}))
        events.append(event.model_copy(update={'event_id': event_ref, 'logical_time': at, 'world_revision': index + 1}))
    state = state.model_copy(update={'plans': tuple(reversed(plans)), 'committed_world_event_refs': tuple(events)})
    cursor = ProjectionCursor(world_revision=state.world_revision,
        deliberation_revision=state.deliberation_revision, ledger_sequence=state.ledger_sequence)
    import companion_daemon.world_v2.activity_lifecycle_state as module
    validate = module.validate_plan_authority_state
    validations = []

    def counted(plans, events, **kwargs):
        validations.append(len(plans))
        return validate(plans, events, **kwargs)

    monkeypatch.setattr(module, 'validate_plan_authority_state', counted)
    selected = lifecycle_states_from_projection(projection=state, actor_ref=original.owner_actor_ref,
        cursor=cursor, viewer_privacy_ceiling='private')
    assert validations == [3]
    assert [item.plan_id for item in selected] == ['plan:ended:0', 'plan:ended:1', 'plan:ended:2']
