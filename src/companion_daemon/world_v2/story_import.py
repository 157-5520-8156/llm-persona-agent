"""History-scoped incremental acceptance over the existing prehistory ledger.

No similarity matching or narrative decisions live here. The submitted mapping
is an author proposal; source review owns its semantic validity.
"""
from __future__ import annotations

from functools import lru_cache

from .character_prehistory import (
    PrehistoryArchiveBasisPin, PrehistoryArchiveDocument, PrehistoryImportContext,
    PrehistoryEventMatch, PrehistoryExistingRecord, digest,
)


def bind_import_context(document: PrehistoryArchiveDocument, *, accepted_archives,
                        world_start_ref: str, world_start_payload_hash: str,
                        world_started_at, biography_events=(), event_matches=(), cross_story_links=(), require_coverage_review=False) -> PrehistoryArchiveDocument:
    document = PrehistoryArchiveDocument.model_validate_json(document.model_dump_json())
    if document.contract != "character-prehistory-archive.2":
        raise ValueError("incremental import requires an unbound structured archive")
    if any((archive.world_id, archive.actor_ref) != (document.world_id, document.actor_ref)
           for archive in accepted_archives):
        raise ValueError("history basis crosses World or actor")
    prior = {record.record_id: (archive, record) for archive in accepted_archives for record in archive.records}
    referenced = {match.prior_record_id for match in event_matches}
    own = {record.record_id for record in document.records}
    for edge in cross_story_links:
        referenced.update({edge.left_record_id, edge.right_record_id} - own)
    referenced.update(ref for episode in document.episodes for ref in episode.record_refs if ref not in own)
    if referenced - prior.keys():
        raise ValueError("story import references an unknown prior record")
    resolved_matches = []
    for match in event_matches:
        archive, record = prior[match.prior_record_id]
        if record.story is None or match.prior_event_ref not in {b.event_ref for b in record.story.blocks}:
            raise ValueError("shared event target lacks exact structured occurrence identity")
        resolved_matches.append(PrehistoryEventMatch(
            **match.model_dump(), canonical_event_ref=canonical_event_ref(archive.manifest(), match.prior_event_ref),
        ))
    context = PrehistoryImportContext(
        world_start_ref=world_start_ref, world_start_payload_hash=world_start_payload_hash,
        world_started_at=world_started_at, require_coverage_review=require_coverage_review,
        base_archives=tuple(sorted((PrehistoryArchiveBasisPin(
            archive_id=archive.archive_id, manifest_hash=digest(archive.manifest()),
        ) for archive in accepted_archives), key=lambda p: p.archive_id)),
        biography_events=tuple(sorted(biography_events, key=lambda p: p.event_ref)),
        existing_records=tuple(PrehistoryExistingRecord(
            archive_id=prior[ref][0].archive_id, archive_manifest_hash=digest(prior[ref][0].manifest()),
            actor_ref=prior[ref][0].actor_ref, record=prior[ref][1],
        ) for ref in sorted(referenced)),
        event_matches=tuple(sorted(resolved_matches, key=lambda p: p.event_ref)),
        cross_story_links=tuple(cross_story_links),
    )
    return PrehistoryArchiveDocument.model_validate_json(document.model_copy(update={
        "contract": "character-prehistory-archive.3", "import_context": context,
    }).model_dump_json())


