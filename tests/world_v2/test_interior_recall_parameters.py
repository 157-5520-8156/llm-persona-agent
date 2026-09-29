"""An authored filter must survive the Core bridge, not become a wider search."""
from types import SimpleNamespace as N

import pytest

from companion_daemon.world_v2.character_interior import CharacterInterior
from companion_daemon.world_v2.character_interior.ports import _InteriorRoleResult
from companion_daemon.world_v2.character_interior.production import _CoordinatorRecallPort
from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
from test_character_interior import _ConsiderThenRecallRole, _Projection, _Recall, _opportunity


def parameters(query='something she may not actually have stored'):
    return CharacterRecallRequest.model_validate_json('''{
        "query_text": "''' + query + '''", "lexical_text": "校刊",
        "memory_kinds": ["episodic", "reflective"], "include_historical": true,
        "occurred_from": "2020-01-01T00:00:00Z", "occurred_to": "2024-01-01T00:00:00Z",
        "link_refs": ["event:known"], "limit": 3
    }''')


@pytest.mark.asyncio
async def test_core_retains_exact_role_owned_filters_through_recall():
    class Role(_ConsiderThenRecallRole):
        async def consider(self, request):
            value = await super().consider(request)
            if value['status'] == 'recall_request':
                value['recall_parameters'] = parameters().model_dump(mode='python')
            return value

    recall = _Recall()
    core = CharacterInterior(projection=_Projection(), role=Role(), recall=recall)
    result = await core.consider(_opportunity())
    assert result.status == 'decided', result.failure_code
    assert len(recall.requests) == 1
    assert recall.requests[0].recall_parameters == parameters()
    await core.consider(_opportunity())
    assert len(recall.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('modern', [False, True])
async def test_coordinator_receives_complete_query_and_legacy_defaults_remain(modern):
    class Coordinator:
        def scheduled_prefetch_token(self, **kwargs):
            return None

        def recall(self, **kwargs):
            self.kwargs = kwargs
            return object()

    class Port(_CoordinatorRecallPort):
        @staticmethod
        def _trace_result(trace, **kwargs):
            return {'sentinel': 'trace'}

    coordinator = Coordinator()
    port = Port(coordinator)
    request = N(cursor=N(world_revision=1, deliberation_revision=1, ledger_sequence=2),
                trigger_ref='trigger', query='query', inner_turn_id='turn')
    if modern:
        request.recall_parameters = parameters('query')
    await port.recall(request)
    actual = coordinator.kwargs['request']
    assert actual == (parameters('query') if modern else CharacterRecallRequest(query_text='query', limit=6))
    request.recall_parameters = parameters('different')
    with pytest.raises(ValueError, match='differ'):
        await port.recall(request)


def test_legacy_role_result_bytes_omit_new_field_and_new_parameters_bind_query():
    old = dict(status='recall_request', summary='想找找', recall_query='query')
    value = _InteriorRoleResult.model_validate(old)
    assert 'recall_parameters' not in value.model_dump(mode='json')
    with pytest.raises(ValueError, match='must match'):
        _InteriorRoleResult.model_validate({**old, 'recall_parameters': parameters()})
    modern = _InteriorRoleResult.model_validate({**old, 'recall_parameters': parameters('query')})
    assert _InteriorRoleResult.model_validate_json(modern.model_dump_json()) == modern


@pytest.mark.asyncio
async def test_inbound_faculty_forwards_filters_along_with_private_self_lineage(monkeypatch):
    import test_character_interior_private_self_lineage as fixture
    from companion_daemon.world_v2.character_interior.inbound_author import _InboundRecallRequested

    requests = []
    class Cognition(fixture._RecallingInboundCognition):
        async def propose(self, request):
            try:
                return await super().propose(request)
            except _InboundRecallRequested as choice:
                choice.recall_parameters = parameters(choice.query)
                raise

    class Recall(fixture._Recall):
        async def recall(self, request):
            requests.append(request)
            return await super().recall(request)

    monkeypatch.setattr(fixture, '_RecallingInboundCognition', Cognition)
    monkeypatch.setattr(fixture, '_Recall', Recall)
    await fixture.test_inbound_recall_preserves_the_model_authored_initial_private_self()
    assert len(requests) == 1
    assert requests[0].recall_parameters == parameters('the walk this rain brought back')


@pytest.mark.asyncio
async def test_real_recall_index_filters_and_trace_match_the_authored_request():
    from companion_daemon.world_v2.recall_runtime import TrustedRecallTrace
    from companion_daemon.world_v2.recall_runtime import RecallCoordinator, verify_trusted_recall_trace
    from companion_daemon.world_v2.recall_index import InMemoryRecallIndex
    from test_recall_attribution import _documents, CURSOR, NOW, LexicalOnly

    documents = _documents()
    assert {d.memory_kind for d in documents} == {'semantic', 'episodic'}
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=CURSOR, documents=documents)
    coordinator = RecallCoordinator.from_built_index(
        index=index, cursor=CURSOR, actor_ref='agent:companion',
        subject_refs=('agent:companion', 'user:primary'), logical_time=NOW,
        trigger_ref='event:observation:1',
    )
    request = CharacterRecallRequest(query_text='盖碗泡凤凰单丛', memory_kinds=('episodic',),
                                     include_historical=True, limit=2)
    try:
        result = await _CoordinatorRecallPort(coordinator).recall(N(
            cursor=CURSOR, trigger_ref='event:observation:1', query=request.query_text,
            recall_parameters=request, inner_turn_id='filtered-query',
            world_id='world:test', actor_ref='agent:companion',
        ))
        trace = TrustedRecallTrace.model_validate_json(result['recall_trace_json'])
        audit = verify_trusted_recall_trace(trace)
        assert audit.request == request
        assert audit.query.memory_kinds == ('episodic',) and audit.query.limit == 2
        assert audit.hits and all(h.document.memory_kind == 'episodic' for h in audit.hits)
    finally:
        coordinator.close()
