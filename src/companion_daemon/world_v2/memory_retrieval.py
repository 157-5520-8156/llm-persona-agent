"""Read-only, source-bound text retrieval for active MemoryCandidates.

``MemoryCandidate`` is intentionally only a retrieval-control authority.  This
module is the separate read seam that turns an eligible Fact-backed candidate
into a bounded excerpt of its exact persisted assertion message.  It never
changes candidate strength, writes events, or treats a model summary/ref as a
fact.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator
from pydantic import PrivateAttr

from .character_prehistory import PrehistoryRecord
from .ledger import LedgerPort, ObservationEventLocator
from .life_content import collect_user_channel_limited_content_refs
from .life_content_reading import (
    CharacterResponseReading, WorldConsequenceReading, read_character_life_experience_content,
)
from .life_content_store import ImmutableLifeContentStore
from .memory_reducers import evaluate_memory_retrieval
from .immutable_value_cache import immutable_value_cache
from .schema_core import FrozenModel, PrivacyClass
from .schemas import (
    ExperienceProjection,
    ExperienceTransitionProjection,
    LifeContentDescriptorProjection,
    FactTransitionProjection,
    MemoryCandidateProjection,
    MemoryCandidateValues,
    MemorySourceBinding,
    Observation,
    ProjectionCursor,
)


from .prehistory_memory_source import (
    PrehistoryMemoryReading, prehistory_memory_binding, prehistory_memory_reading, resolve_prehistory_memory_source,
)


class MemorySourceExcerpt(FrozenModel):
    """A bounded source-text view, pinned to a Memory source authority."""

    source_kind: Literal["fact", "experience", "prehistory"]
    source_id: str = Field(min_length=1)
    source_entity_revision: int = Field(ge=1)
    authority_event_ref: str = Field(min_length=1)
    authority_world_revision: int = Field(ge=1)
    authority_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_values_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    excerpt_ref: str = Field(min_length=1)
    excerpt_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str | None = Field(default=None, min_length=1, exclude_if=lambda value: value is None)
    truncated: bool
    # Preserve attribution when a quoted assertion says "I". New consumers
    # must not learn a counterpart's biography as the companion's own life.
    subject_ref: str | None = Field(default=None, exclude_if=lambda v: v is None)
    speaker_ref: str | None = Field(default=None, exclude_if=lambda v: v is None)
    prehistory: PrehistoryMemoryReading | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    world_consequence: WorldConsequenceReading | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    character_response: CharacterResponseReading | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    # Exact source bytes available only to the local index compiler. This is
    # deliberately absent from model-visible/durable readings; the ordinary
    # excerpt remains bounded by ``max_excerpt_characters``.
    _recall_source_record: PrehistoryRecord | None = PrivateAttr(default=None)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MemorySourceExcerpt):
            return NotImplemented
        # The local indexing cache is deliberately not serialized. Equality of
        # durable readings depends on their typed fields and source hashes,
        # otherwise a cold round trip differs merely because the cache is empty.
        return (
            type(self) is type(other)
            and self.__dict__ == other.__dict__
            and self.__pydantic_extra__ == other.__pydantic_extra__
        )

    @model_validator(mode="after")
    def one_source_carrier(self):
        if (self.source_kind == "prehistory") != (self.prehistory is not None):
            raise ValueError("prehistory memory must retain its historical scope")
        if self.prehistory is not None and (
            self.text is None
            or hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.excerpt_payload_hash
        ):
            raise ValueError("prehistory memory excerpt hash does not match its text")
        if (self.text is None) == (self.world_consequence is None):
            raise ValueError("memory source requires one exact content carrier")
        if self.world_consequence is not None and (
            self.source_kind != "experience" or self.character_response is None
        ):
            raise ValueError("structured memory requires the paired Experience reading")
        if self.character_response is not None and self.world_consequence is None:
            raise ValueError("private reading requires its separate World source")
        return self


class MemoryRetrievalItem(FrozenModel):
    """Model-visible retrieval material without any new claim authority."""

    candidate_id: str = Field(min_length=1)
    cue_kind: str = Field(min_length=1)
    retention_rationales: tuple[str, ...] = Field(min_length=1)
    privacy_ceiling: PrivacyClass
    retrieval_strength_bp: int = Field(ge=1, le=10_000)
    source_excerpts: tuple[MemorySourceExcerpt, ...] = Field(min_length=1)
    truncated: bool
    # Optional keeps old serialized views byte-compatible. New reads carry
    # the actual candidate times; otherwise capsule recency silently scores 0.
    updated_at: datetime | None = Field(default=None, exclude_if=lambda v: v is None)
    reviewed_at: datetime | None = Field(default=None, exclude_if=lambda v: v is None)
    assessment_scope: Literal["memory_salience_not_current_goal_or_obligation", "initialization_defaults_not_character_judgment"] | None = Field(
        default=None, exclude_if=lambda v: v is None,
    )

    def committed_source_claims(self) -> tuple[tuple[str, int, str], ...]:
        """Include the reviewed archive proof without exposing its other records."""
        claims = {
            (source.authority_event_ref, source.authority_world_revision,
             source.authority_payload_hash)
            for source in self.source_excerpts
        }
        claims.update(
            (source.prehistory.archive_event_ref, source.prehistory.archive_world_revision,
             source.prehistory.archive_payload_hash)
            for source in self.source_excerpts if source.prehistory is not None
        )
        claims.update((membership.authority.event_ref, membership.authority.world_revision, membership.authority.payload_hash)
            for source in self.source_excerpts if source.prehistory is not None
            for membership in source.prehistory.episodes)
        claims.update((edge.authority.event_ref, edge.authority.world_revision, edge.authority.payload_hash)
            for source in self.source_excerpts if source.prehistory is not None and source.prehistory.story is not None
            for edge in source.prehistory.story.relations if edge.authority is not None)
        return tuple(sorted(claims))


class MemoryRetrievalSuppression(FrozenModel):
    """Trace-only reason why an active-looking candidate supplied no text."""

    candidate_id: str = Field(min_length=1)
    reasons: tuple[
        Literal[
            "not_active",
            "stale_source",
            "privacy_ceiling",
            "content_unavailable",
            "source_proof_failed",
        ],
        ...,
    ] = Field(min_length=1)


class MemoryRetrievalResult(FrozenModel):
    items: tuple[MemoryRetrievalItem, ...]
    suppressions: tuple[MemoryRetrievalSuppression, ...]


def _material_updated_at(candidate, transitions):
    """A routine retention review must not make an old memory look newly lived."""
    history = [t for t in transitions if t.candidate_id == candidate.candidate_id]
    if not history or max(history, key=lambda t: t.entity_revision).operation != "review":
        return candidate.updated_at
    changed = [t for t in history if t.operation != "review"]
    return max(changed, key=lambda t: t.entity_revision).accepted_at if changed else candidate.opened_at


def _source_summary_is_bound(values: MemoryCandidateValues) -> bool:
    """The existing source-alias format used by correction/retirement writers.

    These aliases carry no authored summary bytes: the hash is the exact
    retained source event hash. An arbitrary replacement ref/hash cannot use
    this exception, and an earlier compression still requires its own reader.
    """
    return any(
        values.summary_ref == f"summary:source:{binding.authority_event_ref}"
        and values.summary_payload_hash == binding.authority_payload_hash
        for binding in values.source_bindings
    )


@immutable_value_cache(maxsize=2048)
def _prehistory_excerpt_value(row, archive, max_characters):
    """Pure bounded reading; eligibility and visibility are checked by callers."""
    text = row.record.statement[:max_characters]
    excerpt = MemorySourceExcerpt(
        **prehistory_memory_binding(row).model_dump(), excerpt_ref=row.record.record_id,
        excerpt_payload_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text, truncated=len(text) < len(row.record.statement),
        prehistory=prehistory_memory_reading(row, archive),
    )
    excerpt._recall_source_record = row.record
    return excerpt


@immutable_value_cache(maxsize=2048)
def _single_history_item_material(candidate, excerpt, assessment_scope):
    """Pure unscoped construction; current eligibility is checked before use.

    Scope-dependent story/episode relations and historical update time are
    applied later. The compiler copies every prehistory excerpt before this
    material can leave its Interface, keeping private pointers isolated.
    """
    return MemoryRetrievalItem(
        candidate_id=candidate.candidate_id, cue_kind=candidate.values.cue_kind,
        retention_rationales=candidate.values.retention_rationales,
        privacy_ceiling=candidate.values.privacy_ceiling,
        retrieval_strength_bp=candidate.values.retrieval_strength_bp,
        source_excerpts=(excerpt,), truncated=excerpt.truncated,
        updated_at=candidate.updated_at, reviewed_at=candidate.values.reviewed_at,
        assessment_scope=assessment_scope,
    )


class MemoryRetrievalCompiler:
    """Deep read module for source-bound memory excerpts.

    The caller needs only a complete cursor, a candidate set already scoped by
    Context, a viewer privacy ceiling, and a fixed per-source text budget.
    All source validation and observation reads remain private to this module.
    """

    def __init__(
        self,
        *,
        ledger: LedgerPort,
        life_content_store: ImmutableLifeContentStore | None = None,
        max_excerpt_characters: int = 480,
    ) -> None:
        if max_excerpt_characters <= 0:
            raise ValueError("memory excerpt budget must be positive")
        self._ledger = ledger
        self._life_content_store = life_content_store
        self._max_excerpt_characters = max_excerpt_characters

    def compile(
        self,
        *,
        cursor: ProjectionCursor,
        candidates: tuple[MemoryCandidateProjection, ...],
        viewer_privacy_ceiling: PrivacyClass,
        projection=None,
        actor_ref: str | None = None,
    ) -> MemoryRetrievalResult:
        projection = projection if projection is not None else self._ledger.project_at(cursor)
        if (
            projection.world_revision != cursor.world_revision
            or projection.deliberation_revision != cursor.deliberation_revision
            or projection.ledger_sequence != cursor.ledger_sequence
        ):
            raise ValueError("memory retrieval projection does not match its pinned cursor")
        # Reuse raw source-qualified members only inside this exact content
        # read scope. The visible story/episode closure is recomputed below for
        # EACH requested set, including broader Recall and narrower Context.
        from .context_read_scope import memoized_context_read_batch

        def collect(missing):
            result = self._collect_raw(
                cursor=cursor, candidates=missing, viewer_privacy_ceiling=viewer_privacy_ceiling,
                projection=projection, actor_ref=actor_ref,
            )
            items_by_id = {item.candidate_id: item for item in result.items}
            suppressed_by_id = {item.candidate_id: item for item in result.suppressions}
            return tuple((items_by_id.get(item.candidate_id), suppressed_by_id.get(item.candidate_id))
                         for item in missing)

        if len({item.candidate_id for item in candidates}) != len(candidates):
            # Preserve the historical handling of duplicate/unusual caller input.
            result = self._collect_raw(
                cursor=cursor, candidates=candidates, viewer_privacy_ceiling=viewer_privacy_ceiling,
                projection=projection, actor_ref=actor_ref,
            )
            items, suppressions = list(result.items), list(result.suppressions)
        else:
            members = memoized_context_read_batch(
                store=self._life_content_store, projection=projection, kind="memory_raw_members",
                owner=self, inputs=candidates,
                parameters=(actor_ref, viewer_privacy_ceiling, self._max_excerpt_characters),
                compute=collect,
            )
            items = [item for item, _ in members if item is not None]
            suppressions = [item for _, item in members if item is not None]
        from .prehistory_memory_source import restrict_story_reading
        visible_records = {excerpt.source_id for item in items for excerpt in item.source_excerpts
                           if excerpt.prehistory is not None}
        from .story_episode import readable_episode_memberships
        memberships = readable_episode_memberships(archives=projection.prehistory_archives,
            actor_ref=actor_ref, world_id=self._ledger.world_id, visible_record_ids=visible_records)
        # Only already source-checked, currently readable endpoints contribute
        # cross-story edges. Their new archive remains the edge's authority.
        cross_story_relations = tuple(dict.fromkeys(edge
            for item in items for excerpt in item.source_excerpts
            if excerpt.prehistory is not None and excerpt.prehistory.story is not None
            for edge in excerpt.prehistory.story.relations if edge.authority is not None
            and {edge.left_record_id, edge.right_record_id} <= visible_records))
        items = [item.model_copy(update={"source_excerpts": tuple(
            excerpt.model_copy(update={"prehistory": restrict_story_reading(
                excerpt.prehistory.model_copy(update={"episodes": memberships.get(excerpt.source_id, ())}),
                visible_records, record_id=excerpt.source_id,
                cross_story_relations=cross_story_relations,
            )}) if excerpt.prehistory is not None else excerpt.model_copy()
            for excerpt in item.source_excerpts
        )}) for item in items]
        return MemoryRetrievalResult(items=tuple(items), suppressions=tuple(suppressions))

    def _collect_raw(
        self, *, cursor, candidates, viewer_privacy_ceiling, projection, actor_ref,
    ):
        limited_content_refs = collect_user_channel_limited_content_refs(
            ledger=self._ledger,
            projection=projection,
        )
        material_histories = {}
        for transition in projection.memory_candidate_transitions:
            material_histories.setdefault(transition.candidate_id, []).append(transition)
        # A per-invocation handoff, never an authority cache across reads.
        resolved_prehistory = {}
        decisions = {
            item.candidate_id: item
            for item in evaluate_memory_retrieval(
                candidates,
                facts=projection.facts,
                fact_history=projection.fact_transitions,
                experiences=projection.experiences,
                experience_history=projection.experience_transitions,
                threads=projection.threads,
                thread_history=projection.thread_transitions,
                committed_events=projection.committed_world_event_refs,
                prehistory_records=projection.prehistory_records,
                prehistory_archives=projection.prehistory_archives,
                viewer_privacy_ceiling=viewer_privacy_ceiling,
                _resolved_prehistory=resolved_prehistory,
            )
        }
        items: list[MemoryRetrievalItem] = []
        suppressions: list[MemoryRetrievalSuppression] = []
        current_candidates = {item.candidate_id: item for item in projection.memory_candidates}
        unavailable_representations = {
            transition.candidate_id
            for transition in projection.memory_candidate_transitions
            if transition.values_before is not None and (
                transition.revise_kind == "compress"
                or (
                    (transition.values_before.summary_ref, transition.values_before.summary_payload_hash)
                    != (transition.values_after.summary_ref, transition.values_after.summary_payload_hash)
                    and not _source_summary_is_bound(transition.values_after)
                )
            )
        }

        def read_boundary(candidate: MemoryCandidateProjection) -> Literal["source_proof_failed", "content_unavailable"] | None:
            # Every memory lane must use its exact current projection image.
            # An old active/public image is not authority after forgetting,
            # privacy tightening, source correction or removal.
            if current_candidates.get(candidate.candidate_id) != candidate:
                return "source_proof_failed"
            if candidate.candidate_id in unavailable_representations:
                # Until an authorized representation reader is installed,
                # never recover compressed detail from an original source.
                return "content_unavailable"
            return None

        # A context compile can expose several retained facts.  Reading each
        # source through ``observation_events_at`` separately repeats the
        # pinned-boundary proof and SQLite transaction for every candidate.
        # Collect the exact locators first and open the immutable history in
        # one batch.  This is only a read coalescing optimization: every
        # locator remains source-bound and the ledger still performs all proof
        # checks.
        source_locators: dict[str, ObservationEventLocator] = {}
        for candidate in candidates:
            decision = decisions[candidate.candidate_id]
            if not decision.eligible or read_boundary(candidate) is not None:
                continue
            for binding in candidate.values.source_bindings:
                if binding.source_kind != "fact":
                    continue
                locator = self._fact_locator(
                    binding=binding,
                    cursor=cursor,
                    projection=projection,
                )
                if locator is not None:
                    source_locators[locator.idempotency_key] = locator
        historical_by_identity = {}
        if source_locators:
            locators = tuple(
                sorted(
                    source_locators.values(),
                    key=lambda item: (
                        item.observation_id,
                        item.event_type,
                        item.idempotency_key,
                    ),
                )
            )
            for item in self._ledger.observation_events_at(locators, cursor=cursor):
                historical_by_identity[item.event.idempotency_key] = item
                # Test/durable LedgerPort implementations may expose the
                # same exact observation under a different event idempotency
                # key; the payload's observation id is still the projection's
                # source identity and is safe to use as a local join key.
                try:
                    observation = Observation.model_validate_json(item.event.payload_json)
                except ValueError:
                    continue
                historical_by_identity[observation.observation_id] = item
        from .prehistory_memory_initialization import is_unassessed_initial_memory
        for candidate in candidates:
            decision = decisions[candidate.candidate_id]
            if not decision.eligible:
                suppressions.append(
                    MemoryRetrievalSuppression(
                        candidate_id=candidate.candidate_id,
                        reasons=decision.suppression_reasons,
                    )
                )
                continue
            boundary = read_boundary(candidate)
            if boundary is not None:
                suppressions.append(MemoryRetrievalSuppression(
                    candidate_id=candidate.candidate_id, reasons=(boundary,),
                ))
                continue
            excerpts: list[MemorySourceExcerpt] = []
            unavailable = False
            for binding in candidate.values.source_bindings:
                if binding.source_kind == "fact":
                    excerpt = self._fact_excerpt(
                        binding=binding,
                        cursor=cursor,
                        projection=projection,
                        historical_by_identity=historical_by_identity,
                    )
                elif binding.source_kind == "experience":
                    excerpt = self._experience_excerpt(
                        binding=binding,
                        projection=projection,
                        viewer_privacy_ceiling=viewer_privacy_ceiling,
                        user_channel_limited_content_refs=limited_content_refs,
                    )
                elif binding.source_kind == "prehistory":
                    resolved_source = resolved_prehistory.get(binding)
                    if resolved_source is None:
                        excerpt = self._prehistory_excerpt(
                            binding=binding, projection=projection, actor_ref=actor_ref,
                        )
                    else:
                        # This local pair was just proved against the same
                        # immutable projection by evaluate_memory_retrieval.
                        row, archive = resolved_source
                        excerpt = (
                            _prehistory_excerpt_value(row, archive, self._max_excerpt_characters)
                            if actor_ref is not None and row.actor_ref == actor_ref else None
                        )
                else:
                    excerpt = None
                if excerpt is None:
                    unavailable = True
                else:
                    excerpts.append(excerpt)
            if not excerpts:
                suppressions.append(
                    MemoryRetrievalSuppression(
                        candidate_id=candidate.candidate_id,
                        reasons=("content_unavailable",),
                    )
                )
                continue
            if unavailable:
                suppressions.append(
                    MemoryRetrievalSuppression(
                        candidate_id=candidate.candidate_id,
                        reasons=("content_unavailable",),
                    )
                )
            updated_at = _material_updated_at(candidate, material_histories.get(candidate.candidate_id, ()))
            assessment_scope = (
                "initialization_defaults_not_character_judgment"
                if is_unassessed_initial_memory(candidate, ledger=self._ledger)
                else "memory_salience_not_current_goal_or_obligation"
            )
            if len(excerpts) == 1 and excerpts[0].prehistory is not None:
                material = _single_history_item_material(candidate, excerpts[0], assessment_scope)
                items.append(material.model_copy(update={"updated_at": updated_at}))
            else:
                items.append(MemoryRetrievalItem(
                    candidate_id=candidate.candidate_id,
                    cue_kind=candidate.values.cue_kind,
                    retention_rationales=candidate.values.retention_rationales,
                    privacy_ceiling=candidate.values.privacy_ceiling,
                    retrieval_strength_bp=candidate.values.retrieval_strength_bp,
                    source_excerpts=tuple(excerpts),
                    truncated=any(item.truncated for item in excerpts),
                    updated_at=updated_at, reviewed_at=candidate.values.reviewed_at,
                    assessment_scope=assessment_scope,
                ))
        return MemoryRetrievalResult(items=tuple(items), suppressions=tuple(suppressions))

    def _prehistory_excerpt(self, *, binding, projection, actor_ref):
        row, archive = resolve_prehistory_memory_source(
            binding, records=projection.prehistory_records,
            archives=projection.prehistory_archives,
            committed_events=projection.committed_world_event_refs,
        )
        if actor_ref is None or row.actor_ref != actor_ref:
            return None
        # Only internal material is shared. compile() always copies these
        # excerpts while applying this invocation's visible story/episode set.
        return _prehistory_excerpt_value(row, archive, self._max_excerpt_characters)

    def _experience_excerpt(
        self,
        *,
        binding: MemorySourceBinding,
        projection,
        viewer_privacy_ceiling: PrivacyClass = "private",
        user_channel_limited_content_refs: frozenset[str] = frozenset(),
    ) -> MemorySourceExcerpt | None:
        """Read one exact Experience summary through its descriptor and sidecar.

        A memory candidate may retain an Experience after it leaves the small
        ``recent_experiences`` context window.  The candidate does not grant
        access to prose by itself: the Experience transition, committed
        source event, LifeContent descriptor, and immutable sidecar bytes must
        all still agree at this projection cursor.
        """

        if self._life_content_store is None:
            return None
        experience = next(
            (
                item
                for item in projection.experiences
                if isinstance(item, ExperienceProjection)
                and item.experience_id == binding.source_id
            ),
            None,
        )
        transition = next(
            (
                item
                for item in projection.experience_transitions
                if isinstance(item, ExperienceTransitionProjection)
                and item.experience_id == binding.source_id
                and item.entity_revision == binding.source_entity_revision
                and item.accepted_event_ref == binding.authority_event_ref
            ),
            None,
        )
        committed = next(
            (
                item
                for item in projection.committed_world_event_refs
                if item.event_id == binding.authority_event_ref
                and item.event_type == "ExperienceCommitted"
            ),
            None,
        )
        if experience is None or transition is None or committed is None:
            return None
        if (
            experience.entity_revision != binding.source_entity_revision
            or experience.origin.accepted_event_ref != binding.authority_event_ref
            or committed.world_revision != binding.authority_world_revision
            or committed.payload_hash != binding.authority_payload_hash
            or transition.values_after != experience.values
            or transition.semantic_fingerprint_after != experience.semantic_fingerprint
            or self._canonical_hash(transition.values_after) != binding.source_values_hash
        ):
            return None
        descriptor = next(
            (
                item
                for item in projection.life_content_descriptors
                if isinstance(item, LifeContentDescriptorProjection)
                and item.source_kind == "experience"
                and item.source_entity_id == experience.experience_id
                and item.source_entity_revision == experience.entity_revision
                and item.source_event_ref == binding.authority_event_ref
                and item.source_world_revision == binding.authority_world_revision
                and item.source_payload_hash == binding.authority_payload_hash
                and item.content_ref == experience.values.summary_ref
                and item.content_payload_hash == experience.values.summary_payload_hash
            ),
            None,
        )
        if descriptor is None or descriptor.privacy_class != experience.values.privacy_class:
            return None
        if descriptor.content_ref in user_channel_limited_content_refs:
            return None
        descriptor_event = next(
            (
                item
                for item in projection.committed_world_event_refs
                if item.event_id == descriptor.descriptor_event_ref
                and item.event_type == "LifeContentRecorded"
                and item.world_revision == descriptor.descriptor_world_revision
                and item.payload_hash == descriptor.descriptor_payload_hash
            ),
            None,
        )
        if descriptor_event is None:
            return None
        stored = self._life_content_store.read_exact(content_ref=descriptor.content_ref)
        if stored is None or (
            stored.content_kind != "experience_summary"
            or stored.content_payload_hash != descriptor.content_payload_hash
        ):
            return None
        reading = None
        if experience.authority_contract_version == "experience.2":
            try:
                reading = read_character_life_experience_content(
                    store=self._life_content_store, projection=projection, experience=experience,
                    actor_ref=experience.values.source_bindings[0].response.actor_ref,
                    viewer_privacy_ceiling=viewer_privacy_ceiling,
                    max_characters=self._max_excerpt_characters,
                    user_channel_limited_content_refs=user_channel_limited_content_refs,
                )
            except ValueError:
                return None
        text = stored.text[: self._max_excerpt_characters] if reading is None else None
        return MemorySourceExcerpt(
            source_kind="experience",
            source_id=binding.source_id,
            source_entity_revision=binding.source_entity_revision,
            authority_event_ref=binding.authority_event_ref,
            authority_world_revision=binding.authority_world_revision,
            authority_payload_hash=binding.authority_payload_hash,
            source_values_hash=binding.source_values_hash,
            excerpt_ref=descriptor.content_ref,
            excerpt_payload_hash=descriptor.content_payload_hash,
            text=text,
            truncated=reading.truncated if reading is not None else text != stored.text,
            world_consequence=reading.world_consequence if reading is not None else None,
            character_response=reading.character_response if reading is not None else None,
        )

    @staticmethod
    def _canonical_hash(value) -> str:
        return hashlib.sha256(
            json.dumps(
                value.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def _fact_excerpt(
        self,
        *,
        binding: MemorySourceBinding,
        cursor: ProjectionCursor,
        projection,
        historical_by_identity=None,
    ) -> MemorySourceExcerpt | None:
        locator = self._fact_locator(binding=binding, cursor=cursor, projection=projection)
        if locator is None:
            return None
        transition = next(
            (
                item
                for item in projection.fact_transitions
                if item.fact_id == binding.source_id
                and item.entity_revision == binding.source_entity_revision
                and item.accepted_event_ref == binding.authority_event_ref
            ),
            None,
        )
        if transition is None:
            return None
        assertion = transition.values_after.assertion_binding
        historical = (
            historical_by_identity.get(locator.idempotency_key)
            if historical_by_identity is not None
            else None
        )
        if historical is None and historical_by_identity is not None:
            historical = historical_by_identity.get(assertion.source_ref)
        if historical_by_identity is None:
            events = self._ledger.observation_events_at((locator,), cursor=cursor)
            historical = events[0] if len(events) == 1 else None
        if historical is None:
            return None
        event = historical.event
        reference = self._message_reference_for_binding(binding=binding, projection=projection)
        if reference is None:
            return None
        try:
            observation = Observation.model_validate_json(event.payload_json)
        except ValueError:
            return None
        if (
            event.event_type != "ObservationRecorded"
            or event.payload_hash != reference.event_payload_hash
            or observation.observation_id != reference.observation_id
            or observation.actor != assertion.actor_ref
            or observation.channel != assertion.channel
            or observation.payload_ref != assertion.payload_ref
            or observation.payload_hash != assertion.content_payload_hash
            or observation.text is None
        ):
            return None
        source_text = observation.text
        text = source_text[: self._max_excerpt_characters]
        return MemorySourceExcerpt(
            source_kind="fact",
            source_id=binding.source_id,
            source_entity_revision=binding.source_entity_revision,
            authority_event_ref=binding.authority_event_ref,
            authority_world_revision=binding.authority_world_revision,
            authority_payload_hash=binding.authority_payload_hash,
            source_values_hash=binding.source_values_hash,
            excerpt_ref=observation.observation_id,
            excerpt_payload_hash=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            text=text,
            truncated=text != source_text,
            subject_ref=transition.values_after.subject_ref,
            speaker_ref=observation.actor,
        )

    def _fact_locator(
        self,
        *,
        binding: MemorySourceBinding,
        cursor: ProjectionCursor,
        projection,
    ) -> ObservationEventLocator | None:
        del cursor  # Kept in the seam for callers that pin all source reads.
        transition = next(
            (
                item
                for item in projection.fact_transitions
                if item.fact_id == binding.source_id
                and item.entity_revision == binding.source_entity_revision
                and item.accepted_event_ref == binding.authority_event_ref
            ),
            None,
        )
        if transition is None or not self._fact_binding_matches(transition, binding):
            return None
        assertion = transition.values_after.assertion_binding
        if assertion.source_kind != "observed_message":
            return None
        reference = self._message_reference_for_binding(binding=binding, projection=projection)
        if reference is None:
            return None
        return ObservationEventLocator.for_message(
            world_id=self._ledger.world_id,
            observation_id=reference.observation_id,
            source=reference.source,
            source_event_id=reference.source_event_id,
        )

    @staticmethod
    def _message_reference_for_binding(*, binding: MemorySourceBinding, projection):
        transition = next(
            (
                item
                for item in projection.fact_transitions
                if item.fact_id == binding.source_id
                and item.entity_revision == binding.source_entity_revision
                and item.accepted_event_ref == binding.authority_event_ref
            ),
            None,
        )
        if transition is None:
            return None
        assertion = transition.values_after.assertion_binding
        if assertion.source_kind != "observed_message":
            return None
        reference = next(
            (
                item
                for item in projection.message_observations
                if item.observation_id == assertion.source_ref
            ),
            None,
        )
        if reference is None or (
            reference.actor != assertion.actor_ref
            or reference.channel != assertion.channel
            or reference.payload_ref != assertion.payload_ref
            or reference.content_payload_hash != assertion.content_payload_hash
        ):
            return None
        return reference

    @staticmethod
    def _fact_binding_matches(
        transition: FactTransitionProjection, binding: MemorySourceBinding
    ) -> bool:
        # ``evaluate_memory_retrieval`` already proves full lifecycle/current
        # eligibility. This repeats only the content-path identity check before
        # a raw observation is opened.
        material = transition.values_after.model_dump(mode="json")
        values_hash = hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return values_hash == binding.source_values_hash


__all__ = [
    "MemoryRetrievalCompiler",
    "MemoryRetrievalItem",
    "MemoryRetrievalResult",
    "MemoryRetrievalSuppression",
    "MemorySourceExcerpt",
]
