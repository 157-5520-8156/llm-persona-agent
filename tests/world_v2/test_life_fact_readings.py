"""Accepted predicate values stay narrower than the original observed report."""
from copy import deepcopy
import json
import hashlib

import pytest

from companion_daemon.world_v2.character_interior.life_source_origin import LifeSourceOrigin, canonical
from companion_daemon.world_v2.character_interior.life_source_readings import prepare_life_source_readings
from companion_daemon.world_v2.character_interior.ports import _InteriorRoleRequest, _InteriorRoleResult
from companion_daemon.world_v2.character_interior.contracts import _InteriorCapabilityManifest
from companion_daemon.world_v2.character_interior.snapshot_compiler import compile_inner_life_snapshot, source_envelopes_from_capsule
from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty
from test_character_interior_structured_role import _RequiredToolQueueModel, _world_stimulus_no_change_result
from test_visible_selected_source_context import _sources

VALUE = '保留周四的约定'


async def _fact_view(capsule, *, review_contract=None):
    snapshot = compile_inner_life_snapshot(json.loads(capsule.model_content_json),
        source_envelopes=source_envelopes_from_capsule(capsule),
        life_source_origin=LifeSourceOrigin.from_capsule(capsule))
    payload = canonical({'contract': 'character-interior-world-stimulus-capability.1'})
    manifest = _InteriorCapabilityManifest(capability_ref='capability:fact-reading',
        capability_kind='world_stimulus_appraisal', payload_json=payload,
        payload_hash='sha256:' + hashlib.sha256(payload.encode()).hexdigest(),
        source_refs=(capsule.trigger_ref,))
    request = _InteriorRoleRequest(inner_turn_id='life:fact-source', phase='experience',
        purpose='world_stimulus_appraisal', subject_ref=capsule.actor_ref,
        trigger_ref=capsule.trigger_ref, subject_source_refs=(capsule.trigger_ref,), snapshot=snapshot,
        capability_manifest=manifest)
    response = json.loads(_world_stimulus_no_change_result())
    response['attended_source_refs'] = []
    model = _RequiredToolQueueModel(canonical(response))
    raw = await StructuredCharacterRoleFaculty(model=model, model_id='offline-fact-reading').experience(request)
    result = _InteriorRoleResult.model_validate(raw)
    view = result.life_source_view
    assert json.loads(view.messages_json) == model.calls[-1][0]
    view.verify_request(request)
    if review_contract is not None:
        from test_life_claim_authority import new_fixture_view

        view = new_fixture_view(view, snapshot, review_contract)
    return view, snapshot


@pytest.mark.asyncio
async def test_predicate_value_requires_the_exact_accepted_quote(tmp_path):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        view, snapshot = await _fact_view(case.capsule)
        prepared = prepare_life_source_readings(view=view, snapshot=snapshot)
        facts = [r for r in prepared.as_dict()['readings'] if r['source_family'] == 'accepted_fact_value']
        assert len(facts) == 1
        fact = facts[0]
        assert fact['value'] == case.observation.text != VALUE
        assert fact['permissions'] == []
        assert fact['fact_context']['predicate_code'] == 'schedule.commitment'
        chosen = prepared.require_fact_value(reading_id=fact['reading_id'], quoted_value=VALUE,
            claim_scope='accepted_fact', subject_ref=case.observation.actor, view=view, snapshot=snapshot)
        assert chosen['quoted_value'] == VALUE
        assert chosen['observation_context'] == case.observation.text
        assert chosen['fact_context'] == fact['fact_context']
        assert chosen['write_authority'] is False
        for text in (case.observation.text, '用户决定取消周五的报告', VALUE + '。', '保留周五的约定', ''):
            with pytest.raises(ValueError, match='exact accepted Fact value'):
                prepared.require_fact_value(reading_id=fact['reading_id'], quoted_value=text,
                    claim_scope='accepted_fact', subject_ref=case.observation.actor, view=view, snapshot=snapshot)
        for scope, subject in [('historical_accepted_fact', case.observation.actor),
                               ('external_fact', case.observation.actor), ('accepted_fact', case.capsule.actor_ref)]:
            with pytest.raises(ValueError, match='predicate/subject/status'):
                prepared.require_fact_value(reading_id=fact['reading_id'], quoted_value=VALUE,
                    claim_scope=scope, subject_ref=subject, view=view, snapshot=snapshot)
        with pytest.raises(ValueError, match='field permission'):
            prepared.require_reading(reading_id=fact['reading_id'], claim_scope='accepted_fact',
                subject_role='source_owner', view=view, snapshot=snapshot)
        assert 'accepted_value_binding' not in json.loads(view.messages_json)[1]['content']


@pytest.mark.asyncio
async def test_fact_binding_presentation_and_legacy_absence_fail_closed(tmp_path):
    from companion_daemon.world_v2.character_interior.life_fact_readings import fact_value_reading
    from companion_daemon.world_v2.selected_source_composer import compile_selected_source_table

    async with _sources(tmp_path, retained_value=VALUE) as case:
        view, _ = await _fact_view(case.capsule)
        rows = compile_selected_source_table(capsule=case.capsule).source_references()
        rendered = json.loads(json.loads(view.messages_json)[1]['content'])['inner_life_snapshot']
        row = next(r for r in rows if fact_value_reading(r, rendered=rendered)[0] is not None)
        for field in ('predicate_code', 'subject_ref', 'status', 'source_excerpt', 'occurred_at'):
            changed = deepcopy(rendered)
            changed['materials']['relevant_facts'][0][field] = 'changed'
            assert fact_value_reading(row, rendered=changed)[0] is None
        legacy = deepcopy(row)
        del legacy['review_material']['item']['value']['accepted_value_binding']
        from companion_daemon.world_v2.context_capsule import FactRecallItem
        legacy_value = legacy['review_material']['item']['value']
        parsed = FactRecallItem.model_validate_json(canonical(legacy_value))
        assert 'accepted_value_binding' not in parsed.model_dump(mode='json')
        legacy['review_material']['item']['value_hash'] = hashlib.sha256(canonical(legacy_value).encode()).hexdigest()
        assert fact_value_reading(legacy, rendered=rendered) == (None, 'no_bound_fact_value_reader')
        alias = deepcopy(row)
        alias['source_ref'] = alias['review_material']['item']['value']['observation_event_ref']
        assert fact_value_reading(alias, rendered=rendered)[0] is None


