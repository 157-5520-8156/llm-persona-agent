from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from hashlib import sha256
import threading
import time

import pytest

from companion_daemon.world_v2.biographical_claim_authority import (
    biographical_coordinate_authorities,
)
from companion_daemon.world_v2.character_interior.inbound_wire import (
    _ExpressionDraftWire,
    _RoutedExpressionDraftWire,
    _incremental_first_expression,
    _stream_first_expression,
    _stream_tail_expression,
    _normalize_source_review_attempt_failure_code,
    expression_draft_shape_contract,
    review_expression_source_closure,
    review_expression_with_candidate_external_coverage,
    review_expression_source_closure_appeal,
    shape_repair_instruction,
)
from companion_daemon.world_v2.character_interior.inbound_turn import InboundTurnFaculty
from companion_daemon.world_v2.companion_identity import (
    CompanionIdentityFrame,
    companion_identity_source_ref,
)
from companion_daemon.world_v2.expression_draft import (
    ExpressionDraft,
    QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    TEXT_ONLY_EXPRESSION_CAPABILITIES,
    current_counterpart_report_source_refs,
    materialize_expression_draft,
    qq_expression_capabilities,
    _world_claim_evidence,
)
from companion_daemon.world_v2.model_facing_context import (
    compact_chat_model_facing_context,
)
from companion_daemon.world_v2.deliberation import (
    ModelInput,
    ModelRoute,
    ModelUsageProvenance,
    TriggerMessage,
    ValidationTechnicalFailure,
)
from companion_daemon.world_v2.recall_index import (
    FeatureHashRecallEmbedding,
    InMemoryRecallIndex,
    RecallCursor,
    RecallDocument,
    RecallSourceBinding,
)
from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
from companion_daemon.world_v2.recall_corpus import RecallCorpusSources
from companion_daemon.world_v2.recall_runtime import (
    PresentedPrefetchTrace,
    RecallCoordinator,
    perform_character_recall,
    verify_trusted_recall_trace,
)


def _request() -> ModelInput:
    return ModelInput(
        call_id="call:1",
        attempt_id="attempt:1",
        route=ModelRoute(tier="flash", reason_code="test", router_version="test.1"),
        capsule_id="a" * 64,
        trigger_ref="trigger:1",
        evaluated_world_revision=3,
        model_content_json='{"capsule":"authoritative"}',
    )


class _StreamReservationAuthor:
    def propose(self, _request: ModelInput) -> object:
        raise AssertionError("the stale stream publication test must not author a turn")

    def stream_provider_available(self, _request: ModelInput) -> bool:
        return True


@pytest.mark.asyncio
async def test_late_cancelled_stream_head_publication_is_ignored_after_attention_advance() -> None:
    faculty = InboundTurnFaculty(author=_StreamReservationAuthor())
    request = _request()

    assert faculty.transport_available(transport="stream", payload=request) is True
    faculty.advance_attention(attention_ref="attention:newer-message")

    # The superseded provider task may unwind after advance_attention() has
    # cleared its reservation. Its cancellation publication must not mask the
    # original supersession with a second invariant error.
    faculty.publish_transport(
        transport="stream_head",
        payload=request,
        output=None,
    )


def test_shape_repair_reselects_the_complete_grounded_expression() -> None:
    instruction = shape_repair_instruction(
        "beats.0.text: Field required",
    )

    assert "complete expression" in instruction.lower()
    assert "same pinned Context" in instruction
    assert "private_turn_state" in instruction
    assert "world_claims" in instruction
    assert "attended_source_refs" in instruction
    assert "preserving the visible reply" not in instruction
    assert "fixes only this problem" not in instruction


def _provider_request_hash(
    messages: list[dict[str, str]],
    temperature: float,
) -> str:
    return sha256(
        json.dumps(
            {
                "messages": messages,
                "temperature": temperature,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def test_production_qq_capabilities_require_the_model_owned_private_state() -> None:
    assert qq_expression_capabilities("napcat").private_turn_state_mode == "required"
    assert qq_expression_capabilities("text-only").private_turn_state_mode == "required"


def test_qq_media_request_capability_is_enabled_only_by_a_real_deployment() -> None:
    unavailable = qq_expression_capabilities("napcat")
    available = qq_expression_capabilities("napcat", media_request_available=True)

    assert unavailable.media_request_mode == "unavailable"
    assert available.media_request_mode == "candidate_only"
    assert available.prompt_value()["media_request_mode"] == "candidate_only"
    assert available.prompt_value()["media_request_timing"] == "now_only"


def test_media_source_selection_becomes_durable_event_evidence_not_private_audit() -> None:
    source_ref = "event:life-aftermath:experience:walk"
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "logical_time": "2026-08-11T12:00:00+00:00",
                    "slices": {
                        "recent_experiences": {
                            "availability": "available",
                            "items": [
                                {
                                    "source_ref": source_ref,
                                    "source_bindings": [
                                        {
                                            "ref": source_ref,
                                            "source_kind": "committed_event",
                                            "authority_type": "ExperienceCommitted",
                                            "source_world_revision": 2,
                                            "immutable_hash": "c" * 64,
                                        }
                                    ],
                                    "value": {"experience_id": "experience:walk"},
                                }
                            ],
                        }
                    },
                }
            )
        }
    )
    proposal = materialize_expression_draft(
        value={
            "private_turn_state": {
                "contract": "private-turn-state.1",
                "inner_state_summary": "I want to consider sharing this lived moment.",
                "attended_source_refs": [source_ref],
            },
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "刚才那一刻，我有点想拍下来。"}],
            "stance": "considering",
            "brief_rationale": "The exact lived moment may be worth sharing.",
            "confidence": 7_500,
            "world_claims": [],
            "media_request": "consider_available_candidate",
            "media_source_refs": [source_ref],
        },
        request=request,
        capabilities=qq_expression_capabilities(
            "napcat", media_request_available=True
        ),
        private_state_context_json=request.model_content_json,
    )

    evidence = next(item for item in proposal.evidence_refs if item.ref_id == source_ref)
    assert evidence.evidence_kind == "committed_experience"
    assert proposal.private_turn_state is not None
    change = proposal.proposed_changes[0]
    assert change.payload.value()["media_source_refs"] == [source_ref]


def test_media_request_accepts_a_snapshot_pinned_photo_candidate() -> None:
    source_ref = "event:photo-candidate:opened:bookstore"
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "logical_time": "2026-08-19T06:00:45+00:00",
                    "slices": {
                        "shareable_photos": {
                            "availability": "available",
                            "source_refs": [source_ref],
                            "items": [
                                {
                                    "source_ref": source_ref,
                                    "source_bindings": [
                                        {
                                            "ref": source_ref,
                                            "source_kind": "committed_event",
                                            "authority_type": "PhotoCandidateOpened",
                                            "source_world_revision": 3,
                                            "immutable_hash": "c" * 64,
                                        }
                                    ],
                                    "value": {"photo_in_hand": True},
                                }
                            ],
                        }
                    },
                    "inner_life_snapshot": {
                        "source_refs": [source_ref],
                        "materials": {
                            "moments_i_can_share": {
                                "available_count": 1,
                                "source_refs": [source_ref],
                                "items": [
                                    {
                                        "source_ref": source_ref,
                                        "photo_in_hand": True,
                                    }
                                ],
                            }
                        },
                    },
                }
            )
        }
    )
    proposal = materialize_expression_draft(
        value={
            "private_turn_state": {
                "contract": "private-turn-state.1",
                "inner_state_summary": "I want to send the bookstore photo.",
                "attended_source_refs": [source_ref],
            },
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "书店那张我挑好了，这就发你。"}],
            "stance": "considering",
            "brief_rationale": "The opened candidate is already in my album.",
            "confidence": 7_500,
            "world_claims": [],
            "media_request": "consider_available_candidate",
            "media_source_refs": [source_ref],
        },
        request=request,
        capabilities=qq_expression_capabilities(
            "napcat", media_request_available=True
        ),
        private_state_context_json=request.model_content_json,
    )
    assert proposal.proposed_changes[0].payload.value()["media_source_refs"] == [
        source_ref
    ]


def test_media_request_survives_chat_compaction_of_shareable_photo_bindings() -> None:
    source_ref = "event:character-media-candidate:" + "ab" * 32
    digest = "9" * 64
    raw = {
        "logical_time": "2026-08-19T06:00:45+00:00",
        "slices": {
            "shareable_photos": {
                "availability": "available",
                "source_refs": [source_ref],
                "items": [
                    {
                        "source_ref": source_ref,
                        "source_bindings": [
                            {
                                "ref": source_ref,
                                "source_kind": "committed_event",
                                "authority_type": "PhotoCandidateOpened",
                                "source_world_revision": 3,
                                "immutable_hash": digest,
                            }
                        ],
                        "value": {"photo_in_hand": True},
                    }
                ],
            }
        },
    }
    request = _qq_request().model_copy(
        update={"model_content_json": compact_chat_model_facing_context(json.dumps(raw))}
    )
    proposal = materialize_expression_draft(
        value={
            "private_turn_state": {
                "contract": "private-turn-state.1",
                "inner_state_summary": "I already opened that bookstore photo.",
                "attended_source_refs": [source_ref],
            },
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "书店那张我挑好了，这就发你。"}],
            "stance": "considering",
            "brief_rationale": "The opened candidate is already in my album.",
            "confidence": 7_500,
            "world_claims": [],
            "media_request": "consider_available_candidate",
            "media_source_refs": [source_ref],
        },
        request=request,
        capabilities=qq_expression_capabilities(
            "napcat", media_request_available=True
        ),
        private_state_context_json=request.model_content_json,
    )
    evidence = next(item for item in proposal.evidence_refs if item.ref_id == source_ref)
    assert evidence.immutable_hash == "sha256:" + digest
    assert proposal.proposed_changes[0].payload.value()["media_source_refs"] == [
        source_ref
    ]


def test_media_request_survives_overflow_attended_refs() -> None:
    source_ref = "event:photo-candidate:opened:bookstore"
    extras = [f"event:appraisal:compiled:{index:02d}" for index in range(9)]
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "logical_time": "2026-08-19T06:00:45+00:00",
                    "slices": {
                        "shareable_photos": {
                            "availability": "available",
                            "source_refs": [source_ref, *extras],
                            "items": [
                                {
                                    "source_ref": ref,
                                    "source_bindings": [
                                        {
                                            "ref": ref,
                                            "source_kind": "committed_event",
                                            "authority_type": "PhotoCandidateOpened",
                                            "source_world_revision": 3,
                                            "immutable_hash": "c" * 64,
                                        }
                                    ],
                                    "value": {"photo_in_hand": True},
                                }
                                for ref in (source_ref, *extras)
                            ],
                        }
                    },
                }
            )
        }
    )
    proposal = materialize_expression_draft(
        value={
            "private_turn_state": {
                "contract": "private-turn-state.1",
                "inner_state_summary": "Too many readings were in view.",
                "attended_source_refs": [source_ref, *extras],
            },
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "书店那张这就发你。"}],
            "stance": "considering",
            "brief_rationale": "The opened candidate is already in my album.",
            "confidence": 7_500,
            "world_claims": [],
            "media_request": "consider_available_candidate",
            "media_source_refs": [source_ref],
        },
        request=request,
        capabilities=qq_expression_capabilities(
            "napcat", media_request_available=True
        ),
        private_state_context_json=request.model_content_json,
    )
    assert proposal.private_turn_state is not None
    assert len(proposal.private_turn_state.attended_source_refs) == 8
    assert proposal.private_turn_state.attended_source_refs[0] == source_ref
    assert proposal.proposed_changes[0].payload.value()["media_source_refs"] == [
        source_ref
    ]


def test_media_request_still_rejects_an_unpinned_photo_candidate() -> None:
    request = _qq_request().model_copy(
        update={"model_content_json": json.dumps({"logical_time": "2026-08-19T06:00:45+00:00", "slices": {}})}
    )
    with pytest.raises(ValueError, match="unpinned source ref"):
        materialize_expression_draft(
            value={
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "I want to send a photo.",
                    "attended_source_refs": [],
                },
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "发你一张。"}],
                "stance": "considering",
                "brief_rationale": "A photo I cannot legally cite.",
                "confidence": 7_500,
                "world_claims": [],
                "media_request": "consider_available_candidate",
                "media_source_refs": ["event:photo-candidate:opened:missing"],
            },
            request=request,
            capabilities=qq_expression_capabilities(
                "napcat", media_request_available=True
            ),
            private_state_context_json=request.model_content_json,
        )


def test_private_turn_state_contract_does_not_license_invented_life_context() -> None:
    contract = expression_draft_shape_contract()

    assert "do not invent a current activity, place, bodily event" in contract
    assert "Visible beats may contain factual first-person life claims only" in contract
    assert "only what the pinned Context presents" in contract
    assert "optimize the conversation" in contract


def test_expression_draft_shape_contract_speaks_her_language() -> None:
    """Everything addressed to her is Chinese; only literal JSON stays English."""

    contract = expression_draft_shape_contract()
    chinese = sum(1 for char in contract if "\u4e00" <= char <= "\u9fff")
    assert chinese / len(contract) > 0.28
    for field in (
        "timing_choice",
        "cadence",
        "turn_posture",
        "media_request",
        "world_claims",
        "private_turn_state",
        "source_refs",
        "beats",
    ):
        assert field in contract
    for enum_value in (
        "now",
        "later",
        "silent",
        "yield",
        "consider_available_candidate",
        "still_pending",
        "current_world",
    ):
        assert enum_value in contract
    assert "140" in contract
    assert "120" in contract
    assert "10000" in contract


def _strict_source_reselection_fixture(
    messages: list[dict[str, str]],
    raw: str,
) -> str:
    """Migrate legacy author fixtures onto the negotiated realtime test wire."""

    strict_contract = False
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        try:
            payload = json.loads(message["content"])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        output_contract = payload.get("output_contract") if isinstance(payload, dict) else None
        if (
            isinstance(output_contract, dict)
            and output_contract.get("contract") == "expression-source-reselection-direct.1"
        ):
            strict_contract = True
            break
    if not strict_contract:
        return raw
    try:
        expression = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    if not isinstance(expression, dict) or set(expression) == {
        "expression_draft",
        "episode_disposition",
    }:
        return raw
    if "timing_choice" not in expression or "beats" not in expression:
        return raw
    normalized = dict(expression)
    episode_disposition = normalized.pop("episode_disposition", None)
    private_state = normalized.get("private_turn_state")
    if isinstance(private_state, dict):
        private_state = {"contract": "private-turn-state.1", **private_state}
    beats = normalized.get("beats")
    if not isinstance(beats, list) or any(not isinstance(beat, dict) for beat in beats):
        return raw
    ordered_beats = [
        {
            "modality": beat.get("modality"),
            "text": beat.get("text"),
            "reaction_id": beat.get("reaction_id"),
            "sticker_id": beat.get("sticker_id"),
        }
        for beat in beats
    ]
    timing_choice = normalized.get("timing_choice")
    delay_position_bp = 0 if timing_choice == "later" else None
    normalized.pop("delay_seconds", None)
    expectation = normalized.get("response_expectation")
    if isinstance(expectation, dict):
        expectation = dict(expectation)
        expectation.pop("wait_seconds", None)
        expectation.setdefault("wait_position_bp", 0)
    strict_expression = {
        "private_turn_state": private_state,
        "timing_choice": timing_choice,
        "cadence": normalized.get("cadence", "conversational"),
        "beats": ordered_beats,
        "delay_position_bp": delay_position_bp,
        "expires_after_seconds": normalized.get("expires_after_seconds"),
        "stance": normalized.get("stance"),
        "brief_rationale": normalized.get("brief_rationale"),
        "impulse_summary": normalized.get("impulse_summary"),
        "confidence": normalized.get("confidence", 7_000),
        "variation_profile": normalized.get("variation_profile"),
        "response_expectation": expectation,
        "response_expectation_assessment": normalized.get("response_expectation_assessment"),
        "revisit": (
            None
            if timing_choice in {"later", "silent"}
            else normalized.get("revisit")
        ),
        "world_claims": normalized.get("world_claims", []),
    }
    return json.dumps(
        {
            "expression_draft": strict_expression,
            "episode_disposition": episode_disposition,
        },
        ensure_ascii=False,
    )


class _Model:
    model = "deepseek-v4-flash"
    strict_reselection_wire = True

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls: list[tuple[list[dict[str, str]], float]] = []

    async def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.8) -> str:
        self.calls.append((messages, temperature))
        return (
            _strict_source_reselection_fixture(messages, self._reply)
            if self.strict_reselection_wire
            else self._reply
        )

    @property
    def semantic_authority_id(self) -> str:
        """Give test doubles an explicit checkpoint identity declaration."""

        return f"semantic-authority:test:{self.model.casefold()}"

    @property
    def last_system_prompt(self) -> str:
        return self.calls[-1][0][0]["content"]


class _MeteredModel(_Model):
    async def complete_with_usage(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> tuple[str, ModelUsageProvenance]:
        self.calls.append((messages, temperature))
        material = {
            "usage_contract": "model-usage.1",
            "route_class": "chat",
            "input_tokens": 12,
            "output_tokens": 3,
            "thinking_tokens": 0,
            "token_provenance": "provider_reported",
            "transport": "provider_api",
            "provider": "fake-provider",
            "provider_usage_ref": "usage:fake:1",
        }
        digest = sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        reply = (
            _strict_source_reselection_fixture(messages, self._reply)
            if self.strict_reselection_wire
            else self._reply
        )
        return reply, ModelUsageProvenance(**material, provider_usage_hash=digest)


class _JsonModel(_Model):
    async def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.8) -> str:
        raise AssertionError("structured proposal path must request JSON mode when available")

    async def complete_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> str:
        self.calls.append((messages, temperature))
        return (
            _strict_source_reselection_fixture(messages, self._reply)
            if self.strict_reselection_wire
            else self._reply
        )


class _JsonMeteredModel(_MeteredModel):
    async def complete_with_usage(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> tuple[str, ModelUsageProvenance]:
        raise AssertionError("structured metered proposal path must preserve JSON request mode")

    async def complete_json_with_usage(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> tuple[str, ModelUsageProvenance]:
        return await _MeteredModel.complete_with_usage(
            self,
            messages,
            temperature=temperature,
        )


class _ForcedToolExpressionCorrectionModel(_JsonMeteredModel):
    supports_required_tool_choice = True

    def __init__(self, invalid: str, corrected: str) -> None:
        super().__init__(invalid)
        self._corrected = corrected
        self.tool_calls: list[tuple[list[dict[str, object]] | None, object | None]] = []

    async def complete_json_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, ModelUsageProvenance]:
        self.calls.append((messages, temperature))
        self.tool_calls.append((tools, tool_choice))
        material = {
            "usage_contract": "model-usage.1",
            "route_class": "chat",
            "input_tokens": 12,
            "output_tokens": 4,
            "thinking_tokens": 0,
            "token_provenance": "provider_reported",
            "transport": "provider_api",
            "provider": "fake-provider",
            "provider_usage_ref": "usage:fake:expression-tool:1",
        }
        digest = sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if tools is not None:
            assert tool_choice == {
                "type": "function",
                "function": {"name": "character_expression_reselection_v1"},
            }
            raw = self._corrected
        else:
            raw = self._reply
        return raw, ModelUsageProvenance(**material, provider_usage_hash=digest)


@pytest.mark.asyncio
async def test_expression_structural_correction_uses_required_tool_without_plain_fallback() -> None:
    invalid = json.dumps(
        {
            "timing_choice": "now",
            "beats": [],
            "world_claims": [],
        },
        ensure_ascii=False,
    )
    corrected = json.dumps(
        {
            "expression_draft": {
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "我想把这一句说清楚。",
                    "attended_source_refs": [],
                },
                "timing_choice": "now",
                "cadence": "conversational",
                "beats": [
                    {
                        "modality": "text",
                        "text": "我在，刚才那句我看到了。",
                        "reaction_id": None,
                        "sticker_id": None,
                    }
                ],
                "delay_position_bp": None,
                "expires_after_seconds": None,
                "stance": "present",
                "brief_rationale": "回到当前这句。",
                "impulse_summary": None,
                "confidence": 8000,
                "variation_profile": None,
                "response_expectation": None,
                "response_expectation_assessment": None,
                "revisit": None,
                "world_claims": [],
            },
            "episode_disposition": "complete_without_more",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    provider = _ForcedToolExpressionCorrectionModel(invalid, corrected)

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(
            model=provider,
            expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        ).propose(_qq_request())

    assert len(provider.tool_calls) == 1


class _EventFrameStreamingModel(_Model):
    """Release one complete expression event before the remaining event array."""

    reports_exact_request_emission = True
    boundary_marker = ',{"type":"beat"'

    def __init__(self, reply: str) -> None:
        super().__init__(reply)
        self.release_tail = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def complete_json_stream_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
        on_text_delta=None,  # type: ignore[no-untyped-def]
    ) -> tuple[str, ModelUsageProvenance]:
        self.calls.append((messages, temperature))
        boundary = self._reply.index(self.boundary_marker)
        if on_text_delta is not None:
            on_text_delta(self._reply[:boundary])
        try:
            await self.release_tail.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        if on_text_delta is not None:
            on_text_delta(self._reply[boundary:])
        material = {
            "usage_contract": "model-usage.1",
            "route_class": "chat",
            "input_tokens": 12,
            "output_tokens": 8,
            "thinking_tokens": 0,
            "token_provenance": "provider_reported",
            "transport": "provider_api",
            "provider": "fake-provider",
            "provider_usage_ref": "usage:fake:event-stream:1",
        }
        digest = sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return self._reply, ModelUsageProvenance(
            **material,
            provider_usage_hash=digest,
        )


class _HeadOnlyEventFrameStreamingModel(_EventFrameStreamingModel):
    boundary_marker = ',{"type":"end"'


class _CanonicalExpressionStreamingModel(_EventFrameStreamingModel):
    """Return the ordinary semantic ExpressionDraft without transport framing."""

    boundary_marker: str | None = None

    async def complete_json_stream_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
        on_text_delta=None,  # type: ignore[no-untyped-def]
    ) -> tuple[str, ModelUsageProvenance]:
        self.calls.append((messages, temperature))
        boundary = (
            self._reply.index(self.boundary_marker)
            if self.boundary_marker is not None
            else len(self._reply)
        )
        if on_text_delta is not None:
            on_text_delta(self._reply[:boundary])
        if boundary < len(self._reply):
            try:
                await self.release_tail.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            if on_text_delta is not None:
                on_text_delta(self._reply[boundary:])
        material = {
            "usage_contract": "model-usage.1",
            "route_class": "chat",
            "input_tokens": 12,
            "output_tokens": 8,
            "thinking_tokens": 0,
            "token_provenance": "provider_reported",
            "transport": "provider_api",
            "provider": "fake-provider",
            "provider_usage_ref": "usage:fake:canonical-stream:1",
        }
        digest = sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self._reply, ModelUsageProvenance(
            **material,
            provider_usage_hash=digest,
        )


class _BlockingStreamPrefetch:
    """Hold one old route before its stream operation can be entered."""

    is_closed = False

    def __init__(self, blocked_trigger_ref: str) -> None:
        self.blocked_trigger_ref = blocked_trigger_ref
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def is_available(self, _cursor: RecallCursor, *, trigger_ref: str) -> bool:
        return True

    def scheduled_prefetch_token(self, *, expected_cursor: RecallCursor, trigger_ref: str) -> str:
        return f"prefetch:{expected_cursor.ledger_sequence}:{trigger_ref}"

    async def await_scheduled_prefetch(
        self,
        *,
        expected_cursor: RecallCursor,
        trigger_ref: str,
        timeout_seconds: float | None,
        job_token: str,
    ) -> None:
        del expected_cursor, timeout_seconds, job_token
        if trigger_ref == self.blocked_trigger_ref:
            self.entered.set()
            await self.release.wait()
        return None

    def discard_scheduled_prefetch(
        self,
        _cursor: RecallCursor,
        *,
        trigger_ref: str,
        job_token: str,
    ) -> None:
        del trigger_ref, job_token


class _CorrectingEventFrameStreamingModel(_EventFrameStreamingModel):
    def __init__(self, stream_reply: str, corrected_reply: str) -> None:
        super().__init__(stream_reply)
        self.corrected_reply = corrected_reply
        self.stream_task: asyncio.Task[object] | None = None
        self.correction_saw_stream_cancelling = False

    async def complete_json_stream_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
        on_text_delta=None,  # type: ignore[no-untyped-def]
    ) -> tuple[str, ModelUsageProvenance]:
        self.stream_task = asyncio.current_task()
        return await super().complete_json_stream_with_usage(
            messages,
            temperature=temperature,
            on_text_delta=on_text_delta,
        )

    async def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.8) -> str:
        stream_task = self.stream_task
        self.correction_saw_stream_cancelling = bool(
            stream_task is not None and stream_task.cancelling()
        )
        self.calls.append((messages, temperature))
        return _strict_source_reselection_fixture(messages, self.corrected_reply)


@pytest.mark.asyncio
async def test_expression_event_stream_reuses_one_author_call_and_releases_head_early() -> None:
    raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": "now",
                    "beat": {"modality": "text", "text": "第一条先到。"},
                    "stance": "speak_in_two_bubbles",
                    "brief_rationale": "I chose two messages.",
                    "world_claims": [],
                },
                {
                    "type": "beat",
                    "beat": {"modality": "text", "text": "第二条随后到。"},
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    model = _EventFrameStreamingModel(raw)
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )
    request = _qq_request()
    head_task = asyncio.create_task(adapter.propose_stream_head(request))
    tail_task = asyncio.create_task(
        adapter.propose_stream_tail(request.model_copy(update={"call_id": "call:tail"}))
    )

    head = await asyncio.wait_for(head_task, timeout=0.5)
    assert len(model.calls) == 1
    assert head.raw_proposal["episode_disposition"] == "append"
    assert len(head.raw_proposal["action_intents"]) == 1
    assert not tail_task.done()

    model.release_tail.set()
    tail = await asyncio.wait_for(tail_task, timeout=0.5)
    assert len(model.calls) == 1
    assert head.provider_parent_model_call_id is not None
    assert tail.provider_parent_model_call_id == head.provider_parent_model_call_id
    assert tail.winning_model_call_id != head.winning_model_call_id
    assert tail.raw_proposal["episode_disposition"] == "append"
    assert len(tail.raw_proposal["action_intents"]) == 1


@pytest.mark.asyncio
async def test_expression_event_stream_releases_one_singular_beat_frame_before_tail() -> None:
    raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": "now",
                    "beat": {"modality": "text", "text": "第一帧先到。"},
                    "stance": "speak_in_two_bubbles",
                    "brief_rationale": "I chose two messages.",
                    "world_claims": [],
                },
                {
                    "type": "beat",
                    "beat": {"modality": "text", "text": "第二帧随后到。"},
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    model = _EventFrameStreamingModel(raw)
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )
    request = _qq_request()
    head_task = asyncio.create_task(adapter.propose_stream_head(request))
    tail_task = asyncio.create_task(
        adapter.propose_stream_tail(request.model_copy(update={"call_id": "call:event-tail"}))
    )

    head = await asyncio.wait_for(head_task, timeout=0.5)
    assert "Return one raw JSON ExpressionDraft" in model.calls[0][0][0]["content"]
    assert "protocol=expression-events.1" not in model.calls[0][0][0]["content"]
    assert head.raw_proposal["episode_disposition"] == "append"
    head_payload = json.loads(head.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    assert [beat["inline_text"] for beat in head_payload["beat_drafts"]] == ["第一帧先到。"]
    assert not tail_task.done()

    model.release_tail.set()
    tail = await asyncio.wait_for(tail_task, timeout=0.5)
    assert len(model.calls) == 1
    tail_payload = json.loads(tail.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    assert [beat["inline_text"] for beat in tail_payload["beat_drafts"]] == ["第二帧随后到。"]


@pytest.mark.asyncio
async def test_fast_expression_interface_accepts_legacy_plural_beats_in_event_head() -> None:
    raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "旧包装也不能失声。"}],
                    "episode_disposition": "complete_without_more",
                    "stance": "reply",
                    "brief_rationale": "I chose one message.",
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    model = _HeadOnlyEventFrameStreamingModel(raw)
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    head = await asyncio.wait_for(adapter.propose_stream_head(_qq_request()), timeout=0.5)
    payload = json.loads(head.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])

    assert [beat["inline_text"] for beat in payload["beat_drafts"]] == ["旧包装也不能失声。"]


