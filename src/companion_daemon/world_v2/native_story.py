"""Native fictional source -> reproducible actor archive. No model review verdict.

The source author declares scenes, knowledge and links. Code only compiles those
declarations under existing history/actor/time authority, retaining world-only prose.
"""
from __future__ import annotations

import json

from datetime import datetime, timedelta
from functools import lru_cache
from itertools import pairwise
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, TypeAdapter, ValidationInfo, field_validator, model_validator

from .schema_core import FrozenModel, PrivacyClass
from .character_prehistory import (
    HistoricalEntity, PrehistoryArchiveDocument, PrehistoryRecord, PrehistoryCompilationReceipt,
    CompiledPrehistoryArchive, CrossStoryRelation, digest,
)
from .prehistory_story import PrehistoryStoryStructure, StoryMaterialBlock, PrehistoryStoryRelation, StoryLinkEvidence, StoryRelationKind
from .prehistory_authoring import PrehistoryAuthoringBrief
from .story_import import bind_context_from_brief
from .story_episode import StoryEpisode, validate_episode_basis


class NativeStoryLine(FrozenModel):
    line_id: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]*$', max_length=100)
    title: str = Field(min_length=1, max_length=200)


class NativeStoryBlock(FrozenModel):
    block_id: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]*$', max_length=100)
    kind: Literal['event', 'historical_interpretation', 'background', 'intention']
    subject_actor_ref: str | None = Field(default=None,
        description='Actor namespace, not historical person ID. For this source owner use source.actor_ref; ordinary event blocks may use null.')
    text: str = Field(min_length=1, max_length=1100)
    knowledge: Literal['experienced', 'told', 'self_reflection', 'known_before_cutoff', 'world_only']
    informant_ref: str | None = None
    known_from: datetime | None = None
    known_until: datetime | None = None
    privacy_class: PrivacyClass

    @model_validator(mode='before')
    @classmethod
    def normalize_explicit_reflection_alias(cls, value):
        # One exact protocol alias, corroborated by the declared knowledge
        # mode. No prose classification and no widening of actor ownership.
        if isinstance(value, dict) and value.get('kind') == value.get('knowledge') == 'self_reflection':
            return {**value, 'kind': 'historical_interpretation'}
        return value

    @field_validator('known_from', 'known_until', mode='before')
    @classmethod
    def acquisition_json_datetime(cls, value, info: ValidationInfo):
        # The alias before-validator exposes a Python dictionary to the strict
        # nullable datetime union. Restore only JSON's documented datetime
        # parsing; Python-mode strings and timezone/boundary checks stay strict.
        if info.mode == 'json' and isinstance(value, str):
            return TypeAdapter(datetime).validate_json(json.dumps(value), strict=True)
        return value

    @model_validator(mode='after')
    def complete_acquisition(self):
        if (self.known_from is None) != (self.known_until is None):
            raise ValueError('acquisition bounds must be both known or both absent')
        if self.known_from is not None and self.known_until < self.known_from:
            raise ValueError('acquisition bounds are reversed')
        if (self.informant_ref is not None) != (self.knowledge == 'told'):
            raise ValueError('reported knowledge requires exactly one declared informant')
        if self.kind == 'historical_interpretation' and self.subject_actor_ref is None:
            raise ValueError('private historical interpretation requires its owner')
        if self.knowledge == 'self_reflection' and self.kind != 'historical_interpretation':
            raise ValueError('self reflection must retain its interpretation kind')
        return self


