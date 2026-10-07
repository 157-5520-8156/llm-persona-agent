"""The public import path admits cost before any provider transport."""
import json

import httpx
import pytest

from companion_daemon.world_v2.character_prehistory import digest
from companion_daemon.world_v2.story_import_runner import prepare_story_import
from test_prehistory_life_materials_ingestion import brief, materials_for, NARRATIVE


def install_provider(monkeypatch, *, timeout=False, invalid_plan=False):
    from companion_daemon import llm
    monkeypatch.setattr(llm, 'shared_deepseek_circuit_breaker', llm.ProviderCircuitBreaker)
    requests = []

    async def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if timeout:
            raise httpx.ReadTimeout('fixture issued request lost its response', request=request)
        packet = json.loads(payload['messages'][1]['content'])
        contract = packet['contract']
        if 'ingestion' in contract:
            value = materials_for().model_dump(mode='json')
        elif 'story-link-request' in contract:
            value = {'contract': 'prehistory-story-link-response.1', 'relations': []}
        elif 'import-plan-request' in contract:
            value = {'contract': 'story-import-plan.2', 'event_identities': [
                {'event_ref': ref, 'status': 'new_or_distinct', 'prior_record_id': None,
                 'prior_event_ref': None, 'rationale': 'Fixture separate event.'}
                for ref in packet['new_event_inventory']], 'cross_story_links': []}
            if invalid_plan:
                value['event_identities'] = []
        else:
            value = {'contract': 'prehistory-import-assessment.4', 'records': [
                {'record_index': 0, 'verdict': 'approve', 'rationale': 'Fixture source supported.'}],
                'story_links': [], 'cross_record_findings': [], 'event_matches': [],
                'cross_story_links': [], 'missing_connections': []}
        message = {'role': 'assistant', 'content': json.dumps(value, ensure_ascii=False)}
        if payload.get('tools'):
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'fixture', 'type': 'function', 'function': {
                    'name': 'story_stage_result', 'arguments': json.dumps(value, ensure_ascii=False)}}]}
        return httpx.Response(200, json={'id': 'fixture-' + str(len(requests)),
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': message}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50,
                      'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 100}}, request=request)

    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(handler))
    return requests


def parameters(tmp_path, budget):
    return dict(brief=brief().model_copy(update={'world_start_payload_hash': digest({})}),
        narrative_text=NARRATIVE, archive_id='prehistory-archive:budget-test',
        run_dir=tmp_path / 'run', api_key='fixture', base_url='https://fixture.invalid', budget=budget)


@pytest.mark.asyncio
async def test_zero_budget_stops_before_provider_and_does_not_report_uncertain_billing(tmp_path, monkeypatch):
    from companion_daemon.world_v2.story_import_budget import StoryImportBudget, StoryBudgetExceeded
    requests = install_provider(monkeypatch)
    budget = StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=0, batch_limit_cny=0)
    with pytest.raises(StoryBudgetExceeded):
        await prepare_story_import(**parameters(tmp_path, budget))
    assert requests == []
    assert not list((tmp_path / 'run').rglob('provider-request.json'))
    assert budget.snapshot()['reserved_cny'] == 0
    assert budget.snapshot()['request_count'] == 0


@pytest.mark.asyncio
async def test_all_stages_are_metered_and_captured_resume_is_free(tmp_path, monkeypatch):
    from companion_daemon.world_v2.story_import_budget import StoryImportBudget
    requests = install_provider(monkeypatch)
    budget = StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=1, batch_limit_cny=1)
    params = parameters(tmp_path, budget)
    first = await prepare_story_import(**params)
    report = budget.snapshot()
    assert first is not None
    assert len(requests) == report['request_count'] == 4
    assert report['spent_cny'] > 0 and report['reserved_cny'] == 0
    assert report['unknown_or_inflight_count'] == 0
    assert report['scope'] == 'source_preparation_only'
    assert first == await prepare_story_import(**params)
    assert budget.snapshot() == report and len(requests) == 4