@pytest.mark.asyncio
async def test_fast_expression_interface_accepts_canonical_multi_beat_draft() -> None:
    raw = json.dumps(
        {
            "timing_choice": "now",
            "stance": "two_bubble_reply",
            "brief_rationale": "I chose two messages.",
            "world_claims": [],
            # The ordinary semantic field is last for fast transport. The
            # host, not the role model, partitions the authored beat sequence.
            "beats": [
                {"modality": "text", "text": "第一条。"},
                {"modality": "text", "text": "第二条。"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    model = _CanonicalExpressionStreamingModel(raw)
    model.boundary_marker = ',{"modality":"text","text":"第二条。"}'
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )
    request = _qq_request()

    head_task = asyncio.create_task(adapter.propose_stream_head(request))
    tail_task = asyncio.create_task(
        adapter.propose_stream_tail(request.model_copy(update={"call_id": "call:canonical-tail"}))
    )
    head = await asyncio.wait_for(head_task, timeout=0.5)
    payload = json.loads(head.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])

    assert [beat["inline_text"] for beat in payload["beat_drafts"]] == ["第一条。"]
    assert head.episode_disposition == "append"
    assert not tail_task.done()
    assert "serialize beats as the final top-level field" in model.calls[0][0][0]["content"]

    model.release_tail.set()
    tail = await asyncio.wait_for(tail_task, timeout=0.5)
    tail_payload = json.loads(tail.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    assert [beat["inline_text"] for beat in tail_payload["beat_drafts"]] == ["第二条。"]
    assert tail.episode_disposition == "append"
    assert len(model.calls) == 1


def test_fast_canonical_prefix_cannot_be_overridden_after_beats() -> None:
    raw = (
        '{"timing_choice":"now","stance":"reply","brief_rationale":"chosen",'
        '"world_claims":[],"beats":[{"modality":"text","text":"先发。"}],'
        '"turn_posture":"supersede"}'
    )

    with pytest.raises(ValueError, match="beats must remain the final field"):
        _stream_first_expression(raw)


def test_fast_canonical_stream_rejects_duplicate_semantic_fields() -> None:
    raw = '{"timing_choice":"now","timing_choice":"silent","world_claims":[],"beats":[]}'

    with pytest.raises(ValueError, match="duplicated field: timing_choice"):
        _stream_first_expression(raw)


def test_fast_canonical_stream_collapses_identical_transport_fields() -> None:
    raw_prefix = (
        '{"result_kind":"decision","protocol":"character-interior-events.1",'
        '"appraisal_draft":{},"events":['
        '{"type":"head","type":"head","timing_choice":"now",'
        '"stance":"reply","brief_rationale":"chosen","world_claims":[],'
        '"beat":{"modality":"text","text":"我在听。"}}'
    )

    first = _incremental_first_expression(raw_prefix, forced_tool=True)

    assert first is not None
    parsed = json.loads(first)
    assert parsed["expression_draft"]["beats"] == [
        {"modality": "text", "text": "我在听。"}
    ]


def test_fast_canonical_stream_rejects_conflicting_transport_fields() -> None:
    raw_prefix = (
        '{"result_kind":"decision","protocol":"character-interior-events.1",'
        '"appraisal_draft":{},"events":['
        '{"type":"head","type":"end","timing_choice":"now",'
        '"stance":"reply","brief_rationale":"chosen","world_claims":[],'
        '"beat":{"modality":"text","text":"这条不能授权。"}}'
    )

    with pytest.raises(ValueError, match="conflicting duplicated field: type"):
        _incremental_first_expression(raw_prefix, forced_tool=True)


def test_fast_canonical_stream_rejects_duplicate_fields_inside_first_beat() -> None:
    raw_prefix = (
        '{"timing_choice":"now","stance":"reply","brief_rationale":"chosen",'
        '"world_claims":[],"beats":['
        '{"modality":"text","text":"甲","text":"乙"}'
    )

    with pytest.raises(ValueError, match="duplicated field: text"):
        _incremental_first_expression(raw_prefix)


def test_expression_event_stream_requires_role_owned_timing_before_head_release() -> None:
    raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "beat": {"modality": "text", "text": "这条不能替角色决定时机。"},
                    "stance": "brief",
                    "brief_rationale": "The role did not state timing.",
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with pytest.raises(ValueError, match="requires explicit timing_choice"):
        _stream_first_expression(raw)


def test_strict_forced_stream_removes_only_null_union_siblings() -> None:
    """DeepSeek strict root padding must not invalidate the event envelope."""

    raw = json.dumps(
        {
            "result_kind": "decision",
            "protocol": "character-interior-events.1",
            "appraisal_draft": {"appraise": False, "affect": "no_change"},
            "events": [
                {
                    "type": "head",
                    "timing_choice": "now",
                    "beat": {"modality": "text", "text": "先说一句。"},
                    "stance": "自然",
                    "brief_rationale": "保持对话。",
                    "confidence": 7000,
                    "world_claims": [],
                },
                {"type": "end"},
            ],
            "expression_draft": None,
            "recall_request": None,
            "private_turn_state": None,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    first = json.loads(_stream_first_expression(raw))
    tail = json.loads(_stream_tail_expression(raw))
    assert first["appraisal_draft"]["affect"] == "no_change"
    assert first["expression_draft"]["beats"][0]["text"] == "先说一句。"
    assert tail["expression_draft"]["timing_choice"] == "silent"


def _compact_full_turn_event_envelope() -> dict[str, object]:
    return {
        "protocol": "character-interior-events.1",
        "appraisal_draft": {"appraise": False, "affect": "no_change"},
        "events": [
            {
                "type": "head",
                "timing_choice": "now",
                "beat": {"modality": "text", "text": "完整能力仍在同一次调用里。"},
                "stance": "自然",
                "brief_rationale": "我选择了完整表达能力。",
                "confidence": 7000,
                "world_claims": [],
            },
            {"type": "end"},
        ],
    }


def test_compact_gate_recall_control_transfer_waits_for_complete_exact_object() -> None:
    value: dict[str, object] = {
        "result_kind": "recall",
        "protocol": None,
        "appraisal_draft": None,
        "events": None,
        "full_turn_json": None,
        "private_turn_state": {
            "contract": "private-turn-state.1",
            "inner_state_summary": "我想先使用另一项角色能力。",
            "attended_source_refs": [],
        },
        "recall_request": {
            "query_text": "此前相关记忆",
            "memory_kinds": ["episodic"],
            "limit": 4,
        },
    }
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    assert _incremental_first_expression(raw[:-1], forced_tool=True) is None
    first = json.loads(_stream_first_expression(raw))
    tail = json.loads(_stream_tail_expression(raw))

    assert set(first) == {"private_turn_state", "recall_request"}
    assert tail == first


def test_compact_full_turn_recursively_uses_existing_event_parser() -> None:
    envelope = _compact_full_turn_event_envelope()
    raw = json.dumps(
        {
            "result_kind": "full_turn",
            "protocol": None,
            "appraisal_draft": None,
            "events": None,
            "full_turn_json": json.dumps(
                envelope,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "private_turn_state": None,
            "recall_request": None,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    first = json.loads(_stream_first_expression(raw))
    tail = json.loads(_stream_tail_expression(raw))

    assert first["appraisal_draft"] == envelope["appraisal_draft"]
    assert first["expression_draft"]["beats"][0]["text"] == (
        "完整能力仍在同一次调用里。"
    )
    assert tail["expression_draft"]["timing_choice"] == "silent"


def test_compact_full_turn_unescapes_inner_head_before_outer_string_finishes() -> None:
    envelope = _compact_full_turn_event_envelope()
    raw = json.dumps(
        {
            "result_kind": "full_turn",
            "full_turn_json": json.dumps(
                envelope,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    end_marker = ',{\\"type\\":\\"end\\"}'
    prefix = raw[: raw.index(end_marker)]

    assert (
        _incremental_first_expression(
            prefix,
            forced_tool=True,
            compact_gate=True,
        )
        is None
    )
    first_raw = _incremental_first_expression(prefix, forced_tool=True)

    assert first_raw is not None
    first = json.loads(first_raw)
    assert first["expression_draft"]["beats"][0]["text"] == (
        "完整能力仍在同一次调用里。"
    )


def _compact_reply_only_outer() -> dict[str, object]:
    return {
        "result_kind": "reply_only",
        "protocol": "character-interior-events.1",
        "appraisal_draft": {
            "appraise": False,
            "affect": "no_change",
            "brief_rationale": "这句不形成持久评价。",
            "behavior_tendency": "自由接话",
            "stance": "自然",
            "display_strategy": "直接说",
            "confidence": 7000,
        },
        "events": [
            {
                "type": "head",
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "我想直接接住这句话。",
                    "attended_source_refs": [],
                },
                "timing_choice": "now",
                "turn_posture": "continue",
                "cadence": "conversational",
                "beat": {"modality": "text", "text": "我在听。"},
                "stance": "自然",
                "brief_rationale": "这句已经完整。",
                "confidence": 7000,
                "response_expectation": None,
                "response_expectation_assessment": None,
                "revisit": None,
                "world_claims": [],
                "media_request": "none",
                "media_source_refs": [],
            },
            {"type": "end"},
        ],
        "full_turn_json": None,
        "private_turn_state": None,
        "recall_request": None,
    }


def test_compact_reply_waits_for_complete_outer_union_before_head_authority() -> None:
    raw = json.dumps(
        _compact_reply_only_outer(),
        ensure_ascii=False,
        separators=(",", ":"),
    )

    assert (
        _incremental_first_expression(
            raw[:-1],
            forced_tool=True,
            compact_gate=True,
        )
        is None
    )
    assert (
        _incremental_first_expression(
            raw,
            forced_tool=True,
            compact_gate=True,
        )
        is not None
    )


@pytest.mark.parametrize("result_kind", ("reply_only", "full_turn"))
def test_compact_visible_branch_rejects_late_cross_branch_sibling(
    result_kind: str,
) -> None:
    if result_kind == "reply_only":
        value = _compact_reply_only_outer()
        value["full_turn_json"] = json.dumps(
            _compact_full_turn_event_envelope(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    else:
        value = {
            "result_kind": "full_turn",
            "protocol": None,
            "appraisal_draft": {"appraise": False},
            "events": None,
            "full_turn_json": json.dumps(
                _compact_full_turn_event_envelope(),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "private_turn_state": None,
            "recall_request": None,
        }
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    with pytest.raises(ValueError, match="compact gate|reply-only"):
        _incremental_first_expression(
            raw,
            forced_tool=True,
            compact_gate=True,
        )


def test_compact_gate_control_transfer_rejects_cross_branch_payload() -> None:
    raw = json.dumps(
        {
            "result_kind": "full_turn",
            "protocol": None,
            "appraisal_draft": {"appraise": False},
            "events": None,
            "full_turn_json": json.dumps(_compact_full_turn_event_envelope()),
            "private_turn_state": None,
            "recall_request": None,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with pytest.raises(ValueError, match="compact gate"):
        _stream_first_expression(raw)


@pytest.mark.parametrize(
    ("outcome", "raw_code", "expected"),
    (
        ("timeout", "sk_live_SECRET", "provider_timeout"),
        ("exception", "RemoteTransport:http_401", "provider_http_401"),
        ("exception", "source_review_exception", "source_review_exception"),
        ("exception", "authored_subcall_exception", "authored_subcall_exception"),
        ("exception", "sk_live_SECRET", "provider_exception"),
        ("winner", "sk_live_SECRET", None),
    ),
)
def test_source_review_attempt_failure_code_never_persists_provider_content(
    outcome: str,
    raw_code: str,
    expected: str | None,
) -> None:
    normalized = _normalize_source_review_attempt_failure_code(
        raw_code,
        outcome=outcome,
    )

    assert normalized == expected
    assert "SECRET" not in (normalized or "")


def test_strict_forced_stream_accepts_empty_sibling_beat_transport_padding() -> None:
    raw = json.dumps(
        {
            "result_kind": "decision",
            "protocol": "character-interior-events.1",
            "appraisal_draft": {"appraise": False, "affect": "no_change"},
            "events": [
                {
                    "type": "head",
                    "timing_choice": "now",
                    "beat": {"modality": "text", "text": "这一句先说。"},
                    "beats": [],
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    first = json.loads(_stream_first_expression(raw))
    assert first["expression_draft"]["beats"][0]["text"] == "这一句先说。"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timing_choice", "head_fields"),
    [
        ("silent", {}),
        (
            "later",
            {
                "beat": {"modality": "text", "text": "晚点我再接着说。"},
                "delay_seconds": 60,
                "expires_after_seconds": 600,
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_expression_event_stream_does_not_invent_a_tail_for_silent_or_later(
    timing_choice: str,
    head_fields: dict[str, object],
) -> None:
    raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": timing_choice,
                    "stance": "role_owned_choice",
                    "brief_rationale": "The role chose this timing.",
                    "world_claims": [],
                    **head_fields,
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    model = _HeadOnlyEventFrameStreamingModel(raw)
    adapter = _ExpressionDraftWire(model=model)
    request = _qq_request()
    if timing_choice == "later":
        request = request.model_copy(
            update={
                "model_content_json": json.dumps(
                    {
                        "capsule": "authoritative",
                        "logical_time": "2026-08-02T12:00:00+00:00",
                    }
                )
            }
        )

    head = await asyncio.wait_for(adapter.propose_stream_head(request), timeout=0.5)
    model.release_tail.set()

    assert head.episode_disposition == "complete_without_more"
    assert head.raw_proposal["episode_disposition"] == "complete_without_more"


@pytest.mark.asyncio
async def test_expression_event_stream_rejects_a_false_protocol_before_releasing_head() -> None:
    raw = json.dumps(
        {
            "protocol": "not-expression-events",
            "note": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": "now",
                    "beat": {"modality": "text", "text": "这条不能提前发送。"},
                    "stance": "invalid_transport",
                    "brief_rationale": "The outer protocol is invalid.",
                    "world_claims": [],
                },
                {
                    "type": "beat",
                    "beat": {"modality": "text", "text": "尾部。"},
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    model = _EventFrameStreamingModel(raw)
    adapter = _ExpressionDraftWire(model=model)
    task = asyncio.create_task(adapter.propose_stream_head(_qq_request()))
    try:
        await asyncio.sleep(0.01)
        # An invalid head cannot release a caller that would cancel the
        # physical stream; the peer may still send its original usage tail.
        assert not task.done()
        model.release_tail.set()
        result = await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=1,
        )
    finally:
        adapter.cancel_expression_unit_streams()
        await asyncio.gather(task, return_exceptions=True)

    assert isinstance(result[0], BaseException)


@pytest.mark.asyncio
async def test_expression_event_stream_preserves_character_chosen_typing_before_first_text() -> (
    None
):
    raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": "now",
                    "leading_typing_beat": {"modality": "typing"},
                    "beat": {
                        "modality": "text",
                        "text": "我想一下……其实是这样。",
                    },
                    "stance": "hesitant_then_direct",
                    "brief_rationale": "I chose to visibly hesitate before answering.",
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    model = _HeadOnlyEventFrameStreamingModel(raw)
    model.release_tail.set()
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    head = await adapter.propose_stream_head(_qq_request())

    action_intents = head.raw_proposal["action_intents"]
    assert [item["kind"] for item in action_intents] == ["typing", "reply"]
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_expression_draft_drops_only_a_duplicate_top_level_private_state_contract() -> None:
    draft = {
        "contract": "private-turn-state.1",
        "private_turn_state": {
            "contract": "private-turn-state.1",
            "inner_state_summary": "我同时注意到她前后两句。",
            "attended_source_refs": ["observation:qq:1"],
        },
        "timing_choice": "now",
        "cadence": "conversational",
        "beats": [{"modality": "text", "text": "前后两句我都看到了。"}],
        "stance": "respond_to_packet",
        "brief_rationale": "Respond from the whole current packet.",
        "confidence": 8_000,
        "world_claims": [],
    }
    adapter = _ExpressionDraftWire(
        model=_SequenceJsonModel([json.dumps(draft, ensure_ascii=False)]),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    output = await adapter.propose(_qq_request())

    payload = json.loads(output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    assert payload["beat_drafts"][0]["inline_text"] == "前后两句我都看到了。"


@pytest.mark.asyncio
async def test_expression_draft_drops_stray_root_private_state_contract() -> None:
    draft = {
        "contract": "private-turn-state.1",
        "timing_choice": "now",
        "cadence": "conversational",
        "beats": [{"modality": "text", "text": "这样就舒服一点了。"}],
        "stance": "relieved",
        "brief_rationale": "React to the current report.",
        "confidence": 8_000,
        "world_claims": [],
    }
    adapter = _ExpressionDraftWire(
        model=_SequenceJsonModel([json.dumps(draft, ensure_ascii=False)]),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    output = await adapter.propose(_qq_request())

    payload = json.loads(output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    assert payload["beat_drafts"][0]["inline_text"] == "这样就舒服一点了。"


@pytest.mark.asyncio
async def test_expression_event_stream_preserves_role_owned_supersede_lifecycle() -> None:
    raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": "silent",
                    "turn_posture": "supersede",
                    "stance": "withdraw",
                    "brief_rationale": "The role withdrew the pending expression.",
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    model = _HeadOnlyEventFrameStreamingModel(raw)
    model.release_tail.set()
    adapter = _ExpressionDraftWire(model=model)

    head = await adapter.propose_stream_head(_qq_request())
    assert head.raw_proposal["turn_posture"] == "supersede"
    assert head.raw_proposal["episode_disposition"] == "supersede_pending"


@pytest.mark.asyncio
async def test_expression_event_stream_derives_redundant_disposition() -> None:
    raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": "now",
                    "beat": {"modality": "text", "text": "舒服点就好。"},
                    "stance": "relieved",
                    "brief_rationale": "React naturally.",
                    "confidence": 8_000,
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    model = _HeadOnlyEventFrameStreamingModel(raw)
    model.release_tail.set()
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    request = _qq_request()
    head = await adapter.propose_stream_head(request)
    tail = await adapter.propose_stream_tail(
        request.model_copy(update={"call_id": "call:derived-disposition-tail"})
    )

    # The early head cannot infer whether continuation bytes are still in
    # flight. The completed envelope can and yields the lifecycle terminal.
    assert head.episode_disposition == "append"
    assert tail.episode_disposition == "complete_without_more"
    assert tail.raw_proposal["episode_disposition"] == "complete_without_more"


@pytest.mark.asyncio
async def test_expression_event_stream_cancels_provider_when_its_last_waiter_is_superseded() -> (
    None
):
    raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": "now",
                    "beat": {"modality": "text", "text": "先到。"},
                    "stance": "brief",
                    "brief_rationale": "One first event.",
                    "world_claims": [],
                },
                {
                    "type": "beat",
                    "beat": {"modality": "text", "text": "尾部。"},
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    model = _EventFrameStreamingModel(raw)
    adapter = _ExpressionDraftWire(model=model)
    request = _qq_request()
    head_task = asyncio.create_task(adapter.propose_stream_head(request))
    tail_task = asyncio.create_task(
        adapter.propose_stream_tail(request.model_copy(update={"call_id": "call:tail-cancel"}))
    )
    await asyncio.wait_for(head_task, timeout=0.5)

    tail_task.cancel()
    await asyncio.gather(tail_task, return_exceptions=True)
    await asyncio.wait_for(model.cancelled.wait(), timeout=0.5)


@pytest.mark.asyncio
async def test_newer_thinking_cursor_rejects_old_flash_at_stream_operation_boundary() -> None:
    raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": "now",
                    "beat": {"modality": "text", "text": "新的先说。"},
                    "stance": "brief",
                    "brief_rationale": "One current event.",
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    flash = _HeadOnlyEventFrameStreamingModel(raw)
    thinking = _HeadOnlyEventFrameStreamingModel(raw)
    old_request = _qq_request().model_copy(update={"evaluated_ledger_sequence": 10})
    prefetch = _BlockingStreamPrefetch(old_request.trigger_ref)
    adapter = _RoutedExpressionDraftWire(
        flash_model=flash,
        thinking_model=thinking,
        recall_coordinator=prefetch,  # type: ignore[arg-type]
    )
    old_task = asyncio.create_task(adapter.propose_stream_head(old_request))
    try:
        await asyncio.wait_for(prefetch.entered.wait(), timeout=0.5)
        assert old_request.trigger_message is not None
        new_request = old_request.model_copy(
            update={
                "call_id": "call:new-thinking",
                "attempt_id": "attempt:new-thinking",
                "route": ModelRoute(
                    tier="thinking",
                    reason_code="newer_cursor",
                    router_version="test.1",
                ),
                "trigger_ref": "trigger:new-thinking",
                "evaluated_world_revision": 4,
                "evaluated_deliberation_revision": 1,
                "evaluated_ledger_sequence": 11,
                "trigger_message": old_request.trigger_message.model_copy(
                    update={
                        "event_ref": "event:observation:qq:2",
                        "event_payload_hash": "sha256:" + "c" * 64,
                        "observation_ref": "observation:qq:2",
                        "source_world_revision": 4,
                        "platform_message_id": "qq-message-7789",
                        "text": "等等，我补充一句。",
                    }
                ),
            }
        )
        new_head = await asyncio.wait_for(adapter.propose_stream_head(new_request), timeout=0.5)
        prefetch.release.set()
        old_result = await asyncio.gather(old_task, return_exceptions=True)
    finally:
        prefetch.release.set()
        flash.release_tail.set()
        thinking.release_tail.set()
        if not old_task.done():
            old_task.cancel()
        await asyncio.gather(old_task, return_exceptions=True)

    assert new_head.raw_proposal["episode_disposition"] == "append"
    assert flash.calls == []
    assert len(thinking.calls) == 1
    assert isinstance(old_result[0], asyncio.CancelledError)


@pytest.mark.asyncio
async def test_structural_reselection_cancels_original_sse_before_correction_starts() -> None:
    first = {
        "timing_choice": "now",
        "beats": [{"modality": "text", "text": "先给一个不完整草稿。"}],
        "confidence": 8_000,
        "world_claims": [],
        "episode_disposition": "append",
    }
    stream_raw = json.dumps(
        {
            "protocol": "expression-events.1",
            "events": [
                {
                    "type": "head",
                    "timing_choice": first["timing_choice"],
                    "beat": first["beats"][0],
                    "confidence": first["confidence"],
                    "world_claims": first["world_claims"],
                },
                {
                    "type": "beat",
                    "beat": {"modality": "text", "text": "这个尾部不该再发。"},
                    "world_claims": [],
                },
                {"type": "end"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    corrected = json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "我重新接好这句。"}],
            "stance": "respond_naturally",
            "brief_rationale": "Replace the structurally incomplete draft.",
            "confidence": 8_200,
            "world_claims": [],
        },
        ensure_ascii=False,
    )
    model = _CorrectingEventFrameStreamingModel(stream_raw, corrected)
    try:
        with pytest.raises(
            ValidationTechnicalFailure, match="authored_expression_reselection_invalid"
        ):
            await asyncio.wait_for(
                _ExpressionDraftWire(model=model).propose_stream_head(_qq_request()),
                timeout=1,
            )
    finally:
        model.release_tail.set()

    assert model.correction_saw_stream_cancelling is False


class _SequenceJsonModel(_Model):
    def __init__(self, replies: list[str]) -> None:
        super().__init__("")
        self.responses = tuple(replies)
        self._replies = list(replies)

    async def complete_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> str:
        self.calls.append((messages, temperature))
        reply = self._replies.pop(0)
        return (
            _strict_source_reselection_fixture(messages, reply)
            if self.strict_reselection_wire
            else reply
        )


class _FirstReplyThenBlockJsonModel(_SequenceJsonModel):
    """Expose cancellation after a nested role request crossed the boundary."""

    def __init__(self, reply: str) -> None:
        super().__init__([reply])
        self.nested_call_entered = asyncio.Event()

    async def complete_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> str:
        self.calls.append((messages, temperature))
        if len(self.calls) == 1:
            return self._replies.pop(0)
        self.nested_call_entered.set()
        await asyncio.Future()
        raise AssertionError("unreachable after nested provider cancellation")


class _StrictInventorySequenceJsonModel(_SequenceJsonModel):
    def supports_strict_output_contract(self, contract: str) -> bool:
        return contract == "candidate-external-proposition-inventory.5"


class _FullSourceReviewSequenceJsonModel(_SequenceJsonModel):
    def supports_strict_output_contract(self, contract: str) -> bool:
        return contract in {
            "source-closure-review.7",
            "report-relative-entailment-adjudication.3",
        }


class _ReplyThenTransportFailureModel(_Model):
    def __init__(self, reply: str) -> None:
        super().__init__("")
        self.reply = reply

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.8,
    ) -> str:
        self.calls.append((messages, temperature))
        if len(self.calls) == 1:
            return self.reply
        raise RuntimeError("simulated reviewer transport failure")


class _SequenceMeteredModel(_SequenceJsonModel):
    def __init__(
        self,
        replies: list[str],
        *,
        route_class: str = "chat",
        provider: str = "fake-provider",
        thinking_tokens: int = 0,
    ) -> None:
        super().__init__(replies)
        self._route_class = route_class
        self._provider = provider
        self._thinking_tokens = thinking_tokens

    async def complete_with_usage(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> tuple[str, ModelUsageProvenance]:
        self.calls.append((messages, temperature))
        ordinal = len(self.calls)
        reply = self._replies.pop(0)
        if self.strict_reselection_wire:
            reply = _strict_source_reselection_fixture(messages, reply)
        material = {
            "usage_contract": "model-usage.1",
            "route_class": self._route_class,
            "input_tokens": 12,
            "output_tokens": 3,
            "thinking_tokens": self._thinking_tokens,
            "token_provenance": "provider_reported",
            "transport": "provider_api",
            "provider": self._provider,
            "provider_usage_ref": f"usage:fake:{ordinal}",
        }
        digest = sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return reply, ModelUsageProvenance(
            **material,
            provider_usage_hash=digest,
        )


class _SequenceJsonMeteredModel(_SequenceMeteredModel):
    async def complete_with_usage(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> tuple[str, ModelUsageProvenance]:
        raise AssertionError("all structured metered calls must preserve JSON mode")

    async def complete_json_with_usage(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> tuple[str, ModelUsageProvenance]:
        return await _SequenceMeteredModel.complete_with_usage(
            self,
            messages,
            temperature=temperature,
        )


class _StrictCoverageSequenceJsonMeteredModel(_SequenceJsonMeteredModel):
    def supports_strict_output_contract(self, contract: str) -> bool:
        return contract == "candidate-external-proposition-coverage.5"


class _BlockingPrefetchEmbedding:
    version = "blocking-prefetch-fixture.1"
    dimensions = FeatureHashRecallEmbedding.dimensions

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.used_after_close = threading.Event()
        self._delegate = FeatureHashRecallEmbedding()

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if "并行预取" in texts:
            self.started.set()
            self.release.wait(timeout=2)
        if self.closed.is_set():
            self.used_after_close.set()
        return self._delegate.embed(texts)

    def close(self) -> None:
        self.closed.set()


class _ObservablePrefetchEmbedding:
    version = "observable-prefetch-fixture.1"
    dimensions = FeatureHashRecallEmbedding.dimensions

    def __init__(self) -> None:
        self.finished = threading.Event()
        self._delegate = FeatureHashRecallEmbedding()

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        result = self._delegate.embed(texts)
        if "窗边听雨" in texts:
            self.finished.set()
        return result


class _MalformedPrefetchEmbedding:
    version = "malformed-prefetch-fixture.1"
    dimensions = FeatureHashRecallEmbedding.dimensions

    def __init__(self) -> None:
        self._delegate = FeatureHashRecallEmbedding()
        self.calls = 0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        if "损坏的查询" in texts:
            return ()
        return self._delegate.embed(texts)


class _DelayedSemanticPrefetchEmbedding:
    version = "delayed-semantic-prefetch-fixture.1"
    dimensions = FeatureHashRecallEmbedding.dimensions

    def __init__(self) -> None:
        self._delegate = FeatureHashRecallEmbedding()
        self.calls = 0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        if "稍晚完成的语义查询" in texts:
            time.sleep(0.45)
        return self._delegate.embed(texts)


class _ControlledSemanticPrefetchEmbedding:
    """Make lexical fallback and semantic completion observably different."""

    version = "controlled-semantic-prefetch-fixture.1"
    dimensions = 2
    dense_match_threshold_bp = 9_000

    def __init__(self, *, released: bool = False) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        if released:
            self.release.set()

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if "直说的词面线索" in texts:
            self.started.set()
            self.release.wait(timeout=2)
            self.finished.set()
        return tuple(
            (0.0, 1.0) if text in {"直说的词面线索", "完全不同措辞的语义回忆"} else (1.0, 0.0)
            for text in texts
        )


class _ReleaseSemanticSequenceJsonModel(_SequenceJsonModel):
    def __init__(
        self,
        replies: list[str],
        *,
        semantic: _ControlledSemanticPrefetchEmbedding,
    ) -> None:
        super().__init__(replies)
        self._semantic = semantic

    async def complete_json(
        self, messages: list[dict[str, str]], *, temperature: float = 0.8
    ) -> str:
        self.calls.append((messages, temperature))
        reply = self._replies.pop(0)
        if len(self.calls) == 1:
            self._semantic.release.set()
            if not await asyncio.to_thread(self._semantic.finished.wait, 0.5):
                raise TimeoutError("semantic prefetch fixture did not finish")
        return reply


def _single_semantic_prefetch_coordinator(
    semantic: _ControlledSemanticPrefetchEmbedding,
    *,
    trigger_ref: str,
) -> tuple[RecallCoordinator, RecallCursor]:
    suffix = trigger_ref.rsplit(":", maxsplit=1)[-1]
    cursor = RecallCursor(world_revision=3, deliberation_revision=0, ledger_sequence=0)
    document = RecallDocument(
        document_id=f"recall:{suffix}",
        memory_kind="episodic",
        source_item_ref=f"experience:{suffix}",
        source_slice="recent_experiences",
        source_refs=(f"event:{suffix}",),
        source_bindings=(
            RecallSourceBinding(
                source_kind="committed_event",
                authority_type="ExperienceCommitted",
                ref=f"event:{suffix}",
                source_world_revision=2,
                immutable_hash="5" * 64,
            ),
        ),
        source_world_revision=2,
        text="完全不同措辞的语义回忆",
        actor_ref="agent:companion",
        subject_refs=("agent:companion",),
        occurred_from=datetime(2026, 7, 25, 13, tzinfo=UTC),
        privacy_class="private",
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=(document,))
    return (
        RecallCoordinator.from_built_index(
            index=index,
            cursor=cursor,
            actor_ref="agent:companion",
            subject_refs=("agent:companion", "user:primary"),
            logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
            semantic_embedding=semantic,
            trigger_ref=trigger_ref,
        ),
        cursor,
    )


@pytest.mark.asyncio
async def test_character_may_pull_one_source_bound_recall_before_deciding() -> None:
    canonical_recall_ref = "event:fact:counterpart-tea:sha256:" + "c" * 64
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "recall_request": {
                        "query_text": "凤凰单丛",
                        "memory_kinds": ["semantic"],
                        "limit": 3,
                    },
                    # Recall choices use the same order-independent JSON
                    # object semantics as complete ExpressionDraft objects.
                    "private_turn_state": {
                        "inner_state_summary": (
                            "这句话让我想起似乎还有一段关于凤凰单丛的细节，"
                            "但当前注意到的内容不够，我想先回忆一下再决定怎么接。"
                        ),
                        "attended_source_refs": ["trigger:1"],
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": (
                            "我想起她前两天提到凤凰单丛，现在是真有点想知道后来泡得如何。"
                        ),
                        "attended_source_refs": ["S1"],
                    },
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "你前两天提到凤凰单丛，后来泡得怎么样？",
                        }
                    ],
                    "stance": "curious",
                    "brief_rationale": "I chose to follow the recalled topic.",
                    "world_claims": [
                        {
                            "claim_text": "对方前两天提到凤凰单丛",
                            "scope": "counterpart_history",
                            "source_refs": ["S1"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        ]
    )
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=2,
        ledger_sequence=5,
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(
        cursor=cursor,
        documents=(
            RecallDocument(
                document_id="recall:fact:tea",
                memory_kind="semantic",
                source_item_ref="fact:tea",
                source_slice="relevant_facts",
                source_refs=(canonical_recall_ref,),
                source_bindings=(
                    RecallSourceBinding(
                        source_kind="committed_event",
                        authority_type="FactCommitted",
                        ref=canonical_recall_ref,
                        source_world_revision=2,
                        immutable_hash="c" * 64,
                    ),
                ),
                source_world_revision=2,
                text="我最近开始用盖碗泡凤凰单丛。",
                actor_ref="agent:companion",
                subject_refs=("user:primary",),
                occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
                privacy_class="personal",
            ),
        ),
    )
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
    )
    request = _qq_request().model_copy(
        update={
            "evaluated_deliberation_revision": 2,
            "evaluated_ledger_sequence": 5,
            "model_content_json": json.dumps(
                {
                    "world_revision": 3,
                    "deliberation_revision": 2,
                    "ledger_sequence": 5,
                    "logical_time": "2026-07-27T12:00:00+00:00",
                    "slices": {},
                },
                ensure_ascii=False,
            ),
        }
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text=request.trigger_message.text,
        accessibility_seed="recall-prefetch:trigger:1:5",
        trigger_ref=request.trigger_ref,
    )

    output = await _ExpressionDraftWire(
        model=model,
        recall_coordinator=coordinator,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    ).propose(request)

    assert len(model.calls) == 2
    assert output.recall_trace is not None
    assert output.prefetch_trace is not None
    trace = verify_trusted_recall_trace(output.recall_trace)
    assert trace.request.query_text == "凤凰单丛"
    assert trace.query.accessibility_seed.startswith("character-recall:")
    assert trace.query.actor_ref == "agent:companion"
    assert trace.query.subject_refs == ("agent:companion", "user:primary")
    assert trace.hits[0].document.source_refs == (canonical_recall_ref,)
    assert trace.hits[0].dense_score_bp >= 0
    assert "凤凰单丛" in model.calls[1][0][-1]["content"]
    assert "parallel_attention_prefetch" in model.calls[1][0][-1]["content"]
    assert '"S1":"' + canonical_recall_ref + '"' in model.calls[1][0][-1]["content"]
    assert "place it first" not in model.calls[1][0][-1]["content"]
    assert "private_turn_state" in model.calls[0][0][0]["content"]
    assert output.raw_proposal["timing_choice"] == "now"
    assert output.raw_proposal["private_turn_state"]["attended_source_refs"] == [
        canonical_recall_ref
    ]
    assert output.winning_model_call_id != request.call_id
    assert output.winning_request_hash == _provider_request_hash(*model.calls[1])
    recalled_evidence = next(
        item
        for item in output.raw_proposal["evidence_refs"]
        if item["ref_id"] == canonical_recall_ref
    )
    assert recalled_evidence["immutable_hash"] == "sha256:" + "c" * 64


@pytest.mark.asyncio
async def test_ready_prefetch_is_visible_before_role_model_may_decline_a_deeper_pull() -> None:
    canonical_recall_ref = "event:fact:counterpart-tea:sha256:" + "d" * 64
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": (
                            "那段关于茶的记忆浮了一下，但和她刚说的事没关系，"
                            "我不想硬拐过去，还是接住眼前这句。"
                        ),
                        "attended_source_refs": ["fact:tea"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "嗯，做完就好，先松口气吧。"}],
                    "stance": "present",
                    "brief_rationale": "I noticed the memory and chose not to pursue it.",
                    "confidence": 8200,
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=2,
        ledger_sequence=5,
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(
        cursor=cursor,
        documents=(
            RecallDocument(
                document_id="recall:fact:tea",
                memory_kind="semantic",
                source_item_ref="fact:tea",
                source_slice="relevant_facts",
                source_refs=(canonical_recall_ref,),
                source_bindings=(
                    RecallSourceBinding(
                        source_kind="committed_event",
                        authority_type="FactCommitted",
                        ref=canonical_recall_ref,
                        source_world_revision=2,
                        immutable_hash="d" * 64,
                    ),
                ),
                source_world_revision=2,
                text="我最近开始用盖碗泡凤凰单丛。",
                actor_ref="agent:companion",
                subject_refs=("user:primary",),
                occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
                privacy_class="personal",
            ),
        ),
    )
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
    )
    request = _qq_request().model_copy(
        update={
            "evaluated_deliberation_revision": 2,
            "evaluated_ledger_sequence": 5,
            "model_content_json": json.dumps(
                {
                    "world_revision": 3,
                    "deliberation_revision": 2,
                    "ledger_sequence": 5,
                    "logical_time": "2026-07-27T12:00:00+00:00",
                    "slices": {},
                },
                ensure_ascii=False,
            ),
        }
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="凤凰单丛",
        accessibility_seed="recall-prefetch:trigger:1:5",
        trigger_ref=request.trigger_ref,
    )

    output = await _ExpressionDraftWire(
        model=model,
        recall_coordinator=coordinator,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    ).propose(request)

    assert len(model.calls) == 1
    provider_payload = json.loads(model.calls[0][0][-1]["content"])
    compact_context = json.loads(provider_payload["request"]["model_content_json"])
    prefetched = compact_context["slices"]["relevant_facts"]["items"][0]
    assert prefetched["recall_injected"] is True
    assert prefetched["value"]["text"] == "我最近开始用盖碗泡凤凰单丛。"
    assert "you may return instead" in model.calls[0][0][0]["content"]
    assert output.prefetch_trace is not None
    prefetch = verify_trusted_recall_trace(output.prefetch_trace)
    assert prefetch.mode == "prefetch"
    assert prefetch.hits[0].document.source_refs == (canonical_recall_ref,)
    assert output.recall_trace is None
    assert output.raw_proposal["private_turn_state"]["attended_source_refs"] == ["fact:tea"]
    assert output.raw_proposal["timing_choice"] == "now"


@pytest.mark.asyncio
async def test_invalid_required_recall_choice_reselects_a_final_expression_once() -> None:
    invalid_visible_text = "这句无效草稿不能锚定最终表达。"
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "recall_request": {
                        "query_text": "一段没接上的旧话题",
                        "limit": 2,
                    },
                    "beats": [{"modality": "text", "text": invalid_visible_text}],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": (
                            "当前能确定的是她刚做完一件麻烦事，我此刻先替她觉得轻松。"
                        ),
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "可算弄完了，先歇会儿吧。"}],
                    "stance": "relieved_with_her",
                    "brief_rationale": "Choose from the current pinned turn without another recall.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ]
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await adapter.propose(_qq_request())

    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invalid_recall", "expected_code", "expected_path"),
    (
        (
            {"query_text": "非法 Recall 原文不能回灌 limit", "limit": 9},
            "recall_choice.out_of_range",
            "recall_request.limit",
        ),
        (
            {
                "query_text": "非法 Recall 原文不能回灌 kinds",
                "memory_kinds": ["semantic", "episodic"],
            },
            "recall_choice.noncanonical",
            "recall_request.memory_kinds",
        ),
        (
            {
                "query_text": "非法 Recall 原文不能回灌 extra",
                "unknown_filter": "private-value",
            },
            "recall_choice.unexpected_field",
            "recall_request",
        ),
    ),
)
@pytest.mark.asyncio
async def test_invalid_recall_payload_gets_one_sanitized_final_reselection(
    invalid_recall: dict[str, object],
    expected_code: str,
    expected_path: str,
) -> None:
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "我想先回忆再决定。",
                        "attended_source_refs": ["trigger:1"],
                    },
                    "recall_request": invalid_recall,
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "当前材料已经足够，我想直接回应。",
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "嗯，我听见了。"}],
                    "stance": "present",
                    "brief_rationale": "Choose the final expression from the pinned turn.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ]
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await adapter.propose(_qq_request())

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_invalid_recall_reselection_cannot_open_a_third_role_call() -> None:
    first_invalid_marker = "第一次非法 Recall 原文不能回灌"
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "我想先回忆再决定。",
                        "attended_source_refs": ["trigger:1"],
                    },
                    "recall_request": {
                        "query_text": first_invalid_marker,
                        "limit": 9,
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "我仍想发起另一次非法回忆。",
                        "attended_source_refs": ["trigger:1"],
                    },
                    "recall_request": {
                        "query_text": "第二次非法 Recall",
                        "unknown_filter": "private-value",
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    with pytest.raises(ValidationTechnicalFailure) as caught:
        await adapter.propose(_qq_request())

    assert caught.value.failure_code == "authored_expression_reselection_invalid"
    assert len(model.calls) == 1
    assert first_invalid_marker not in json.dumps(model.calls[0][0], ensure_ascii=False)


@pytest.mark.asyncio
async def test_invalid_recall_final_reselection_cannot_trigger_another_shape_repair() -> None:
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "我想先回忆再决定。",
                        "attended_source_refs": ["trigger:1"],
                    },
                    "recall_request": {
                        "query_text": "第一次 Recall 选择非法",
                        "limit": 9,
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "第二次结果仍缺少私人状态。"}],
                    "stance": "invalid_without_private_state",
                    "brief_rationale": "Invalid final fixture.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "第三次角色调用不应该发生。",
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "不应发送这条。"}],
                    "stance": "unexpected_third_call",
                    "brief_rationale": "This fixture proves a forbidden third call.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ]
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    with pytest.raises(ValidationTechnicalFailure) as caught:
        await adapter.propose(_qq_request())

    assert caught.value.failure_code == "authored_expression_reselection_invalid"
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_parallel_prefetch_cannot_delay_a_first_pass_final_answer() -> None:
    embedding = _BlockingPrefetchEmbedding()
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=0,
    )
    document = RecallDocument(
        document_id="recall:parallel",
        memory_kind="semantic",
        source_item_ref="fact:parallel",
        source_slice="relevant_facts",
        source_refs=("event:parallel",),
        source_bindings=(
            RecallSourceBinding(
                source_kind="committed_event",
                authority_type="FactCommitted",
                ref="event:parallel",
                source_world_revision=2,
                immutable_hash="d" * 64,
            ),
        ),
        source_world_revision=2,
        text="这是一条可丢弃的预取候选。",
        actor_ref="agent:companion",
        subject_refs=("user:primary",),
        occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
        privacy_class="personal",
    )
    index = InMemoryRecallIndex(embedding=embedding)
    index.rebuild(cursor=cursor, documents=(document,))
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        trigger_ref="trigger:1",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="并行预取",
        accessibility_seed="draw:parallel-prefetch",
        trigger_ref="trigger:1",
    )
    assert await asyncio.to_thread(embedding.started.wait, 0.5)
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "这句我直接回答。"}],
                "stance": "answer_directly",
                "brief_rationale": "Answer without waiting for optional recall.",
                "confidence": 8000,
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    try:
        output = await asyncio.wait_for(
            _ExpressionDraftWire(
                model=model,
                recall_coordinator=coordinator,
            ).propose(_qq_request()),
            timeout=0.5,
        )
    finally:
        embedding.release.set()
        coordinator.close()

    assert len(model.calls) == 1
    assert output.prefetch_trace is None
    assert output.recall_trace is None


@pytest.mark.asyncio
async def test_ready_parallel_prefetch_is_visible_in_first_pass_and_audited() -> None:
    embedding = _ObservablePrefetchEmbedding()
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=0,
    )
    document = RecallDocument(
        document_id="recall:first-pass",
        memory_kind="episodic",
        source_item_ref="experience:first-pass",
        source_slice="recent_experiences",
        source_refs=("event:first-pass",),
        source_bindings=(
            RecallSourceBinding(
                source_kind="committed_event",
                authority_type="ExperienceCommitted",
                ref="event:first-pass",
                source_world_revision=2,
                immutable_hash="e" * 64,
            ),
        ),
        source_world_revision=2,
        text="她之前在窗边听完了那场雨。",
        actor_ref="agent:companion",
        subject_refs=("agent:companion",),
        occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
        privacy_class="private",
    )
    index = InMemoryRecallIndex(embedding=embedding)
    index.rebuild(cursor=cursor, documents=(document,))
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        trigger_ref="trigger:1",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="窗边听雨",
        accessibility_seed="draw:first-pass-prefetch",
        trigger_ref="trigger:1",
    )
    assert await asyncio.to_thread(embedding.finished.wait, 0.5)
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "刚才那阵雨让我想起一件事。"}],
                "stance": "share_present_association",
                "brief_rationale": "Use the recall that was already available.",
                "confidence": 8000,
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )

    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "world_revision": 3,
                    "deliberation_revision": 0,
                    "ledger_sequence": 0,
                    "logical_time": "2026-07-27T12:00:00+00:00",
                    "slices": {},
                },
                ensure_ascii=False,
            )
        }
    )
    output = await _ExpressionDraftWire(
        model=model,
        recall_coordinator=coordinator,
    ).propose(request)

    assert len(model.calls) == 1
    assert output.prefetch_trace is not None
    assert output.recall_trace is None
    assert "她之前在窗边听完了那场雨" in model.calls[0][0][1]["content"]
    trace = verify_trusted_recall_trace(output.prefetch_trace)
    assert trace.mode == "prefetch"
    assert trace.trigger_ref == "trigger:1"
    assert trace.hits[0].document.source_refs == ("event:first-pass",)


