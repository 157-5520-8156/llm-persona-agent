"""Conversation occasion and sourced lived facts for media selection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from companion_daemon.world_v2.event_ecology_media import occurrence_world_fact_slice
from companion_daemon.world_v2.image_evidence_contract import (
    ImageEvidenceDeclaredPayload,
    ImageEvidenceV1,
)
from companion_daemon.world_v2.media_selection_occasion import (
    compile_candidate_occasion,
    compile_lived_facts,
    privacy_allows,
    safe_summary_from_lived_facts,
)
from companion_daemon.world_v2.media_v2 import MediaEvidenceSource, PhotoCandidate


NOW = datetime(2026, 8, 22, 16, 1, 30, tzinfo=UTC)
SECRET = "这是不该出现的私人印象原文"
SHAREABLE = "She finds a rare poetry collection that she buys for herself."


def _candidate(*, opened_at: datetime = NOW) -> PhotoCandidate:
    return PhotoCandidate(
        candidate_id="candidate:book",
        source_event_refs=("event:declaration", "event:settlement"),
        family="life_share",
        privacy_ceiling="shareable",
        opened_at=opened_at,
        expires_at=opened_at + timedelta(hours=48),
        ecology_category="settled_outcome",
        ecology_observed_at=opened_at,
        source_events=(
            MediaEvidenceSource(event_ref="event:declaration", payload_hash="b" * 64),
            MediaEvidenceSource(event_ref="event:settlement", payload_hash="a" * 64),
        ),
    )


def test_privacy_allows_does_not_surface_withhold() -> None:
    assert privacy_allows("shareable", ceiling="shareable") is True
    assert privacy_allows("private", ceiling="shareable") is False
    assert privacy_allows("withhold", ceiling="private") is False


def test_cold_background_has_no_occasion() -> None:
    candidate = _candidate()
    occasion = compile_candidate_occasion(
        projection=SimpleNamespace(
            logical_time=NOW,
            message_observations=(
                SimpleNamespace(actor="user:geoff", source_event_id="event:old"),
            ),
            committed_world_event_refs=(
                SimpleNamespace(
                    event_id="event:old",
                    logical_time=NOW - timedelta(days=5),
                ),
            ),
            trigger_processes=(),
            expression_plan_manifests=(),
            threads=(),
            conversation_threads=(),
        ),
        candidate=candidate,
        logical_time=NOW,
        character_actor_ref="agent:companion",
    )
    assert occasion is None


def test_live_conversation_and_fresh_candidate_is_an_occasion() -> None:
    candidate = _candidate()
    occasion = compile_candidate_occasion(
        projection=SimpleNamespace(
            logical_time=NOW,
            message_observations=(
                SimpleNamespace(actor="user:geoff", source_event_id="event:inbound"),
            ),
            committed_world_event_refs=(
                SimpleNamespace(event_id="event:inbound", logical_time=NOW),
            ),
            trigger_processes=(),
            expression_plan_manifests=(),
            threads=(),
            conversation_threads=(),
        ),
        candidate=candidate,
        logical_time=NOW,
        character_actor_ref="agent:companion",
    )
    assert occasion is not None
    assert occasion.kind == "live_conversation_fresh_candidate"


def test_qq_coalesced_observation_id_still_counts_as_live_conversation() -> None:
    """Production QQ ``source_event_id`` is nested inside the WorldEvent id."""

    candidate = _candidate()
    source_event_id = (
        "qq:2759284998:qq-coalesced:ae28c68882abe5cb0c3652f549e92ca8104040c9265872cd1e868c9278874f2d"
    )
    event_id = "event:trigger:observation:platform:qq:" + source_event_id
    occasion = compile_candidate_occasion(
        projection=SimpleNamespace(
            logical_time=NOW - timedelta(days=4),
            message_observations=(
                SimpleNamespace(
                    actor="user:geoff",
                    source_event_id=source_event_id,
                    observation_id="observation:" + source_event_id,
                ),
            ),
            committed_world_event_refs=(
                SimpleNamespace(
                    event_id=event_id,
                    event_type="ObservationRecorded",
                    logical_time=NOW + timedelta(seconds=2),
                ),
            ),
            trigger_processes=(),
            expression_plan_manifests=(),
            threads=(),
            conversation_threads=(),
        ),
        candidate=candidate,
        logical_time=NOW - timedelta(days=4),
        character_actor_ref="agent:companion",
    )
    assert occasion is not None
    assert occasion.kind == "live_conversation_fresh_candidate"


def test_occasion_does_not_inspect_counterpart_wording() -> None:
    candidate = _candidate()
    silent = compile_candidate_occasion(
        projection=SimpleNamespace(
            logical_time=NOW,
            message_observations=(
                SimpleNamespace(
                    actor="user:geoff",
                    source_event_id="event:inbound",
                    text="把你翻到的那本诗集拍一张给我看看",
                ),
            ),
            committed_world_event_refs=(
                SimpleNamespace(event_id="event:inbound", logical_time=NOW),
            ),
            trigger_processes=(),
            expression_plan_manifests=(),
            threads=(),
            conversation_threads=(),
        ),
        candidate=candidate,
        logical_time=NOW,
        character_actor_ref="agent:companion",
    )
    quiet = compile_candidate_occasion(
        projection=SimpleNamespace(
            logical_time=NOW,
            message_observations=(
                SimpleNamespace(
                    actor="user:geoff",
                    source_event_id="event:inbound",
                    text="在吗",
                ),
            ),
            committed_world_event_refs=(
                SimpleNamespace(event_id="event:inbound", logical_time=NOW),
            ),
            trigger_processes=(),
            expression_plan_manifests=(),
            threads=(),
            conversation_threads=(),
        ),
        candidate=candidate,
        logical_time=NOW,
        character_actor_ref="agent:companion",
    )
    assert silent is not None and quiet is not None
    assert silent.kind == quiet.kind == "live_conversation_fresh_candidate"


def test_live_conversation_offers_an_unexpired_candidate_opened_hours_ago() -> None:
    """He just spoke. The bookstore candidate opened last night. Offer it."""

    opened = NOW - timedelta(hours=8)
    candidate = _candidate(opened_at=opened)
    occasion = compile_candidate_occasion(
        projection=SimpleNamespace(
            logical_time=NOW,
            message_observations=(
                SimpleNamespace(actor="user:geoff", source_event_id="event:inbound"),
            ),
            committed_world_event_refs=(
                SimpleNamespace(event_id="event:inbound", logical_time=NOW),
            ),
            trigger_processes=(),
            expression_plan_manifests=(),
            threads=(),
            conversation_threads=(),
        ),
        candidate=candidate,
        logical_time=NOW,
        character_actor_ref="agent:companion",
    )
    assert occasion is not None
    assert occasion.kind == "live_conversation_fresh_candidate"


def test_stale_speech_does_not_offer_an_old_candidate() -> None:
    """Last speech was three hours ago; this is not a live conversation."""

    candidate = _candidate(opened_at=NOW - timedelta(hours=8))
    occasion = compile_candidate_occasion(
        projection=SimpleNamespace(
            logical_time=NOW,
            message_observations=(
                SimpleNamespace(actor="user:geoff", source_event_id="event:old"),
            ),
            committed_world_event_refs=(
                SimpleNamespace(
                    event_id="event:old",
                    logical_time=NOW - timedelta(hours=3),
                ),
            ),
            trigger_processes=(),
            expression_plan_manifests=(),
            threads=(),
            conversation_threads=(),
        ),
        candidate=candidate,
        logical_time=NOW,
        character_actor_ref="agent:companion",
    )
    assert occasion is None


def test_lived_facts_keep_source_refs_and_drop_withhold() -> None:
    candidate = _candidate()
    declaration = ImageEvidenceDeclaredPayload(
        source_event_ref="event:settlement",
        source_event_payload_hash="a" * 64,
        source_event_type="WorldOccurrenceSettled",
        source_privacy_ceiling="shareable",
        image_evidence=ImageEvidenceV1(
            visibility="shareable",
            summary=SHAREABLE,
            activity={"kind": "browse", "description": "Browsing through secondhand books."},
            location={"kind": "market", "publicness": "public"},
        ),
        declared_at=NOW,
    )
    ledger = SimpleNamespace(
        lookup_event_commit=lambda event_id: (
            (
                SimpleNamespace(
                    event_type="ImageEvidenceDeclared",
                    payload_hash="b" * 64,
                    payload_json=declaration.model_dump_json(),
                ),
                SimpleNamespace(),
            )
            if event_id == "event:declaration"
            else None
        )
    )
    reader = SimpleNamespace(
        read_for_occurrence=lambda **_kwargs: SimpleNamespace(
            outcomes=(SimpleNamespace(text=SECRET),)
        )
    )
    facts = compile_lived_facts(
        projection=SimpleNamespace(
            logical_time=NOW,
            world_occurrences=(
                SimpleNamespace(
                    status="settled",
                    settlement_event_ref="event:settlement",
                    trigger_ref="plan:book",
                    visibility="withhold",
                    settled_at=NOW,
                    location_ref="location:shanghai-old-book-market",
                    participant_refs=("agent:companion",),
                    settled_outcome_ref="outcome:keep",
                ),
            ),
            threads=(),
            conversation_threads=(),
            expression_plan_manifests=(),
            trigger_processes=(),
            committed_world_event_refs=(),
            private_impressions=(SimpleNamespace(reflection_summary=SECRET),),
        ),
        candidate=candidate,
        ledger=ledger,
        material_reader=reader,
        character_actor_ref="agent:companion",
    )
    dumped = repr(facts)
    assert SECRET not in dumped
    assert any(
        item["kind"] == "image_evidence"
        and item["source_ref"] == "event:declaration"
        and item["summary"] == SHAREABLE
        for item in facts
    )
    assert all(item.get("kind") != "settled_occurrence" for item in facts)
    summary = safe_summary_from_lived_facts(facts)
    assert SECRET not in summary
    assert SHAREABLE[:20] in summary
    assert "值得分享" not in summary
    assert all(
        "." not in str((item.get("activity") or {}).get("kind") or "")
        for item in facts
    )


def test_activity_catalog_token_is_dropped_from_lived_facts() -> None:
    candidate = _candidate()
    declaration = ImageEvidenceDeclaredPayload(
        source_event_ref="event:settlement",
        source_event_payload_hash="a" * 64,
        source_event_type="WorldOccurrenceSettled",
        source_privacy_ceiling="shareable",
        image_evidence=ImageEvidenceV1(
            visibility="shareable",
            summary=SHAREABLE,
            activity={
                "kind": "open_life.37a58696c6f4138443841433",
                "description": "Browsing through secondhand books.",
                "id": "plan:must-not-leak",
            },
            location={"kind": "market", "publicness": "public"},
        ),
        declared_at=NOW,
    )
    ledger = SimpleNamespace(
        lookup_event_commit=lambda event_id: (
            (
                SimpleNamespace(
                    event_type="ImageEvidenceDeclared",
                    payload_hash="b" * 64,
                    payload_json=declaration.model_dump_json(),
                ),
                SimpleNamespace(),
            )
            if event_id == "event:declaration"
            else None
        )
    )
    facts = compile_lived_facts(
        projection=SimpleNamespace(
            logical_time=NOW,
            world_occurrences=(),
            threads=(),
            conversation_threads=(),
            expression_plan_manifests=(),
            trigger_processes=(),
            committed_world_event_refs=(),
        ),
        candidate=candidate,
        ledger=ledger,
        character_actor_ref="agent:companion",
    )
    dumped = repr(facts)
    assert "open_life.37a58696c6f4138443841433" not in dumped
    assert "plan:must-not-leak" not in dumped
    evidence = next(item for item in facts if item["kind"] == "image_evidence")
    assert evidence["activity"] == {"description": "Browsing through secondhand books."}


def test_occurrence_world_fact_slice_is_coordinates_not_a_share_reason() -> None:
    slice_ = occurrence_world_fact_slice(
        SimpleNamespace(
            settled_at=NOW,
            location_ref="location:shanghai-old-book-market",
            participant_refs=("agent:companion",),
            settled_outcome_ref="outcome:keep",
        )
    )
    assert slice_["location_ref"] == "location:shanghai-old-book-market"
    assert "reason" not in slice_
    assert "worth_sharing" not in slice_