class NativeStoryScene(FrozenModel):
    event_ref: str = Field(pattern=r'^event:[a-z0-9][a-z0-9:._-]*$', max_length=200)
    title: str = Field(min_length=1, max_length=200)
    occurred_from: datetime | None
    occurred_until: datetime | None
    line_refs: tuple[str, ...] = Field(min_length=1, max_length=8)
    participant_refs: tuple[str, ...] = Field(max_length=32)
    blocks: tuple[NativeStoryBlock, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def occurrence_is_bounded(self):
        if (self.occurred_from is None) != (self.occurred_until is None):
            raise ValueError('scene occurrence bounds must be paired')
        if self.occurred_from is not None and self.occurred_until < self.occurred_from:
            raise ValueError('scene occurrence bounds are reversed')
        if len(set(self.line_refs)) != len(self.line_refs) or len(set(self.participant_refs)) != len(self.participant_refs):
            raise ValueError('scene references must be unique')
        return self


class NativeStoryLink(FrozenModel):
    left_block_id: str
    right_block_id: str
    relation_kind: StoryRelationKind
    bridge_source_quote: str | None = Field(default=None, min_length=1, max_length=1100,
        description='Optional exact quote. The authored typed relation itself is source; compiler binds endpoint text when null.')


class NativeLegacyLink(FrozenModel):
    new_block_id: str
    prior_record_id: str
    prior_source_quote: str | None = Field(default=None, min_length=1, max_length=1200)
    bridge_source_quote: str | None = Field(default=None, min_length=1, max_length=1100)
    relation_kind: StoryRelationKind
    old_is_left: bool = Field(default=True,
        description='True orders the prior record before the new block; false orders the new block before the prior record.')


class NativeStorySource(FrozenModel):
    contract: Literal['native-story-source.1', 'native-story-source.2'] = 'native-story-source.1'
    episodes: tuple[StoryEpisode, ...] = Field(default=(), max_length=32, exclude_if=lambda v: not v)
    source_id: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]*$', max_length=80)
    title: str = Field(min_length=1, max_length=200)
    authored_at: datetime
    author_ref: str = Field(min_length=1, max_length=256)
    actor_ref: str
    knowledge_cutoff: datetime | None = None
    entities: tuple[HistoricalEntity, ...] = Field(max_length=128)
    storylines: tuple[NativeStoryLine, ...] = Field(min_length=1, max_length=32)
    scenes: tuple[NativeStoryScene, ...] = Field(min_length=1, max_length=256)
    links: tuple[NativeStoryLink, ...] = Field(max_length=512)
    legacy_links: tuple[NativeLegacyLink, ...] = Field(max_length=128)
    open_questions: tuple[str, ...] = Field(max_length=64)

    @model_validator(mode='after')
    def identities_and_knowledge_are_explicit(self):
        if self.episodes and self.contract != 'native-story-source.2':
            raise ValueError('episode source requires native story version 2')
        for refs in ([s.event_ref for s in self.scenes], [line.line_id for line in self.storylines],
                     [b.block_id for s in self.scenes for b in s.blocks], [e.entity_ref for e in self.entities]):
            if len(refs) != len(set(refs)):
                raise ValueError('native source identities must be unique')
        lines = {line.line_id for line in self.storylines}
        blocks = {b.block_id: b for s in self.scenes for b in s.blocks}
        for scene in self.scenes:
            if set(scene.line_refs) - lines:
                raise ValueError('scene names an undeclared story line')
            for block in scene.blocks:
                if block.knowledge != 'world_only':
                    if block.privacy_class == 'withhold':
                        raise ValueError('withheld source cannot grant actor knowledge')
                    if block.subject_actor_ref not in {None, self.actor_ref}:
                        raise ValueError('another actor private content cannot become owner knowledge')
                    if block.kind == 'historical_interpretation' and block.subject_actor_ref != self.actor_ref:
                        raise ValueError('interpretation does not belong to the reader')
        for edge in self.links:
            if (edge.left_block_id not in blocks or edge.right_block_id not in blocks
                    or edge.left_block_id == edge.right_block_id):
                raise ValueError('native relation references an unknown or identical block')
            if edge.bridge_source_quote is not None and not any(edge.bridge_source_quote in blocks[ref].text for ref in (edge.left_block_id, edge.right_block_id)):
                raise ValueError('native relation bridge is absent from its endpoints')
        for edge in self.legacy_links:
            if edge.new_block_id not in blocks or (edge.bridge_source_quote is not None and edge.bridge_source_quote not in blocks[edge.new_block_id].text):
                raise ValueError('native legacy bridge is absent from its new source')
        return self