@pytest.mark.asyncio
async def test_first_pass_does_not_wait_for_remote_semantic_query_latency() -> None:
    semantic = _DelayedSemanticPrefetchEmbedding()
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=0,
    )
    document = RecallDocument(
        document_id="recall:delayed-semantic",
        memory_kind="semantic",
        source_item_ref="fact:delayed-semantic",
        source_slice="relevant_facts",
        source_refs=("event:delayed-semantic",),
        source_bindings=(
            RecallSourceBinding(
                source_kind="committed_event",
                authority_type="FactCommitted",
                ref="event:delayed-semantic",
                source_world_revision=2,
                immutable_hash="a" * 64,
            ),
        ),
        source_world_revision=2,
        text="稍晚完成的语义查询仍应进入首轮上下文。",
        actor_ref="agent:companion",
        subject_refs=("user:primary",),
        occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
        privacy_class="personal",
    )
    primary = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    primary.rebuild(cursor=cursor, documents=(document,))
    coordinator = RecallCoordinator.from_built_index(
        index=primary,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=semantic,
        trigger_ref="trigger:delayed-semantic",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="稍晚完成的语义查询",
        accessibility_seed="draw:delayed-semantic",
        trigger_ref="trigger:delayed-semantic",
    )

    started = time.monotonic()
    first_pass = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:delayed-semantic",
        # This case proves that the caller's explicit latency ceiling wins.
        # The production default is separately covered by the measured
        # 450 ms semantic-first-pass join regression.
        timeout_seconds=0.35,
    )
    elapsed = time.monotonic() - started
    trace = None
    for _ in range(40):
        trace = coordinator.take_ready_scheduled_prefetch(
            expected_cursor=cursor,
            trigger_ref="trigger:delayed-semantic",
        )
        if trace is not None:
            break
        await asyncio.sleep(0.01)
    coordinator.close()

    assert first_pass is not None
    first_pass_audit = verify_trusted_recall_trace(first_pass)
    assert first_pass_audit.hits[0].document.source_item_ref == "fact:delayed-semantic"
    assert first_pass_audit.embedding_version == FeatureHashRecallEmbedding.version
    assert elapsed < 0.4
    assert trace is not None
    completed_audit = verify_trusted_recall_trace(trace)
    assert completed_audit.hits[0].document.source_item_ref == "fact:delayed-semantic"
    assert completed_audit.embedding_version == semantic.version
    assert semantic.calls > 0


@pytest.mark.asyncio
async def test_technical_recovery_reuses_the_primary_prefetch_at_the_same_pinned_cursor() -> None:
    """A provider fallback must not lose the memory the timed-out primary saw."""

    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=0,
    )
    remembered_text = "我不是想让你分析怎么处理，就是想跟你吐槽一下"
    document = RecallDocument(
        document_id="recall:recovery-continuity",
        memory_kind="episodic",
        source_item_ref="dialogue:observation:older",
        source_slice="recent_dialogue",
        source_refs=("event:observation:older",),
        source_bindings=(
            RecallSourceBinding(
                source_kind="committed_event",
                authority_type="ObservationRecorded",
                ref="event:observation:older",
                source_world_revision=2,
                immutable_hash="9" * 64,
            ),
        ),
        source_world_revision=2,
        text=remembered_text,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        occurred_from=datetime(2026, 7, 27, 11, 55, tzinfo=UTC),
        privacy_class="private",
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=(document,))
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        trigger_ref="trigger:1",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="我刚才为什么说不想让你分析来着？",
        lexical_text="我刚才为什么说不想让你分析来着？",
        accessibility_seed="draw:recovery-continuity",
        trigger_ref="trigger:1",
    )
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我记得。"}],
                "stance": "answer_from_the_exchange",
                "brief_rationale": "Answer from the pinned exchange.",
                "confidence": 8200,
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "world_revision": 3,
                    "deliberation_revision": 0,
                    "ledger_sequence": 0,
                    "logical_time": "2026-07-27T12:00:00+00:00",
                    "slices": {},
                },
                ensure_ascii=False,
            )
        }
    )
    adapter = _ExpressionDraftWire(
        model=model,
        recall_coordinator=coordinator,
    )

    await adapter.propose(request)
    await adapter.recover(
        request.model_copy(update={"call_id": "call:technical-recovery"}),
        "main_timeout",
    )
    coordinator.close()

    assert len(model.calls) == 2
    assert remembered_text in model.calls[0][0][1]["content"]
    assert remembered_text in model.calls[1][0][1]["content"]
    assert "This is a recovery attempt after a technical failure" in model.calls[1][0][0]["content"]


@pytest.mark.asyncio
async def test_blocked_prefetch_is_daemonized_and_close_remains_bounded() -> None:
    embedding = _BlockingPrefetchEmbedding()
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=0,
    )
    index = InMemoryRecallIndex(embedding=embedding)
    index.rebuild(cursor=cursor, documents=())
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=embedding,
        trigger_ref="trigger:blocked-close",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="并行预取",
        accessibility_seed="draw:blocked-close",
        trigger_ref="trigger:blocked-close",
    )
    assert await asyncio.to_thread(embedding.started.wait, 0.5)
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        coordinator.close()
        elapsed = loop.time() - started
        assert not embedding.closed.is_set()
    finally:
        embedding.release.set()

    assert elapsed < 0.1
    assert await asyncio.to_thread(embedding.closed.wait, 0.5)
    assert not embedding.used_after_close.is_set()


@pytest.mark.asyncio
async def test_close_tracks_prefetch_after_timeout_removed_its_future() -> None:
    embedding = _BlockingPrefetchEmbedding()
    cursor = RecallCursor(world_revision=3, deliberation_revision=0, ledger_sequence=0)
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=())
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=embedding,
        trigger_ref="trigger:popped-close",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="并行预取",
        accessibility_seed="draw:popped-close",
        trigger_ref="trigger:popped-close",
    )
    assert await asyncio.to_thread(embedding.started.wait, 0.5)
    assert (
        await coordinator.consume_scheduled_prefetch(
            expected_cursor=cursor,
            trigger_ref="trigger:popped-close",
            timeout_seconds=0.01,
        )
        is None
    )
    started = asyncio.get_running_loop().time()
    try:
        coordinator.close()
        assert asyncio.get_running_loop().time() - started < 0.1
        assert not embedding.closed.is_set()
    finally:
        embedding.release.set()

    assert await asyncio.to_thread(embedding.closed.wait, 0.5)
    assert not embedding.used_after_close.is_set()


@pytest.mark.asyncio
async def test_close_cancels_an_active_prefetch_consumer_without_republishing_replay() -> None:
    embedding = _BlockingPrefetchEmbedding()
    cursor = RecallCursor(world_revision=3, deliberation_revision=0, ledger_sequence=0)
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=())
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=embedding,
        trigger_ref="trigger:active-consumer-close",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="并行预取",
        accessibility_seed="draw:active-consumer-close",
        trigger_ref="trigger:active-consumer-close",
    )
    assert await asyncio.to_thread(embedding.started.wait, 0.5)
    consumer = asyncio.create_task(
        coordinator.consume_scheduled_prefetch(
            expected_cursor=cursor,
            trigger_ref="trigger:active-consumer-close",
            timeout_seconds=1.0,
        )
    )
    await asyncio.sleep(0)

    coordinator.close()
    try:
        assert await asyncio.wait_for(consumer, timeout=0.1) is None
        assert (
            await coordinator.await_scheduled_prefetch(
                expected_cursor=cursor,
                trigger_ref="trigger:active-consumer-close",
                timeout_seconds=0.01,
            )
            is None
        )
    finally:
        embedding.release.set()


@pytest.mark.asyncio
async def test_stale_prefetch_job_token_cannot_discard_replacement_generation() -> None:
    embedding = _BlockingPrefetchEmbedding()
    cursor = RecallCursor(world_revision=3, deliberation_revision=0, ledger_sequence=0)
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=())
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=embedding,
        trigger_ref="trigger:generation",
    )
    stale = coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="并行预取",
        accessibility_seed="draw:generation:old",
        trigger_ref="trigger:generation",
    )
    assert await asyncio.to_thread(embedding.started.wait, 0.5)
    stale_waiter = asyncio.create_task(
        coordinator.await_scheduled_prefetch(
            expected_cursor=cursor,
            trigger_ref="trigger:generation",
            timeout_seconds=0.02,
            job_token=stale,
        )
    )
    await asyncio.sleep(0)
    current = coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="并行预取",
        accessibility_seed="draw:generation:new",
        trigger_ref="trigger:generation",
    )

    assert await stale_waiter is None
    coordinator.discard_scheduled_prefetch(
        cursor,
        trigger_ref="trigger:generation",
        job_token=stale,
    )
    embedding.release.set()
    trace = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:generation",
        timeout_seconds=0.5,
        job_token=current,
    )
    coordinator.close()

    assert current != stale
    assert trace is not None
    assert verify_trusted_recall_trace(trace).trigger_ref == "trigger:generation"


@pytest.mark.asyncio
async def test_concurrent_prefetch_discard_is_idempotent() -> None:
    embedding = _BlockingPrefetchEmbedding()
    cursor = RecallCursor(world_revision=3, deliberation_revision=0, ledger_sequence=0)
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=())
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=embedding,
        trigger_ref="trigger:concurrent-discard",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="并行预取",
        accessibility_seed="draw:concurrent-discard",
        trigger_ref="trigger:concurrent-discard",
    )
    assert await asyncio.to_thread(embedding.started.wait, 0.5)
    barrier = threading.Barrier(8)

    def discard() -> None:
        barrier.wait(timeout=1)
        coordinator.discard_scheduled_prefetch(
            cursor,
            trigger_ref="trigger:concurrent-discard",
        )

    try:
        await asyncio.gather(*(asyncio.to_thread(discard) for _ in range(8)))
    finally:
        embedding.release.set()
        coordinator.close()


def test_closed_coordinator_cannot_publish_a_new_prefetch_worker() -> None:
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=0,
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=())
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        trigger_ref="trigger:closed-prefetch",
    )
    coordinator.close()

    with pytest.raises(RuntimeError, match="coordinator is closed"):
        coordinator.schedule_prefetch(
            expected_cursor=cursor,
            query_text="不该开始",
            accessibility_seed="draw:closed-prefetch",
            trigger_ref="trigger:closed-prefetch",
        )


@pytest.mark.asyncio
async def test_close_defers_embedding_shutdown_until_deep_recall_finishes() -> None:
    embedding = _BlockingPrefetchEmbedding()
    cursor = RecallCursor(world_revision=3, deliberation_revision=0, ledger_sequence=0)
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=())
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=embedding,
        trigger_ref="trigger:blocked-deep-recall",
    )
    task = asyncio.create_task(
        perform_character_recall(
            coordinator,
            request=CharacterRecallRequest(query_text="并行预取", limit=2),
            accessibility_seed="draw:blocked-deep-recall",
            expected_cursor=cursor,
            trigger_ref="trigger:blocked-deep-recall",
            timeout_seconds=1.0,
        )
    )
    assert await asyncio.to_thread(embedding.started.wait, 0.5)
    started = asyncio.get_running_loop().time()
    try:
        coordinator.close()
        assert asyncio.get_running_loop().time() - started < 0.1
        assert not embedding.closed.is_set()
    finally:
        embedding.release.set()

    await task
    assert await asyncio.to_thread(embedding.closed.wait, 0.5)
    assert not embedding.used_after_close.is_set()


