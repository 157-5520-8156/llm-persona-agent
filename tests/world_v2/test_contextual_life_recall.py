"""Synthetic source fixtures exercise long-history/window/causal retrieval.

These are typed test inputs, not events accepted in a real character World.
"""
from datetime import UTC, datetime, timedelta
import hashlib
import json

from companion_daemon.world_v2.life_content import LifeContentExcerpt
from companion_daemon.world_v2.life_content_reading import WorldConsequenceReading, WorldEnvironmentExcerpt, AuthorizedAttemptExcerpt
from companion_daemon.world_v2.world_consequence_contract import ActivityExecutionBinding
from companion_daemon.world_v2.world_life_context import (WorldLifeContextItem, WorldLifeSourceBinding,
    ActiveActivityContextItem, AcceptedActivityIntention, ActiveWorldOccurrenceProposalBinding)
from companion_daemon.world_v2.recall_corpus import RecallCorpusCompiler, RecallCorpusSources
from companion_daemon.world_v2.recall_index import RecallCursor, RecallQuery, InMemoryRecallIndex, FeatureHashRecallEmbedding
from companion_daemon.world_v2.recall_model_reading import interior_recall_item

ACTOR = 'actor:companion'
NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
CURSOR = RecallCursor(world_revision=10000, deliberation_revision=0, ledger_sequence=10000)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def life(name, text, *, plan=None, days=0, private='private'):
    at = NOW - timedelta(days=days)
    source = WorldLifeSourceBinding(authority_event_ref='settled:'+name, authority_world_revision=10,
                                   authority_payload_hash=digest('settled:'+name))
    world = WorldConsequenceReading(environment=WorldEnvironmentExcerpt(text='周围环境没有新的变化。'))
    if plan is not None:
        binding = ActivityExecutionBinding(actor_ref=ACTOR, source_event_ref='started:'+plan,
            source_world_revision=2, source_payload_hash=digest(plan), privacy_class=private,
            source_event_type='ActivityStarted', plan_id='plan:'+plan, activity_id='activity:'+plan,
            plan_entity_revision=2)
        world = world.model_copy(update={'authorized_attempt_result': AuthorizedAttemptExcerpt(text=text, execution_binding=binding)})
    else:
        world = world.model_copy(update={'environment': WorldEnvironmentExcerpt(text=text)})
    content_hash = digest(world.model_dump_json())
    content = LifeContentExcerpt(content_id='descriptor:'+name, content_kind='occurrence_result',
        content_ref='content:'+name, content_payload_hash=content_hash, truncated=False, privacy_class=private,
        source_entity_id='occurrence:'+name, source_entity_revision=3,
        authority_event_ref=source.authority_event_ref, authority_world_revision=10,
        authority_payload_hash=source.authority_payload_hash,
        descriptor_event_ref='described:'+name, descriptor_world_revision=11,
        descriptor_payload_hash=digest('described:'+name), world_consequence=world)
    return WorldLifeContextItem(occurrence_id='occurrence:'+name, occurrence_entity_revision=3,
        participant_refs=(ACTOR,), result_id='result:'+name, result_payload_ref=content.content_ref,
        result_payload_hash=content_hash, settled_at=at, privacy_class=private, source=source, content=content)


def intention(plan, text):
    binding = WorldLifeSourceBinding(authority_event_ref='started:'+plan, authority_world_revision=2,
                                    authority_payload_hash=digest(plan))
    return ActiveActivityContextItem(activity_event_ref=binding.authority_event_ref, plan_id='plan:'+plan,
        plan_entity_revision=2, owner_actor_ref=ACTOR, activity_kind='self_directed', participant_refs=(ACTOR,),
        active_since=NOW-timedelta(days=40), privacy_class='private',
        accepted_intention=AcceptedActivityIntention(content_ref='intention:'+plan,
            content_payload_hash=digest(text), text=text, truncated=False),
        proposal_source=ActiveWorldOccurrenceProposalBinding(authority_event_ref='proposal:'+plan,
            authority_ledger_sequence=1, authority_payload_hash=digest('proposal:'+plan)),
        source_bindings=(binding, binding.model_copy(update={'authority_event_ref':'planned:'+plan})))


