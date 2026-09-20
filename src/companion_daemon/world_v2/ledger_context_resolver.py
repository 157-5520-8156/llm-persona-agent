"""Ledger-backed composition seam for revision-pinned Context Capsules.

This module is intentionally conservative: a projection value is exposed only
when every typed authority reference can be resolved at the requested cursor.
Missing authority makes that whole domain unavailable; it is never replaced by
an inferred reference or hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import logging
import time
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .affect_events import AffectEpisodeOpenedPayload
from .appraisal_events import AppraisalAcceptedPayload
from .appraisal_source_identity import conversation_source_cluster_ref
from .fact_observation_value import FactObservationValueBinding
from .biographical_lifecycle import BiographicalLifecycleCatalog
from .biographical_timeline_authority import (
    BiographicalTimelineConfiguredPayload,
)
from .context_capsule import (
    ContextCapsuleBudgetPolicy,
    ContextCapsuleCompiler,
    ContextCapsuleRequest,
    FactRecallItem,
    HistoricalFactRecallItem,
    InnerAdvisoryProjection,
    MAX_INPUT_ITEMS_PER_SLICE,
    MAX_RESOLVER_DOMAIN_SCAN_ITEMS,
    MAX_SOURCE_REFS_PER_ITEM,
    RANK_DOMAIN_IMPORTANCE_BP,
    RANK_RECENCY_WINDOW_SECONDS,
    RANK_WEIGHT_BP,
    RESOLUTION_POLICY_DIGEST,
    RESOLUTION_POLICY_VERSION,
    RESOLVER_ID,
    RESOLVER_VERSION,
    PendingOutboundExpressionItem,
    ResolvedItemMetadata,
    ResolvedSlice,
    ResolvedSourceBinding,
    ResolverProof,
    SharedMediaDeliveryContextItem,
    SliceName,
    authority_refs_digest,
    canonical_value_hash,
    compile_pending_outbound_expression_item,
    compile_shared_media_delivery_item,
    derived_privacy_floor,
    _shared_media_about,
    resolved_result_set_hash,
    source_bindings_hash,
)
from .conversation_continuity import (
    ContinuityRetrievalCandidate,
    ConversationContinuityCompiler,
    pack_recent_dialogue_under_source_budget,
)
from .associative_recall import lexical_relevance_bp
from .epoch_migration_source import (
    archive_observation_ids,
    epoch_migration_source_excerpt,
    is_epoch_genesis_fact,
    lookup_archive_observations,
)
from .fact_predicate_stability import STABLE_FACT_RECENCY_BP, fact_predicate_is_stable
from .fact_accepted_contracts import rehydrate_fact_commit_materialized_v2_json
from .fact_events import FactChangedPayload
from .context_resolver import (
    ContextCompileQuery,
    ResolvedContextResult,
    TrustedInternalContextResolver,
    context_query_hash,
    projection_snapshot_id,
)
from .ledger import LedgerPort
from .memory_retrieval import MemoryRetrievalCompiler, MemoryRetrievalItem
from .later_expression_freshness import queued_later_facts
from .life_content import LifeContentCompiler, collect_user_channel_limited_content_refs, RecentExperienceContextItem
from .private_impression_events import collect_user_channel_limited_impression_ids
from .life_content_store import ImmutableLifeContentStore
from .life_development_runtime import LifeDevelopmentProposalReader
from .chat_life_intent_runtime import ChatLifeIntentActiveReader, ChatLifeIntentCompletedReader, ChatLifeIntentPlannedReader, CompositeActiveActivityReader, CompositeCompletedActivityReader, CompositePlannedActivityReader
from .world_life_intent_runtime import WorldLifeIntentActiveReader, WorldLifeIntentCompletedReader, WorldLifeIntentPlannedReader
from .day_open_life_intent_runtime import DayOpenLifeIntentActiveReader, DayOpenLifeIntentCompletedReader, DayOpenLifeIntentPlannedReader
from .life_events import NpcRegisteredPayload
from .npc_identity_view import npc_identity_views
from .perception_result_context import (
    PerceptionResultContextCompiler,
    PerceptionResultContextItem,
    PerceptionResultReader,
)
from .external_perception_events import (
    ExternalPerceptionLifeInfluenceView,
    compile_external_perception_life_influences,
)
from .expression_payload_store import ImmutableExpressionPayloadStore
from .media_v2 import MediaDeliverySharedPayload
from .model_facing_context import CHAT_RECENT_DIALOGUE_ITEM_LIMIT
from .present_prompt import (
    PRESENT_PENDING_OUTBOUND_ITEM_LIMIT,
    PRESENT_SHARED_MEDIA_ITEM_LIMIT,
)
from .recent_dialogue import (
    RecentDialogueCompiler,
    RecentDialogueItem,
    delivered_photo_dialogue_item,
)
from .recall_corpus import (
    AffectOpeningRecallItem,
    MAX_RECALL_CORPUS_DOCUMENTS,
    NpcIdentityRecallItem,
    RecallCorpusSources,
    required_recall_authority_refs,
    select_recall_authority_bindings,
)
from .recall_attention import (
    build_automatic_recall_request,
    select_recent_dialogue_for_automatic_recall,
)
from .recall_index import RecallCursor, RecallSourceBinding
from .recall_runtime import RecallCoordinator
from .schema_core import PrivacyClass
from .schemas import (
    AffectEpisodeProjection,
    AppraisalProjection,
    BudgetAccount,
    CommittedWorldEventRef,
    FactProjection,
    LedgerProjection,
    Observation,
    PrivateImpressionProjection,
    ProjectionCursor,
    WorldEvent,
)
from .situation_compiler import SituationCompiler, request_from_ledger_projection
from .world_life_context import (
    ActiveActivityContextItem,
    ActivityLifecycleStateContextItem,
    CompletedActivityContextItem,
    PlannedActivityContextItem,
    ActiveWorldOccurrenceContextItem,
    BiographicalWorldContextItem,
    WorldLifeContextCompiler,
    WorldLifeContextItem,
    WorldLifeSourceBinding,
)


_PRIVACY_FLOOR: dict[SliceName, PrivacyClass] = {
    "character_core": "withhold",
    "current_situation": "private",
    "recent_dialogue": "private",
    "relationship_slice": "private",
    "appraisals": "private",
    "affect_episodes": "private",
    "open_threads": "private",
    "relevant_facts": "personal",
    "recent_experiences": "personal",
    "world_life": "personal",
    "perception_results": "private",
    "active_memory_candidates": "personal",
    "available_capabilities": "private",
    "action_budget": "withhold",
    "private_impressions": "withhold",
    "advisories": "private",
    "media_deliveries": "personal",
    "pending_outbound": "private",
}
_PRIVACY_RANK = {"public": 0, "shareable": 1, "personal": 2, "private": 3, "withhold": 4}
_EXPERIENCE_CONTENT_UNAVAILABLE_REASONS = frozenset(
    {
        "descriptor_missing",
        "source_proof_failed",
        "content_missing",
        "hash_mismatch",
        "structured_content_unavailable",
    }
)

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContextResolverPerformanceCounters:
    """Non-authoritative evidence for cursor-pinned resolver reuse."""

    resolve_calls: int
    cache_hits: int
    cache_misses: int


_ITEM_ID: dict[SliceName, str] = {
    "character_core": "core_id",
    "current_situation": "actor_ref",
    "recent_dialogue": "dialogue_id",
    "relationship_slice": "relationship_id",
    "appraisals": "appraisal_id",
    "affect_episodes": "episode_id",
    "open_threads": "thread_id",
    "relevant_facts": "fact_id",
    "recent_experiences": "experience_id",
    "world_life": "occurrence_id",
    "perception_results": "result_id",
    "active_memory_candidates": "candidate_id",
    "available_capabilities": "grant_id",
    "action_budget": "account_id",
    "private_impressions": "impression_id",
    "advisories": "advisory_id",
    "media_deliveries": "delivery_id",
    "pending_outbound": "action_id",
}


class ContextRelevanceScope(BaseModel):
    """Explicit actor/subject boundary for a ledger-backed Context resolver."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_ref: str = Field(min_length=1, max_length=256)
    related_subject_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def refs_are_canonical(self) -> ContextRelevanceScope:
        if self.related_subject_refs != tuple(sorted(set(self.related_subject_refs))):
            raise ValueError("Context relevance subject refs must be unique and sorted")
        if self.actor_ref in self.related_subject_refs:
            raise ValueError("Context actor must not be repeated as a related subject")
        return self

    @property
    def subject_refs(self) -> frozenset[str]:
        return frozenset((self.actor_ref, *self.related_subject_refs))

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


def _bounded_recall_texts(values: object) -> tuple[str, ...]:
    """Keep only usable lexical-recall source text from one domain item.

    A memory candidate or fact can exist with an empty projected excerpt.  It
    is still valid ledger state, but it carries no surface form for lexical
    prefetch; skipping its text must not fail the whole Context resolution.
    """

    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(
        value for value in values if isinstance(value, str) and value
    )


def _automatic_recall_link_refs(
    *,
    projection: LedgerProjection,
    subject_refs: frozenset[str],
    link_refs: tuple[str, ...],
) -> tuple[str, ...]:
    """Keep specific cues; known conversation membership is not relevance.

    Derive exact scope identities from pinned message coordinates, never from
    an opaque reference's spelling. Source documents, authority bindings and
    subject eligibility stay unchanged. This only builds new automatic queries;
    persisted queries and explicit character recall retain their original refs.
    """

    conversation_scopes = {
        conversation_source_cluster_ref(actor_ref=item.actor, channel=item.channel)
        for item in projection.message_observations
        if item.actor is not None and item.actor in subject_refs and item.channel is not None
    }
    return tuple(sorted(set(link_refs) - conversation_scopes))


def context_capsule_compiler_from_ledger(
    *,
    ledger: LedgerPort,
    situation_compiler: SituationCompiler | None = None,
    policy: ContextCapsuleBudgetPolicy | None = None,
    relevance_scope: ContextRelevanceScope | None = None,
    life_content_store: ImmutableLifeContentStore | None = None,
    perception_result_reader: PerceptionResultReader | None = None,
    expression_payload_store: ImmutableExpressionPayloadStore | None = None,
    recall_coordinator: RecallCoordinator | None = None,
    biographical_catalog: BiographicalLifecycleCatalog | None = None,
    biographical_timezone_name: str | None = None,
    biographical_timeline: BiographicalTimelineConfiguredPayload | None = None,
    reviewed_npc_identity_summaries: dict[str, str] | None = None,
    archive_ledger: LedgerPort | None = None,
    retain_pinned_appraisals: bool = False,
) -> ContextCapsuleCompiler:
    """Composition-root factory for the production ledger-backed seam."""

    return ContextCapsuleCompiler(
        resolver=LedgerProjectionContextResolver(
            ledger=ledger,
            situation_compiler=situation_compiler or SituationCompiler(),
            relevance_scope=relevance_scope,
            life_content_store=life_content_store,
            perception_result_reader=perception_result_reader,
            expression_payload_store=expression_payload_store,
            recall_coordinator=recall_coordinator,
            biographical_catalog=biographical_catalog,
            biographical_timezone_name=biographical_timezone_name,
            biographical_timeline=biographical_timeline,
            reviewed_npc_identity_summaries=reviewed_npc_identity_summaries,
            archive_ledger=archive_ledger,
            retain_pinned_appraisals=retain_pinned_appraisals,
        ),
        policy=policy,
    )


def _item_ref(slice_name: SliceName, item: BaseModel) -> str:
    identity = str(
        getattr(
            item,
            (
                "activity_event_ref"
                if slice_name == "world_life" and isinstance(item, (ActiveActivityContextItem, CompletedActivityContextItem, PlannedActivityContextItem, ActivityLifecycleStateContextItem))
                else "biography_id"
                if slice_name == "world_life" and isinstance(item, BiographicalWorldContextItem)
                else "influence_id"
                if slice_name == "perception_results"
                and isinstance(item, ExternalPerceptionLifeInfluenceView)
                else _ITEM_ID[slice_name]
            ),
        )
    )
    if slice_name == "action_budget":
        identity = f"{identity}:{item.window_id}"
    return identity


def _observation_event_aliases(projection: LedgerProjection) -> dict[str, str]:
    """Resolve legacy observation IDs only when their committed envelope is exact.

    Early typed psychological records cited an ``observation_id`` while the
    ledger authority is the corresponding ``ObservationRecorded`` event.  The
    projection retains an exact revision and envelope hash, so this is a
    deterministic normalization, not a text lookup or a permissive alias.
    An ambiguous or absent match deliberately remains unresolved.
    """

    by_identity: dict[tuple[int, str], list[CommittedWorldEventRef]] = {}
    for event in projection.committed_world_event_refs:
        if event.event_type == "ObservationRecorded":
            by_identity.setdefault((event.world_revision, event.payload_hash), []).append(event)
    aliases: dict[str, str] = {}
    for observation in projection.message_observations:
        candidates = by_identity.get(
            (observation.world_revision, observation.event_payload_hash), []
        )
        if len(candidates) == 1:
            aliases[observation.observation_id] = candidates[0].event_id
    return aliases


def _genesis_carried_fact_recall(item: FactRecallItem) -> bool:
    return (
        "WorldStarted" in item.accepted_fact_event_ref
        and item.accepted_fact_world_revision == 1
    )