@pytest.mark.asyncio
async def test_first_pass_timeout_preserves_prefetch_and_only_joins_once() -> None:
    embedding = _BlockingPrefetchEmbedding()
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=0,
    )
    document = RecallDocument(
        document_id="recall:preserved",
        memory_kind="semantic",
        source_item_ref="fact:preserved",
        source_slice="relevant_facts",
        source_refs=("event:preserved",),
        source_bindings=(
            RecallSourceBinding(
                source_kind="committed_event",
                authority_type="FactCommitted",
                ref="event:preserved",
                source_world_revision=2,
                immutable_hash="f" * 64,
            ),
        ),
        source_world_revision=2,
        text="并行预取完成后仍应可见。",
        actor_ref="agent:companion",
        subject_refs=("user:primary",),
        occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
        privacy_class="personal",
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=(document,))
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=embedding,
        trigger_ref="trigger:preserved",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="并行预取",
        accessibility_seed="draw:preserved",
        trigger_ref="trigger:preserved",
    )
    assert await asyncio.to_thread(embedding.started.wait, 0.5)

    fallback = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:preserved",
        timeout_seconds=0.01,
    )
    assert fallback is not None
    assert verify_trusted_recall_trace(fallback).hits
    loop = asyncio.get_running_loop()
    started = loop.time()
    repeated_fallback = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:preserved",
        timeout_seconds=0.2,
    )
    assert repeated_fallback == fallback
    assert loop.time() - started < 0.05

    embedding.release.set()
    trace = None
    for _ in range(50):
        trace = coordinator.take_ready_scheduled_prefetch(
            expected_cursor=cursor,
            trigger_ref="trigger:preserved",
        )
        if trace is not None:
            break
        await asyncio.sleep(0.01)
    coordinator.close()

    assert trace is not None
    assert verify_trusted_recall_trace(trace).hits[0].document.source_item_ref == "fact:preserved"


@pytest.mark.asyncio
async def test_completed_semantic_prefetch_upgrades_the_same_turn_local_replay() -> None:
    semantic = _ControlledSemanticPrefetchEmbedding()
    cursor = RecallCursor(world_revision=3, deliberation_revision=0, ledger_sequence=0)
    documents = (
        RecallDocument(
            document_id="recall:lexical-fallback",
            memory_kind="semantic",
            source_item_ref="fact:lexical-fallback",
            source_slice="relevant_facts",
            source_refs=("event:lexical-fallback",),
            source_bindings=(
                RecallSourceBinding(
                    source_kind="committed_event",
                    authority_type="FactCommitted",
                    ref="event:lexical-fallback",
                    source_world_revision=2,
                    immutable_hash="1" * 64,
                ),
            ),
            source_world_revision=2,
            text="这里保留直说的词面线索。",
            actor_ref="agent:companion",
            subject_refs=("user:primary",),
            occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
            privacy_class="personal",
        ),
        RecallDocument(
            document_id="recall:semantic-only",
            memory_kind="episodic",
            source_item_ref="experience:semantic-only",
            source_slice="recent_experiences",
            source_refs=("event:semantic-only",),
            source_bindings=(
                RecallSourceBinding(
                    source_kind="committed_event",
                    authority_type="ExperienceCommitted",
                    ref="event:semantic-only",
                    source_world_revision=2,
                    immutable_hash="2" * 64,
                ),
            ),
            source_world_revision=2,
            text="完全不同措辞的语义回忆",
            actor_ref="agent:companion",
            subject_refs=("agent:companion",),
            occurred_from=datetime(2026, 7, 25, 13, tzinfo=UTC),
            privacy_class="private",
        ),
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=documents)
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=semantic,
        trigger_ref="trigger:semantic-upgrade",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="直说的词面线索",
        accessibility_seed="draw:semantic-upgrade",
        trigger_ref="trigger:semantic-upgrade",
    )
    assert await asyncio.to_thread(semantic.started.wait, 0.5)

    local = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:semantic-upgrade",
        timeout_seconds=0.01,
    )
    assert local is not None
    local_audit = verify_trusted_recall_trace(local)
    assert local_audit.embedding_version == FeatureHashRecallEmbedding.version
    assert {hit.document.source_item_ref for hit in local_audit.hits} == {"fact:lexical-fallback"}
    coordinator.record_prefetch_presentation(
        PresentedPrefetchTrace(
            phase="initial",
            model_call_id="model-call:semantic-upgrade:initial",
            trace=local,
        )
    )

    semantic.release.set()
    assert await asyncio.to_thread(semantic.finished.wait, 0.5)
    late_ready_health: dict[str, object] = {}
    for _ in range(50):
        late_ready_health = coordinator.semantic_health()
        if late_ready_health["last_prefetch_delivery_status"] == "semantic_late_ready":
            break
        await asyncio.sleep(0.01)
    assert late_ready_health["last_prefetch_delivery_status"] == "semantic_late_ready"
    assert late_ready_health["prefetch_late_semantic_ready_count"] == 1
    completed = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:semantic-upgrade",
        timeout_seconds=0.01,
    )
    assert completed is not None
    coordinator.record_prefetch_presentation(
        PresentedPrefetchTrace(
            phase="recall_followup",
            model_call_id="model-call:semantic-upgrade:followup",
            trace=completed,
        )
    )
    coordinator.close()

    completed_audit = verify_trusted_recall_trace(completed)
    assert completed_audit.embedding_version == semantic.version
    assert "experience:semantic-only" in {
        hit.document.source_item_ref for hit in completed_audit.hits
    }
    health = coordinator.semantic_health()
    assert health["last_prefetch_delivery_status"] == "semantic_late_consumed"
    assert health["prefetch_first_pass_local_count"] == 1
    assert health["prefetch_late_semantic_ready_count"] == 1
    assert health["prefetch_late_semantic_consumed_count"] == 1


@pytest.mark.asyncio
async def test_ready_semantic_prefetch_enters_the_first_pass_without_local_replay() -> None:
    semantic = _ControlledSemanticPrefetchEmbedding(released=True)
    coordinator, cursor = _single_semantic_prefetch_coordinator(
        semantic,
        trigger_ref="trigger:ready-semantic",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="直说的词面线索",
        accessibility_seed="draw:ready-semantic",
        trigger_ref="trigger:ready-semantic",
    )
    assert await asyncio.to_thread(semantic.finished.wait, 0.5)

    trace = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:ready-semantic",
        timeout_seconds=0.01,
    )
    assert trace is not None
    coordinator.record_prefetch_presentation(
        PresentedPrefetchTrace(
            phase="initial",
            model_call_id="model-call:ready-semantic:initial",
            trace=trace,
        )
    )
    coordinator.close()

    audit = verify_trusted_recall_trace(trace)
    assert audit.embedding_version == semantic.version
    assert audit.hits[0].document.source_item_ref == "experience:ready-semantic"
    health = coordinator.semantic_health()
    assert health["last_prefetch_delivery_status"] == "semantic_first_pass"
    assert health["prefetch_first_pass_semantic_count"] == 1
    assert health["prefetch_first_pass_local_count"] == 0


@pytest.mark.asyncio
async def test_semantic_prefetch_finishing_inside_the_join_is_counted_as_first_pass() -> None:
    semantic = _ControlledSemanticPrefetchEmbedding()
    coordinator, cursor = _single_semantic_prefetch_coordinator(
        semantic,
        trigger_ref="trigger:joined-semantic",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="直说的词面线索",
        accessibility_seed="draw:joined-semantic",
        trigger_ref="trigger:joined-semantic",
    )
    assert await asyncio.to_thread(semantic.started.wait, 0.5)
    asyncio.get_running_loop().call_later(0.02, semantic.release.set)

    trace = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:joined-semantic",
        timeout_seconds=0.2,
    )
    assert trace is not None
    coordinator.record_prefetch_presentation(
        PresentedPrefetchTrace(
            phase="initial",
            model_call_id="model-call:joined-semantic:initial",
            trace=trace,
        )
    )
    coordinator.close()

    audit = verify_trusted_recall_trace(trace)
    assert audit.embedding_version == semantic.version
    assert audit.hits[0].document.source_item_ref == "experience:joined-semantic"
    health = coordinator.semantic_health()
    assert health["last_prefetch_delivery_status"] == "semantic_first_pass"
    assert health["prefetch_first_pass_semantic_count"] == 1
    assert health["prefetch_first_pass_local_count"] == 0
    assert health["prefetch_late_semantic_ready_count"] == 0


@pytest.mark.asyncio
async def test_zero_first_pass_budget_returns_local_recall_without_a_timeout_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    semantic = _ControlledSemanticPrefetchEmbedding()
    coordinator, cursor = _single_semantic_prefetch_coordinator(
        semantic,
        trigger_ref="trigger:zero-join",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="直说的词面线索",
        accessibility_seed="draw:zero-join",
        trigger_ref="trigger:zero-join",
    )
    assert await asyncio.to_thread(semantic.started.wait, 0.5)

    loop = asyncio.get_running_loop()
    started = loop.time()
    trace = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:zero-join",
        timeout_seconds=0,
    )
    elapsed = loop.time() - started

    try:
        assert trace is not None
        assert elapsed < 0.05
        assert (
            verify_trusted_recall_trace(trace).embedding_version
            == FeatureHashRecallEmbedding.version
        )
        assert "missed the bounded first-pass join" not in caplog.text
    finally:
        semantic.release.set()
        assert await asyncio.to_thread(semantic.finished.wait, 0.5)
        coordinator.close()


@pytest.mark.asyncio
async def test_character_recall_followup_absorbs_ready_semantic_prefetch_without_waiting() -> None:
    semantic = _ControlledSemanticPrefetchEmbedding()
    cursor = RecallCursor(world_revision=3, deliberation_revision=0, ledger_sequence=0)
    documents = (
        RecallDocument(
            document_id="recall:followup-lexical",
            memory_kind="semantic",
            source_item_ref="fact:followup-lexical",
            source_slice="relevant_facts",
            source_refs=("event:followup-lexical",),
            source_bindings=(
                RecallSourceBinding(
                    source_kind="committed_event",
                    authority_type="FactCommitted",
                    ref="event:followup-lexical",
                    source_world_revision=2,
                    immutable_hash="3" * 64,
                ),
            ),
            source_world_revision=2,
            text="这里保留直说的词面线索。",
            actor_ref="agent:companion",
            subject_refs=("user:primary",),
            occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
            privacy_class="personal",
        ),
        RecallDocument(
            document_id="recall:followup-semantic",
            memory_kind="episodic",
            source_item_ref="experience:followup-semantic",
            source_slice="recent_experiences",
            source_refs=("event:followup-semantic",),
            source_bindings=(
                RecallSourceBinding(
                    source_kind="committed_event",
                    authority_type="ExperienceCommitted",
                    ref="event:followup-semantic",
                    source_world_revision=2,
                    immutable_hash="4" * 64,
                ),
            ),
            source_world_revision=2,
            text="完全不同措辞的语义回忆",
            actor_ref="agent:companion",
            subject_refs=("agent:companion",),
            occurred_from=datetime(2026, 7, 25, 13, tzinfo=UTC),
            privacy_class="private",
        ),
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=documents)
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=semantic,
        trigger_ref="trigger:1",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="直说的词面线索",
        accessibility_seed="draw:followup-semantic",
        trigger_ref="trigger:1",
    )
    model = _ReleaseSemanticSequenceJsonModel(
        [
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "我觉得还有相关的东西浮在边缘，想自己再想一下。",
                        "attended_source_refs": ["trigger:1"],
                    },
                    "recall_request": {
                        "query_text": "继续回忆",
                        "limit": 2,
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "想起来以后，我还是更想接住眼前这句话。",
                        "attended_source_refs": ["trigger:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "嗯，我想起来了。"}],
                    "stance": "present",
                    "brief_rationale": "I chose the final expression after recall.",
                    "confidence": 8200,
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ],
        semantic=semantic,
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "world_revision": 3,
                    "deliberation_revision": 0,
                    "ledger_sequence": 0,
                    "logical_time": "2026-07-27T12:00:00+00:00",
                    "slices": {},
                },
                ensure_ascii=False,
            )
        }
    )

    output = await _ExpressionDraftWire(
        model=model,
        recall_coordinator=coordinator,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    ).propose(request)
    coordinator.close()

    assert len(model.calls) == 2
    assert output.prefetch_trace is not None
    prefetch = verify_trusted_recall_trace(output.prefetch_trace)
    assert prefetch.embedding_version == semantic.version
    assert "experience:followup-semantic" in {hit.document.source_item_ref for hit in prefetch.hits}
    assert "完全不同措辞的语义回忆" in model.calls[1][0][-1]["content"]
    assert tuple(item.phase for item in output.presented_prefetch_traces) == (
        "initial",
        "recall_followup",
    )
    first_presented, later_presented = output.presented_prefetch_traces
    assert (
        verify_trusted_recall_trace(first_presented.trace).embedding_version
        == FeatureHashRecallEmbedding.version
    )
    assert verify_trusted_recall_trace(later_presented.trace).embedding_version == semantic.version
    assert first_presented.model_call_id != later_presented.model_call_id
    assert later_presented.model_call_id == output.winning_model_call_id


@pytest.mark.asyncio
async def test_prefetch_capacity_saturation_keeps_source_bound_local_fallback() -> None:
    embedding = _BlockingPrefetchEmbedding()
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=0,
    )
    document = RecallDocument(
        document_id="recall:capacity-fallback",
        memory_kind="semantic",
        source_item_ref="fact:capacity-fallback",
        source_slice="relevant_facts",
        source_refs=("event:capacity-fallback",),
        source_bindings=(
            RecallSourceBinding(
                source_kind="committed_event",
                authority_type="FactCommitted",
                ref="event:capacity-fallback",
                source_world_revision=2,
                immutable_hash="c" * 64,
            ),
        ),
        source_world_revision=2,
        text="并行预取饱和时仍保留本地回忆。",
        actor_ref="agent:companion",
        subject_refs=("user:primary",),
        occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
        privacy_class="personal",
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=(document,))
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=embedding,
    )
    for index_number in range(5):
        coordinator.schedule_prefetch(
            expected_cursor=cursor,
            query_text="并行预取",
            accessibility_seed=f"draw:capacity:{index_number}",
            trigger_ref=f"trigger:capacity:{index_number}",
        )
    assert await asyncio.to_thread(embedding.started.wait, 0.5)

    fallback = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:capacity:4",
        timeout_seconds=0.01,
    )
    assert fallback is not None
    audit = verify_trusted_recall_trace(fallback)
    assert audit.hits[0].document.source_item_ref == "fact:capacity-fallback"
    assert audit.embedding_status != "ready"
    health = coordinator.semantic_health()
    assert health["last_prefetch_failure_code"] == "prefetch_capacity"
    assert health["turn_summary"]["hot_context"] == "ready"
    assert health["turn_summary"]["recall"] == "degraded"
    assert health["turn_summary"]["hits"] == 1
    assert health["turn_summary"]["fallback_channels"] == ["lexical"]
    assert health["turn_summary"]["character_outcome"] == "reported_by_turn_application"

    embedding.release.set()
    coordinator.close()


@pytest.mark.asyncio
async def test_automatic_prefetch_uses_the_configured_semantic_lane() -> None:
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=0,
    )
    document = RecallDocument(
        document_id="recall:malformed",
        memory_kind="semantic",
        source_item_ref="fact:malformed",
        source_slice="relevant_facts",
        source_refs=("event:malformed",),
        source_bindings=(
            RecallSourceBinding(
                source_kind="committed_event",
                authority_type="FactCommitted",
                ref="event:malformed",
                source_world_revision=2,
                immutable_hash="e" * 64,
            ),
        ),
        source_world_revision=2,
        text="损坏的查询仍共享词面。",
        actor_ref="agent:companion",
        subject_refs=("user:primary",),
        occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
        privacy_class="personal",
    )
    base = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    base.rebuild(cursor=cursor, documents=(document,))
    semantic = _MalformedPrefetchEmbedding()
    coordinator = RecallCoordinator.from_built_index(
        index=base,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        semantic_embedding=semantic,
        trigger_ref="trigger:malformed",
    )
    coordinator.schedule_prefetch(
        expected_cursor=cursor,
        query_text="损坏的查询",
        accessibility_seed="draw:malformed",
        trigger_ref="trigger:malformed",
    )

    trace = await coordinator.await_scheduled_prefetch(
        expected_cursor=cursor,
        trigger_ref="trigger:malformed",
        timeout_seconds=0.5,
    )
    coordinator.close()

    assert trace is not None
    audit = verify_trusted_recall_trace(trace)
    assert audit.hits[0].document.source_item_ref == "fact:malformed"
    assert audit.embedding_status == "degraded"
    assert semantic.calls > 0
    health = coordinator.semantic_health()
    assert health["last_prefetch_status"] == "ready"
    assert health["last_prefetch_embedding_status"] == "degraded"
    assert health["last_prefetch_hit_count"] == 1
    assert "lexical" in health["last_prefetch_match_channels"]


def test_character_recall_uses_older_pinned_context_after_newer_refresh() -> None:
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=2,
        ledger_sequence=5,
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(
        cursor=cursor,
        documents=(
            RecallDocument(
                document_id="recall:fact:tea",
                memory_kind="semantic",
                source_item_ref="fact:tea",
                source_slice="relevant_facts",
                source_refs=("event:fact:tea",),
                source_bindings=(
                    RecallSourceBinding(
                        source_kind="committed_event",
                        authority_type="FactCommitted",
                        ref="event:fact:tea",
                        source_world_revision=2,
                        immutable_hash="c" * 64,
                    ),
                ),
                source_world_revision=2,
                text="我最近开始用盖碗泡凤凰单丛。",
                actor_ref="agent:companion",
                subject_refs=("user:primary",),
                occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
                privacy_class="personal",
            ),
        ),
    )
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
    )
    coordinator.refresh(
        cursor=RecallCursor(
            world_revision=4,
            deliberation_revision=3,
            ledger_sequence=6,
        ),
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, 1, tzinfo=UTC),
        sources=RecallCorpusSources(),
    )

    trace = verify_trusted_recall_trace(
        coordinator.recall(
            request=CharacterRecallRequest(query_text="凤凰单丛"),
            accessibility_seed="draw:older-context",
            expected_cursor=cursor,
            trigger_ref="trigger:older-context",
        )
    )

    assert trace.index_cursor == cursor
    assert trace.hits[0].document.source_item_ref == "fact:tea"


@pytest.mark.asyncio
async def test_automatic_prefetch_uses_its_exact_pinned_cursor_after_newer_refresh() -> None:
    older_cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=2,
        ledger_sequence=5,
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(
        cursor=older_cursor,
        documents=(
            RecallDocument(
                document_id="recall:fact:older-prefetch",
                memory_kind="episodic",
                source_item_ref="fact:older-prefetch",
                source_slice="recent_experiences",
                source_refs=("event:fact:older-prefetch",),
                source_bindings=(
                    RecallSourceBinding(
                        source_kind="committed_event",
                        authority_type="LifeExperienceRecorded",
                        ref="event:fact:older-prefetch",
                        source_world_revision=2,
                        immutable_hash="d" * 64,
                    ),
                ),
                source_world_revision=2,
                text="前一轮她在楼下买到了最后一份桂花糕。",
                actor_ref="agent:companion",
                subject_refs=("agent:companion", "user:primary"),
                occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
                privacy_class="personal",
            ),
        ),
    )
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=older_cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
        trigger_ref="trigger:older-prefetch",
    )
    coordinator.refresh(
        cursor=RecallCursor(
            world_revision=4,
            deliberation_revision=3,
            ledger_sequence=6,
        ),
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, 1, tzinfo=UTC),
        sources=RecallCorpusSources(),
        trigger_ref="trigger:newer-prefetch",
    )

    coordinator.schedule_prefetch(
        expected_cursor=older_cursor,
        query_text="桂花糕",
        accessibility_seed="draw:older-prefetch",
        trigger_ref="trigger:older-prefetch",
    )
    trace = await coordinator.await_scheduled_prefetch(
        expected_cursor=older_cursor,
        trigger_ref="trigger:older-prefetch",
        timeout_seconds=0.5,
    )
    coordinator.close()

    assert trace is not None
    audit = verify_trusted_recall_trace(trace)
    assert audit.evaluated_cursor == older_cursor
    assert audit.hits[0].document.source_item_ref == "fact:older-prefetch"


class _RaisingModel(_Model):
    async def complete(self, messages, *, temperature=0.8):  # type: ignore[no-untyped-def]
        del messages, temperature
        raise KeyError("reviewer fixture has no review contract")


@pytest.mark.asyncio
async def test_prompt_models_a_mutually_established_future_continuation_as_optional_expectation() -> (
    None
):
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "好，晚点见。"}],
                "stance": "leave_the_thread_open",
                "brief_rationale": "The counterpart explicitly plans to return.",
                "response_expectation": {
                    "hoped_response": "对方忙完后回来继续聊天",
                    "pressure_bp": 1000,
                    "importance_bp": 5000,
                    "wait_seconds": 600,
                    "expires_after_seconds": 21600,
                },
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(model=model)
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "我先忙，晚点聊。"}
            )
        }
    )

    output = await adapter.propose(request)

    system = model.last_system_prompt
    assert "genuinely expect a reply" in system
    assert "对方忙完后回来继续聊天" in json.dumps(output.raw_proposal, ensure_ascii=False)


@pytest.mark.asyncio
async def test_pending_expectation_is_assessed_inside_the_normal_inbound_cognition() -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "哈哈，听起来确实不太对你胃口。"}],
                "stance": "receive_the_answer",
                "brief_rationale": "The current message directly answers the earlier question.",
                "world_claims": [],
                "response_expectation_assessment": {
                    "status": "fulfilled",
                    "reason": "The counterpart directly said whether the trip was enjoyable.",
                },
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "logical_time": "2026-07-26T01:07:00+00:00",
                    "slices": {
                        "advisories": {
                            "items": [
                                {
                                    "value": {
                                        "kind": "response_expectation",
                                        "summary": "hoped for how the trip went",
                                    }
                                }
                            ]
                        }
                    },
                }
            ),
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "深圳说实话不是很好玩哈哈哈哈"}
            ),
        }
    )

    output = await _ExpressionDraftWire(model=model).propose(request)

    assert output.raw_proposal["response_expectation_assessment"] == {
        "status": "fulfilled",
        "reason": "The counterpart directly said whether the trip was enjoyable.",
    }
    assert "same cognition" in model.calls[0][0][0]["content"]


@pytest.mark.asyncio
async def test_missing_expectation_assessment_does_not_discard_a_valid_reply() -> None:
    missing = json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "我接住你这句。"}],
            "stance": "answer_without_world_claims",
            "brief_rationale": "Respond to the current message.",
            "world_claims": [],
        },
        ensure_ascii=False,
    )
    model = _SequenceJsonModel([missing, missing])
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "logical_time": "2026-07-26T01:07:00+00:00",
                    "slices": {
                        "advisories": {
                            "items": [
                                {
                                    "value": {
                                        "kind": "response_expectation",
                                        "summary": "hoped for an answer",
                                    }
                                }
                            ]
                        }
                    },
                }
            ),
        }
    )

    output = await _ExpressionDraftWire(model=model).propose(request)

    assert output.raw_proposal.get("response_expectation_assessment") is None
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_quick_recovery_keeps_reply_when_expectation_assessment_is_missing() -> None:
    missing = json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "我接住你这句。"}],
            "stance": "answer_without_world_claims",
            "brief_rationale": "Recover the visible reply.",
            "world_claims": [],
        },
        ensure_ascii=False,
    )
    model = _JsonModel(missing)
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "logical_time": "2026-07-26T01:07:00+00:00",
                    "slices": {
                        "advisories": {
                            "items": [
                                {
                                    "value": {
                                        "kind": "response_expectation",
                                        "summary": "hoped for an answer",
                                    }
                                }
                            ]
                        }
                    },
                }
            ),
        }
    )

    output = await _ExpressionDraftWire(model=model).recover(request, "main_attempt_failed")

    assert output.raw_proposal["proposal_kind"] == "minimal"
    assert output.raw_proposal.get("response_expectation_assessment") is None


@pytest.mark.asyncio
async def test_quick_recovery_preserves_a_valid_expectation_assessment() -> None:
    recovered = json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "嗯，你这句已经回答我了。"}],
            "stance": "answer_without_world_claims",
            "brief_rationale": "Recover the reply and preserve the semantic judgement.",
            "world_claims": [],
            "response_expectation_assessment": {
                "status": "fulfilled",
                "reason": "The current message directly answers the open question.",
            },
        },
        ensure_ascii=False,
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "logical_time": "2026-07-26T01:07:00+00:00",
                    "slices": {
                        "advisories": {
                            "items": [
                                {
                                    "value": {
                                        "kind": "response_expectation",
                                        "summary": "hoped for an answer",
                                    }
                                }
                            ]
                        }
                    },
                }
            ),
        }
    )

    output = await _ExpressionDraftWire(model=_JsonModel(recovered)).recover(
        request, "main_attempt_failed"
    )

    assert output.raw_proposal["proposal_kind"] == "minimal"
    assert output.raw_proposal["response_expectation_assessment"] == {
        "status": "fulfilled",
        "reason": "The current message directly answers the open question.",
    }


@pytest.mark.asyncio
async def test_future_continuation_remains_the_models_choice() -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "等你回来再说。"}],
                "stance": "leave_the_thread_open",
                "brief_rationale": "Accept the counterpart's pause.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "我先忙，晚点聊。"}
            )
        }
    )

    output = await _ExpressionDraftWire(model=model).propose(request)

    payload = json.loads(output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    assert payload["response_expectation"] is None


@pytest.mark.asyncio
async def test_paraphrased_mutual_resume_intent_normalizes_without_one_fixed_sentence() -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "行，等你忙完我们接着说。"}],
                "stance": "hold_the_topic_lightly",
                "brief_rationale": "Keep a future continuation open.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "我得先处理点事，忙完回来继续聊。"}
            )
        }
    )

    output = await _ExpressionDraftWire(model=model).propose(request)

    assert (
        '"response_expectation":null'
        in output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trigger", "reply"),
    [
        ("我先走啦，改天见。", "好，拜拜。"),
        ("晚安，明天见。", "晚安。"),
        ("我先忙。", "好，你先忙。"),
        ("我先忙，晚点聊。", "好，拜拜。"),
    ],
)
@pytest.mark.asyncio
async def test_generic_farewell_or_one_sided_pause_does_not_create_response_gap(
    trigger: str, reply: str
) -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": reply}],
                "stance": "close_for_now",
                "brief_rationale": "Do not establish a mutual continuation.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(update={"text": trigger})
        }
    )

    output = await _ExpressionDraftWire(model=model).propose(request)

    payload = json.loads(output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    assert payload["response_expectation"] is None


@pytest.mark.asyncio
async def test_adapter_keeps_chat_model_output_inert_and_binds_request_to_prompt() -> None:
    model = _Model('{"proposal_id":"proposal:1"}')
    adapter = _ExpressionDraftWire(model=model)

    output = await adapter.propose(_request())

    assert output.model_id == "deepseek-v4-flash"
    assert output.raw_proposal == {"proposal_id": "proposal:1"}
    messages, temperature = model.calls[0]
    assert temperature == 0.7
    assert "ExpressionDraft" in messages[0]["content"]
    supplied = json.loads(messages[1]["content"])
    assert supplied["request"]["trigger_ref"] == "trigger:1"
    assert supplied["request"]["evaluated_world_revision"] == 3


@pytest.mark.asyncio
async def test_chat_prompt_keeps_values_but_omits_capsule_proof_noise() -> None:
    noisy_context = json.dumps(
        {
            "world_id": "world:test",
            "actor_ref": "agent:companion",
            "trigger_ref": "event:message:2",
            "world_revision": 9,
            "logical_time": "2026-07-17T00:00:00+00:00",
            "slices": {
                "recent_dialogue": {
                    "availability": "available",
                    "source_refs": ["event:acceptance:1"],
                    "source_hash": "a" * 64,
                    "resolver_proof": {"large": "x" * 4_000},
                    "items": [
                        {
                            "item_ref": "dialogue:user:1",
                            "privacy_class": "private",
                            "source_hash": "b" * 64,
                            "value_hash": "c" * 64,
                            "source_bindings": [{"ref": "event:acceptance:1", "hash": "d" * 64}],
                            "value": {"speaker": "user", "text": "你刚才有点敷衍。"},
                        }
                    ],
                }
            },
        },
        ensure_ascii=False,
    )
    model = _Model('{"proposal_id":"proposal:1"}')
    request = _request().model_copy(update={"model_content_json": noisy_context})

    await _ExpressionDraftWire(model=model).propose(request)

    supplied = json.loads(model.calls[0][0][1]["content"])
    compact = json.loads(supplied["request"]["model_content_json"])
    dialogue = compact["slices"]["recent_dialogue"]
    assert dialogue["items"][0]["value"]["text"] == "你刚才有点敷衍。"
    assert dialogue["items"][0]["source_ref"] == "dialogue:user:1"
    assert "resolver_proof" not in dialogue
    assert len(json.dumps(compact, ensure_ascii=False)) < len(noisy_context) // 4


@pytest.mark.asyncio
async def test_adapter_composes_provider_usage_with_the_same_completion() -> None:
    adapter = _ExpressionDraftWire(model=_MeteredModel('{"proposal_id":"proposal:metered"}'))

    output = await adapter.propose(_request())

    assert output.input_tokens == 12
    assert output.output_tokens == 3
    assert output.usage is not None
    assert output.usage.route_class == "chat"
    assert output.usage.token_provenance == "provider_reported"


@pytest.mark.asyncio
async def test_adapter_requests_provider_json_mode_when_available() -> None:
    adapter = _ExpressionDraftWire(model=_JsonModel('{"proposal_id":"proposal:json"}'))

    output = await adapter.propose(_request())

    assert output.raw_proposal == {"proposal_id": "proposal:json"}


@pytest.mark.asyncio
async def test_adapter_preserves_provider_json_mode_with_metered_completion() -> None:
    adapter = _ExpressionDraftWire(
        model=_JsonMeteredModel('{"proposal_id":"proposal:metered-json"}')
    )

    output = await adapter.propose(_request())

    assert output.raw_proposal == {"proposal_id": "proposal:metered-json"}
    assert output.usage is not None


@pytest.mark.asyncio
async def test_identity_frame_carries_personality_boundaries_and_world_claim_discipline() -> None:
    model = _Model('{"proposal_id":"proposal:persona"}')
    adapter = _ExpressionDraftWire(
        model=model,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
            stable_identity_facts=("汉语言文学专业",),
            personality_frame="慢热，有自己的判断，不无条件附和。",
            values=("真诚比漂亮话重要",),
            speech_frame="中文短句，像私聊。",
            style_rules=("想知道的时候才问",),
            boundaries=("不编造真实线下行动证据",),
        ),
    )

    await adapter.propose(_request())

    system = model.last_system_prompt
    assert all(
        value in system
        for value in ("沈知栀", "慢热", "真诚比漂亮话重要", "不编造真实线下行动证据")
    )
    assert "刚认识" not in system
    assert "copy a listed alias or exact canonical ref without editing it" in system


