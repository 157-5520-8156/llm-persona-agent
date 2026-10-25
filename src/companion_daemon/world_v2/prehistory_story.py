"""Versioned accepted story structure and minimal actor reading coordinates.

Raw source quotations are review evidence, never model-visible recollections.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator

from .schema_core import FrozenModel

StoryRelationKind = Literal[
    "shared_scene",
    "episode_followup",
    "later_disclosure",
    "explicit_callback",
]


class StoryLinkEvidence(FrozenModel):
    block_id: str = Field(min_length=1, max_length=256)
    source_quote: str = Field(min_length=1, max_length=1_200)


class PrehistoryStoryRelation(FrozenModel):
    left_record_id: str = Field(min_length=1, max_length=256,
        description="First scene in the narrative when order is known; otherwise an endpoint of an undirected association.")
    right_record_id: str = Field(min_length=1, max_length=256,
        description="Later scene when order is known; otherwise the other endpoint of an undirected association.")
    relation_kind: StoryRelationKind
    evidence: tuple[StoryLinkEvidence, ...] = Field(min_length=2, max_length=6)
    bridge_source_quote: str = Field(min_length=1, max_length=1_200,
        description="Exact original-narrative text that explicitly connects the two scenes; chronology alone is insufficient.")
    rationale: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def identities_are_distinct(self):
        if self.left_record_id == self.right_record_id:
            raise ValueError("story-link record endpoints must be distinct")
        if len({item.block_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("story-link evidence block IDs must be unique")
        return self


class StoryMaterialBlock(FrozenModel):
    block_id: str = Field(min_length=1, max_length=256)
    event_ref: str = Field(min_length=1, max_length=256)
    kind: Literal["event", "historical_interpretation", "background", "intention"]
    subject_actor_ref: str | None = None
    text: str = Field(min_length=1, max_length=4000)
    source_quote: str = Field(min_length=1, max_length=4000)
    source_locator: str = Field(min_length=1, max_length=512)
    occurred_from: datetime | None = None
    occurred_until: datetime | None = None

    @model_validator(mode="after")
    def occurrence_is_bounded(self):
        if (self.occurred_from is None) != (self.occurred_until is None):
            raise ValueError("story occurrence requires both bounds or neither")
        if self.occurred_from is not None and self.occurred_until < self.occurred_from:
            raise ValueError("story occurrence interval is reversed")
        return self


class PrehistoryStoryStructure(FrozenModel):
    contract: Literal["prehistory-story-structure.1"] = "prehistory-story-structure.1"
    actor_ref: str = Field(min_length=1, max_length=256)
    source_artifact_ref: str = Field(min_length=1, max_length=512)
    known_from: datetime
    known_until: datetime
    blocks: tuple[StoryMaterialBlock, ...] = Field(min_length=1, max_length=64)
    # Stored at the left endpoint only. Direction and evidence remain intact.
    relations: tuple[PrehistoryStoryRelation, ...] = Field(default=(), max_length=24)

    @model_validator(mode="after")
    def knowledge_is_bounded(self):
        if self.known_until < self.known_from:
            raise ValueError("story knowledge interval is reversed")
        if len({b.block_id for b in self.blocks}) != len(self.blocks):
            raise ValueError("story block IDs must be unique")
        if any(b.kind == "event" and b.occurred_from is not None
               and b.occurred_from > self.known_until for b in self.blocks):
            raise ValueError("story knowledge predates its event")
        return self


class StoryBlockReading(FrozenModel):
    canonical_event_ref: str | None = Field(default=None, exclude_if=lambda v: v is None)
    block_id: str
    event_ref: str
    kind: Literal["event", "historical_interpretation", "background", "intention"]
    subject_actor_ref: str | None
    source_locator: str
    occurred_from: datetime | None
    occurred_until: datetime | None


class StoryRelationAuthority(FrozenModel):
    event_ref: str = Field(min_length=1)
    world_revision: int = Field(ge=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class StoryRelationReading(FrozenModel):
    left_record_id: str
    right_record_id: str
    relation_kind: StoryRelationKind
    authority: StoryRelationAuthority | None = Field(default=None, exclude_if=lambda v: v is None)


class StoryMemoryReading(FrozenModel):
    contract: Literal["story-memory-reading.1"] = "story-memory-reading.1"
    source_artifact_ref: str
    known_from: datetime
    known_until: datetime
    blocks: tuple[StoryBlockReading, ...]
    relations: tuple[StoryRelationReading, ...]
    outer_interval_semantics: Literal["knowledge_acquisition"] = "knowledge_acquisition"
    coverage: Literal["bounded_record_material"] = "bounded_record_material"
    record_characters: int = Field(ge=1)

    def restrict_relations(self, visible_record_ids: set[str]) -> StoryMemoryReading:
        return self.model_copy(update={"relations": tuple(
            edge for edge in self.relations
            if {edge.left_record_id, edge.right_record_id} <= visible_record_ids
        )})


def story_memory_reading(story: PrehistoryStoryStructure, *, statement: str) -> StoryMemoryReading:
    return StoryMemoryReading(
        source_artifact_ref=story.source_artifact_ref,
        known_from=story.known_from, known_until=story.known_until,
        blocks=tuple(StoryBlockReading(**block.model_dump(exclude={"text", "source_quote"}))
                     for block in story.blocks),
        relations=tuple(StoryRelationReading(**edge.model_dump(include={
            "left_record_id", "right_record_id", "relation_kind",
        })) for edge in story.relations),
        record_characters=len(statement),
    )


class StoryWindowRequest(FrozenModel):
    """Coordinates into an already retained, source-verified record."""
    record_ref: str = Field(pattern=r"^prehistory-record:[a-z0-9][a-z0-9._-]*$", max_length=256)
    start: int = Field(ge=0, le=1600)


class StoryEventReading(FrozenModel):
    """Semantic coordinates; exact block/quote positions remain in the archive."""
    canonical_event_ref: str | None = Field(default=None, exclude_if=lambda v: v is None)
    event_ref: str
    kind: Literal["event", "historical_interpretation", "background", "intention"]
    subject_actor_ref: str | None = Field(default=None, exclude_if=lambda v: v is None)
    occurred_from: datetime | None = Field(default=None, exclude_if=lambda v: v is None)
    occurred_until: datetime | None = Field(default=None, exclude_if=lambda v: v is None)


class CompactStoryMemoryReading(StoryMemoryReading):
    contract: Literal["story-memory-reading.2"] = "story-memory-reading.2"
    blocks: tuple[StoryEventReading, ...]


@lru_cache(maxsize=4096)
def compact_story_reading(reading: StoryMemoryReading) -> CompactStoryMemoryReading:
    if isinstance(reading, CompactStoryMemoryReading):
        return reading
    return CompactStoryMemoryReading(
        **reading.model_dump(exclude={"contract", "blocks"}),
        blocks=tuple(StoryEventReading(**block.model_dump(exclude={"block_id", "source_locator"}))
                     for block in reading.blocks),
    )
