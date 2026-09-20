"""Life .12 wire bindings, not real-provider semantic qualification."""
from contextlib import asynccontextmanager
from copy import deepcopy
import json

from jsonschema import ValidationError
import pytest

from companion_daemon.world_v2.character_interior.life_source_origin import canonical
from companion_daemon.world_v2.character_interior.life_source_readings import PreparedLifeSourceReadings
from companion_daemon.world_v2.character_interior.life_source_review import inspect_review, prepare_review
from companion_daemon.world_v2.character_interior.life_source_view import LifeSourceView
from test_life_fact_value_display import OBSERVATION, VALUE, candidate, real_observation, response
from test_life_fact_readings import _fact_view
from test_visible_selected_source_context import _sources

CURRENT = 'life-source-review.12'
PREVIOUS = 'life-source-review.11'


@asynccontextmanager
async def prepared_fact(tmp_path, monkeypatch, contract=CURRENT):
    real_observation(monkeypatch)
    async with _sources(tmp_path, retained_value=VALUE) as case:
        original, snapshot = await _fact_view(case.capsule)
        # Synthetic author fixture only; real archived author pins are never retagged.
        view = original.model_copy(update={'review_contract': contract})
        prepared, readings = prepare_review(candidate_json=candidate(), provider_raw=candidate(),
            view=view, snapshot=snapshot)
        request = json.loads(prepared)['request']
        packet = json.loads(request['messages'][1]['content'])
        fact = next(r for r in readings['readings'] if r['source_family'] == 'accepted_fact_value')
        choice = next(p for p in packet['permission_choices'] if p['reading_id'] == fact['reading_id'])
        yield view, snapshot, prepared, readings, request, packet, choice


@pytest.mark.asyncio
async def test_permission_only_resolves_the_exact_fact_and_keeps_report_separate(tmp_path, monkeypatch):
    async with prepared_fact(tmp_path, monkeypatch) as (_, _, prepared, readings, request, packet, choice):
        schema = request['tools'][0]['function']['parameters']['properties']['fields']['items']['properties']
        support = schema['record_bound_claims']['items']['properties']['supports']['items']
        assert list(support['properties']) == support['required'] == ['permission_id']
        assert support['additionalProperties'] is False
        assert request['tool_choice']['function']['name'] == 'review_life_candidate_v7'
        assert packet['support_selection_contract'] == 'life-bound-permission-selection.1'
        assert choice['selection'] == 'bound_accepted_value' and choice['accepted_value'] == VALUE
        assert all('requires_exact_fact_quote' not in p for p in packet['permission_choices'])
        instruction = request['messages'][0]['content']
        assert 'Only Fact permissions also require' not in instruction
        assert 'requires_exact_fact_quote' not in instruction
        assert 'use the shown accepted_value verbatim' not in instruction
        assert 'Do not return quoted_value' in instruction
        assert inspect_review(raw=response(packet, permission=choice['permission_id']),
            prepared_json=prepared, readings=readings)[0] == 'accepted'
        for quote in (VALUE, OBSERVATION):
            with pytest.raises(ValidationError):
                inspect_review(raw=response(packet, permission=choice['permission_id'], quote=quote),
                    prepared_json=prepared, readings=readings)
        with pytest.raises(ValidationError):
            inspect_review(raw=response(packet, permission='permission:not-offered'),
                prepared_json=prepared, readings=readings)
        report = next(r for r in readings['readings'] if r['source_family'] == 'counterpart_report')
        assert report['value'] == OBSERVATION
        uptake = next(p for p in packet['permission_choices']
            if p['reading_id'] == report['reading_id'] and p['claim_scope'] == 'report_uptake')
        assert uptake['selection'] == 'direct_field'
        assert inspect_review(raw=response(packet, permission=uptake['permission_id']),
            prepared_json=prepared, readings=readings)[0] == 'accepted'


@pytest.mark.asyncio
async def test_current_intention_wire_does_not_exempt_embedded_history(tmp_path, monkeypatch):
    async with prepared_fact(tmp_path, monkeypatch) as (_, _, prepared, readings, request, packet, choice):
        schema = request['tools'][0]['function']['parameters']['properties']['fields']['items']['properties']
        assert schema['created_current_states']['items']['properties']['time_relation']['enum'] == ['current']
        assert 'A choice formed now to act later is a current intention' in request['messages'][0]['content']
        verdict = json.loads(response(packet, permission=choice['permission_id']))
        field = next(f for f in verdict['fields'] if f['path'] == '/summary')
        span = next(f['text'] for f in packet['text_fields'] if f['path'] == '/summary')
        field['created_current_states'] = [{'source_span': span,
            'state_description': 'Synthetic present intention targeting a future action.',
            'subject_ref': packet['current_authorship_authority']['actor_ref'], 'time_relation': 'current'}]
        assert inspect_review(raw=canonical(verdict), prepared_json=prepared, readings=readings)[0] == 'accepted'
        for relation in ('past', 'future', 'unspecified'):
            malformed = deepcopy(verdict)
            next(f for f in malformed['fields'] if f['path'] == '/summary')['created_current_states'][0]['time_relation'] = relation
            with pytest.raises(ValidationError):
                inspect_review(raw=canonical(malformed), prepared_json=prepared, readings=readings)
        # Same span may carry a present state and an unsupported historical premise.
        field['record_bound_claims'][0].update(verdict='unsupported', supports=[])
        assert inspect_review(raw=canonical(verdict), prepared_json=prepared, readings=readings)[0] == 'rejected'
        field['record_bound_claims'][0]['verdict'] = 'uncertain'
        assert inspect_review(raw=canonical(verdict), prepared_json=prepared, readings=readings)[0] == 'uncertain'
        field['record_bound_claims'][0]['verdict'] = 'supported'
        with pytest.raises(ValueError, match='support is missing'):
            inspect_review(raw=canonical(verdict), prepared_json=prepared, readings=readings)


