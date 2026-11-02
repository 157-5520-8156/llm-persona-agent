"""Author source-bound visual declarations from settled reviewed life.

This is the missing supply seam between the life ecology and the image
machine: committed occurrences alone are envelopes, and the production media
ecology deliberately refuses to photograph an envelope without a separate
accepted visual declaration.  The author closes that gap without becoming a
second world writer:

- it only reads occurrences that already settled through the aftermath lane;
- every visible fact it declares is copied verbatim from the reviewed
  ``visual_evidence`` annex of the opening that produced the occurrence, plus
  the settled outcome text that is already immutable sidecar content;
- whether she "bothers to keep this moment photographable" is one recorded
  uniform draw per occurrence (stable across retries), compared against a
  deterministic threshold modulated by the reviewed visual class, accepted
  Affect and the day's declaration rhythm;
- the write itself goes through the existing declaration runtimes, so source
  privacy, hash binding and idempotent identity stay owned by those seams.

The author never opens an opportunity, never renders, and never sends.  A
declared occurrence still has to survive candidate discovery, bounded
selection, Acceptance, planning and inspection downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
import logging
from types import MappingProxyType, SimpleNamespace
from typing import Literal, Mapping, Protocol

from .character_media_fact_binder import CharacterMediaCandidateRuntime
from .declared_display_contract import live_declared_display
from .image_evidence_contract import (
    CharacterBodyDetailGroundingV1,
    CharacterMediaEvidenceV1,
    ImageEvidenceV1,
    MediaSituationalContextV1,
)
from .image_evidence_runtime import (
    ImageEvidenceDeclarationCommand,
    ImageEvidenceDeclarationRuntime,
)
from .life_author_seed import (
    ReviewedLifeSeedCatalog,
    ReviewedLifeSeedOpening,
    ReviewedOpeningVisualEvidence,
)
from .life_development_draft import LifeDevelopmentVisualEvidenceDraft
from .life_development_runtime import LifeDevelopmentProposalReader
from .mood_view import active_mood_intensities
from .present_moment_candidate import inspect_present_moment
from .private_image_evidence_contract import RecipientScopedImageEvidenceV1
from .private_image_evidence_runtime import (
    RecipientScopedImageEvidenceDeclarationCommand,
    RecipientScopedImageEvidenceDeclarationRuntime,
)
from .random_authority import RandomAuthority
from .schema_core import FrozenModel


_LOG = logging.getLogger(__name__)

_DECLARATION_EVENT_TYPES = frozenset({
    "ImageEvidenceDeclared", "RecipientScopedImageEvidenceDeclared",
})
_ORDINARY_LIFE_VISIBILITIES = frozenset({"public", "shareable", "personal", "private"})
_PRIVACY_RANK = {
    "public": 0,
    "shareable": 1,
    "personal": 2,
    "private": 3,
    "withhold": 4,
}
_POSITIVE_MOODS = ("warmth", "joy")
_NEGATIVE_MOODS = ("hurt", "anger", "sadness", "loneliness", "anxiety", "resentment")
# 40 recorded buckets of 250bp keep the whole chance draw inside one stable
# RandomDrawRecorded event while still allowing mood to move the threshold.
_BUCKET_COUNT = 40
_BUCKET_WIDTH_BP = 10_000 // _BUCKET_COUNT
_PRIVATE_ELIGIBLE_STAGES = frozenset(
    {"acquaintance", "friend", "close_friend", "ambiguous", "lover"}
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class VisualEvidenceAuthorPolicy:
    """Rhythm and chance policy, versioned independently of the seed catalog.

    The policy suppresses and weights; it never invents an occurrence or a
    visible fact.  ``base_share_chance_bp`` is the per-visual-class mass an
    eligible settled occurrence has of being kept photographable at a neutral
    mood; the reviewed annex may override it per opening.
    """

    catalog_version: str = "life-visual-evidence.1"
    lookback: timedelta = timedelta(hours=12)
    # When the photo-candidate pool is empty, ecology may still look farther
    # back for one annex-backed settled moment so a role media_request is not
    # doomed to no_candidate.  This never invents a scene.
    starvation_lookback: timedelta = timedelta(days=7)
    min_gap: timedelta = timedelta(hours=2)
    max_declarations_per_day: int = 3
    max_private_declarations_per_day: int = 1
    base_share_chance_bp: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({
            "place": 4_500,
            "food": 4_000,
            "object": 3_800,
            "character": 3_500,
            "activity": 3_200,
            "social": 3_000,
            "private_transition": 2_800,
            "ambient": 2_200,
        })
    )
    # Accepted Affect modulates the threshold, not the recorded draw: a warm
    # day makes the same ticket cross the line, a heavy day holds it back,
    # and an undeclared moment may still be picked up by a later, brighter
    # wake while its lookback lasts.
    mood_positive_gain_bp: int = 4_000
    mood_negative_drop_bp: int = 5_000
    multiplier_floor_bp: int = 5_000
    multiplier_cap_bp: int = 14_000
    threshold_cap_bp: int = 9_500

    def __post_init__(self) -> None:
        if self.max_declarations_per_day < 1 or self.max_private_declarations_per_day < 0:
            raise ValueError("visual evidence policy caps are invalid")
        if self.lookback <= timedelta(0) or self.min_gap < timedelta(0):
            raise ValueError("visual evidence policy windows are invalid")
        if self.starvation_lookback < self.lookback:
            raise ValueError("visual evidence starvation lookback must cover ordinary lookback")


class VisualEvidenceAuthorResult(FrozenModel):
    status: Literal["declared", "idle", "unavailable"]
    reason_code: str
    declared_event_ref: str | None = None
    declared_source_ref: str | None = None
    lane: Literal["public", "private"] | None = None
    opened_candidate_ids: tuple[str, ...] = ()


class _ProjectionLike(Protocol):
    logical_time: datetime | None
    committed_world_event_refs: tuple[object, ...]
    plans: tuple[object, ...]
    world_occurrences: tuple[object, ...]
    affect_episodes: tuple[object, ...]
    photo_candidates: tuple[object, ...]


class LifeVisualEvidenceAuthor:
    """Turn one settled, annex-backed occurrence into one visual declaration."""

    def __init__(
        self,
        *,
        ledger,  # type: ignore[no-untyped-def]
        catalog: ReviewedLifeSeedCatalog,
        content_store,  # type: ignore[no-untyped-def]
        character_ref: str,
        recipient_ref: str | None = None,
        policy: VisualEvidenceAuthorPolicy = VisualEvidenceAuthorPolicy(),
        image_evidence: ImageEvidenceDeclarationRuntime | None = None,
        recipient_scoped: RecipientScopedImageEvidenceDeclarationRuntime | None = None,
        character_candidates: CharacterMediaCandidateRuntime | None = None,
        media_ecology=None,
        life_development_proposals=None,  # LifeDevelopmentProposalReader-compatible
        actor: str = "worker:world-v2:life-visual-evidence",
    ) -> None:
        if not character_ref or not actor:
            raise ValueError("life visual evidence author requires character and actor refs")
        self._ledger = ledger
        self._catalog = catalog
        self._content_store = content_store
        self._character_ref = character_ref
        self._recipient_ref = recipient_ref
        self._policy = policy
        self._image_evidence = image_evidence or ImageEvidenceDeclarationRuntime(
            ledger=ledger, source="world-v2:life-visual-evidence"
        )
        self._recipient_scoped = recipient_scoped or RecipientScopedImageEvidenceDeclarationRuntime(
            ledger=ledger, source="world-v2:life-visual-evidence"
        )
        self._character_candidates = character_candidates or CharacterMediaCandidateRuntime(
            ledger=ledger
        )
        self._media_ecology = media_ecology
        self._life_development_proposals = (
            life_development_proposals
            if life_development_proposals is not None
            else LifeDevelopmentProposalReader(
                ledger=ledger,
                content_store=content_store,
            )
        )
        self._random = RandomAuthority(ledger=ledger, source="world-v2:life-visual-evidence-random")
        self._actor = actor

    def advance_once(
        self, *, wake_event_ref: str, trace_id: str, correlation_id: str
    ) -> VisualEvidenceAuthorResult:
        projection: _ProjectionLike = self._ledger.project()
        logical_time = getattr(projection, "logical_time", None)
        if not isinstance(logical_time, datetime):
            return VisualEvidenceAuthorResult(
                status="unavailable", reason_code="visual_evidence.logical_time_unavailable"
            )
        prefer_private = self._has_live_declared_display(projection)
        declared_sources, recent = self._declaration_ledger_view(
            projection=projection,
            logical_time=logical_time,
            private_only=prefer_private,
        )
        daily = sum(1 for _lane, at in recent if logical_time - at <= timedelta(days=1))
        if not prefer_private and daily >= self._policy.max_declarations_per_day:
            return VisualEvidenceAuthorResult(
                status="idle", reason_code="visual_evidence.daily_budget_exhausted"
            )
        if (
            not prefer_private
            and recent
            and logical_time - max(at for _lane, at in recent) < self._policy.min_gap
        ):
            return VisualEvidenceAuthorResult(
                status="idle", reason_code="visual_evidence.min_gap_not_elapsed"
            )
        private_today = sum(
            1 for lane, at in recent
            if lane == "private" and logical_time - at <= timedelta(days=1)
        )
        mood_multiplier = self._mood_multiplier_bp(projection)
        pool_empty = (
            self._available_photo_candidate_count(
                projection, private_only=prefer_private
            )
            == 0
        )
        present = self._declare_present_moment_if_ready(
            projection=projection,
            declared_sources=declared_sources,
            logical_time=logical_time,
            private_today=private_today,
            prefer_private=prefer_private,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        if present is not None and present.status == "declared":
            return present
        eligible = self._eligible_occurrences(
            projection=projection,
            logical_time=logical_time,
            declared_sources=declared_sources,
            lookback=self._policy.lookback,
            prefer_private=prefer_private,
        )
        open_life = self._eligible_open_life_occurrences(
            projection=projection,
            logical_time=logical_time,
            declared_sources=declared_sources,
            lookback=self._policy.lookback,
        )
        starvation_eligible: tuple[
            tuple[object, ReviewedLifeSeedOpening, ReviewedOpeningVisualEvidence, str],
            ...,
        ] = ()
        starvation_open: tuple[
            tuple[object, str, LifeDevelopmentVisualEvidenceDraft], ...
        ] = ()
        if pool_empty:
            starvation_eligible = self._eligible_occurrences(
                projection=projection,
                logical_time=logical_time,
                declared_sources=declared_sources,
                lookback=self._policy.starvation_lookback,
                prefer_private=prefer_private,
            )
            starvation_open = self._eligible_open_life_occurrences(
                projection=projection,
                logical_time=logical_time,
                declared_sources=declared_sources,
                lookback=self._policy.starvation_lookback,
            )
        if not eligible and not open_life and not starvation_eligible and not starvation_open:
            return VisualEvidenceAuthorResult(
                status="idle", reason_code="visual_evidence.no_eligible_settled_occurrence"
            )
        for occurrence, activity_kind, visual in open_life:
            if prefer_private and getattr(occurrence, "visibility", None) not in {
                "personal",
                "private",
            }:
                continue
            threshold = min(
                self._policy.threshold_cap_bp,
                self._policy.base_share_chance_bp["activity"]
                * mood_multiplier
                // 10_000,
            )
            bucket = self._chance_bucket(
                occurrence=occurrence,
                logical_time=logical_time,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
            if bucket * _BUCKET_WIDTH_BP + _BUCKET_WIDTH_BP // 2 >= threshold:
                continue
            return self._declare_open_life(
                occurrence=occurrence,
                activity_kind=activity_kind,
                visual=visual,
                prefer_private=prefer_private,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
        for occurrence, opening, annex, lane in eligible:
            if prefer_private and lane != "private":
                continue
            if lane == "private":
                if (
                    (not prefer_private and private_today >= self._policy.max_private_declarations_per_day)
                    or not self._recipient_relationship_ready(projection)
                ):
                    continue
            threshold = self._threshold_bp(
                opening=opening, annex=annex, mood_multiplier_bp=mood_multiplier
            )
            bucket = self._chance_bucket(occurrence=occurrence, logical_time=logical_time,
                                         trace_id=trace_id, correlation_id=correlation_id)
            if bucket * _BUCKET_WIDTH_BP + _BUCKET_WIDTH_BP // 2 >= threshold:
                continue
            return self._declare(
                occurrence=occurrence, opening=opening, annex=annex, lane=lane,
                trace_id=trace_id, correlation_id=correlation_id,
            )
        if pool_empty:
            # Empty pool is the starvation condition.  Lottery still ran and
            # remains on the ledger; fill may now use the same recent
            # eligibles the lottery just missed.  Excluding them left a
            # settled life with no photographable candidate until the 12h
            # ordinary window aged out — and a stable miss never retries.
            fill = self._declare_first_eligible(
                open_life=starvation_open or open_life,
                eligible=starvation_eligible or eligible,
                private_today=private_today,
                projection=projection,
                prefer_private=prefer_private,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
            if fill is not None:
                return fill.model_copy(
                    update={"reason_code": "visual_evidence.starvation_fill_declared"}
                )
        _LOG.warning(
            "visual evidence selected nothing wake=%s pool_empty=%s "
            "ordinary_open=%s ordinary_catalog=%s starvation_open=%s "
            "starvation_catalog=%s",
            wake_event_ref,
            pool_empty,
            len(open_life),
            len(eligible),
            len(starvation_open),
            len(starvation_eligible),
        )
        return VisualEvidenceAuthorResult(
            status="idle", reason_code="visual_evidence.nothing_selected"
        )

    def request_once(
        self,
        *,
        source_refs: tuple[str, ...],
        trace_id: str,
        correlation_id: str,
    ) -> VisualEvidenceAuthorResult:
        """Compile one role-requested candidate from exact attended life evidence.

        The ordinary ecology uses a recorded chance draw to decide whether an
        already-settled moment becomes photographable.  An accepted
        CharacterInterior media request is itself the missing selection
        authority, so this entry point does not draw again.  It still cannot
        invent a scene or a camera: the selected source must be one of the
        exact refs attended by the role and must carry an already-reviewed
        visual annex with a self-capture capability.  Daily rhythm, privacy,
        relationship and source-closure boundaries remain unchanged.
        """

        requested = tuple(dict.fromkeys(source_refs))
        if not requested:
            # Role chose consider_available_candidate without naming a source.
            # Compile the active present moment if photographable, or at most
            # one eligible settled moment so selection is not an empty-pool
            # instant failure.  Still cannot invent a scene.
            return self._starvation_fill_for_role_request(
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
        projection: _ProjectionLike = self._ledger.project()
        prefer_private = self._has_live_declared_display(projection)
        requested_set = frozenset(requested)
        active_plan_requested = any(
            getattr(plan, "status", None) == "active"
            and bool(self._active_plan_aliases(plan) & requested_set)
            for plan in getattr(projection, "plans", ()) or ()
        )
        if active_plan_requested:
            present = self._declare_present_moment_if_ready(
                projection=projection,
                declared_sources=frozenset(),
                logical_time=getattr(projection, "logical_time", None),
                private_today=0,
                prefer_private=prefer_private,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
            if present is not None and present.status == "declared":
                return present
        projection = self._ledger.project()
        logical_time = getattr(projection, "logical_time", None)
        if not isinstance(logical_time, datetime):
            return VisualEvidenceAuthorResult(
                status="unavailable", reason_code="visual_evidence.logical_time_unavailable"
            )
        declared_sources, recent = self._declaration_ledger_view(
            projection=projection,
            logical_time=logical_time,
            private_only=prefer_private,
        )
        eligible = self._eligible_occurrences(
            projection=projection,
            logical_time=logical_time,
            declared_sources=declared_sources,
            prefer_private=prefer_private,
        )
        for occurrence, opening, annex, lane in eligible:
            attended_aliases = self._requested_source_aliases(
                projection=projection,
                occurrence=occurrence,
            )
            if not requested_set.intersection(attended_aliases) or not annex.self_capture:
                continue
            if lane == "private" and not self._recipient_relationship_ready(projection):
                continue
            result = self._declare(
                occurrence=occurrence,
                opening=opening,
                annex=annex,
                lane=lane,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
            if result.opened_candidate_ids:
                return result.model_copy(
                    update={"reason_code": "visual_evidence.role_requested_candidate_declared"}
                )
        open_life = self._eligible_open_life_occurrences(
            projection=projection,
            logical_time=logical_time,
            declared_sources=declared_sources,
            lookback=self._policy.starvation_lookback,
        )
        for occurrence, activity_kind, visual in open_life:
            attended_aliases = self._requested_source_aliases(
                projection=projection,
                occurrence=occurrence,
            )
            if not requested_set.intersection(attended_aliases):
                continue
            if self._open_life_character_media(occurrence, activity_kind=activity_kind) is None:
                continue
            result = self._declare_open_life(
                occurrence=occurrence,
                activity_kind=activity_kind,
                visual=visual,
                prefer_private=prefer_private,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
            if result.opened_candidate_ids:
                return result.model_copy(
                    update={"reason_code": "visual_evidence.role_requested_candidate_declared"}
                )
        if prefer_private:
            return self._starvation_fill_for_role_request(
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
        return VisualEvidenceAuthorResult(
            status="idle",
            reason_code="visual_evidence.no_requested_capture_source",
        )

    @staticmethod
    def _requested_source_aliases(
        *, projection: _ProjectionLike, occurrence: object
    ) -> frozenset[str]:
        """Resolve only ledger-proven aliases for one settled lived moment.

        Chat snapshots commonly present the committed Experience authority and
        its LifeContent descriptor rather than the underlying settlement event.
        Those are not guesses: the Experience carries a typed
        ``occurrence_settlement`` binding, and each descriptor is pinned to the
        same occurrence or Experience.  No free text participates in this
        mapping.
        """

        occurrence_id = getattr(occurrence, "occurrence_id", None)
        settlement_ref = getattr(occurrence, "settlement_event_ref", None)
        aliases = {settlement_ref} if isinstance(settlement_ref, str) else set()
        experience_ids: set[str] = set()
        for experience in getattr(projection, "experiences", ()):
            bindings = getattr(getattr(experience, "values", None), "source_bindings", ())
            if not any(
                getattr(binding, "source_kind", None) == "occurrence_settlement"
                and getattr(binding, "occurrence_id", None) == occurrence_id
                and getattr(binding, "authority_event_ref", None) == settlement_ref
                for binding in bindings
            ):
                continue
            experience_id = getattr(experience, "experience_id", None)
            accepted_ref = getattr(getattr(experience, "origin", None), "accepted_event_ref", None)
            if isinstance(experience_id, str):
                experience_ids.add(experience_id)
            if isinstance(accepted_ref, str):
                aliases.add(accepted_ref)
        for descriptor in getattr(projection, "life_content_descriptors", ()):
            source_entity_id = getattr(descriptor, "source_entity_id", None)
            if source_entity_id != occurrence_id and source_entity_id not in experience_ids:
                continue
            descriptor_ref = getattr(descriptor, "descriptor_event_ref", None)
            if isinstance(descriptor_ref, str):
                aliases.add(descriptor_ref)
        if isinstance(settlement_ref, str):
            for candidate in getattr(projection, "photo_candidates", ()) or ():
                candidate_sources = set(getattr(candidate, "source_event_refs", ()) or ())
                candidate_sources.update(
                    getattr(src, "event_ref", None)
                    for src in getattr(candidate, "source_events", ()) or ()
                )
                if settlement_ref not in candidate_sources:
                    continue
                opened_ref = getattr(candidate, "opened_event_ref", None)
                if isinstance(opened_ref, str) and opened_ref:
                    aliases.add(opened_ref)
                for ref in candidate_sources:
                    if isinstance(ref, str) and ref:
                        aliases.add(ref)
        return frozenset(aliases)

    # -- discovery -------------------------------------------------------

    def _declare_present_moment_if_ready(
        self,
        *,
        projection: _ProjectionLike,
        declared_sources: frozenset[str],
        logical_time: datetime | None,
        private_today: int,
        prefer_private: bool = False,
        trace_id: str,
        correlation_id: str,
    ) -> VisualEvidenceAuthorResult | None:
        """Open one annex-backed active plan.  Does not invent a scene."""

        if not isinstance(logical_time, datetime):
            return None
        if not declared_sources:
            declared_sources, _recent = self._declaration_ledger_view(
                projection=projection,
                logical_time=logical_time,
                private_only=prefer_private,
            )
        fact = inspect_present_moment(
            projection=projection,
            catalog=self._catalog,
            logical_time=logical_time,
            declared_sources=declared_sources,
        )
        if fact.reason == "already_open" and (
            not prefer_private
            or not fact.source_ref
            or fact.source_ref in declared_sources
        ):
            return VisualEvidenceAuthorResult(
                status="idle",
                reason_code="visual_evidence.present_moment_already_open",
                declared_source_ref=fact.source_ref,
            )
        if (
            not fact.photographable
            or fact.reason not in {"active", "already_open"}
            or not fact.source_ref
        ):
            return None
        open_life = self._declare_open_life_present_moment(
            projection=projection,
            source_ref=fact.source_ref,
            prefer_private=prefer_private,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        if open_life is not None:
            return open_life
        row = self._active_plan_declaration_row(
            projection=projection,
            source_ref=fact.source_ref,
            prefer_private=prefer_private,
        )
        if row is None:
            return None
        occurrence, opening, annex, lane = row
        if lane == "private" and (
            (not prefer_private and private_today >= self._policy.max_private_declarations_per_day)
            or not self._recipient_relationship_ready(projection)
        ):
            return None
        result = self._declare(
            occurrence=occurrence,
            opening=opening,
            annex=annex,
            lane=lane,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        return result.model_copy(
            update={"reason_code": "visual_evidence.present_moment_declared"}
        )

    @staticmethod
    def _active_plan_aliases(plan: object) -> frozenset[str]:
        aliases: set[str] = set()
        origin = getattr(plan, "authority_origin", None)
        accepted_ref = getattr(origin, "accepted_event_ref", None)
        if isinstance(accepted_ref, str) and accepted_ref:
            aliases.add(accepted_ref)
        plan_id = getattr(plan, "plan_id", None)
        if isinstance(plan_id, str) and plan_id:
            aliases.add(plan_id)
            if plan_id.startswith("plan:life-development:"):
                aliases.add(
                    "event:life-development:plan:"
                    + plan_id.removeprefix("plan:life-development:")
                )
            elif plan_id.startswith("plan:"):
                aliases.add("event:" + plan_id.removeprefix("plan:"))
        for item in getattr(plan, "evidence_refs", ()) or ():
            ref_id = getattr(item, "ref_id", None)
            if isinstance(ref_id, str) and ref_id:
                aliases.add(ref_id)
        return frozenset(aliases)

    def _self_directed_plan_intention(self, *, plan_id: object) -> str | None:
        if not isinstance(plan_id, str) or not plan_id:
            return None
        reader = getattr(self._life_development_proposals, "read_for_plan", None)
        if callable(reader):
            try:
                material = reader(plan_id=plan_id)
            except (TypeError, ValueError):
                material = None
            intention = getattr(material, "character_intention", None)
            if isinstance(intention, str) and intention.strip():
                return " ".join(intention.split()).strip()
        try:
            from .world_plan_management import reader_for

            composite = reader_for(
                self._ledger, self._character_ref, content_store=self._content_store
            )
            material = composite.read_for_plan(plan_id=plan_id)
        except Exception:
            material = None
        intention = getattr(material, "character_intention", None)
        if isinstance(intention, str) and intention.strip():
            return " ".join(intention.split()).strip()
        return None

    def _declare_open_life_present_moment(
        self,
        *,
        projection: _ProjectionLike,
        source_ref: str,
        prefer_private: bool = False,
        trace_id: str,
        correlation_id: str,
    ) -> VisualEvidenceAuthorResult | None:
        """Declare an in-progress open-life or self-directed plan from its verified material."""

        for plan in getattr(projection, "plans", ()) or ():
            origin = getattr(plan, "authority_origin", None)
            if getattr(origin, "accepted_event_ref", None) != source_ref:
                continue
            kind = getattr(plan, "activity_kind", None)
            if not isinstance(kind, str) or not kind.startswith(
                ("open_life.", "self_directed.")
            ):
                return None
            visual: LifeDevelopmentVisualEvidenceDraft | None = None
            if kind.startswith("open_life."):
                reader = getattr(self._life_development_proposals, "read_for_plan", None)
                if not callable(reader):
                    return None
                try:
                    material = reader(plan_id=getattr(plan, "plan_id"))
                except ValueError:
                    return None
                if material is None:
                    return None
                visual = next(
                    (
                        item.visual_evidence
                        for item in getattr(material, "outcomes", ()) or ()
                        if getattr(item, "visual_evidence", None) is not None
                    ),
                    None,
                )
            else:
                intention = self._self_directed_plan_intention(
                    plan_id=getattr(plan, "plan_id", None)
                )
                if intention:
                    visual = LifeDevelopmentVisualEvidenceDraft(
                        claim_refs=("local:claim:self-directed-activity",),
                        activity_description=intention[:240],
                    )
            if visual is None:
                return None
            occurrence = SimpleNamespace(
                settlement_event_ref=source_ref,
                trigger_ref=getattr(plan, "plan_id", kind),
                visibility=getattr(plan, "privacy_class", None) or "shareable",
                settled_at=getattr(plan, "last_transitioned_at", None)
                or getattr(projection, "logical_time", None),
                result_payload_ref=None,
                result_payload_hash=None,
                participant_refs=tuple(
                    dict.fromkeys(
                        (
                            self._character_ref,
                            *(getattr(plan, "participant_refs", ()) or ()),
                        )
                    )
                ),
            )
            result = self._declare_open_life(
                occurrence=occurrence,
                activity_kind=kind,
                visual=visual,
                prefer_private=prefer_private,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
            return result.model_copy(
                update={"reason_code": "visual_evidence.present_moment_declared"}
            )
        return None

    def _active_plan_declaration_row(
        self,
        *,
        projection: _ProjectionLike,
        source_ref: str,
        prefer_private: bool = False,
    ) -> tuple[object, ReviewedLifeSeedOpening, object, str] | None:
        for plan in getattr(projection, "plans", ()) or ():
            origin = getattr(plan, "authority_origin", None)
            if getattr(origin, "accepted_event_ref", None) != source_ref:
                continue
            if getattr(plan, "status", None) != "active":
                continue
            kind = getattr(plan, "activity_kind", None)
            opening = self._catalog.opening_for_activity(kind) if isinstance(kind, str) else None
            annex = getattr(opening, "visual_evidence", None) if opening is not None else None
            if opening is None or annex is None:
                return None
            visibility = getattr(plan, "privacy_class", None) or opening.privacy
            if visibility not in _ORDINARY_LIFE_VISIBILITIES and visibility != "private":
                return None
            lane = (
                "private"
                if (
                    opening.visual_potential == "private_transition"
                    or (
                        prefer_private
                        and visibility in {"personal", "private"}
                        and self._recipient_ref is not None
                        and bool(annex.self_capture)
                    )
                )
                else "public"
            )
            if lane == "public" and visibility not in _ORDINARY_LIFE_VISIBILITIES:
                return None
            occurrence = SimpleNamespace(
                settlement_event_ref=source_ref,
                trigger_ref=getattr(plan, "plan_id", kind),
                visibility=(
                    "private"
                    if lane == "private"
                    else visibility
                    if visibility in _ORDINARY_LIFE_VISIBILITIES
                    else "private"
                ),
                settled_at=getattr(plan, "last_transitioned_at", None)
                or getattr(projection, "logical_time", None),
                result_payload_ref=None,
                result_payload_hash=None,
                participant_refs=getattr(plan, "participant_refs", ()) or (),
            )
            return occurrence, opening, annex, lane
        return None

    @staticmethod
    def _available_photo_candidate_count(
        projection: _ProjectionLike, *, private_only: bool = False
    ) -> int:
        logical_time = getattr(projection, "logical_time", None)
        declined = {
            (getattr(item, "candidate_id", None), getattr(item, "entity_revision", None))
            for item in getattr(projection, "media_declined_candidate_revisions", ()) or ()
        }
        count = 0
        for item in getattr(projection, "photo_candidates", ()) or ():
            if getattr(item, "status", None) != "available":
                continue
            if (getattr(item, "candidate_id", None), getattr(item, "entity_revision", None)) in declined:
                continue
            expires_at = getattr(item, "expires_at", None)
            if (
                isinstance(logical_time, datetime)
                and isinstance(expires_at, datetime)
                and expires_at <= logical_time
            ):
                continue
            if private_only and not any(
                "recipient-scoped-image-evidence-declared"
                in str(getattr(src, "event_ref", ""))
                for src in getattr(item, "source_events", ()) or ()
            ):
                continue
            count += 1
        return count

    @staticmethod
    def _newest_available_photo_candidate_time(
        projection: _ProjectionLike, *, private_only: bool = False
    ) -> datetime | None:
        logical_time = getattr(projection, "logical_time", None)
        declined = {
            (getattr(item, "candidate_id", None), getattr(item, "entity_revision", None))
            for item in getattr(projection, "media_declined_candidate_revisions", ()) or ()
        }
        newest: datetime | None = None
        for item in getattr(projection, "photo_candidates", ()) or ():
            if getattr(item, "status", None) != "available":
                continue
            if (getattr(item, "candidate_id", None), getattr(item, "entity_revision", None)) in declined:
                continue
            expires_at = getattr(item, "expires_at", None)
            if (
                isinstance(logical_time, datetime)
                and isinstance(expires_at, datetime)
                and expires_at <= logical_time
            ):
                continue
            if private_only and not any(
                "recipient-scoped-image-evidence-declared"
                in str(getattr(src, "event_ref", ""))
                for src in getattr(item, "source_events", ()) or ()
            ):
                continue
            candidate_at = getattr(item, "ecology_observed_at", None) or getattr(
                item, "opened_at", None
            )
            if isinstance(candidate_at, datetime) and (
                newest is None or candidate_at > newest
            ):
                newest = candidate_at
        return newest

    def _declare_first_eligible(
        self,
        *,
        open_life: tuple[tuple[object, str, LifeDevelopmentVisualEvidenceDraft], ...],
        eligible: tuple[
            tuple[object, ReviewedLifeSeedOpening, ReviewedOpeningVisualEvidence, str],
            ...,
        ],
        private_today: int,
        projection: _ProjectionLike,
        prefer_private: bool = False,
        trace_id: str,
        correlation_id: str,
    ) -> VisualEvidenceAuthorResult | None:
        if prefer_private and self._recipient_relationship_ready(projection):
            private_catalog = next(
                (row for row in eligible if row[3] == "private"),
                None,
            )
            private_open_life = next(
                (
                    row
                    for row in open_life
                    if getattr(row[0], "visibility", None) in {"personal", "private"}
                    and self._open_life_character_media(row[0], activity_kind=row[1]) is not None
                ),
                None,
            )
            if private_open_life is not None and (
                private_catalog is None
                or getattr(private_open_life[0], "settled_at")
                >= getattr(private_catalog[0], "settled_at")
            ):
                occurrence, activity_kind, visual = private_open_life
                return self._declare_open_life(
                    occurrence=occurrence,
                    activity_kind=activity_kind,
                    visual=visual,
                    prefer_private=prefer_private,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                )
            if private_catalog is not None:
                occurrence, opening, annex, lane = private_catalog
                return self._declare(
                    occurrence=occurrence,
                    opening=opening,
                    annex=annex,
                    lane=lane,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                )
        for occurrence, activity_kind, visual in open_life:
            return self._declare_open_life(
                occurrence=occurrence,
                activity_kind=activity_kind,
                visual=visual,
                prefer_private=prefer_private,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
        for occurrence, opening, annex, lane in eligible:
            if lane == "private":
                if (
                    (not prefer_private and private_today >= self._policy.max_private_declarations_per_day)
                    or not self._recipient_relationship_ready(projection)
                ):
                    continue
            return self._declare(
                occurrence=occurrence,
                opening=opening,
                annex=annex,
                lane=lane,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
        return None

    def _starvation_fill_for_role_request(
        self,
        *,
        trace_id: str,
        correlation_id: str,
    ) -> VisualEvidenceAuthorResult:
        projection: _ProjectionLike = self._ledger.project()
        logical_time = getattr(projection, "logical_time", None)
        if not isinstance(logical_time, datetime):
            return VisualEvidenceAuthorResult(
                status="unavailable", reason_code="visual_evidence.logical_time_unavailable"
            )
        prefer_private = self._has_live_declared_display(projection)
        has_available = (
            self._available_photo_candidate_count(
                projection, private_only=prefer_private
            )
            > 0
        )
        newest_available_at = self._newest_available_photo_candidate_time(
            projection, private_only=prefer_private
        )
        present = self._declare_present_moment_if_ready(
            projection=projection,
            declared_sources=frozenset(),
            logical_time=logical_time,
            private_today=0,
            prefer_private=prefer_private,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        if present is not None and present.status == "declared":
            return present
        declared_sources, recent = self._declaration_ledger_view(
            projection=projection,
            logical_time=logical_time,
            private_only=prefer_private,
        )
        private_today = sum(
            1
            for lane, at in recent
            if lane == "private" and logical_time - at <= timedelta(days=1)
        )
        eligible = self._eligible_occurrences(
            projection=projection,
            logical_time=logical_time,
            declared_sources=declared_sources,
            lookback=self._policy.starvation_lookback,
            prefer_private=prefer_private,
        )
        open_life = self._eligible_open_life_occurrences(
            projection=projection,
            logical_time=logical_time,
            declared_sources=declared_sources,
            lookback=self._policy.starvation_lookback,
        )
        if has_available:
            if newest_available_at is None:
                return VisualEvidenceAuthorResult(
                    status="idle", reason_code="visual_evidence.candidates_already_available"
                )
            eligible = tuple(
                row
                for row in eligible
                if isinstance(getattr(row[0], "settled_at", None), datetime)
                and getattr(row[0], "settled_at") > newest_available_at
            )
            open_life = tuple(
                row
                for row in open_life
                if isinstance(getattr(row[0], "settled_at", None), datetime)
                and getattr(row[0], "settled_at") > newest_available_at
            )
            if not eligible and not open_life:
                return VisualEvidenceAuthorResult(
                    status="idle", reason_code="visual_evidence.candidates_already_available"
                )
        fill = self._declare_first_eligible(
            open_life=open_life,
            eligible=eligible,
            private_today=private_today,
            projection=projection,
            prefer_private=prefer_private,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        if fill is None:
            return VisualEvidenceAuthorResult(
                status="idle",
                reason_code=(
                    "visual_evidence.candidates_already_available"
                    if has_available
                    else "visual_evidence.no_requested_capture_source"
                ),
            )
        return fill.model_copy(
            update={"reason_code": "visual_evidence.role_requested_starvation_fill"}
        )

    def _eligible_occurrences(
        self, *, projection: _ProjectionLike, logical_time: datetime,
        declared_sources: frozenset[str],
        lookback: timedelta | None = None,
        prefer_private: bool = False,
    ) -> tuple[tuple[object, ReviewedLifeSeedOpening, ReviewedOpeningVisualEvidence, str], ...]:
        window = self._policy.lookback if lookback is None else lookback
        plans = {
            plan_id: item
            for item in getattr(projection, "plans", ())
            if (plan_id := getattr(item, "plan_id", None))
        }
        rows: list[tuple[object, ReviewedLifeSeedOpening, ReviewedOpeningVisualEvidence, str]] = []
        for occurrence in getattr(projection, "world_occurrences", ()):
            settled_at = getattr(occurrence, "settled_at", None)
            settlement_ref = getattr(occurrence, "settlement_event_ref", None)
            if (
                getattr(occurrence, "status", None) != "settled"
                or not isinstance(settled_at, datetime)
                or settlement_ref is None
                or settlement_ref in declared_sources
                or settled_at > logical_time
                or logical_time - settled_at > window
            ):
                continue
            plan = plans.get(getattr(occurrence, "trigger_ref", None))
            activity_kind = getattr(plan, "activity_kind", None)
            if not isinstance(activity_kind, str):
                continue
            opening = self._catalog.opening_for_activity(activity_kind)
            if opening is None or opening.visual_evidence is None:
                continue
            annex = opening.visual_evidence
            visibility = getattr(occurrence, "visibility", None)
            if (
                prefer_private
                and visibility in {"personal", "private"}
                and self._recipient_ref is not None
                and annex.self_capture
            ):
                rows.append((occurrence, opening, annex, "private"))
            elif (
                visibility in _ORDINARY_LIFE_VISIBILITIES
                and opening.visual_potential not in {"none", "private_transition"}
            ):
                rows.append((occurrence, opening, annex, "public"))
            elif (
                visibility in {"personal", "private"}
                and opening.visual_potential == "private_transition"
                and self._recipient_ref is not None
                and annex.self_capture
            ):
                rows.append((occurrence, opening, annex, "private"))
        rows.sort(key=lambda row: (getattr(row[0], "settled_at"), getattr(row[0], "occurrence_id", "")), reverse=True)
        return tuple(rows)

    def _eligible_open_life_occurrences(
        self,
        *,
        projection: _ProjectionLike,
        logical_time: datetime,
        declared_sources: frozenset[str],
        lookback: timedelta | None = None,
    ) -> tuple[tuple[object, str, LifeDevelopmentVisualEvidenceDraft], ...]:
        window = self._policy.lookback if lookback is None else lookback
        plans = {
            plan_id: item
            for item in getattr(projection, "plans", ())
            if (plan_id := getattr(item, "plan_id", None))
        }
        rows: list[tuple[object, str, LifeDevelopmentVisualEvidenceDraft]] = []
        for occurrence in getattr(projection, "world_occurrences", ()):
            settled_at = getattr(occurrence, "settled_at", None)
            settlement_ref = getattr(occurrence, "settlement_event_ref", None)
            if (
                getattr(occurrence, "status", None) != "settled"
                or getattr(occurrence, "visibility", None) not in _ORDINARY_LIFE_VISIBILITIES
                or not isinstance(settled_at, datetime)
                or settlement_ref is None
                or settlement_ref in declared_sources
                or settled_at > logical_time
                or logical_time - settled_at > window
            ):
                continue
            plan = plans.get(getattr(occurrence, "trigger_ref", None))
            activity_kind = getattr(plan, "activity_kind", None)
            plan_id = getattr(plan, "plan_id", None)
            material = None
            if (
                isinstance(activity_kind, str)
                and activity_kind.startswith("open_life.")
                and isinstance(plan_id, str)
            ):
                try:
                    material = self._life_development_proposals.read_for_plan(
                        plan_id=plan_id
                    )
                except ValueError:
                    _LOG.warning(
                        "open Life visual evidence authority is unavailable plan=%s",
                        plan_id,
                    )
                    material = None
            if material is None:
                read_occurrence = getattr(
                    self._life_development_proposals, "read_for_occurrence", None
                )
                if callable(read_occurrence):
                    try:
                        material = read_occurrence(occurrence=occurrence)
                    except (TypeError, ValueError):
                        _LOG.warning(
                            "open Life visual evidence authority is unavailable occurrence=%s",
                            getattr(occurrence, "occurrence_id", None),
                        )
                        material = None
                if material is not None:
                    activity_kind = getattr(material, "activity_kind", None)
                    if not isinstance(activity_kind, str) or not activity_kind.startswith(
                        "open_life."
                    ):
                        activity_kind = "open_life.world_occurrence"
            selected = None
            if material is not None:
                selected_ref = getattr(occurrence, "settled_outcome_ref", None)
                selected = next(
                    (
                        item
                        for item in material.outcomes
                        if item.descriptor.candidate_result_ref == selected_ref
                    ),
                    None,
                )
            if selected is not None and selected.visual_evidence is not None:
                rows.append((occurrence, activity_kind, selected.visual_evidence))
                continue
            consequence = self._world_consequence_visual_draft(
                occurrence=occurrence, plans=plans
            )
            if consequence is not None:
                rows.append((occurrence, consequence[0], consequence[1]))
        rows.sort(
            key=lambda row: (
                getattr(row[0], "settled_at"),
                getattr(row[0], "occurrence_id", ""),
            ),
            reverse=True,
        )
        return tuple(rows)

    def _declaration_ledger_view(
        self,
        *,
        projection: _ProjectionLike,
        logical_time: datetime,
        private_only: bool = False,
    ) -> tuple[frozenset[str], tuple[tuple[str, datetime], ...]]:
        """Collect already-declared source refs and recent declaration beats."""

        lookup = getattr(self._ledger, "lookup_event_commit", None)
        declared: set[str] = set()
        recent: list[tuple[str, datetime]] = []
        for ref in getattr(projection, "committed_world_event_refs", ()):
            event_type = getattr(ref, "event_type", None)
            if event_type not in _DECLARATION_EVENT_TYPES:
                continue
            at = getattr(ref, "logical_time", None)
            if isinstance(at, datetime) and at <= logical_time:
                lane = (
                    "private"
                    if event_type == "RecipientScopedImageEvidenceDeclared"
                    else "public"
                )
                recent.append((lane, at))
            if private_only and event_type != "RecipientScopedImageEvidenceDeclared":
                continue
            if not callable(lookup):
                continue
            located = lookup(ref.event_id)
            if located is None:
                continue
            event, _commit = located
            try:
                payload = event.payload()
            except (AttributeError, ValueError):
                continue
            source_ref = payload.get("source_event_ref")
            if isinstance(source_ref, str):
                declared.add(source_ref)
        return frozenset(declared), tuple(recent)

    # -- controlled chance -------------------------------------------------

    def _mood_multiplier_bp(self, projection: _ProjectionLike) -> int:
        intensities = active_mood_intensities(getattr(projection, "affect_episodes", ()))
        positive = max((intensities.get(name, 0) for name in _POSITIVE_MOODS), default=0)
        negative = max((intensities.get(name, 0) for name in _NEGATIVE_MOODS), default=0)
        multiplier = (
            10_000
            + positive * self._policy.mood_positive_gain_bp // 10_000
            - negative * self._policy.mood_negative_drop_bp // 10_000
        )
        return max(self._policy.multiplier_floor_bp, min(self._policy.multiplier_cap_bp, multiplier))

    def _threshold_bp(
        self, *, opening: ReviewedLifeSeedOpening,
        annex: ReviewedOpeningVisualEvidence, mood_multiplier_bp: int,
    ) -> int:
        base = (
            annex.share_chance_bp
            if annex.share_chance_bp is not None
            else self._policy.base_share_chance_bp.get(opening.visual_potential, 2_500)
        )
        return min(self._policy.threshold_cap_bp, base * mood_multiplier_bp // 10_000)

    def _chance_bucket(
        self, *, occurrence, logical_time: datetime, trace_id: str, correlation_id: str,
    ) -> int:
        """One stable recorded uniform ticket per settled occurrence."""

        settlement_ref = getattr(occurrence, "settlement_event_ref")
        buckets = tuple(f"chance-bucket:{index:02d}" for index in range(_BUCKET_COUNT))
        draw = self._random.draw(
            attempt_id="attempt:visual-evidence:" + _digest([self._ledger.world_id, settlement_ref]),
            candidate_refs=buckets,
            catalog_version=self._policy.catalog_version,
            logical_time=logical_time,
            seed_instant=getattr(occurrence, "settled_at"),
            actor=self._actor,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        return int(draw.selected_candidate_ref.rsplit(":", 1)[1])

    # -- declaration -------------------------------------------------------

    def _declare(
        self, *, occurrence, opening: ReviewedLifeSeedOpening,
        annex: ReviewedOpeningVisualEvidence, lane: str, trace_id: str, correlation_id: str,
    ) -> VisualEvidenceAuthorResult:
        settlement_ref = getattr(occurrence, "settlement_event_ref")
        summary = self._settled_summary(occurrence)
        location = self._location_facts(annex)
        environment = self._environment_facts(annex)
        situational_context = self._situational_context(
            projection=self._ledger.project(),
            logical_time=getattr(occurrence, "settled_at"),
            privacy_ceiling=getattr(occurrence, "visibility"),
        )
        objects = tuple(
            {
                key: value
                for key, value in {
                    "id": item.id, "kind": item.kind, "description": item.description,
                }.items()
                if value is not None
            }
            for item in annex.objects
        )
        activity: dict[str, object] = {
            "id": getattr(occurrence, "trigger_ref", None) or opening.activity_kind,
            "kind": opening.activity_kind,
            "description": annex.activity_description,
        }
        character_media = (
            CharacterMediaEvidenceV1(
                character_ref=self._character_ref,
                present=True,
                capture_capabilities=annex.self_capture,
            )
            if annex.self_capture
            else None
        )
        projection = self._ledger.project()
        logical_time = projection.logical_time
        command_id = "visual-evidence:" + _digest([self._ledger.world_id, settlement_ref, lane])
        if lane == "private":
            activity["private_transition"] = True
            evidence = RecipientScopedImageEvidenceV1(
                visibility="private",
                summary=summary,
                activity=activity,
                location=location,
                environment=environment,
                situational_context=situational_context,
                character_media=character_media,
            )
            assert self._recipient_ref is not None
            commit = self._recipient_scoped.declare(
                RecipientScopedImageEvidenceDeclarationCommand(
                    command_id=command_id,
                    source_event_ref=settlement_ref,
                    recipient_ref=self._recipient_ref,
                    image_evidence=evidence,
                ),
                logical_time=logical_time, created_at=logical_time, actor=self._actor,
                trace_id=trace_id, correlation_id=correlation_id,
            )
        else:
            evidence = ImageEvidenceV1(
                visibility=getattr(occurrence, "visibility"),
                summary=summary,
                activity=activity,
                location=location,
                environment=environment,
                situational_context=situational_context,
                objects=objects,
                character_media=character_media,
            )
            commit = self._image_evidence.declare(
                ImageEvidenceDeclarationCommand(
                    command_id=command_id,
                    source_event_ref=settlement_ref,
                    image_evidence=evidence,
                ),
                logical_time=logical_time, created_at=logical_time, actor=self._actor,
                trace_id=trace_id, correlation_id=correlation_id,
            )
        declared_ref = next(iter(getattr(commit, "event_ids", ())), None)
        opened: tuple[str, ...] = ()
        if declared_ref is not None and character_media is not None:
            try:
                opened = self._character_candidates.open_once(
                    wake_event_ref=declared_ref,
                    logical_time=logical_time,
                    actor=self._actor,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                )
            except ValueError:
                # The declaration itself is durable; candidate opening can be
                # retried by any later declaration-aware pass without losing
                # or duplicating the declared evidence.
                _LOG.warning("character media candidates could not open for %s", declared_ref)
        if declared_ref is not None and lane == "public" and self._media_ecology is not None:
            try:
                ecology_res = self._media_ecology.drain_once(
                    wake_event_ref=declared_ref,
                    logical_time=logical_time,
                    actor=self._actor,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                )
                if ecology_res.candidate_ids:
                    opened = tuple(dict.fromkeys((*opened, *ecology_res.candidate_ids)))
            except Exception:
                _LOG.warning("ecology media candidates could not open for %s", declared_ref)
        return VisualEvidenceAuthorResult(
            status="declared",
            reason_code="visual_evidence.declared",
            declared_event_ref=declared_ref,
            declared_source_ref=settlement_ref,
            lane=lane,  # type: ignore[arg-type]
            opened_candidate_ids=opened,
        )

    def _declare_open_life(
        self,
        *,
        occurrence,
        activity_kind: str,
        visual: LifeDevelopmentVisualEvidenceDraft,
        prefer_private: bool = False,
        trace_id: str,
        correlation_id: str,
    ) -> VisualEvidenceAuthorResult:
        """Declare only the World Author's accepted, claim-closed visual bytes."""

        settlement_ref = getattr(occurrence, "settlement_event_ref")
        projection = self._ledger.project()
        logical_time = projection.logical_time
        location = (
            {
                ("id" if key == "location_ref" else key): value
                for key, value in visual.location.model_dump(
                    mode="json", exclude_none=True
                ).items()
            }
            if visual.location is not None
            else None
        )
        environment = (
            visual.environment.model_dump(mode="json", exclude_none=True)
            if visual.environment is not None
            else None
        )
        objects = tuple(
            {
                ("id" if key == "local_ref" else key): value
                for key, value in item.model_dump(mode="json").items()
            }
            for item in visual.objects
        )
        character_media = self._open_life_character_media(
            occurrence,
            activity_kind=activity_kind,
            include_mirror=bool(isinstance(location, dict) and location.get("mirror_available")),
            objects=objects,
        )
        visibility = getattr(occurrence, "visibility")
        situational_context = self._situational_context(
            projection=projection,
            logical_time=getattr(occurrence, "settled_at"),
            privacy_ceiling=visibility,
        )
        if (
            prefer_private
            and visibility in {"personal", "private"}
            and self._recipient_ref is not None
            and character_media is not None
            and self._recipient_relationship_ready(projection)
        ):
            private_location: dict[str, object] = (
                dict(location)
                if isinstance(location, dict) and location
                else {
                    "id": "location:private_room",
                    "kind": "private",
                    "publicness": "private",
                    "mirror_available": True,
                }
            )
            private_location.setdefault("kind", "private")
            private_location.setdefault("publicness", "private")
            private_character_media = (
                self._open_life_character_media(
                    occurrence,
                    activity_kind=activity_kind,
                    include_mirror=bool(private_location.get("mirror_available")),
                )
                or character_media
            )
            activity: dict[str, object] = {
                "id": getattr(occurrence, "trigger_ref", activity_kind) or activity_kind,
                "kind": activity_kind,
                "description": visual.activity_description or activity_kind,
                "private_transition": True,
            }
            evidence = RecipientScopedImageEvidenceV1(
                visibility="private",
                summary=self._settled_summary(occurrence) or visual.activity_description,
                activity=activity,
                location=private_location,
                environment=environment,
                situational_context=situational_context,
                character_media=private_character_media,
            )
            command_id = "visual-evidence:" + _digest(
                [self._ledger.world_id, settlement_ref, "open-life-private"]
            )
            commit = self._recipient_scoped.declare(
                RecipientScopedImageEvidenceDeclarationCommand(
                    command_id=command_id,
                    source_event_ref=settlement_ref,
                    recipient_ref=self._recipient_ref,
                    image_evidence=evidence,
                ),
                logical_time=logical_time,
                created_at=logical_time,
                actor=self._actor,
                trace_id=trace_id,
                correlation_id=correlation_id,
            )
            declared_ref = next(iter(getattr(commit, "event_ids", ())), None)
            opened: tuple[str, ...] = ()
            if declared_ref is not None:
                try:
                    opened = self._character_candidates.open_once(
                        wake_event_ref=declared_ref,
                        logical_time=logical_time,
                        actor=self._actor,
                        trace_id=trace_id,
                        correlation_id=correlation_id,
                    )
                except ValueError:
                    _LOG.warning(
                        "character media candidates could not open for %s", declared_ref
                    )
            return VisualEvidenceAuthorResult(
                status="declared",
                reason_code="visual_evidence.declared",
                declared_event_ref=declared_ref,
                declared_source_ref=settlement_ref,
                lane="private",
                opened_candidate_ids=opened,
            )
        evidence = ImageEvidenceV1(
            visibility=visibility,
            summary=self._settled_summary(occurrence) or visual.activity_description,
            activity=(
                {
                    "id": getattr(occurrence, "trigger_ref", activity_kind),
                    "kind": activity_kind,
                    "description": visual.activity_description,
                }
                if visual.activity_description is not None
                else None
            ),
            location=location,
            environment=environment,
            objects=objects,
            situational_context=situational_context,
            character_media=character_media,
        )
        command_id = "visual-evidence:" + _digest(
            [self._ledger.world_id, settlement_ref, "open-life"]
        )
        commit = self._image_evidence.declare(
            ImageEvidenceDeclarationCommand(
                command_id=command_id,
                source_event_ref=settlement_ref,
                image_evidence=evidence,
            ),
            logical_time=logical_time,
            created_at=logical_time,
            actor=self._actor,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        declared_ref = next(iter(getattr(commit, "event_ids", ())), None)
        opened: tuple[str, ...] = ()
        if declared_ref is not None and character_media is not None:
            try:
                opened = self._character_candidates.open_once(
                    wake_event_ref=declared_ref,
                    logical_time=logical_time,
                    actor=self._actor,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                )
            except ValueError:
                _LOG.warning("character media candidates could not open for %s", declared_ref)
        if declared_ref is not None and self._media_ecology is not None:
            try:
                ecology_res = self._media_ecology.drain_once(
                    wake_event_ref=declared_ref,
                    logical_time=logical_time,
                    actor=self._actor,
                    trace_id=trace_id,
                    correlation_id=correlation_id,
                )
                if ecology_res.candidate_ids:
                    opened = tuple(dict.fromkeys((*opened, *ecology_res.candidate_ids)))
            except Exception:
                _LOG.warning("ecology media candidates could not open for %s", declared_ref)
        return VisualEvidenceAuthorResult(
            status="declared",
            reason_code="visual_evidence.declared",
            declared_event_ref=declared_ref,
            declared_source_ref=settlement_ref,
            lane="public",
            opened_candidate_ids=opened,
        )

    def _open_life_character_media(
        self,
        occurrence,
        *,
        activity_kind: str = "",
        include_mirror: bool = False,
        objects: tuple[dict[str, object], ...] = (),
    ) -> CharacterMediaEvidenceV1 | None:  # type: ignore[no-untyped-def]
        participants = tuple(getattr(occurrence, "participant_refs", ()) or ())
        if (
            self._character_ref not in participants
            and not activity_kind.startswith("self_directed.")
        ):
            return None
        public_object_id = next(
            (
                str(item["id"])
                for item in objects
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item.get("id")
                and item.get("visibility") in {"public", "shareable"}
            ),
            None,
        )
        caps: list[str] = ["character_front_camera"]
        if include_mirror:
            caps.append("mirror")
        if public_object_id is not None:
            caps.append("character_rear_camera")
        return CharacterMediaEvidenceV1(
            character_ref=self._character_ref,
            present=True,
            capture_capabilities=tuple(caps),  # type: ignore[arg-type]
            body_detail=(
                CharacterBodyDetailGroundingV1(
                    body_region="hand",
                    object_ref=public_object_id,
                )
                if public_object_id is not None
                else None
            ),
        )

    def _world_consequence_visual_draft(
        self, *, occurrence: object, plans: Mapping[str, object]
    ) -> tuple[str, LifeDevelopmentVisualEvidenceDraft] | None:
        content_ref = getattr(occurrence, "result_payload_ref", None)
        content_hash = getattr(occurrence, "result_payload_hash", None)
        if content_ref is None or content_hash is None or self._content_store is None:
            return None
        record = self._content_store.read_exact(content_ref=content_ref)
        expected_hash = str(content_hash).removeprefix("sha256:")
        if (
            record is None
            or str(record.content_payload_hash).removeprefix("sha256:") != expected_hash
        ):
            return None
        text = record.text.strip()
        if not text.startswith("{"):
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict) or parsed.get("contract") not in {
            "world-consequence.2",
            "world-consequence.3",
        }:
            return None
        attempt = parsed.get("authorized_attempt_result")
        attempt_text = (
            " ".join(attempt.get("text", "").split()).strip()
            if isinstance(attempt, dict) and isinstance(attempt.get("text"), str)
            else ""
        )
        env_text = (
            " ".join(parsed.get("environment_text", "").split()).strip()
            if isinstance(parsed.get("environment_text"), str)
            else ""
        )
        description = (attempt_text or env_text)[:240]
        if not description:
            return None
        binding = (
            attempt.get("execution_binding")
            if isinstance(attempt, dict) and isinstance(attempt.get("execution_binding"), dict)
            else None
        )
        bound_plan_id = (
            binding.get("plan_id")
            if isinstance(binding, dict) and isinstance(binding.get("plan_id"), str)
            else None
        )
        bound_plan = plans.get(bound_plan_id) if bound_plan_id is not None else None
        activity_kind = getattr(bound_plan, "activity_kind", None)
        if not isinstance(activity_kind, str) or not activity_kind:
            activity_kind = (
                "self_directed.attempt_consequence"
                if attempt_text
                else "open_life.world_consequence"
            )
        is_private_self_directed = (
            activity_kind.startswith("self_directed.")
            and getattr(occurrence, "visibility", None) in {"personal", "private"}
        )
        return (
            activity_kind,
            LifeDevelopmentVisualEvidenceDraft(
                claim_refs=("local:claim:world-consequence",),
                activity_description=description,
                **(
                    {
                        "location": {
                            "location_ref": getattr(bound_plan, "location_ref", None)
                            or "location:private_room",
                            "kind": "private",
                            "publicness": "private",
                            "mirror_available": True,
                        }
                    }
                    if is_private_self_directed
                    else {}
                ),
            ),
        )

    def _settled_summary(self, occurrence) -> str | None:  # type: ignore[no-untyped-def]
        content_ref = getattr(occurrence, "result_payload_ref", None)
        content_hash = getattr(occurrence, "result_payload_hash", None)
        if content_ref is None or content_hash is None or self._content_store is None:
            return None
        record = self._content_store.read_exact(content_ref=content_ref)
        expected_hash = str(content_hash).removeprefix("sha256:")
        if (
            record is None
            or str(record.content_payload_hash).removeprefix("sha256:") != expected_hash
        ):
            return None
        text = record.text.strip()
        if not text:
            return None
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("contract") in {
                "world-consequence.2",
                "world-consequence.3",
            }:
                attempt = parsed.get("authorized_attempt_result")
                attempt_text = (
                    " ".join(attempt.get("text", "").split()).strip()
                    if isinstance(attempt, dict) and isinstance(attempt.get("text"), str)
                    else ""
                )
                env_text = (
                    " ".join(parsed.get("environment_text", "").split()).strip()
                    if isinstance(parsed.get("environment_text"), str)
                    else ""
                )
                combined = attempt_text or env_text
                if combined:
                    return combined[:480]
        return text[:480]

    @staticmethod
    def _location_facts(annex: ReviewedOpeningVisualEvidence) -> dict[str, object] | None:
        if annex.location is None:
            return None
        return {
            key: value
            for key, value in {
                "id": annex.location.id,
                "kind": annex.location.kind,
                "city": annex.location.city,
                "publicness": annex.location.publicness,
                "mirror_available": annex.location.mirror_available,
            }.items()
            if value is not None
        }

    @staticmethod
    def _environment_facts(annex: ReviewedOpeningVisualEvidence) -> dict[str, object] | None:
        if annex.environment is None:
            return None
        facts = {
            key: value
            for key, value in {
                "light": annex.environment.light,
                "structure": annex.environment.structure,
            }.items()
            if value is not None
        }
        return facts or None

    def _situational_context(
        self,
        *,
        projection: _ProjectionLike,
        logical_time: datetime,
        privacy_ceiling: str,
    ) -> MediaSituationalContextV1 | None:
        biography = self._catalog.biographical_context_at(
            instant=logical_time,
            life_arcs=tuple(getattr(projection, "life_arcs", ())),
            biographical_coordinates=tuple(
                getattr(projection, "biographical_coordinates", ())
            ),
        )
        tags = biography.context_tags
        season = next(
            (item.removeprefix("season:") for item in tags if item.startswith("season:")),
            None,
        )
        if season not in {"spring", "summer", "autumn", "winter"}:
            return None
        timeline_refs = tuple(
            item.event_id
            for item in getattr(projection, "committed_world_event_refs", ())
            if getattr(item, "event_type", None) == "BiographicalTimelineConfigured"
            and getattr(item, "logical_time", logical_time) <= logical_time
        )
        if len(timeline_refs) != 1:
            _LOG.warning(
                "media situational context requires one biographical timeline source"
            )
            return None
        active_arc_ids = set(biography.active_life_arc_ids)
        active_arcs = tuple(
            item
            for item in getattr(projection, "life_arcs", ())
            if getattr(item, "arc_id", None) in active_arc_ids
        )
        ceiling_rank = _PRIVACY_RANK.get(privacy_ceiling)
        if (
            ceiling_rank is None
            or len(active_arcs) != len(active_arc_ids)
            or any(
                not getattr(item, "accepted_event_ref", None)
                or _PRIVACY_RANK.get(getattr(item, "privacy_class", None), 99)
                > ceiling_rank
                for item in active_arcs
            )
        ):
            _LOG.warning(
                "media situational context has an unbound or more-private active Life Arc"
            )
            return None
        source_event_refs = tuple(
            sorted(
                {
                    *timeline_refs,
                    *(
                        str(item.accepted_event_ref)
                        for item in active_arcs
                    ),
                }
            )
        )
        return MediaSituationalContextV1(
            season=season,
            academic_phase=biography.academic_phase,
            academic_year=biography.academic_year,
            calendar_context_tags=tuple(
                item
                for item in tags
                if item.startswith(("calendar:", "academic:"))
            ),
            current_residence_context_tags=tuple(
                item for item in tags if item.startswith("residence:")
            ),
            life_arc_context_tags=tuple(
                item
                for item in tags
                if item.startswith(("life_arc:", "narrative:", "work:", "travel:"))
            ),
            active_life_arc_ids=biography.active_life_arc_ids,
            source_event_refs=source_event_refs,
        )

    def _has_live_declared_display(self, projection: _ProjectionLike) -> bool:
        logical_time = getattr(projection, "logical_time", None)
        if self._recipient_ref is None or not isinstance(logical_time, datetime):
            return False
        if not callable(getattr(self._ledger, "lookup_event_commit", None)):
            return False
        return (
            live_declared_display(
                ledger=self._ledger,
                projection=projection,
                recipient_ref=self._recipient_ref,
                at_logical_time=logical_time,
            )
            is not None
        )

    def _recipient_relationship_ready(self, projection: _ProjectionLike) -> bool:
        """Check whether recipient-scoped P3 declarations are eligible."""

        if self._recipient_ref is None:
            return False
        states = tuple(
            state
            for state in getattr(projection, "relationship_states", ())
            if getattr(state, "subject_ref", None) == self._recipient_ref
        )
        if len(states) != 1:
            return False
        if self._has_live_declared_display(projection):
            return True
        return getattr(states[0], "stage", None) in _PRIVATE_ELIGIBLE_STAGES


__all__ = [
    "LifeVisualEvidenceAuthor",
    "VisualEvidenceAuthorPolicy",
    "VisualEvidenceAuthorResult",
]