def _typed_refs(item: BaseModel, *, observation_aliases: dict[str, str]) -> tuple[str, ...] | None:
    if isinstance(item, RecentDialogueItem):
        return tuple(sorted(claim.authority_event_ref for claim in item.source_claims))
    if isinstance(item, SharedMediaDeliveryContextItem):
        return (item.authority_event_ref,)
    if isinstance(item, PendingOutboundExpressionItem):
        return (item.authority_event_ref,)
    if isinstance(item, MemoryRetrievalItem):
        return tuple(claim[0] for claim in item.committed_source_claims())
    if isinstance(item, RecentExperienceContextItem):
        return tuple(
            sorted(
                (
                    item.content.authority_event_ref,
                    item.content.descriptor_event_ref,
                )
            )
        )
    if isinstance(item, (ActiveWorldOccurrenceContextItem, ActiveActivityContextItem, CompletedActivityContextItem, PlannedActivityContextItem, ActivityLifecycleStateContextItem)):
        return tuple(sorted(binding.authority_event_ref for binding in item.source_bindings))
    if isinstance(item, WorldLifeContextItem):
        refs = {item.source.authority_event_ref}
        if item.content is not None:
            refs.add(item.content.descriptor_event_ref)
        return tuple(sorted(refs))
    if isinstance(item, BiographicalWorldContextItem):
        return tuple(sorted(binding.authority_event_ref for binding in item.source_bindings))
    if isinstance(item, PerceptionResultContextItem):
        return tuple(sorted({item.source.result_event_ref, item.source.receipt_event_ref}))
    if isinstance(item, ExternalPerceptionLifeInfluenceView):
        return (item.source_event_ref,)
    if isinstance(item, FactProjection):
        # A Fact's full assertion/evidence structure is committed by this
        # exact Fact event. Its retained observation id is an internal anchor,
        # not a second event authority that Context must resolve as an event.
        return (item.origin.accepted_event_ref,)
    if isinstance(item, AppraisalProjection):
        # AppraisalAccepted commits the reading. Observation evidence ids are
        # stimulus anchors inside that payload; requiring them as separate
        # committed-event authorities drops the whole appraisals slice whenever
        # message_observations no longer alias them (production photo-pressure
        # turns then saw appraisal_affect unavailable despite AppraisalAccepted).
        return (item.origin.accepted_event_ref,)
    if isinstance(item, AffectEpisodeProjection):
        # Same as appraisals: AffectEpisodeOpened is the Context authority.
        return (item.origin.accepted_event_ref,)
    if isinstance(item, FactRecallItem):
        if _genesis_carried_fact_recall(item):
            return (item.accepted_fact_event_ref,)
        return tuple(sorted((item.accepted_fact_event_ref, item.observation_event_ref)))
    if isinstance(item, PrivateImpressionProjection):
        # Only accepted impressions are a valid Context source.  Their source
        # refs are recorded event identities, never a model-generated summary.
        if item.origin is None:
            return None
        return tuple(sorted({item.origin.accepted_event_ref, *item.source_refs}))
    refs: set[str] = set()
    origin = getattr(item, "origin", None)
    for field in ("accepted_event_ref", "event_ref"):
        if value := getattr(origin, field, None):
            refs.add(value)
    values = getattr(item, "values", None)
    for evidence in getattr(values, "source_evidence_refs", ()):
        refs.add(observation_aliases.get(evidence.ref_id, evidence.ref_id))
    for binding in getattr(values, "source_bindings", ()):
        # Receipt authority has a typed immutable hash but no committed world
        # revision.  It cannot satisfy a committed-event-only ledger resolver.
        if getattr(binding, "receipt_id", None) is not None:
            return None
        if value := getattr(binding, "authority_event_ref", None):
            refs.add(value)
    for evidence in getattr(item, "evidence_refs", ()):
        refs.add(observation_aliases.get(evidence.ref_id, evidence.ref_id))
    return tuple(sorted(refs)) or None


def _normalize_observation_evidence(
    item: BaseModel, *, observation_aliases: dict[str, str]
) -> BaseModel:
    """Return a Context-only view whose evidence refs use ledger event IDs.

    Psychological projections written before the ledger Context boundary used
    durable observation IDs as their evidence locators.  The resolver proves
    the one-to-one event mapping from the retained revision and envelope hash,
    then normalizes the *read model* as well as its metadata.  The underlying
    projection remains untouched; a partial or ambiguous mapping is left
    unchanged and consequently fails closed in ``_domain_slice``.
    """

    evidence_refs = getattr(item, "evidence_refs", None)
    if not evidence_refs:
        return item
    normalized = tuple(
        evidence.model_copy(
            update={"ref_id": observation_aliases.get(evidence.ref_id, evidence.ref_id)}
        )
        for evidence in evidence_refs
    )
    return item.model_copy(update={"evidence_refs": normalized})


_CONTEXT_AFFECT_APPRAISAL_REF_LIMIT = 8


def _compact_affect_episode_context_view(item: BaseModel) -> BaseModel:
    """Bound the non-authoritative Affect read view without erasing history.

    The durable Affect projection intentionally retains every accepted
    appraisal reference.  A long, dense conversation can therefore make one
    active episode larger than the Context Capsule's per-item safety limit.
    Provider context needs the episode's current state plus representative
    causal endpoints, not a copy of the immutable ledger history.  Keep the
    opening reference and the newest references for each component; the full
    lineage remains in the projection and its accepted events.
    """

    if not isinstance(item, AffectEpisodeProjection):
        return item
    components = []
    changed = False
    for component in item.components:
        refs = component.appraisal_refs
        if len(refs) <= _CONTEXT_AFFECT_APPRAISAL_REF_LIMIT:
            components.append(component)
            continue
        retained = (refs[0], *refs[-(_CONTEXT_AFFECT_APPRAISAL_REF_LIMIT - 1) :])
        components.append(component.model_copy(update={"appraisal_refs": retained}))
        changed = True
    return item.model_copy(update={"components": tuple(components)}) if changed else item


def _typed_authority_claims(
    item: BaseModel, *, observation_aliases: dict[str, str]
) -> tuple[tuple[str, int, str], ...] | None:
    """Return exact embedded event claims, or None for an incomplete claim."""

    if isinstance(item, RecentDialogueItem):
        return tuple(
            sorted(
                (
                    claim.authority_event_ref,
                    claim.authority_world_revision,
                    claim.authority_payload_hash,
                )
                for claim in item.source_claims
            )
        )
    if isinstance(item, SharedMediaDeliveryContextItem):
        return (
            (
                item.authority_event_ref,
                item.authority_world_revision,
                item.authority_payload_hash,
            ),
        )
    if isinstance(item, PendingOutboundExpressionItem):
        return (
            (
                item.authority_event_ref,
                item.authority_world_revision,
                item.authority_payload_hash,
            ),
        )
    if isinstance(item, FactProjection):
        # The source evidence remains immutable inside the Fact event payload
        # and was verified by the Fact reducer. Context binds that complete
        # payload through ``origin.accepted_event_ref`` instead of attempting
        # to reinterpret its durable observation identifier as an event id.
        return ()
    if isinstance(item, (AppraisalProjection, AffectEpisodeProjection)):
        # Accepted psychological events already sealed stimulus evidence. Context
        # proves those acceptances; it does not re-prove observation envelopes.
        return ()
    if isinstance(item, FactRecallItem):
        if _genesis_carried_fact_recall(item):
            return (
                (
                    item.accepted_fact_event_ref,
                    item.accepted_fact_world_revision,
                    item.accepted_fact_payload_hash,
                ),
            )
        return tuple(
            sorted(
                (
                    (
                        item.accepted_fact_event_ref,
                        item.accepted_fact_world_revision,
                        item.accepted_fact_payload_hash,
                    ),
                    (
                        item.observation_event_ref,
                        item.observation_world_revision,
                        item.observation_event_payload_hash,
                    ),
                )
            )
        )
    if isinstance(item, RecentExperienceContextItem):
        return tuple(
            sorted(
                (
                    (
                        item.content.authority_event_ref,
                        item.content.authority_world_revision,
                        item.content.authority_payload_hash,
                    ),
                    (
                        item.content.descriptor_event_ref,
                        item.content.descriptor_world_revision,
                        item.content.descriptor_payload_hash,
                    ),
                )
            )
        )
    if isinstance(item, (ActiveWorldOccurrenceContextItem, ActiveActivityContextItem, CompletedActivityContextItem, PlannedActivityContextItem, ActivityLifecycleStateContextItem)):
        return tuple(
            sorted(
                (
                    binding.authority_event_ref,
                    binding.authority_world_revision,
                    binding.authority_payload_hash,
                )
                for binding in item.source_bindings
            )
        )
    if isinstance(item, WorldLifeContextItem):
        authorities = {
            (
                item.source.authority_event_ref,
                item.source.authority_world_revision,
                item.source.authority_payload_hash,
            ),
        }
        if item.content is not None:
            authorities.add(
                (
                    item.content.descriptor_event_ref,
                    item.content.descriptor_world_revision,
                    item.content.descriptor_payload_hash,
                )
            )
        return tuple(sorted(authorities))
    if isinstance(item, BiographicalWorldContextItem):
        return tuple(
            sorted(
                (
                    binding.authority_event_ref,
                    binding.authority_world_revision,
                    binding.authority_payload_hash,
                )
                for binding in item.source_bindings
            )
        )
    if isinstance(item, PerceptionResultContextItem):
        return (
            (
                item.source.receipt_event_ref,
                item.source.receipt_world_revision,
                item.source.receipt_payload_hash,
            ),
            (
                item.source.result_event_ref,
                item.source.result_world_revision,
                item.source.result_payload_hash,
            ),
        )
    if isinstance(item, ExternalPerceptionLifeInfluenceView):
        return (
            (
                item.source_event_ref,
                item.source_world_revision,
                item.source_event_payload_hash,
            ),
        )
    values = getattr(item, "values", None)
    claims: set[tuple[str, int, str]] = set()
    evidence_values = (
        *getattr(values, "source_evidence_refs", ()),
        *getattr(item, "evidence_refs", ()),
    )
    for evidence in evidence_values:
        if evidence.source_world_revision is None or not evidence.immutable_hash:
            return None
        claims.add(
            (
                observation_aliases.get(evidence.ref_id, evidence.ref_id),
                evidence.source_world_revision,
                evidence.immutable_hash,
            )
        )
    for binding in getattr(values, "source_bindings", ()):
        ref = getattr(binding, "authority_event_ref", None)
        if ref is None:
            continue
        revision = getattr(binding, "authority_world_revision", None)
        immutable_hash = getattr(binding, "authority_payload_hash", None)
        if revision is None or immutable_hash is None:
            return None
        claims.add((ref, revision, immutable_hash))
    if isinstance(item, MemoryRetrievalItem):
        claims.update(item.committed_source_claims())
    return tuple(sorted(claims))


def _privacy(slice_name: SliceName, item: BaseModel) -> PrivacyClass:
    values = getattr(item, "values", None)
    candidates: list[PrivacyClass] = [_PRIVACY_FLOOR[slice_name]]
    # The capsule compiler independently re-derives the typed value's privacy
    # floor and rejects any metadata classified below it (typed authority must
    # never be downgraded).  Stamp with the exact same derivation so composite
    # values -- e.g. a current_situation whose activity or social-environment
    # children carry ``withhold`` -- classify at least as strict as that floor.
    derived = derived_privacy_floor(slice_name, item)
    if derived is not None:
        candidates.append(derived)
    for value in (
        getattr(item, "privacy_class", None),
        getattr(values, "privacy_class", None),
        getattr(values, "privacy_ceiling", None),
        getattr(item, "privacy_ceiling", None),
    ):
        if value in _PRIVACY_RANK:
            candidates.append(value)
    return max(candidates, key=_PRIVACY_RANK.__getitem__)


