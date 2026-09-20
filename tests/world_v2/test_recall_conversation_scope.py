"""Conversation membership is scope, not independent retrieval relevance."""

from __future__ import annotations

import pytest

from companion_daemon.world_v2.appraisal_proposal_compiler import AppraisalProposalCompiler
from companion_daemon.world_v2.appraisal_source_identity import conversation_source_cluster_ref
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.ledger_context_resolver import _automatic_recall_link_refs
from companion_daemon.world_v2.recall_index import InMemoryRecallIndex
from companion_daemon.world_v2.schemas import Observation

from test_ledger_context_resolver import NOW, _event, _message_observation
from test_recall_index import CURSOR, _documents, _query


class _ZeroEmbedding:
    version = "zero-scope-fixture.1"
    dimensions = 2

    def embed(self, texts):
        # Isolate structured matching; this fixture claims no semantic quality.
        return tuple((0.0, 0.0) for _ in texts)


def _scope_fixture():
    world_id = "world:recall-conversation-scope"
    ledger = WorldLedger.in_memory(world_id=world_id)
    message = _message_observation(world_id, 1, "新的话题", received_at=NOW)
    ledger.commit(
        [_event(world_id), message],
        expected_world_revision=0,
        expected_deliberation_revision=0,
    )
    observation = Observation.model_validate_json(message.payload_json)
    scope_ref = conversation_source_cluster_ref(
        actor_ref=observation.actor, channel=observation.channel,
    )
    return ledger.project(), observation, scope_ref


def _filtered(projection, refs):
    return _automatic_recall_link_refs(
        projection=projection,
        subject_refs=frozenset(("agent:companion", "user:primary")),
        link_refs=refs,
    )


def test_shared_conversation_alone_no_longer_retrieves_unrelated_appraisal() -> None:
    projection, observation, scope_ref = _scope_fixture()
    # Frozen pre-extraction source-clustering.1 identity, also used by acceptance.
    assert scope_ref == "conversation:c6dabbbe4c809a06902acbdec4e4307fa29ba8b3f5375b3f239b5575536e2ef9"
    assert AppraisalProposalCompiler._source_cluster(observation) == scope_ref
    document = _documents()[3].model_copy(update={"link_refs": (scope_ref,)})
    index = InMemoryRecallIndex(embedding=_ZeroEmbedding())
    index.rebuild(cursor=CURSOR, documents=(document,))
    original = _query(query_text="zzzz", link_refs=(scope_ref,))
    old_result = index.search(original)
    assert len(old_result.hits) == 1
    assert old_result.hits[0].match_channels == ("structured",)

    new_query = original.model_copy(update={"link_refs": _filtered(projection, (scope_ref,))})
    assert new_query.link_refs == ()
    assert index.search(new_query).hits == ()
    assert index.snapshot().documents == (document,)
    # Source links/authority and an already pinned query's exact replay survive.
    assert index.search(original) == old_result


@pytest.mark.parametrize("specific_ref", (
    "event:particular-occurrence", "thread:particular-question", "conversation:opaque-event",
))
def test_specific_link_still_retrieves_without_keyword_or_prefix_classification(specific_ref) -> None:
    projection, _, scope_ref = _scope_fixture()
    document = _documents()[3].model_copy(
        update={"link_refs": tuple(sorted((scope_ref, specific_ref)))},
    )
    index = InMemoryRecallIndex(embedding=_ZeroEmbedding())
    index.rebuild(cursor=CURSOR, documents=(document,))
    links = _filtered(projection, (scope_ref, specific_ref))
    assert links == (specific_ref,)
    result = index.search(_query(query_text="zzzz", link_refs=links))
    assert len(result.hits) == 1
    assert result.hits[0].document == document
    assert result.hits[0].match_channels == ("structured",)


def test_specific_link_cannot_override_subject_isolation() -> None:
    projection, _, scope_ref = _scope_fixture()
    specific_ref = "event:shared-reference"
    own = _documents()[3].model_copy(update={"link_refs": (specific_ref,)})
    other = own.model_copy(update={"document_id": "recall:other", "subject_refs": ("user:other",)})
    index = InMemoryRecallIndex(embedding=_ZeroEmbedding())
    index.rebuild(cursor=CURSOR, documents=(own, other))
    query = _query(query_text="zzzz", link_refs=_filtered(projection, (scope_ref, specific_ref)))
    assert tuple(hit.document for hit in index.search(query).hits) == (own,)
    assert set(index.snapshot().documents) == {own, other}