@pytest.mark.asyncio
async def test_timeout_keeps_its_reservation_across_restart_and_never_reissues(tmp_path, monkeypatch):
    from companion_daemon.world_v2.story_import_budget import StoryImportBudget
    requests = install_provider(monkeypatch, timeout=True)
    budget = StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=1, batch_limit_cny=1)
    params = parameters(tmp_path, budget)
    with pytest.raises(httpx.ReadTimeout):
        await prepare_story_import(**params)
    before = budget.snapshot()
    assert len(requests) == before['unknown_or_inflight_count'] == 1
    assert before['reserved_cny'] > 0 and before['spent_cny'] == 0
    resumed = StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=1, batch_limit_cny=1)
    with pytest.raises(RuntimeError, match='billed'):
        await prepare_story_import(**{**params, 'budget': resumed})
    assert resumed.snapshot() == before and len(requests) == 1


@pytest.mark.asyncio
async def test_invalid_completed_plan_and_its_correction_both_count_toward_budget(tmp_path, monkeypatch):
    from companion_daemon.world_v2.story_import_budget import StoryImportBudget
    requests = install_provider(monkeypatch, invalid_plan=True)
    budget = StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=1, batch_limit_cny=1)
    with pytest.raises(ValueError, match='every new event'):
        await prepare_story_import(**parameters(tmp_path, budget))
    report = budget.snapshot()
    assert len(requests) == report['request_count'] == 4
    assert report['spent_cny'] > 0 and report['reserved_cny'] == 0
    assert all(r['charged'] is not None for r in report['requests'])


@pytest.mark.asyncio
async def test_unknown_bill_consumes_shared_task_budget_for_other_batches(tmp_path, monkeypatch):
    from companion_daemon.world_v2.story_import_budget import StoryImportBudget, StoryBudgetExceeded
    requests = install_provider(monkeypatch, timeout=True)
    probe = StoryImportBudget(tmp_path / 'probe.sqlite', total_limit_cny=1, batch_limit_cny=1)
    probe_params = parameters(tmp_path / 'probe', probe)
    with pytest.raises(httpx.ReadTimeout):
        await prepare_story_import(**probe_params)
    reserve = probe.snapshot()['reserved_cny']
    shared = StoryImportBudget(tmp_path / 'shared.sqlite', total_limit_cny=reserve * 1.5,
                               batch_limit_cny=reserve * 1.5)
    with pytest.raises(httpx.ReadTimeout):
        await prepare_story_import(**parameters(tmp_path / 'first', shared))
    before = len(requests)
    with pytest.raises(StoryBudgetExceeded):
        await prepare_story_import(**parameters(tmp_path / 'second', shared))
    assert len(requests) == before
    assert shared.snapshot()['request_count'] == 1


@pytest.mark.asyncio
async def test_capture_recovers_charge_after_process_interruption_without_a_second_request(tmp_path, monkeypatch):
    from companion_daemon.world_v2.story_import_budget import StoryImportBudget
    requests = install_provider(monkeypatch)
    budget = StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=1, batch_limit_cny=1)
    original = budget.settle_capture
    def interrupt(*args):
        raise RuntimeError('fixture interruption after complete wire capture')
    monkeypatch.setattr(budget, 'settle_capture', interrupt)
    with pytest.raises(RuntimeError, match='fixture interruption'):
        await prepare_story_import(**parameters(tmp_path, budget))
    assert len(requests) == 1 and budget.snapshot()['reserved_cny'] > 0
    monkeypatch.setattr(budget, 'settle_capture', original)
    budget.reconcile()
    assert budget.snapshot()['reserved_cny'] == 0 and budget.snapshot()['spent_cny'] > 0
    assert len(requests) == 1
    assert await prepare_story_import(**parameters(tmp_path, budget)) is not None
    assert len(requests) == 4