def _recency_bp(item: BaseModel, logical_time: datetime | None) -> int:
    if logical_time is None:
        return 0
    instants = (
        getattr(item, "updated_at", None),
        getattr(item, "last_supported", None),
        getattr(item, "opened_at", None),
        getattr(getattr(item, "values", None), "occurred_to", None),
        getattr(item, "settled_at", None),
        getattr(item, "ended_at", None),
        getattr(item, "activated_at", None),
        getattr(item, "occurred_at", None),
        getattr(item, "shared_at", None),
        getattr(item, "send_at", None),
        getattr(item, "written_at", None),
    )
    instant = next((value for value in instants if value is not None), None)
    if instant is None:
        return 0
    age_seconds = max(0, int((logical_time - instant).total_seconds()))
    # Linear seven-day fixed-point window.  Integer arithmetic is replay stable.
    return max(0, 10_000 - age_seconds * 10_000 // RANK_RECENCY_WINDOW_SECONDS)


def _signal_bp(slice_name: SliceName, item: BaseModel) -> int:
    if slice_name == "recent_dialogue" and isinstance(item, RecentDialogueItem):
        reasons = set(item.continuity_reasons)
        if "current_turn" in reasons:
            return 10_000
        if "pending_interaction" in reasons:
            return 10_000
        if "topic_reactivation" in reasons:
            return 9_900
        if "recent_companion" in reasons or "recent" in reasons:
            return 9_800
        if "acknowledged_context" in reasons:
            return 9_700
    values = getattr(item, "values", None)
    direct = (
        getattr(values, "importance_bp", None),
        getattr(values, "retrieval_strength_bp", None),
        getattr(item, "confidence_bp", None),
        getattr(values, "confidence_bp", None),
        getattr(item, "strength_bp", None),
    )
    for value in direct:
        if isinstance(value, int):
            return max(0, min(10_000, value))
    if slice_name == "affect_episodes":
        components = getattr(item, "components", ())
        intensities = [getattr(value, "intensity_bp", 0) for value in components]
        return max(intensities, default=0)
    return RANK_DOMAIN_IMPORTANCE_BP[slice_name]


_READ_SCORE_SLICES = frozenset({"active_memory_candidates", "relevant_facts"})
CHAT_RECALL_AUTHORITY = "FactCommittedV2"


def memory_read_score_bp(
    *,
    recency_bp: int,
    importance_bp: int,
    relevance_bp: int = 10_000,
) -> int:
    """Park et al. recency × importance × relevance in basis points."""

    return (
        max(0, min(10_000, recency_bp))
        * max(0, min(10_000, importance_bp))
        * max(0, min(10_000, relevance_bp))
        // 100_000_000
    )


def _item_relevance_texts(item: BaseModel) -> tuple[str, ...]:
    excerpt = getattr(item, "source_excerpt", None)
    if isinstance(excerpt, str) and excerpt.strip():
        texts = [excerpt]
        predicate = getattr(item, "predicate_code", None)
        if isinstance(predicate, str) and predicate.strip():
            texts.append(predicate)
        return tuple(texts)
    excerpts = getattr(item, "source_excerpts", None)
    if not excerpts:
        return ()
    texts = tuple(
        part.text
        for part in excerpts
        if isinstance(getattr(part, "text", None), str) and part.text.strip()
    )
    return texts


def memory_relevance_bp(query_text: str, item: BaseModel) -> int:
    if not query_text.strip():
        return 10_000
    texts = _item_relevance_texts(item)
    if not texts:
        return 10_000
    return lexical_relevance_bp(query_text, texts)


def _rank(
    slice_name: SliceName,
    item: BaseModel,
    logical_time: datetime | None,
    query_text: str = "",
) -> int:
    if slice_name in _READ_SCORE_SLICES:
        recency_bp = _recency_bp(item, logical_time)
        if slice_name == "relevant_facts" and isinstance(item, FactRecallItem):
            if fact_predicate_is_stable(item.predicate_code):
                recency_bp = STABLE_FACT_RECENCY_BP
        return memory_read_score_bp(
            recency_bp=recency_bp,
            importance_bp=_signal_bp(slice_name, item),
            relevance_bp=memory_relevance_bp(query_text, item),
        )
    total_weight = sum(RANK_WEIGHT_BP.values())
    return (
        RANK_DOMAIN_IMPORTANCE_BP[slice_name] * RANK_WEIGHT_BP["domain_importance"]
        + _signal_bp(slice_name, item) * RANK_WEIGHT_BP["typed_signal"]
        + _recency_bp(item, logical_time) * RANK_WEIGHT_BP["recency"]
    ) // total_weight


def _bounded_relevant_facts(
    items: tuple[BaseModel, ...],
    logical_time: datetime | None,
    rank_overrides: frozenset[tuple[str, str]],
    query_text: str,
) -> tuple[BaseModel, ...]:
    """Stable single-slot Facts precede episodic set Facts.

    Identity and residence slots must not compete on recency with
    ``situation.recent`` entries from the same conversation week.
    """

    slice_name: SliceName = "relevant_facts"
    stable: list[BaseModel] = []
    episodic: list[BaseModel] = []
    for item in items:
        if isinstance(item, FactRecallItem) and fact_predicate_is_stable(item.predicate_code):
            stable.append(item)
        else:
            episodic.append(item)

    def episodic_key(item: BaseModel) -> tuple[int, str]:
        override = (slice_name, _item_ref(slice_name, item)) in rank_overrides
        score = (
            max(9_900, _rank(slice_name, item, logical_time, query_text))
            if override
            else _rank(slice_name, item, logical_time, query_text)
        )
        return (-score, _item_ref(slice_name, item))

    stable_sorted = sorted(
        stable,
        key=lambda item: (
            -getattr(item, "confidence_bp", 0),
            getattr(item, "predicate_code", ""),
            _item_ref(slice_name, item),
        ),
    )
    episodic_sorted = sorted(episodic, key=episodic_key)
    merged = (*stable_sorted, *episodic_sorted)
    return merged[:MAX_INPUT_ITEMS_PER_SLICE]


def _bounded_domain_items(
    slice_name: SliceName,
    items: tuple[BaseModel, ...],
    logical_time: datetime | None,
    rank_overrides: frozenset[tuple[str, str]] = frozenset(),
    query_text: str = "",
) -> tuple[BaseModel, ...] | None:
    """Apply the installed bounded selection policy before any ledger lookup."""

    if len(items) > MAX_RESOLVER_DOMAIN_SCAN_ITEMS:
        return None
    if slice_name == "relevant_facts":
        return _bounded_relevant_facts(items, logical_time, rank_overrides, query_text)
    return tuple(
        sorted(
            items,
            key=lambda item: (
                -(
                    max(9_900, _rank(slice_name, item, logical_time, query_text))
                    if (slice_name, _item_ref(slice_name, item)) in rank_overrides
                    else _rank(slice_name, item, logical_time, query_text)
                ),
                _item_ref(slice_name, item),
            ),
        )[:MAX_INPUT_ITEMS_PER_SLICE]
    )


def _binding(event: CommittedWorldEventRef) -> ResolvedSourceBinding:
    return ResolvedSourceBinding(
        source_kind="committed_event",
        authority_type=event.event_type,
        ref=event.event_id,
        source_world_revision=event.world_revision,
        immutable_hash=event.payload_hash,
    )


def _shared_media_kind(*, family: str, contract_kind: str | None, ecology_category: str | None) -> str:
    raw = contract_kind or ecology_category
    if isinstance(raw, str) and raw.strip():
        return raw.strip().removeprefix("character_media:")
    return family


_BOOK_LOCATION_MARKERS = ("book-market", "bookstore", "book_market")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _bound_shared_media_about(
    *,
    family: str,
    kind: str,
    candidate: object,
    projection: LedgerProjection,
) -> str:
    """Copy a place already bound on the candidate. Never invent a scene."""

    base = _shared_media_about(family=family, kind=kind)
    bound_refs = {
        ref
        for ref in getattr(candidate, "source_event_refs", ()) or ()
        if isinstance(ref, str) and ref
    }
    bound_refs.update(
        item.event_ref
        for item in getattr(candidate, "source_events", ()) or ()
        if isinstance(getattr(item, "event_ref", None), str)
    )
    if not bound_refs:
        return base
    place: str | None = None
    observed_at = getattr(candidate, "ecology_observed_at", None)
    for occurrence in projection.world_occurrences:
        settlement = getattr(occurrence, "settlement_event_ref", None)
        if not isinstance(settlement, str) or settlement not in bound_refs:
            continue
        location = str(getattr(occurrence, "location_ref", "") or "").lower()
        if any(marker in location for marker in _BOOK_LOCATION_MARKERS):
            place = "书店"
        if observed_at is None:
            settled_at = getattr(occurrence, "settled_at", None)
            if isinstance(settled_at, datetime):
                observed_at = settled_at
    if place is None:
        return base
    when = ""
    if isinstance(observed_at, datetime) and observed_at.tzinfo is not None:
        hour = observed_at.astimezone(_SHANGHAI).hour
        if 12 <= hour < 18:
            when = "下午的"
        elif 18 <= hour <= 23:
            when = "晚上的"
        elif hour < 12:
            when = "上午的"
    rest = base[2:] if base.startswith("一张") else base
    return f"一张{place}{when}{rest}"


def shared_media_delivery_items(
    *,
    ledger: LedgerPort,
    projection: LedgerProjection,
    recent_dialogue: tuple[RecentDialogueItem, ...],
) -> tuple[SharedMediaDeliveryContextItem, ...]:
    """Bind delivered photos as source-closed world facts. Failed joins drop the item."""

    deliveries = {item.delivery_id: item for item in projection.media_deliveries}
    if not deliveries:
        return ()
    plans = {item.plan_id: item for item in projection.media_plans}
    opportunities = {item.opportunity_id: item for item in projection.media_opportunities}
    candidates = {item.candidate_id: item for item in projection.photo_candidates}
    refs = tuple(
        sorted(
            (
                ref
                for ref in projection.committed_world_event_refs
                if ref.event_type == "MediaDeliveryShared"
            ),
            key=lambda ref: (ref.logical_time, ref.event_id),
            reverse=True,
        )
    )
    items: list[SharedMediaDeliveryContextItem] = []
    for ref in refs:
        if len(items) >= PRESENT_SHARED_MEDIA_ITEM_LIMIT:
            break
        located = ledger.lookup_event_commit(ref.event_id)
        if located is None:
            continue
        event, _commit = located
        if (
            event.event_id != ref.event_id
            or event.event_type != "MediaDeliveryShared"
            or event.payload_hash != ref.payload_hash
        ):
            continue
        try:
            payload = MediaDeliverySharedPayload.model_validate_json(event.payload_json)
        except ValueError:
            continue
        delivery = payload.delivery
        if delivery.delivery_id not in deliveries:
            continue
        plan = plans.get(delivery.plan_id)
        opportunity = opportunities.get(plan.opportunity_id) if plan is not None else None
        candidate = (
            candidates.get(opportunity.candidate_id) if opportunity is not None else None
        )
        if plan is None or opportunity is None or candidate is None:
            continue
        contract = candidate.character_media_contract
        kind = _shared_media_kind(
            family=opportunity.family,
            contract_kind=None if contract is None else contract.kind,
            ecology_category=opportunity.ecology_category or candidate.ecology_category,
        )
        privacy_layer = opportunity.media_privacy_ceiling
        if privacy_layer not in {"ordinary", "personal", "intimate"}:
            continue
        if opportunity.family not in {"life_share", "character_media"}:
            continue
        shared_at = delivery.shared_at or ref.logical_time
        he_spoke_after = any(
            item.speaker == "counterpart" and item.occurred_at > shared_at
            for item in recent_dialogue
        )
        try:
            items.append(
                compile_shared_media_delivery_item(
                    delivery_id=delivery.delivery_id,
                    shared_at=shared_at,
                    family=opportunity.family,
                    kind=kind,
                    privacy_layer=privacy_layer,
                    he_spoke_after=he_spoke_after,
                    authority_event_ref=ref.event_id,
                    authority_world_revision=ref.world_revision,
                    authority_payload_hash=ref.payload_hash,
                    about=_bound_shared_media_about(
                        family=opportunity.family,
                        kind=kind,
                        candidate=candidate,
                        projection=projection,
                    ),
                )
            )
        except ValueError:
            continue
    return tuple(items)


def pending_outbound_expression_items(
    *,
    projection: LedgerProjection,
    logical_time: datetime | None,
) -> tuple[PendingOutboundExpressionItem, ...]:
    """Bind unsent later followups as source-closed world facts. Failed joins drop the row."""

    items: list[PendingOutboundExpressionItem] = []
    for fact in queued_later_facts(projection, logical_time=logical_time):
        if len(items) >= PRESENT_PENDING_OUTBOUND_ITEM_LIMIT:
            break
        try:
            items.append(
                compile_pending_outbound_expression_item(
                    action_id=fact.action_id,
                    plan_id=fact.plan_id,
                    beat_id=fact.beat_id,
                    text=fact.text,
                    written_at=fact.written_at,
                    send_at=fact.send_at,
                    he_spoke_after=fact.he_spoke_after,
                    i_spoke_after=fact.i_spoke_after,
                    authority_event_ref=fact.authority_event_ref,
                    authority_world_revision=fact.authority_world_revision,
                    authority_payload_hash=fact.authority_payload_hash,
                )
            )
        except ValueError:
            continue
    return tuple(items)


def _epoch_genesis_fact_recall_item(
    *,
    fact: FactProjection,
    ledger: LedgerPort,
    projection: LedgerProjection,
    archive_ledger: LedgerPort | None,
    archive_observations: Mapping[str, tuple[Observation, WorldEvent, int]] | None = None,
) -> FactRecallItem | None:
    if not is_epoch_genesis_fact(fact):
        return None
    fact_ref = next(
        (
            item
            for item in projection.committed_world_event_refs
            if item.event_id == fact.origin.accepted_event_ref
        ),
        None,
    )
    if fact_ref is None or fact_ref.world_revision > projection.world_revision:
        return None
    migrated = epoch_migration_source_excerpt(
        fact,
        archive_ledger=archive_ledger,
        archive_observations=archive_observations,
    )
    if migrated is None:
        return None
    source_excerpt, observation, source_event, observation_world_revision = migrated
    if (
        observation.world_id != projection.world_id
        or not source_excerpt
        or fact.values.assertion_binding.asserted_subject_ref != fact.values.subject_ref
    ):
        return None
    return FactRecallItem(
        fact_id=fact.fact_id,
        subject_ref=fact.values.subject_ref,
        predicate_code=fact.values.predicate_code,
        source_excerpt=source_excerpt,
        confidence_bp=fact.values.confidence_bp,
        privacy_class=fact.values.privacy_class,
        occurred_at=observation.logical_time,
        committed_at=fact.committed_at,
        updated_at=fact.updated_at,
        accepted_fact_event_ref=fact_ref.event_id,
        accepted_fact_world_revision=fact_ref.world_revision,
        accepted_fact_payload_hash=fact_ref.payload_hash,
        observation_event_ref=source_event.event_id,
        observation_world_revision=observation_world_revision,
        observation_event_payload_hash=source_event.payload_hash,
        source_observation_id=observation.observation_id,
        assertion_payload_ref=observation.payload_ref,
        assertion_payload_hash=observation.payload_hash,
        accepted_value_binding=FactObservationValueBinding.from_fact_values(fact.values),
    )


def fact_recall_items(
    *,
    ledger: LedgerPort,
    projection: LedgerProjection,
    facts: tuple[FactProjection, ...],
    archive_ledger: LedgerPort | None = None,
) -> tuple[FactRecallItem, ...]:
    """Close active Facts over the exact messages which asserted them.

    This is the only semantic reconstruction seam for persistent Facts.  Any
    missing, legacy, ambiguous, or contradictory authority drops that item;
    opaque refs and hashes are never presented as if they were prose.
    """

    committed = {item.event_id: item for item in projection.committed_world_event_refs}
    observations_by_id: dict[str, list[object]] = {}
    for item in projection.message_observations:
        observations_by_id.setdefault(item.observation_id, []).append(item)
    genesis_observation_ids = tuple(
        observation_id
        for fact in facts
        if is_epoch_genesis_fact(fact)
        for observation_id in archive_observation_ids(fact)
    )
    archive_observations = (
        lookup_archive_observations(
            archive_ledger,
            observation_ids=genesis_observation_ids,
        )
        if archive_ledger is not None and genesis_observation_ids
        else {}
    )
    output: list[FactRecallItem] = []
    for fact in facts:
        genesis_item = _epoch_genesis_fact_recall_item(
            fact=fact,
            ledger=ledger,
            projection=projection,
            archive_ledger=archive_ledger,
            archive_observations=archive_observations,
        )
        if genesis_item is not None:
            output.append(genesis_item)
            continue
        binding = fact.values.assertion_binding
        if binding.source_kind != "observed_message" or binding.payload_ref is None:
            continue
        fact_ref = committed.get(fact.origin.accepted_event_ref)
        fact_located = ledger.lookup_event_commit(fact.origin.accepted_event_ref)
        if fact_ref is None or fact_located is None:
            continue
        fact_event, fact_commit = fact_located
        if (
            fact_event.event_id != fact_ref.event_id
            or fact_event.event_type != fact_ref.event_type
            or fact_event.payload_hash != fact_ref.payload_hash
            or fact_event.event_id not in fact_commit.event_ids
            or fact_commit.world_revision < fact_ref.world_revision
            or fact_commit.ledger_sequence > projection.ledger_sequence
            or fact_ref.world_revision > projection.world_revision
        ):
            continue
        try:
            if fact_ref.event_type == "FactCommittedV2":
                materialized = rehydrate_fact_commit_materialized_v2_json(fact_event.payload_json)
                values_match = (
                    materialized.fact_id == fact.fact_id
                    and materialized.values.model_dump(mode="json")
                    == fact.values.model_dump(mode="json")
                )
            elif fact_ref.event_type == "FactCorrected":
                corrected = FactChangedPayload.model_validate_json(fact_event.payload_json)
                values_match = corrected.operation == "correct" and corrected.fact_after == fact
            else:
                values_match = False
        except ValueError:
            continue
        if not values_match:
            continue

        observation_candidates = observations_by_id.get(binding.source_ref, [])
        if len(observation_candidates) != 1:
            continue
        observation_ref = observation_candidates[0]
        if (
            observation_ref.actor != binding.actor_ref
            or observation_ref.channel != binding.channel
            or observation_ref.payload_ref != binding.payload_ref
            or observation_ref.content_payload_hash != binding.content_payload_hash
        ):
            continue
        source_event_candidates = tuple(
            item
            for item in projection.committed_world_event_refs
            if item.event_type == "ObservationRecorded"
            and item.world_revision == observation_ref.world_revision
            and item.payload_hash == observation_ref.event_payload_hash
        )
        if len(source_event_candidates) != 1:
            continue
        source_ref = source_event_candidates[0]
        source_located = ledger.lookup_event_commit(source_ref.event_id)
        if source_located is None:
            continue
        source_event, source_commit = source_located
        if (
            source_event.event_id != source_ref.event_id
            or source_event.event_type != source_ref.event_type
            or source_event.payload_hash != source_ref.payload_hash
            or source_event.event_id not in source_commit.event_ids
            or source_commit.world_revision < source_ref.world_revision
            or source_commit.ledger_sequence > projection.ledger_sequence
            or source_ref.world_revision >= fact_ref.world_revision
        ):
            continue
        try:
            observation = Observation.model_validate_json(source_event.payload_json)
        except ValueError:
            continue
        if (
            observation.observation_id != binding.source_ref
            or observation.world_id != projection.world_id
            or observation.actor != binding.actor_ref
            or observation.channel != binding.channel
            or observation.payload_ref != binding.payload_ref
            or observation.payload_hash != binding.content_payload_hash
            or not observation.text
            or binding.asserted_subject_ref != fact.values.subject_ref
        ):
            continue
        output.append(
            FactRecallItem(
                fact_id=fact.fact_id,
                subject_ref=fact.values.subject_ref,
                predicate_code=fact.values.predicate_code,
                source_excerpt=observation.text,
                confidence_bp=fact.values.confidence_bp,
                privacy_class=fact.values.privacy_class,
                occurred_at=observation.logical_time,
                committed_at=fact.committed_at,
                updated_at=fact.updated_at,
                accepted_fact_event_ref=fact_ref.event_id,
                accepted_fact_world_revision=fact_ref.world_revision,
                accepted_fact_payload_hash=fact_ref.payload_hash,
                observation_event_ref=source_ref.event_id,
                observation_world_revision=source_ref.world_revision,
                observation_event_payload_hash=source_ref.payload_hash,
                source_observation_id=observation.observation_id,
                assertion_payload_ref=observation.payload_ref,
                assertion_payload_hash=observation.payload_hash,
                accepted_value_binding=FactObservationValueBinding.from_fact_values(fact.values),
            )
        )
    return tuple(output)


def historical_fact_recall_items(
    *,
    ledger: LedgerPort,
    projection: LedgerProjection,
    subject_refs: frozenset[str],
) -> tuple[HistoricalFactRecallItem, ...]:
    """Rehydrate superseded Fact before-images from immutable transition lineage."""

    reader = getattr(ledger, "recent_fact_transition_events", None)
    if callable(reader):
        events = reader(
            subject_refs=subject_refs,
            cursor=ProjectionCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            ),
            limit=MAX_RECALL_CORPUS_DOCUMENTS,
        )
    else:
        # Compatibility for narrow test/decorator LedgerPorts.  Production
        # SQLite uses the indexed method above and never scans this projection.
        fallback: list[WorldEvent] = []
        for transition in reversed(projection.fact_transitions):
            if (
                transition.operation not in {"correct", "withdraw"}
                or transition.values_before is None
                or transition.values_before.subject_ref not in subject_refs
            ):
                continue
            located = ledger.lookup_event_commit(transition.accepted_event_ref)
            if located is not None:
                fallback.append(located[0])
            if len(fallback) >= MAX_RECALL_CORPUS_DOCUMENTS:
                break
        events = tuple(fallback)
    prepared: list[tuple[WorldEvent, FactProjection]] = []
    for event in events:
        try:
            payload = FactChangedPayload.model_validate_json(event.payload_json)
        except ValueError:
            continue
        historical = payload.fact_before
        if (
            event.event_type not in {"FactCorrected", "FactWithdrawn"}
            or payload.operation not in {"correct", "withdraw"}
            or historical is None
            or historical.values.subject_ref not in subject_refs
            or payload.fact_after.fact_id != historical.fact_id
            or payload.fact_after.entity_revision != historical.entity_revision + 1
        ):
            continue
        prepared.append((event, historical))
    resolved = {
        item.accepted_fact_event_ref: item
        for item in fact_recall_items(
            ledger=ledger,
            projection=projection,
            facts=tuple(item[1] for item in prepared),
        )
    }
    output: list[HistoricalFactRecallItem] = []
    for event, historical in prepared:
        item = resolved.get(historical.origin.accepted_event_ref)
        if item is None or event.logical_time < historical.updated_at:
            continue
        output.append(
            HistoricalFactRecallItem(
                **item.model_dump(mode="python", exclude={"status"}),
                valid_from=historical.updated_at,
                valid_to=event.logical_time,
            )
        )
    return tuple(output)


