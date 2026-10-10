"""Bounded scene completion that preserves independently matched story entries.

Selection changes accessibility only. Every document supplied by the caller
has already passed the current actor, privacy, status and temporal filters.
No archive reads, prose interpretation, new identities or model calls occur.
"""
from __future__ import annotations

from collections import defaultdict

_GRAPH_DISCOVERY_HOPS = 3
_GRAPH_DISCOVERY_RECORDS = 64


def select_scene_context(*, hits, ranked, eligible_rows, query, index_version, question_focus_scores):
    from .recall_index import (
        RecallHit, RECALL_STORY_MODEL_READING_MAX_BYTES, RECALL_STORY_RESULT_MAX_BYTES,
        _canonical_json,
        _lexical_features, _lexical_feature_spans, _matched_query_groups,
    )
    from .recall_model_reading import interior_recall_item

    if query.story_window is not None or not any(hit.document.prehistory is not None for hit in hits):
        return hits

    def record_ref(document):
        return document.source_item_ref.split(':window:', 1)[0]

    def scene_key(document):
        history = document.prehistory
        story = history.story if history is not None else None
        if story is not None:
            occurrences = {('canonical', block.canonical_event_ref) if block.canonical_event_ref
                           else ('local', history.archive_event_ref, story.source_artifact_ref, block.event_ref)
                           for block in story.blocks}
            if len(occurrences) == 1:
                # A later interpretation of an old occurrence remains its own
                # knowledge episode, even when canonical event identity agrees.
                return (document.actor_ref, next(iter(occurrences)), story.known_from, story.known_until)
        return ('record', record_ref(document))

    by_record = defaultdict(list)
    for document, _ in eligible_rows:
        if document.prehistory is not None and not document.context_only:
            by_record[record_ref(document)].append(document)
    if not by_record:
        return hits
    ranked_hits = {hit.document.document_id: hit for _, _, hit in ranked}
    positions = {ref: i for i, (_, ref, _) in enumerate(ranked)}
    groups = defaultdict(set)
    related = defaultdict(set)
    for ref, documents in by_record.items():
        for document in documents:
            groups[scene_key(document)].add(ref)
            for other in document.prehistory.related_record_refs:
                if other != ref and other in by_record:
                    related[ref].add(other)
                    related[other].add(ref)

    def best_hit(ref, *, question_focused=False):
        documents = by_record[ref]
        matches = [ranked_hits[d.document_id] for d in documents if d.document_id in ranked_hits]
        if matches:
            return min(matches, key=lambda h: (
                -question_focus_scores.get(h.document.document_id, 0) if question_focused else 0,
                positions[h.document.document_id]))
        document = min(documents, key=lambda d: (d.source_window_start or 0, d.document_id))
        return RecallHit(document=document, match_channels=('structured',), score_bp=0,
            lexical_score_bp=0, dense_score_bp=0, temporal_score_bp=0,
            structured_score_bp=10_000, accessibility_offset_bp=0)

    selected = []
    selected_records = set()

    def presented(hit):
        document = hit.document
        if document.prehistory is not None:
            return hit.model_copy(update={'document': document.model_copy(update={'retrieval_text': None})})
        return hit

    def fits(additions):
        candidate = (*selected, *additions)
        return (len(candidate) <= 6 and
            len(_canonical_json({'items': [interior_recall_item(h.document, index_version=index_version)
                for h in candidate]}).encode()) <= RECALL_STORY_MODEL_READING_MAX_BYTES and
            len(_canonical_json([h.model_dump(mode='json') for h in candidate]).encode())
                <= RECALL_STORY_RESULT_MAX_BYTES)

    def add(hit):
        ref = record_ref(hit.document)
        hit = presented(hit)
        if ref in selected_records or not fits((hit,)):
            return False
        selected.append(hit)
        selected_records.add(ref)
        return True

    # Preserve explicit structured seeds. A second entry can cover the exact
    # question rather than only the broader recent-dialogue attention cue.
    historical_hits = [hit for hit in hits if hit.document.prehistory is not None]
    primary = next((hit for hit in historical_hits if hit.structured_score_bp > 0), None)
    if primary is None:
        # Preserve the linked-story entry preference of the previous policy;
        # an isolated direct hit must not claim the entire story budget.
        primary = next((hit for hit in historical_hits
                        if related.get(record_ref(hit.document))
                        or len(groups[scene_key(hit.document)]) > 1), historical_hits[0])
    primary_key = scene_key(primary.document)
    spans = _lexical_feature_spans(query.lexical_text or query.query_text)

    def scene_features(key):
        return frozenset(term for ref in groups[key] for document in by_record[ref]
                         for term in _lexical_features(document.text))

    covered = _matched_query_groups(spans, scene_features(primary_key))
    focused = []
    for key, refs in groups.items():
        if key == primary_key:
            continue
        focus = max((question_focus_scores.get(d.document_id, 0)
                     for ref in refs for d in by_record[ref]), default=0)
        if focus == 0:
            continue
        novel = sum(not any(left < old_right and old_left < right for old_left, old_right, _ in covered)
                    for left, right, _ in _matched_query_groups(spans, scene_features(key)))
        representative = min((best_hit(ref, question_focused=True) for ref in refs),
            key=lambda hit: (-question_focus_scores.get(hit.document.document_id, 0),
                             positions.get(hit.document.document_id, len(ranked)), hit.document.document_id))
        focused.append((focus, novel, representative))
    max_novel = max((novel for _, novel, _ in focused), default=1) or 1
    # Relevance dominates; independent question cues provide bounded diversity.
    # No lexical category, named entity or conversational action is hard-coded.
    second = max(focused, key=lambda row: (
        .9 * row[0] + .1 * 10_000 * row[1] / max_novel,
        -positions.get(row[2].document.document_id, len(ranked)),
    ))[2] if focused else None
    literal_entry = None
    if index_version.partition('+embedding:')[0] == 'world-v2-recall-index.hybrid.18' and query.lexical_text:
        # Independent current-question evidence is reserved before a context
        # scene's graph consumes the whole reading budget. This is matching,
        # not a decision about what she should recall or tell the user.
        literal_matches = [hit for _, _, hit in ranked
                           if hit.document.prehistory is not None and hit.lexical_score_bp > 0]
        literal_entry = max(literal_matches, key=lambda hit: (
            hit.lexical_score_bp, hit.score_bp, -positions[hit.document.document_id]),
            default=None)
    priority = [*(hit for hit in hits if hit.structured_score_bp > 0), primary,
                *([literal_entry] if literal_entry is not None else []),
                *([second] if second is not None else hits)]
    if literal_entry is not None and query.limit >= 2:
        priority = [primary, literal_entry, *priority]

    # Reserve independent entries before completing any one's nearby branch.
    seeds = []
    seen_scenes = set()
    for hit in priority:
        if len(seeds) >= query.limit:
            break
        key = scene_key(hit.document)
        if key not in seen_scenes:
            if add(hit):
                seeds.append(hit)
                seen_scenes.add(key)

    if literal_entry is not None and record_ref(literal_entry.document) in selected_records:
        # Preserve one authenticated continuation/origin alongside the topic
        # entry before an unrelated branch consumes every remaining slot.
        neighbours = related.get(record_ref(literal_entry.document), set())
        if neighbours:
            neighbour = min((best_hit(ref, question_focused=True) for ref in neighbours),
                key=lambda hit: (-question_focus_scores.get(hit.document.document_id, 0),
                                 positions.get(hit.document.document_id, len(ranked)),
                                 hit.document.document_id))
            if add(neighbour):
                seen_scenes.add(scene_key(neighbour.document))

    def order(ref):
        hit = best_hit(ref)
        return (positions.get(hit.document.document_id, len(ranked)), ref)

    # Siblings share an authenticated occurrence and acquisition interval;
    # they need no new semantic inference to complete the presented scene.
    for seed in seeds:
        for ref in sorted(groups.get(scene_key(seed.document), ()), key=order):
            add(best_hit(ref))

    def add_scene(hit, *, allow_partial=False):
        parts = [hit, *(best_hit(ref) for ref in sorted(groups.get(scene_key(hit.document), ()), key=order)
                       if ref != record_ref(hit.document))]
        additions = [presented(part) for part in parts if record_ref(part.document) not in selected_records]
        if not additions:
            return False
        if not fits(additions):
            # A large scene cannot hide a strongly relevant endpoint. The
            # ordinary record/continuation coordinates remain explicit and
            # the new reading never claims exhaustive scene/history coverage.
            return add(hit) if allow_partial else False
        for part in additions:
            add(part)
        return True

    # Discover a bounded reachable set before spending model-reading slots.
    # Walking through a bridge does not require presenting every bridge block;
    # each selected endpoint still carries its own immutable source proof.
    frontier = [record_ref(h.document) for h in selected if h.document.prehistory is not None]
    visited = set(frontier)
    reachable = set()
    for _ in range(_GRAPH_DISCOVERY_HOPS):
        if not frontier or len(visited) >= _GRAPH_DISCOVERY_RECORDS:
            break
        candidates = set().union(*(related.get(ref, set()) for ref in frontier)) - visited
        frontier = []
        for ref in sorted(candidates, key=order):
            if len(visited) >= _GRAPH_DISCOVERY_RECORDS:
                break
            visited.add(ref)
            reachable.add(ref)
            frontier.append(ref)

    graph_scenes = {scene_key(best_hit(ref).document) for ref in reachable} - seen_scenes
    represented_features = frozenset(term for hit in selected for term in _lexical_features(hit.document.text))
    represented = _matched_query_groups(spans, represented_features)
    candidates = []
    for key in graph_scenes:
        representative = min((best_hit(ref, question_focused=True) for ref in groups[key]), key=lambda hit: (
            -question_focus_scores.get(hit.document.document_id, 0),
            positions.get(hit.document.document_id, len(ranked)), hit.document.document_id))
        focus = question_focus_scores.get(representative.document.document_id, 0)
        novelty = sum(not any(left < old_right and old_left < right for old_left, old_right, _ in represented)
                      for left, right, _ in _matched_query_groups(spans, scene_features(key)))
        candidates.append((focus, novelty, representative))
    max_novelty = max((n for _, n, _ in candidates), default=1) or 1
    candidates.sort(key=lambda row: (
        -(.9 * row[0] + .1 * 10_000 * row[1] / max_novelty) if row[0] else 0,
        positions.get(row[2].document.document_id, len(ranked)), row[2].document.document_id))
    for _, _, hit in candidates:
        if add_scene(hit, allow_partial=True):
            seen_scenes.add(scene_key(hit.document))

    # Unrelated direct hits only use remaining space after the selected
    # stories' useful, source-bound continuations.
    for hit in hits:
        key = scene_key(hit.document)
        if key not in seen_scenes and add_scene(hit):
            seen_scenes.add(key)
    # Keep each knowledge episode contiguous in the model's reading. This is
    # grouping, not a claim about causal order between independent sources.
    ordered_groups = {}
    for hit in selected:
        ordered_groups.setdefault(scene_key(hit.document), []).append(hit)
    return tuple(hit for group in ordered_groups.values() for hit in group) or hits
