"""An epoch.1 mode is recovered from exact history, never current cache bytes."""
import json

import pytest

from companion_daemon.world_v2.epoch_binding_compat import legacy_epoch_fact_binding
from companion_daemon.world_v2.epoch_continuity import ContinuitySnapshot, apply_continuity_snapshot
from companion_daemon.world_v2.schemas import FactAssertionBinding, FactProjection, WorldEvent, fact_semantic_fingerprint
from companion_daemon.world_v2.reducers import ReducerState, reduce_event
from test_epoch_continuity_h10 import _fact, NOW, WORLD


def fingerprint(fact, values):
    return fact_semantic_fingerprint(subject_ref=values.subject_ref,predicate_code=values.predicate_code,
        cardinality=values.cardinality,conflict_key=values.conflict_key,value_hash=values.value_hash,
        assertion_binding=values.assertion_binding,anchor_evidence_refs=values.anchor_evidence_refs,
        policy_refs=fact.origin.policy_refs)


def event(name, kind, payload, world=WORLD):
    return WorldEvent.from_payload(schema_version='world-v2.1',event_id='event:'+name,world_id=world,
        event_type=kind,logical_time=NOW,created_at=NOW,actor='system:test',source='test',
        trace_id='trace:test',causation_id='cause:test',correlation_id='test',idempotency_key='key:'+name,payload=payload)


def fixture(version='epoch-continuity.1'):
    original = _fact()
    binding = FactAssertionBinding(source_kind='observed_message',source_ref='observation:archive',
        asserted_subject_ref=original.values.subject_ref,actor_ref=original.values.subject_ref,
        channel='test',payload_ref='payload:archive',content_payload_hash='a'*64)
    values = original.values.model_copy(update={'assertion_binding':binding})
    original = original.model_copy(update={'values':values,'semantic_fingerprint':fingerprint(original,values)})
    snapshot = ContinuitySnapshot(epoch_id='epoch:2',archive_world_id=WORLD,archive_head_hash='f'*64,
                                  archive_world_revision=20,continuity_version=version,
                                  facts=(original.model_dump(mode='json'),))
    genesis = event('genesis','WorldStarted',{'continuity':snapshot.model_dump(mode='json')})
    def read(mode):
        return apply_continuity_snapshot(snapshot,genesis_event_id=genesis.event_id,
            genesis_payload_hash=genesis.payload_hash,logical_time=NOW,legacy_fact_binding=mode)['facts'][0]
    return genesis, read('archive'), read('genesis')


@pytest.mark.parametrize('mode', ['archive','genesis'])
def test_exact_before_image_recovers_original_epoch1_representation(mode):
    genesis, archived, rebound = fixture()
    before = archived if mode=='archive' else rebound
    witness = event('correction','FactCorrected',{'fact_before':before.model_dump(mode='json')})
    selected = legacy_epoch_fact_binding(genesis, (witness,))
    assert selected == mode
    state = reduce_event(ReducerState(), genesis, legacy_epoch_fact_binding=selected)
    assert state.facts[0] == before
    assert json.loads(genesis.payload_json)['continuity']['continuity_version']=='epoch-continuity.1'


def test_new_epoch_version_is_explicit_and_cannot_inherit_archive_mode():
    genesis, archived, rebound = fixture('epoch-continuity.2')
    assert archived == rebound
    assert rebound.values.assertion_binding.source_kind == 'operator_observation'
    assert legacy_epoch_fact_binding(genesis, ()) == 'genesis'
    assert ContinuitySnapshot.model_fields['continuity_version'].default == 'epoch-continuity.2'
    with pytest.raises(ValueError):
        ContinuitySnapshot(epoch_id='x',archive_world_id=WORLD,archive_head_hash='f'*64,
                           archive_world_revision=1,continuity_version='unknown')


@pytest.mark.parametrize('fault',['privacy','value','confidence','origin','world'])
def test_unrelated_or_altered_before_image_cannot_select_old_policy(fault):
    genesis, before, _ = fixture()
    world = WORLD
    if fault=='world':
        world='world:other'
    elif fault=='origin':
        before=before.model_copy(update={'origin':before.origin.model_copy(update={'accepted_event_ref':'event:other'})})
    else:
        updates = {'privacy_class':'public'} if fault=='privacy' else {'value_hash':'e'*64} if fault=='value' else {'confidence_bp':8000}
        values=before.values.model_copy(update=updates)
        before=before.model_copy(update={'values':values,'semantic_fingerprint':fingerprint(before,values)})
    before=FactProjection.model_validate_json(before.model_dump_json())
    witness=event('changed','FactCorrected',{'fact_before':before.model_dump(mode='json')},world)
    assert legacy_epoch_fact_binding(genesis,(witness,))=='genesis'


def test_without_witness_keeps_prior_behavior_and_later_evidence_cannot_override_first():
    genesis, archived, rebound = fixture()
    assert legacy_epoch_fact_binding(genesis,())=='genesis'
    witnesses=tuple(event(str(i),'FactCorrected',{'fact_before':before.model_dump(mode='json')})
                    for i,before in enumerate((archived,rebound)))
    assert legacy_epoch_fact_binding(genesis,witnesses)=='archive'