class _AuditRecoveryLedger:
    """A read-only ledger prefix for all nested Context readers."""

    def __init__(self, ledger: LedgerPort, projection: LedgerProjection) -> None:
        self._ledger = ledger
        self._projection = projection
        self.world_id = projection.world_id
        self._cursor = ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )

    def _require_prefix(self, cursor: ProjectionCursor) -> None:
        if any(getattr(cursor, key) > getattr(self._cursor, key) for key in (
            "world_revision", "deliberation_revision", "ledger_sequence",
        )):
            raise ValueError("Context recovery read exceeds its audited prefix")

    def project(self) -> LedgerProjection:
        return self._projection

    def project_at(self, cursor: ProjectionCursor) -> LedgerProjection:
        self._require_prefix(cursor)
        return self._projection if cursor == self._cursor else self._ledger.project_at(cursor)

    def lookup_event_commit(self, event_id: str):
        found = self._ledger.lookup_event_commit(event_id)
        if found is None:
            return None
        event, commit = found
        if event.world_id != self.world_id or event.event_id not in commit.event_ids:
            raise ValueError("Context recovery event lacks its exact World commit")
        try:
            self._require_prefix(ProjectionCursor(
                world_revision=commit.world_revision,
                deliberation_revision=commit.deliberation_revision,
                ledger_sequence=commit.ledger_sequence,
            ))
        except ValueError:
            return None
        return found

    def observation_events_at(self, locators, *, cursor: ProjectionCursor):
        self._require_prefix(cursor)
        return self._ledger.observation_events_at(locators, cursor=cursor)

    def recent_fact_transition_events(self, *, subject_refs, cursor: ProjectionCursor, limit):
        self._require_prefix(cursor)
        return self._ledger.recent_fact_transition_events(
            subject_refs=subject_refs, cursor=cursor, limit=limit,
        )

    def resolve_committed_event_refs(self, event_ids, *, at_world_revision: int):
        if at_world_revision > self._cursor.world_revision or any(
            self.lookup_event_commit(event_id) is None for event_id in event_ids
        ):
            raise ValueError("Context recovery source exceeds its audited prefix")
        return self._ledger.resolve_committed_event_refs(
            event_ids, at_world_revision=at_world_revision,
        )

    def resolve_initial_world_event_ref(self, *, at_world_revision: int):
        if at_world_revision > self._cursor.world_revision:
            raise ValueError("Context recovery source exceeds its audited prefix")
        ref = self._ledger.resolve_initial_world_event_ref(at_world_revision=at_world_revision)
        if self.lookup_event_commit(ref.event_id) is None:
            raise ValueError("Context recovery initial World source is unavailable")
        return ref


