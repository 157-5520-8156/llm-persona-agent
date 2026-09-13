"""Real client/role adapter and focused-review owner, with captured HTTP fixtures."""
import json

import httpx
from jsonschema import Draft202012Validator
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.life_development_model_adapter import RoleBoundLifeDevelopmentModelAdapter
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.life_development_source_closure import (
    LifeDevelopmentSourceClosureError, novel_origin_review_tool_contract,
    parse_life_development_novel_origin_review,
)
from test_life_development_runtime import (
    WorldLedger, WORLD_ID, NOW, _seed_clock, _manifest, _projection_cursor,
    _novel_book_exchange_draft, parse_world_author_draft, _novel_origin_review,
)
from test_world_stimulus_life_intent import _http_result


def _subject():
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger))
    draft = parse_world_author_draft(
        raw=json.dumps(_novel_book_exchange_draft(wake=wake)), manifest=manifest, logical_time=NOW,
    )
    return draft, manifest


def test_extra_closing_brace_remains_invalid_and_tool_requires_array_findings():
    draft, _ = _subject()
    raw = _novel_origin_review(decision="supported")
    with pytest.raises(LifeDevelopmentSourceClosureError, match="valid JSON"):
        parse_life_development_novel_origin_review(raw=raw+'}', draft=draft)
    verdict = parse_life_development_novel_origin_review(raw=raw, draft=draft).model_dump(mode="json")
    verdict['unsupported_dynamic_life_directions'] = []
    contract = novel_origin_review_tool_contract(draft)
    schema = contract['tools'][0]['function']['parameters']
    Draft202012Validator(schema).validate({'review':verdict})
    assert schema['properties']['review']['properties']['unsupported_claims']['type'] == 'array'
    assert 'unsupported_claims' in schema['properties']['review']['required']


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_count', [0, 1, 2])
async def test_focused_owner_uses_strict_tool_and_keeps_one_complete_reselection(invalid_count):
    draft, manifest = _subject()
    accepted = parse_life_development_novel_origin_review(
        raw=_novel_origin_review(decision="supported"), draft=draft,
    ).model_dump(mode="json")
    accepted["unsupported_dynamic_life_directions"] = []
    requests, usage = [], []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == '/beta/chat/completions'
        assert 'response_format' not in body
        assert body['tool_choice']['function']['name'] == 'life_novel_origin_review_v1'
        assert body['tools'][0]['function']['strict'] is True
        value = {'review': {'decision':'supported', 'reason':'ok', 'unexpected':True}} if len(requests)<=invalid_count else {'review':accepted}
        if len(requests)>invalid_count:
            Draft202012Validator(body['tools'][0]['function']['parameters']).validate(value)
        return _http_result(body,value)

    provider = DeepSeekChatModel('offline-fixture','https://fixture.invalid','deepseek-v4-flash',thinking_enabled=False,transport=httpx.MockTransport(respond),usage_observer=usage.append)
    adapter = RoleBoundLifeDevelopmentModelAdapter(model=provider,role='world_author_source_reviewer')
    runtime = object.__new__(LifeDevelopmentRuntime)
    runtime._novel_origin_critic = adapter
    runtime._novel_origin_critic_model_id = adapter.model
    messages = [{'role':'system','content':'Review only the supplied truth origin.'},{'role':'user','content':'{"fixture":"same pinned subject"}'}]
    try:
        run = await runtime._novel_origin_review(messages=messages,draft=draft,manifest=manifest)
        assert run.succeeded is (invalid_count<2)
        assert len(requests)==len(usage)==(1 if invalid_count==0 else 2)
        assert run.attempts[0].raw_output is not None
        if invalid_count:
            assert requests[1]['messages'][:2] == requests[0]['messages']
            assert requests[1]['tools'] == requests[0]['tools']
            assert run.attempts[0].failure_code == 'main_invalid_output'
        if invalid_count==2:
            assert run.parsed is None
            assert run.attempts[-1].failure_code == 'corrective_invalid'
    finally:
        await provider.aclose()
