"""Live-head dialogue on she-initiates turns; no host semantic suppression."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace

from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.conversation_continuity import (
    ConversationContinuityCompiler,
    pack_recent_dialogue_under_source_budget,
)
from companion_daemon.world_v2.ledger_context_resolver import _bounded_domain_items
from companion_daemon.world_v2.recent_dialogue import (
    DialogueSourceClaim,
    RecentDialogueItem,
    _mark_live_conversation_head,
)
from companion_daemon.world_v2.response_expectation_view import (
    attach_pending_expectation_advisory,
    counterpart_last_spoke_facts,
    expired_hope_advisory_value,
    expired_unanswered_expectation,
)
from test_expectation_feelings import HOPED, NOW as EXPECTATION_NOW


NOW = datetime(2026, 8, 18, 13, 37, 1, tzinfo=UTC)
SUN = "是个太阳的表情来着，表示我今天心情不错"
BUSY = "没啥，刚刚忙完，想着来骚扰你一下"
STRANGE = "你想要我发什么奇怪的东西嘛"
OLD_HIM = "对的，你那边看不到嘛"
RECEIPT = "event:receipt:invite:verified"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _item(
    *,
    dialogue_id: str,
    speaker: str,
    text: str,
    sequence: int,
    claim_count: int = 1,
    at: datetime | None = None,
) -> RecentDialogueItem:
    occurred = at or (NOW - timedelta(seconds=max(0, 200 - sequence)))
    claims = tuple(
        DialogueSourceClaim(
            authority_event_ref=f"event:{dialogue_id}:{index}",
            authority_world_revision=max(1, sequence // 100),
            authority_payload_hash=_hash(f"{dialogue_id}:{index}"),
        )
        for index in range(claim_count)
    )
    return RecentDialogueItem(
        dialogue_id=dialogue_id,
        speaker=speaker,
        text=text,
        occurred_at=occurred,
        delivery_state="observed" if speaker == "counterpart" else "delivered",
        sequence=sequence,
        source_claims=claims,
    )


def _production_like_dialogue() -> tuple[list[RecentDialogueItem], list[RecentDialogueItem]]:
    him = [
        _item(
            dialogue_id="dialogue:observation:old-unseen",
            speaker="counterpart",
            text=OLD_HIM,
            sequence=200_500,
        ),
        _item(
            dialogue_id="dialogue:observation:busy",
            speaker="counterpart",
            text=BUSY,
            sequence=203_000,
        ),
        _item(
            dialogue_id="dialogue:observation:sun",
            speaker="counterpart",
            text=SUN,
            sequence=205_500,
        ),
        _item(
            dialogue_id="dialogue:observation:strange",
            speaker="counterpart",
            text=STRANGE,
            sequence=207_100,
        ),
    ]
    her = [
        _item(
            dialogue_id=f"dialogue:expression:beat-{index:02d}",
            speaker="companion",
            text=f"她自己的第 {index} 条 beat，带好几条 claim。",
            sequence=190_000 + index * 1_000,
            claim_count=2,
        )
        for index in range(12)
    ]
    return him, her


def _select_under_ref_budget(
    items: tuple[RecentDialogueItem, ...], *, max_refs: int = 32
) -> list[RecentDialogueItem]:
    selected: list[RecentDialogueItem] = []
    refs: set[str] = set()
    for item in items:
        candidate = {claim.authority_event_ref for claim in item.source_claims}
        if len(refs | candidate) > max_refs:
            continue
        selected.append(item)
        refs |= candidate
    return selected


def _ranked(items: list[RecentDialogueItem]) -> tuple[RecentDialogueItem, ...]:
    ranked = _bounded_domain_items("recent_dialogue", tuple(items), NOW)
    assert ranked is not None
    return ranked


def test_without_live_head_eight_item_budget_drops_the_sun_line() -> None:
    him, her = _production_like_dialogue()
    ranked = _ranked([*him, *her])
    kept: list[RecentDialogueItem] = []
    used = 0
    for item in ranked:
        n = len(item.model_dump(mode="json"))
        if used + n > 96:
            continue
        kept.append(item)
        used += n
        if len(kept) >= 8:
            break
    texts = [item.text for item in kept]

    assert SUN not in texts
    assert BUSY not in texts
    assert STRANGE not in texts


def test_without_live_head_the_sun_line_loses_the_ref_budget() -> None:
    him, her = _production_like_dialogue()
    # Inflate claim cost so a pure rank fill still starves his live head —
    # the packed live-window path is what keeps both speakers under 32 refs.
    heavy_her = [
        item.model_copy(
            update={
                "source_claims": tuple(
                    DialogueSourceClaim(
                        authority_event_ref=f"event:{item.dialogue_id}:heavy:{index}",
                        authority_world_revision=max(1, item.sequence // 100),
                        authority_payload_hash=_hash(f"{item.dialogue_id}:heavy:{index}"),
                    )
                    for index in range(4)
                )
            }
        )
        for item in her
    ]
    selected = _select_under_ref_budget(_ranked([*him, *heavy_her]))
    texts = [item.text for item in selected]

    assert SUN not in texts
    assert BUSY not in texts
    assert STRANGE not in texts
    assert any(item.speaker == "companion" for item in selected)


def test_packed_live_window_keeps_eight_companion_lines() -> None:
    him, her = _production_like_dialogue()
    # Stretch her side past the old 4-seat reserve.
    extra = []
    base = her[-1]
    for index in range(8):
        extra.append(
            base.model_copy(
                update={
                    "dialogue_id": f"{base.dialogue_id}:extra:{index}",
                    "text": f"companion-live-{index}",
                    "sequence": base.sequence + index + 1,
                    "source_claims": (
                        DialogueSourceClaim(
                            authority_event_ref=f"event:accept:{index}",
                            authority_world_revision=10 + index,
                            authority_payload_hash="a" * 64,
                        ),
                        DialogueSourceClaim(
                            authority_event_ref=f"event:payload:{index}",
                            authority_world_revision=10 + index,
                            authority_payload_hash="b" * 64,
                        ),
                    ),
                }
            )
        )
    marked_him, marked_her = _mark_live_conversation_head(list(him), list(extra))
    packed = pack_recent_dialogue_under_source_budget((*marked_him, *marked_her))
    companion_texts = [item.text for item in packed if item.speaker == "companion"]
    refs = {claim.authority_event_ref for item in packed for claim in item.source_claims}

    assert len(companion_texts) >= 8
    assert all(f"companion-live-{index}" in companion_texts for index in range(8))
    assert len(refs) <= 32


def test_packed_live_window_keeps_both_speakers() -> None:
    him, her = _production_like_dialogue()
    marked_him, marked_her = _mark_live_conversation_head(list(him), list(her))
    packed = pack_recent_dialogue_under_source_budget((*marked_him, *marked_her))
    texts = [item.text for item in packed]
    speakers = {item.speaker for item in packed}

    assert speakers == {"counterpart", "companion"}
    assert SUN in texts
    assert BUSY in texts
    assert STRANGE in texts
    assert her[-1].text in texts
    assert all("current_turn" not in item.continuity_reasons for item in packed)


def test_packed_window_fits_the_source_ref_budget() -> None:
    him, her = _production_like_dialogue()
    marked_him, marked_her = _mark_live_conversation_head(list(him), list(her))
    packed = pack_recent_dialogue_under_source_budget((*marked_him, *marked_her))
    refs = {claim.authority_event_ref for item in packed for claim in item.source_claims}

    assert len(packed) <= 16
    assert len(refs) <= 32
    field_count = sum(len(item.model_dump(mode="json")) for item in packed)
    assert field_count <= 256


def test_live_head_marks_are_deterministic() -> None:
    him, her = _production_like_dialogue()
    first = _mark_live_conversation_head(list(him), list(her))
    second = _mark_live_conversation_head(list(him), list(her))

    assert [item.model_dump() for item in first[0]] == [
        item.model_dump() for item in second[0]
    ]
    assert [item.model_dump() for item in first[1]] == [
        item.model_dump() for item in second[1]
    ]


def test_receipt_clock_and_impression_triggers_keep_live_head_marks() -> None:
    him, her = _production_like_dialogue()
    marked_him, marked_her = _mark_live_conversation_head(list(him), list(her))
    dialogue = tuple(sorted((*marked_him, *marked_her), key=lambda item: item.sequence))
    compiler = ConversationContinuityCompiler()
    for trigger in (
        "event:receipt:late-verified",
        "event:clock:advanced",
        "event:private-impression:accepted",
    ):
        continuity = compiler.compile(dialogue=dialogue, trigger_ref=trigger)
        by_text = {item.text: item for item in continuity.dialogue}
        assert SUN in by_text
        assert "recent" in by_text[SUN].continuity_reasons
        assert "current_turn" not in by_text[STRANGE].continuity_reasons
        assert "current_turn" not in by_text[SUN].continuity_reasons


def test_inbound_observation_trigger_still_overwrites_live_head_marks() -> None:
    him, her = _production_like_dialogue()
    marked_him, marked_her = _mark_live_conversation_head(list(him), list(her))
    dialogue = tuple(sorted((*marked_him, *marked_her), key=lambda item: item.sequence))
    sun = next(item for item in marked_him if item.text == SUN)
    trigger = sun.source_claims[0].authority_event_ref
    continuity = ConversationContinuityCompiler().compile(
        dialogue=dialogue, trigger_ref=trigger
    )
    by_text = {item.text: item for item in continuity.dialogue}

    assert "current_turn" in by_text[SUN].continuity_reasons
    assert "acknowledged_context" not in by_text[SUN].continuity_reasons
    assert "current_turn" not in by_text[STRANGE].continuity_reasons


def test_expired_advisory_states_timing_facts_without_telling_her_to_chase() -> None:
    value = expired_hope_advisory_value(
        hoped_response="他解释一下这个表情是什么意思",
        seconds_since_he_last_spoke=73,
        spoken_since_declared=True,
    )

    assert value == (
        "He last spoke 73s ago; he has spoken since she declared a hope. "
        "What she hoped for (her words, not a world event): 他解释一下这个表情是什么意思. "
        "Timing evidence only; she still decides."
    )
    assert "Hope expired:" not in value
    assert "Unanswered" not in value
    assert "没理" not in value
    assert "should" not in value.lower()
    assert "追问" not in value


def test_expired_advisory_attaches_when_pending_hope_has_already_expired() -> None:
    first_visible = SimpleNamespace(
        event_id="event:receipt:invite:accepted",
        event_type="ExecutionReceiptRecorded",
        world_revision=2,
        logical_time=EXPECTATION_NOW,
    )
    late_verified = SimpleNamespace(
        event_id=RECEIPT,
        event_type="ExecutionReceiptRecorded",
        world_revision=10,
        logical_time=EXPECTATION_NOW + timedelta(minutes=2),
    )
    sun_obs = SimpleNamespace(
        event_id="event:observation:sun",
        event_type="ObservationRecorded",
        world_revision=8,
        payload_hash=_hash("sun"),
        logical_time=EXPECTATION_NOW + timedelta(seconds=90),
    )
    expires_at = EXPECTATION_NOW + timedelta(seconds=120)
    projection = SimpleNamespace(
        logical_time=EXPECTATION_NOW + timedelta(seconds=180),
        message_observations=(
            SimpleNamespace(
                observation_id="message:sun",
                world_revision=8,
                event_payload_hash=_hash("sun"),
            ),
        ),
        committed_world_event_refs=(first_visible, late_verified, sun_obs),
        execution_receipts=(
            SimpleNamespace(action_id="action:invite", observed_state="provider_accepted"),
            SimpleNamespace(action_id="action:invite", observed_state="delivered"),
        ),
        expression_plan_manifests=(
            SimpleNamespace(
                plan_id="plan:invite",
                acceptance_event_ref="event:acceptance:invite",
                recorded_at_world_revision=1,
                response_expectation=SimpleNamespace(
                    source_beat_id="beat:invite",
                    hoped_response=HOPED,
                    pressure_bp=5_000,
                    importance_bp=5_000,
                    not_before=EXPECTATION_NOW + timedelta(seconds=60),
                    expires_at=expires_at,
                ),
                beats=(
                    SimpleNamespace(
                        beat_id="beat:invite",
                        action=SimpleNamespace(action_id="action:invite"),
                    ),
                ),
            ),
        ),
        response_expectation_assessments=(),
    )
    seconds, spoken_since = counterpart_last_spoke_facts(
        projection, since_world_revision=2
    )
    attached = attach_pending_expectation_advisory(
        {
            "world_id": "world:stale-context",
            "actor_ref": "agent:companion",
            "world_revision": 10,
            "deliberation_revision": 0,
            "ledger_sequence": 1,
            "logical_time": projection.logical_time.isoformat(),
            "consumer_scope": "deliberation_internal",
            "viewer_privacy_ceiling": "private",
            "context_compiler_version": "context-capsule-compiler:test",
            "truncation": {},
            "slices": {},
        },
        projection,
        anchor_event_ref=RECEIPT,
    )
    snapshot = compile_inner_life_snapshot(attached)
    advisories = snapshot.materials.get("advisories")

    assert expired_unanswered_expectation(projection) is not None
    assert seconds == 90
    assert spoken_since is True
    assert isinstance(advisories, list)
    assert advisories[0]["kind"] == "expired_expectation"
    value = advisories[0]["candidates"][0]["value"]
    assert "He last spoke 90s ago" in value
    assert "he has spoken since she declared a hope" in value
    assert "her words, not a world event" in value
    assert "Hope expired:" not in value
    assert "Timing evidence only; she still decides." in value
    assert "没理" not in value
    assert "追问" not in value