def documents(items):
    return RecallCorpusCompiler().compile(cursor=CURSOR, actor_ref=ACTOR, subject_refs=(ACTOR,),
        sources=RecallCorpusSources(world_life=tuple(items)))


def query(text, **kwargs):
    return RecallQuery(query_text=text, cursor=CURSOR, actor_ref=ACTOR, subject_refs=(ACTOR,),
        viewer_privacy_ceiling='private', at=NOW, accessibility_seed='controlled-recall', limit=4, **kwargs)


def search(docs, text):
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=CURSOR, documents=docs)
    return index.snapshot().search_with_diagnostics(query(text))


def test_old_source_is_indexed_beyond_256_with_small_result_budget():
    old = life('old-umbrella', '修伞完成，换了伞骨。', plan='umbrella', days=30)
    docs = documents([old, *(life(f'noise-{i}', '窗边的树叶轻轻晃动。', days=1)for i in range(300))])
    assert len(docs) > 256
    result, diagnostic = search(docs, '修伞换伞骨的结果')
    assert len(diagnostic.corpus_document_ids) == len(docs)
    assert any('换了伞骨' in hit.document.text for hit in result.hits)
    assert len(json.dumps({'items':[interior_recall_item(h.document)for h in result.hits]},ensure_ascii=False,separators=(',',':')).encode()) <= 6000


def test_late_result_span_reaches_model_with_exact_source():
    text = '普通背景文字。' * 250 + '钥匙柜领取码是K739，已经存入第二格。'
    docs = documents([life('long', text, plan='keys')])
    result, _ = search(docs, '钥匙柜领取码K739')
    hit = next(h for h in result.hits if 'K739' in h.document.text)
    assert hit.document.source_window_start > 1024
    assert hit.document.settled_life.content.world_consequence.authorized_attempt_result.text in text
    assert hit.document.settled_life.result_payload_hash == life('long', text, plan='keys').result_payload_hash
    assert hit.document.settled_life.content.truncated


def test_exact_plan_links_retrieve_newer_result_without_changing_truth_text():
    items = [life('old', '修理台灯只完成了接线，更换灯泡尚未完成。', plan='lamp', days=2),
             life('new', '剩下的一步完成了，通电测试通过。', plan='lamp', days=1)]
    docs = documents(items)
    result, _ = search(docs, '修理台灯接线更换灯泡剩下那一步后来怎么样了')
    assert any(h.document.source_item_ref.startswith('occurrence:new:') for h in result.hits)
    for doc in docs:
        if doc.source_item_ref.startswith('occurrence:new:authorized_attempt_result'):
            assert '台灯' not in doc.retrieval_text
            assert '台灯' not in doc.text
    old_query = query('修理台灯', occurred_to=NOW-timedelta(days=1,hours=12))
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=CURSOR,documents=docs)
    assert all(not h.document.source_item_ref.startswith('occurrence:new:') for h in index.search(old_query).hits)


def test_sqlite_reuses_vectors_when_archive_grows_and_removes_inaccessible_sources(tmp_path):
    from companion_daemon.world_v2.recall_index import SQLiteRecallIndex

    class Counting(FeatureHashRecallEmbedding):
        def __init__(self):
            self.texts = []

        def embed(self, texts):
            self.texts.extend(texts)
            return super().embed(texts)

    provider = Counting()
    index = SQLiteRecallIndex(path=tmp_path / 'recall.sqlite', world_id='test:archive', embedding=provider)
    old = documents([life(f'item{i}', f'原始内容{i}。')for i in range(300)])
    try:
        index.rebuild(cursor=CURSOR, documents=old)
        assert len(provider.texts) == len(old)
        provider.texts.clear()
        added = documents([life('extra', '另一条新结果。')])
        later = CURSOR.model_copy(update={'world_revision':10001, 'ledger_sequence':10001})
        index.rebuild(cursor=later, documents=(*old, *added))
        assert len(provider.texts) == len(added)
        provider.texts.clear()
        # A new pinned source view can withdraw a document; cached vectors
        # must not resurrect it. Previously captured snapshots remain fixed.
        frozen = index.snapshot()
        index.rebuild(cursor=later, documents=added)
        assert not provider.texts
        assert len(index.snapshot().documents) == len(added)
        assert len(frozen.documents) == len(old) + len(added)
    finally:
        index.close()