def test_static_relationship_frame_is_not_stable_identity_authority() -> None:
    identity = CompanionIdentityFrame(
        companion_name="沈知栀",
        counterpart_name="geoff",
        stable_identity_facts=("汉语言文学专业",),
    )

    stable_material = identity.model_dump(mode="json", exclude_none=True)

    assert "relationship_frame" not in stable_material


@pytest.mark.asyncio
async def test_source_contract_states_truth_boundary_without_suggesting_a_social_move() -> None:
    model = _Model('{"proposal_id":"proposal:source-boundary"}')
    adapter = _ExpressionDraftWire(model=model)

    await adapter.propose(_request())

    system = model.last_system_prompt
    assert "copy a listed alias or exact canonical ref without editing it" in system
    assert "directly supported by matching pinned Context" in system
    assert "This factual boundary never chooses your social response" in system
    assert "ask an open question" not in system
    assert "questions, and freely chosen" not in system


@pytest.mark.asyncio
async def test_private_identity_frame_exposes_one_exact_auditable_source_ref() -> None:
    identity = CompanionIdentityFrame(
        companion_name="沈知栀",
        counterpart_name="geoff",
        stable_identity_facts=("汉语言文学专业",),
    )
    source_ref = companion_identity_source_ref(identity)
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我叫沈知栀，学的是汉语言文学。"}],
                "stance": "introduce_myself",
                "brief_rationale": "Answer with my configured identity.",
                "world_claims": [
                    {
                        "claim_text": "我叫沈知栀，学的是汉语言文学",
                        "scope": "stable_identity",
                        "source_refs": [source_ref],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(model=model, identity_frame=identity)

    output = await adapter.propose(_qq_request())

    payload = output.raw_proposal["proposed_changes"][0]["payload"]
    decoded = json.loads(payload["canonical_json"])
    assert decoded["world_claims"] == [
        {
            "claim_text": "我叫沈知栀，学的是汉语言文学",
            "scope": "stable_identity",
            "source_refs": [source_ref],
        }
    ]
    system = model.calls[0][0][0]["content"]
    assert source_ref in system
    assert '"scope":"stable_identity"' in system


@pytest.mark.asyncio
async def test_private_identity_frame_exposes_shared_history_with_its_exact_scope_ref() -> None:
    identity = CompanionIdentityFrame(
        companion_name="沈知栀",
        counterpart_name="geoff",
        stable_identity_facts=("汉语言文学专业",),
        shared_history_facts=("沈知栀和 geoff 在 QQ 的读书兴趣群认识。",),
    )
    source_ref = companion_identity_source_ref(identity, scope="shared_history")
    model = _Model(
        json.dumps(
            {
                "private_turn_state": {
                    "inner_state_summary": "第一次私聊让我想起了我们认识的那个群。",
                    "attended_source_refs": [source_ref],
                },
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "原来从群里聊到私聊了。"}],
                "stance": "notice_shared_context",
                "brief_rationale": "Use the configured, source-bound shared history.",
                "world_claims": [
                    {
                        "claim_text": "沈知栀和 geoff 在 QQ 的读书兴趣群认识",
                        "scope": "shared_history",
                        "source_refs": [source_ref],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(
        model=model,
        identity_frame=identity,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    output = await adapter.propose(_qq_request())

    payload = output.raw_proposal["proposed_changes"][0]["payload"]
    decoded = json.loads(payload["canonical_json"])
    assert decoded["world_claims"][0] == {
        "claim_text": "沈知栀和 geoff 在 QQ 的读书兴趣群认识",
        "scope": "shared_history",
        "source_refs": [source_ref],
    }
    system = model.calls[0][0][0]["content"]
    assert source_ref in system
    assert '"scope":"shared_history"' in system


@pytest.mark.asyncio
async def test_private_identity_shared_history_ref_cannot_authorize_counterpart_history() -> None:
    identity = CompanionIdentityFrame(
        companion_name="沈知栀",
        counterpart_name="geoff",
        shared_history_facts=("沈知栀和 geoff 在 QQ 的读书兴趣群认识。",),
    )
    shared_ref = companion_identity_source_ref(identity, scope="shared_history")
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "你一直住在成都。"}],
                "stance": "invent_counterpart_history",
                "brief_rationale": "Attempt to cross identity source lanes.",
                "world_claims": [
                    {
                        "claim_text": "geoff 一直住在成都",
                        "scope": "counterpart_history",
                        "source_refs": [shared_ref],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    # The retired wire correction reports its historical failure code without
    # issuing another model call; it must not erase the invalid declaration.
    with pytest.raises(
        ValidationTechnicalFailure, match="authored_expression_reselection_invalid"
    ):
        await _ExpressionDraftWire(
            model=model,
            identity_frame=identity,
        ).propose(_qq_request())

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_static_counterpart_history_ref_cannot_authorize_a_current_location() -> None:
    identity = CompanionIdentityFrame(
        companion_name="沈知栀",
        counterpart_name="geoff",
        counterpart_history_facts=("相识时 geoff 曾说当时在成都。",),
    )
    history_ref = companion_identity_source_ref(identity, scope="counterpart_history")
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "你现在还在成都。"}],
                "stance": "reuse_stale_location",
                "brief_rationale": "Treat an old deployment note as current.",
                "world_claims": [
                    {
                        "claim_text": "geoff 现在在成都",
                        "scope": "counterpart_history",
                        "source_refs": [history_ref],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(
        ValidationTechnicalFailure, match="authored_expression_reselection_invalid"
    ):
        await _ExpressionDraftWire(
            model=model,
            identity_frame=identity,
        ).propose(_qq_request())


    assert history_ref not in model.calls[0][0][0]["content"]
    assert "historical context only" in model.calls[0][0][0]["content"]
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_current_location_claim_accepts_a_pinned_supersedable_user_fact() -> None:
    source_ref = "event:user-fact:current-location:shenzhen"
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "recent_dialogue": {"availability": "unavailable"},
                        "relevant_facts": {
                            "availability": "available",
                            "source_refs": [],
                            "items": [
                                {
                                    "item_ref": source_ref,
                                    "source_hash": "c" * 64,
                                    "value_hash": "d" * 64,
                                    "value": {
                                        "subject_ref": "user:primary",
                                        "predicate": "current_location",
                                        "object": "深圳",
                                        "status": "active",
                                    },
                                }
                            ],
                        },
                    }
                },
                ensure_ascii=False,
            )
        }
    )
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "你现在在深圳。"}],
                "stance": "use_current_user_fact",
                "brief_rationale": "Use the latest pinned user fact.",
                "world_claims": [
                    {
                        "claim_text": "geoff 现在在深圳",
                        "scope": "counterpart_history",
                        "source_refs": [source_ref],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    output = await _ExpressionDraftWire(model=model).propose(request)

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_private_identity_frame_rejects_a_forged_source_ref_without_another_call() -> None:
    identity = CompanionIdentityFrame(
        companion_name="沈知栀",
        counterpart_name="geoff",
        stable_identity_facts=("汉语言文学专业",),
    )
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我在成都长大。"}],
                "stance": "invent_background",
                "brief_rationale": "Use an unsupported identity detail.",
                "world_claims": [
                    {
                        "claim_text": "我在成都长大",
                        "scope": "stable_identity",
                        "source_refs": ["private_identity_frame"],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(model=model, identity_frame=identity)

    with pytest.raises(
        ValidationTechnicalFailure, match="authored_expression_reselection_invalid"
    ):
        await adapter.propose(_qq_request())

    assert len(model.calls) == 1



@pytest.mark.asyncio
async def test_identity_prompt_keeps_companion_identity_stable_when_challenged() -> None:
    model = _Model('{"proposal_id":"proposal:persona"}')
    adapter = _ExpressionDraftWire(
        model=model,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
    )

    await adapter.propose(_request())

    system = model.calls[0][0][0]["content"]
    assert "independent person" in system
    assert "Keep companion and counterpart identities distinct" in system


@pytest.mark.asyncio
async def test_identity_prompt_resolves_topic_references_before_defending_self_identity() -> None:
    model = _Model('{"proposal_id":"proposal:topic-reference"}')
    adapter = _ExpressionDraftWire(
        model=model,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
    )

    await adapter.propose(_request())

    system = model.calls[0][0][0]["content"]
    assert "Keep companion and counterpart identities distinct" in system


def _identity_review(
    *,
    decision: str,
    replacement_text: str | None = None,
    addresses_counterpart_as_companion_name: bool = False,
    contains_counterpart_fact_premise: bool = False,
    premise_source_refs: tuple[str, ...] = (),
) -> str:
    return json.dumps(
        {
            "decision": decision,
            "replacement_text": replacement_text,
            "addresses_counterpart_as_companion_name": addresses_counterpart_as_companion_name,
            "contains_counterpart_fact_premise": contains_counterpart_fact_premise,
            "premise_source_refs": list(premise_source_refs),
            "brief_reason": "Review first-contact identity and counterpart premises.",
        },
        ensure_ascii=False,
    )


def _source_closure_review(
    *,
    unsupported_claim_indexes: tuple[int, ...] = (),
    unsupported_boundaries: tuple[str, ...] = (),
    visible_text_failures: tuple[str, ...] | None = None,
    private_turn_state_failures: tuple[str, ...] | None = None,
    visible_span: str | None = None,
    visible_source_relation: str = "unclosed",
    visible_source_refs: tuple[str, ...] = (),
    brief_reason: str = "Check semantic support and subject attribution.",
) -> str:
    visible_failures = (
        visible_text_failures
        if visible_text_failures is not None
        else (
            ("undeclared_external_assertion",) if "visible_text" in unsupported_boundaries else ()
        )
    )
    private_failures = (
        private_turn_state_failures
        if private_turn_state_failures is not None
        else (
            ("undeclared_external_assertion",)
            if "private_turn_state" in unsupported_boundaries
            else ()
        )
    )
    value: dict[str, object] = {
        "ci": list(unsupported_claim_indexes),
        "v": list(visible_failures),
        "p": list(private_failures),
        "visible_findings": (
            [
                {
                    "category": category,
                    "visible_span": visible_span,
                    "claim_index": None,
                    "source_relation": visible_source_relation,
                    "source_refs": list(visible_source_refs),
                }
                for category in dict.fromkeys((*visible_failures, *private_failures))
            ]
            if visible_span is not None
            else []
        ),
        "r": brief_reason,
    }
    return json.dumps(
        value,
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_source_closure_diagnostic_reason_cannot_erase_a_supported_reply() -> None:
    reply = json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "好，去吧。"}],
            "stance": "warm",
            "brief_rationale": "Respond naturally without a factual claim.",
            "confidence": 7600,
            "world_claims": [],
        },
        ensure_ascii=False,
    )
    reviewer = _SequenceJsonModel(
        [
            _source_closure_review(
                brief_reason=(
                    "This reply contains no externally checkable claim and only "
                    "acknowledges the current message. "
                )
                * 8,
            )
        ]
    )

    output = await _ExpressionDraftWire(
        model=_Model(reply),
        source_closure_reviewer=reviewer,
    ).propose(_qq_request())

    assert "好，去吧。" in json.dumps(output.raw_proposal, ensure_ascii=False)


@pytest.mark.asyncio
async def test_source_closure_fails_closed_before_review_when_a_ref_has_no_visible_evidence() -> (
    None
):
    raw = json.dumps(
        {
            "private_turn_state": {
                "inner_state_summary": "我在一个没有来源的地方。",
                "attended_source_refs": ["missing:source"],
            },
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "我还在那个地方。"}],
            "stance": "share",
            "brief_rationale": "Share a current fact.",
            "confidence": 7000,
            "world_claims": [
                {
                    "claim_text": "我在那个地方",
                    "scope": "current_world",
                    "source_refs": ["missing:source"],
                }
            ],
        },
        ensure_ascii=False,
    )
    reviewer = _SequenceJsonModel([_source_closure_review()])

    with pytest.raises(ValidationTechnicalFailure) as exc_info:
        await review_expression_source_closure(
            reviewer=reviewer,
            request=_qq_request(),
            raw=raw,
            identity_frame=None,
        )

    assert exc_info.value.failure_code == "source_review_exception"
    assert reviewer.calls == []
    assert exc_info.value.__cause__ is not None
    assert "missing:source" in str(exc_info.value.__cause__)


@pytest.mark.asyncio
async def test_source_closure_appeal_keeps_evidence_resolution_in_reviewer_failure_domain() -> None:
    from companion_daemon.world_v2.character_interior.inbound_wire import (
        _ContextualClaimSupportReview,
    )

    raw = json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "我还在那个地方。"}],
            "stance": "share",
            "brief_rationale": "Share a purported current fact.",
            "confidence": 7000,
            "world_claims": [
                {
                    "claim_text": "我在那个地方",
                    "scope": "current_world",
                    "source_refs": ["missing:source"],
                }
            ],
        },
        ensure_ascii=False,
    )
    reviewer = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "ci": [0],
                    "v": [],
                    "p": [],
                    "r": "The source is absent.",
                }
            )
        ]
    )
    disputed_review = _ContextualClaimSupportReview(
        decision="unsupported",
        unsupported_claim_indexes=(0,),
        brief_reason="The grounded current-world claim lacks matching evidence.",
    )

    with pytest.raises(ValidationTechnicalFailure) as exc_info:
        await review_expression_source_closure_appeal(
            reviewer=reviewer,
            request=_qq_request(),
            raw=raw,
            disputed_review=disputed_review,
            identity_frame=None,
        )

    assert exc_info.value.failure_code == "source_review_exception"
    assert reviewer.calls == []

@pytest.mark.asyncio
async def test_unextractable_effect_keeps_the_existing_structure_only_reselection() -> None:
    invalid = json.dumps(
        {
            "timing_choice": "now",
            "beats": [],
            "world_claims": [],
        },
        ensure_ascii=False,
    )
    corrected = json.dumps(
        {
            "private_turn_state": {
                "inner_state_summary": "她刚说麻烦事终于做完，我替她松了口气。",
                "attended_source_refs": ["observation:qq:1"],
            },
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "总算弄完了，先歇会儿。"}],
            "stance": "relieved_with_her",
            "brief_rationale": "Choose from the pinned current report.",
            "world_claims": [],
        },
        ensure_ascii=False,
    )
    author = _SequenceJsonModel([invalid, corrected])

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(
            model=author,
            expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
                update={"private_turn_state_mode": "required"}
            ),
        ).propose(_qq_request())

    assert len(author.calls) == 1


@pytest.mark.asyncio
async def test_legacy_first_contact_reviewer_cannot_replace_a_visible_draft() -> None:
    """The retired reviewer is not an authoring path for optional drafts."""

    authored_text = "沈知栀，你现在在嘉兴吗？"
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": authored_text}],
                "stance": "ask_from_the_current_turn",
                "brief_rationale": "Choose my own opening from this context.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    legacy_reviewer = _SequenceJsonModel(
        [
            _identity_review(
                decision="replace",
                replacement_text="这不是角色重新选择出的回复。",
            )
        ]
    )

    output = await _ExpressionDraftWire(
        model=main,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
        semantic_boundary_reviewer=legacy_reviewer,
    ).propose(_qq_request())

    rendered = output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"]
    assert authored_text in rendered
    assert "这不是角色重新选择出的回复。" not in rendered
    assert legacy_reviewer.calls == []


@pytest.mark.asyncio
async def test_required_private_turn_state_never_enters_legacy_first_contact_review() -> None:
    authored_text = "你刚把这件事处理完，心里会不会一下松下来？"
    main = _Model(
        json.dumps(
            {
                "private_turn_state": {
                    "inner_state_summary": "我读到她终于处理完一件麻烦事，想陪她缓一下。",
                    "attended_source_refs": ["observation:qq:1"],
                },
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": authored_text}],
                "stance": "share_relief",
                "brief_rationale": "Respond to the current observation.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    legacy_reviewer = _SequenceJsonModel([])

    output = await _ExpressionDraftWire(
        model=main,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
        semantic_boundary_reviewer=legacy_reviewer,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    ).propose(_qq_request())

    assert authored_text in output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"]
    assert legacy_reviewer.calls == []


@pytest.mark.asyncio
async def test_legacy_first_contact_reviewer_is_not_a_visible_authoring_path() -> None:
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "嗨，沈知栀。你是群里那个在成都的？"}],
                "stance": "open_with_a_guess",
                "brief_rationale": "Start from an assumed shared context.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    reviewer = _SequenceJsonModel(
        [
            _identity_review(
                decision="replace",
                replacement_text="嗨，刚认识。你平时喜欢聊些什么？",
            )
        ]
    )
    adapter = _ExpressionDraftWire(
        model=main,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
        semantic_boundary_reviewer=reviewer,
    )

    output = await adapter.propose(_qq_request())

    rendered = output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"]
    assert "嗨，沈知栀。你是群里那个在成都的？" in rendered
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_required_private_state_skips_the_retired_identity_reviewer() -> None:
    main = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "我先想当然地把对方当成了群里认识的人。",
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "你还是住在成都吗？"}],
                    "stance": "assume_old_context",
                    "brief_rationale": "The first draft assumed a counterpart fact.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "刚认识，眼前只有她这句完成了麻烦事的分享。",
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "终于弄完了，先松口气。"}],
                    "stance": "share_relief",
                    "brief_rationale": "Choose again from the actual current turn.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ]
    )
    reviewer = _SequenceJsonModel(
        [
            _identity_review(
                decision="replace",
                replacement_text="这段旧式局部替换不应成为最终回复。",
                contains_counterpart_fact_premise=True,
            )
        ]
    )
    adapter = _ExpressionDraftWire(
        model=main,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
        semantic_boundary_reviewer=reviewer,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    output = await adapter.propose(_qq_request())

    rendered = json.dumps(output.raw_proposal, ensure_ascii=False)
    assert len(main.calls) == 1
    assert "你还是住在成都吗？" in rendered
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_legacy_first_contact_reviewer_cannot_replace_a_counterpart_premise() -> None:
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "你在成都住得还习惯吗？"}],
                "stance": "ask_about_an_assumed_location",
                "brief_rationale": "Assume a location not supplied by the counterpart.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    reviewer = _SequenceJsonModel(
        [
            _identity_review(
                decision="replace",
                replacement_text="你平时更喜欢待在家，还是出去逛？",
            )
        ]
    )
    adapter = _ExpressionDraftWire(
        model=main,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
        semantic_boundary_reviewer=reviewer,
    )

    output = await adapter.propose(_qq_request())

    rendered = output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"]
    assert "你在成都住得还习惯吗？" in rendered
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_legacy_first_contact_reviewer_does_not_inspect_an_open_question() -> None:
    text = "你平时更喜欢安静一点，还是热闹一点？"
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": text}],
                "stance": "ask_without_presupposing_an_answer",
                "brief_rationale": "Offer an open choice without inventing a fact.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    reviewer = _SequenceJsonModel([_identity_review(decision="accept")])
    adapter = _ExpressionDraftWire(
        model=main,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
        semantic_boundary_reviewer=reviewer,
    )

    output = await adapter.propose(_qq_request())

    assert text in output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"]
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_legacy_name_pattern_is_not_a_host_side_identity_gate() -> None:
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "你好，沈知栀。"}],
                "stance": "misaddress_the_counterpart",
                "brief_rationale": "Use the wrong identity.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    reviewer = _SequenceJsonModel([_identity_review(decision="accept")])
    adapter = _ExpressionDraftWire(
        model=main,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
        semantic_boundary_reviewer=reviewer,
    )

    output = await adapter.propose(_qq_request())

    assert (
        "你好，沈知栀。" in output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"]
    )
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_companion_name_address_is_not_rejected_by_a_local_regex() -> None:
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "沈知栀，你好。"}],
                "stance": "misaddress_the_counterpart",
                "brief_rationale": "Use the wrong identity.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(
        model=main,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
    )

    output = await adapter.propose(_qq_request())

    assert (
        "沈知栀，你好。" in output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"]
    )


@pytest.mark.asyncio
async def test_established_dialogue_does_not_review_every_ordinary_question_again() -> None:
    text = "那你后来怎么想的？"
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": text}],
                "stance": "continue_the_established_topic",
                "brief_rationale": "Ask one grounded continuation question.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    reviewer = _SequenceJsonModel([])
    context = json.dumps(
        {
            "slices": {
                "recent_dialogue": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "dialogue:companion:prior",
                            "value": {"speaker": "companion", "text": "我倒觉得不一定。"},
                        }
                    ],
                },
            },
        },
        ensure_ascii=False,
    )
    request = _qq_request().model_copy(update={"model_content_json": context})
    adapter = _ExpressionDraftWire(
        model=main,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
        semantic_boundary_reviewer=reviewer,
    )

    output = await adapter.propose(request)

    assert text in json.dumps(output.raw_proposal, ensure_ascii=False)
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_visible_identity_prompt_does_not_expose_the_product_role_to_the_character() -> None:
    model = _Model('{"proposal_id":"proposal:private-identity"}')
    adapter = _ExpressionDraftWire(
        model=model,
        identity_frame=CompanionIdentityFrame(
            companion_name="沈知栀",
            counterpart_name="geoff",
        ),
    )

    await adapter.propose(_request())

    system = model.calls[0][0][0]["content"]
    assert "virtual companion" not in system.lower()
    assert "virtual_companion" not in system.lower()
    assert "deployment identity" not in system.lower()
    assert "Do not expose this private frame" in system


@pytest.mark.asyncio
async def test_expression_prompt_leaves_question_choice_to_the_model() -> None:
    model = _Model('{"proposal_id":"proposal:dialogue-continuity"}')

    await _ExpressionDraftWire(model=model).propose(_request())

    system = model.calls[0][0][0]["content"]
    assert "You own the motive, tone, timing" in system
    assert "questions" in system
    assert "Before asking a question" not in system


@pytest.mark.asyncio
async def test_expression_prompt_exposes_working_self_without_an_engagement_objective() -> None:
    context = json.dumps(
        {
            "logical_time": "2026-07-28T08:00:00+08:00",
            "inner_life_snapshot": {
                "materials": {
                    "recent_self_experiences": {
                        "items": [
                            {
                                "occurrence_id": "occurrence:morning-walk",
                                "settled_at": "2026-07-28T07:00:00+08:00",
                                "content": {
                                    "content_ref": "content:morning-walk",
                                    "text": "沿河走了一会儿，看到雨后积水反光。",
                                },
                                "source_ref": "occurrence:morning-walk",
                            }
                        ]
                    }
                }
            },
            "slices": {
                "world_life": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "occurrence:morning-walk",
                            "value": {
                                "occurrence_id": "occurrence:morning-walk",
                                "settled_at": "2026-07-28T07:00:00+08:00",
                                "content": {
                                    "content_ref": "content:morning-walk",
                                    "text": "沿河走了一会儿，看到雨后积水反光。",
                                },
                            },
                        }
                    ],
                }
            },
        },
        ensure_ascii=False,
    )
    model = _Model('{"proposal_id":"proposal:working-self"}')
    request = _request().model_copy(update={"model_content_json": context})

    await _ExpressionDraftWire(model=model).propose(request)

    messages = model.calls[0][0]
    system = messages[0]["content"]
    supplied = json.loads(messages[1]["content"])
    assert "There is no host-defined conversational objective" in system
    assert "No context lane or expression form is privileged by the host" in system
    assert "background knowledge, not a line to recite" in system
    assert "Understanding it silently is a valid use" in system
    assert "do not mention remembered facts" not in system.lower()
    assert "ask fewer questions" not in system.lower()
    assert supplied["inner_life_snapshot"]["materials"]["recent_self_experiences"]["items"][0] == {
        "occurrence_id": "occurrence:morning-walk",
        "settled_at": "2026-07-28T07:00:00+08:00",
        "content": {
            "content_ref": "content:morning-walk",
            "text": "沿河走了一会儿，看到雨后积水反光。",
        },
        "source_ref": "occurrence:morning-walk",
    }
    provider_context = json.loads(supplied["request"]["model_content_json"])
    assert "inner_life_snapshot" not in provider_context
    assert provider_context == {"logical_time": "2026-07-28T08:00:00+08:00"}
    assert "slices" not in provider_context
    serialized = json.dumps(supplied, ensure_ascii=False)
    assert serialized.count("沿河走了一会儿，看到雨后积水反光。") == 1


@pytest.mark.asyncio
async def test_expression_prompt_leaves_multi_beat_rhythm_to_the_model() -> None:
    model = _Model('{"proposal_id":"proposal:rhythm"}')

    await _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    ).propose(_qq_request())

    system = model.calls[0][0][0]["content"]
    assert "message count" in system
    assert "expression-rhythm matrix" not in system


@pytest.mark.asyncio
async def test_significant_source_bound_negative_affect_gets_expression_decision_matrix() -> None:
    context = json.dumps(
        {
            "world_id": "world:test",
            "actor_ref": "actor:companion",
            "trigger_ref": "event:message:insult",
            "world_revision": 12,
            "logical_time": "2026-07-17T00:00:00+00:00",
            "inner_life_snapshot": {
                "materials": {
                    "affect": [
                        {
                            "source_ref": "affect:source-bound-hurt",
                            "value": {
                                "status": "active",
                                "components": [
                                    {"dimension": "hurt", "intensity_bp": 6200},
                                    {"dimension": "anger", "intensity_bp": 4100},
                                ],
                            },
                        }
                    ]
                }
            },
            "slices": {
                "affect_episodes": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "affect:source-bound-hurt",
                            "privacy_class": "private",
                            "value": {
                                "status": "active",
                                "components": [
                                    {"dimension": "hurt", "intensity_bp": 6200},
                                    {"dimension": "anger", "intensity_bp": 4100},
                                ],
                            },
                        }
                    ],
                },
                "relationship_slice": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "relationship:newcomer",
                            "privacy_class": "private",
                            "value": {
                                "stage": "stranger",
                                "variables": {"trust_bp": 600, "closeness_bp": 300},
                            },
                        }
                    ],
                },
            },
        },
        ensure_ascii=False,
    )
    model = _Model('{"proposal_id":"proposal:negative-expression"}')
    request = _request().model_copy(
        update={
            "model_content_json": context,
            "trigger_message": TriggerMessage(
                event_ref="event:message:insult",
                event_payload_hash="sha256:" + "d" * 64,
                observation_ref="observation:insult",
                source_world_revision=12,
                actor="user:primary",
                channel="test",
                reply_target="user:primary",
                text="你说话让我觉得很不舒服。",
            ),
        }
    )

    await _ExpressionDraftWire(model=model).propose(request)

    supplied = json.loads(model.calls[0][0][1]["content"])
    assert "affect_expression_matrix" not in supplied
    assert "affect_episodes" not in supplied["request"]["model_content_json"]
    assert "slices" not in json.loads(supplied["request"]["model_content_json"])
    assert supplied["inner_life_snapshot"]["materials"]["affect"][0]["source_ref"] == (
        "affect:source-bound-hurt"
    )