@pytest.mark.asyncio
async def test_run_cannot_reset_cost_by_switching_budget_file(tmp_path, monkeypatch):
    from companion_daemon.world_v2.story_import_budget import StoryImportBudget, StoryBudgetPolicyError
    requests = install_provider(monkeypatch)
    first = StoryImportBudget(tmp_path / 'first.sqlite', total_limit_cny=1, batch_limit_cny=1)
    await prepare_story_import(**parameters(tmp_path, first))
    second = StoryImportBudget(tmp_path / 'second.sqlite', total_limit_cny=1, batch_limit_cny=1)
    with pytest.raises(StoryBudgetPolicyError, match='different budget ledger'):
        await prepare_story_import(**parameters(tmp_path, second))
    assert len(requests) == 4 and second.snapshot()['request_count'] == 0


@pytest.mark.asyncio
async def test_native_cny_price_is_pinned_and_not_replaced_by_usd_conversion(tmp_path, monkeypatch):
    from companion_daemon.world_v2 import story_import_budget as module
    from companion_daemon.usage_metrics import ModelPrice
    install_provider(monkeypatch)
    monkeypatch.setattr(module, 'resolve_model_price', lambda *a, **k: ModelPrice(
        model='deepseek-flash', version='fixture-native', cache_hit_usd_per_million=99,
        cache_miss_usd_per_million=99, output_usd_per_million=99,
        cache_hit_cny_per_million=.25, cache_miss_cny_per_million=2, output_cny_per_million=3))
    budget = module.StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=1, batch_limit_cny=1)
    await prepare_story_import(**parameters(tmp_path, budget))
    # Four actual wire responses: 100 uncached input * 2 + 50 output * 3 = 350 micro-CNY each.
    assert budget.snapshot()['spent_cny'] == .0014
    monkeypatch.setattr(module, 'resolve_model_price', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must use captured price')))
    budget.reconcile()
    assert budget.snapshot()['spent_cny'] == .0014


def test_prepare_cli_reports_budget_stop_and_writes_a_cost_report(tmp_path, monkeypatch, capsys):
    import prepare_story_import as cli
    requests = install_provider(monkeypatch)
    monkeypatch.setenv('DEEPSEEK_DEBUG_API_KEY', 'fixture')
    b = brief().model_copy(update={'world_start_payload_hash': digest({})})
    (tmp_path / 'brief.json').write_text(b.model_dump_json())
    (tmp_path / 'source.md').write_text(NARRATIVE)
    code = cli.main(['prepare', '--brief', str(tmp_path / 'brief.json'), '--narrative', str(tmp_path / 'source.md'),
        '--archive-id', 'prehistory-archive:cli-budget', '--run-dir', str(tmp_path / 'run'),
        '--batch-budget-cny', '0', '--total-budget-cny', '0'])
    assert code == 2 and requests == []
    assert json.loads(capsys.readouterr().out)['status'] == 'budget_exceeded'
    report = json.loads((tmp_path / 'run/budget-report.json').read_text())
    assert report['request_count'] == 0 and report['scope'] == 'source_preparation_only'


def test_explicit_batch_reallocation_preserves_shared_ceiling_and_audit(tmp_path):
    from companion_daemon.world_v2.story_import_budget import StoryImportBudget, StoryBudgetPolicyError
    budget = StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=5, batch_limit_cny=1)
    with pytest.raises(StoryBudgetPolicyError):
        StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=5, batch_limit_cny=2)
    budget.reallocate_batch_limit(2, reason='One long source needs a larger conservative reserve; total task remains CNY5.')
    reopened = StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=5, batch_limit_cny=2)
    report = reopened.snapshot()
    assert report['total_limit_cny'] == 5 and report['batch_limit_cny'] == 2
    assert report['request_count'] == 0
    assert report['allocation_changes'][0]['old_limit_cny'] == 1
    assert report['allocation_changes'][0]['new_limit_cny'] == 2
    with pytest.raises(ValueError):
        reopened.reallocate_batch_limit(6, reason='Must not raise the task ceiling.')
