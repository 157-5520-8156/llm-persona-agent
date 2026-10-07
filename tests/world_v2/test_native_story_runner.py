"""Native authoring uses one metered source-author call, with no reviewer lane."""
import json

import httpx
import pytest

from companion_daemon.world_v2.native_story_runner import author_native_story
from companion_daemon.world_v2.story_import_budget import StoryImportBudget
from test_native_story_compiler import source_value, authoring_brief


@pytest.mark.asyncio
async def test_native_author_compile_and_resume_only_charge_once(tmp_path, monkeypatch):
    from companion_daemon import llm
    monkeypatch.setattr(llm, 'shared_deepseek_circuit_breaker', llm.ProviderCircuitBreaker)
    raw = source_value()
    raw['scenes'][0]['blocks'][1]['block_id'] = 'boat-hidden'
    header = {k: v for k, v in raw.items() if k not in {'entities', 'scenes', 'links', 'legacy_links', 'open_questions'}}
    batch = {k: raw[k] for k in ('entities', 'scenes', 'links', 'legacy_links', 'open_questions')}
    plan = {'header': header, 'archive_id': 'prehistory-archive:native-source-fixture',
        'creative_direction': 'Fixture shared scene.', 'phases': [{'prefix': 'boat',
            'start': '2015-01-01T00:00:00Z', 'end': '2015-12-31T23:59:59Z'}]}
    requests = []
    async def handler(request):
        packet = json.loads(request.content)
        requests.append(packet)
        assert 'native-story-author-request.2' in packet['messages'][1]['content']
        return httpx.Response(200, json={'id': 'fixture-native-author', 'choices': [{'index': 0,
            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'native', 'type': 'function', 'function': {'name': 'story_stage_result',
                    'arguments': json.dumps(batch, ensure_ascii=False)}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}, request=request)
    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda **_: httpx.MockTransport(handler))
    budget = StoryImportBudget(tmp_path / 'budget.sqlite', total_limit_cny=1, batch_limit_cny=1)
    args = dict(plan=plan, brief=authoring_brief(), run_dir=tmp_path / 'author', budget=budget,
                api_key='fixture', base_url='https://fixture.invalid')
    first = await author_native_story(**args)
    assert first == await author_native_story(**args)
    assert len(requests) == budget.snapshot()['request_count'] == 1
    assert budget.snapshot()['covered_stages'] == ['source_authoring']
    assert 'source_authoring' not in budget.snapshot()['excluded_stages']
    assert first[1].compilation is not None and len(first[1].document.records) == 1


def test_native_compile_cli_creates_readable_artifacts_without_models(tmp_path, capsys):
    import compile_native_story
    (tmp_path / 'source.json').write_text(json.dumps(source_value()))
    (tmp_path / 'brief.json').write_text(authoring_brief().model_dump_json())
    compile_native_story.main(['compile', '--source', str(tmp_path / 'source.json'),
        '--brief', str(tmp_path / 'brief.json'), '--archive-id', 'prehistory-archive:cli-native',
        '--out-dir', str(tmp_path / 'compiled')])
    result = json.loads(capsys.readouterr().out)
    assert result['model_calls'] == 0 and result['independent_model_review'] is False
    assert (tmp_path / 'compiled/compiled-package.json').exists()
    text = (tmp_path / 'compiled/life-reading.md').read_text()
    assert '作者世界层；不授予主角记忆' in text and '已有0条历史' in text
