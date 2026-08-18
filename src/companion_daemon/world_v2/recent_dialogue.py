"""Replay-safe recent dialogue compiler backed only by accepted ledger authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .expression_payload_store import ImmutableExpressionPayloadStore
from .present_prompt import (
    PRESENT_COMPANION_DIALOGUE_ITEM_LIMIT,
    PRESENT_RECENT_DIALOGUE_ITEM_LIMIT,
)
from .ledger import LedgerPort
from .qq_face_render_catalog import compile_inbound_surfaces
from .schema_core import FrozenModel, PrivacyClass
from .schemas import (
    CommittedWorldEventRef,
    ExecutionReceipt,
    LedgerProjection,
    Observation,
)


_DIALOGUE_SEQUENCE_SCALE = 100
# ResolverProof only admits 32 authority refs for the whole dialogue slice.
# Companion beats carry several claims each, so without a live-head boost the
# lexicographic item_ref tie-break fills that budget with her lines and drops
# his later ones. These caps mark the still-live tail so rank keeps it.
_LIVE_COUNTERPART_ATTENTION = 8
_LIVE_COMPANION_ATTENTION = 8


def observation_dialogue_text(observation: Observation) -> str | None:
    """Visible counterpart line for one Observation, including non-text reactions.

    Text stays the Observation body when he actually typed.  A reaction or
    sticker with no body uses the reviewed platform render label (glyph plus
    catalog name) so continuity can mark that Observation as the current turn.
    Unmatched ids stay as the provider ref.  This is the same catalog fact as
    ``inbound_surfaces``; it is not a host reading of mood or intent.
    """

    if observation.text is not None and observation.text.strip():
        return observation.text
    meta = observation.coalescing_metadata
    if not isinstance(meta, dict):
        return None
    reaction_refs = tuple(
        item for item in (meta.get("reaction_refs") or ()) if isinstance(item, str) and item
    )
    sticker_refs = tuple(
        item for item in (meta.get("sticker_refs") or ()) if isinstance(item, str) and item
    )
    if not reaction_refs and not sticker_refs:
        return None
    labels: list[str] = []
    for surface in compile_inbound_surfaces(
        reaction_refs=reaction_refs,
        sticker_refs=sticker_refs,
    ):
        glyph = surface.platform_render_glyph
        name = surface.platform_render_name
        if isinstance(glyph, str) and glyph.strip() and isinstance(name, str) and name.strip():
            labels.append(f"{glyph.strip()} {name.strip()}")
        elif isinstance(name, str) and name.strip():
            labels.append(name.strip())
        else:
            labels.append(surface.provider_ref)
    joined = "、".join(labels)
    return joined[:4_096] if joined else None


def dialogue_causal_sequence(*, world_revision: int, position: int = 0) -> int:
    """Map ledger authority plus an in-plan beat position onto one total order."""

    if world_revision < 1:
        raise ValueError("dialogue world revision must be positive")
    if not 0 <= position < _DIALOGUE_SEQUENCE_SCALE:
        raise ValueError("dialogue position exceeds sequence scale")
    return world_revision * _DIALOGUE_SEQUENCE_SCALE + position


class DialogueSourceClaim(FrozenModel):
    authority_event_ref: str = Field(min_length=1)
    authority_world_revision: int = Field(ge=1)
    authority_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RecentDialogueItem(FrozenModel):
    dialogue_id: str = Field(min_length=1)
    speaker: Literal["counterpart", "companion"]
    # The role label is convenient for presentation, but it cannot preserve
    # whose report an old turn contains.  Production records the exact actor
    # so recall can distinguish "the counterpart told me X" from a companion
    # Experience.  The optional default keeps historical in-memory fixtures
    # and replayed compatibility packets readable.
    speaker_ref: str | None = Field(
        default=None,
        min_length=1,
        exclude_if=lambda value: value is None,
    )
    text: str = Field(min_length=1, max_length=4_096)
    occurred_at: datetime
    delivery_state: Literal["observed", "provider_accepted", "delivered", "unknown"]
    sequence: int = Field(ge=1)
    privacy_class: PrivacyClass = "private"
    source_claims: tuple[DialogueSourceClaim, ...] = Field(min_length=1, max_length=6)
    acknowledges_observation_event_refs: tuple[str, ...] = Field(
        default=(), max_length=4
    )
    sidecar_ref: str | None = Field(default=None, min_length=1)
    sidecar_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    continuity_reasons: tuple[
        Literal[
            "current_turn",
            "pending_interaction",
            "acknowledged_context",
            "topic_reactivation",
            "recent_companion",
            "recent",
        ],
        ...,
    ] = Field(default=(), exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def sidecar_binding_is_complete(self) -> "RecentDialogueItem":
        if (self.sidecar_ref is None) != (self.sidecar_hash is None):
            raise ValueError("recent dialogue sidecar binding is partial")
        refs = tuple(item.authority_event_ref for item in self.source_claims)
        if len(refs) != len(set(refs)):
            raise ValueError("recent dialogue source claims must be unique")
        if len(self.acknowledges_observation_event_refs) != len(
            set(self.acknowledges_observation_event_refs)
        ):
            raise ValueError("recent dialogue acknowledgement refs must be unique")
        return self


@dataclass(frozen=True, slots=True)
class RecentDialogueCompilation:
    """Bounded visible text plus full effect-once acknowledgement authority."""

    dialogue: tuple[RecentDialogueItem, ...]
    acknowledged_observation_event_refs: frozenset[str]


class RecentDialogueCompiler:
    """Compile recent inbound text and only provider-visible accepted expressions."""

    def __init__(
        self,
        *,
        ledger: LedgerPort,
        expression_payload_store: ImmutableExpressionPayloadStore | None = None,
        max_user_items: int = PRESENT_RECENT_DIALOGUE_ITEM_LIMIT,
        max_companion_items: int = PRESENT_COMPANION_DIALOGUE_ITEM_LIMIT,
    ) -> None:
        if (
            not 8 <= max_user_items <= PRESENT_RECENT_DIALOGUE_ITEM_LIMIT
            or not 0 <= max_companion_items <= PRESENT_COMPANION_DIALOGUE_ITEM_LIMIT
        ):
            raise ValueError("recent dialogue history bounds are invalid")
        self._ledger = ledger
        self._payloads = expression_payload_store
        self._max_user = max_user_items
        self._max_companion = max_companion_items

    def compile(
        self,
        *,
        projection: LedgerProjection,
        actor_ref: str,
        subject_refs: frozenset[str],
        max_user_items: int | None = None,
    ) -> tuple[RecentDialogueItem, ...]:
        return self.compile_with_acknowledgements(
            projection=projection,
            actor_ref=actor_ref,
            subject_refs=subject_refs,
            max_user_items=max_user_items,
        ).dialogue

    def compile_with_acknowledgements(
        self,
        *,
        projection: LedgerProjection,
        actor_ref: str,
        subject_refs: frozenset[str],
        max_user_items: int | None = None,
    ) -> RecentDialogueCompilation:
        user_limit = self._max_user if max_user_items is None else max_user_items
        if not self._max_user <= user_limit <= PRESENT_RECENT_DIALOGUE_ITEM_LIMIT:
            raise ValueError("recent dialogue candidate window is invalid")
        refs = {item.event_id: item for item in projection.committed_world_event_refs}
        observation_event_refs = {
            (item.world_revision, item.payload_hash): item
            for item in projection.committed_world_event_refs
            if item.event_type == "ObservationRecorded"
        }
        inbound: list[RecentDialogueItem] = []
        # Resolve only the bounded recent user slice.  Walking every message
        # ever observed and then proving each source defeats the purpose of a
        # bounded Context capsule once a long-lived room has hundreds of
        # turns.  The projection already carries the actor and exact payload
        # hash needed to select candidates without trusting model text.
        observation_candidates = sorted(
            (
                item
                for item in projection.message_observations
                if item.actor != actor_ref and item.actor in subject_refs
            ),
            key=lambda item: (item.world_revision, item.observation_id),
        )[-user_limit :]
        for observation_ref in observation_candidates:
            event_ref = observation_event_refs.get(
                (observation_ref.world_revision, observation_ref.event_payload_hash)
            )
            if event_ref is None:
                continue
            located = self._ledger.lookup_event_commit(event_ref.event_id)
            if located is None:
                continue
            try:
                observation = Observation.model_validate_json(located[0].payload_json)
            except ValueError:
                continue
            text = observation_dialogue_text(observation)
            if (
                observation.actor == actor_ref
                or observation.actor not in subject_refs
                or text is None
            ):
                continue
            inbound.append(
                RecentDialogueItem(
                    dialogue_id=f"dialogue:observation:{observation.observation_id}",
                    speaker="counterpart",
                    speaker_ref=observation.actor,
                    text=text,
                    occurred_at=observation.received_at,
                    delivery_state="observed",
                    # Observation timestamps are only second-granularity on
                    # some platform paths.  Use the committed ledger revision
                    # as the primary conversational order and reserve the
                    # low digits for multi-beat replies.
                    sequence=dialogue_causal_sequence(
                        world_revision=event_ref.world_revision
                    ),
                    source_claims=(self._claim(event_ref),),
                )
            )
        inbound = inbound[-user_limit:]

        companion: list[RecentDialogueItem] = []
        plans = {item.plan_id: item for item in projection.expression_plans}
        actions = {item.action_id: item for item in projection.actions}
        stored = {item.payload_ref: item for item in projection.stored_message_payloads}
        descriptors = {item.payload_ref: item for item in projection.expression_payload_descriptors}
        receipts = {item.action_id: item for item in projection.execution_receipts}
        historically_visible_action_ids = frozenset(
            item.action_id
            for item in projection.execution_receipts
            if item.observed_state in {"provider_accepted", "delivered", "unknown"}
        )
        historically_delivered_action_ids = frozenset(
            item.action_id
            for item in projection.execution_receipts
            if item.observed_state == "delivered"
        )
        proposal_audits = {
            item.proposal_id: item for item in projection.proposal_audits
        }
        accepted_expressions: list[
            tuple[str, str, str, str | None, list[dict[str, str | None]]]
        ] = []
        for manifest in projection.expression_plan_manifests:
            audit = proposal_audits.get(manifest.proposal_id)
            accepted_expressions.append((
                manifest.acceptance_id,
                manifest.plan_id,
                manifest.acceptance_event_ref,
                (
                    manifest.social_source_observation_event_ref
                    or (audit.trigger_ref if audit is not None else None)
                ),
                [
                    {
                        "beat_id": beat.beat_id,
                        "payload_ref": beat.payload_ref,
                        "payload_hash": beat.payload_hash,
                        "text": beat.text,
                        "action_id": beat.action.action_id,
                    }
                    for beat in manifest.beats
                ],
            ))
        for manifest in projection.minimal_reply_manifests:
            audit = proposal_audits.get(manifest.proposal_id)
            accepted_expressions.append((
                manifest.acceptance_id,
                manifest.plan_id,
                manifest.acceptance_event_ref,
                audit.trigger_ref if audit is not None else None,
                [{
                    "beat_id": manifest.beat_id,
                    "payload_ref": manifest.message_payload_ref,
                    "payload_hash": manifest.message_payload_hash,
                    "text": None,
                    "action_id": manifest.action_id,
                }],
            ))
        candidate_observation_event_refs = frozenset(
            claim.authority_event_ref
            for item in inbound
            for claim in item.source_claims
        )
        acknowledged_observation_event_refs = frozenset(
            acknowledged_event_ref
            for (
                _acceptance_id,
                plan_id,
                acceptance_event_ref,
                acknowledged_event_ref,
                beats,
            ) in accepted_expressions
            if acknowledged_event_ref is not None
            and acknowledged_event_ref in candidate_observation_event_refs
            and acknowledged_event_ref in refs
            and refs[acknowledged_event_ref].event_type == "ObservationRecorded"
            and acceptance_event_ref in refs
            and plan_id in plans
            and any(
                isinstance(beat.get("action_id"), str)
                and (action := actions.get(str(beat["action_id"]))) is not None
                and action.action_id in historically_visible_action_ids
                for beat in beats
            )
        )
        # Select a small recent manifest window before proving delivery
        # receipts.  Manifests are already bound to an acceptance event in
        # the projection; sorting by that committed revision retains the
        # visible tail while avoiding one SQLite authority lookup per old
        # reply on every turn.
        accepted_expressions = sorted(
            accepted_expressions,
            key=lambda item: (
                refs.get(item[2]).world_revision if refs.get(item[2]) is not None else 0,
                item[1],
            ),
            reverse=True,
        )[: max(1, self._max_companion)]
        candidate_action_ids = {
            str(beat["action_id"])
            for _, _, _, _, beats in accepted_expressions
            for beat in beats
            if isinstance(beat.get("action_id"), str)
        }
        receipt_events: dict[
            str, tuple[CommittedWorldEventRef, ExecutionReceipt]
        ] = {}
        first_visible_receipts: dict[
            str, tuple[CommittedWorldEventRef, ExecutionReceipt]
        ] = {}
        for ref in projection.committed_world_event_refs:
            if ref.event_type != "ExecutionReceiptRecorded":
                continue
            located = self._ledger.lookup_event_commit(ref.event_id)
            raw = located[0].payload().get("receipt") if located is not None else None
            try:
                # Ledger JSON necessarily represents tuples and datetimes as
                # arrays and strings.  Parse that JSON representation before
                # applying the frozen receipt contract.
                recorded_receipt = ExecutionReceipt.model_validate(raw, strict=False)
            except ValueError:
                continue
            if recorded_receipt.action_id not in candidate_action_ids:
                continue
            receipt_events[recorded_receipt.receipt_id] = (ref, recorded_receipt)
            if recorded_receipt.observed_state not in {
                "provider_accepted",
                "delivered",
                "unknown",
            }:
                continue
            previous_visible = first_visible_receipts.get(recorded_receipt.action_id)
            if (
                previous_visible is None
                or ref.world_revision < previous_visible[0].world_revision
            ):
                first_visible_receipts[recorded_receipt.action_id] = (
                    ref,
                    recorded_receipt,
                )
        for (
            acceptance_id,
            plan_id,
            acceptance_event_ref,
            acknowledged_event_ref,
            beats,
        ) in accepted_expressions:
            acceptance = refs.get(acceptance_event_ref)
            plan = plans.get(plan_id)
            if acceptance is None or plan is None:
                continue
            for position, beat in enumerate(beats, start=1):
                action_id = beat["action_id"]
                payload_id = beat["payload_ref"]
                payload_hash = beat["payload_hash"]
                beat_id = beat["beat_id"]
                if not all(isinstance(value, str) for value in (
                    action_id, payload_id, payload_hash, beat_id
                )):
                    continue
                action = actions.get(action_id)
                receipt = receipts.get(action_id)
                if action is None:
                    continue
                first_visible_receipt = first_visible_receipts.get(action_id)
                if first_visible_receipt is None:
                    continue
                visible_receipt_event_ref, visible_receipt = first_visible_receipt
                receipt_event = (
                    receipt_events.get(receipt.receipt_id)
                    if receipt is not None
                    else None
                )
                delivery_refs = [visible_receipt_event_ref]
                if (
                    receipt_event is not None
                    and receipt_event[0].event_id != visible_receipt_event_ref.event_id
                ):
                    delivery_refs.append(receipt_event[0])
                if action.state == "delivered":
                    terminal = next(
                        (
                            item
                            for item in reversed(plan.history)
                            if item.state == "completed"
                            and item.terminal_action_state == "delivered"
                        ),
                        None,
                    )
                    if terminal is None or terminal.event_ref not in refs:
                        continue
                    delivery_refs.append(refs[terminal.event_ref])
                text: str | None = None
                payload_ref = stored.get(payload_id)
                sidecar_ref = sidecar_hash = None
                if (
                    payload_ref is not None
                    and payload_ref.acceptance_id == acceptance_id
                    and payload_ref.payload_hash == payload_hash
                    and (beat["text"] is None or payload_ref.text == beat["text"])
                    and payload_ref.event_ref in refs
                ):
                    text = payload_ref.text
                    payload_event_ref = refs[payload_ref.event_ref]
                else:
                    descriptor = descriptors.get(payload_id)
                    record = (
                        self._payloads.read_exact(payload_ref=payload_id)
                        if descriptor is not None and self._payloads is not None
                        else None
                    )
                    if (
                        descriptor is None
                        or record is None
                        or descriptor.acceptance_id != acceptance_id
                        or descriptor.payload_hash != payload_hash
                        or record.payload_hash != payload_hash
                        or record.content_type != "text/plain"
                        or record.payload_kind != "referenced"
                        or descriptor.event_ref not in refs
                    ):
                        continue
                    text = record.encoded_payload
                    payload_event_ref = refs[descriptor.event_ref]
                    sidecar_ref, sidecar_hash = record.payload_ref, record.payload_hash
                companion.append(
                    RecentDialogueItem(
                        dialogue_id=f"dialogue:expression:{plan_id}:{beat_id}",
                        speaker="companion",
                        speaker_ref=actor_ref,
                        text=text,
                        occurred_at=visible_receipt.received_at,
                        delivery_state=(
                            "delivered"
                            if action_id in historically_delivered_action_ids
                            else visible_receipt.observed_state
                        ),
                        sequence=dialogue_causal_sequence(
                            world_revision=visible_receipt_event_ref.world_revision,
                            position=position,
                        ),
                        source_claims=tuple(
                            sorted(
                                (
                                    self._claim(acceptance),
                                    self._claim(payload_event_ref),
                                    *(self._claim(item) for item in delivery_refs),
                                    *(
                                        (self._claim(refs[acknowledged_event_ref]),)
                                        if acknowledged_event_ref in refs
                                        else ()
                                    ),
                                ),
                                key=lambda item: item.authority_event_ref,
                            )
                        ),
                        acknowledges_observation_event_refs=(
                            (acknowledged_event_ref,)
                            if acknowledged_event_ref in refs
                            and refs[acknowledged_event_ref].event_type
                            == "ObservationRecorded"
                            else ()
                        ),
                        sidecar_ref=sidecar_ref,
                        sidecar_hash=sidecar_hash,
                    )
                )
        companion = sorted(companion, key=lambda item: item.sequence)[-self._max_companion :]
        inbound, companion = _mark_live_conversation_head(inbound, companion)
        return RecentDialogueCompilation(
            dialogue=tuple(
                sorted(
                    (*inbound, *companion),
                    key=lambda item: item.sequence,
                    reverse=True,
                )
            ),
            acknowledged_observation_event_refs=acknowledged_observation_event_refs,
        )

    @staticmethod
    def _claim(ref) -> DialogueSourceClaim:  # type: ignore[no-untyped-def]
        return DialogueSourceClaim(
            authority_event_ref=ref.event_id,
            authority_world_revision=ref.world_revision,
            authority_payload_hash=ref.payload_hash,
        )


def _with_continuity_reasons(
    item: RecentDialogueItem, *extra: str
) -> RecentDialogueItem:
    reasons = tuple(dict.fromkeys((*item.continuity_reasons, *extra)))
    if reasons == item.continuity_reasons:
        return item
    return item.model_copy(update={"continuity_reasons": reasons})


def _mark_live_conversation_head(
    inbound: list[RecentDialogueItem],
    companion: list[RecentDialogueItem],
) -> tuple[list[RecentDialogueItem], list[RecentDialogueItem]]:
    """Tag both live tails so a she-initiates turn keeps a real conversation.

    Inbound continuity overwrites these reasons when a counterpart Observation
    is the trigger. Receipt, clock, and impression triggers keep the tags.
    His latest line is ordinary live history (``recent``), never ``current_turn``:
    only the actual triggering observation is current. Packing, not a sticky
    current mark, is what keeps his last few lines in the slice.
    """

    inbound_by_seq = sorted(
        inbound, key=lambda item: (item.sequence, item.occurred_at, item.dialogue_id)
    )
    companion_by_seq = sorted(
        companion, key=lambda item: (item.sequence, item.occurred_at, item.dialogue_id)
    )
    live_him = {id(item) for item in inbound_by_seq[-_LIVE_COUNTERPART_ATTENTION:]}
    live_me = {id(item) for item in companion_by_seq[-_LIVE_COMPANION_ATTENTION:]}
    marked_inbound = [
        _with_continuity_reasons(item, "recent") if id(item) in live_him else item
        for item in inbound
    ]
    marked_companion = [
        _with_continuity_reasons(item, "recent_companion") if id(item) in live_me else item
        for item in companion
    ]
    return marked_inbound, marked_companion


def delivered_photo_dialogue_item(
    *,
    delivery_id: str,
    about: str,
    shared_at: datetime,
    actor_ref: str,
    authority_event_ref: str,
    authority_world_revision: int,
    authority_payload_hash: str,
) -> RecentDialogueItem:
    """Companion line for one already-delivered photo. No prompt or artifact."""

    label = about.strip()
    return RecentDialogueItem(
        dialogue_id=f"dialogue:media-delivery:{delivery_id}",
        speaker="companion",
        speaker_ref=actor_ref,
        text=f"[{label}]",
        occurred_at=shared_at,
        delivery_state="delivered",
        sequence=dialogue_causal_sequence(world_revision=authority_world_revision),
        source_claims=(
            DialogueSourceClaim(
                authority_event_ref=authority_event_ref,
                authority_world_revision=authority_world_revision,
                authority_payload_hash=authority_payload_hash,
            ),
        ),
        continuity_reasons=("recent_companion",),
    )


__all__ = [
    "DialogueSourceClaim",
    "RecentDialogueCompilation",
    "RecentDialogueCompiler",
    "RecentDialogueItem",
    "delivered_photo_dialogue_item",
    "dialogue_causal_sequence",
    "observation_dialogue_text",
]