@pytest.mark.asyncio
async def test_withdrawn_fact_leaves_current_sources_and_keeps_old_value_on_replay(tmp_path):
    from companion_daemon.world_v2.context_resolver import query_from_projection
    from test_fact_member_withdrawal import _record

    async with _sources(tmp_path, retained_value=VALUE) as case:
        target = case.ledger.project().facts[0]
        case.fact_model.results.append({'decision': 'withdraw', 'predicate_code': 'schedule.commitment',
            'target_fact_ref': target.fact_id, 'confidence': 9500, 'rationale': 'Explicit withdrawal.'})
        _record(case.ledger, 2, '周四的约定也取消了。', logical_time=case.ledger.project().logical_time)
        assert (await case.fact_runtime.drain_one()).work_status == 'accepted'
        capsule = case.compiler.compile_for_deliberation(query_from_projection(case.ledger.project(),
            actor_ref=case.capsule.actor_ref, trigger_ref='event:observation:member:2')).capsule
        view, snapshot = await _fact_view(capsule)
        prepared = prepare_life_source_readings(view=view, snapshot=snapshot)
        facts = [r for r in prepared.as_dict()['readings'] if r['source_family'] == 'accepted_fact_value']
        assert facts == []
        assert not capsule.relevant_facts.items
        # Historical Facts enter the Recall corpus, not current relevant_facts.
        # Do not fabricate a current source from the still-retained old value.
        # Closing and rebuilding from immutable events preserves the new binding.
        from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
        from companion_daemon.world_v2.ledger_context_resolver import historical_fact_recall_items
        case.ledger.close()
        reopened = SQLiteWorldLedger(path=tmp_path / 'selected-visible.sqlite', world_id=capsule.world_id)
        try:
            recalled = historical_fact_recall_items(ledger=reopened, projection=reopened.project(),
                subject_refs=frozenset({case.observation.actor}))
            assert len(recalled) == 1
            assert recalled[0].accepted_value_binding.select(source_excerpt=recalled[0].source_excerpt, quoted_value=VALUE) == VALUE
            assert recalled[0].status == 'historical'
            assert recalled[0].valid_to >= recalled[0].valid_from
        finally:
            reopened.close()


def test_value_binding_is_optional_for_legacy_sources_and_never_guesses_other_producers():
    from types import SimpleNamespace
    from companion_daemon.world_v2.fact_observation_value import FactObservationValueBinding

    sha = hashlib.sha256(VALUE.encode()).hexdigest()
    binding = FactObservationValueBinding(value_ref='value:observation:' + sha, value_hash=sha)
    for source_kind, ref in [('operator_observation', binding.value_ref), ('observed_message', 'value:unknown')]:
        values = SimpleNamespace(assertion_binding=SimpleNamespace(source_kind=source_kind), value_ref=ref, value_hash=sha)
        assert FactObservationValueBinding.from_fact_values(values) is None
    with pytest.raises(ValueError):
        binding.model_copy(update={'value_ref': 'value:another'}).select(source_excerpt=VALUE, quoted_value=VALUE)
    assert binding.select(source_excerpt=VALUE + VALUE, quoted_value=VALUE) == VALUE
    with pytest.raises(ValueError):
        binding.select(source_excerpt='另一段观察', quoted_value=VALUE)


@pytest.mark.asyncio
async def test_historical_reader_requires_displayed_validity_and_narrow_permission(tmp_path):
    from companion_daemon.world_v2.character_interior.life_fact_readings import fact_value_reading
    from companion_daemon.world_v2.context_capsule import HistoricalFactRecallItem
    from companion_daemon.world_v2.selected_source_composer import compile_selected_source_table

    async with _sources(tmp_path, retained_value=VALUE) as case:
        view, _ = await _fact_view(case.capsule)
        rows = compile_selected_source_table(capsule=case.capsule).source_references()
        rendered = json.loads(json.loads(view.messages_json)[1]['content'])['inner_life_snapshot']
        row = deepcopy(next(r for r in rows if fact_value_reading(r, rendered=rendered)[0] is not None))
        value = row['review_material']['item']['value']
        old = HistoricalFactRecallItem.model_validate_json(canonical({**value, 'status': 'historical',
            'valid_from': value['updated_at'], 'valid_to': case.capsule.logical_time.isoformat()}))
        value = old.model_dump(mode='json')
        # Reader-only fixture. This does not install a recalled Fact into a
        # trusted Capsule or assert that the real current Fact was withdrawn.
        row['review_material']['item']['value'] = value
        row['review_material']['item']['value_hash'] = hashlib.sha256(canonical(value).encode()).hexdigest()
        shown = deepcopy(rendered)
        shown['materials']['relevant_facts'][0].update({key: value[key] for key in ('status', 'valid_from', 'valid_to')})
        reading, reason = fact_value_reading(row, rendered=shown)
        assert reason is None
        assert reading['value_selection_permissions'] == [['historical_accepted_fact', 'source_owner']]
        del shown['materials']['relevant_facts'][0]['valid_to']
        assert fact_value_reading(row, rendered=shown)[0] is None
