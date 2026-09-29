"""Recall source eligibility must not depend on a foreground display budget."""
from datetime import timedelta

import pytest

from companion_daemon.world_v2.life_content import LifeContentCompiler
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore, StoredLifeContent, life_content_payload_hash,
)
from test_life_content import _projection_with_bound_content, _cursor


def archive():
    state, descriptor, _ = _projection_with_bound_content()
    occurrence = state.world_occurrences[0]
    refs = {r.event_id: r for r in state.committed_world_event_refs}
    source = refs[descriptor.source_event_ref]
    accepted = refs[descriptor.descriptor_event_ref]
    store = InMemoryImmutableLifeContentStore()
    occurrences, descriptors, events = [], [], []
    for i in range(6):
        text = ('旧照片只挑出四张。' if i == 0 else '新的普通环境记录。') * 90
        digest = life_content_payload_hash(text)
        oid, ref, event_id, descriptor_id = (f'occurrence:{i}', f'content:{i}',
                                            f'settled:{i}', f'described:{i}')
        at = state.logical_time - timedelta(minutes=6 - i)
        occurrences.append(occurrence.model_copy(update={
            'occurrence_id': oid, 'settled_at': at, 'result_payload_ref': ref,
            'result_payload_hash': digest, 'settlement_event_ref': event_id,
        }))
        descriptors.append(descriptor.model_copy(update={
            'content_id': f'content-id:{i}', 'content_ref': ref,
            'content_payload_hash': digest, 'source_entity_id': oid,
            'source_event_ref': event_id, 'descriptor_event_ref': descriptor_id,
        }))
        events.extend((source.model_copy(update={'event_id': event_id, 'logical_time': at}),
                       accepted.model_copy(update={'event_id': descriptor_id, 'logical_time': at})))
        store.put_if_absent(StoredLifeContent(content_ref=ref, content_kind='occurrence_result',
                                             content_payload_hash=digest, text=text))
    state = state.model_copy(update={
        'world_occurrences': tuple(occurrences), 'life_content_descriptors': tuple(descriptors),
        'committed_world_event_refs': (*state.committed_world_event_refs, *events),
    })
    return state, store


def read(state, store, **kwargs):
    return LifeContentCompiler(store=store).compile(
        projection=state, cursor=_cursor(state), actor_ref='actor:companion',
        viewer_privacy_ceiling='private', **kwargs,
    )


def test_older_result_survives_for_recall_while_display_stays_bounded():
    state, store = archive()
    display = read(state, store)
    corpus = read(state, store, budget=None)
    assert sum(x.text_characters() for x in display.settled_items) <= 1440
    assert all(x.source_entity_id != 'occurrence:0' for x in display.settled_items)
    assert any(s.reason == 'budget_exhausted' for s in display.suppressions)
    assert len(corpus.settled_items) == 6
    old = next(x for x in corpus.settled_items if x.source_entity_id == 'occurrence:0')
    assert old.text == '旧照片只挑出四张。' * 90
    assert not old.truncated
    assert read(state, store) == display


@pytest.mark.parametrize('fault', ['privacy', 'limited', 'missing', 'source_hash'])
def test_archive_read_keeps_authority_and_privacy_gates(fault):
    state, store = archive()
    descriptor = state.life_content_descriptors[0]
    kwargs = {}
    if fault == 'privacy':
        descriptor = descriptor.model_copy(update={'privacy_class': 'withhold'})
    elif fault == 'limited':
        kwargs['user_channel_limited_content_refs'] = frozenset({descriptor.content_ref})
    elif fault == 'missing':
        original = store
        class Missing:
            def read_exact(self, *, content_ref):
                return None if content_ref == descriptor.content_ref else original.read_exact(content_ref=content_ref)
        store = Missing()
    else:
        descriptor = descriptor.model_copy(update={'source_payload_hash': 'f' * 64})
    state = state.model_copy(update={'life_content_descriptors': (descriptor, *state.life_content_descriptors[1:])})
    result = read(state, store, budget=None, **kwargs)
    assert all(x.source_entity_id != 'occurrence:0' for x in result.settled_items)
    assert len(result.settled_items) == 5
    assert any(s.source_entity_id == 'occurrence:0' for s in result.suppressions)
