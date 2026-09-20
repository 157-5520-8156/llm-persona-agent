"""Frozen request bytes and cold compilation, not provider semantic qualification."""

from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import ValidationError
import pytest

from companion_daemon.llm import provider_invocation_request_hash
from companion_daemon.world_v2.character_interior.life_source_origin import canonical, digest
from companion_daemon.world_v2.character_interior.life_source_review import inspect_review, prepare_review
from companion_daemon.world_v2.character_interior.life_source_view import LifeSourceView
from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy, SliceBudget
from test_life_claim_authority import new_fixture_view, review_response
from test_life_fact_readings import _fact_view
from test_life_fact_value_display import VALUE, candidate, real_observation
from test_visible_selected_source_context import _sources

GOLDEN = json.loads(
    (Path(__file__).parent / 'fixtures/life_review_request_hashes.json').read_text()
)['cases']
CURRENT_CONTRACTS = ['life-source-review.13', 'life-source-review.14']


@asynccontextmanager
async def source_case(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    real_observation(monkeypatch)
    policy = None if mode == 'fact' else ContextCapsuleBudgetPolicy(
        relevant_facts=SliceBudget(max_items=0),
        **({'recent_dialogue': SliceBudget(max_items=0)} if mode == 'empty' else {}),
    )
    async with _sources(tmp_path, retained_value=VALUE, policy=policy) as case:
        yield await _fact_view(case.capsule)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['fact', 'report_only', 'empty'])
@pytest.mark.parametrize('version', range(1, 15))
async def test_all_versioned_request_bytes_and_provider_hashes_survive_cold_compilation(
    tmp_path, monkeypatch, mode, version,
):
    contract = f'life-source-review.{version}'
    async with source_case(tmp_path, monkeypatch, mode) as (original, snapshot):
        # Public synthetic authors only. New presentation is compiled for .13/.14;
        # this never upgrades the contract of a persisted real author request.
        view = new_fixture_view(original, snapshot, contract) if version >= 13 else (
            original.model_copy(update={'review_contract': contract})
        )
        prepared, readings = prepare_review(
            candidate_json=candidate(), provider_raw=candidate(),
            view=view, snapshot=snapshot, contract=contract,
        )
        request = json.loads(prepared)['request']
        packet = json.loads(request['messages'][1]['content'])
        assert packet['contract'] == contract
        families = {r['source_family'] for r in readings['readings']}
        assert families == {
            'fact': {'accepted_fact_value', 'counterpart_report'},
            'report_only': {'counterpart_report'},
            'empty': set(),
        }[mode]
        # The full envelope hash retains instruction bytes and schema key order.
        # The separate provider identity checks the audit/replay hash contract.
        assert {
            'prepared_sha256': digest(prepared),
            'request_sha256': provider_invocation_request_hash(**request),
        } == GOLDEN[f'{mode}-{version}']
        saved_view, saved_snapshot = view.model_dump_json(), snapshot.model_dump_json()

    cold_view = LifeSourceView.model_validate_json(saved_view)
    cold_snapshot = type(snapshot).model_validate_json(saved_snapshot)
    rebuilt, cold_readings = prepare_review(
        candidate_json=candidate(), provider_raw=candidate(),
        view=cold_view, snapshot=cold_snapshot, contract=contract,
    )
    assert rebuilt == prepared
    assert cold_readings == readings


@pytest.mark.asyncio
@pytest.mark.parametrize('contract', CURRENT_CONTRACTS)
@pytest.mark.parametrize('mode', ['fact', 'report_only', 'empty'])
async def test_current_wire_preserves_exact_permissions_and_empty_source_bound(
    tmp_path, monkeypatch, contract, mode,
):
    from companion_daemon.world_v2.character_interior import (
        life_source_authorship_review,
        life_source_review,
    )

    def legacy_builder_is_unavailable(**kwargs):
        pytest.fail('Current Life requests must not construct a legacy predecessor.')

    monkeypatch.setattr(life_source_review, '_prepare_legacy_review', legacy_builder_is_unavailable)
    monkeypatch.setattr(life_source_authorship_review, 'prepare_legacy_authorship_review',
        legacy_builder_is_unavailable)
    async with source_case(tmp_path, monkeypatch, mode) as (original, snapshot):
        view = new_fixture_view(original, snapshot, contract)
        prepared, readings = prepare_review(
            candidate_json=candidate(), provider_raw=candidate(), view=view, snapshot=snapshot,
        )
    request = json.loads(prepared)['request']
    packet = json.loads(request['messages'][1]['content'])
    schema = request['tools'][0]['function']['parameters']
    support = schema['properties']['fields']['items']['properties']['record_bound_claims'][
        'items'
    ]['properties']['supports']
    assert request['tools'][0]['function']['name'] == request['tool_choice']['function']['name'] == (
        'review_life_candidate_v8' if contract.endswith('.14') else 'review_life_candidate_v7'
    )
    assert packet['support_selection_contract'] == 'life-bound-permission-selection.1'
    assert list(support['items']['properties']) == ['permission_id']
    assert support['items']['additionalProperties'] is False
    assert all('requires_exact_fact_quote' not in p for p in packet['permission_choices'])
    if mode == 'empty':
        assert packet['permission_choices'] == []
        assert support['maxItems'] == 0
        assert inspect_review(
            raw=canonical(review_response(packet, subjective=True)),
            prepared_json=prepared, readings=readings,
        )[0] == 'accepted'
        return

    choice = next(p for p in packet['permission_choices'] if p['claim_scope'] == (
        'accepted_fact' if mode == 'fact' else 'report_uptake'
    ))
    if mode == 'fact':
        assert choice['selection'] == 'bound_accepted_value'
        assert choice['accepted_value'] == VALUE
    else:
        assert choice['selection'] == 'direct_field'
    source = next(r for r in readings['readings'] if r['reading_id'] == choice['reading_id'])
    verdict = review_response(
        packet, scope=choice['claim_scope'], role='source_owner',
        ref=source['source_owner_ref'], permission=choice['permission_id'],
    )
    if contract.endswith('.13'):
        for field in verdict['fields']:
            for claim in field['record_bound_claims']:
                for key in ('required_scope', 'subject_role', 'subject_ref'):
                    del claim[key]
    assert inspect_review(raw=canonical(verdict), prepared_json=prepared, readings=readings)[0] == 'accepted'
    claim = next(f for f in verdict['fields'] if f['path'] == '/summary')['record_bound_claims'][0]
    for malformed_support in (
        {'permission_id': 'permission:not-offered'},
        {'permission_id': choice['permission_id'], 'quoted_value': VALUE},
    ):
        claim['supports'] = [malformed_support]
        with pytest.raises(ValidationError):
            inspect_review(raw=canonical(verdict), prepared_json=prepared, readings=readings)