class NativeStoryBatch(FrozenModel):
    """A source author's bounded addition; host binds identity and provenance."""
    episodes: tuple[StoryEpisode, ...] = Field(default=(), max_length=8, exclude_if=lambda v: not v)
    entities: tuple[HistoricalEntity, ...] = Field(max_length=16)
    scenes: tuple[NativeStoryScene, ...] = Field(min_length=1, max_length=20)
    links: tuple[NativeStoryLink, ...] = Field(max_length=64)
    legacy_links: tuple[NativeLegacyLink, ...] = Field(max_length=32)
    open_questions: tuple[str, ...] = Field(max_length=16)


def _compile_document(source, brief, archive_id):
    source = NativeStorySource.model_validate_json(source.model_dump_json())
    brief = PrehistoryAuthoringBrief.model_validate_json(brief.model_dump_json())
    if source.actor_ref != brief.actor_ref:
        raise ValueError('native source actor differs from its authoring authority')
    for entry in brief.profile.get('accepted_biographical_chronology', ()):
        if digest(entry['payload']) != entry.get('payload_hash'):
            raise ValueError('native authoring chronology body differs from its pinned payload')
        chronology = entry['payload'].get('document', {})
        birth_date = chronology.get('birth_date')
        if birth_date and brief.born_at.astimezone(ZoneInfo(entry['payload'].get('timezone_name', 'UTC'))).date().isoformat() != birth_date:
            raise ValueError('native birth bound differs from accepted biography')
    cutoff = source.knowledge_cutoff or brief.world_started_at
    if not brief.born_at < cutoff <= brief.world_started_at:
        raise ValueError('native knowledge cutoff crosses actor lifetime or World start')
    entities = {e.entity_ref: e for a in brief.accepted_archives for e in a.entities}
    for entity in source.entities:
        if entity.entity_ref in entities and entities[entity.entity_ref] != entity:
            raise ValueError('native source cannot rewrite an accepted identity')
        entities[entity.entity_ref] = entity
    source_ref = 'native-story:sha256:' + digest(source)
    prior_events = {block.event_ref for a in brief.accepted_archives for record in a.records
                    if record.story is not None for block in record.story.blocks}
    if any(scene.event_ref in prior_events for scene in source.scenes):
        raise ValueError('native source redeclares an already accepted occurrence; reference its accepted record instead')
    records, by_block, scene_blocks = {}, {}, []
    for scene in source.scenes:
        if (scene.occurred_until is not None and scene.occurred_until >= brief.world_started_at):
            raise ValueError('native source cannot backfill runtime occurrences')
        if any(ref not in entities or entities[ref].kind not in {'person', 'group'} for ref in scene.participant_refs):
            raise ValueError('native scene has an undeclared participant')
        visible = []
        for block in scene.blocks:
            by_block[block.block_id] = block
            if block.knowledge == 'world_only':
                continue
            if block.informant_ref is not None and (
                block.informant_ref not in entities or entities[block.informant_ref].kind != 'person'
            ):
                raise ValueError('native reported source has no declared informant')
            known_from, known_until = block.known_from, block.known_until
            if known_from is None and block.knowledge in {'experienced', 'self_reflection'}:
                known_from, known_until = scene.occurred_from, scene.occurred_until
            uncertain = known_from is None
            if uncertain:
                known_from, known_until = brief.born_at, cutoff
            if not brief.born_at <= known_from <= known_until <= cutoff or known_from >= brief.world_started_at:
                raise ValueError('native knowledge crosses birth, declared cutoff or World start')
            if scene.occurred_from is not None and known_until < scene.occurred_from:
                raise ValueError('past occurrence cannot be known before it happens')
            if (block.knowledge in {'experienced', 'self_reflection'} and scene.occurred_until is not None
                    and known_from > scene.occurred_until):
                raise ValueError('direct experience or reflection must overlap its own scene; later recollection needs a separate scene')
            labels = []
            if block.knowledge == 'told':
                labels.append(f"[由{entities[block.informant_ref].label}讲述的往事；不是本人亲历证明]")
            if block.kind == 'historical_interpretation':
                labels.append('[属于该角色当时的理解，不是当前行为指令]')
            if block.kind == 'intention':
                labels.append('[当时的意向，不能据此认定后来已实行]')
            if uncertain:
                labels.append(f'[具体获知时间未定；源稿仅声明在{cutoff.isoformat()}前已经知道]')
            statement = '\n'.join((*labels, block.text))
            record_id = f'prehistory-record:{source.source_id}.{block.block_id}'
            records[block.block_id] = PrehistoryRecord(
                record_id=record_id, occurred_from=known_from,
                occurred_until=known_until - timedelta(microseconds=1) if known_until == brief.world_started_at else known_until,
                time_precision='interval', timezone_name='Asia/Shanghai', statement=statement,
                participant_refs=scene.participant_refs, privacy_class=block.privacy_class,
                story=PrehistoryStoryStructure(actor_ref=source.actor_ref, source_artifact_ref=source_ref,
                    known_from=known_from, known_until=known_until,
                    blocks=(StoryMaterialBlock(block_id=block.block_id, event_ref=scene.event_ref,
                        kind=block.kind, subject_actor_ref=block.subject_actor_ref, text=block.text,
                        source_quote=block.text, source_locator=f'{source.source_id}/{scene.event_ref}/{block.block_id}',
                        occurred_from=scene.occurred_from, occurred_until=scene.occurred_until),), relations=()))
            visible.append(block.block_id)
        scene_blocks.append(visible)
    if not records:
        raise ValueError('native source has no owner-readable history')
    edges = list(source.links)
    declared_pairs = {frozenset((e.left_block_id, e.right_block_id)) for e in edges}
    for visible in scene_blocks:
        for left, right in pairwise(visible):
            if frozenset((left, right)) not in declared_pairs:
                edges.append(NativeStoryLink(left_block_id=left, right_block_id=right,
                    relation_kind='shared_scene', bridge_source_quote=by_block[left].text))
    seen_pairs = set()
    for edge in edges:
        pair = frozenset((edge.left_block_id, edge.right_block_id))
        if pair in seen_pairs:
            raise ValueError('duplicate native relationship pair')
        seen_pairs.add(pair)
        if not pair <= records.keys():
            continue  # World graph retains it; an unreadable endpoint grants no memory edge.
        left, right = records[edge.left_block_id], records[edge.right_block_id]
        relation = PrehistoryStoryRelation(left_record_id=left.record_id, right_record_id=right.record_id,
            relation_kind=edge.relation_kind, bridge_source_quote=edge.bridge_source_quote or by_block[edge.left_block_id].text,
            evidence=tuple(StoryLinkEvidence(block_id=ref, source_quote=by_block[ref].text) for ref in (edge.left_block_id, edge.right_block_id)),
            rationale='Explicit native source relation or shared source scene; compiler makes no semantic judgment.')
        records[edge.left_block_id] = left.model_copy(update={'related_record_refs': tuple(sorted({*left.related_record_refs, right.record_id})),
            'story': left.story.model_copy(update={'relations': (*left.story.relations, relation)})})
        records[edge.right_block_id] = right.model_copy(update={'related_record_refs': tuple(sorted({*right.related_record_refs, left.record_id}))})
    prior = {r.record_id: r for a in brief.accepted_archives for r in a.records}
    cross = []
    for edge in source.legacy_links:
        old = prior.get(edge.prior_record_id)
        if old is None:
            raise ValueError('native legacy relation has no accepted record identity')
        prior_quote = edge.prior_source_quote or (old.story.blocks[0].source_quote[:1200] if old.story else old.statement[:1200])
        if prior_quote not in old.statement and old.story is None:
            raise ValueError('native legacy relation lacks exact accepted source')
        if edge.new_block_id not in records:
            continue
        if old.story is not None:
            old_block = next((b.block_id for b in old.story.blocks if prior_quote in b.source_quote), None)
            if old_block is None:
                raise ValueError('structured legacy quote must bind its exact source block')
        else:
            old_block = old.record_id
        new = records[edge.new_block_id]
        if edge.old_is_left and new.story.known_until < old.occurred_from:
            raise ValueError(f'{edge.new_block_id}: old_is_left=true would place {edge.prior_record_id} '
                f'({old.occurred_from.isoformat()}) before new knowledge ending {new.story.known_until.isoformat()}; '
                'remove the unsupported relation or have the source author explicitly correct its direction')
        endpoints = [(old.record_id, old_block, prior_quote),
                     (new.record_id, edge.new_block_id, by_block[edge.new_block_id].text)]
        if not edge.old_is_left:
            endpoints.reverse()
        cross.append(CrossStoryRelation(left_record_id=endpoints[0][0], left_block_id=endpoints[0][1], left_source_quote=endpoints[0][2],
            right_record_id=endpoints[1][0], right_block_id=endpoints[1][1], right_source_quote=endpoints[1][2],
            relation_kind=edge.relation_kind, bridge_source_quote=edge.bridge_source_quote or by_block[edge.new_block_id].text,
            rationale='Relation declared in the native source graph; exact endpoint excerpts identify sources, not an independent entailment verdict.'))
    validate_episode_basis(source.episodes, (e for a in brief.accepted_archives for e in a.episodes))
    document = PrehistoryArchiveDocument(contract='character-prehistory-archive.2', archive_id=archive_id,
        world_id=brief.world_id, actor_ref=source.actor_ref, source_artifact_ref=source_ref,
        entities=tuple(sorted(entities.values(), key=lambda e: e.entity_ref)), episodes=source.episodes,
        records=tuple(sorted(records.values(), key=lambda r: r.record_id)))
    return bind_context_from_brief(document, brief, cross_story_links=tuple(cross))