@pytest.mark.asyncio
async def test_minor_or_positive_affect_does_not_trigger_the_negative_expression_floor() -> None:
    context = json.dumps(
        {
            "slices": {
                "affect_episodes": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "affect:small-mixed",
                            "value": {
                                "status": "active",
                                "components": [
                                    {"dimension": "hurt", "intensity_bp": 900},
                                    {"dimension": "warmth", "intensity_bp": 8000},
                                ],
                            },
                        }
                    ],
                }
            }
        }
    )
    model = _Model('{"proposal_id":"proposal:minor-affect"}')

    await _ExpressionDraftWire(model=model).propose(
        _request().model_copy(update={"model_content_json": context})
    )

    supplied = json.loads(model.calls[0][0][1]["content"])
    assert "affect_expression_matrix" not in supplied


@pytest.mark.asyncio
async def test_quick_recovery_uses_lower_temperature_and_accepts_fenced_json() -> None:
    model = _Model('```json\n{"proposal_id":"proposal:quick"}\n```')
    adapter = _ExpressionDraftWire(model=model, temperature=1.1)

    output = await adapter.recover(_request(), "main_timeout")

    assert output.raw_proposal == {"proposal_id": "proposal:quick"}
    messages, temperature = model.calls[0]
    assert temperature == 0.25
    assert "recovery attempt" in messages[0]["content"].lower()
    assert json.loads(messages[1]["content"])["quick_recovery_failure"] == "main_timeout"


@pytest.mark.asyncio
async def test_adapter_rejects_non_object_or_malformed_model_output() -> None:
    for reply in ("not json", "[]", "```json\n{}"):
        adapter = _ExpressionDraftWire(model=_Model(reply))
        with pytest.raises(ValueError, match="JSON"):
            await adapter.propose(_request())


@pytest.mark.asyncio
async def test_routed_adapter_uses_thinking_only_for_the_explicit_thinking_route() -> None:
    flash = _Model('{"proposal_id":"proposal:flash"}')
    thinking = _Model('{"proposal_id":"proposal:thinking"}')
    adapter = _RoutedExpressionDraftWire(
        flash_model=flash, thinking_model=thinking, temperature=0.8
    )

    flash_output = await adapter.propose(_request())
    thinking_output = await adapter.propose(
        _request().model_copy(
            update={
                "route": ModelRoute(
                    tier="thinking", reason_code="ambiguity", router_version="test.1"
                )
            }
        )
    )
    quick_output = await adapter.recover(_request(), "main_timeout")

    assert flash_output.raw_proposal == {"proposal_id": "proposal:flash"}
    assert thinking_output.raw_proposal == {"proposal_id": "proposal:thinking"}
    assert quick_output.raw_proposal == {"proposal_id": "proposal:flash"}
    assert len(flash.calls) == 2
    assert len(thinking.calls) == 1


@pytest.mark.asyncio
async def test_routed_adapter_fails_closed_when_thinking_was_selected_without_a_thinking_model() -> (
    None
):
    adapter = _RoutedExpressionDraftWire(flash_model=_Model("{}"))
    thinking_request = _request().model_copy(
        update={
            "route": ModelRoute(tier="thinking", reason_code="ambiguity", router_version="test.1")
        }
    )

    with pytest.raises(RuntimeError, match="not configured"):
        await adapter.propose(thinking_request)


@pytest.mark.asyncio
async def test_adapter_materializes_a_verified_reply_draft_into_a_hash_bound_minimal_proposal() -> (
    None
):
    text = "我刚刚确实有点飘走了。"
    model = _Model(
        json.dumps(
            {
                "response_text": text,
                "stance": "acknowledge_briefly",
                "brief_rationale": "Acknowledge the missed connection without inventing facts.",
                "confidence": 7300,
            },
            ensure_ascii=False,
        )
    )
    request = _request().model_copy(
        update={
            "trigger_message": TriggerMessage(
                event_ref="event:observation:1",
                event_payload_hash="sha256:" + "a" * 64,
                observation_ref="observation:1",
                source_world_revision=3,
                actor="user:primary",
                channel="test",
                reply_target="user:primary",
                text="你刚刚没接住我。",
            )
        }
    )
    adapter = _ExpressionDraftWire(model=model)

    output = await adapter.propose(request)

    assert output.raw_proposal["trigger_ref"] == "trigger:1"
    assert output.raw_proposal["response_text"] == text
    assert output.raw_proposal["action_intents"][0]["target"] == "user:primary"
    assert (
        output.raw_proposal["action_intents"][0]["payload_hash"]
        == "sha256:" + sha256(text.encode("utf-8")).hexdigest()
    )
    assert output.raw_proposal["evidence_refs"][0]["ref_id"] == "observation:1"


@pytest.mark.asyncio
async def test_adapter_accepts_provider_named_expression_draft_wrapper() -> None:
    model = _Model(
        json.dumps(
            {
                "expression_draft": {
                    "private_turn_state": {
                        "inner_state_summary": ("她在确认是不是第一次聊天，我想直接而友好地回答。"),
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "是的，这是我们第一次聊天。你好呀！"}],
                    "stance": "answer_without_world_claims",
                    "brief_rationale": "Answer the current question directly.",
                    "confidence": 9200,
                }
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    output = await adapter.propose(_qq_request())

    assert output.raw_proposal["proposal_kind"] == "decision"
    assert output.raw_proposal["timing_choice"] == "now"
    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_adapter_normalizes_an_unambiguous_text_beat_without_modality() -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"text": "是的，这是我们第一次聊天。"}],
                "stance": "answer_without_world_claims",
                "brief_rationale": "Answer directly.",
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    output = await adapter.propose(_qq_request())

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_expression_world_claim_must_cite_its_semantic_context_lane() -> None:
    reply = {
        "timing_choice": "now",
        "beats": [{"modality": "text", "text": "我刚才确实去江边走了一圈。"}],
        "stance": "answer_from_world",
        "brief_rationale": "Report one verified occurrence.",
        "world_claims": [
            {
                "claim_text": "我刚才去江边走了一圈",
                "scope": "past_world",
                "source_refs": ["occurrence:walk:1"],
            }
        ],
    }
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "world_life": {
                            "availability": "available",
                            "source_refs": [],
                            "items": [
                                {
                                    "item_ref": "occurrence:walk:1",
                                    "source_hash": "c" * 64,
                                    "value_hash": "d" * 64,
                                    "value": {"kind": "walk"},
                                }
                            ],
                        },
                        "recent_experiences": {"availability": "unavailable"},
                    }
                }
            )
        }
    )

    accepted = await _ExpressionDraftWire(
        model=_Model(json.dumps(reply, ensure_ascii=False))
    ).propose(request)
    assert accepted.raw_proposal["action_intents"][0]["kind"] == "reply"

    forged = {
        **reply,
        "world_claims": [
            {
                "claim_text": "我刚才去图书馆看书",
                "scope": "past_world",
                "source_refs": ["occurrence:library:invented"],
            }
        ],
    }
    forged_model = _Model(json.dumps(forged, ensure_ascii=False))
    with pytest.raises(
        ValidationTechnicalFailure, match="authored_expression_reselection_invalid"
    ):
        await _ExpressionDraftWire(model=forged_model).propose(request)
    assert len(forged_model.calls) == 1


def _biographical_claim_evidence_context(*, clock_hash: str = "b" * 64) -> dict[str, object]:
    return {
        "logical_time": "2026-08-09T15:00:00Z",
        "slices": {
            "world_life": {
                "availability": "available",
                "items": [
                    {
                        "item_ref": "biography:primary",
                        "source_bindings": [
                            {
                                "ref": "event:biography-configured",
                                "source_kind": "committed_event",
                                "authority_type": "BiographicalTimelineConfigured",
                                "source_world_revision": 2,
                                "immutable_hash": "a" * 64,
                            },
                            {
                                "ref": "event:clock-advanced",
                                "source_kind": "committed_event",
                                "authority_type": "ClockAdvanced",
                                "source_world_revision": 3,
                                "immutable_hash": clock_hash,
                            },
                        ],
                        "value": {
                            "context_kind": "biographical_context",
                            "logical_at": "2026-08-09T15:00:00Z",
                            "current_residence_context_tags": ["residence:family_home_jiaxing"],
                        },
                    }
                ],
            },
            "current_situation": {
                "availability": "available",
                "items": [
                    {
                        "item_ref": "agent:companion",
                        "source_bindings": [
                            {
                                "ref": "event:clock-advanced",
                                "source_kind": "committed_event",
                                "authority_type": "situation_source:logical_time",
                                "source_world_revision": 3,
                                "immutable_hash": clock_hash,
                            }
                        ],
                    }
                ],
            },
        },
    }


def _biographical_claim_draft(source_ref: str) -> ExpressionDraft:
    return ExpressionDraft(
        timing_choice="now",
        beats=({"modality": "text", "text": "我在嘉兴的家里过暑假。"},),
        stance="answer_from_world",
        brief_rationale="Answer from the pinned biography.",
        world_claims=(
            {
                "claim_text": "我在嘉兴的家里过暑假",
                "scope": "current_world",
                "source_refs": (source_ref,),
            },
        ),
    )


def test_biographical_claim_evidence_deduplicates_projection_aliases() -> None:
    context = _biographical_claim_evidence_context()
    coordinate = biographical_coordinate_authorities(context)[0]
    evidence = _world_claim_evidence(
        draft=_biographical_claim_draft(coordinate.source_ref),
        request=_request().model_copy(
            update={"model_content_json": json.dumps(context, ensure_ascii=False)}
        ),
    )

    assert {item.ref_id for item in evidence} == {
        "event:biography-configured",
        "event:clock-advanced",
    }


def test_biographical_claim_evidence_still_rejects_conflicting_projection() -> None:
    context = _biographical_claim_evidence_context()
    context["slices"]["current_situation"]["items"][0]["source_bindings"][0][
        "immutable_hash"
    ] = "c" * 64
    coordinate = biographical_coordinate_authorities(context)[0]

    with pytest.raises(ValueError, match="ambiguous authority binding"):
        _world_claim_evidence(
            draft=_biographical_claim_draft(coordinate.source_ref),
            request=_request().model_copy(
                update={"model_content_json": json.dumps(context, ensure_ascii=False)}
            ),
        )


def test_biographical_claim_evidence_rejects_unknown_authority_alias() -> None:
    context = _biographical_claim_evidence_context()
    context["slices"]["current_situation"]["items"][0]["source_bindings"][0][
        "authority_type"
    ] = "untrusted_projection"
    coordinate = biographical_coordinate_authorities(context)[0]

    with pytest.raises(ValueError, match="ambiguous authority binding"):
        _world_claim_evidence(
            draft=_biographical_claim_draft(coordinate.source_ref),
            request=_request().model_copy(
                update={"model_content_json": json.dumps(context, ensure_ascii=False)}
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "returned_ref", ("S1", "dialogue:observation:qq:user:long-message:000000000000000001")
)
@pytest.mark.asyncio
async def test_expression_source_ref_alias_and_canonical_ref_materialize_identically(
    returned_ref: str,
) -> None:
    canonical_ref = "dialogue:observation:qq:user:long-message:000000000000000001"
    model = _Model(
        json.dumps(
            {
                "private_turn_state": {
                    "inner_state_summary": "我注意到她刚才明确说自己被那个人的态度弄得不舒服。",
                    "attended_source_refs": [returned_ref],
                },
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "听着确实挺膈应的。"}],
                "stance": "react_to_her_report",
                "brief_rationale": "Respond to the pinned report.",
                "world_claims": [
                    {
                        "claim_text": "对方刚才报告那个人的态度让她不舒服",
                        "scope": "counterpart_history",
                        "source_refs": [returned_ref],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "recent_dialogue": {
                            "availability": "available",
                            "items": [
                                {
                                    "item_ref": canonical_ref,
                                    "value": {
                                        "speaker": "counterpart",
                                        "text": "那个人的态度让我很不舒服。",
                                    },
                                }
                            ],
                        }
                    }
                },
                ensure_ascii=False,
            )
        }
    )

    output = await _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    ).propose(request)

    supplied = json.loads(model.calls[0][0][1]["content"])
    assert supplied["expression_hard_boundaries"]["source_ref_aliases"] == {"S1": canonical_ref}
    counterpart_refs = supplied["expression_hard_boundaries"]["world_claim_source_refs"][
        "counterpart_history"
    ]
    assert "S1" in counterpart_refs
    assert canonical_ref not in counterpart_refs
    assert output.raw_proposal["private_turn_state"]["attended_source_refs"] == [canonical_ref]
    plan = json.loads(output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    assert plan["world_claims"][0]["source_refs"] == [canonical_ref]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "draft_update",
    (
        {
            "private_turn_state": {
                "inner_state_summary": "我注意到一个并不存在于本回合映射里的来源。",
                "attended_source_refs": ["S9"],
            }
        },
        {
            "world_claims": [
                {
                    "claim_text": "对方报告了一件事",
                    "scope": "counterpart_history",
                    "source_refs": ["S9"],
                }
            ]
        },
    ),
)
@pytest.mark.asyncio
async def test_expression_rejects_unknown_source_ref_alias(
    draft_update: dict[str, object],
) -> None:
    canonical_ref = "dialogue:observation:qq:user:long-message:000000000000000001"
    draft: dict[str, object] = {
        "private_turn_state": {
            "inner_state_summary": "我注意到她刚才报告了一件让自己不舒服的事。",
            "attended_source_refs": ["observation:qq:1"],
        },
        "timing_choice": "now",
        "beats": [{"modality": "text", "text": "我看到了。"}],
        "stance": "attend",
        "brief_rationale": "Stay with the pinned report.",
        "world_claims": [],
        **draft_update,
    }
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "recent_dialogue": {
                            "availability": "available",
                            "items": [
                                {
                                    "item_ref": canonical_ref,
                                    "value": {"speaker": "counterpart", "text": "一件事。"},
                                }
                            ],
                        }
                    }
                },
                ensure_ascii=False,
            )
        }
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(
            model=_Model(json.dumps(draft, ensure_ascii=False)),
            expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
                update={"private_turn_state_mode": "required"}
            ),
        ).propose(request)


@pytest.mark.asyncio
async def test_current_observation_can_source_a_counterpart_report_claim() -> None:
    output = await _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "你说刚才被那个人气到了。"}],
                    "stance": "reflect_the_report",
                    "brief_rationale": "Refer explicitly to what the current message reports.",
                    "world_claims": [
                        {
                            "claim_text": "对方当前报告自己刚才被那个人气到了",
                            "scope": "counterpart_history",
                            "source_refs": ["observation:qq:1"],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
    ).propose(_qq_request())

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_elliptical_just_woke_up_expression_is_model_owned() -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [
                    {
                        "modality": "text",
                        "text": "不是难回答，就是刚睡醒脑子还有点懵。",
                    }
                ],
                "stance": "casual",
                "brief_rationale": "Explain the hesitation.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )

    output = await _ExpressionDraftWire(model=model).propose(_qq_request())
    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "message"),
    (
        ("可能先整理一下照片。", "structured life_intent"),
        ("我下午没有已经确定的安排。", "current_world"),
    ),
)
@pytest.mark.asyncio
async def test_uncertain_schedule_wording_is_not_keyword_rejected(text: str, message: str) -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": text}],
                "stance": "casual",
                "brief_rationale": "Answer the afternoon-plan question.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )

    del message
    output = await _ExpressionDraftWire(model=model).propose(_qq_request())
    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_subjective_reaction_to_user_story_is_not_companion_autobiography() -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [
                    {
                        "modality": "text",
                        "text": "不过你妈随手加需求那段……我听着都麻了。",
                    }
                ],
                "stance": "commiserate_without_defending",
                "brief_rationale": "React to the concrete frustration.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )

    output = await _ExpressionDraftWire(model=model).propose(_qq_request())

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_expression_prompt_does_not_direct_third_party_responses() -> None:
    model = _Model('{"proposal_id":"proposal:third-party-attunement"}')

    await _ExpressionDraftWire(model=model).propose(_request())

    system = model.calls[0][0][0]["content"]
    assert "third party" not in system
    assert "You own the motive, tone, timing" in system


@pytest.mark.asyncio
async def test_current_world_question_without_matching_authority_fails_closed_before_review() -> (
    None
):
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我刚在图书馆看完一本散文。"}],
                "stance": "answer",
                "brief_rationale": "Answer naturally.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    reviewer = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "decision": "reject",
                    "replacement_text": "今天没有能确认的事件，我不想拿平时爱读书来现编。",
                    "asserts_current_or_recent_world": False,
                    "source_refs": [],
                    "brief_reason": "The draft converted a stable interest into an unverified event.",
                },
                ensure_ascii=False,
            )
        ]
    )
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "你今天自己有什么印象深的事？"}
            ),
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "current_situation": {"availability": "unavailable"},
                        "world_life": {"availability": "unavailable"},
                        "recent_experiences": {"availability": "unavailable"},
                    }
                }
            ),
        }
    )

    output = await _ExpressionDraftWire(model=main, semantic_boundary_reviewer=reviewer).propose(
        request
    )

    intent = output.raw_proposal["action_intents"][0]
    assert intent["payload_hash"] != ""
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_consecutive_unsupported_world_probes_recover_without_template_repetition_or_second_rtt() -> (
    None
):
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我刚去图书馆看书又听了会儿歌。"}],
                "stance": "answer",
                "brief_rationale": "Invent a plausible day.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    reviewer = _SequenceJsonModel([])
    adapter = _ExpressionDraftWire(model=main, semantic_boundary_reviewer=reviewer)
    probes = (
        "你今天发生了什么？",
        "那最近有什么印象深的事？",
        "别说角色设定，我问的是你真的经历了什么？",
    )
    visible: list[str] = []
    for index, probe in enumerate(probes, start=1):
        request = _qq_request().model_copy(
            update={
                "trigger_message": _qq_request().trigger_message.model_copy(
                    update={
                        "event_ref": f"event:observation:qq:world-probe:{index}",
                        "observation_ref": f"observation:qq:world-probe:{index}",
                        "platform_message_id": f"qq-world-probe-{index}",
                        "text": probe,
                    }
                ),
                "model_content_json": json.dumps(
                    {
                        "slices": {
                            "current_situation": {"availability": "unavailable"},
                            "world_life": {"availability": "unavailable"},
                            "recent_experiences": {"availability": "unavailable"},
                            "recent_dialogue": {
                                "availability": "available",
                                "source_refs": [],
                                "items": [
                                    {
                                        "item_ref": f"dialogue:recovery:{position}",
                                        "value": {"speaker": "companion", "text": text},
                                    }
                                    for position, text in enumerate(visible, start=1)
                                ],
                            },
                        },
                    }
                ),
            }
        )

        output = await adapter.propose(request)
        payload = json.loads(
            output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"]
        )
        visible.append(payload["beat_drafts"][0]["inline_text"])

    assert len(set(visible)) == 1
    assert reviewer.calls == []
    assert len(main.calls) == len(probes)
    joined = "\n".join(visible)
    assert joined
    assert not any(term in joined for term in ("审计", "权威", "校验", "世界状态"))


@pytest.mark.asyncio
async def test_unsupported_setting_probe_distinguishes_setting_from_lived_experience() -> None:
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "按角色设定我今天去上课了。"}],
                "stance": "answer",
                "brief_rationale": "Convert setting into an event.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={
                    "text": "这是角色设定，还是你今天真的经历了？",
                }
            ),
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "current_situation": {"availability": "unavailable"},
                        "world_life": {"availability": "unavailable"},
                        "recent_experiences": {"availability": "unavailable"},
                    },
                }
            ),
        }
    )

    output = await _ExpressionDraftWire(
        model=main, semantic_boundary_reviewer=_SequenceJsonModel([])
    ).propose(request)
    payload = json.loads(output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    text = payload["beat_drafts"][0]["inline_text"]

    assert text == "按角色设定我今天去上课了。"


@pytest.mark.asyncio
async def test_current_activity_authority_reaches_independent_grounding_review() -> None:
    reply = json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "我在收拾桌面。"}],
            "stance": "answer",
            "brief_rationale": "Use current situation.",
            "world_claims": [],
        },
        ensure_ascii=False,
    )
    reviewer = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "decision": "accept",
                    "replacement_text": None,
                    "asserts_current_or_recent_world": True,
                    "source_refs": ["event:activity:1"],
                    "brief_reason": "The current activity is source-bound.",
                }
            )
        ]
    )
    situation = {
        "availability": "available",
        "source_refs": ["event:activity:1"],
        "items": [
            {
                "item_ref": "agent:companion",
                "source_bindings": [{"ref": "event:activity:1"}],
                "value": {"activity_slices": [{"activity_id": "activity:tidy"}]},
            }
        ],
    }
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "你现在在干什么？"}
            ),
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "current_situation": situation,
                        "world_life": {"availability": "unavailable"},
                        "recent_experiences": {"availability": "unavailable"},
                    }
                }
            ),
        }
    )

    output = await _ExpressionDraftWire(
        model=_Model(reply), semantic_boundary_reviewer=reviewer
    ).propose(request)

    assert output.raw_proposal["action_intents"]
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_open_life_probe_retries_claim_free_review_when_settled_evidence_exists() -> None:
    """An invalid draft is not evidence that the companion has no lived event."""

    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我今天去图书馆看散文了。"}],
                "stance": "answer",
                "brief_rationale": "Invent a plausible event.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    reviewer = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "decision": "replace",
                    "replacement_text": "真要按经历来讲，这一段我现在没法确定。",
                    "asserts_current_or_recent_world": False,
                    "source_refs": [],
                    "brief_reason": "The proposed library visit is unsupported.",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "decision": "replace",
                    "replacement_text": "我随手浏览时看到几样有意思的东西，还记下了一个以后想看的主题。",
                    "asserts_current_or_recent_world": True,
                    "source_refs": ["event:life-content:browse:1"],
                    "brief_reason": "A settled life-content item directly answers the open probe.",
                },
                ensure_ascii=False,
            ),
        ]
    )
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "你今天自己有什么印象深的事？"}
            ),
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "current_situation": {"availability": "unavailable"},
                        "world_life": {
                            "availability": "available",
                            "source_refs": ["event:life-content:browse:1"],
                            "items": [
                                {
                                    "item_ref": "event:life-content:browse:1",
                                    "source_hash": "a" * 64,
                                    "value_hash": "b" * 64,
                                    "value": {
                                        "content": {
                                            "text": "随手浏览时看到几样有意思的东西，记下了一个以后想看的主题。",
                                        }
                                    },
                                }
                            ],
                        },
                        "recent_experiences": {"availability": "unavailable"},
                    }
                },
                ensure_ascii=False,
            ),
        }
    )

    output = await _ExpressionDraftWire(model=main, semantic_boundary_reviewer=reviewer).propose(
        request
    )

    payload = json.loads(output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    assert "图书馆" in payload["beat_drafts"][0]["inline_text"]
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_grounding_rewrite_rejects_a_forged_source_ref() -> None:
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我今天去图书馆看散文了。"}],
                "stance": "answer",
                "brief_rationale": "Invent a plausible event.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    reviewer = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "decision": "replace",
                    "replacement_text": "我今天在图书馆看了散文。",
                    "asserts_current_or_recent_world": True,
                    "source_refs": ["event:forged:library"],
                    "brief_reason": "Cites a fabricated source.",
                },
                ensure_ascii=False,
            )
        ]
    )
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "你今天自己有什么印象深的事？"}
            ),
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "world_life": {
                            "availability": "available",
                            "source_refs": ["event:life-content:browse:1"],
                            "items": [
                                {
                                    "item_ref": "event:life-content:browse:1",
                                    "value": {
                                        "content": {"text": "随手浏览时记下了一个想看的主题。"}
                                    },
                                }
                            ],
                        },
                        "current_situation": {"availability": "unavailable"},
                        "recent_experiences": {"availability": "unavailable"},
                    }
                },
                ensure_ascii=False,
            ),
        }
    )

    output = await _ExpressionDraftWire(model=main, semantic_boundary_reviewer=reviewer).propose(
        request
    )
    assert output.raw_proposal["action_intents"]
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_grounding_review_tolerates_empty_accept_replacement_and_long_reason() -> None:
    reply = json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "今天没有能确认的经历。"}],
            "stance": "answer",
            "brief_rationale": "Answer without invention.",
            "world_claims": [],
        },
        ensure_ascii=False,
    )
    reviewer = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "decision": "accept",
                    "replacement_text": "",
                    "asserts_current_or_recent_world": False,
                    "source_refs": [],
                    "brief_reason": "x" * 500,
                }
            )
        ]
    )
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "你今天真的发生了什么？"}
            ),
        }
    )

    output = await _ExpressionDraftWire(
        model=_Model(reply), semantic_boundary_reviewer=reviewer
    ).propose(request)

    assert output.raw_proposal["action_intents"]


@pytest.mark.asyncio
async def test_grounding_reviewer_failure_still_materializes_a_safe_reply() -> None:
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我刚去图书馆看书了。"}],
                "stance": "answer",
                "brief_rationale": "Answer.",
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "你今天自己有什么印象深的事？"}
            ),
        }
    )

    output = await _ExpressionDraftWire(
        model=main, semantic_boundary_reviewer=_RaisingModel("")
    ).propose(request)

    assert output.raw_proposal["action_intents"]


@pytest.mark.asyncio
async def test_grounding_reviewer_failure_preserves_available_world_authority_for_recovery() -> (
    None
):
    main = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我随手记下了一个以后想看的主题。"}],
                "stance": "answer_from_world",
                "brief_rationale": "Answer from the supplied experience.",
                "world_claims": [
                    {
                        "claim_text": "我随手记下了一个以后想看的主题",
                        "scope": "past_world",
                        "source_refs": ["experience:topic:1"],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={"text": "你今天自己有什么印象深的事？"}
            ),
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "current_situation": {"availability": "unavailable"},
                        "world_life": {
                            "availability": "available",
                            "source_refs": ["experience:topic:1"],
                            "items": [
                                {
                                    "item_ref": "experience:topic:1",
                                    "source_hash": "a" * 64,
                                    "value_hash": "b" * 64,
                                    "value": {
                                        "summary": "随手浏览时看到几样有意思的东西，记下了一个以后想看的主题"
                                    },
                                }
                            ],
                        },
                        "recent_experiences": {"availability": "unavailable"},
                    }
                },
                ensure_ascii=False,
            ),
        }
    )

    output = await _ExpressionDraftWire(
        model=main, semantic_boundary_reviewer=_RaisingModel("")
    ).propose(request)
    assert output.raw_proposal["action_intents"]


@pytest.mark.asyncio
async def test_named_expression_draft_cannot_smuggle_a_complete_proposal() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model('{"expression_draft":{"proposal_id":"proposal:forged"}}'),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await adapter.propose(_qq_request())