class LedgerProjectionContextResolver(TrustedInternalContextResolver):
    """Resolve Context domains from exactly one ledger projection cursor."""

    def __init__(
        self,
        *,
        ledger: LedgerPort,
        situation_compiler: SituationCompiler,
        relevance_scope: ContextRelevanceScope | None = None,
        life_content_store: ImmutableLifeContentStore | None = None,
        perception_result_reader: PerceptionResultReader | None = None,
        expression_payload_store: ImmutableExpressionPayloadStore | None = None,
        recall_coordinator: RecallCoordinator | None = None,
        biographical_catalog: BiographicalLifecycleCatalog | None = None,
        biographical_timezone_name: str | None = None,
        biographical_timeline: BiographicalTimelineConfiguredPayload | None = None,
        reviewed_npc_identity_summaries: dict[str, str] | None = None,
        archive_ledger: LedgerPort | None = None,
        retain_pinned_appraisals: bool = False,
    ) -> None:
        super().__init__()
        if (biographical_catalog is None) != (biographical_timezone_name is None):
            raise ValueError("biographical Context catalog and timezone must be installed together")
        if (biographical_catalog is None) != (biographical_timeline is None):
            raise ValueError(
                "biographical Context catalog and timeline authority must be installed together"
            )
        if (
            biographical_catalog is not None
            and biographical_timezone_name is not None
            and biographical_timeline is not None
            and (
                biographical_catalog.document_hash != biographical_timeline.document_hash
                or biographical_timezone_name != biographical_timeline.timezone_name
            )
        ):
            raise ValueError("biographical Context catalog does not match its timeline authority")
        if type(retain_pinned_appraisals) is not bool:
            raise TypeError("pinned appraisal context option must be boolean")
        self._retain_pinned_appraisals = retain_pinned_appraisals
        self._ledger = ledger
        # Reconstruct the same configured readers at an audited prefix. In
        # particular, nested activity readers must not consult the live head.
        self._audit_recovery_options = {
            "situation_compiler": situation_compiler,
            "relevance_scope": relevance_scope,
            "life_content_store": life_content_store,
            "perception_result_reader": perception_result_reader,
            "expression_payload_store": expression_payload_store,
            "recall_coordinator": recall_coordinator,
            "biographical_catalog": biographical_catalog,
            "biographical_timezone_name": biographical_timezone_name,
            "biographical_timeline": biographical_timeline,
            "reviewed_npc_identity_summaries": reviewed_npc_identity_summaries,
            "archive_ledger": archive_ledger,
            "retain_pinned_appraisals": retain_pinned_appraisals,
        }
        self._archive_ledger = archive_ledger
        self._situation_compiler = situation_compiler
        self._relevance_scope = relevance_scope
        self._memory_retrieval = MemoryRetrievalCompiler(
            ledger=ledger,
            life_content_store=life_content_store,
        )
        self._recent_dialogue = RecentDialogueCompiler(
            ledger=ledger, expression_payload_store=expression_payload_store
        )
        self._conversation_continuity = ConversationContinuityCompiler()
        self._life_content = LifeContentCompiler(store=life_content_store)
        self._life_content_store = life_content_store
        self._reviewed_npc_identity_summaries = reviewed_npc_identity_summaries or {}
        self._world_life = WorldLifeContextCompiler(
            life_content=self._life_content,
            planned_activity_reader=CompositePlannedActivityReader(
                ChatLifeIntentPlannedReader(ledger=ledger),
                WorldLifeIntentPlannedReader(ledger=ledger),
                DayOpenLifeIntentPlannedReader(ledger=ledger),
            ),
            completed_activity_reader=CompositeCompletedActivityReader(
                ChatLifeIntentCompletedReader(ledger=ledger),
                WorldLifeIntentCompletedReader(ledger=ledger),
                DayOpenLifeIntentCompletedReader(ledger=ledger),
            ),
            active_activity_reader=CompositeActiveActivityReader(
                ChatLifeIntentActiveReader(ledger=ledger),
                WorldLifeIntentActiveReader(ledger=ledger),
                DayOpenLifeIntentActiveReader(ledger=ledger),
                LifeDevelopmentProposalReader(ledger=ledger, content_store=life_content_store)
                if life_content_store is not None else None,
            ),
            active_occurrence_reader=(
                LifeDevelopmentProposalReader(
                    ledger=ledger,
                    content_store=life_content_store,
                )
                if life_content_store is not None
                else None
            ),
            biography=biographical_catalog,
            biography_timezone=(
                ZoneInfo(biographical_timezone_name)
                if biographical_catalog is not None and biographical_timezone_name is not None
                else None
            ),
        )
        self._biographical_timeline = biographical_timeline
        self._perception_results = (
            PerceptionResultContextCompiler(reader=perception_result_reader)
            if perception_result_reader is not None
            else None
        )
        self._recall = recall_coordinator
        # Cache the complete resolver product at the module boundary instead
        # of teaching every slice caller about dependencies. The exact query
        # hash covers cursor, snapshot, actor, trigger, consumer profile and
        # logical time; fixed resolver collaborators are instance-scoped.
        self._resolved_context_cache: dict[str, ResolvedContextResult] = {}
        self._resolve_calls = 0
        self._resolve_cache_hits = 0
        self._resolve_cache_misses = 0

    def for_audit_recovery(self, query: ContextCompileQuery) -> LedgerProjectionContextResolver:
        projection = self._ledger.project_at(query.cursor)
        self._validate_projection(query, projection)
        return LedgerProjectionContextResolver(
            ledger=_AuditRecoveryLedger(self._ledger, projection),
            **self._audit_recovery_options,
        )

    def _biographical_timeline_source(
        self,
        projection: LedgerProjection,
    ) -> WorldLifeSourceBinding | None:
        expected = self._biographical_timeline
        if expected is None:
            return None
        authorities = tuple(
            item
            for item in projection.committed_world_event_refs
            if item.event_type == "BiographicalTimelineConfigured"
        )
        if len(authorities) != 1:
            raise ValueError(
                "configured biographical Context requires exactly one committed timeline authority"
            )
        authority = authorities[0]
        located = self._ledger.lookup_event_commit(authority.event_id)
        if located is None:
            raise ValueError("biographical timeline authority is not readable")
        event, commit = located
        if (
            commit.world_revision < authority.world_revision
            or event.event_id not in commit.event_ids
            or event.payload_hash != authority.payload_hash
        ):
            raise ValueError("biographical timeline authority binding is inconsistent")
        try:
            recorded = BiographicalTimelineConfiguredPayload.model_validate_json(event.payload_json)
        except ValueError as exc:
            raise ValueError("biographical timeline authority payload is invalid") from exc
        if recorded != expected:
            raise ValueError("biographical timeline authority differs from configured chronology")
        return WorldLifeSourceBinding(
            authority_event_ref=authority.event_id,
            authority_world_revision=authority.world_revision,
            authority_payload_hash=authority.payload_hash,
        )

    def performance_counters(self) -> ContextResolverPerformanceCounters:
        return ContextResolverPerformanceCounters(
            resolve_calls=self._resolve_calls,
            cache_hits=self._resolve_cache_hits,
            cache_misses=self._resolve_cache_misses,
        )

    def _npc_identity_recall_items(
        self, projection: LedgerProjection
    ) -> tuple[NpcIdentityRecallItem, ...]:
        """Close NPC descriptors over registration plus exact immutable bytes."""

        if self._life_content_store is None:
            return ()
        views = npc_identity_views(
            projection,
            content_store=self._life_content_store,
            reviewed_identity_summaries=self._reviewed_npc_identity_summaries,
        )
        committed = {item.event_id: item for item in projection.committed_world_event_refs}
        registrations: dict[str, list[CommittedWorldEventRef]] = {}
        for authority in projection.committed_world_event_refs:
            if authority.event_type != "NpcRegistered":
                continue
            located = self._ledger.lookup_event_commit(authority.event_id)
            if located is None:
                continue
            event, commit = located
            if (
                event.payload_hash != authority.payload_hash
                or event.event_id not in commit.event_ids
                or commit.ledger_sequence > projection.ledger_sequence
            ):
                continue
            try:
                payload = NpcRegisteredPayload.model_validate_json(event.payload_json)
            except ValueError:
                continue
            registrations.setdefault(f"npc:{payload.npc.npc_id}", []).append(authority)

        npc_by_ref = {f"npc:{item.npc_id}": item for item in projection.npcs}
        result: list[NpcIdentityRecallItem] = []
        for view in views:
            exact = committed.get(view.promotion_event_ref)
            candidates = registrations.get(view.npc_ref, [])
            registration = (
                exact
                if exact is not None and exact.event_type == "NpcRegistered"
                else candidates[0]
                if len(candidates) == 1
                else None
            )
            npc = npc_by_ref.get(view.npc_ref)
            if registration is None or npc is None:
                continue
            bindings = tuple(
                sorted(
                    (
                        RecallSourceBinding(
                            source_kind="committed_event",
                            authority_type="NpcRegistered",
                            ref=registration.event_id,
                            source_world_revision=registration.world_revision,
                            immutable_hash=registration.payload_hash,
                        ),
                        RecallSourceBinding(
                            source_kind="immutable_payload",
                            authority_type="NpcIdentityDescriptor",
                            ref=view.descriptor_content_ref,
                            source_world_revision=registration.world_revision,
                            immutable_hash=view.descriptor_payload_hash,
                        ),
                    ),
                    key=lambda item: (
                        item.source_kind,
                        item.authority_type,
                        item.ref,
                        item.source_world_revision,
                        item.immutable_hash,
                    ),
                )
            )
            result.append(
                NpcIdentityRecallItem(
                    npc_ref=view.npc_ref,
                    descriptor=view.descriptor,
                    descriptor_content_ref=view.descriptor_content_ref,
                    lifecycle_state=view.lifecycle_state,
                    occurred_at=registration.logical_time,
                    privacy_class=npc.privacy_class,
                    bindings=bindings,
                    link_refs=tuple(
                        sorted(
                            {
                                *view.shared_experience_refs,
                                *view.active_plan_refs,
                                *view.organization_refs,
                                *view.life_arc_refs,
                                *(
                                    (view.current_location_ref,)
                                    if view.current_location_ref
                                    else ()
                                ),
                            }
                        )
                    ),
                )
            )
        return tuple(sorted(result, key=lambda item: item.npc_ref))

    def _scope_for_query(
        self, query: ContextCompileQuery, projection: LedgerProjection
    ) -> ContextRelevanceScope:
        """Derive the current interlocutor only for the default local scope.

        A composition root can still install a fixed, narrower scope.  Without
        one, an incoming Observation's committed actor is the only additional
        subject whose relationship, appraisal, facts, and memories may enter
        that turn.  This prevents the previous actor-only default from making
        all user-specific psychological state invisible to a companion.
        """

        if self._relevance_scope is not None:
            if self._relevance_scope.actor_ref != query.actor_ref:
                raise ValueError("Context relevance scope belongs to another actor")
            return self._relevance_scope
        if query.trigger_ref not in {
            item.event_id for item in projection.committed_world_event_refs
        }:
            return ContextRelevanceScope(actor_ref=query.actor_ref)
        located = self._ledger.lookup_event_commit(query.trigger_ref)
        if located is None:
            return ContextRelevanceScope(actor_ref=query.actor_ref)
        event, commit = located
        if (
            event.world_id != query.world_id
            or event.event_type != "ObservationRecorded"
            or commit.world_revision > projection.world_revision
            or commit.deliberation_revision > projection.deliberation_revision
            or commit.ledger_sequence > projection.ledger_sequence
            or event.actor == query.actor_ref
        ):
            return ContextRelevanceScope(actor_ref=query.actor_ref)
        return ContextRelevanceScope(actor_ref=query.actor_ref, related_subject_refs=(event.actor,))

    def _budget_authority_refs(
        self, projection: LedgerProjection
    ) -> dict[str, tuple[str, ...] | None]:
        """Return the finite event closure for each projected budget account.

        ``BudgetAccount`` is a reducer aggregate: its live balances are changed
        by reservation and settlement events, so a configuration event alone
        cannot authoritatively describe it.  The account schema deliberately
        has no mutable ``origin`` field.  Instead Context binds the closed
        event lineage which the reducer uses to arrive at the pinned balance.

        This is intentionally bounded.  A long-lived account whose complete
        lineage no longer fits in the Context source envelope is unavailable,
        rather than being presented with an incomplete balance history.
        """

        accounts = {account.account_id for account in projection.budget_accounts}
        refs_by_account: dict[str, list[str]] = {account_id: [] for account_id in accounts}
        configuration_count: dict[str, int] = {account_id: 0 for account_id in accounts}
        reservation_accounts = {
            reservation.reservation_id: reservation.account_id
            for reservation in projection.budget_reservations
        }
        budget_event_types = {
            "BudgetAccountConfigured",
            "BudgetReserved",
            "BudgetSettled",
            "BudgetReleased",
            "BudgetAdjusted",
        }

        # The action-budget slice has one bounded authority closure shared by
        # all accounts.  Once the projection already contains more budget
        # events than that envelope can hold, the slice is necessarily
        # unavailable; walking every historical reservation just to discover
        # that fact turns a normal chat Context build into an O(history)
        # series of verified SQLite lookups.  Returning the optional slice as
        # unavailable is the same fail-closed result as the bounded loop
        # below, and does not expose or infer any balance.
        if (
            sum(
                ref.event_type in budget_event_types
                for ref in projection.committed_world_event_refs
            )
            > MAX_SOURCE_REFS_PER_ITEM
        ):
            return {account_id: None for account_id in accounts}

        for ref in projection.committed_world_event_refs:
            if ref.event_type not in budget_event_types:
                continue
            located = self._ledger.lookup_event_commit(ref.event_id)
            if located is None:
                # The projection claims this event exists; missing storage is
                # a broken authority chain for every potentially affected
                # account, not an invitation to infer a balance.
                return {account_id: None for account_id in accounts}
            event, commit = located
            if (
                event.event_id != ref.event_id
                or event.event_type != ref.event_type
                or event.payload_hash != ref.payload_hash
                or commit.world_revision < ref.world_revision
                or commit.world_revision > projection.world_revision
            ):
                return {account_id: None for account_id in accounts}
            payload = event.payload()
            account_id: str | None
            if ref.event_type == "BudgetAccountConfigured":
                raw = payload.get("account")
                account_id = raw.get("account_id") if isinstance(raw, dict) else None
                if account_id in configuration_count:
                    configuration_count[account_id] += 1
            elif ref.event_type == "BudgetReserved":
                raw = payload.get("reservation")
                account_id = raw.get("account_id") if isinstance(raw, dict) else None
            else:
                raw = payload.get("settlement")
                reservation_id = raw.get("reservation_id") if isinstance(raw, dict) else None
                account_id = reservation_accounts.get(reservation_id)
            if account_id in refs_by_account:
                refs_by_account[account_id].append(ref.event_id)
                # Once one live account exceeds the bounded authority closure,
                # the slice is unavailable by contract.  Stop walking the
                # remaining historical budget events instead of performing a
                # verified SQLite lookup for every old reservation/settlement
                # on every interactive turn.
                if len(refs_by_account[account_id]) > MAX_SOURCE_REFS_PER_ITEM:
                    return {account_id: None for account_id in accounts}

        result: dict[str, tuple[str, ...] | None] = {}
        for account_id, refs in refs_by_account.items():
            if configuration_count[account_id] != 1 or len(refs) > MAX_SOURCE_REFS_PER_ITEM:
                result[account_id] = None
            else:
                result[account_id] = tuple(sorted(refs))
        # ResolverProof bounds the authority closure for the whole slice, not
        # only each account item.  Multiple individually valid long-lived
        # accounts can otherwise exceed that envelope and crash an ordinary
        # chat turn.  Until budget checkpoints provide a compact authority,
        # fail the optional slice closed as unavailable instead of presenting
        # a partial balance or raising after many turns.
        slice_refs = {ref for refs in result.values() if refs is not None for ref in refs}
        if len(slice_refs) > MAX_SOURCE_REFS_PER_ITEM:
            return {account_id: None for account_id in accounts}
        return result

    def resolve(self, query: ContextCompileQuery) -> ResolvedContextResult:
        started = time.perf_counter()
        self._resolve_calls += 1
        head = self._ledger.project()
        if (
            head.world_revision != query.world_revision
            or head.deliberation_revision != query.deliberation_revision
            or head.ledger_sequence != query.ledger_sequence
        ):
            raise ValueError("historical Context resolution requires an indexed projection reader")
        query_hash = context_query_hash(query)
        cached = self._resolved_context_cache.get(query_hash)
        if cached is not None:
            self._resolve_cache_hits += 1
            self._resolved_context_cache.pop(query_hash)
            self._resolved_context_cache[query_hash] = cached
            return cached
        self._resolve_cache_misses += 1
        projection = self._ledger.project_at(query.cursor)
        self._validate_projection(query, projection)
        scope = self._scope_for_query(query, projection)
        observation_aliases = _observation_event_aliases(projection)
        after_snapshot = time.perf_counter()

        situation_result = self._situation_compiler.compile(
            request_from_ledger_projection(
                projection, actor_ref=query.actor_ref, event_resolver=self._ledger
            )
        )
        if situation_result.internal is None:
            raise ValueError("internal Situation compilation did not return internal authority")
        situation = situation_result.internal

        subject_refs = scope.subject_refs
        domain_phase_started = time.perf_counter()
        recent_dialogue = self._recent_dialogue.compile_with_acknowledgements(
            projection=projection,
            actor_ref=query.actor_ref,
            subject_refs=subject_refs,
            max_user_items=CHAT_RECENT_DIALOGUE_ITEM_LIMIT,
        )
        media_deliveries = shared_media_delivery_items(
            ledger=self._ledger,
            projection=projection,
            recent_dialogue=recent_dialogue.dialogue,
        )
        pending_outbound = pending_outbound_expression_items(
            projection=projection,
            logical_time=query.logical_time,
        )
        photo_dialogue = tuple(
            delivered_photo_dialogue_item(
                delivery_id=item.delivery_id,
                about=item.about,
                shared_at=item.shared_at,
                actor_ref=query.actor_ref,
                authority_event_ref=item.authority_event_ref,
                authority_world_revision=item.authority_world_revision,
                authority_payload_hash=item.authority_payload_hash,
            )
            for item in media_deliveries
        )
        dialogue_candidates = (
            tuple(
                sorted(
                    (*recent_dialogue.dialogue, *photo_dialogue),
                    key=lambda item: (item.sequence, item.occurred_at, item.dialogue_id),
                    reverse=True,
                )
            )
            if photo_dialogue
            else recent_dialogue.dialogue
        )
        recent_dialogue_ms = (time.perf_counter() - domain_phase_started) * 1000
        domain_phase_started = time.perf_counter()
        scoped_facts = tuple(
            item
            for item in projection.facts
            if item.values.status != "withdrawn" and item.values.subject_ref in subject_refs
        )
        recalled_facts = fact_recall_items(
            ledger=self._ledger,
            projection=projection,
            facts=scoped_facts,
            archive_ledger=self._archive_ledger,
        )
        historical_facts = historical_fact_recall_items(
            ledger=self._ledger,
            projection=projection,
            subject_refs=subject_refs,
        )
        fact_ms = (time.perf_counter() - domain_phase_started) * 1000
        domain_phase_started = time.perf_counter()
        scoped_threads = tuple(
            item for item in projection.threads if item.values.subject_ref in subject_refs
        )
        user_channel_limited_content_refs = collect_user_channel_limited_content_refs(
            ledger=self._ledger,
            projection=projection,
        )
        user_channel_limited_impression_ids = collect_user_channel_limited_impression_ids(
            ledger=self._ledger,
            projection=projection,
        )
        life_content = self._life_content.compile(
            cursor=query.cursor,
            actor_ref=query.actor_ref,
            viewer_privacy_ceiling="private",
            projection=projection,
            user_channel_limited_content_refs=user_channel_limited_content_refs,
        )
        scoped_experiences = tuple(
            item
            for item in life_content.experience_items
            if query.actor_ref in item.values.participant_refs
        )
        expected_experience_ids = {
            item.experience_id
            for item in projection.experiences
            if hasattr(item, "origin") and query.actor_ref in item.values.participant_refs
        }
        experience_content_unavailable = any(
            item.source_entity_id in expected_experience_ids
            and item.reason in _EXPERIENCE_CONTENT_UNAVAILABLE_REASONS
            for item in life_content.suppressions
        )
        recent_experiences_domain = (
            scoped_experiences if scoped_experiences or not experience_content_unavailable else None
        )
        world_life = self._world_life.compile(
            projection=projection,
            actor_ref=query.actor_ref,
            cursor=query.cursor,
            biographical_timeline_source=self._biographical_timeline_source(projection),
            user_channel_limited_content_refs=user_channel_limited_content_refs,
        )
        world_life_ms = (time.perf_counter() - domain_phase_started) * 1000
        domain_phase_started = time.perf_counter()
        perception_results = (
            self._perception_results.compile(
                projection=projection,
                cursor=query.cursor,
                subject_refs=scope.subject_refs,
            )
            if self._perception_results is not None
            else None
        )
        external_perception_influences = compile_external_perception_life_influences(projection)
        if external_perception_influences:
            perception_results = (
                *(perception_results or ()),
                *external_perception_influences,
            )
        scoped_source_ids = {
            *(item.fact_id for item in scoped_facts),
            *(item.thread_id for item in scoped_threads),
            *(item.experience_id for item in scoped_experiences),
            *(item.record.record_id for item in projection.prehistory_records
              if item.actor_ref == query.actor_ref),
        }
        scoped_memories = tuple(
            item
            for item in projection.memory_candidates
            if item.values.status == "active"
            and all(
                binding.source_id in scoped_source_ids for binding in item.values.source_bindings
            )
        )
        memory_retrievals = self._memory_retrieval.compile(
            cursor=query.cursor,
            candidates=scoped_memories,
            viewer_privacy_ceiling="private",
            projection=projection,
            actor_ref=query.actor_ref,
        )
        open_threads_for_continuity = tuple(
            item for item in scoped_threads if item.values.status == "open"
        )
        if len(open_threads_for_continuity) > MAX_RESOLVER_DOMAIN_SCAN_ITEMS:
            open_threads_for_continuity = ()
        dialogue_text_by_ref: dict[str, str] = {}
        for item in dialogue_candidates:
            dialogue_text_by_ref[item.dialogue_id] = item.text
            if item.dialogue_id.startswith("dialogue:observation:"):
                dialogue_text_by_ref[item.dialogue_id.removeprefix("dialogue:observation:")] = (
                    item.text
                )
            for claim in item.source_claims:
                dialogue_text_by_ref[claim.authority_event_ref] = item.text
        missing_anchor_aliases = {
            ref.ref_id: observation_aliases.get(ref.ref_id, ref.ref_id)
            for item in open_threads_for_continuity
            for ref in item.values.anchor_evidence_refs
            if ref.ref_id not in dialogue_text_by_ref
        }
        # Older open-thread anchors may be outside the 64-message dialogue
        # candidate window. Resolve a small, deterministic authority set
        # directly instead of making recency a hidden prerequisite for topic
        # reactivation.
        if len(missing_anchor_aliases) <= 64:
            anchor_events = self._resolve_exact(
                missing_anchor_aliases.values(), query.world_revision
            )
            for original_ref, event_ref in sorted(missing_anchor_aliases.items()):
                committed = anchor_events.get(event_ref)
                stored = self._ledger.lookup_event_commit(event_ref)
                if (
                    committed is None
                    or committed.event_type != "ObservationRecorded"
                    or stored is None
                ):
                    continue
                try:
                    observation = Observation.model_validate_json(stored[0].payload_json)
                except ValueError:
                    continue
                if observation.text:
                    dialogue_text_by_ref[original_ref] = observation.text
                    dialogue_text_by_ref[event_ref] = observation.text
        facts_for_continuity = (
            recalled_facts if len(recalled_facts) <= MAX_RESOLVER_DOMAIN_SCAN_ITEMS else ()
        )
        memories_for_continuity = (
            memory_retrievals.items
            if len(memory_retrievals.items) <= MAX_RESOLVER_DOMAIN_SCAN_ITEMS
            else ()
        )
        bounded_texts = _bounded_recall_texts
        retrieval_candidates: list[ContinuityRetrievalCandidate] = []
        for item in facts_for_continuity:
            texts = bounded_texts((item.source_excerpt,))
            if texts:
                retrieval_candidates.append(
                    ContinuityRetrievalCandidate(
                        slice_name="relevant_facts",
                        item_ref=item.fact_id,
                        texts=texts,
                    )
                )
        for item in memories_for_continuity:
            texts = bounded_texts(
                tuple(source.text for source in item.source_excerpts)
            )
            if texts:
                retrieval_candidates.append(
                    ContinuityRetrievalCandidate(
                        slice_name="active_memory_candidates",
                        item_ref=item.candidate_id,
                        texts=texts,
                    )
                )
        for item in open_threads_for_continuity:
            texts = bounded_texts(
                tuple(
                    dialogue_text_by_ref[ref.ref_id]
                    for ref in item.values.anchor_evidence_refs
                    if ref.ref_id in dialogue_text_by_ref
                )
            )
            if texts:
                retrieval_candidates.append(
                    ContinuityRetrievalCandidate(
                        slice_name="open_threads",
                        item_ref=item.thread_id,
                        texts=texts,
                    )
                )
        continuity = self._conversation_continuity.compile(
            dialogue=dialogue_candidates,
            trigger_ref=query.trigger_ref,
            acknowledged_observation_event_refs=recent_dialogue.acknowledged_observation_event_refs,
            retrieval_candidates=tuple(retrieval_candidates),
        )
        recent_dialogue = continuity.dialogue
        continuity_rank_overrides = continuity.rank_overrides
        is_inbound_dialogue_turn = any(
            claim.authority_event_ref == query.trigger_ref
            for item in dialogue_candidates
            if item.speaker == "counterpart"
            for claim in item.source_claims
        )
        context_facts = (
            tuple(
                item
                for item in recalled_facts
                if ("relevant_facts", item.fact_id) in continuity_rank_overrides
            )
            if is_inbound_dialogue_turn
            else recalled_facts
        )
        context_memories = (
            tuple(
                item
                for item in memory_retrievals.items
                if (
                    "active_memory_candidates",
                    item.candidate_id,
                )
                in continuity_rank_overrides
            )
            if is_inbound_dialogue_turn
            else memory_retrievals.items
        )
        memory_ms = (time.perf_counter() - domain_phase_started) * 1000
        domain_phase_started = time.perf_counter()
        appraisal_by_id = {item.appraisal_id: item for item in projection.appraisals}
        active_affect = tuple(
            item for item in projection.affect_episodes if item.status == "active"
        )
        affect_refs = {
            ref.appraisal_id
            for item in active_affect
            for component in item.components
            for ref in component.appraisal_refs
        }
        scoped_affect: tuple[BaseModel, ...] | None
        if not affect_refs.issubset(appraisal_by_id):
            scoped_affect = None
        else:
            scoped_affect = tuple(
                _compact_affect_episode_context_view(
                    _normalize_observation_evidence(item, observation_aliases=observation_aliases)
                )
                for item in active_affect
                if all(
                    appraisal_by_id[ref.appraisal_id].subject_ref in subject_refs
                    for component in item.components
                    for ref in component.appraisal_refs
                )
            )
        scoped_appraisals: tuple[BaseModel, ...] = tuple(
            _normalize_observation_evidence(item, observation_aliases=observation_aliases)
            for item in projection.appraisals
            if item.status == "active" and item.subject_ref in subject_refs
        )
        scoped_relationships = tuple(
            item
            for item in projection.relationship_states
            if item.subject_ref in subject_refs and item.origin is not None
        )
        # Current Affect stays in working Context. Historical affective
        # accessibility is rebuilt from the appraisal acceptances that carry
        # their own exact event/evidence closure; AffectEpisodeProjection does
        # not retain every update event identity and therefore is not itself
        # safe historical recall authority.
        recall_appraisal_candidates = tuple(
            sorted(
                (item for item in projection.appraisals if item.subject_ref in subject_refs),
                key=lambda item: (item.accepted_at, item.appraisal_id),
                reverse=True,
            )[:32]
        )
        recall_appraisals = tuple(
            normalized
            for item in recall_appraisal_candidates
            for normalized in (
                _normalize_observation_evidence(item, observation_aliases=observation_aliases),
            )
            if isinstance(normalized, AppraisalProjection)
        )
        recall_affect_openings = []
        for episode in sorted(
            projection.affect_episodes,
            key=lambda item: (item.opened_at, item.episode_id),
            reverse=True,
        )[:32]:
            located = self._ledger.lookup_event_commit(episode.origin.accepted_event_ref)
            if located is None:
                continue
            event, _ = located
            committed = next(
                (
                    ref
                    for ref in projection.committed_world_event_refs
                    if ref.event_id == event.event_id
                ),
                None,
            )
            if (
                committed is None
                or event.event_type != "AffectEpisodeOpened"
                or committed.world_revision > query.world_revision
                or committed.payload_hash != event.payload_hash
            ):
                continue
            try:
                opening = AffectEpisodeOpenedPayload.model_validate_json(event.payload_json).episode
            except ValueError:
                continue
            if (
                opening.episode_id != episode.episode_id
                or opening.origin.accepted_event_ref != event.event_id
            ):
                continue
            scoped_appraisal_subjects: set[str] = set()
            subject_authority_refs: set[str] = set()
            subject_scope_is_closed = True
            for meaning_ref in {
                (meaning.appraisal_id, meaning.hypothesis_id)
                for component in opening.components
                for meaning in component.appraisal_refs
            }:
                appraisal = next(
                    (item for item in projection.appraisals if item.appraisal_id == meaning_ref[0]),
                    None,
                )
                if appraisal is None or appraisal.subject_ref not in subject_refs:
                    subject_scope_is_closed = False
                    break
                appraisal_located = self._ledger.lookup_event_commit(
                    appraisal.origin.accepted_event_ref
                )
                appraisal_committed = next(
                    (
                        ref
                        for ref in projection.committed_world_event_refs
                        if ref.event_id == appraisal.origin.accepted_event_ref
                    ),
                    None,
                )
                if appraisal_located is None or appraisal_committed is None:
                    subject_scope_is_closed = False
                    break
                appraisal_event, _ = appraisal_located
                if (
                    appraisal_event.event_type != "AppraisalAccepted"
                    or appraisal_committed.world_revision > query.world_revision
                    or appraisal_committed.payload_hash != appraisal_event.payload_hash
                ):
                    subject_scope_is_closed = False
                    break
                try:
                    accepted_appraisal = AppraisalAcceptedPayload.model_validate_json(
                        appraisal_event.payload_json
                    ).appraisal
                except ValueError:
                    subject_scope_is_closed = False
                    break
                if (
                    accepted_appraisal.appraisal_id != appraisal.appraisal_id
                    or accepted_appraisal.subject_ref != appraisal.subject_ref
                    or meaning_ref[1]
                    not in {
                        hypothesis.hypothesis_id for hypothesis in accepted_appraisal.hypotheses
                    }
                ):
                    subject_scope_is_closed = False
                    break
                scoped_appraisal_subjects.add(appraisal.subject_ref)
                subject_authority_refs.add(appraisal_event.event_id)
            if not subject_scope_is_closed or not scoped_appraisal_subjects:
                continue
            recall_affect_openings.append(
                AffectOpeningRecallItem(
                    episode=opening,
                    subject_refs=tuple(sorted(scoped_appraisal_subjects)),
                    subject_authority_refs=tuple(sorted(subject_authority_refs)),
                )
            )
        if self._recall is not None:
            recall_cursor = RecallCursor(
                world_revision=query.world_revision,
                deliberation_revision=query.deliberation_revision,
                ledger_sequence=query.ledger_sequence,
            )
            try:
                # Dialogue inside the bounded working-memory window is already
                # guaranteed a place in the compiled Context, so "recalling"
                # it is a no-op that crowds facts, threads and older dialogue
                # out of the small bounded hit set.  The 30-turn recall eval
                # measured exactly that: 28 of 29 prefetch traces surfaced
                # only in-window dialogue echoes.  Recall therefore searches
                # what the character could have forgotten — everything
                # outside the window — mirroring the chat recent_dialogue
                # item budget below.
                recall_dialogue = tuple(
                    sorted(dialogue_candidates, key=lambda item: item.occurred_at)
                )[:-CHAT_RECENT_DIALOGUE_ITEM_LIMIT]
                npc_identities = self._npc_identity_recall_items(projection)
                recall_sources = RecallCorpusSources(
                    recent_dialogue=recall_dialogue,
                    relevant_facts=recalled_facts,
                    historical_facts=historical_facts,
                    open_threads=open_threads_for_continuity,
                    recent_experiences=tuple(
                        item for item in scoped_experiences if hasattr(item, "origin")
                    ),
                    world_life=tuple(
                        item for item in world_life if isinstance(item, WorldLifeContextItem)
                    ),
                    active_memory_candidates=memory_retrievals.items,
                    affect_openings=tuple(recall_affect_openings),
                    appraisals=recall_appraisals,
                    private_impressions=tuple(
                        item
                        for item in projection.private_impressions
                        if item.status in {"active", "released"}
                        and item.subject_ref in subject_refs
                        and item.origin is not None
                        and item.impression_id not in user_channel_limited_impression_ids
                    ),
                    npc_identities=npc_identities,
                )
                required_authority_refs = required_recall_authority_refs(recall_sources)
                recall_authority = select_recall_authority_bindings(
                    sources=recall_sources,
                    candidates=(
                        *(
                            RecallSourceBinding(
                                source_kind="committed_event",
                                authority_type=item.event_type,
                                ref=item.event_id,
                                source_world_revision=item.world_revision,
                                immutable_hash=item.payload_hash,
                            )
                            for item in projection.committed_world_event_refs
                            if item.world_revision <= query.world_revision
                            and item.event_id in required_authority_refs
                        ),
                        *(
                            binding
                            for identity in npc_identities
                            for binding in identity.bindings
                            if binding.source_kind == "immutable_payload"
                        ),
                    ),
                )
                self._recall.refresh(
                    cursor=recall_cursor,
                    actor_ref=query.actor_ref,
                    subject_refs=tuple(sorted(scope.subject_refs)),
                    logical_time=situation.logical_time,
                    trigger_ref=query.trigger_ref,
                    sources=recall_sources.model_copy(
                        update={"authority_bindings": recall_authority}
                    ),
                )
                trigger_dialogue = next(
                    (
                        item
                        for item in dialogue_candidates
                        if any(
                            claim.authority_event_ref == query.trigger_ref
                            for claim in item.source_claims
                        )
                    ),
                    None,
                )
                if trigger_dialogue is not None:
                    recent_attention_dialogue = select_recent_dialogue_for_automatic_recall(
                        tuple(
                            item.model_dump(mode="json")
                            for item in sorted(
                                (
                                    candidate
                                    for candidate in dialogue_candidates
                                    if candidate.dialogue_id != trigger_dialogue.dialogue_id
                                ),
                                key=lambda candidate: (
                                    candidate.sequence,
                                    candidate.occurred_at,
                                    candidate.dialogue_id,
                                ),
                            )
                        )
                    )
                    attention_request = build_automatic_recall_request(
                        observation_text=trigger_dialogue.text,
                        recent_dialogue_values=recent_attention_dialogue,
                        affect_values=tuple(
                            item.model_dump(mode="json") for item in scoped_affect or ()
                        ),
                        appraisal_values=tuple(
                            item.model_dump(mode="json") for item in scoped_appraisals
                        ),
                        relationship_values=tuple(
                            item.model_dump(mode="json") for item in scoped_relationships
                        ),
                        situation_value=situation.model_dump(mode="json"),
                        open_thread_values=tuple(
                            item.values.model_dump(mode="json")
                            for item in open_threads_for_continuity
                        ),
                        link_refs=_automatic_recall_link_refs(
                            projection=projection,
                            subject_refs=subject_refs,
                            link_refs=(
                                *(
                                    str(getattr(item, "source_cluster_ref"))
                                    for item in scoped_appraisals
                                    if getattr(item, "source_cluster_ref", None)
                                ),
                                *(
                                    str(getattr(component, "source_cluster_ref"))
                                    for item in scoped_affect or ()
                                    for component in getattr(item, "components", ())
                                    if getattr(component, "source_cluster_ref", None)
                                ),
                                *(item.thread_id for item in open_threads_for_continuity),
                            ),
                        ),
                        limit=4,
                    )
                    try:
                        self._recall.schedule_prefetch(
                            expected_cursor=recall_cursor,
                            query_text=attention_request.query_text,
                            lexical_text=attention_request.lexical_text,
                            occurred_from=attention_request.occurred_from,
                            occurred_to=attention_request.occurred_to,
                            link_refs=attention_request.link_refs,
                            memory_kinds=attention_request.memory_kinds,
                            accessibility_seed=(
                                f"recall-prefetch:{query.trigger_ref}:{query.ledger_sequence}"
                            ),
                            trigger_ref=query.trigger_ref,
                            limit=attention_request.limit,
                        )
                    except Exception as exc:
                        # Automatic attention is optional; the successfully
                        # refreshed cursor-pinned snapshot must remain
                        # available for a later character-chosen pull.  Treat
                        # only the failed job as degraded instead of rolling
                        # back the whole recall context.
                        self._recall.discard_scheduled_prefetch(
                            recall_cursor,
                            trigger_ref=query.trigger_ref,
                        )
                        _LOG.warning(
                            "world v2 recall prefetch unavailable world=%s cursor=%s failure=%s",
                            query.world_id,
                            query.ledger_sequence,
                            type(exc).__name__,
                        )
            except Exception as exc:
                self._recall.discard(
                    recall_cursor,
                    trigger_ref=query.trigger_ref,
                )
                _LOG.warning(
                    "world v2 recall sidecar unavailable world=%s cursor=%s failure=%s",
                    query.world_id,
                    query.ledger_sequence,
                    type(exc).__name__,
                )
        budget_authority_refs = self._budget_authority_refs(projection)
        budget_ms = (time.perf_counter() - domain_phase_started) * 1000
        after_domains = time.perf_counter()
        trigger_text = next(
            (
                item.text
                for item in dialogue_candidates
                if any(
                    claim.authority_event_ref == query.trigger_ref
                    for claim in item.source_claims
                )
            ),
            "",
        )

        domains: dict[SliceName, tuple[BaseModel, ...] | None] = {
            # ``agent:companion`` is the canonical companion actor reference.
            # Early character-core provisioning committed ``actor:companion``
            # (legacy alias of the same companion core); accept both so an
            # already-committed core remains consumable by the companion.
            "character_core": (
                (projection.character_core,)
                if projection.character_core is not None
                and projection.character_core.actor_ref in (query.actor_ref, "actor:companion")
                else None
            ),
            "recent_dialogue": recent_dialogue,
            "relationship_slice": scoped_relationships,
            "appraisals": scoped_appraisals,
            "affect_episodes": scoped_affect,
            "open_threads": tuple(item for item in scoped_threads if item.values.status == "open"),
            "relevant_facts": context_facts,
            "recent_experiences": recent_experiences_domain,
            "world_life": world_life,
            "active_memory_candidates": context_memories,
            "available_capabilities": tuple(
                item
                for item in projection.capability_grants
                if item.values.actor_ref == query.actor_ref and item.values.state == "active"
            ),
            "action_budget": tuple(projection.budget_accounts),
            "private_impressions": tuple(
                item
                for item in projection.private_impressions
                if item.status == "active"
                and item.subject_ref in subject_refs
                and item.origin is not None
                and item.impression_id not in user_channel_limited_impression_ids
            ),
            "advisories": None,
            "media_deliveries": media_deliveries,
            "pending_outbound": pending_outbound,
        }
        if perception_results is not None:
            domains["perception_results"] = perception_results
        domains = {
            slice_name: (
                None
                if items is None
                else _bounded_domain_items(
                    slice_name,
                    items,
                    query.logical_time,
                    continuity_rank_overrides,
                    trigger_text,
                )
            )
            for slice_name, items in domains.items()
        }

        refs_by_item: dict[tuple[SliceName, str], tuple[str, ...] | None] = {}
        required_refs: set[str] = set()
        for slice_name, items in domains.items():
            if items is None:
                continue
            for item in items:
                refs = (
                    budget_authority_refs.get(item.account_id)
                    if isinstance(item, BudgetAccount)
                    else _typed_refs(item, observation_aliases=observation_aliases)
                )
                refs_by_item[(slice_name, _item_ref(slice_name, item))] = refs
                if refs is not None:
                    required_refs.update(refs)

        resolved_events = self._resolve_exact(required_refs, query.world_revision)
        after_refs = time.perf_counter()
        resolved: dict[str, object] = {
            "situation": self._situation_slice(query, situation, scope),
        }
        request_fields = {
            "character_core": "character_core",
            "recent_dialogue": "recent_dialogue",
            "relationship_slice": "relationship_slice",
            "appraisals": "appraisals",
            "affect_episodes": "affect_episodes",
            "open_threads": "open_threads",
            "relevant_facts": "relevant_facts",
            "recent_experiences": "recent_experiences",
            "world_life": "world_life",
            "active_memory_candidates": "active_memory_candidates",
            "available_capabilities": "available_capabilities",
            "action_budget": "action_budget",
            "private_impressions": "private_impressions",
            "advisories": "advisories",
            "media_deliveries": "media_deliveries",
            "pending_outbound": "pending_outbound",
        }
        if perception_results is not None:
            request_fields["perception_results"] = "perception_results"
        for slice_name, field in request_fields.items():
            items = domains[slice_name]
            if items is None:
                resolved[field] = None
                continue
            built = self._domain_slice(
                query,
                slice_name,
                items,
                refs_by_item,
                resolved_events,
                scope,
                observation_aliases,
                continuity_rank_overrides,
            )
            resolved[field] = built

        if self._retain_pinned_appraisals:
            from .pinned_appraisal_context import compile_pinned_appraisals
            resolved["pinned_appraisals"] = compile_pinned_appraisals(
                projection=projection, query=query, ledger=self._ledger,
            )
        request = ContextCapsuleRequest(
            world_id=query.world_id,
            snapshot_id=query.snapshot_id,
            snapshot_hash=query.snapshot_hash,
            actor_ref=query.actor_ref,
            consumer_scope=query.consumer_scope,
            trigger_ref=query.trigger_ref,
            world_revision=query.world_revision,
            deliberation_revision=query.deliberation_revision,
            ledger_sequence=query.ledger_sequence,
            # Situation owns the deployment's civil-time presentation.  Keep
            # the pinned instant, but expose the same local offset at the
            # capsule root so the model does not receive contradictory UTC
            # and local "current time" representations.
            logical_time=situation.logical_time,
            **resolved,
        )
        _LOG.warning(
            "world v2 Context resolve phases world=%s cursor=%s snapshot_ms=%.1f domains_ms=%.1f refs_ms=%.1f total_ms=%.1f required_refs=%d projection_events=%d recent_dialogue_ms=%.1f facts_ms=%.1f world_life_ms=%.1f memory_ms=%.1f budget_ms=%.1f",
            query.world_id,
            query.ledger_sequence,
            (after_snapshot - started) * 1000,
            (after_domains - after_snapshot) * 1000,
            (after_refs - after_domains) * 1000,
            (time.perf_counter() - started) * 1000,
            len(required_refs),
            len(projection.committed_world_event_refs),
            recent_dialogue_ms,
            fact_ms,
            world_life_ms,
            memory_ms,
            budget_ms,
        )
        result = ResolvedContextResult(
            query_hash=query_hash,
            capability=self.capability,
            resolved_context=request,
        )
        self._resolved_context_cache[query_hash] = result
        while len(self._resolved_context_cache) > 16:
            self._resolved_context_cache.pop(next(iter(self._resolved_context_cache)))
        return result

    def resolve_advisory_slice(
        self,
        query: ContextCompileQuery,
        advisories: tuple[InnerAdvisoryProjection, ...],
    ) -> ResolvedSlice[tuple[InnerAdvisoryProjection, ...]]:
        """Bind ephemeral advisory candidates to exact committed event sources.

        Classifiers may propose these values, but cannot supply their own
        authority.  This resolver verifies the event ids against the same
        projection cursor used for the rest of the capsule and issues the
        regular Context proof only after that verification succeeds.
        """

        if len(advisories) > MAX_INPUT_ITEMS_PER_SLICE:
            raise ValueError("advisory overlay exceeds the Context input limit")
        projection = self._ledger.project()
        self._validate_projection(query, projection)
        scope = self._relevance_scope or ContextRelevanceScope(actor_ref=query.actor_ref)
        if scope.actor_ref != query.actor_ref:
            raise ValueError("Context relevance scope belongs to another actor")
        frozen = tuple(
            InnerAdvisoryProjection.model_validate(item.model_dump(mode="python", warnings="error"))
            for item in advisories
        )
        if len({item.advisory_id for item in frozen}) != len(frozen):
            raise ValueError("advisory overlay contains duplicate identities")
        refs = tuple(sorted({ref for item in frozen for ref in item.source_refs}))
        events = self._resolve_exact(refs, query.world_revision)
        metadata: list[ResolvedItemMetadata] = []
        for item in frozen:
            bindings = tuple(
                sorted(
                    (_binding(events[ref]) for ref in item.source_refs),
                    key=lambda value: (
                        value.source_kind,
                        value.authority_type,
                        value.ref,
                        value.source_world_revision,
                        value.immutable_hash,
                    ),
                )
            )
            metadata.append(
                ResolvedItemMetadata(
                    item_ref=item.advisory_id,
                    rank_score_bp=item.confidence_bp,
                    privacy_class="private",
                    source_bindings=bindings,
                    source_hash=source_bindings_hash(bindings),
                    value_hash=canonical_value_hash(item),
                )
            )
        ordered = tuple(
            sorted(
                zip(frozen, metadata, strict=True),
                key=lambda pair: (-pair[1].rank_score_bp, pair[1].item_ref),
            )
        )
        values = tuple(pair[0] for pair in ordered)
        ordered_metadata = tuple(pair[1] for pair in ordered)
        return ResolvedSlice(
            world_id=query.world_id,
            snapshot_id=query.snapshot_id,
            snapshot_hash=query.snapshot_hash,
            pinned_world_revision=query.world_revision,
            value=values,
            resolver_proof=self._proof(query, "advisories", ordered_metadata, scope),
            item_metadata=ordered_metadata,
        )

    @staticmethod
    def _validate_projection(query: ContextCompileQuery, projection: LedgerProjection) -> None:
        if (
            projection.world_id != query.world_id
            or projection.world_revision != query.world_revision
            or projection.deliberation_revision != query.deliberation_revision
            or projection.ledger_sequence != query.ledger_sequence
            or projection_snapshot_id(projection) != query.snapshot_id
            or projection.semantic_hash != query.snapshot_hash
            or projection.logical_time != query.logical_time
        ):
            raise ValueError("ledger projection does not match the exact Context query cursor")

    def _resolve_exact(
        self, refs: Iterable[str], world_revision: int
    ) -> dict[str, CommittedWorldEventRef]:
        requested = tuple(sorted(set(refs)))
        if not requested:
            return {}
        resolved = self._ledger.resolve_committed_event_refs(
            requested, at_world_revision=world_revision
        )
        if set(resolved) - set(requested):
            raise ValueError("ledger event resolver returned unrequested authority")
        for ref, event in resolved.items():
            if event.event_id != ref or event.world_revision > world_revision:
                raise ValueError("ledger event resolver returned invalid pinned authority")
            stored = self._ledger.lookup_event_commit(ref)
            if stored is None:
                raise ValueError("resolved Context authority is absent from the ledger")
            stored_event, commit = stored
            if (
                stored_event.event_id != ref
                or stored_event.event_type != event.event_type
                or stored_event.payload_hash != event.payload_hash
                # A batch may atomically append several world events.  The
                # committed-event index records each event's own revision,
                # whereas lookup_event_commit returns the batch's terminal
                # cursor.  Equality would reject every non-final event in a
                # valid settlement batch.
                or commit.world_revision < event.world_revision
                or commit.world_revision > world_revision
            ):
                raise ValueError("resolved Context authority contradicts its committed event")
        return resolved

    @staticmethod
    def _proof(
        query: ContextCompileQuery,
        slice_name: SliceName,
        metadata: tuple[ResolvedItemMetadata, ...],
        scope: ContextRelevanceScope,
    ) -> ResolverProof:
        refs = tuple(sorted({binding.ref for item in metadata for binding in item.source_bindings}))
        return ResolverProof(
            resolver_id=RESOLVER_ID,
            resolver_version=RESOLVER_VERSION,
            policy_digest=RESOLUTION_POLICY_DIGEST,
            world_id=query.world_id,
            snapshot_id=query.snapshot_id,
            snapshot_hash=query.snapshot_hash,
            pinned_world_revision=query.world_revision,
            slice_name=slice_name,
            query_ref=query.trigger_ref,
            window_ref=(
                f"cursor:{query.world_revision}:{query.deliberation_revision}:"
                f"{query.ledger_sequence}:scope:{scope.digest[:16]}"
            ),
            policy_version=RESOLUTION_POLICY_VERSION,
            completeness="complete",
            privacy_floor=_PRIVACY_FLOOR[slice_name],
            explicit_authority_refs=refs,
            authority_refs_digest=authority_refs_digest(refs),
            result_set_hash=resolved_result_set_hash(slice_name, metadata),
        )

    def _situation_slice(
        self,
        query: ContextCompileQuery,
        situation: BaseModel,
        scope: ContextRelevanceScope,
    ) -> ResolvedSlice:
        if situation.source_revisions:
            bindings = tuple(
                sorted(
                    (
                        ResolvedSourceBinding(
                            source_kind="committed_event",
                            authority_type=f"situation_source:{source.domain}",
                            ref=source.event_ref,
                            source_world_revision=source.source_world_revision,
                            immutable_hash=source.payload_hash,
                        )
                        for source in situation.source_revisions
                    ),
                    key=lambda item: (
                        item.source_kind,
                        item.authority_type,
                        item.ref,
                        item.source_world_revision,
                        item.immutable_hash,
                    ),
                )
            )
        else:
            bindings = (
                ResolvedSourceBinding(
                    source_kind="projection_snapshot",
                    authority_type="LedgerProjection",
                    ref=query.snapshot_id,
                    source_world_revision=query.world_revision,
                    immutable_hash=situation.authority_snapshot_hash,
                ),
            )
        metadata = (
            ResolvedItemMetadata(
                item_ref=query.actor_ref,
                rank_score_bp=10_000,
                privacy_class=_privacy("current_situation", situation),
                source_bindings=bindings,
                source_hash=source_bindings_hash(bindings),
                value_hash=canonical_value_hash(situation),
            ),
        )
        return ResolvedSlice(
            world_id=query.world_id,
            snapshot_id=query.snapshot_id,
            snapshot_hash=query.snapshot_hash,
            pinned_world_revision=query.world_revision,
            value=situation,
            resolver_proof=self._proof(query, "current_situation", metadata, scope),
            item_metadata=metadata,
        )

    def _domain_slice(
        self,
        query: ContextCompileQuery,
        slice_name: SliceName,
        items: tuple[BaseModel, ...],
        refs_by_item: dict[tuple[SliceName, str], tuple[str, ...] | None],
        events: dict[str, CommittedWorldEventRef],
        scope: ContextRelevanceScope,
        observation_aliases: dict[str, str],
        rank_overrides: frozenset[tuple[str, str]] = frozenset(),
    ) -> ResolvedSlice | None:
        metadata: list[ResolvedItemMetadata] = []
        selected_items: list[BaseModel] = []
        selected_authority_refs: set[str] = set()
        if slice_name == "recent_dialogue":
            dialogue_items = tuple(
                item for item in items if isinstance(item, RecentDialogueItem)
            )
            if len(dialogue_items) == len(items):
                items = pack_recent_dialogue_under_source_budget(dialogue_items)
        for item in items:
            item_ref = _item_ref(slice_name, item)
            refs = refs_by_item[(slice_name, item_ref)]
            if refs is None or any(ref not in events for ref in refs):
                return None
            claims = _typed_authority_claims(item, observation_aliases=observation_aliases)
            if claims is None or any(
                events[ref].world_revision != revision or events[ref].payload_hash != immutable_hash
                for ref, revision, immutable_hash in claims
            ):
                return None
            bindings = tuple(
                sorted(
                    (
                        *(_binding(events[ref]) for ref in refs),
                        *(
                            (
                                ResolvedSourceBinding(
                                    source_kind="immutable_payload",
                                    authority_type="ImmutableExpressionPayload",
                                    ref=item.sidecar_ref,
                                    source_world_revision=query.world_revision,
                                    immutable_hash=item.sidecar_hash.removeprefix("sha256:"),
                                ),
                            )
                            if isinstance(item, RecentDialogueItem)
                            and item.sidecar_ref is not None
                            and item.sidecar_hash is not None
                            else ()
                        ),
                    ),
                    key=lambda value: (
                        value.source_kind,
                        value.authority_type,
                        value.ref,
                        value.source_world_revision,
                        value.immutable_hash,
                    ),
                )
            )
            candidate_refs = {binding.ref for binding in bindings}
            if len(selected_authority_refs | candidate_refs) > MAX_SOURCE_REFS_PER_ITEM:
                # ResolverProof has a fixed authority-closure bound. A long
                # conversation may leave many independently valid appraisal
                # or dialogue items; attempting to prove all of them used to
                # raise during an ordinary reply once the 33rd ref appeared.
                # Items arrive in deterministic relevance order, so retain the
                # highest-ranked whole items that fit and keep every retained
                # value fully source-bound.
                continue
            metadata.append(
                ResolvedItemMetadata(
                    item_ref=item_ref,
                    rank_score_bp=(
                        10_000
                        if isinstance(item, BiographicalWorldContextItem)
                        else max(9_900, _rank(slice_name, item, query.logical_time))
                        if (slice_name, item_ref) in rank_overrides
                        else _rank(slice_name, item, query.logical_time)
                    ),
                    privacy_class=_privacy(slice_name, item),
                    source_bindings=bindings,
                    source_hash=source_bindings_hash(bindings),
                    value_hash=canonical_value_hash(item),
                )
            )
            selected_items.append(item)
            selected_authority_refs.update(candidate_refs)
        if items and not selected_items:
            return None
        ordered = tuple(
            sorted(
                zip(selected_items, metadata, strict=True),
                key=lambda pair: (-pair[1].rank_score_bp, pair[1].item_ref),
            )
        )
        sorted_items = tuple(pair[0] for pair in ordered)
        sorted_metadata = tuple(pair[1] for pair in ordered)
        value: BaseModel | tuple[BaseModel, ...]
        if slice_name in {"character_core", "relationship_slice"}:
            if len(sorted_items) != 1:
                return None
            value = sorted_items[0]
        else:
            value = sorted_items
        return ResolvedSlice(
            world_id=query.world_id,
            snapshot_id=query.snapshot_id,
            snapshot_hash=query.snapshot_hash,
            pinned_world_revision=query.world_revision,
            value=value,
            resolver_proof=self._proof(query, slice_name, sorted_metadata, scope),
            item_metadata=sorted_metadata,
        )