def validate_import_basis(manifest, *, state):
    from .story_episode import validate_episode_basis
    validate_episode_basis(manifest.episodes, (e for a in state.prehistory_archives
        if a.manifest.actor_ref == manifest.actor_ref for e in a.manifest.episodes))
    context = manifest.import_context
    if context is None:
        if manifest.episodes:
            raise ValueError('episode import requires its pinned history context')
        return
    actual = tuple(sorted((PrehistoryArchiveBasisPin(
        archive_id=a.manifest.archive_id, manifest_hash=digest(a.manifest),
    ) for a in state.prehistory_archives if a.manifest.actor_ref == manifest.actor_ref),
                          key=lambda pin: pin.archive_id))
    if context.base_archives != actual:
        raise ValueError("story import history basis changed; re-review against current history")
    starts = [e for e in state.committed_world_event_refs if e.event_type == "WorldStarted"]
    if len(starts) != 1 or (context.world_start_ref, context.world_start_payload_hash, context.world_started_at) != (
        starts[0].event_id, starts[0].payload_hash, starts[0].logical_time,
    ):
        raise ValueError("story import World start basis changed")
    from .character_prehistory import PrehistoryAuthorityPin
    actual_biography = tuple(sorted((PrehistoryAuthorityPin(event_ref=e.event_id, payload_hash=e.payload_hash)
        for e in state.committed_world_event_refs if e.event_type == "BiographicalTimelineConfigured"),
        key=lambda pin: pin.event_ref))
    if context.biography_events != actual_biography:
        raise ValueError("story import biography basis changed; re-review against current history")

    by_record = {row.record.record_id: row for row in state.prehistory_records}
    archives = {a.manifest.archive_id: a.manifest for a in state.prehistory_archives}
    for prior in context.existing_records:
        row = by_record.get(prior.record.record_id)
        if prior.actor_ref != manifest.actor_ref or row is None or (row.actor_ref, row.archive_id, row.archive_manifest_hash, row.record) != (
            manifest.actor_ref, prior.archive_id, prior.archive_manifest_hash, prior.record,
        ):
            raise ValueError("story import prior record differs from accepted history")
    for match in context.event_matches:
        row = by_record[match.prior_record_id]
        if match.canonical_event_ref != canonical_event_ref(archives[row.archive_id], match.prior_event_ref):
            raise ValueError("story import shared event differs from its accepted identity")


def canonical_event_ref(manifest, event_ref: str) -> str:
    context = manifest.import_context
    if context is not None:
        matched = next((match for match in context.event_matches if match.event_ref == event_ref), None)
        if matched is not None:
            return matched.canonical_event_ref
    return "history-event:" + digest([manifest.world_id, manifest.actor_ref, manifest.archive_id, event_ref])


def validate_context_document(document):
    context = document.import_context
    own = {record.record_id: record for record in document.records}
    prior = {p.record.record_id: p for p in context.existing_records}
    bases = {p.archive_id: p.manifest_hash for p in context.base_archives}
    if set(own) & prior.keys() or document.archive_id in bases:
        raise ValueError("incremental story cannot rewrite accepted identities")
    if any(p.actor_ref != document.actor_ref or bases.get(p.archive_id) != p.archive_manifest_hash
           for p in context.existing_records):
        raise ValueError("story prior record is outside its actor or history basis")
    events = {b.event_ref for record in document.records for b in record.story.blocks}
    used = set()
    for episode in document.episodes:
        if set(episode.record_refs) - (own.keys() | prior.keys()):
            raise ValueError("episode references an unknown record")
        used.update(set(episode.record_refs) & prior.keys())
    for match in context.event_matches:
        target = prior.get(match.prior_record_id)
        if match.event_ref not in events or target is None or target.record.story is None or (
            match.prior_event_ref not in {b.event_ref for b in target.record.story.blocks}
        ):
            raise ValueError("shared story event references an unknown source identity")
        used.add(match.prior_record_id)
    all_records = {**{ref: p.record for ref, p in prior.items()}, **own}
    pairs = set()
    for edge in context.cross_story_links:
        endpoints = {edge.left_record_id, edge.right_record_id}
        if len(endpoints) != 2 or len(endpoints & own.keys()) != 1 or len(endpoints & prior.keys()) != 1:
            raise ValueError(
                f"cross-story edge {edge.left_record_id} -> {edge.right_record_id} needs "
                "one new and one exact prior endpoint; two new endpoints belong to source-local links"
            )
        if frozenset(endpoints) in pairs:
            raise ValueError("duplicate cross-story relation pair")
        pairs.add(frozenset(endpoints))
        used.update(endpoints & prior.keys())
        for ref, block_id, quote in ((edge.left_record_id, edge.left_block_id, edge.left_source_quote),
                                     (edge.right_record_id, edge.right_block_id, edge.right_source_quote)):
            record = all_records[ref]
            block = next((b for b in record.story.blocks if b.block_id == block_id), None) if record.story else None
            source_quote = (block.source_quote if block is not None else
                            record.statement if record.story is None and block_id == record.record_id else None)
            if source_quote is None or quote not in source_quote:
                raise ValueError("cross-story evidence is not an exact endpoint quote")
        new_record = own[next(iter(endpoints & own.keys()))]
        if not any(edge.bridge_source_quote in b.source_quote for b in new_record.story.blocks):
            raise ValueError("cross-story bridge is absent from the new source blocks")
    if used != set(prior):
        raise ValueError("story import context contains unused prior records")