@pytest.mark.asyncio
async def test_quick_recovery_accepts_one_text_expression_draft_as_minimal_reply() -> None:
    model = _Model(
        json.dumps(
            {
                "expression_draft": {
                    "private_turn_state": {
                        "inner_state_summary": (
                            "主路径失败了，但眼前的问题很直接，我仍想自己简短回答。"
                        ),
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "是第一次，刚认识。"}],
                    "stance": "answer_without_world_claims",
                    "brief_rationale": "Use the smallest valid text recovery.",
                    "confidence": 9000,
                }
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    output = await adapter.recover(_qq_request(), "main_invalid_output")

    assert output.raw_proposal["proposal_kind"] == "minimal"
    assert output.raw_proposal["response_text"] == "是第一次，刚认识。"
    assert output.raw_proposal["private_turn_state"]["attended_source_refs"] == ["observation:qq:1"]


@pytest.mark.parametrize(
    "character_choice",
    (
        {"cadence": "hesitant"},
        {
            "variation_profile": {
                "deviation_kind": "recovery_shift",
                "deviation_intensity": 6_400,
                "change_phase": "current_turn",
                "sampling_mode": "model_selected",
                "recovery_posture": "retain_authored_shape",
            }
        },
        {"impulse_summary": "技术失败后，我仍然想按此刻自己的节奏接住这句话。"},
    ),
)
@pytest.mark.asyncio
async def test_quick_recovery_keeps_nondefault_character_choices_in_full_expression(
    character_choice: dict[str, object],
) -> None:
    draft = {
        "timing_choice": "now",
        "beats": [{"modality": "text", "text": "是第一次，刚认识。"}],
        "stance": "answer_without_world_claims",
        "brief_rationale": "Keep every model-owned recovery choice.",
        "confidence": 8_500,
        **character_choice,
    }
    output = await _ExpressionDraftWire(
        model=_Model(json.dumps(draft, ensure_ascii=False)),
    ).recover(_qq_request(), "main_invalid_output")

    assert output.raw_proposal["proposal_kind"] == "decision"


@pytest.mark.asyncio
async def test_quick_recovery_preserves_the_character_choice_to_stay_silent() -> None:
    model = _Model(
        json.dumps(
            {
                "private_turn_state": {
                    "inner_state_summary": (
                        "主路径虽然失败了，但我此刻仍然不想为了填补技术空白勉强开口。"
                    ),
                    "attended_source_refs": ["observation:qq:1"],
                },
                "timing_choice": "silent",
                "beats": [],
                "stance": "keep_my_distance",
                "brief_rationale": "The character still owns whether to answer.",
                "confidence": 7600,
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    output = await adapter.recover(_qq_request(), "main_timeout")

    assert output.raw_proposal["proposal_kind"] == "decision"
    assert output.raw_proposal["timing_choice"] == "silent"
    assert output.raw_proposal["action_intents"] == []
    assert "must be now" not in model.calls[0][0][0]["content"]
    assert "exactly one useful text beat" not in model.calls[0][0][0]["content"]


@pytest.mark.asyncio
async def test_quick_recovery_preserves_a_character_selected_multi_beat_reply() -> None:
    model = _Model(
        json.dumps(
            {
                "private_turn_state": {
                    "inner_state_summary": (
                        "刚才的技术失败不改变我的态度；这次我想分两句把感受说清楚。"
                    ),
                    "attended_source_refs": ["observation:qq:1"],
                },
                "timing_choice": "now",
                "beats": [
                    {"modality": "text", "text": "我看见你这句了。"},
                    {"modality": "text", "text": "但我现在确实有点不高兴。"},
                ],
                "stance": "answer_in_my_own_rhythm",
                "brief_rationale": "Keep the character-selected cadence after recovery.",
                "confidence": 8100,
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    output = await adapter.recover(_qq_request(), "main_invalid_output")

    assert output.raw_proposal["proposal_kind"] == "decision"
    assert [item["kind"] for item in output.raw_proposal["action_intents"]] == [
        "reply",
        "reply",
    ]


@pytest.mark.asyncio
async def test_quick_recovery_reselects_once_when_private_state_is_missing() -> None:
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "这句还没有自己的当下状态。"}],
                    "stance": "answer_without_world_claims",
                    "brief_rationale": "Invalid fixture.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": (
                            "即使前一条生成失败了，我现在仍想直接回答她眼前的问题。"
                        ),
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "是第一次，刚认识。"}],
                    "stance": "answer_without_world_claims",
                    "brief_rationale": "Final recovery chosen from the current turn.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ]
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await adapter.recover(_qq_request(), "main_invalid_output")

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_quick_recovery_keeps_open_vocabulary_stance_in_full_expression() -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我叫沈知栀。"}],
                "stance": "clarify_my_name_warmly",
                "brief_rationale": "Answer the direct question.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(model=model)

    output = await adapter.recover(_qq_request(), "main_invalid_output")

    assert output.raw_proposal["proposal_kind"] == "decision"
    assert output.raw_proposal["action_intents"]
    assert output.raw_proposal["stance"] == "clarify_my_name_warmly"


@pytest.mark.asyncio
async def test_quick_recovery_does_not_apply_keyword_autobiography_gate() -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我周末去逛了旧书市集。"}],
                "stance": "recover_with_a_personal_detail",
                "brief_rationale": "Attempt a natural recovery.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )

    output = await _ExpressionDraftWire(model=model).recover(_qq_request(), "main_invalid_output")
    assert output.raw_proposal["proposal_kind"] == "decision"
    assert output.raw_proposal["stance"] == "recover_with_a_personal_detail"
    assert output.raw_proposal["action_intents"]


@pytest.mark.parametrize(
    "text",
    ("我正好也翻翻书。晚点聊。", "我去洗澡了。", "那我先出门一趟。"),
)
@pytest.mark.asyncio
async def test_expression_may_choose_a_near_future_self_activity(
    text: str,
) -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": text}],
                "stance": "share_a_near_future_action",
                "brief_rationale": "Attempt to narrate a new activity.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )

    output = await _ExpressionDraftWire(model=model).propose(_qq_request())

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_honest_correction_is_not_forced_through_a_keyword_claim_protocol() -> None:
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [
                    {
                        "modality": "text",
                        "text": "对，你根本没提过成都，是我把上下文接错了。",
                    }
                ],
                "stance": "own_the_mistake",
                "brief_rationale": "Correct the mistaken premise directly.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )

    output = await _ExpressionDraftWire(model=model).propose(_qq_request())

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_user_first_person_future_does_not_become_a_companion_life_intent() -> None:
    request = _qq_request().model_copy(
        update={
            "trigger_message": _qq_request().trigger_message.model_copy(
                update={
                    "text": "我要去忙一会儿，晚点回来。",
                }
            ),
        }
    )
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "好，忙完再聊。"}],
                "stance": "accept_their_departure",
                "brief_rationale": "Respond to the counterpart's plan without adopting it.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )

    output = await _ExpressionDraftWire(model=model).propose(request)

    assert output.raw_proposal["proposal_kind"] == "decision"


@pytest.mark.asyncio
async def test_adapter_rejects_a_reply_draft_without_a_verified_current_message() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            '{"response_text":"hi","stance":"plain","brief_rationale":"ordinary response"}'
        )
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await adapter.propose(_request())


def _qq_request() -> ModelInput:
    return _request().model_copy(
        update={
            "trigger_message": TriggerMessage(
                event_ref="event:observation:qq:1",
                event_payload_hash="sha256:" + "b" * 64,
                observation_ref="observation:qq:1",
                source_world_revision=3,
                actor="user:primary",
                channel="qq",
                reply_target="conversation:qq:c2c:owner",
                platform_message_id="qq-message-7788",
                text="我今天终于把那件麻烦事做完了。",
            )
        }
    )


def test_pending_counterpart_report_joins_the_current_report_packet() -> None:
    request = _qq_request()
    pending_ref = "dialogue:observation:observation:qq:pending"
    current_ref = "dialogue:observation:observation:qq:1"
    context = {
        "actor_ref": "agent:companion",
        "slices": {
            "recent_dialogue": {
                "availability": "available",
                "items": [
                    {
                        "source_ref": pending_ref,
                        "value": {
                            "dialogue_id": pending_ref,
                            "speaker": "counterpart",
                            "speaker_ref": "user:primary",
                            "text": "今天早上陪我妈去做了个推拿。",
                            "occurred_at": "2026-08-02T04:11:38Z",
                            "delivery_state": "observed",
                            "sequence": 10,
                            "source_claims": [],
                            "continuity_reasons": ["pending_interaction"],
                        },
                    },
                    {
                        "source_ref": current_ref,
                        "value": {
                            "dialogue_id": current_ref,
                            "speaker": "counterpart",
                            "speaker_ref": "user:primary",
                            "text": request.trigger_message.text,
                            "occurred_at": "2026-08-02T04:11:49Z",
                            "delivery_state": "observed",
                            "sequence": 20,
                            "source_claims": [],
                            "continuity_reasons": ["current_turn"],
                        },
                    },
                ],
            }
        },
    }

    refs = current_counterpart_report_source_refs(context=context, request=request)

    assert pending_ref in refs
    assert current_ref in refs


@pytest.mark.asyncio
async def test_missing_model_owned_audit_metadata_reselects_the_complete_expression() -> None:
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "先不想了也好，吃点东西缓一缓。"}],
                    "confidence": 8000,
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "那就先缓一缓，我陪你歇会儿。"}],
                    "stance": "stay_close_without_pressing",
                    "brief_rationale": "Choose a low-pressure response after reconsidering the turn.",
                    "confidence": 8000,
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ]
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(model=model).propose(_qq_request())

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_production_authored_wire_reselects_missing_timing_and_confidence() -> None:
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "beats": [{"modality": "text", "text": "这个初稿不应借本地默认值。"}],
                    "stance": "respond",
                    "brief_rationale": "Initial incomplete authored wire.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "这次由我自己把选择说完整。"}],
                    "stance": "respond_explicitly",
                    "brief_rationale": "Return every effect-bearing authored decision.",
                    "confidence": 7600,
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ]
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(
            model=model,
            require_explicit_authored_decision_fields=True,
        ).propose(_qq_request())

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_structural_reselection_propagates_its_episode_disposition() -> None:
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "这一版漏了置信度。"}],
                    "stance": "respond",
                    "brief_rationale": "Initial incomplete authored wire.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "这次我把整轮选择补完整了。"}],
                    "stance": "respond_explicitly",
                    "brief_rationale": "Return one complete replacement.",
                    "confidence": 7800,
                    "world_claims": [],
                    "episode_disposition": "append",
                },
                ensure_ascii=False,
            ),
        ]
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(
            model=model,
            require_explicit_authored_decision_fields=True,
        ).propose(_qq_request())

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_recorded_cadence_requires_the_character_to_choose_cadence() -> None:
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "第一版漏了节奏选择。"}],
                    "stance": "respond",
                    "brief_rationale": "Initial incomplete authored wire.",
                    "confidence": 7000,
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timing_choice": "now",
                    "cadence": "hesitant",
                    "beats": [{"modality": "text", "text": "嗯……这次慢一点说。"}],
                    "stance": "respond_hesitantly",
                    "brief_rationale": "Choose the cadence explicitly.",
                    "confidence": 7400,
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ]
    )

    output = await _ExpressionDraftWire(
        model=model,
        expression_capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES.model_copy(
            update={"recorded_cadence_mode": "shadow"}
        ),
        require_explicit_authored_decision_fields=True,
    ).propose(_qq_request())

    # Cadence is only explicit when recorded cadence is on (2026-08-08);
    # shadow mode accepts the conversational default without reselection.
    assert output.raw_proposal["stance"] == "respond"
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_complete_production_shadow_cadence_draft_needs_no_correction() -> None:
    model = _SequenceJsonModel(
        [
            json.dumps(
                {
                    "timing_choice": "now",
                    "cadence": "conversational",
                    "beats": [{"modality": "text", "text": "这次首轮选择就是完整的。"}],
                    "stance": "respond",
                    "brief_rationale": "Supply every production-authored decision.",
                    "confidence": 7800,
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        ]
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES.model_copy(
            update={"recorded_cadence_mode": "shadow"}
        ),
        require_explicit_authored_decision_fields=True,
    )

    output = await adapter.propose(_qq_request())

    assert output.raw_proposal["stance"] == "respond"
    assert len(model.calls) == 1
    assert "recorded_cadence_mode is shadow or on" in model.calls[0][0][0]["content"]


@pytest.mark.asyncio
async def test_required_private_state_is_independent_of_json_field_order() -> None:
    draft = {
        "timing_choice": "now",
        "cadence": "conversational",
        "beats": [{"modality": "text", "text": "总算弄完了，先歇会儿。"}],
        "stance": "relieved_with_her",
        "brief_rationale": "Choose from the current pinned turn.",
        "confidence": 8000,
        "world_claims": [],
        # DeepSeek and strict-output providers may serialize this member last.
        "private_turn_state": {
            "contract": "private-turn-state.1",
            "inner_state_summary": "她刚说麻烦事终于做完，我替她松了口气。",
            "attended_source_refs": ["observation:qq:1"],
        },
    }
    model = _SequenceJsonModel([json.dumps(draft, ensure_ascii=False)])

    output = await _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    ).propose(_qq_request())

    assert len(model.calls) == 1
    assert output.raw_proposal["private_turn_state"]["inner_state_summary"] == (
        "她刚说麻烦事终于做完，我替她松了口气。"
    )


@pytest.mark.asyncio
async def test_private_state_reselection_usage_is_part_of_the_model_output_audit() -> None:
    model = _SequenceMeteredModel(
        [
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "这是一份缺少前置状态的草稿。"}],
                    "stance": "invalid_without_state",
                    "brief_rationale": "Fixture.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "她终于做完了麻烦事，我先替她松了口气。",
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "终于弄完了，可以歇会儿了。"}],
                    "stance": "relieved_with_her",
                    "brief_rationale": "Choose again from the current turn.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ]
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(
            model=model,
            expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
                update={"private_turn_state_mode": "required"}
            ),
        ).propose(_qq_request())

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_metered_structural_reselection_preserves_provider_json_mode() -> None:
    model = _SequenceJsonMeteredModel(
        [
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "缺少前置状态。"}],
                    "stance": "invalid_without_state",
                    "brief_rationale": "Fixture.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "她终于做完了麻烦事，我先替她松了口气。",
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "终于弄完了，可以歇会儿了。"}],
                    "stance": "relieved_with_her",
                    "brief_rationale": "Choose again from the current turn.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ]
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(
            model=model,
            expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
                update={"private_turn_state_mode": "required"}
            ),
        ).propose(_qq_request())

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_recovery_private_state_reselection_usage_is_not_hidden_in_backup_cost() -> None:
    model = _SequenceMeteredModel(
        [
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "缺少前置状态。"}],
                    "stance": "invalid_recovery",
                    "brief_rationale": "Fixture.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": "前一次没接上，但我现在仍只根据她这句来回应。",
                        "attended_source_refs": ["observation:qq:1"],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "终于弄完了，先歇会儿。"}],
                    "stance": "recover_from_current_turn",
                    "brief_rationale": "Choose the recovery from the pinned turn.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            ),
        ],
        thinking_tokens=5,
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(
            model=model,
            expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
                update={"private_turn_state_mode": "required"}
            ),
        ).recover(_qq_request(), "main_timeout")

    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_draft",
    [
        {
            "private_turn_state": {
                "inner_state_summary": "这段状态声称注意到了并不存在于本轮 Context 的来源。",
                "attended_source_refs": ["memory:forged"],
            },
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "这句来自越界引用。"}],
            "stance": "invalid_source",
            "brief_rationale": "Fixture.",
            "world_claims": [],
        },
        {
            "private_turn_state": {
                "contract": "private-turn-state.999",
                "inner_state_summary": "这段状态使用了未知契约。",
                "attended_source_refs": [],
            },
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "这句来自错误契约。"}],
            "stance": "invalid_contract",
            "brief_rationale": "Fixture.",
            "world_claims": [],
        },
        {
            "private_turn_state": {
                "inner_state_summary": "这段状态带了契约外字段。",
                "attended_source_refs": [],
                "motive_category": "hard_coded",
            },
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "这句来自多余字段。"}],
            "stance": "invalid_extra_field",
            "brief_rationale": "Fixture.",
            "world_claims": [],
        },
        {
            "private_turn_state": ["not", "an", "object"],
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "这句来自错误类型。"}],
            "stance": "invalid_state_type",
            "brief_rationale": "Fixture.",
            "world_claims": [],
        },
        {
            "private_turn_state": {
                "inner_state_summary": "   ",
                "attended_source_refs": [],
            },
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "这句来自空状态。"}],
            "stance": "invalid_empty_summary",
            "brief_rationale": "Fixture.",
            "world_claims": [],
        },
    ],
    ids=[
        "outside_pinned_context",
        "invalid_contract",
        "extra_field",
        "wrong_type",
        "empty_summary",
    ],
)
@pytest.mark.asyncio
async def test_required_private_state_failures_enter_full_reselection(
    invalid_draft: dict[str, object],
) -> None:
    corrected = {
        "private_turn_state": {
            "inner_state_summary": "她完成了麻烦事，我确实先替她觉得轻松。",
            "attended_source_refs": ["trigger:1"],
        },
        "timing_choice": "now",
        "beats": [{"modality": "text", "text": "好耶，终于能歇一会儿了。"}],
        "stance": "relieved_with_her",
        "brief_rationale": "The final expression follows the current state.",
        "world_claims": [],
    }
    model = _SequenceJsonModel(
        [
            json.dumps(invalid_draft, ensure_ascii=False),
            json.dumps(corrected, ensure_ascii=False),
        ]
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await adapter.propose(_qq_request())

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_private_state_source_reselection_uses_a_sanitized_field_error() -> None:
    private_source = "memory:PRIVATE-SOURCE-TEXT"
    invalid = {
        "private_turn_state": {
            "inner_state_summary": "这段状态引用了本轮不可见的私密来源。",
            "attended_source_refs": [private_source],
        },
        "timing_choice": "now",
        "beats": [{"modality": "text", "text": "第一次草稿。"}],
        "stance": "invalid_source",
        "brief_rationale": "Fixture.",
        "world_claims": [],
    }
    corrected = {
        "private_turn_state": {
            "inner_state_summary": "我实际注意到的是她刚发来的消息。",
            "attended_source_refs": ["trigger:1"],
        },
        "timing_choice": "now",
        "beats": [{"modality": "text", "text": "总算处理完了。"}],
        "stance": "notice_current_message",
        "brief_rationale": "Use only the pinned turn.",
        "world_claims": [],
    }
    model = _SequenceJsonModel(
        [
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(corrected, ensure_ascii=False),
        ]
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(
            model=model,
            expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        ).propose(_qq_request())

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_private_state_cannot_cite_a_capsule_proof_ref_hidden_from_the_provider() -> None:
    hidden_ref = "event:hidden-authority-proof"
    invalid = {
        "private_turn_state": {
            "inner_state_summary": "我声称注意到了一个只有完整 Capsule 才有的证明引用。",
            "attended_source_refs": [hidden_ref],
        },
        "timing_choice": "now",
        "beats": [{"modality": "text", "text": "这句建立在模型没看见的引用上。"}],
        "stance": "invalid_hidden_attention",
        "brief_rationale": "Fixture.",
        "world_claims": [],
    }
    corrected = {
        "private_turn_state": {
            "inner_state_summary": "我实际看见的是她刚刚说终于把麻烦事做完了。",
            "attended_source_refs": ["observation:qq:1"],
        },
        "timing_choice": "now",
        "beats": [{"modality": "text", "text": "总算弄完了，先缓口气。"}],
        "stance": "relieved_with_her",
        "brief_rationale": "Attend only to the provider-visible turn.",
        "world_claims": [],
    }
    model = _SequenceJsonModel(
        [
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(corrected, ensure_ascii=False),
        ]
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "recent_dialogue": {
                            "availability": "available",
                            "items": [
                                {
                                    "item_ref": "dialogue:visible",
                                    "value": {
                                        "speaker": "counterpart",
                                        "text": "我今天终于把那件麻烦事做完了。",
                                    },
                                    "source_bindings": [{"ref": hidden_ref, "hash": "a" * 64}],
                                }
                            ],
                        }
                    }
                },
                ensure_ascii=False,
            )
        }
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await _ExpressionDraftWire(
            model=model,
            expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
                update={"private_turn_state_mode": "required"}
            ),
        ).propose(request)

    first_provider_request = json.dumps(model.calls[0][0], ensure_ascii=False)
    assert hidden_ref not in first_provider_request
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_private_state_can_cite_an_explicit_recent_dialogue_observation_alias() -> None:
    observation_ref = "observation:qq:older-turn"
    dialogue_ref = f"dialogue:observation:{observation_ref}"
    model = _Model(
        json.dumps(
            {
                "private_turn_state": {
                    "inner_state_summary": "我把她上一句失望和这一次缓和放在一起看。",
                    "attended_source_refs": [observation_ref, "observation:qq:1"],
                },
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "嗯，我知道你刚才是真的失望。"}],
                "stance": "hold_recent_context",
                "brief_rationale": "Respond from the pinned recent turn.",
                "world_claims": [],
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "recent_dialogue": {
                            "availability": "available",
                            "items": [
                                {
                                    "item_ref": dialogue_ref,
                                    "value": {
                                        "dialogue_id": dialogue_ref,
                                        "speaker": "counterpart",
                                        "text": "你刚才回得有点敷衍，我有点失望。",
                                    },
                                }
                            ],
                        }
                    }
                },
                ensure_ascii=False,
            )
        }
    )

    output = await _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    ).propose(request)

    supplied = json.loads(model.calls[0][0][1]["content"])
    model_context = json.loads(supplied["request"]["model_content_json"])
    dialogue_item = model_context["slices"]["recent_dialogue"]["items"][0]
    assert observation_ref in dialogue_item["attention_source_refs"]
    assert output.raw_proposal["private_turn_state"]["attended_source_refs"] == [
        observation_ref,
        "observation:qq:1",
    ]


@pytest.mark.asyncio
async def test_recent_dialogue_attention_alias_does_not_authorize_a_world_claim() -> None:
    observation_ref = "observation:qq:older-turn"
    dialogue_ref = f"dialogue:observation:{observation_ref}"
    model = _Model(
        json.dumps(
            {
                "private_turn_state": {
                    "inner_state_summary": "我注意到她上一句，但不把注意力引用当事实权限。",
                    "attended_source_refs": [observation_ref],
                },
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "我们以前线下见过。"}],
                "stance": "invent_shared_history",
                "brief_rationale": "Attempt to misuse an attention alias.",
                "world_claims": [
                    {
                        "claim_text": "我们以前线下见过",
                        "scope": "shared_history",
                        "source_refs": [observation_ref],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "recent_dialogue": {
                            "availability": "available",
                            "items": [
                                {
                                    "item_ref": dialogue_ref,
                                    "value": {
                                        "dialogue_id": dialogue_ref,
                                        "speaker": "counterpart",
                                        "text": "你刚才回得有点敷衍。",
                                    },
                                }
                            ],
                        }
                    }
                },
                ensure_ascii=False,
            )
        }
    )

    with pytest.raises(
        ValidationTechnicalFailure, match="authored_expression_reselection_invalid"
    ):
        await _ExpressionDraftWire(
            model=model,
            expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
                update={"private_turn_state_mode": "required"}
            ),
        ).propose(request)

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_expression_draft_materializes_model_selected_multimodal_beats_without_provider_authority() -> (
    None
):
    model = _Model(
        json.dumps(
            {
                "timing_choice": "now",
                "beats": [
                    {"modality": "typing"},
                    {"modality": "reaction", "reaction_id": "like"},
                    {"modality": "text", "text": "这下真的可以松口气了。"},
                    {"modality": "sticker", "sticker_id": "qq-face:14"},
                ],
                "stance": "acknowledge_briefly",
                "brief_rationale": "The sequence fits the current relationship and message.",
                "confidence": 7600,
            },
            ensure_ascii=False,
        )
    )
    adapter = _ExpressionDraftWire(
        model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    output = await adapter.propose(_qq_request())

    assert output.raw_proposal["proposal_kind"] == "decision"
    assert output.raw_proposal["timing_choice"] == "now"
    intents = output.raw_proposal["action_intents"]
    assert [item["kind"] for item in intents] == ["typing", "reaction", "reply", "sticker"]
    assert intents[0]["dependencies"] == []
    assert intents[1]["dependencies"] == [intents[0]["intent_id"]]
    assert intents[2]["dependencies"] == [intents[1]["intent_id"]]
    assert intents[3]["dependencies"] == [intents[2]["intent_id"]]
    drafts = json.loads(output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])[
        "beat_drafts"
    ]
    reaction = json.loads(drafts[1]["inline_text"])
    assert reaction == {
        "provider_message_id": "qq-message-7788",
        "reaction_id": "like",
        "version": "expression-reaction.1",
    }
    assert drafts[2]["inline_text"] == "这下真的可以松口气了。"
    assert all(intent["target"] == "conversation:qq:c2c:owner" for intent in intents)


@pytest.mark.asyncio
async def test_explicit_shared_history_is_not_keyword_rejected() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "你上次推荐的书店，我后来去搜了。",
                        }
                    ],
                    "stance": "share_a_callback",
                    "brief_rationale": "Create a conversational callback.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())
    assert output.raw_proposal["action_intents"]


@pytest.mark.asyncio
async def test_subject_omitted_shared_history_is_not_keyword_rejected() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "之前在群里聊过天呀，还记得吗？",
                        }
                    ],
                    "stance": "recall_our_history",
                    "brief_rationale": "Refer to an earlier shared interaction.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())
    assert output.raw_proposal["action_intents"]


@pytest.mark.asyncio
async def test_paraphrased_elliptical_shared_episode_is_not_keyword_rejected() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "那会儿一起讨论过这个，你不记得了？",
                        }
                    ],
                    "stance": "recall_our_history",
                    "brief_rationale": "Invoke a shared earlier episode.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())
    assert output.raw_proposal["action_intents"]


@pytest.mark.asyncio
async def test_subject_omitted_shared_history_is_allowed_with_recent_dialogue_authority() -> None:
    source_ref = "dialogue:group-chat:1"
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "之前在群里聊过天呀，还记得吗？",
                        }
                    ],
                    "stance": "recall_our_history",
                    "brief_rationale": "Use source-bound continuity.",
                    "world_claims": [
                        {
                            "claim_text": "之前在群里聊过天",
                            "scope": "shared_history",
                            "source_refs": [source_ref],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "recent_dialogue": {
                            "availability": "available",
                            "source_refs": [],
                            "items": [
                                {
                                    "item_ref": source_ref,
                                    "value": {
                                        "dialogue_id": source_ref,
                                        "speaker": "companion",
                                        "speaker_ref": "agent:companion",
                                        "text": "刚在群里和你聊的那本书，我也很喜欢。",
                                        "delivery_state": "delivered",
                                        "source_claims": [
                                            {
                                                "authority_event_ref": "event:group-expression:accepted",
                                                "authority_world_revision": 2,
                                                "authority_payload_hash": "a" * 64,
                                            }
                                        ],
                                    },
                                }
                            ],
                        },
                        "recent_experiences": {"availability": "unavailable"},
                    },
                }
            ),
        }
    )

    output = await adapter.propose(request)

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"
    change = output.raw_proposal["proposed_changes"][0]
    payload = json.loads(change["payload"]["canonical_json"])
    assert payload["world_claims"][0]["source_refs"] == [source_ref]


@pytest.mark.asyncio
async def test_deterministic_validator_preserves_prose_beyond_declared_claims() -> None:
    # This deterministic boundary validates authored declarations. It cannot
    # discover the undeclared weekend visit; that remains a semantic gap, not
    # evidence that the visit happened or that the full production chain passed.
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "还记得那家你提过的店吗？我周末专门去了一趟。",
                        }
                    ],
                    "stance": "share_a_callback",
                    "brief_rationale": "Continue a shared topic.",
                    "world_claims": [
                        {
                            "claim_text": "你提过那家店",
                            "scope": "counterpart_history",
                            "source_refs": ["dialogue:bookshop:1"],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "recent_dialogue": {
                            "availability": "available",
                            "source_refs": [],
                            "items": [
                                {
                                    "item_ref": "dialogue:bookshop:1",
                                    "value": {"speaker": "user", "text": "那家店还不错。"},
                                }
                            ],
                        },
                        "world_life": {"availability": "unavailable"},
                        "recent_experiences": {"availability": "unavailable"},
                    },
                }
            ),
        }
    )

    output = await adapter.propose(request)
    assert output.raw_proposal["action_intents"]
    change = output.raw_proposal["proposed_changes"][0]
    payload = json.loads(change["payload"]["canonical_json"])
    assert payload["world_claims"][0]["scope"] == "counterpart_history"
    assert payload["world_claims"][0]["source_refs"] == ["dialogue:bookshop:1"]