@pytest.mark.asyncio
async def test_bound_fact_metadata_and_value_cannot_be_swapped(tmp_path, monkeypatch):
    async with prepared_fact(tmp_path, monkeypatch) as (view, snapshot, prepared, readings, _, packet, choice):
        mutations = [
            ('accepted_value', OBSERVATION), ('value', OBSERVATION),
            ('pointer', '/item/value/source_excerpt'), ('source_owner_ref', snapshot.actor_ref),
            ('fact_context.subject_ref', snapshot.actor_ref), ('fact_context.predicate_code', 'location.current'),
            ('fact_context.status', 'historical'), ('fact_context.occurred_at', '2000-01-01T00:00:00Z'),
            ('fact_context.updated_at', '2000-01-01T00:00:00Z'), ('value_binding.value_hash', '0' * 64),
        ]
        raw = response(packet, permission=choice['permission_id'])
        for path, value in mutations:
            altered = deepcopy(readings)
            row = next(r for r in altered['readings'] if r['reading_id'] == choice['reading_id'])
            parts = path.split('.')
            target = row if len(parts) == 1 else row[parts[0]]
            target[parts[-1]] = value
            with pytest.raises(ValueError, match='pinned source/view compilation'):
                PreparedLifeSourceReadings(canonical(altered)).verify(view=view, snapshot=snapshot)
            with pytest.raises(ValueError, match='exact unique source readings'):
                inspect_review(raw=raw, prepared_json=prepared, readings=altered)
        # Even a forged presentation cannot bypass the original accepted value hash.
        forged = json.loads(prepared)
        forged_packet = json.loads(forged['request']['messages'][1]['content'])
        row = next(r for r in forged_packet['source_readings']['readings'] if r['reading_id'] == choice['reading_id'])
        row['value'] = row['accepted_value'] = OBSERVATION
        forged['request']['messages'][1]['content'] = canonical(forged_packet)
        with pytest.raises(ValueError, match='exact accepted Fact value'):
            inspect_review(raw=raw, prepared_json=canonical(forged), readings=forged_packet['source_readings'])


@pytest.mark.asyncio
@pytest.mark.parametrize('contract', [PREVIOUS, CURRENT])
async def test_versioned_wire_and_cold_compilation_remain_distinct(tmp_path, monkeypatch, contract):
    async with prepared_fact(tmp_path, monkeypatch, contract) as (view, snapshot, prepared, readings, _, packet, choice):
        saved_view, saved_snapshot = view.model_dump_json(), snapshot.model_dump_json()
        if contract == PREVIOUS:
            assert choice['requires_exact_fact_quote'] is True
            with pytest.raises(ValidationError):
                inspect_review(raw=response(packet, permission=choice['permission_id']),
                    prepared_json=prepared, readings=readings)
            old_raw = json.loads(response(packet, permission=choice['permission_id'], quote=VALUE))
            field = next(f for f in old_raw['fields'] if f['path'] == '/summary')
            field['created_current_states'] = [{'source_span': field['record_bound_claims'][0]['source_span'],
                'state_description': 'Old future-state wire remains invalid.',
                'subject_ref': packet['current_authorship_authority']['actor_ref'], 'time_relation': 'future'}]
            with pytest.raises(ValueError, match='non-current state'):
                inspect_review(raw=canonical(old_raw), prepared_json=prepared, readings=readings)
            with pytest.raises(ValueError, match='contract differs'):
                prepare_review(candidate_json=candidate(), provider_raw=candidate(),
                    view=view, snapshot=snapshot, contract=CURRENT)
    cold_view = LifeSourceView.model_validate_json(saved_view)
    cold_snapshot = type(snapshot).model_validate_json(saved_snapshot)
    rebuilt, cold_readings = prepare_review(candidate_json=candidate(), provider_raw=candidate(),
        view=cold_view, snapshot=cold_snapshot, contract=contract)
    assert rebuilt == prepared and cold_readings == readings
