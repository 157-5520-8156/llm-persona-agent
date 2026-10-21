"""Scripted reviewer transport only; verdicts do not establish semantic quality."""
from copy import deepcopy
import json
import httpx
import pytest

from companion_daemon.subscription_usage import BackgroundGLMModel, SubscriptionTokenLedger
from companion_daemon.world_v2.character_interior.core import _restore_prepared_turn
from companion_daemon.world_v2.character_interior.life_source_origin import canonical
import test_life_source_review_runtime as gate


@pytest.mark.asyncio
@pytest.mark.parametrize('carrier', ['json_object', 'json_schema'])
async def test_glm_text_schema_keeps_original_life_review_and_cold_receipt(tmp_path, monkeypatch, carrier):
    original_model = gate._model
    seen = []

    def model_for(handler):
        if not isinstance(handler, gate.ReviewHTTP):
            return original_model(handler)

        async def transport(request):
            body = json.loads(request.content)
            seen.append(body)
            assert 'tools' not in body and 'tool_choice' not in body
            schema = json.loads(body['messages'][-1]['content'])
            assert body['response_format']['type'] == carrier
            if carrier == 'json_schema':
                assert body['response_format']['json_schema'] == {
                    'name':schema['schema_name'], 'strict':True, 'schema':schema['response_schema']}
            # Adapt only the scripted review fixture's HTTP shape. The actual
            # request above is retained, and the original schema/verdict runs.
            fixture = {**body, 'messages':body['messages'][:-1], 'tools':[{
                'type':'function', 'function':{'name':schema['schema_name'], 'parameters':schema['response_schema']}}],
                'tool_choice':{'type':'function', 'function':{'name':schema['schema_name']}}}
            scripted = await handler(httpx.Request('POST', request.url, json=fixture))
            payload = scripted.json()
            raw = payload['choices'][0]['message']['tool_calls'][0]['function']['arguments']
            return httpx.Response(200, json={**payload, 'choices':[{
                'finish_reason':'stop', 'message':{'role':'assistant', 'content':raw}}]})

        return BackgroundGLMModel('fixture', 'https://fixture.invalid/v1', 'GLM-5.3-Flash',
            token_ledger=SubscriptionTokenLedger(tmp_path/'glm-usage.sqlite'),
            tool_result_carrier=carrier, transport=httpx.MockTransport(transport))

    monkeypatch.setattr(gate, '_model', model_for)
    responses, checkpoints, audit, reviews = await gate.run_gate(tmp_path, monkeypatch,
        author=gate._ResponseHTTP(text=gate.FEELING), verdicts=['accepted'])
    assert responses == [gate.FEELING] and len(seen) == 1
    result, snapshot, _, _ = _restore_prepared_turn(canonical(checkpoints[0]), purpose='world_stimulus_appraisal')
    receipt = result.life_source_review.verify(result=result, snapshot=snapshot)
    logical_request = json.loads(receipt.prepared_json)['request']
    assert logical_request['tools'][0]['function']['parameters'] == json.loads(seen[0]['messages'][-1]['content'])['response_schema']


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong_tool', [False, True])
async def test_glm_life_reviewer_uses_auto_and_preserves_bound_receipt(tmp_path, monkeypatch, wrong_tool):
    original_model = gate._model
    original_reply = gate._http_result
    seen = []
    def reply(body, value):
        # Existing fixture names its result through the forced-tool field.
        # Actual request observations below retain the real auto wire.
        fixture = deepcopy(body)
        fixture['tool_choice'] = {'type':'function','function':{'name':body['tools'][0]['function']['name']}}
        response = original_reply(fixture, value)
        if wrong_tool:
            decoded = response.json()
            decoded['choices'][0]['message']['tool_calls'][0]['function']['name'] = 'wrong_reviewer'
            return httpx.Response(200, json=decoded)
        return response
    monkeypatch.setattr(gate, '_http_result', reply)
    def model_for(handler):
        if not isinstance(handler, gate.ReviewHTTP):
            return original_model(handler)
        async def transport(request):
            body = json.loads(request.content)
            seen.append(body)
            assert body['tool_choice'] == 'auto'
            assert 'strict' not in body['tools'][0]['function']
            return await handler(request)
        return BackgroundGLMModel('fixture', 'https://fixture.invalid/v1', 'GLM-5.3-Flash',
            token_ledger=SubscriptionTokenLedger(tmp_path/'glm-usage.sqlite'), transport=httpx.MockTransport(transport))
    monkeypatch.setattr(gate, '_model', model_for)
    responses, checkpoints, audit, reviews = await gate.run_gate(tmp_path, monkeypatch,
        author=gate._ResponseHTTP(text=gate.FEELING), verdicts=['accepted'])
    assert len(seen) == len(reviews.requests) == 1
    if wrong_tool:
        assert responses == checkpoints == []
    else:
        assert responses == [gate.FEELING]
        result, snapshot, _, _ = _restore_prepared_turn(canonical(checkpoints[0]), purpose='world_stimulus_appraisal')
        receipt = result.life_source_review.verify(result=result, snapshot=snapshot)
        logical_request = json.loads(receipt.prepared_json)['request']
        assert logical_request['tool_choice']['function']['name'] == seen[0]['tools'][0]['function']['name']
        assert logical_request['tools'][0]['function']['strict'] is True
        assert logical_request['tools'][0]['function']['parameters'] == seen[0]['tools'][0]['function']['parameters']