def compile_native_story(source, *, brief, archive_id):
    document = _compile_document(source, brief, archive_id)
    receipt = PrehistoryCompilationReceipt(compiler_version=('native-story-compiler.2' if source.contract == 'native-story-source.2' else 'native-story-compiler.1'), manifest_hash=digest(document.manifest()),
        source_hash=digest(source), context_hash=digest(brief), source_authored_at=source.authored_at,
        source_json=source.model_dump_json(), context_json=brief.model_dump_json())
    return CompiledPrehistoryArchive(document=document, compilation=receipt)


@lru_cache(maxsize=32)
def validate_compilation(manifest, receipt):
    source = NativeStorySource.model_validate_json(receipt.source_json)
    brief = PrehistoryAuthoringBrief.model_validate_json(receipt.context_json)
    if (digest(source), digest(brief), source.authored_at) != (receipt.source_hash, receipt.context_hash, receipt.source_authored_at):
        raise ValueError('compilation source or context differs from its pinned content')
    expected_version = 'native-story-compiler.2' if source.contract == 'native-story-source.2' else 'native-story-compiler.1'
    if receipt.compiler_version != expected_version:
        raise ValueError('native compiler version differs from its source contract')
    document = _compile_document(source, brief, manifest.archive_id)
    if document.manifest() != manifest or digest(manifest) != receipt.manifest_hash:
        raise ValueError('compilation cannot reproduce its exact archive manifest')
    return document