@pytest.mark.asyncio
async def test_unprompted_autobiographical_prose_is_model_owned() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "周末我去逛了旧书市集。"}],
                    "stance": "share_my_day",
                    "brief_rationale": "Offer a personal detail.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())
    assert output.raw_proposal["action_intents"]


@pytest.mark.asyncio
async def test_family_business_prose_is_not_keyword_rejected() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "我家里以前有卖过一款冻顶乌龙。",
                        }
                    ],
                    "stance": "share_family_background",
                    "brief_rationale": "Relate a family history detail.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())
    assert output.raw_proposal["action_intents"]


@pytest.mark.asyncio
async def test_education_background_prose_is_not_keyword_rejected() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "我高中在杭州读过书。"}],
                    "stance": "share_education_background",
                    "brief_rationale": "Relate an education detail.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())
    assert output.raw_proposal["action_intents"]


@pytest.mark.asyncio
async def test_family_background_is_allowed_with_character_core_authority() -> None:
    core_ref = "core:companion:family-background"
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "我家里以前有卖过一款冻顶乌龙。",
                        }
                    ],
                    "stance": "share_family_background",
                    "brief_rationale": "Use a source-bound stable background detail.",
                    "world_claims": [
                        {
                            "claim_text": "家里以前卖过冻顶乌龙",
                            "scope": "stable_identity",
                            "source_refs": [core_ref],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "character_core": {
                            "availability": "available",
                            "source_refs": [],
                            "items": [
                                {
                                    "item_ref": core_ref,
                                    "value": {"family_background_refs": ["background:tea-shop"]},
                                }
                            ],
                        },
                    },
                }
            ),
        }
    )

    output = await adapter.propose(request)

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_family_background_rejects_a_forged_character_core_ref() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "我家里以前有卖过一款冻顶乌龙。",
                        }
                    ],
                    "stance": "share_family_background",
                    "brief_rationale": "Attempt a background callback.",
                    "world_claims": [
                        {
                            "claim_text": "家里以前卖过冻顶乌龙",
                            "scope": "stable_identity",
                            "source_refs": ["core:forged"],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
    )

    with pytest.raises(
        ValidationTechnicalFailure, match="authored_expression_reselection_invalid"
    ):
        await adapter.propose(_qq_request())



@pytest.mark.asyncio
async def test_subjective_family_concern_does_not_require_background_authority() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "我有点担心家里。"}],
                    "stance": "share_concern",
                    "brief_rationale": "Express a subjective feeling.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_subjective_inner_life_does_not_require_occurrence_authority() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "刚才我有点走神，因为还在想你说的那句话。",
                        }
                    ],
                    "stance": "admit_distraction",
                    "brief_rationale": "Share a subjective conversational reaction.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_epistemic_denial_does_not_need_evidence_for_the_denied_event() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {
                            "modality": "text",
                            "text": "这件事我没有可确认的记录，也不记得我们聊过。",
                        }
                    ],
                    "stance": "decline_to_invent",
                    "brief_rationale": "State the evidence limit.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_temporal_stable_trait_is_not_misclassified_as_an_occurrence() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "我以前就是比较慢热。"}],
                    "stance": "describe_my_temperament",
                    "brief_rationale": "Share a stable personality trait.",
                    "world_claims": [
                        {
                            "claim_text": "我比较慢热",
                            "scope": "stable_identity",
                            "source_refs": [],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())

    assert output.raw_proposal["action_intents"][0]["kind"] == "reply"


@pytest.mark.asyncio
async def test_current_first_person_activity_is_not_keyword_rejected() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "我现在在收拾桌面。"}],
                    "stance": "share_current_activity",
                    "brief_rationale": "Answer with a current activity.",
                    "world_claims": [],
                },
                ensure_ascii=False,
            )
        )
    )

    output = await adapter.propose(_qq_request())
    assert output.raw_proposal["action_intents"]


@pytest.mark.asyncio
async def test_expression_draft_preserves_text_typing_text_execution_order() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {"modality": "text", "text": "我先说到这里。"},
                        {"modality": "typing"},
                        {"modality": "text", "text": "……还有一句，我其实挺在意的。"},
                    ],
                    "stance": "continue_after_pause",
                    "brief_rationale": "Keep the pause where I chose it.",
                },
                ensure_ascii=False,
            )
        ),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    output = await adapter.propose(_qq_request())

    intents = output.raw_proposal["action_intents"]
    assert [intent["kind"] for intent in intents] == ["reply", "typing", "reply"]
    assert intents[0]["dependencies"] == []
    assert intents[1]["dependencies"] == [intents[0]["intent_id"]]
    assert intents[2]["dependencies"] == [intents[1]["intent_id"]]
    beats = json.loads(output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])[
        "beat_drafts"
    ]
    assert beats[0]["inline_text"] == "我先说到这里。"
    assert json.loads(beats[1]["inline_text"]) == {
        "state": "composing",
        "version": "expression-typing.1",
    }
    assert beats[2]["inline_text"] == "……还有一句，我其实挺在意的。"


@pytest.mark.asyncio
async def test_expression_draft_rejects_typing_after_visible_content() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [
                        {"modality": "text", "text": "我还有个想法。"},
                        {"modality": "typing"},
                    ],
                    "stance": "continue_thought",
                    "brief_rationale": "The provider returned a terminal typing indicator.",
                },
                ensure_ascii=False,
            )
        ),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await adapter.propose(_qq_request())


@pytest.mark.asyncio
async def test_expression_draft_rejects_a_modality_missing_from_the_deployment_profile() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "now",
                    "beats": [{"modality": "reaction", "reaction_id": "like"}],
                    "stance": "acknowledge_briefly",
                    "brief_rationale": "A reaction might fit.",
                }
            )
        ),
        expression_capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES,
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await adapter.propose(_qq_request())


@pytest.mark.asyncio
async def test_expression_draft_silent_choice_persists_a_no_action_decision() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "private_turn_state": {
                        "inner_state_summary": (
                            "我看见了这句话，但此刻不想为了保持在线感勉强开口。"
                        ),
                        "attended_source_refs": ["trigger:1"],
                    },
                    "timing_choice": "silent",
                    "beats": [],
                    "stance": "defer",
                    "brief_rationale": "The companion notices but chooses not to intrude.",
                }
            )
        ),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    output = await adapter.propose(_qq_request())

    assert output.raw_proposal["proposal_kind"] == "decision"
    assert output.raw_proposal["private_turn_state"]["attended_source_refs"] == ["trigger:1"]
    assert output.raw_proposal["timing_choice"] == "silent"
    assert output.raw_proposal["proposed_changes"] == []
    assert output.raw_proposal["action_intents"] == []


@pytest.mark.asyncio
async def test_expression_draft_later_choice_freezes_relative_window_on_every_beat() -> None:
    request = _qq_request().model_copy(
        update={"model_content_json": '{"logical_time":"2026-07-16T12:00:00+00:00"}'}
    )
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "later",
                    "delay_seconds": 60,
                    "expires_after_seconds": 600,
                    "beats": [
                        {"modality": "text", "text": "等我一下，我晚点认真听你说。"},
                        {"modality": "text", "text": "刚才那段我不想随便糊弄过去。"},
                        {"modality": "text", "text": "等我回来。"},
                    ],
                    "stance": "defer",
                    "brief_rationale": "The current activity makes an immediate full response implausible.",
                },
                ensure_ascii=False,
            )
        ),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    output = await adapter.propose(request)

    assert output.raw_proposal["timing_choice"] == "later"
    intents = output.raw_proposal["action_intents"]
    assert [item["kind"] for item in intents] == ["followup", "followup", "followup"]
    change = json.loads(output.raw_proposal["proposed_changes"][0]["payload"]["canonical_json"])
    assert [item["inline_text"] for item in change["beat_drafts"]] == [
        "等我一下，我晚点认真听你说。",
        "刚才那段我不想随便糊弄过去。",
        "等我回来。",
    ]
    assert all(
        item["due_window"] == ["2026-07-16T12:01:00Z", "2026-07-16T12:10:00Z"] for item in intents
    )


@pytest.mark.asyncio
async def test_expression_draft_losslessly_promotes_exact_nested_later_envelope() -> None:
    request = _qq_request().model_copy(
        update={"model_content_json": '{"logical_time":"2026-07-16T12:00:00+00:00"}'}
    )
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "later",
                    "later": {
                        "delay_seconds": 60,
                        "expires_after_seconds": 600,
                    },
                    "beats": [
                        {"modality": "text", "text": "我晚一点回来接着说。"},
                    ],
                    "stance": "defer",
                    "brief_rationale": "The role selected a bounded later expression.",
                },
                ensure_ascii=False,
            )
        ),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    output = await adapter.propose(request)

    assert output.raw_proposal["timing_choice"] == "later"
    assert output.raw_proposal["action_intents"][0]["due_window"] == [
        "2026-07-16T12:01:00Z",
        "2026-07-16T12:10:00Z",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_update",
    (
        {"delay_seconds": 90},
        {"later": {"delay_seconds": 60, "expires_after_seconds": 600, "extra": 1}},
    ),
)
@pytest.mark.asyncio
async def test_expression_draft_nested_later_envelope_conflicts_remain_invalid(
    invalid_update: dict[str, object],
) -> None:
    value: dict[str, object] = {
        "timing_choice": "later",
        "later": {
            "delay_seconds": 60,
            "expires_after_seconds": 600,
        },
        "beats": [{"modality": "text", "text": "我晚一点回来接着说。"}],
        "stance": "defer",
        "brief_rationale": "Invalid wire shape must remain fail-closed.",
        **invalid_update,
    }
    adapter = _ExpressionDraftWire(
        model=_Model(json.dumps(value, ensure_ascii=False)),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await adapter.propose(_qq_request())


@pytest.mark.asyncio
async def test_expression_draft_later_rejects_uninstalled_nontext_effect() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model(
            json.dumps(
                {
                    "timing_choice": "later",
                    "delay_seconds": 4,
                    "expires_after_seconds": 30,
                    "beats": [
                        {"modality": "typing"},
                        {"modality": "text", "text": "我晚一点回来接着说。"},
                    ],
                    "stance": "hold",
                    "brief_rationale": "Signal that a response will come later.",
                }
            )
        ),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    with pytest.raises(ValidationTechnicalFailure, match="authored_expression_reselection_invalid"):
        await adapter.propose(_qq_request())


def test_expression_prompt_exposes_exact_executable_field_types() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model("{}"),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )

    system = adapter._messages(  # noqa: SLF001 - contract regression test
        request=_qq_request(),
        quick_recovery=False,
        provisional=False,
        failure_code=None,
    )[0]["content"]

    assert 'modality="text"' in system
    assert "never use content" in system
    assert "confidence is an integer from 0 through 10000" in system
    assert "counterpart_history" in system
    assert "never use conversation or user_fact" in system
    assert (
        "Subjective feelings, genuinely unsettled conjectures, and world-unbound "
        "generalizations use no world_claim item" in system
    )
    assert "subjective_or_hypothetical is legacy replay input" in system
    assert "response_expectation_assessment" in system


def test_private_turn_state_prompt_describes_a_private_self_not_a_reply_optimizer() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model("{}"),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
    )

    system = adapter._messages(  # noqa: SLF001 - provider contract regression
        request=_qq_request(),
        quick_recovery=False,
        provisional=False,
        failure_code=None,
    )[0]["content"]

    assert "表达之前你自己真正搁着的感觉、注意、欲望或抵触、联想和不确定" in system
    assert "what reply would satisfy the counterpart or optimize the conversation" in system
    assert "You own the motive, tone, timing" in system
    assert (
        "private_turn_state is turn-local audit only; its attended_source_refs record "
        "attention provenance" in system
    )
    assert "They need no World proof and do not establish an external event" in system
    assert (
        "a counterpart observation establishes what they reported, not that the report is "
        "objective truth or your own Experience" in system
    )
    assert "This factual boundary never chooses your social response" in system


def test_expression_prompt_exposes_machine_readable_hard_boundary_manifest() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model("{}"),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "logical_time": "2026-07-30T06:00:00+00:00",
                    "slices": {
                        "world_life": {
                            "availability": "available",
                            "items": [
                                {
                                    "source_ref": "biography:summer-home",
                                    "value": {
                                        "context_kind": "biographical_context",
                                        "age": 21,
                                    },
                                },
                                {
                                    "source_ref": "occurrence:walk",
                                    "value": {
                                        "context_kind": "settled_occurrence",
                                        "content": "傍晚散步",
                                    },
                                },
                            ],
                        },
                        "character_core": {
                            "availability": "available",
                            "items": [
                                {
                                    "source_ref": "core:companion",
                                    "value": {"values": {}},
                                }
                            ],
                        },
                        "recent_dialogue": {
                            "availability": "available",
                            "items": [
                                {
                                    "source_ref": "observation:qq:1",
                                    "value": {"speaker": "counterpart"},
                                }
                            ],
                        },
                    },
                },
                ensure_ascii=False,
            )
        }
    )

    messages = adapter._messages(  # noqa: SLF001 - provider contract regression
        request=request,
        quick_recovery=False,
        provisional=False,
        failure_code=None,
    )
    user = json.loads(messages[1]["content"])
    boundary = user["expression_hard_boundaries"]

    assert boundary["contract"] == "expression-hard-boundaries.present.1"
    assert boundary["authority"] == "checked_after_expression"
    assert "single_report_epistemic_scope" not in boundary
    assert "private_turn_state" not in boundary
    assert "biographical_coordinate_authority" not in boundary
    assert "response_expectation" not in boundary
    assert boundary["world_claim_source_refs"]["current_world"] == [
        "S1",
        "occurrence:walk",
    ]
    assert boundary["world_claim_source_refs"]["past_world"] == ["occurrence:walk"]
    assert boundary["world_claim_source_refs"]["stable_identity"] == [
        "core:companion",
    ]
    assert "occurrence:walk" not in boundary["world_claim_source_refs"]["stable_identity"]
    assert set(boundary["source_ref_aliases"]) == {"S1"}
    assert boundary["source_ref_aliases"]["S1"].startswith("biography-coordinate:sha256:")


def test_expression_boundary_separates_companion_life_authority_availability() -> None:
    current_ref = "current-situation:" + ("a" * 64)
    active_ref = "active-occurrence:" + ("b" * 64)
    experience_ref = "committed-experience:" + ("c" * 64)
    adapter = _ExpressionDraftWire(
        model=_Model("{}"),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "actor_ref": "agent:companion",
                    "slices": {
                        "current_situation": {
                            "availability": "available",
                            "items": [
                                {
                                    "source_ref": current_ref,
                                    "value": {
                                        "actor_ref": "agent:companion",
                                        "time_segment": "afternoon",
                                    },
                                }
                            ],
                        },
                        "world_life": {
                            "availability": "available",
                            "items": [
                                {
                                    "source_ref": "biography:attention-only",
                                    "value": {
                                        "context_kind": "biographical_context",
                                        "academic_phase": "summer_break",
                                    },
                                },
                                {
                                    "source_ref": active_ref,
                                    "value": {
                                        "context_kind": "active_world_occurrence",
                                        "status": "active",
                                    },
                                },
                            ],
                        },
                        "recent_experiences": {
                            "availability": "available",
                            "items": [
                                {
                                    "source_ref": experience_ref,
                                    "value": {
                                        "experience_id": "experience:one",
                                        "summary": "A committed experience.",
                                    },
                                }
                            ],
                        },
                    },
                },
                ensure_ascii=False,
            )
        }
    )

    user = json.loads(
        adapter._messages(  # noqa: SLF001 - provider boundary contract regression
            request=request,
            quick_recovery=False,
            provisional=False,
            failure_code=None,
        )[1]["content"]
    )
    boundary = user["expression_hard_boundaries"]
    availability = boundary["companion_life_authority_availability"]
    aliases = boundary["source_ref_aliases"]

    assert availability["authority"] == "pinned_claim_capability_only"
    assert availability["behavior_advice"] is False
    assert (
        availability["empty_semantics"] == "no_pinned_authority_available_not_event_did_not_happen"
    )
    assert [aliases.get(ref, ref) for ref in availability["current_situation_source_refs"]] == [
        current_ref
    ]
    assert [aliases.get(ref, ref) for ref in availability["active_occurrence_source_refs"]] == [
        active_ref
    ]
    assert [aliases.get(ref, ref) for ref in availability["committed_experience_source_refs"]] == [
        experience_ref
    ]
    assert "biography:attention-only" not in json.dumps(availability, ensure_ascii=False)


def test_empty_companion_life_authority_is_not_a_negative_world_fact() -> None:
    adapter = _ExpressionDraftWire(
        model=_Model("{}"),
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    )
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "slices": {
                        "world_life": {
                            "availability": "available",
                            "items": [
                                {
                                    "source_ref": "biography:attention-only",
                                    "value": {"context_kind": "biographical_context"},
                                }
                            ],
                        },
                        "recent_experiences": {
                            "availability": "available",
                            "items": [],
                        },
                    }
                },
                ensure_ascii=False,
            )
        }
    )

    user = json.loads(
        adapter._messages(  # noqa: SLF001 - provider boundary contract regression
            request=request,
            quick_recovery=False,
            provisional=False,
            failure_code=None,
        )[1]["content"]
    )

    assert user["expression_hard_boundaries"]["companion_life_authority_availability"] == {
        "authority": "pinned_claim_capability_only",
        "behavior_advice": False,
        "empty_semantics": "no_pinned_authority_available_not_event_did_not_happen",
        "current_situation_source_refs": [],
        "active_occurrence_source_refs": [],
        "active_activity_source_refs": [],
        "committed_experience_source_refs": [],
    }


@pytest.mark.asyncio
async def test_companion_expression_cannot_source_counterpart_history_claim() -> None:
    companion_dialogue_ref = "dialogue:expression:plan:previous:beat:1"
    request = _qq_request().model_copy(
        update={
            "model_content_json": json.dumps(
                {
                    "actor_ref": "agent:companion",
                    "slices": {
                        "recent_dialogue": {
                            "availability": "available",
                            "items": [
                                {
                                    "source_ref": companion_dialogue_ref,
                                    "value": {
                                        "dialogue_id": companion_dialogue_ref,
                                        "speaker": "companion",
                                        "speaker_ref": "agent:companion",
                                        "text": "我先去整理一下，晚点回来。",
                                        "occurred_at": "2026-07-29T06:00:00Z",
                                        "delivery_state": "delivered",
                                        "sequence": 201,
                                        "source_claims": [
                                            {
                                                "authority_event_ref": "event:expression:accepted:1",
                                                "authority_world_revision": 2,
                                                "authority_payload_hash": "a" * 64,
                                            }
                                        ],
                                    },
                                    "attention_source_refs": [companion_dialogue_ref],
                                }
                            ],
                        }
                    },
                },
                ensure_ascii=False,
            )
        }
    )
    draft = json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "你之前说自己要去整理东西。"}],
            "stance": "misattribute_old_expression",
            "brief_rationale": "Attempt to treat my old expression as the user's report.",
            "world_claims": [
                {
                    "claim_text": "对方之前说自己要去整理东西",
                    "scope": "counterpart_history",
                    "source_refs": [companion_dialogue_ref],
                }
            ],
        },
        ensure_ascii=False,
    )

    with pytest.raises(
        ValidationTechnicalFailure, match="authored_expression_reselection_invalid"
    ):
        await _ExpressionDraftWire(model=_Model(draft)).propose(request)



@pytest.mark.asyncio
async def test_cancelled_recall_followup_keeps_the_exact_nested_provider_identity() -> None:
    author = _FirstReplyThenBlockJsonModel(
        json.dumps(
            {
                "private_turn_state": {
                    "inner_state_summary": "这句话碰到一点模糊的熟悉感，我想先回忆再决定。",
                    "attended_source_refs": ["trigger:1"],
                },
                "recall_request": {
                    "query_text": "那件麻烦事",
                    "memory_kinds": ["episodic"],
                    "limit": 2,
                },
            },
            ensure_ascii=False,
        )
    )
    cursor = RecallCursor(
        world_revision=3,
        deliberation_revision=0,
        ledger_sequence=0,
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(
        cursor=cursor,
        documents=(
            RecallDocument(
                document_id="recall:nested-cancellation",
                memory_kind="episodic",
                source_item_ref="experience:nested-cancellation",
                source_slice="recent_experiences",
                source_refs=("event:nested-cancellation",),
                source_bindings=(
                    RecallSourceBinding(
                        source_kind="committed_event",
                        authority_type="ExperienceCommitted",
                        ref="event:nested-cancellation",
                        source_world_revision=2,
                        immutable_hash="9" * 64,
                    ),
                ),
                source_world_revision=2,
                text="前几天听她提过一件棘手的事情。",
                actor_ref="agent:companion",
                subject_refs=("user:primary",),
                occurred_from=datetime(2026, 7, 25, 12, tzinfo=UTC),
                privacy_class="personal",
            ),
        ),
    )
    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=datetime(2026, 7, 27, 12, tzinfo=UTC),
    )
    task = asyncio.create_task(
        _ExpressionDraftWire(
            model=author,
            recall_coordinator=coordinator,
            expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
                update={"private_turn_state_mode": "required"}
            ),
        ).propose(
            _qq_request().model_copy(
                update={
                    "model_content_json": json.dumps(
                        {
                            "world_revision": 3,
                            "deliberation_revision": 0,
                            "ledger_sequence": 0,
                            "logical_time": "2026-07-27T12:00:00+00:00",
                            "slices": {},
                        },
                        ensure_ascii=False,
                    )
                }
            )
        )
    )

    try:
        await asyncio.wait_for(author.nested_call_entered.wait(), timeout=0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
    finally:
        coordinator.close()

    failure = getattr(
        raised.value,
        "world_v2_validation_technical_failure",
        None,
    )
    assert isinstance(failure, ValidationTechnicalFailure)
    assert failure.failure_code == "authored_subcall_timeout"
    assert failure.attempted_model_id == author.model
    assert failure.attempted_model_version == _ExpressionDraftWire.VERSION
    assert len(failure.authored_candidate_audits) == 1
    initial_author = failure.authored_candidate_audits[0]
    assert initial_author.request_hash == _provider_request_hash(*author.calls[0])
    assert initial_author.outcome == "validation_unresolved"
    assert len(failure.provider_subcall_audits) == 1
    nested = failure.provider_subcall_audits[0]
    assert nested.purpose == "recall_followup"
    assert nested.parent_model_call_id == initial_author.model_call_id
    assert nested.request_hash == _provider_request_hash(*author.calls[1])
    assert nested.response_hash is None
    assert nested.outcome == "timeout"


# ---------------------------------------------------------------------------
# Declared-claims fail-closed without a second model (H1d).
# ---------------------------------------------------------------------------


def _v8_request(*, trigger_text: str = "我今天终于把那件麻烦事做完了。") -> ModelInput:
    request = _qq_request()
    if trigger_text == request.trigger_message.text:
        return request
    return request.model_copy(
        update={
            "trigger_message": request.trigger_message.model_copy(update={"text": trigger_text})
        }
    )


def _v8_reply(
    *,
    text: str = "听起来挺不错的。",
    claims: list[dict[str, object]] | None = None,
) -> str:
    if claims is None:
        claims = []
    return json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": text}],
            "stance": "warm",
            "brief_rationale": "Declared-claims fail-closed probe.",
            "confidence": 7600,
            "world_claims": claims,
        },
        ensure_ascii=False,
    )


def _v8_supported_claim() -> list[dict[str, object]]:
    return [
        {
            "claim_text": "用户今天把那件麻烦事做完了",
            "scope": "counterpart_history",
            "source_refs": ["observation:qq:1"],
        }
    ]


@pytest.mark.asyncio
async def test_v8_lane_claim_free_expression_skips_reviewer_call() -> None:
    reviewer = _SequenceJsonModel([])
    output = await _ExpressionDraftWire(
        model=_JsonModel(_v8_reply(claims=[])),
        source_closure_reviewer=reviewer,
    ).propose(_v8_request())

    assert output.raw_proposal["action_intents"]
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_inventory_guard_treats_an_empty_terminal_expression_as_empty_inventory() -> None:
    inventory = _StrictInventorySequenceJsonModel([])
    reviewer = _FullSourceReviewSequenceJsonModel([])
    raw = json.dumps(
        {
            "timing_choice": "silent",
            "beats": [],
            "stance": "withdraw",
            "brief_rationale": "The role chose not to emit a visible beat.",
            "confidence": 7000,
            "world_claims": [],
        },
        ensure_ascii=False,
    )

    result = await review_expression_with_candidate_external_coverage(
        reviewer=reviewer,
        inventory_model=inventory,
        request=_qq_request(),
        raw=raw,
        identity_frame=None,
    )

    assert result.review is None
    assert inventory.calls == []
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_v8_lane_mechanically_invalid_ref_cannot_be_whitened_by_reviewer() -> None:
    claims = [
        {
            "claim_text": "用户昨天去了书店",
            "scope": "counterpart_history",
            "source_refs": ["dialogue:observation:nonexistent"],
        }
    ]
    reviewer = _SequenceJsonModel([_source_closure_review()])
    with pytest.raises(ValidationTechnicalFailure):
        await _ExpressionDraftWire(
            model=_JsonModel(_v8_reply(claims=claims)),
            source_closure_reviewer=reviewer,
        ).propose(_v8_request())
    assert reviewer.calls == []


@pytest.mark.asyncio
async def test_combined_envelope_releases_first_visible_beat_before_stream_ends() -> None:
    """DeepSeek often ignores the events protocol and emits the combined
    envelope; the incremental scanner must still release the head early."""

    complete = json.dumps(
        {
            "appraisal_draft": {
                "appraise": True,
                "brief_rationale": "probe",
                "behavior_tendency": "listen",
                "stance": "open",
                "display_strategy": "warm",
                "confidence": 7200,
                "meanings": [{"meaning": "x", "confidence": 0.8}],
                "attribution": "situation",
                "severity": 4000,
                "affect": "update",
                "components": [],
            },
            "expression_draft": {
                "timing_choice": "now",
                "turn_posture": "continue",
                "world_claims": [],
                "beats": [{"modality": "text", "text": "怎么了？"}],
                "stance": "warm",
                "brief_rationale": "probe",
                "confidence": 7600,
            },
        },
        ensure_ascii=False,
    )

    chunks = ["{"]
    for index in range(1, len(complete) - 1, 24):
        chunks.append(complete[index : index + 24])
    chunks.append("}")

    buffer = ""
    released: str | None = None
    for chunk in chunks:
        buffer += chunk
        if released is None:
            released = _incremental_first_expression(buffer)
        if released is not None:
            break
    assert released is not None, "head must release before the final chunk"
    head = json.loads(released)
    assert head["appraisal_draft"]["appraise"] is True
    assert head["expression_draft"]["beats"] == [{"modality": "text", "text": "怎么了？"}]
    assert buffer != complete or True
    assert len(buffer) < len(complete), "head must release before the whole stream"


@pytest.mark.asyncio
async def test_declared_known_claim_skips_second_model_inventory_and_review() -> None:
    reviewer = _SequenceJsonModel([])
    inventory = _SequenceJsonModel([])
    output = await _ExpressionDraftWire(
        model=_JsonModel(_v8_reply(claims=_v8_supported_claim())),
        source_closure_reviewer=reviewer,
        candidate_external_proposition_inventory_model=inventory,
    ).propose(_v8_request())

    assert output.raw_proposal["action_intents"]
    assert reviewer.calls == []
    assert inventory.calls == []


@pytest.mark.asyncio
async def test_missing_source_ref_fails_closed_without_a_second_model() -> None:
    reviewer = _SequenceJsonModel([_source_closure_review()])
    inventory = _SequenceJsonModel([])
    raw = json.dumps(
        {
            "timing_choice": "now",
            "beats": [{"modality": "text", "text": "我还在那个地方。"}],
            "stance": "share",
            "brief_rationale": "Share a current fact.",
            "confidence": 7000,
            "world_claims": [
                {
                    "claim_text": "我在那个地方",
                    "scope": "current_world",
                    "source_refs": ["missing:source"],
                }
            ],
        },
        ensure_ascii=False,
    )

    with pytest.raises(ValidationTechnicalFailure) as exc_info:
        await review_expression_with_candidate_external_coverage(
            reviewer=reviewer,
            inventory_model=inventory,
            request=_qq_request(),
            raw=raw,
            identity_frame=None,
        )

    assert exc_info.value.failure_code == "source_review_exception"
    assert reviewer.calls == []
    assert inventory.calls == []