def validate_import_record(manifest, record):
    """Replay checks each arriving endpoint before it can become readable."""
    context = manifest.import_context
    if context is None:
        return
    if record.story is None:
        raise ValueError("incremental story import requires structured records")
    for edge in context.cross_story_links:
        if record.record_id not in {edge.left_record_id, edge.right_record_id}:
            continue
        block_id, quote = ((edge.left_block_id, edge.left_source_quote)
                           if edge.left_record_id == record.record_id else
                           (edge.right_block_id, edge.right_source_quote))
        block = next((b for b in record.story.blocks if b.block_id == block_id), None)
        if block is None or quote not in block.source_quote:
            raise ValueError("cross-story evidence is not an exact endpoint quote")
        if not any(edge.bridge_source_quote in b.source_quote for b in record.story.blocks):
            raise ValueError("cross-story bridge is absent from the new source blocks")


@lru_cache(maxsize=128)
def validate_complete_import(manifest, records):
    """Cache only immutable source structure, never Memory eligibility."""
    PrehistoryArchiveDocument(
        contract="character-prehistory-archive.3", archive_id=manifest.archive_id,
        world_id=manifest.world_id, actor_ref=manifest.actor_ref,
        source_artifact_ref=manifest.source_artifact_ref, import_context=manifest.import_context,
        entities=manifest.entities, records=records, episodes=manifest.episodes,
    )


def import_biography_pins(brief):
    from .character_prehistory import PrehistoryAuthorityPin
    if brief.world_start_payload_hash is None:
        raise ValueError("incremental import requires a fresh brief with World start payload hash")
    biography = []
    for entry in brief.profile.get("accepted_biographical_chronology", ()):
        if not isinstance(entry, dict) or "payload_hash" not in entry:
            raise ValueError("incremental import requires exact biography payload pins in its brief")
        biography.append(PrehistoryAuthorityPin(event_ref=entry["event_ref"], payload_hash=entry["payload_hash"]))
    return tuple(biography)


def bind_context_from_brief(document, brief, *, event_matches=(), cross_story_links=(), require_coverage_review=False):
    biography = import_biography_pins(brief)
    return bind_import_context(document, accepted_archives=brief.accepted_archives,
        world_start_ref=brief.world_start_event_ref, world_start_payload_hash=brief.world_start_payload_hash,
        world_started_at=brief.world_started_at, biography_events=tuple(biography),
        event_matches=event_matches, cross_story_links=cross_story_links, require_coverage_review=require_coverage_review)


def validate_context_brief(document, brief):
    from .character_prehistory import StoryEventMatchDraft
    context = document.import_context
    base = document.model_copy(update={"contract": "character-prehistory-archive.2", "import_context": None})
    rebuilt = bind_context_from_brief(base, brief,
        event_matches=tuple(StoryEventMatchDraft(**m.model_dump(exclude={"canonical_event_ref"}))
                            for m in context.event_matches), cross_story_links=context.cross_story_links,
        require_coverage_review=context.require_coverage_review)
    if rebuilt != document:
        raise ValueError("story import context differs from the frozen authoring history basis")
