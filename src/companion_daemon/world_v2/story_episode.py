"""Authored episode membership, not another source of lived facts or decisions."""
from __future__ import annotations

from pydantic import Field, model_validator
from .schema_core import FrozenModel


class StoryEpisode(FrozenModel):
    contract: str = Field(default='story-episode.1', pattern=r'^story-episode\.1$')
    episode_ref: str = Field(pattern=r'^story-episode:[a-z0-9][a-z0-9._-]*$', max_length=180)
    title: str = Field(min_length=1, max_length=200, description='Author-only title; never actor reading.')
    revision: int = Field(ge=1)
    predecessor_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$', exclude_if=lambda v: v is None)
    record_refs: tuple[str, ...] = Field(min_length=1, max_length=128,
        description='Authored process order. Does not assert exact dates or make any record readable.')

    @model_validator(mode='after')
    def explicit_revision_and_members(self):
        if (self.revision == 1) != (self.predecessor_hash is None):
            raise ValueError('episode revision requires its exact predecessor')
        if len(set(self.record_refs)) != len(self.record_refs) or any(
                not ref.startswith('prehistory-record:') for ref in self.record_refs):
            raise ValueError('episode members must be distinct historical record references')
        return self


def validate_episode_basis(episodes, prior_episodes):
    from .character_prehistory import digest
    latest = {}
    for episode in prior_episodes:
        old = latest.get(episode.episode_ref)
        if old is None or episode.revision > old.revision:
            latest[episode.episode_ref] = episode
    for episode in episodes:
        old = latest.get(episode.episode_ref)
        if old is None:
            if episode.revision != 1:
                raise ValueError('episode predecessor is unavailable')
        elif episode.revision != old.revision + 1 or episode.predecessor_hash != digest(old):
            raise ValueError('episode revision does not extend the accepted predecessor')


from .prehistory_story import StoryRelationAuthority  # noqa: E402


class EpisodeMembershipReading(FrozenModel):
    episode_ref: str = Field(pattern=r'^story-episode-view:[0-9a-f]{64}$')
    position: int = Field(ge=0, description='Order among currently readable members, never archive count.')
    authority: StoryRelationAuthority


def readable_episode_memberships(*, archives, actor_ref, world_id, visible_record_ids):
    """Only opaque navigation and currently readable members leave the archive."""
    from .character_prehistory import digest
    latest = {}
    for archive in archives:
        if archive.manifest.actor_ref != actor_ref:
            continue
        for episode in archive.manifest.episodes:
            old = latest.get(episode.episode_ref)
            if old is None or episode.revision > old[0].revision:
                latest[episode.episode_ref] = (episode, archive)
    result = {}
    for episode, archive in latest.values():
        refs = [ref for ref in episode.record_refs if ref in visible_record_ids]
        for position, ref in enumerate(refs):
            result.setdefault(ref, []).append(EpisodeMembershipReading(
                episode_ref='story-episode-view:' + digest([world_id, actor_ref, episode.episode_ref]),
                position=position, authority=StoryRelationAuthority(event_ref=archive.accepted_event_ref,
                    world_revision=archive.accepted_world_revision, payload_hash=archive.accepted_payload_hash)))
    return {ref: tuple(sorted(items, key=lambda i: i.episode_ref)) for ref, items in result.items()}


from typing import Literal  # noqa: E402


class EpisodeWindowRequest(FrozenModel):
    episode_ref: str = Field(pattern=r'^story-episode-view:[0-9a-f]{64}$')
    mode: Literal['overview', 'detail'] = 'detail'
    after_record_ref: str | None = Field(default=None, pattern=r'^prehistory-record:[a-z0-9][a-z0-9._-]*$',
        exclude_if=lambda v: v is None)

    @model_validator(mode='after')
    def continuation_is_detail(self):
        if self.mode == 'overview' and self.after_record_ref is not None:
            raise ValueError('episode overview cannot use a detail continuation')
        return self


def select_episode_page(*, eligible_rows, ranked, request, limit, index_version):
    from .recall_index import RecallHit, _canonical_json, RECALL_STORY_MODEL_READING_MAX_BYTES, RECALL_STORY_RESULT_MAX_BYTES
    from .recall_model_reading import interior_recall_item
    by_id = {hit.document.document_id: hit for _, _, hit in ranked}
    records = {}
    for document, _ in eligible_rows:
        if document.context_only or document.prehistory is None:
            continue
        member = next((m for m in document.prehistory.episodes if m.episode_ref == request.episode_ref), None)
        if member is None:
            continue
        ref = document.source_item_ref.split(':window:')[0]
        old = records.get(ref)
        if old is None or (document.source_window_start or 0) < (old[1].source_window_start or 0):
            records[ref] = (member.position, document)
    ordered = sorted(records, key=lambda ref: (records[ref][0], ref))
    if request.after_record_ref is not None:
        if request.after_record_ref not in records:
            return ()  # No hidden/missing member can serve as a readable cursor.
        ordered = ordered[ordered.index(request.after_record_ref) + 1:]
    count = min(6, limit)
    if request.mode == 'overview' and len(ordered) > count:
        # Representative source entries only; no generated summary or claim
        # that intermediate events did not happen.
        indices = [round(i * (len(ordered)-1) / (count-1)) for i in range(count)] if count > 1 else [0]
        ordered = [ordered[i] for i in indices]
    selected = []
    for ref in ordered:
        document = records[ref][1].model_copy(update={'retrieval_text': None})
        hit = by_id.get(document.document_id)
        hit = hit.model_copy(update={'document': document}) if hit is not None else RecallHit(
            document=document, match_channels=('structured',), score_bp=0, lexical_score_bp=0,
            dense_score_bp=0, temporal_score_bp=0, structured_score_bp=10_000, accessibility_offset_bp=0)
        candidate = (*selected, hit)
        if len(_canonical_json({'items': [interior_recall_item(h.document, index_version=index_version)
                for h in candidate]}).encode()) > RECALL_STORY_MODEL_READING_MAX_BYTES or len(
                _canonical_json([h.model_dump(mode='json') for h in candidate]).encode()) > RECALL_STORY_RESULT_MAX_BYTES:
            break  # Detail pagination must not silently skip the next source.
        selected.append(hit)
        if len(selected) >= count:
            break
    return tuple(selected)