@pytest.mark.asyncio
@pytest.mark.parametrize('contract', CURRENT_CONTRACTS)
async def test_present_authorship_does_not_cover_an_embedded_historical_state(
    tmp_path, monkeypatch, contract,
):
    now = '我现在想慢慢听他说下去'
    past = '昨天曾觉得踏实'
    body = json.loads(candidate())
    body['summary'] = f'{now}，也记得{past}。'
    raw = canonical(body)
    async with source_case(tmp_path, monkeypatch, 'empty') as (original, snapshot):
        view = new_fixture_view(original, snapshot, contract)
        prepared, readings = prepare_review(
            candidate_json=raw, provider_raw=raw, view=view, snapshot=snapshot,
        )
    packet = json.loads(json.loads(prepared)['request']['messages'][1]['content'])
    verdict = review_response(packet, subjective=True)
    field = next(f for f in verdict['fields'] if f['path'] == '/summary')
    field['created_current_states'][0]['source_span'] = now
    assert inspect_review(raw=canonical(verdict), prepared_json=prepared, readings=readings)[0] == 'accepted'
    for key, value in [('time_relation', 'past'), ('subject_ref', 'actor:another')]:
        altered = deepcopy(verdict)
        target = next(f for f in altered['fields'] if f['path'] == '/summary')
        target['created_current_states'][0][key] = value
        with pytest.raises(ValidationError):
            inspect_review(raw=canonical(altered), prepared_json=prepared, readings=readings)
    # An explicit scripted decomposition supplies the historical obligation.
    # The first accepted response above does not establish semantic completeness.
    claim = {
        'source_span': past, 'proposition': past, 'reason': 'No selected historical state.',
        'supports': [], 'verdict': 'unsupported',
    }
    if contract.endswith('.14'):
        claim.update(required_scope='subjective_history', subject_role='companion',
            subject_ref=snapshot.actor_ref)
    field['record_bound_claims'] = [claim]
    assert inspect_review(raw=canonical(verdict), prepared_json=prepared, readings=readings)[0] == 'rejected'
    claim['verdict'] = 'uncertain'
    assert inspect_review(raw=canonical(verdict), prepared_json=prepared, readings=readings)[0] == 'uncertain'
    claim['verdict'] = 'supported'
    with pytest.raises(ValueError, match='support is missing'):
        inspect_review(raw=canonical(verdict), prepared_json=prepared, readings=readings)


@pytest.mark.asyncio
async def test_current_request_bounds_its_final_display_without_a_discarded_intermediate(
    tmp_path, monkeypatch,
):
    import test_life_fact_value_display as fact_fixture

    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    observation = VALUE + '春' * (4096 - len(VALUE))
    monkeypatch.setattr(fact_fixture, 'OBSERVATION', observation)
    real_observation(monkeypatch)
    policy = ContextCapsuleBudgetPolicy(
        relevant_facts=SliceBudget(max_items=1, max_characters=30_000),
        recent_dialogue=SliceBudget(max_items=0),
    )
    async with _sources(tmp_path, retained_value=VALUE, policy=policy) as case:
        original, snapshot = await _fact_view(case.capsule)
        view = new_fixture_view(original, snapshot, 'life-source-review.13')
        body = candidate()
        # This is the same native JSON output with valid trailing whitespace.
        # Before direct construction, its discarded .6 intermediate was 256001
        # bytes and rejected it. The sent .13 display is only 248854 bytes.
        provider_raw = body + ' ' * 112_480
        assert json.loads(provider_raw) == json.loads(body)
        assert len(provider_raw.encode()) < 131_072
        prepared, readings = prepare_review(
            candidate_json=body, provider_raw=provider_raw, view=view, snapshot=snapshot,
        )
    assert len(prepared.encode()) == 248_854
    packet = json.loads(json.loads(prepared)['request']['messages'][1]['content'])
    original_snapshot = json.loads(json.loads(view.messages_json)[1]['content'])['inner_life_snapshot']
    assert original_snapshot['materials']['relevant_facts'][0]['source_excerpt'] == observation
    assert packet['author_snapshot_display']['snapshot']['materials']['relevant_facts'][0][
        'accepted_value'
    ] == VALUE
    assert observation not in canonical(packet)
    assert [r['source_family'] for r in readings['readings']] == ['accepted_fact_value']
    oversized_raw = provider_raw + ' ' * 4_000
    assert len(oversized_raw.encode()) < 131_072
    with pytest.raises(ValueError, match='Life review request exceeds its audit bound'):
        prepare_review(candidate_json=body, provider_raw=oversized_raw, view=view, snapshot=snapshot)
