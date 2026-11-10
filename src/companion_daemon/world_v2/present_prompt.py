"""Stable Present prompt pieces: identity prose and prefix-friendly ordering."""

from __future__ import annotations

from collections.abc import Mapping
import copy
import json
import re

from .companion_identity import CompanionIdentityFrame
from .affect_reference_view import present_affect_entry
from .private_turn_state import validate_authored_impression_retention

PRESENT_RECENT_DIALOGUE_ITEM_LIMIT = 240
PRESENT_COMPANION_DIALOGUE_ITEM_LIMIT = 240
PRESENT_MEMORY_ITEM_LIMIT = 8
PRESENT_FACT_ITEM_LIMIT = 8
PRESENT_IMPRESSION_ITEM_LIMIT = 8
PRESENT_EXPERIENCE_ITEM_LIMIT = 8
PRESENT_AUTHORED_RELATIONSHIP_SIGNAL_LIMIT = 4
PRESENT_ACCEPTED_RELATIONSHIP_COMMITMENT_LIMIT = 4
PRESENT_CAPSULE_HARD_MAX_CHARACTERS = 140_000
PRESENT_DIALOGUE_SLICE_CHARACTERS = 32_000
PRESENT_DIALOGUE_FOLD_CHUNK_ITEMS = 20
PRESENT_DIALOGUE_FOLD_LINE_CHARACTERS = 160
PRESENT_DIALOGUE_SEQUENCE_SCALE = 100
PRESENT_WEEK_DIARY_DAYS = 7
PRESENT_WEEK_DIARY_LINES_PER_DAY = 3
PRESENT_SHARED_MEDIA_ITEM_LIMIT = 8
PRESENT_PENDING_OUTBOUND_ITEM_LIMIT = 8

_PRESENT_USER_KEY_ORDER = (
    # Stable prefix first so DeepSeek context cache survives turn-to-turn drift.
    "expression_capabilities",
    "inner_life_snapshot",
    # Per-turn alias tables change often; keep them after the snapshot stable core.
    "expression_hard_boundaries",
    "quick_recovery_failure",
    "prior_source_closure_failure",
    "recall_available",
    "request",
    "current_trigger_message",
)
_SNAPSHOT_VOLATILE_LAST = (
    "snapshot_id",
    "snapshot_hash",
    "cursor",
    "truncation",
    "source_inventory",
    # Per-turn audit coordinates.  ``capability_scope`` carries the capability
    # manifest hash, which changes on every turn's payload, and ``source_refs``
    # re-binds the whole snapshot to the newest committed events.  Both used to
    # be serialized early (alphabetically), which truncated the cacheable prefix
    # about 110 characters into the World Context: measured 2026-09-12, that
    # cost 8 000 uncached prompt tokens per turn while the system contract was
    # the only region DeepSeek could reuse.
    "capability_scope",
    "source_refs",
)
_MATERIAL_ORDER = (
    "stable_self",
    "routine_background",
    "biographical_context",
    "day_sheet",
    "week_diary",
    "situation",
    "current_activities",
    "planned_activities",
    "recently_ended_activities",
    "pending_world_occurrences",
    "relationship",
    "protagonist_npc_relationships",
    "npc_observable_attitudes",
    "unresolved",
    "recent_self_experiences",
    "photos_i_shared",
    "messages_waiting_to_send",
    "moments_i_can_share",
    "remembered_material",
    "recalled_emotional_associations",
    "private_impressions",
    "relevant_facts",
    "appraisals",
    "affect",
    "change_phase",
    "advisories",
    "interruption",
    "perception",
    "interaction_acts",
    "folded_dialogue",
    "recent_dialogue",
    "conversation",
    "since_he_last_spoke",
    "我最近留下的",
    "lived_moment",
    "logical_time",
)

# Presentation-only order for the provider view.  ``_MATERIAL_ORDER`` above is
# *also* the slicing order ``background_context_profile`` uses to cut one
# snapshot per background lane, so it must not move.  What moves is only what
# the compact-gate request serializes.
#
# ``recent_dialogue`` is the one wide slice that grows by appending: with the
# frozen/volatile split it keeps its earlier bytes identical turn to turn, so it
# is emitted first and DeepSeek's prefix cache can reuse it.  Everything the old
# order put ahead of it carries a per-turn coordinate -- ``biographical_context``
# and ``day_sheet`` both stamp ``logical_at`` -- and measured 2026-09-12 on the
# three captured compact-gate runs, that coordinate was the first differing byte
# at offset ~2 487, capping the reusable prefix 2.5 KB into a 36 KB payload no
# matter how stable the dialogue itself had become.  Nothing is dropped or
# rewritten here: the same slices, in an order that lets the cache survive.
_PRESENT_MATERIAL_ORDER = (
    "recent_dialogue",
    *(
        key
        for key in _MATERIAL_ORDER
        if key != "recent_dialogue"
    ),
)

_RECENT_DIALOGUE_CACHE_KEYS = ("stable_turns", "volatile_last_turn")
_APPRAISAL_CACHE_KEYS = ("stable_rows", "volatile_last_row")
_AFFECT_CACHE_KEYS = ("stable_entries", "volatile_last_entry")

# ``ConversationContinuityCompiler.compile`` rebuilds the whole attention
# classification from scratch on every turn.  These three labels are therefore
# properties of *this* turn, not of the dialogue entry that carries them:
#
#   current_turn         this entry is the observation that triggered the turn
#   pending_interaction  his line is still inside the unacknowledged window
#   acknowledged_context a *recent* beat of hers answered it
#
# All three decay as the window slides -- ``acknowledged_context`` becomes
# ``recent``, ``current_turn`` becomes ``acknowledged_context``.  Writing them
# into entries that a later turn freezes made the "stable" half of
# ``stable_turns`` change underneath the cache: measured on the three captured
# compact-gate runs, 11 of 11 frozen-entry mutations were exactly
# ``["acknowledged_context","recent"] -> ["recent"]``, first at the head of the
# slice.  They are withheld from frozen entries and republished once per turn in
# the volatile tail, keyed by ``dialogue_id`` (unique for the counterpart
# entries that can carry them: 0 collisions across all three runs).
#
# ``recent`` and ``recent_companion`` stay inline: they are fill labels the
# compiler attaches to every item of a window far larger than the 16 items this
# slice presents, so they never changed on a frozen entry in any captured run.
_RECENT_DIALOGUE_PER_TURN_REASONS = (
    "acknowledged_context",
    "current_turn",
    "pending_interaction",
)
_RECENT_DIALOGUE_ATTENTION_KEY = "per_turn_attention"

# ---------------------------------------------------------------------------
# Presentation-only size cuts (evidence: output/release-readiness/APPENDIX-A-volume.md)
#
# Everything below removes bytes from the *serialized provider view* only.  The
# canonical Capsule, the frozen request object and every host-side reader keep
# the exact structures they had, and ``expand_present_world_context`` rebuilds
# the canonical shape from the presented one.  Two rules keep each cut
# provably reversible:
#
#   * a value is elided only when it equals a recorded constant of the Inner
#     Life contract, and a deviation is never elided -- absence therefore
#     always means "the constant", on every path;
#   * a reference is replaced by its existing short alias only inside the
#     volatile tail of the dialogue block, where the bytes already sit past the
#     first difference between two consecutive requests.
#
# That second restriction is measured, not stylistic.  ``source_ref_aliases``
# is renumbered from scratch every turn: of the dialogue ids carried over from
# one captured turn to the next, 45/48, 61/89, 62/76 and 66/77 received a
# *different* alias on the following turn.  Aliasing the frozen half of the
# window would therefore rewrite ``stable_turns`` every turn and move the first
# differing byte from the end of that window (~4 400-9 500 chars in) back into
# its head -- trading a cached byte (CNY 0.10/M) for an uncached one (CNY
# 3.00/M) to save a byte that was already cached.  In the volatile tail the
# same substitution is free.
_SOURCE_REF_IS_DIALOGUE_ID_KEY = "source_ref_is_dialogue_id"

# ``automatic_prefetch`` items are remembered private interpretations.  These
# five members are the wrapper the host attaches to every one of them; they
# hold one value in all 58 captured items across four runs.  ``text``,
# ``source_ref`` (the citation) and ``occurred_from`` (the recency) stay.
_AUTOMATIC_PREFETCH_CONSTANTS: tuple[tuple[str, object], ...] = (
    ("authority", "defeasible_interpretation"),
    ("epistemic_scope", "private_interpretation"),
    ("memory_kind", "reflective"),
    ("source_slice", "recalled_emotional_associations"),
    ("occurred_to", None),
)
_PREFETCH_AUTHORITY_FIELDS = frozenset({"authority", "epistemic_scope", "memory_kind"})
# Canonical member order of one ``automatic_prefetch`` item, so expansion puts
# the withheld constants back where the compiler wrote them.
_AUTOMATIC_PREFETCH_ITEM_ORDER = (
    "authority",
    "epistemic_scope",
    "memory_kind",
    "occurred_from",
    "occurred_to",
    "privacy_class",
    "source_ref",
    "source_slice",
    "text",
)

# ``_InteriorBinding.model_view`` wraps every scope/compiler binding as
# ``{"availability": "available", "value": ...}``.  "available" is the only
# value reachable while a value is present, so the wrapper key is elided and
# restored from that default; an explicit ``"unavailable"`` is never touched.
_AVAILABILITY_WRAPPER_KEYS = ("snapshot_compiler", "viewer_scope", "capability_scope")
_AVAILABILITY_AVAILABLE = "available"

# ``request.trigger_message`` is the same ``model_dump`` as the top-level
# ``current_trigger_message`` (seen at inbound_wire.py:10460 and :10529), so it
# is the same 638 characters twice.  When they are identical the copy becomes
# this pointer; when they differ at all the original object is kept verbatim.
_TRIGGER_MESSAGE_POINTER = "see current_trigger_message"


def source_ref_alias_index(aliases: object) -> dict[str, str]:
    """Return ``canonical ref -> shortest alias`` from an alias table.

    ``expression_hard_boundaries.source_ref_aliases`` maps one alias to one
    canonical ref, but a ref can carry several aliases (the beats of one
    companion expression share a ``dialogue_id``).  Any alias that resolves to
    the ref is a faithful citation, so the shortest one is chosen
    deterministically; ``expand_present_world_context`` inverts it through the
    same table, which makes the substitution lossless even when a ref is
    reachable under more than one alias.
    """

    if not isinstance(aliases, Mapping):
        return {}
    grouped: dict[str, list[str]] = {}
    for alias, ref in aliases.items():
        if not isinstance(alias, str) or not alias:
            continue
        if not isinstance(ref, str) or not ref:
            continue
        grouped.setdefault(ref, []).append(alias)
    return {
        ref: min(names, key=lambda name: (len(name), name))
        for ref, names in grouped.items()
    }


def _alias_dialogue_refs(
    entry: dict[str, object], alias_index: Mapping[str, str]
) -> dict[str, object]:
    """Rewrite citable refs of one *volatile* entry as their short aliases."""

    if not alias_index:
        return entry
    rebuilt: dict[str, object] = {}
    for key, value in entry.items():
        if key in {"dialogue_id", "source_ref"} and isinstance(value, str):
            rebuilt[key] = alias_index.get(value, value)
        elif key == "acknowledges_observation_event_refs" and isinstance(value, list):
            rebuilt[key] = [
                alias_index.get(ref, ref) if isinstance(ref, str) else ref
                for ref in value
            ]
        else:
            rebuilt[key] = value
    return rebuilt


def _dealias_dialogue_refs(
    entry: dict[str, object], aliases: object
) -> dict[str, object]:
    """Undo ``_alias_dialogue_refs`` with the alias table of the same view."""

    if not isinstance(aliases, Mapping) or not aliases:
        return entry
    rebuilt: dict[str, object] = {}
    for key, value in entry.items():
        if key in {"dialogue_id", "source_ref"} and isinstance(value, str):
            rebuilt[key] = aliases.get(value, value)
        elif key == "acknowledges_observation_event_refs" and isinstance(value, list):
            rebuilt[key] = [
                aliases.get(ref, ref) if isinstance(ref, str) else ref
                for ref in value
            ]
        else:
            rebuilt[key] = value
    return rebuilt


def _dialogue_source_ref_is_redundant(entries: list[object]) -> bool:
    """Whether every entry repeats its ``dialogue_id`` verbatim as ``source_ref``.

    ``snapshot_compiler._state_entry`` always copies the capsule item's
    ``source_ref`` onto the material entry, and for every non-media dialogue
    item that is byte-identical to ``dialogue_id`` (396/396 entries across the
    four captured runs).  The copy is withheld only when the whole block
    qualifies, so expansion can put it back on every entry without guessing
    which ones ever had it.
    """

    if not entries:
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        dialogue_id = entry.get("dialogue_id")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            return False
        if entry.get("source_ref") != dialogue_id:
            return False
    return True


def _without_dialogue_source_ref(entry: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in entry.items() if key != "source_ref"}


def _with_dialogue_source_ref(entry: dict[str, object]) -> dict[str, object]:
    """Restore the withheld ``source_ref`` copy at its canonical member position."""

    if "source_ref" in entry or not isinstance(entry.get("dialogue_id"), str):
        return entry
    rebuilt: dict[str, object] = {}
    placed = False
    for key, value in entry.items():
        rebuilt[key] = value
        if key == "sequence":
            rebuilt["source_ref"] = entry["dialogue_id"]
            placed = True
    if not placed:
        rebuilt["source_ref"] = entry["dialogue_id"]
    return rebuilt


def _freeze_dialogue_entry(
    entry: dict[str, object],
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Return one entry without per-turn labels, plus the labels held back."""

    reasons = entry.get("continuity_reasons")
    if not isinstance(reasons, list):
        return entry, ()
    per_turn = tuple(
        reason
        for reason in reasons
        if isinstance(reason, str) and reason in _RECENT_DIALOGUE_PER_TURN_REASONS
    )
    if not per_turn:
        return entry, ()
    if not isinstance(entry.get("dialogue_id"), str) or not entry["dialogue_id"]:
        # No stable key to republish under, so dropping the label would lose a
        # signal the system contract promises.  Keep the entry as it is.
        return entry, ()
    kept = [
        reason
        for reason in reasons
        if not (isinstance(reason, str) and reason in _RECENT_DIALOGUE_PER_TURN_REASONS)
    ]
    rebuilt: dict[str, object] = {}
    for key, value in entry.items():
        if key != "continuity_reasons":
            rebuilt[key] = value
        elif kept:
            rebuilt[key] = kept
    return rebuilt, per_turn


def _restore_dialogue_attention(
    entry: dict[str, object], attention: object
) -> dict[str, object]:
    """Re-attach per-turn labels withheld by ``cache_stable_recent_dialogue``."""

    if not isinstance(attention, dict):
        return entry
    ref = entry.get("dialogue_id")
    extra = attention.get(ref) if isinstance(ref, str) else None
    if not isinstance(extra, list) or not extra:
        return entry
    merged = sorted(
        dict.fromkeys(
            (
                *(
                    reason
                    for reason in entry.get("continuity_reasons", ())
                    if isinstance(reason, str)
                ),
                *(reason for reason in extra if isinstance(reason, str)),
            )
        )
    )
    rebuilt: dict[str, object] = {}
    placed = False
    for key, value in entry.items():
        if key == "continuity_reasons":
            rebuilt[key] = merged
            placed = True
            continue
        if not placed and key == "sequence":
            rebuilt["continuity_reasons"] = merged
            placed = True
        rebuilt[key] = value
    if not placed:
        rebuilt["continuity_reasons"] = merged
    return rebuilt


def dialogue_attention_reasons(value: object) -> dict[str, list[str]]:
    """Per-turn attention labels withheld from the frozen dialogue entries."""

    if not isinstance(value, dict):
        return {}
    attention = value.get(_RECENT_DIALOGUE_ATTENTION_KEY)
    if not isinstance(attention, dict):
        return {}
    return {
        ref: [reason for reason in reasons if isinstance(reason, str)]
        for ref, reasons in attention.items()
        if isinstance(ref, str) and isinstance(reasons, list) and reasons
    }


def recent_dialogue_material_entries(
    value: object, *, source_ref_aliases: object = None
) -> list[dict[str, object]]:
    """Expand cache-split or legacy recent_dialogue without changing semantics.

    Inverts everything ``cache_stable_recent_dialogue`` did to the block:
    withheld per-turn labels, refs shortened to their alias, and the withheld
    duplicate ``source_ref``.  ``source_ref_aliases`` is the alias table of the
    same view (``expression_hard_boundaries.source_ref_aliases``); without it an
    aliased entry is returned with its alias, which is what a view-only reader
    that has no alias table can honestly do.  Canonical input is returned
    unchanged.
    """

    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        stable = value.get("stable_turns")
        volatile = value.get("volatile_last_turn")
        attention = value.get(_RECENT_DIALOGUE_ATTENTION_KEY)
        redundant = value.get(_SOURCE_REF_IS_DIALOGUE_ID_KEY) is True
        entries: list[dict[str, object]] = []
        if isinstance(stable, list):
            entries.extend(item for item in stable if isinstance(item, dict))
        if isinstance(volatile, dict):
            entries.append(volatile)
        if entries:
            expanded: list[dict[str, object]] = []
            for entry in entries:
                restored = _restore_dialogue_attention(entry, attention)
                restored = _dealias_dialogue_refs(restored, source_ref_aliases)
                if redundant:
                    restored = _with_dialogue_source_ref(restored)
                expanded.append(restored)
            return expanded
        legacy_items = value.get("items")
        if isinstance(legacy_items, list):
            return [item for item in legacy_items if isinstance(item, dict)]
    return []


def cache_stable_recent_dialogue(
    value: object, *, source_ref_aliases: object = None
) -> object:
    """Split dialogue tail so prior turns stay byte-stable for provider KV cache.

    Frozen entries keep only the labels that describe the entry itself; the
    per-turn attention labels move to the volatile ``per_turn_attention`` map,
    where rewriting them costs nothing because the tail is uncached anyway.

    Two further cuts are presentation-only.  A ``source_ref`` that only repeats
    its ``dialogue_id`` is withheld from the whole block (declared by
    ``source_ref_is_dialogue_id``) because the readers of the material already
    hold the canonical entry from the Capsule.  The volatile last turn has its
    citable refs replaced by the aliases the same message already publishes, so
    the tail stays citable without paying 166 characters per ref; the frozen
    half is deliberately left canonical because the alias table is renumbered
    on nearly every turn.

    Already-split input is re-frozen rather than passed through, so applying the
    presenter twice is the same as applying it once.
    """

    alias_index = source_ref_alias_index(source_ref_aliases)
    if isinstance(value, dict):
        stable = value.get("stable_turns")
        volatile = value.get("volatile_last_turn")
        if not isinstance(stable, list) or not isinstance(volatile, dict):
            return value
        attention: dict[str, list[str]] = {
            ref: list(reasons)
            for ref, reasons in dialogue_attention_reasons(value).items()
        }
        return _split_stable_dialogue(
            stable,
            volatile,
            attention=attention,
            alias_index=alias_index,
            already_declared=value.get(_SOURCE_REF_IS_DIALOGUE_ID_KEY) is True,
        )
    if not isinstance(value, list) or len(value) < 2:
        return value
    return _split_stable_dialogue(
        value[:-1], value[-1], attention={}, alias_index=alias_index
    )


def _split_stable_dialogue(
    frozen_source: list[object],
    volatile_last_turn: object,
    *,
    attention: dict[str, list[str]],
    alias_index: Mapping[str, str],
    already_declared: bool = False,
) -> dict[str, object]:
    redundant = already_declared or _dialogue_source_ref_is_redundant(
        [*frozen_source, volatile_last_turn]
    )
    frozen: list[object] = []
    for entry in frozen_source:
        if not isinstance(entry, dict):
            frozen.append(entry)
            continue
        stripped, per_turn = _freeze_dialogue_entry(entry)
        if redundant and isinstance(stripped, dict):
            stripped = _without_dialogue_source_ref(stripped)
        frozen.append(stripped)
        if per_turn:
            ref = entry["dialogue_id"]
            assert isinstance(ref, str)
            held = attention.setdefault(ref, [])
            for reason in per_turn:
                if reason not in held:
                    held.append(reason)
    volatile = volatile_last_turn
    if isinstance(volatile, dict):
        volatile = _alias_dialogue_refs(volatile, alias_index)
        if redundant:
            volatile = _without_dialogue_source_ref(volatile)
    payload: dict[str, object] = {
        "stable_turns": frozen,
        "volatile_last_turn": volatile,
    }
    if attention:
        payload[_RECENT_DIALOGUE_ATTENTION_KEY] = attention
    if redundant:
        payload[_SOURCE_REF_IS_DIALOGUE_ID_KEY] = True
    return payload


def appraisal_material_rows(value: object) -> list[list[object]]:
    """Expand cache-split or legacy compact appraisal rows without changing semantics."""

    if isinstance(value, dict):
        rows = value.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, list)]
        stable = value.get("stable_rows")
        volatile = value.get("volatile_last_row")
        expanded: list[list[object]] = []
        if isinstance(stable, list):
            expanded.extend(row for row in stable if isinstance(row, list))
        if isinstance(volatile, list):
            expanded.append(volatile)
        if expanded:
            return expanded
    return []


def cache_stable_appraisals(value: object) -> object:
    """Split compact appraisal rows so prior rows stay byte-stable for provider KV cache."""

    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        rows = [row for row in value["rows"] if isinstance(row, list)]
        if len(rows) < 2:
            return value
        payload = {key: item for key, item in value.items() if key != "rows"}
        return {
            **payload,
            "stable_rows": rows[:-1],
            "volatile_last_row": rows[-1],
        }
    if isinstance(value, list) and len(value) >= 2:
        return {
            "stable_entries": value[:-1],
            "volatile_last_entry": value[-1],
        }
    return value


def affect_material_entries(value: object) -> list[dict[str, object]]:
    """Expand cache-split or legacy affect material without changing semantics."""

    if isinstance(value, list):
        return [present_affect_entry(item, expand=True) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        stable = value.get("stable_entries")
        volatile = value.get("volatile_last_entry")
        entries: list[dict[str, object]] = []
        if isinstance(stable, list):
            entries.extend(item for item in stable if isinstance(item, dict))
        if isinstance(volatile, dict):
            entries.append(volatile)
        if entries:
            return [present_affect_entry(item, expand=True) for item in entries]
        legacy_items = value.get("items")
        if isinstance(legacy_items, list):
            return [
                present_affect_entry(item, expand=True)
                for item in legacy_items if isinstance(item, dict)
            ]
    return []


def cache_stable_affect(value: object) -> object:
    """Split the tail and reversibly table typed references; keep every episode."""

    if isinstance(value, dict):
        # Older captured requests already carry the cache split. Re-presenting
        # those requests is idempotent and does not erase wrapper metadata.
        payload = dict(value)
        for key in ("stable_entries", "items"):
            if isinstance(payload.get(key), list):
                payload[key] = [present_affect_entry(item) for item in payload[key]]
        if isinstance(payload.get("volatile_last_entry"), dict):
            payload["volatile_last_entry"] = present_affect_entry(payload["volatile_last_entry"])
        return payload
    if not isinstance(value, list):
        return value
    entries = [present_affect_entry(item) for item in value]
    if len(entries) < 2:
        return entries
    return {
        "stable_entries": entries[:-1],
        "volatile_last_entry": entries[-1],
    }


def slim_peer_specimen() -> dict[str, object]:
    """The one shape sample for the slim decision object.

    Both carriers show this same sample: inner reading and self-state precede
    outward speech so her psychological state causally drives her action;
    ``messages``/``later``/``waiting_for`` sit at the same rank, an empty
    ``messages`` array is silence, and ``wait`` stays off it because she writes
    the short sentence instead.
    """

    return {
        "meaning_of_this": "<role:reading_text>",
        "my_state": "<role:self_state_text>",
        "world_claims": [],
        "messages": ["<role:visible_text>"],
        "later": None,
        "waiting_for": None,
    }


def reply_only_completion_clause() -> str:
    return (
        "reply_only is complete when the external effect fits its slim surface—"
        "the text bubbles you choose to send now (optionally with photo/media_request), "
        "the text bubbles you choose to send later, or silence—"
        "with no continuation and no interaction-protocol update"
    )


def reply_only_bubble_clause() -> str:
    return (
        "Each messages item or text beat is one bubble. "
        "Several sentences in one string remain one bubble. "
        "Consecutive bubbles are consecutive items. "
        "The host does not require a follow-up question, a wrap-up, "
        "or a next step. Silence is complete."
    )


def slim_consider_instruction(*, preserve_legacy_authoring: bool = False) -> str:
    """Describe the slim object concisely in her own language without defensive essay bloat."""

    return (
        "一个 slim 对象就够了。跟 messages 同级可见的是 later，"
        "以及把 messages 写成空数组就是这一轮不回（silent）。"
        "字段包括：meaning_of_this、my_state、stuck_with_me、wants、photo，"
        "以及可选的 waiting_for、wait、how_it_landed、noticed、"
        "keep_impression、come_back、come_back_in、about_us、why_us、us_deltas、"
        "we_are、calling_it、said_as、matters_bp、pressure_bp、importance_bp、"
        "declared_display。宿主不会替你编或代填任何字段；写与不写全由你定，"
        "只有你写下的内心状态会跟着你走，都不写就是这一轮什么都没留下。\n"
        "life_intent={execution_scope:self_directed,intention:future,start_after_seconds:0,duration_seconds:600,"
        "importance_bp:0};full:appraisal_draft. Context-time plan, "
        "no place/NPC/event/results. Prose/wants: no action.\n"
        "messages 是你决定发出去的气泡，一项就是一条。"
        "事实来源是硬边界：messages 陈述外部事件、地点、行为或既往经历时，"
        "须在同级 world_claims 逐条写 claim_text、scope、source_refs（可从 Context 的 source_ref_aliases 挑短标识如 S1、T1，或原样抄 source_ref），"
        "仅用 expression_hard_boundaries 中相应时态的来源。"
        "day_sheet 和传记习惯不能证明当前或刚才发生了什么；没有来源的经历不能补写。"
        + (
            ""
            if preserve_legacy_authoring
            else "\n"
            "scope 按事情本身的时间选：已经发生过的经历属于 shared_history 或 past_world，不要写成 current_world；"
            "current_world 只给此刻确实成立的世界状态。你自己的打算、意愿、语气和态度由你写，不会自动变成已发生的事实或持续状态。"
        )
        + "Context 里还活着的 affect 强度和你这轮写下的 target_intensity_bp 是状态而非行为指令："
        "真的有很多话时可以连续写多条 messages，也可以只说一句或沉默；"
        "情绪强度不会命令你发几条，也不会替你决定语气。"
        + reply_only_bubble_clause()
        + "\n"
        "later 是这些已经想好的话等多少秒再发出去（5..86400 整数秒，只能配非空 messages，不能与 silent 或 photo 同用）；"
        "到点发出前若期间有新对话，宿主会再给你一次机会决定原样发、改写或不发。\n"
        "meaning_of_this 是你对他这句话或眼前处境的暂定理解；"
        "my_state 是你表达前自己此刻真正是什么感觉、想要什么、身体上或注意里搁着什么。"
        "先形成 meaning_of_this 和 my_state，让真实的想法和情绪去驱动你的表达节奏与潜台词，"
        "不要先写台词再事后补观后感；它们不会自动变成可见台词。\n"
        "photo 写 true（或直接写可用候选的 source_ref），意思是你现在想让媒体车道考虑一个可用的候选；"
        "只在文字里说要发图不会打开这条车道。photo true（或写一个可用候选的 source_ref）可以搭 reply_only 或 full_turn，只能配现在发的非空 messages。"
        "当 expression_capabilities.media_request_mode 是 candidate_only 时，即使还没列出候选也可写 photo。\n"
        "photos_i_shared 是你已经成功发给他的照片这一世界事实（发出去的那张会出现在 conversation 栏里，不能再说还没发）。"
        "messages_waiting_to_send 是你已经写好但还没发出的延时消息。\n"
        "moments_i_can_share 里有两件互不替代的世界事实："
        "第一件是存货（available_count 为 0 才是现在没有可发的存货；photo_in_hand 为 true 表示这张现在就可以选、就可以发，和 now.photographable 无关；"
        "what_happened 是那一刻已接受的原文，false 时 hold_reason 是原因，如 already_shared 已经发给他）；"
        "第二件是此刻能不能新拍（now.photographable 为 true 表示可新拍；photographable 为 false 只和「现在新拍一张」打架，"
        "不阻止你发 photo_in_hand 为 true 的存货，也不等于还在整理、相册是空的、或现在发不了手上的那张；reason 含 no_active_activity / annex_insufficient / already_open）。"
        "这不是建议你发存货，也不是建议你去拍。\n"
        "appraisals / unresolved / private_impressions 是账本上仍活着的读法、未完成线程、私下理解（条数就是还活着的条数，不是只给你看最近一条；hold_reason=user_channel_limited 表示仅限私下）。"
        "relevant_facts、appraisals、day_sheet、current_activities 和 recent_self_experiences 是你已经知道和理解事情的背景与心情底色，不是台词清单，也不是每轮必报的日程表；"
        "默默读懂也已经是在使用它们。你可以让心里想的（my_state）和嘴上说的（messages）保留真实的潜台词、嘴硬心软、调侃或暧昧张力；无需像结单一样把话收圆。\n"
        "凡是带 _bp 的整数都是基点（0..10000，不是百分制：5000 是一半，两千才是两成，八十不是百分之八）：\n"
        "- waiting_for（短句）+ wait（5..86400 整数秒，如 30、60、90）+ pressure_bp + importance_bp：四项同写才在超时未回时叫醒你；不写 wait 不会有人按秒叫你，口头说「我等你」和写下 waiting_for 不是同一件事。\n"
        "- come_back 是你心里搁着的一件事，与 come_back_in（5..86400 整数秒，或对象形式）成对写，到点叫醒你补充或换话头。\n"
        "- how_it_landed：有 pending 盼头时可写 fulfilled、superseded、still_pending 或 uncertain。\n"
        "- noticed 仅记本轮注意到的主观细节；想留成私人印象则写 stuck_with_me 并设 keep_impression=true。\n"
        "- affect：可选 open、update、resolve 或 supersede（update/resolve/supersede 需 episode_id，resolve 需 resolution_summary，open/update/supersede 配 components=[{\"dimension\":\"warmth\",\"target_intensity_bp\":3200}]；维度可选 hurt、anger、sadness、loneliness、anxiety、resentment、warmth、joy）；matters_bp（1..10000）决定哪份读法会被再叫醒。\n"
        "- declared_display：可选 sexual_suggestive、explicit_adult 或 withdraw，同轮配 photo=true；也不偏好你写了比不写更好。\n"
        "- about_us、why_us、us_deltas（这件事只有你能定：trust_bp 是能不能信他，closeness_bp 是近不近，respect_bp 是感到被尊重，reliability_bp 是你觉得他靠不靠得住，mutuality_bp 是双向感，repair_confidence_bp 是闹别扭之后你觉得还能不能修好；单轮每轴截到 ±500，阶段门槛在两千、四千五量级，单次 +20 几乎未动）：三项必须一起写（不必先有承诺，也不必先有裂痕，三个得一起写，宿主也不会替你补上缺的字段，也从不建议你写大或写小，也从不建议你写哪几根轴）。\n"
        "- we_are 是你可以写的字段（acquaintance、friend、close_friend、ambiguous、lover，例如 we_are=friend 不必先经过 acquaintance）：只在字段里写 we_are，账本上的阶段不会动；必须与 calling_it、said_as（一字不差抄自实际送达的某条 messages，从不替你生成 said_as，从不从你的措辞里推断承诺，选了沉默也不算）三项同写。关系阶段与慢变量全都是证据而非指令，宿主没有目标阶段也没有偏好方向，被吸引、说不清、觉得没意思或想离远一点都由你自己读；不写、不说、维持现状、说出来都由你定。\n"
    )


# Optional slim keys shown as JSON null in the compact-gate specimen. Null is
# absence: the host never treats a listed key as a field she is supposed to fill.
SLIM_OPTIONAL_SPECIMEN_KEYS = (
    "waiting_for",
    "wait",
    "come_back",
    "come_back_in",
    "later",
    "us_deltas",
    "about_us",
    "why_us",
    "matters_bp",
    "pressure_bp",
    "importance_bp",
    "stuck_with_me",
    "keep_impression",
    "photo",
    "declared_display",
    "wants",
    "life_intent",
    "how_it_landed",
    "noticed",
    "affect",
    "episode_id",
    "components",
    "resolution_summary",
)


def relationship_commitment_usage_specimen() -> dict[str, object]:
    """Concrete optional example: one delivered line binds one declaration.

    Keep this separate from the generic null-filled shape.  Showing the
    triplet as three more nulls taught the provider that it was ordinary
    omitted residue, while the only useful shape is all three fields together
    with ``said_as`` copied from a visible message.
    """

    spoken = "嗯，那我也认了——我们现在算朋友。"
    return {
        "messages": [spoken],
        "meaning_of_this": "他也在认真确认我们的关系。",
        "my_state": "我愿意把这层关系说清楚。",
        "we_are": "friend",
        "calling_it": "朋友",
        "said_as": spoken,
    }


def affect_usage_specimen() -> dict[str, object]:
    """Concrete optional example: a felt shift worth keeping as lasting Affect.

    Keep this separate from the generic null-filled shape.  Showing affect as
    null beside my_state taught the provider that inner prose alone was enough,
    while the only useful shape pairs a self-state with affect + components when
    something should persist beyond this turn.
    """

    return {
        "messages": ["你突然这样说……我反而有点接不住"],
        "meaning_of_this": "他在认真夸我，不是开玩笑",
        "my_state": "心里是暖的，但不想表现得太明显",
        "affect": "open",
        "components": [{"dimension": "warmth", "target_intensity_bp": 6200}],
    }


def calm_affect_usage_specimen() -> dict[str, object]:
    """Concrete optional example: calm turns omit affect on purpose."""

    return {
        "messages": ["嗯 知道啦"],
        "meaning_of_this": "就是一句普通的行程说明",
        "my_state": "我现在很平静，没有特别的情绪要留下",
    }


def silent_usage_specimen() -> dict[str, object]:
    """Concrete optional example: read but choose not to reply this turn."""

    return {
        "messages": [],
        "meaning_of_this": "他这句我不想接",
        "my_state": "看到了，但现在不想说，给他一点空间",
    }




































def reply_only_slim_shape_specimen() -> dict[str, object]:
    """Shape of the slim object: required markers plus optional keys as null.

    Null means unused this turn. The specimen is a map of available decisions,
    not a form and not a recommended fill.
    """

    specimen: dict[str, object] = {
        "messages": ["<role:visible_text>"],
        "meaning_of_this": "<role:reading_text>",
        "my_state": "<role:self_state_text>",
        "world_claims": [],
    }
    for key in SLIM_OPTIONAL_SPECIMEN_KEYS:
        specimen[key] = None
    return specimen


def slim_consider_json_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "messages": {"type": "array"},
            "meaning_of_this": {"type": "string"},
            "my_state": {"type": "string"},
            "world_claims": {"type": "array"},
            "stuck_with_me": {"type": "string"},
            "wants": {"type": "string"},
            "life_intent": {"type": ["object", "null"]},
            "photo": {"type": ["boolean", "string"]},
            "waiting_for": {"type": "string"},
            "wait": {},
            "later": {},
            "come_back": {"type": "string"},
            "come_back_in": {},
            "how_it_landed": {"type": "string"},
            "noticed": {"type": "string"},
            "we_are": {
                "type": "string",
                "enum": ["acquaintance", "friend", "close_friend", "ambiguous", "lover"],
            },
            "calling_it": {"type": "string"},
            "said_as": {"type": "string"},
            "us_deltas": {"type": "object"},
            "matters_bp": {},
            "pressure_bp": {},
            "importance_bp": {},
            "declared_display": {"type": "string"},
            "affect": {"type": "string"},
            "episode_id": {"type": "string"},
            "components": {"type": "array"},
            "resolution_summary": {"type": "string"},
        },
        "required": ["messages", "meaning_of_this", "my_state"],
    }


def canonical_atomic_consider_instruction() -> str:
    """Role guidance for the complete draft dialect, without slim aliases.

    Capability schemas and the full appraisal/expression contracts remain the
    authority for fields and bounds. This replaces only the slim tutorial in
    opt-in atomic v3 requests; historical v1/v2 leads retain their exact bytes.
    """

    return (
        "\nCANONICAL ATOMIC ROLE GUIDANCE .1:\n"
        "这一轮使用完整的 appraisal_draft 与 expression_draft；"
        "字段层级和可选能力以它们的完整合同与本次工具 schema 为准。"
        "appraisal_draft 是你对眼前事情的暂定理解，"
        "expression_draft.private_turn_state 是表达前你自己的感受、注意和想法；"
        "它们不会自动成为说给对方的话，也不会产生新的外部事实。"
        "你的动机、态度、措辞、消息条数、是否追问、是否主动联系、是否沉默都由你决定。"
        "理解和回忆可以只改变你的回应方式，不需要为了证明记得而复述背景。\n"
        "expression_draft.beats 是你选择表达的内容；timing_choice 决定现在说、稍后说或沉默。"
        "延后须同时提供 delay_seconds 与 expires_after_seconds；沉默时 beats 为空。"
        "持续情绪用 appraisal_draft.affect 和相应生命周期字段表达，"
        "components 的 target_intensity_bp 是绝对强度基点，不是增量或表达指令。"
        "状态强弱不会替你决定语气或消息数量。"
        "若你选择保留私下理解，在 expression_draft.private_turn_state 中填写"
        "非空 stuck_with_me 和 keep_impression=true，并提供这轮有来源的 appraisal；"
        "否则可以不保留。私下解释可以有不确定性，不能充当新经历的来源。\n"
        "appraisal_draft.life_intent 表达你选择的一项未来自主活动，"
        "只在本次能力允许时填写；意图、愿望和普通台词不证明活动已经完成。"
        "expression_draft.response_expectation 和 revisit 分别记录你选择的等待与回访；"
        "普通台词不会自动建立它们。关系理解、变化和可见承诺分别使用合同中的"
        "relationship_signal、relationship_commitment 与 interaction_act；"
        "是否使用由你决定，字段本身不能冒充已经交付或完成的外部结果。\n"
        "可见外部事实在 expression_draft.world_claims 中声明，并使用 Context 的"
        "source_ref_aliases 短标识或原样 source_ref，遵守该来源的对象、时间和事实范围。"
        "一般背景或习惯不能代替具体经历的来源；当前感受和计划不能证明行动已经完成。"
        "appraisals、private_impressions 和相关事实是理解情境的材料，不是台词清单；"
        "user_channel_limited 的私人内容不能用于当前对话。\n"
        "本次允许媒体能力时，expression_draft.media_request 配合 media_source_refs "
        "表达考虑或选择可用候选的意图；只说要发图不等于请求，更不等于已经发出。"
        "媒体请求仅能配 timing_choice=now。photos_i_shared 表示已成功发送的照片，"
        "messages_waiting_to_send 表示尚未发送的内容，两者不能互换。"
        "moments_i_can_share 的 available_count 和 photo_in_hand 描述已有可选存货，"
        "now.photographable 描述此刻能否新拍；此刻不能新拍不等于没有可发的存货。"
        "候选的 hold_reason、隐私和权限继续有效，提不提、选不选由你决定。\n"
        "只有本次明确允许 Recall 时才可请求检索；"
        "检索提供你所选范围的记忆材料，不替你决定表达。"
    )


def combined_turn_system_lead(
    *, private_turn_state_required: bool, atomic_drafts: bool = False,
    preserve_legacy_authoring: bool = False,
) -> str:
    instruction = (
        canonical_atomic_consider_instruction() if atomic_drafts else slim_consider_instruction(preserve_legacy_authoring=preserve_legacy_authoring)
    )
    epistemic_expression_clause = (
        "appraisal_draft 负责你私下怎么理解，expression_draft 负责你决定说什么；"
        "后者不需要复述、解释或证明前者，让那份理解改变回应方式就已经够了。"
        "要 keep_impression=true 留下 stuck_with_me 原文，需要本次有来源的 appraisal，"
        "不能同时写 appraise=false；也可以自主不保留。"
        "先从你已经知道的事实和私下理解出发读懂这一轮，再决定自然回应什么。"
        "知道、认出或推断了一个意思，并不要求你在可见消息里把那个意思解释给他听；"
        "理解可以只体现在你的回应方式里。是否点破仍是你的表达决定，"
        "不要为了证明你记得或看懂了而复述 Context。"
        "如果眼前是你已经理解的表情、暗号或习惯用法，就把它当作你们之间的语言本身来接住，"
        "而不是把释义当成一条新消息解释回去；只有当你此刻确实想谈那个含义时才需要点明。"
        "如果同一层意思在 recent_dialogue 里已经说过，它再次出现也不会自动成为一个新话题；"
        "你仍可以接住、转开、逗他或沉默，不需要靠重复释义来表示你看见了。"
    )
    if private_turn_state_required:
        return (
            "Return either one JSON object with exactly two keys, appraisal_draft "
            "and expression_draft, or a recall choice with exactly the keys "
            "private_turn_state and recall_request in either serialization order "
            "when the occasion (last user object) says recall is available. "
            "If recall is unavailable, return only the two-draft envelope. "
            + epistemic_expression_clause
            + instruction
        )
    return (
        "Return either one JSON object with exactly two keys, "
        "appraisal_draft and expression_draft, or the single recall_request "
        "object described below when the occasion says recall is available. "
        "If recall is unavailable, return only the two-draft envelope. "
        + epistemic_expression_clause
        + instruction
    )


def compact_gate_recall_instruction() -> str:
    return (
        "Choose result_kind=recall only when the occasion says recall is available; "
        "otherwise do not choose recall. "
    )


def forced_tool_recall_instruction(*, private_turn_state_required: bool) -> str:
    extra = (
        " and private_turn_state"
        if private_turn_state_required
        else " (private_turn_state may also be included)"
    )
    return (
        "For result_kind=recall include recall_request"
        + extra
        + " only when the occasion says recall is available."
    )


def _present_availability_wrappers(snapshot: dict[str, object]) -> dict[str, object]:
    """Elide ``"availability": "available"`` from the scope/compiler bindings.

    ``_InteriorBinding.model_view`` returns ``{"availability": "available",
    "value": ...}`` whenever a value exists; only the *unavailable* stub carries
    information, and it is never touched.
    """

    for key in _AVAILABILITY_WRAPPER_KEYS:
        binding = snapshot.get(key)
        if (
            isinstance(binding, dict)
            and binding.get("availability") == _AVAILABILITY_AVAILABLE
            and len(binding) > 1
        ):
            snapshot[key] = {
                name: value
                for name, value in binding.items()
                if name != "availability"
            }
    return snapshot


def _expand_availability_wrappers(snapshot: dict[str, object]) -> dict[str, object]:
    for key in _AVAILABILITY_WRAPPER_KEYS:
        binding = snapshot.get(key)
        if isinstance(binding, dict) and "availability" not in binding:
            rebuilt: dict[str, object] = {"availability": _AVAILABILITY_AVAILABLE}
            rebuilt.update(binding)
            snapshot[key] = rebuilt
    return snapshot


def _prefetch_constants(snapshot: Mapping[str, object]) -> tuple[tuple[str, object], ...]:
    # A saved snapshot selects its original reversible presentation. New .27
    # keeps the actual authority labels visible instead of leaving the model
    # to infer them from omitted constants. Missing labels remain missing.
    from .character_interior.life_context_presentation import EXPLICIT_PREFETCH_AUTHORITY_COMPILER_VERSIONS

    compiler = snapshot.get("snapshot_compiler")
    version = compiler.get("value") if isinstance(compiler, Mapping) else None
    if isinstance(version, str) and version in EXPLICIT_PREFETCH_AUTHORITY_COMPILER_VERSIONS:
        return tuple(pair for pair in _AUTOMATIC_PREFETCH_CONSTANTS if pair[0] not in _PREFETCH_AUTHORITY_FIELDS)
    return _AUTOMATIC_PREFETCH_CONSTANTS


def _compact_story_prefetch(snapshot: Mapping[str, object]) -> bool:
    from .character_interior.life_context_presentation import COMPACT_STORY_PREFETCH_COMPILER_VERSIONS

    compiler = snapshot.get("snapshot_compiler")
    version = compiler.get("value") if isinstance(compiler, Mapping) else None
    return isinstance(version, str) and version in COMPACT_STORY_PREFETCH_COMPILER_VERSIONS


# Presentation-only framing for unbidden memories.  It tells her what these
# items are, never what to do with them; ``_expand_automatic_prefetch`` removes
# it so the canonical snapshot round-trips unchanged.
AUTOMATIC_PREFETCH_FRAMING_KEY = "how_this_came_to_mind"
AUTOMATIC_PREFETCH_FRAMING = (
    "这些是此刻被眼前的话或刚经历的事自然勾起的旧记忆，可能相关，也可能只是一闪而过的联想。"
    "它们是你过去的记忆，不是现在新发生的事，提起时按它原本的时间和细节。"
    "像平常人一样，有时会顺口说一句「这让我想起…」，有时只在心里过一下，也可以完全不理会；"
    "要不要想、要不要说、跟谁说、怎么说，都由你决定。"
)


def _present_automatic_prefetch(value: object, *, constants=_AUTOMATIC_PREFETCH_CONSTANTS,
                                compact_stories: bool = False, lived_framing: bool = True) -> object:
    """Elide only the constants allowed by the original snapshot version."""

    if not isinstance(value, Mapping):
        return value
    items = value.get("items")
    if not isinstance(items, list):
        return value
    if items and lived_framing:
        value = {AUTOMATIC_PREFETCH_FRAMING_KEY: AUTOMATIC_PREFETCH_FRAMING, **value}
    presented: list[object] = []
    for item in items:
        if not isinstance(item, dict):
            presented.append(item)
            continue
        item_constants = _prefetch_item_constants(item, constants) if compact_stories else constants
        presented.append(
            {
                name: entry
                for name, entry in item.items()
                if not any(
                    name == constant and entry == expected
                    for constant, expected in item_constants
                )
            }
        )
    return {**{k: v for k, v in value.items() if k != "items"}, "items": presented}


def _prefetch_item_constants(item: Mapping[str, object], constants):
    history = item.get("prehistory")
    story = history.get("story") if isinstance(history, Mapping) else None
    if isinstance(story, Mapping) and "outgoing_relations" in story:
        # hybrid.15's compact story reading intentionally has no generic
        # memory wrapper. Its absent members are not elided emotional defaults.
        return ()
    return constants


def _expand_automatic_prefetch(value: object, *, constants=_AUTOMATIC_PREFETCH_CONSTANTS,
                               compact_stories: bool = False) -> object:
    if not isinstance(value, Mapping):
        return value
    if value.get(AUTOMATIC_PREFETCH_FRAMING_KEY) == AUTOMATIC_PREFETCH_FRAMING:
        value = {k: v for k, v in value.items() if k != AUTOMATIC_PREFETCH_FRAMING_KEY}
    items = value.get("items")
    if not isinstance(items, list):
        return value
    expanded: list[object] = []
    for item in items:
        if not isinstance(item, dict):
            expanded.append(item)
            continue
        item_constants = dict(_prefetch_item_constants(item, constants) if compact_stories else constants)
        rebuilt: dict[str, object] = {}
        for name in _AUTOMATIC_PREFETCH_ITEM_ORDER:
            if name in item:
                rebuilt[name] = item[name]
            elif name in item_constants:
                rebuilt[name] = item_constants[name]
        for name, entry in item.items():
            if name not in rebuilt:
                rebuilt[name] = entry
        expanded.append(rebuilt)
    return {**{k: v for k, v in value.items() if k != "items"}, "items": expanded}


def _present_duplicate_trigger_message(
    prepared: dict[str, object],
) -> dict[str, object]:
    """Replace the second copy of the trigger with a pointer to the first.

    ``inbound_wire`` writes the same ``trigger_message.model_dump`` into
    ``request.trigger_message`` and into the top-level
    ``current_trigger_message``.  Every reader of the trigger reads the frozen
    request object, not this JSON, so the duplicate is presentation only.
    """

    request = prepared.get("request")
    trigger = prepared.get("current_trigger_message")
    if not isinstance(request, dict) or "trigger_message" not in request:
        return prepared
    if request["trigger_message"] != trigger:
        return prepared
    rewritten = dict(request)
    rewritten["trigger_message"] = _TRIGGER_MESSAGE_POINTER
    return {**prepared, "request": rewritten}


# ---------------------------------------------------------------------------
# Mind renderer (lean presentation), 2026-10-22.
#
# Measured on a production stimulus-appraisal snapshot: 41% of its characters
# were opaque reference strings, ~13K characters were hex digests, and fewer
# than 5% were Chinese content.  A fact of 13 characters travelled inside 762.
# The ledger keeps every proof; the model needs the content, its time, and a
# short id it can cite.  This presentation keeps exactly that:
#
# * a ref the request lets her cite becomes its catalog id (s0, s1, ...) - the
#   same ids ``citeable_sources`` already lists and the host already restores;
# * a ref she cannot cite carries nothing for her and is omitted;
# * digests, revisions, carrier contracts and duplicate identity fields are
#   omitted; content, times, speakers, scopes and authority labels stay.
#
# It is used only for background cognition, only for snapshots compiled at a
# lean compiler version (persisted older snapshots keep their exact bytes),
# and only with the request's own citation catalog, so the sent view can be
# re-derived exactly from the snapshot plus the sent ``citeable_sources``.
# ---------------------------------------------------------------------------
LEAN_PRESENTATION_MARKER = "lean-mind.1"
_LEAN_DROP_KEYS = frozenset({
    "content_id", "content_ref", "descriptor_event_ref", "display_contract",
    "contract", "sequence", "source_entity_id", "source_entity_revision",
    "fact_context", "snapshot_hash", "snapshot_id", "updated_at",
})
_LEAN_DROP_SUFFIXES = ("_hash", "_world_revision", "_entity_revision")
_OPAQUE_REF = re.compile(r"[0-9a-f]{16,}")


def _lean_uncitable_ref(value: str) -> bool:
    return (
        ":" in value
        and not any(ch.isspace() for ch in value)
        and _OPAQUE_REF.search(value) is not None
        and not any("\u4e00" <= ch <= "\u9fff" for ch in value)
    )


_LEAN_OMIT = object()


def _lean_value(value: object, aliases: Mapping[str, str], *, aligned_tables: bool = False) -> object:
    if isinstance(value, str):
        if value in aliases:
            return aliases[value]
        return _LEAN_OMIT if _lean_uncitable_ref(value) else value
    if isinstance(value, list):
        kept = [_lean_value(item, aliases, aligned_tables=aligned_tables) for item in value]
        return [item for item in kept if item is not _LEAN_OMIT]
    if isinstance(value, dict):
        if aligned_tables and isinstance(value.get("columns"), list):
            table = _lean_table(value, aliases)
            if table is not None:
                return table
        own_ref = value.get("source_ref")
        result: dict[str, object] = {}
        for key, item in value.items():
            if key in _LEAN_DROP_KEYS or key.endswith(_LEAN_DROP_SUFFIXES):
                continue
            if key == "truncated" and item is False:
                continue
            if key != "source_ref" and isinstance(item, str) and item == own_ref:
                continue
            lean = (
                item
                if key == "speaker_ref" and isinstance(item, str)
                else _lean_value(item, aliases, aligned_tables=aligned_tables)
            )
            # Absence and emptiness read the same to her; omit both.
            if lean is _LEAN_OMIT or lean is None or lean == [] or lean == {}:
                continue
            result[key] = lean
        return result
    return value



def _lean_table(value: dict, aliases: Mapping[str, str]) -> dict | None:
    """Project column names and values together, including cache-split rows."""
    columns = value["columns"]
    if not columns or not all(isinstance(c, str) for c in columns) or len(set(columns)) != len(columns):
        return None
    groups = {key: value[key] for key in ("rows", "stable_rows") if key in value}
    if "volatile_last_row" in value:
        groups["volatile_last_row"] = [value["volatile_last_row"]]
    if not groups or any(not isinstance(rows, list) or any(
        not isinstance(row, list) or len(row) != len(columns) for row in rows
    ) for rows in groups.values()):
        return None
    records = {
        key: [_lean_value(dict(zip(columns, row, strict=True)), aliases, aligned_tables=True) for row in rows]
        for key, rows in groups.items()
    }
    kept = [column for column in columns if any(column in record for rows in records.values() for record in rows)]
    result = _lean_value({k: v for k, v in value.items() if k not in {*groups, "columns"}},
                         aliases, aligned_tables=True)
    result["columns"] = kept
    for key, rows in records.items():
        projected = [[record.get(column) for column in kept] for record in rows]
        result[key] = projected[0] if key == "volatile_last_row" else projected
    return result


def lean_present_snapshot(
    snapshot: Mapping[str, object], *, ref_aliases: Mapping[str, str],
) -> dict[str, object]:
    """Lean view of an already ordered snapshot presentation (see above)."""

    from .character_interior.life_context_presentation import ALIGNED_LEAN_TABLE_COMPILER_VERSIONS

    compiler = snapshot.get("snapshot_compiler")
    version = compiler.get("value") if isinstance(compiler, Mapping) else compiler
    aligned_tables = version in ALIGNED_LEAN_TABLE_COMPILER_VERSIONS
    result = dict(snapshot)
    materials = result.get("materials")
    if isinstance(materials, dict):
        result["materials"] = {
            key: _lean_value(value, ref_aliases, aligned_tables=aligned_tables) for key, value in materials.items()
        }
    inventory = result.get("source_inventory")
    if isinstance(inventory, list):
        lean_inventory = []
        for item in inventory:
            lean = _lean_value(item, ref_aliases, aligned_tables=aligned_tables)
            if isinstance(lean, dict) and lean.get("source_ref") in ref_aliases.values():
                lean_inventory.append(lean)
        result["source_inventory"] = lean_inventory
    refs = result.get("source_refs")
    if isinstance(refs, list):
        result["source_refs"] = [ref_aliases[ref] for ref in refs if ref in ref_aliases]
    for key in ("snapshot_hash", "snapshot_id"):
        result.pop(key, None)
    return result


def lean_aliases_from_citeable(view: object) -> dict[str, str] | None:
    """The request's own citation catalog as ref -> id, or None when absent."""

    if not isinstance(view, Mapping):
        return None
    items = view.get("items")
    if not isinstance(items, list):
        return None
    aliases: dict[str, str] = {}
    for item in items:
        if isinstance(item, Mapping) and isinstance(item.get("ref"), str) and isinstance(item.get("id"), str):
            aliases.setdefault(item["ref"], item["id"])
    return aliases or None


def lean_presentation_enabled(snapshot: Mapping[str, object]) -> bool:
    from .character_interior.life_context_presentation import LEAN_PRESENTATION_COMPILER_VERSIONS

    compiler = snapshot.get("snapshot_compiler")
    version = compiler.get("value") if isinstance(compiler, Mapping) else compiler
    return isinstance(version, str) and version in LEAN_PRESENTATION_COMPILER_VERSIONS


def present_inner_life(
    snapshot: dict[str, object], *, source_ref_aliases: object = None,
    lean_ref_aliases: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if lean_ref_aliases is not None and lean_presentation_enabled(snapshot):
        return lean_present_snapshot(
            present_inner_life(snapshot, source_ref_aliases=source_ref_aliases),
            ref_aliases=lean_ref_aliases,
        )
    materials = snapshot.get("materials")
    ordered_snapshot = dict(snapshot)
    if isinstance(materials, dict):
        ordered_materials: dict[str, object] = {}
        for key in _PRESENT_MATERIAL_ORDER:
            if key in materials:
                ordered_materials[key] = materials[key]
        for key, value in materials.items():
            if key not in ordered_materials:
                ordered_materials[key] = value
        if "recent_dialogue" in ordered_materials:
            ordered_materials["recent_dialogue"] = cache_stable_recent_dialogue(
                ordered_materials["recent_dialogue"],
                source_ref_aliases=source_ref_aliases,
            )
        if "appraisals" in ordered_materials:
            ordered_materials["appraisals"] = cache_stable_appraisals(
                ordered_materials["appraisals"]
            )
        if "affect" in ordered_materials:
            ordered_materials["affect"] = cache_stable_affect(ordered_materials["affect"])
        if "automatic_prefetch" in ordered_materials:
            ordered_materials["automatic_prefetch"] = _present_automatic_prefetch(
                ordered_materials["automatic_prefetch"], constants=_prefetch_constants(snapshot),
                compact_stories=_compact_story_prefetch(snapshot),
                lived_framing=lean_presentation_enabled(snapshot),
            )
        # ``production._bind_capability_evidence`` is the only writer and no
        # module in src/ reads it back (its refs are already carried by
        # ``source_refs`` and ``source_inventory``, which is where the visible
        # source closure looks).  It is a host audit trail, not a fact for her.
        ordered_materials.pop("capability_evidence", None)
        ordered_snapshot["materials"] = ordered_materials
    ordered_snapshot = _present_availability_wrappers(ordered_snapshot)
    ordered: dict[str, object] = {}
    volatile = set(_SNAPSHOT_VOLATILE_LAST)
    for key, value in ordered_snapshot.items():
        if key not in volatile:
            ordered[key] = value
    for key in _SNAPSHOT_VOLATILE_LAST:
        if key in ordered_snapshot:
            ordered[key] = ordered_snapshot[key]
    return ordered


def expand_present_world_context(view: Mapping[str, object]) -> dict[str, object]:
    """Rebuild the canonical presentation input from a presented World Context.

    The inverse of ``order_user_present_payload`` for everything that presenter
    elides: the withheld duplicate ``source_ref``, the aliases of the volatile
    dialogue turn, the availability wrappers of the scope/compiler bindings, the
    ``automatic_prefetch`` wrapper, typed Affect reference tables, and the trigger pointer. It reads the alias
    table from the same view, so it needs nothing but the view itself.

    ``recent_dialogue`` is returned in its canonical shape -- the flat
    chronological list ``fold_dialogue_entries`` produced -- because the
    frozen/volatile split is itself presentation.

    ``inner_life_snapshot.materials.capability_evidence`` is the one member that
    is removed rather than withheld, and it is not reconstructed here.
    """

    if not isinstance(view, Mapping):
        return {}
    expanded = copy.deepcopy(dict(view))
    request = expanded.get("request")
    if (
        isinstance(request, dict)
        and request.get("trigger_message") == _TRIGGER_MESSAGE_POINTER
        and "current_trigger_message" in expanded
    ):
        request["trigger_message"] = copy.deepcopy(expanded["current_trigger_message"])
    snapshot = expanded.get("inner_life_snapshot")
    if not isinstance(snapshot, dict):
        return expanded
    boundaries = expanded.get("expression_hard_boundaries")
    aliases = boundaries.get("source_ref_aliases") if isinstance(boundaries, Mapping) else None
    materials = snapshot.get("materials")
    if isinstance(materials, dict):
        affect = materials.get("affect")
        if isinstance(affect, list):
            materials["affect"] = [present_affect_entry(item, expand=True) for item in affect]
        elif isinstance(affect, dict):
            # Preserve the existing affect envelope; only the reference table
            # is new presentation. Its full records are required by readers.
            for key in ("stable_entries", "items"):
                if isinstance(affect.get(key), list):
                    affect[key] = [present_affect_entry(item, expand=True) for item in affect[key]]
            if isinstance(affect.get("volatile_last_entry"), dict):
                affect["volatile_last_entry"] = present_affect_entry(affect["volatile_last_entry"], expand=True)
        dialogue = materials.get("recent_dialogue")
        if isinstance(dialogue, dict):
            materials["recent_dialogue"] = recent_dialogue_material_entries(
                dialogue, source_ref_aliases=aliases
            )
        if "automatic_prefetch" in materials:
            materials["automatic_prefetch"] = _expand_automatic_prefetch(
                materials["automatic_prefetch"], constants=_prefetch_constants(snapshot),
                compact_stories=_compact_story_prefetch(snapshot),
            )
    expanded["inner_life_snapshot"] = _expand_availability_wrappers(snapshot)
    return expanded


def order_user_present_payload(material: dict[str, object]) -> dict[str, object]:
    prepared = dict(material)
    snapshot = prepared.get("inner_life_snapshot")
    if isinstance(snapshot, dict):
        boundaries = prepared.get("expression_hard_boundaries")
        prepared["inner_life_snapshot"] = present_inner_life(
            snapshot,
            source_ref_aliases=(
                boundaries.get("source_ref_aliases")
                if isinstance(boundaries, Mapping)
                else None
            ),
        )
    prepared = _present_duplicate_trigger_message(prepared)
    ordered: dict[str, object] = {}
    for key in _PRESENT_USER_KEY_ORDER:
        if key in prepared:
            ordered[key] = prepared[key]
    for key, value in prepared.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def ordered_mapping(value: Mapping[str, object], *, key_order: tuple[str, ...]) -> dict[str, object]:
    """Return a shallow-ordered dict for prefix-stable JSON serialization."""

    ordered: dict[str, object] = {}
    for key in key_order:
        if key in value:
            ordered[key] = value[key]
    for key, item in value.items():
        if key not in ordered:
            ordered[key] = item
    return ordered


def ordered_json_dumps(value: object, *, key_order: tuple[str, ...] | None = None) -> str:
    if isinstance(value, Mapping) and key_order is not None:
        value = ordered_mapping(value, key_order=key_order)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def present_hard_boundary_prompt(manifest: Mapping[str, object]) -> dict[str, object]:
    """Keep copyable tokens and their exact biography coordinates, without mechanism essays."""

    allowed = (
        "world_claim_source_refs",
        "source_ref_aliases",
        "companion_life_authority_availability",
    )
    stub: dict[str, object] = {
        "contract": "expression-hard-boundaries.present.2",
        "authority": "checked_after_expression",
    }
    for key in allowed:
        if key in manifest:
            stub[key] = manifest[key]
    if manifest.get("biographical_coordinate_authority"):
        stub["biographical_coordinate_authority"] = manifest["biographical_coordinate_authority"]
    return stub


def normalize_text_beats(value: dict[str, object]) -> dict[str, object]:
    if "beats" not in value:
        messages = value.get("messages")
        if (
            isinstance(messages, list)
            and messages
            and all(isinstance(item, str) and item.strip() for item in messages)
        ):
            value = {key: item for key, item in value.items() if key != "messages"}
            value["beats"] = [{"modality": "text", "text": item.strip()} for item in messages]
    beats = value.get("beats")
    if not isinstance(beats, list):
        return value
    normalized: list[object] = []
    changed = False
    for beat in beats:
        if isinstance(beat, str) and beat.strip():
            normalized.append({"modality": "text", "text": beat})
            changed = True
            continue
        if (
            isinstance(beat, dict)
            and set(beat) == {"text"}
            and isinstance(beat.get("text"), str)
            and beat["text"]
        ):
            normalized.append({"modality": "text", "text": beat["text"]})
            changed = True
            continue
        normalized.append(beat)
    if not changed:
        return value
    return {**value, "beats": normalized}


def json_schema_g4_metrics(schema: Mapping[str, object]) -> tuple[int, int, int]:
    required = schema.get("required")
    required_count = len(required) if isinstance(required, list) else 0
    properties = schema.get("properties")
    total = len(properties) if isinstance(properties, dict) else 0

    def depth(node: object) -> int:
        if not isinstance(node, dict):
            return 0
        nested = []
        props = node.get("properties")
        if isinstance(props, dict):
            nested.extend(props.values())
        items = node.get("items")
        if isinstance(items, dict):
            nested.append(items)
        one_of = node.get("oneOf")
        if isinstance(one_of, list):
            nested.extend(item for item in one_of if isinstance(item, dict))
        if not nested:
            return 1
        return 1 + max(depth(item) for item in nested)

    return required_count, total, depth(schema)


def identity_prose(frame: CompanionIdentityFrame) -> str:
    parts: list[str] = []
    if frame.base_prompt:
        parts.append(frame.base_prompt.strip())
    if frame.personality_frame:
        parts.append(frame.personality_frame.strip())
    if frame.appearance:
        parts.append("外貌：" + frame.appearance.strip())
    if frame.background:
        parts.append("成长背景：" + frame.background.strip())
    if frame.daily_life:
        parts.append(
            "习惯（不是此刻正在做的事）："
            + " ".join(item.strip() for item in frame.daily_life if item.strip())
        )
    if frame.speech_frame:
        parts.append("说话：" + frame.speech_frame.strip())
    if frame.style_rules:
        parts.append("写法：" + " ".join(item.strip() for item in frame.style_rules if item.strip()))
    if frame.speech_examples:
        # One example per line: dialogue-shaped examples (他：… ／ 她：…)
        # show turn rhythm, which a space-joined run would blur together.
        parts.append(
            "她说话的样子（示例，不是固定台词，也不是她的经历）：\n"
            + "\n".join("· " + item.strip() for item in frame.speech_examples if item.strip())
        )
    if frame.values:
        parts.append("价值：" + " ".join(item.strip() for item in frame.values if item.strip()))
    if frame.boundaries:
        parts.append("边界：" + " ".join(item.strip() for item in frame.boundaries if item.strip()))
    if frame.first_message:
        parts.append("初次开口：" + frame.first_message.strip())
    return "\n".join(parts)


SLIM_CONSIDER_KEYS = frozenset(
    {
        "messages",
        "meaning_of_this",
        "my_state",
        "stuck_with_me",
        "wants",
        "photo",
        "later",
        "waiting_for",
        "wait",
        "how_it_landed",
        "noticed",
        "keep_impression",
        "come_back",
        "come_back_in",
        "about_us",
        "why_us",
        "us_deltas",
        "we_are",
        "calling_it",
        "said_as",
        "matters_bp",
        "pressure_bp",
        "importance_bp",
        "declared_display",
        "affect",
        "episode_id",
        "components",
        "resolution_summary",
    }
)
_SLIM_CONSIDER_KEYS = SLIM_CONSIDER_KEYS
_SLIM_AFFECT_OPERATIONS = frozenset(
    {"no_change", "open", "update", "resolve", "supersede"}
)
_SLIM_AFFECT_DIMENSIONS = frozenset(
    {
        "hurt",
        "anger",
        "sadness",
        "loneliness",
        "anxiety",
        "resentment",
        "warmth",
        "joy",
    }
)
# Used only when she does not weigh the reading herself.  It sits below the
# reflection threshold on purpose: an unweighted reading should not schedule
# her to think about it again.
_SLIM_APPRAISAL_DEFAULT_CONFIDENCE_BP = 5_000
_SLIM_ORDINARY_STAGES = frozenset(
    {"acquaintance", "friend", "close_friend", "ambiguous", "lover"}
)
_SLIM_DECLARED_DISPLAY = frozenset(
    {"sexual_suggestive", "explicit_adult", "withdraw"}
)
_SLIM_ZERO_RELATIONSHIP_DELTAS = {
    "trust_bp": 0,
    "closeness_bp": 0,
    "respect_bp": 0,
    "reliability_bp": 0,
    "mutuality_bp": 0,
    "repair_confidence_bp": 0,
}
# The wire accepts the full signed range; the adjustment compiler owns the
# per-turn cap.  Anything outside this range is a malformed number rather than
# an ambitious one, so the whole object is dropped instead of being rescaled.
_SLIM_RELATIONSHIP_DELTA_LIMIT_BP = 10_000
_SLIM_ASSESSMENT_STATUSES = frozenset(
    {"fulfilled", "superseded", "still_pending", "uncertain"}
)
_SLIM_WAIT_FLOOR_SECONDS = 5
_SLIM_WAIT_MAX_SECONDS = 86_400
_SLIM_REVISIT_MAX_SECONDS = 604_800
_SLIM_REVISIT_EXPIRES_MAX_SECONDS = 691_200
# Same hard cap as ExpressionDraftCapabilities.max_beats / max_later_beats.
SLIM_REPLY_ONLY_MAX_TEXT_BEATS = 8
_SLIM_EXPECTATION_EXPIRES_MAX_SECONDS = 172_800
_SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS = 60
# H21: waiting_for without wait does not compile a hope. These sentinels remain
# only so leftover ledger records written by the 2026-08-18 optional-wait
# experiment can still be recognized as "not a seconds-declared wait".
SLIM_OPEN_HOPE_WAIT_SECONDS = _SLIM_WAIT_MAX_SECONDS
SLIM_OPEN_HOPE_EXPIRES_AFTER_SECONDS = _SLIM_EXPECTATION_EXPIRES_MAX_SECONDS
SLIM_OPEN_HOPE_CHASE_SECONDS = (
    SLIM_OPEN_HOPE_EXPIRES_AFTER_SECONDS - SLIM_OPEN_HOPE_WAIT_SECONDS
)
_WAIT_DURATION = re.compile(r"^(\d+)\s*([A-Za-z\u4e00-\u9fff]+)$")
_WAIT_UNIT_SECONDS = (
    (("秒钟", "秒", "seconds", "second", "secs", "sec", "s"), 1),
    (("minutes", "minute", "mins", "min", "分钟", "分", "m"), 60),
    (("hours", "hour", "hrs", "hr", "小时", "钟头", "h"), 3_600),
    (("days", "day", "天", "d"), 86_400),
)
def _clip_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text[:limit]


_LITERAL_MEDIA_PLACEHOLDERS = frozenset(
    {
        "[图片]",
        "[照片]",
        "[发图]",
        "[自拍]",
        "[image]",
        "[photo]",
        "［图片］",
        "［照片］",
        "［发图］",
        "［自拍］",
    }
)


_INLINE_STICKER_RE = re.compile(r"\[sticker:([^\[\]]+)\]")


def _expand_inline_sticker_text(
    text: str, *, allow_nontext: bool,
) -> list[dict[str, object]]:
    stripped = text.strip()
    if not stripped:
        return []
    if not allow_nontext or "[sticker:" not in stripped:
        return [{"modality": "text", "text": stripped}]
    beats: list[dict[str, object]] = []
    cursor = 0
    for match in _INLINE_STICKER_RE.finditer(stripped):
        before = stripped[cursor : match.start()].strip()
        if before:
            beats.append({"modality": "text", "text": before})
        sticker_id = match.group(1).strip()
        if sticker_id:
            beats.append({"modality": "sticker", "sticker_id": sticker_id})
        cursor = match.end()
    after = stripped[cursor:].strip()
    if after:
        beats.append({"modality": "text", "text": after})
    return beats or [{"modality": "text", "text": stripped}]


def _slim_beats(
    value: object, *, allow_nontext: bool = False,
) -> list[dict[str, object]] | None:
    if value is None:
        return []
    if isinstance(value, str):
        return _expand_inline_sticker_text(value, allow_nontext=allow_nontext)
    if not isinstance(value, list):
        return None
    messages: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if isinstance(item, str) and item.strip():
            messages.extend(_expand_inline_sticker_text(item, allow_nontext=allow_nontext))
            continue
        if (
            isinstance(item, dict)
            and set(item) == {"text"}
            and isinstance(item.get("text"), str)
            and item["text"].strip()
        ):
            messages.extend(
                _expand_inline_sticker_text(item["text"], allow_nontext=allow_nontext)
            )
            continue
        if (
            isinstance(item, dict)
            and set(item) == {"voice"}
            and isinstance(item.get("voice"), str)
            and item["voice"].strip()
        ):
            voice_text = item["voice"].strip()
            if not voice_text.startswith("[CQ:voice]"):
                voice_text = f"[CQ:voice]{voice_text}"
            messages.append({"modality": "text", "text": voice_text})
            continue
        if allow_nontext and isinstance(item, dict):
            if set(item) == {"typing"} and item["typing"] is True:
                messages.append({"modality": "typing"})
                continue
            for key, modality in (("sticker_id", "sticker"), ("reaction_id", "reaction")):
                if set(item) == {key} and isinstance(item[key], str) and item[key]:
                    # The chosen ID stays exact; the original capability and
                    # Acceptance checks reject absent or unavailable options.
                    messages.append({"modality": modality, key: item[key]})
                    break
            else:
                raise ValueError(
                    f"messages[{index}]只能是一条文字、{{sticker_id:已提供ID}}、"
                    "{reaction_id:已提供ID}或{typing:true}，不能混写字段"
                )
            continue
        if allow_nontext:
            raise ValueError(f"messages[{index}]不是有效的文字或表达选项对象")
        return None
    if (
        len(messages) > SLIM_REPLY_ONLY_MAX_TEXT_BEATS
        and any(item.get("modality") == "typing" for item in messages)
        and any(item.get("modality") != "typing" for item in messages)
    ):
        trimmed: list[dict[str, object]] = []
        remaining_over = len(messages) - SLIM_REPLY_ONLY_MAX_TEXT_BEATS
        for item in messages:
            if item.get("modality") == "typing" and remaining_over > 0:
                remaining_over -= 1
                continue
            trimmed.append(item)
        messages = trimmed
    return messages


def is_slim_consider_payload(value: Mapping[str, object]) -> bool:
    """True when she wrote the cheap ``messages`` object.

    A complete event envelope or dual-draft still belongs to those compilers.
    Extra sibling keys from those envelopes — most commonly ``appraisal_draft``
    riding next to ``messages`` and the private self-state — used to poison this detector,
    so a finished Chinese reply was discarded as if she had written nothing.
    """

    if "messages" not in value:
        return False
    if (
        value.get("protocol") == "character-interior-events.1"
        and isinstance(value.get("appraisal_draft"), dict)
        and isinstance(value.get("events"), list)
    ):
        return False
    if isinstance(value.get("expression_draft"), dict) and isinstance(
        value.get("appraisal_draft"), dict
    ):
        return False
    return True


def _clamp_declared_wait_seconds(
    value: int, *, max_seconds: int = _SLIM_WAIT_MAX_SECONDS
) -> int:
    return max(_SLIM_WAIT_FLOOR_SECONDS, min(max_seconds, value))


def _parse_declared_wait_seconds(
    raw: object, *, max_seconds: int = _SLIM_WAIT_MAX_SECONDS
) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return _clamp_declared_wait_seconds(raw, max_seconds=max_seconds)
    if isinstance(raw, float) and raw.is_integer():
        return _clamp_declared_wait_seconds(int(raw), max_seconds=max_seconds)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.isdigit():
        return _clamp_declared_wait_seconds(int(text), max_seconds=max_seconds)
    match = _WAIT_DURATION.fullmatch(text)
    if match is None:
        return None
    unit = match.group(2).lower()
    for labels, multiplier in _WAIT_UNIT_SECONDS:
        if unit in labels or unit in {item.lower() for item in labels}:
            return _clamp_declared_wait_seconds(
                int(match.group(1)) * multiplier, max_seconds=max_seconds
            )
    return None


def _slim_wait_horizon(value: Mapping[str, object]) -> tuple[int, int] | None:
    wait_seconds = _parse_declared_wait_seconds(value.get("wait"))
    if wait_seconds is None:
        return None
    expires_after_seconds = min(
        _SLIM_EXPECTATION_EXPIRES_MAX_SECONDS,
        wait_seconds + _SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS,
    )
    return wait_seconds, expires_after_seconds


def _slim_revisit_horizon(value: Mapping[str, object]) -> tuple[int, int] | None:
    wait_seconds = _parse_declared_wait_seconds(
        value.get("come_back_in"), max_seconds=_SLIM_REVISIT_MAX_SECONDS
    )
    if wait_seconds is None:
        return None
    expires_after_seconds = min(
        _SLIM_REVISIT_EXPIRES_MAX_SECONDS,
        wait_seconds + _SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS,
    )
    return wait_seconds, expires_after_seconds


def _slim_response_expectation(value: Mapping[str, object]) -> dict[str, object] | None:
    hoped = _clip_text(value.get("waiting_for"), 160)
    if not hoped:
        return None
    horizon = _slim_wait_horizon(value)
    if horizon is None:
        # H21: 没填 wait 不编译盼头（不定时叫醒）。
        return None
    wait_seconds, expires_after_seconds = horizon
    hoped = hoped[:128]
    if not hoped:
        return None
    pressure_bp = _slim_basis_points(value.get("pressure_bp"), allow_zero=True)
    importance_bp = _slim_basis_points(value.get("importance_bp"), allow_zero=True)
    if pressure_bp is None or importance_bp is None:
        return None
    return {
        "hoped_response": hoped,
        "pressure_bp": pressure_bp,
        "importance_bp": importance_bp,
        "wait_seconds": wait_seconds,
        "expires_after_seconds": expires_after_seconds,
    }


def compile_declared_response_expectation(
    value: Mapping[str, object],
) -> dict[str, object] | None:
    """Compile slim waiting_for+wait into a hope, or raise a visible pair error.

    waiting_for without wait is a legal omit: no hope, no timed wake.
    wait without waiting_for is a half-written pair and must not be eaten.
    """

    _raise_if_incomplete_wait_pair(value)
    _raise_if_incomplete_expectation_strength(value)
    return _slim_response_expectation(value)


def _slim_revisit(value: Mapping[str, object]) -> dict[str, object] | None:
    thought = _clip_text(value.get("come_back"), 160)
    if not thought:
        return None
    horizon = _slim_revisit_horizon(value)
    if horizon is None:
        return None
    wait_seconds, expires_after_seconds = horizon
    return {
        "thought": thought,
        "wait_seconds": wait_seconds,
        "expires_after_seconds": expires_after_seconds,
    }


def _slim_response_expectation_assessment(
    value: Mapping[str, object],
    *,
    reason: str,
) -> dict[str, object] | None:
    status = value.get("how_it_landed")
    if not isinstance(status, str) or status not in _SLIM_ASSESSMENT_STATUSES:
        return None
    clipped = reason.strip()[:240]
    if not clipped:
        return None
    return {"status": status, "reason": clipped}


def _parse_declared_later_seconds(raw: object) -> int | None:
    parsed = _parse_declared_wait_seconds(raw)
    if parsed is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw != parsed:
        return None
    if isinstance(raw, float) and raw.is_integer() and int(raw) != parsed:
        return None
    return parsed


def _slim_later_horizon(value: Mapping[str, object]) -> tuple[int, int] | None:
    delay_seconds = _parse_declared_later_seconds(value.get("later"))
    if delay_seconds is None:
        return None
    expires_after_seconds = min(
        _SLIM_EXPECTATION_EXPIRES_MAX_SECONDS,
        delay_seconds + _SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS,
    )
    if expires_after_seconds <= delay_seconds:
        return None
    return delay_seconds, expires_after_seconds


SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE = (
    "关系增量要留下，us_deltas 必须和 about_us、why_us 一起写"
)
SLIM_RELATIONSHIP_DELTAS_UNREADABLE = "us_deltas 必须是六轴上的带符号整数"
SLIM_WAIT_PAIR_INCOMPLETE = "wait 只能和 waiting_for 一起写"
SLIM_WAIT_NOT_A_DURATION = "wait 必须是 5 到 86400 的整数秒"
SLIM_COME_BACK_PAIR_INCOMPLETE = "come_back_in 只能和 come_back 一起写"
SLIM_COME_BACK_IN_NOT_A_DURATION = "come_back_in 必须是 5 到 86400 的整数秒"
SLIM_COMMITMENT_TRIPLET_INCOMPLETE = "we_are、calling_it、said_as 三个要一起写"
SLIM_COMMITMENT_WE_ARE_INVALID = (
    "we_are 只能是 acquaintance、friend、close_friend、ambiguous 或 lover"
)
SLIM_COMMITMENT_REQUIRES_SPEECH = (
    "we_are 只能跟这一轮真的说出去的话一起写，沉默这一轮不要写 we_are"
)
SLIM_DECLARED_DISPLAY_INVALID = (
    "declared_display 只能是 sexual_suggestive、explicit_adult 或 withdraw"
)
SLIM_LATER_NOT_A_DURATION = "later 必须是 5 到 86400 的整数秒"
SLIM_LATER_REQUIRES_TEXT = "later 只能和非空 messages 一起写，不能和沉默或 photo 一起用"
SLIM_HOW_IT_LANDED_INVALID = (
    "how_it_landed 只能是 fulfilled、superseded、still_pending 或 uncertain"
)
SLIM_SELF_STATE_REQUIRED = "slim 必须分别写 meaning_of_this 和 my_state"
SLIM_AFFECT_TARGET_REQUIRED = (
    "持续情绪必须用 affect 和 components 明确写出每个 target_intensity_bp"
)
SLIM_EXPECTATION_STRENGTH_REQUIRED = (
    "盼头必须由你同时写 pressure_bp 和 importance_bp"
)


def _slim_field_attempted(value: Mapping[str, object], key: str) -> bool:
    """True when she wrote a key as a real attempt. JSON null is omission."""

    if key not in value:
        return False
    raw = value[key]
    if raw is None:
        return False
    if isinstance(raw, str) and not raw.strip():
        return False
    if isinstance(raw, Mapping) and not raw:
        return False
    if isinstance(raw, list) and not raw:
        return False
    return True


def _slim_basis_points(value: object, *, allow_zero: bool) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    minimum = 0 if allow_zero else 1
    return value if minimum <= value <= 10_000 else None


def _raise_if_invalid_affect_choice(value: Mapping[str, object]) -> None:
    if _slim_field_attempted(value, "mood"):
        raise ValueError(
            SLIM_AFFECT_TARGET_REQUIRED
            + "。mood 简写不再被接受，因为宿主不能替你把强度填成 5000。"
            "不想留下持续情绪就省略 affect；想留下就自己写 operation、dimension 和 target_intensity_bp。"
        )
    affect = value.get("affect")
    operation = affect.strip().lower() if isinstance(affect, str) else None
    if operation is None and not _slim_field_attempted(value, "components"):
        return
    if operation not in _SLIM_AFFECT_OPERATIONS:
        raise ValueError(SLIM_AFFECT_TARGET_REQUIRED + "。affect operation 读不出来。")
    if operation == "open" and _slim_field_attempted(value, "episode_id"):
        raise ValueError(
            SLIM_AFFECT_TARGET_REQUIRED
            + "。affect=open 是新起一段持续情绪，不能写 episode_id；若要更新已有持续情绪请用 affect=update 并写顶层 episode_id。"
        )
    if operation == "resolve" and not _slim_field_attempted(
        value, "resolution_summary"
    ):
        raise ValueError(
            SLIM_AFFECT_TARGET_REQUIRED
            + "。affect=resolve 必须同时填写非空 resolution_summary。"
        )
    if operation in {"open", "update", "supersede"}:
        components = value.get("components")
        if not isinstance(components, list) or not components:
            raise ValueError(SLIM_AFFECT_TARGET_REQUIRED + "。这次缺少 components。")
        for component in components:
            if not isinstance(component, Mapping):
                raise ValueError(SLIM_AFFECT_TARGET_REQUIRED + "。components 必须是对象数组。")
            if _slim_affect_dimension(component.get("dimension")) is None:
                raise ValueError(SLIM_AFFECT_TARGET_REQUIRED + "。dimension 不在可用维度里。")
            if _slim_basis_points(
                component.get("target_intensity_bp"), allow_zero=False
            ) is None:
                raise ValueError(
                    SLIM_AFFECT_TARGET_REQUIRED
                    + "。每个 component 都必须有 1 到 10000 的 target_intensity_bp。"
                )


def _raise_if_incomplete_expectation_strength(value: Mapping[str, object]) -> None:
    hoped = _clip_text(value.get("waiting_for"), 160)
    horizon = _slim_wait_horizon(value)
    pressure_attempted = _slim_field_attempted(value, "pressure_bp")
    importance_attempted = _slim_field_attempted(value, "importance_bp")
    if not hoped or horizon is None:
        if pressure_attempted or importance_attempted:
            raise ValueError(
                SLIM_EXPECTATION_STRENGTH_REQUIRED
                + "，并和 waiting_for、wait 一起写；否则全部省略。"
            )
        return
    missing: list[str] = []
    if _slim_basis_points(value.get("pressure_bp"), allow_zero=True) is None:
        missing.append("pressure_bp")
    if _slim_basis_points(value.get("importance_bp"), allow_zero=True) is None:
        missing.append("importance_bp")
    if missing:
        raise ValueError(
            SLIM_EXPECTATION_STRENGTH_REQUIRED
            + "。这次缺少或写坏了："
            + "、".join(missing)
            + "。宿主不会填 5000，也不会覆盖你写的值。"
        )


def _raise_incomplete_pair(
    *,
    stem: str,
    missing: list[str],
) -> None:
    raise ValueError(
        stem
        + "。这次缺了："
        + "、".join(missing)
        + "。宿主不会替你补上缺的字段。"
        "两个一起写才会在那个秒数叫醒你；只写一半不会生效，也不会被默默丢掉。"
        "两个都不写也可以，那就是这一轮没有人叫你。"
    )


def _raise_if_incomplete_wait_pair(value: Mapping[str, object]) -> None:
    """Refuse a half-written wait instead of silently eating waiting_for or wait."""

    hoped = (
        _clip_text(value.get("waiting_for"), 160)
        if _slim_field_attempted(value, "waiting_for")
        else ""
    )
    wait_attempted = _slim_field_attempted(value, "wait")
    parsed = _parse_declared_wait_seconds(value.get("wait")) if wait_attempted else None
    if not hoped and not wait_attempted:
        return
    if wait_attempted and parsed is None:
        raise ValueError(
            SLIM_WAIT_NOT_A_DURATION
            + "。这次写的 wait 读不成秒数。宿主不会替你编一个秒数，"
            "也不会把写坏的 wait 丢掉后假装没写。"
            "两个都写对了才会叫醒你；两个都不写也可以。"
        )
    if wait_attempted and not hoped:
        _raise_incomplete_pair(stem=SLIM_WAIT_PAIR_INCOMPLETE, missing=["waiting_for"])


def _raise_if_incomplete_come_back_pair(value: Mapping[str, object]) -> None:
    """Refuse a half-written come_back instead of silently eating the leftover."""

    thought = (
        _clip_text(value.get("come_back"), 160)
        if _slim_field_attempted(value, "come_back")
        else ""
    )
    time_attempted = _slim_field_attempted(value, "come_back_in")
    parsed = (
        _parse_declared_wait_seconds(
            value.get("come_back_in"), max_seconds=_SLIM_REVISIT_MAX_SECONDS
        )
        if time_attempted
        else None
    )
    if not thought and not time_attempted:
        return
    if time_attempted and parsed is None:
        raise ValueError(
            SLIM_COME_BACK_IN_NOT_A_DURATION
            + "。这次写的 come_back_in 读不成秒数。宿主不会替你编一个秒数，"
            "也不会把写坏的 come_back_in 丢掉后假装没写。"
            "两个都写对了才会为这件事叫你；两个都不写也可以。"
        )
    missing = [
        name
        for name, present in (
            ("come_back", thought),
            ("come_back_in", time_attempted and parsed is not None),
        )
        if not present
    ]
    if missing:
        _raise_incomplete_pair(stem=SLIM_COME_BACK_PAIR_INCOMPLETE, missing=missing)


def _raise_if_incomplete_relationship_residue(value: Mapping[str, object]) -> None:
    """Refuse a half-written relationship residue instead of silently eating it.

    about_us + why_us without us_deltas still records a reading with zero
    movement.  us_deltas, or exactly one of the prose fields, used to compile
    and then vanish.  That is a visible failure: the host never invents the
    missing field.
    """

    about_us = _clip_text(value.get("about_us"), 128)
    why_us = _clip_text(value.get("why_us"), 128)
    wrote_deltas = slim_relationship_deltas(value.get("us_deltas")) is not None
    if _slim_field_attempted(value, "us_deltas") and not wrote_deltas:
        raise ValueError(
            SLIM_RELATIONSHIP_DELTAS_UNREADABLE
            + "。这次写的 us_deltas 读不成六轴增量。宿主不会替你编一个数，"
            "也不会把写坏的增量丢掉后假装没写。"
            "要留下增量就写成带符号整数的对象，并和 about_us、why_us 一起写；"
            "三个都不写也可以。"
        )
    if (bool(about_us) == bool(why_us)) and (not wrote_deltas or (about_us and why_us)):
        return
    missing = [
        name
        for name, present in (("about_us", about_us), ("why_us", why_us))
        if not present
    ]
    raise ValueError(
        SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE
        + "。这次缺了："
        + "、".join(missing)
        + "。宿主不会替你补上缺的字段。"
        "三个一起写才会变成信号；只写一半不会生效，也不会被默默丢掉。"
        "三个都不写也可以，那就是这一轮关系没有动。"
    )


def _raise_if_incomplete_commitment_triplet(value: Mapping[str, object]) -> None:
    """Refuse a half-written we_are / calling_it / said_as instead of dropping it."""

    we_are_attempted = _slim_field_attempted(value, "we_are")
    calling_attempted = _slim_field_attempted(value, "calling_it")
    said_attempted = _slim_field_attempted(value, "said_as")
    if not we_are_attempted and not calling_attempted and not said_attempted:
        return
    we_are = value.get("we_are") if we_are_attempted else None
    valid_stage = we_are in _SLIM_ORDINARY_STAGES
    if we_are_attempted and not valid_stage:
        raise ValueError(
            SLIM_COMMITMENT_WE_ARE_INVALID
            + "。这次写的 we_are 不是其中之一。宿主不会替你改成一个合法阶段，"
            "也不会把写坏的承诺丢掉后假装没写。"
            "三个都写对了才会留下这条路径；三个都不写也可以。"
        )
    calling_it = (
        _clip_text(value.get("calling_it"), 128) if calling_attempted else ""
    )
    said_as = _clip_text(value.get("said_as"), 512) if said_attempted else ""
    missing = [
        name
        for name, present in (
            ("we_are", valid_stage),
            ("calling_it", calling_it),
            ("said_as", said_as),
        )
        if not present
    ]
    if missing:
        raise ValueError(
            SLIM_COMMITMENT_TRIPLET_INCOMPLETE
            + "。这次缺了："
            + "、".join(missing)
            + "。宿主不会替你补上缺的字段。"
            "三个一起写才会留下这条路径；只写一半不会生效，也不会被默默丢掉。"
            "三个都不写也可以，那就是这一轮没有声明你们是什么关系。"
        )


def _raise_if_invalid_declared_display(value: Mapping[str, object]) -> None:
    """Refuse a written-but-unreadable display intent instead of eating it."""

    if not _slim_field_attempted(value, "declared_display"):
        return
    if _slim_declared_display(value.get("declared_display")) is None:
        raise ValueError(
            SLIM_DECLARED_DISPLAY_INVALID
            + "。省略或 null 也可以，那是这一轮不声明、也不改之前的声明。"
            "这次写的 declared_display 不是其中之一。宿主不会替你改成一个合法值，"
            "也不会把写坏的声明丢掉后假装没写。"
        )


def _raise_if_invalid_later(
    value: Mapping[str, object],
    *,
    messages: list[dict[str, object]],
    media_request: str,
) -> tuple[int, int] | None:
    """Refuse a written-but-unreadable later instead of sending now or dropping."""

    if not _slim_field_attempted(value, "later"):
        return None
    horizon = _slim_later_horizon(value)
    if horizon is None:
        raise ValueError(
            SLIM_LATER_NOT_A_DURATION
            + "。这次写的 later 读不成秒数。宿主不会替你编一个秒数，"
            "也不会把写坏的 later 当成现在发，更不会丢掉后假装没写。"
            "不写 later 也可以，那就是现在发。"
        )
    if (not messages or media_request != "none"
        or any(item["modality"] != "text" for item in messages)):
        raise ValueError(
            SLIM_LATER_REQUIRES_TEXT
            + "。later 只能配已经想好的文字气泡；沉默和 photo 都不能跟 later 一起写。"
            "宿主不会替你改成现在发，也不会把写坏的 later 丢掉后假装没写。"
        )
    return horizon


def _raise_if_invalid_how_it_landed(value: Mapping[str, object]) -> None:
    """Refuse a written-but-unreadable landing status instead of eating it."""

    if not _slim_field_attempted(value, "how_it_landed"):
        return
    status = value.get("how_it_landed")
    if not isinstance(status, str) or status not in _SLIM_ASSESSMENT_STATUSES:
        raise ValueError(
            SLIM_HOW_IT_LANDED_INVALID
            + "。省略或 null 也可以，那是这一轮不评估上次的盼头。"
            "这次写的 how_it_landed 不是其中之一。宿主不会替你改成一个合法值，"
            "也不会把写坏的评估丢掉后假装没写。"
        )


def _short_clause_label(text: str, limit: int = 64) -> str:
    """Keep short text as-is; clip longer text at a natural clause boundary."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for sep in ("。", "；", "！", "？", "——"):
        idx = window.find(sep)
        if 4 <= idx:
            return window[:idx]
    comma_idx = window.rfind("，")
    if comma_idx >= 8:
        return window[:comma_idx]
    return window


def compile_slim_consider_payload(
    value: Mapping[str, object],
    *,
    allow_nontext: bool = False,
) -> dict[str, object] | None:
    """Compile the slim consider shape into dual drafts. Old drafts stay as-is."""

    if not is_slim_consider_payload(value):
        return None
    raw_messages = value.get("messages")
    if isinstance(raw_messages, list):
        top_keys = SLIM_CONSIDER_KEYS - {"messages", "my_state"}
        beat_keys = {"sticker_id", "reaction_id", "typing", "voice"}
        cleaned_messages: list[object] = []
        hoisted_fields: dict[str, object] = {}
        changed_messages = False
        for item in raw_messages:
            if isinstance(item, Mapping) and len(item) >= 1:
                item_keys = set(item)
                if item_keys <= top_keys:
                    changed_messages = True
                    for k, v in item.items():
                        if k not in value and k not in hoisted_fields:
                            hoisted_fields[k] = v
                    continue
                matched_beat = item_keys & beat_keys
                extra_keys = item_keys - beat_keys
                if len(matched_beat) == 1 and extra_keys and extra_keys <= top_keys:
                    changed_messages = True
                    b_key = next(iter(matched_beat))
                    cleaned_messages.append({b_key: item[b_key]})
                    for k in extra_keys:
                        if k not in value and k not in hoisted_fields:
                            hoisted_fields[k] = item[k]
                    continue
            cleaned_messages.append(item)
        if changed_messages:
            value = {**value, **hoisted_fields, "messages": cleaned_messages}
    wait = value.get("wait")
    if isinstance(wait, Mapping):
        # The role supplied the complete choice in one object. This is only
        # transport normalization; ambiguous, missing or malformed choices
        # still fail instead of being guessed, dropped or rescaled.
        if set(wait) != {"after_seconds", "pressure_bp", "importance_bp"}:
            raise ValueError("wait对象必须且只能包含after_seconds、pressure_bp、importance_bp")
        seconds = wait["after_seconds"]
        if type(seconds) is not int or not 5 <= seconds <= 86400:
            raise ValueError("wait.after_seconds必须是5到86400整数秒")
        for key in ("pressure_bp", "importance_bp"):
            if type(wait[key]) is not int or not 0 <= wait[key] <= 10000:
                raise ValueError(f"wait.{key}必须是0到10000整数基点")
            if key in value and (type(value[key]) is not int or value[key] != wait[key]):
                raise ValueError(f"wait.{key}与顶层{key}冲突")
        value = {**value, "wait": seconds, "pressure_bp": wait["pressure_bp"],
                 "importance_bp": wait["importance_bp"]}
    # Current carriers keep the topic and duration in one authored object.
    # Legacy pairs still replay exactly. No missing choice is inferred.
    revisit = value.get("come_back")
    if revisit is not None and not isinstance(revisit, (str, Mapping)):
        raise ValueError("come_back必须是完整对象；旧字符串格式必须配come_back_in，不需要时省略或null")
    if isinstance(revisit, Mapping):
        if set(revisit) != {"thought", "after_seconds"} or "come_back_in" in value:
            raise ValueError("come_back对象必须且只能包含thought和after_seconds，不能混写come_back_in")
        thought, seconds = revisit["thought"], revisit["after_seconds"]
        if not isinstance(thought, str) or not thought.strip() or len(thought) > 160:
            raise ValueError("come_back.thought必须是1到160字符的自选主题")
        if type(seconds) is not int or not 5 <= seconds <= _SLIM_REVISIT_MAX_SECONDS:
            raise ValueError("come_back.after_seconds必须是5到604800整数秒")
        value = {**value, "come_back": thought, "come_back_in": seconds}
    raw_affect = value.get("affect")
    if isinstance(raw_affect, Mapping):
        nested_op = raw_affect.get("affect") or raw_affect.get("operation")
        if not isinstance(nested_op, str):
            bool_ops = [
                op for op in _SLIM_AFFECT_OPERATIONS if raw_affect.get(op) is True
            ]
            if len(bool_ops) == 1:
                nested_op = bool_ops[0]
        if isinstance(nested_op, str):
            unpacked_affect: dict[str, object] = {**value, "affect": nested_op}
            for k in ("episode_id", "components", "resolution_summary"):
                if k in raw_affect and k not in value:
                    unpacked_affect[k] = raw_affect[k]
            value = unpacked_affect
    validate_authored_impression_retention(value)
    messages = _slim_beats(value.get("messages"), allow_nontext=allow_nontext)
    if messages is None:
        return None
    meaning_of_this = _clip_text(value.get("meaning_of_this"), 240)
    my_state = _clip_text(value.get("my_state"), 480)
    if not my_state:
        raise ValueError(
            SLIM_SELF_STATE_REQUIRED
            + "。my_state 只写你自己此刻的感觉、欲望、抵触、注意或平静；"
            "meaning_of_this 只在你有对对方或处境的特定理解时填写。宿主不会用 messages、stuck_with_me 或同一段 felt 替你补。"
        )
    if meaning_of_this.strip() == my_state.strip():
        meaning_of_this = ""
    label = _short_clause_label(my_state, 64)
    rationale = (meaning_of_this or my_state)[:240]
    wants = _clip_text(value.get("wants"), 240)
    photo = value.get("photo")
    media_request = "none"
    media_source_refs: list[str] = []
    if photo is True:
        media_request = "consider_available_candidate"
    elif isinstance(photo, str) and photo.strip() and photo.strip() not in {"none", "false"}:
        media_request = "consider_available_candidate"
        if photo.strip() not in {"true", "consider_available_candidate"}:
            media_source_refs = [photo.strip()]
    non_placeholder_messages = [
        item
        for item in messages
        if not (
            item.get("modality") == "text"
            and str(item.get("text", "")).strip().lower() in _LITERAL_MEDIA_PLACEHOLDERS
        )
    ]
    if len(non_placeholder_messages) < len(messages):
        if media_request != "none" and non_placeholder_messages:
            messages = non_placeholder_messages
        else:
            raise ValueError(
                "messages里不能写'[图片]'或'[照片]'文字占位符；"
                "若要发照片请设photo=true并在messages里写随图附带的一句自然台词，若不发照片请直接写台词"
            )
    if media_request != "none" and not messages:
        return None
    delay_seconds: int | None = None
    expires_after_seconds: int | None = None
    later_horizon = _raise_if_invalid_later(
        value, messages=messages, media_request=media_request
    )
    if later_horizon is not None:
        delay_seconds, expires_after_seconds = later_horizon
        timing = "later"
    else:
        timing = "now" if messages else "silent"
    private_turn_state: dict[str, object] = {
        "contract": "private-turn-state.1",
        "inner_state_summary": my_state,
        "attended_source_refs": [],
    }
    stuck_with_me = _clip_text(value.get("stuck_with_me"), 1_200)
    if stuck_with_me:
        private_turn_state["stuck_with_me"] = stuck_with_me
    noticed = _clip_text(value.get("noticed"), 720)
    if noticed:
        private_turn_state["noticed"] = noticed
    keep_impression = value.get("keep_impression")
    if keep_impression is True:
        private_turn_state["keep_impression"] = True
    elif keep_impression is False:
        private_turn_state["keep_impression"] = False
    _raise_if_incomplete_wait_pair(value)
    _raise_if_incomplete_expectation_strength(value)
    _raise_if_incomplete_come_back_pair(value)
    _raise_if_incomplete_relationship_residue(value)
    _raise_if_incomplete_commitment_triplet(value)
    _raise_if_invalid_declared_display(value)
    _raise_if_invalid_how_it_landed(value)
    _raise_if_invalid_affect_choice(value)
    about_us = _clip_text(value.get("about_us"), 128)
    why_us = _clip_text(value.get("why_us"), 128)
    if about_us and why_us:
        private_turn_state["about_us"] = about_us
        private_turn_state["why_us"] = why_us
    we_are = value.get("we_are")
    calling_it = _clip_text(value.get("calling_it"), 128)
    said_as = _clip_text(value.get("said_as"), 512)
    if we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as:
        private_turn_state["we_are"] = we_are
        private_turn_state["calling_it"] = calling_it
        private_turn_state["said_as"] = said_as
    declared_display = _slim_declared_display(value.get("declared_display"))
    if declared_display is not None:
        private_turn_state["declared_display"] = declared_display
    claims = value.get("world_claims")
    if isinstance(claims, list):
        normalized_claims: list[object] = []
        for claim in claims:
            if isinstance(claim, dict):
                c_copy = dict(claim)
                if "source_refs" not in c_copy and "source_ref" in c_copy:
                    raw_sr = c_copy.pop("source_ref")
                    if isinstance(raw_sr, str) and raw_sr.strip():
                        c_copy["source_refs"] = [raw_sr.strip()]
                    elif isinstance(raw_sr, list):
                        c_copy["source_refs"] = raw_sr
                elif isinstance(c_copy.get("source_refs"), str) and c_copy["source_refs"].strip():
                    c_copy["source_refs"] = [c_copy["source_refs"].strip()]
                if isinstance(c_copy.get("source_refs"), list) and all(
                    isinstance(ref, str) for ref in c_copy["source_refs"]
                ):
                    c_copy["source_refs"] = list(dict.fromkeys(c_copy["source_refs"]))
                if (
                    not (isinstance(c_copy.get("scope"), str) and c_copy["scope"].strip())
                    and isinstance(c_copy.get("source_refs"), list)
                    and c_copy["source_refs"]
                ):
                    c_copy["scope"] = "past_world"
                normalized_claims.append(c_copy)
            else:
                normalized_claims.append(claim)
        claims = normalized_claims
    expression: dict[str, object] = {
        "private_turn_state": private_turn_state,
        "timing_choice": timing,
        "cadence": "conversational",
        "beats": messages,
        "stance": label,
        "brief_rationale": rationale,
        "confidence": 5000,
        # Preserve explicit declarations for the ordinary source validator.
        # A compact carrier must not turn a sourced or invalid authored claim
        # into an apparently claim-free message. Absence keeps the old shape.
        "world_claims": claims if claims is not None else [],
        "media_request": media_request,
        "media_source_refs": media_source_refs,
    }
    if wants:
        expression["impulse_summary"] = wants
    raw_posture = value.get("turn_posture")
    if isinstance(raw_posture, str) and raw_posture in {"yield", "continue", "interject", "supersede"}:
        expression["turn_posture"] = raw_posture
    if timing == "later":
        assert delay_seconds is not None and expires_after_seconds is not None
        expression["delay_seconds"] = delay_seconds
        expression["expires_after_seconds"] = expires_after_seconds
    if messages:
        expectation = _slim_response_expectation(value)
        if expectation is not None:
            expression["response_expectation"] = expectation
        leftover = None if timing == "later" else _slim_revisit(value)
        if leftover is not None:
            expression["revisit"] = leftover
    assessment = _slim_response_expectation_assessment(value, reason=my_state)
    if assessment is not None:
        expression["response_expectation_assessment"] = assessment
    has_relationship_residue = bool(
        (about_us and why_us)
        or (we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as)
    )
    compiled = {
        "appraisal_draft": _slim_appraisal_draft(
            meaning_of_this=meaning_of_this,
            my_state=my_state,
            label=label,
            affect=value.get("affect"),
            episode_id=value.get("episode_id"),
            components=value.get("components"),
            resolution_summary=value.get("resolution_summary"),
            matters_bp=value.get("matters_bp"),
            keep_impression=keep_impression is True,
            stuck_with_me=stuck_with_me,
            has_relationship_residue=has_relationship_residue,
        ),
        "expression_draft": expression,

    }
    for field in ("life_intent", "plan_response"):
        if value.get(field) is not None:
            compiled["appraisal_draft"][field] = value[field]
    return compiled


def _slim_declared_display(value: object) -> str | None:
    """Read her display intent; the host never invents one from prose.

    A string enum is the documented shape.  An object with media_intent or
    explicit declare/withdraw fields is accepted so a nested write is not
    dropped, but recipient_ref is ignored: the inbound observation actor is
    bound later, never a model-authored ref.
    """

    if isinstance(value, str):
        intent = value.strip()
        return intent if intent in _SLIM_DECLARED_DISPLAY else None
    if isinstance(value, Mapping):
        intent = value.get("media_intent")
        if isinstance(intent, str) and intent.strip() in _SLIM_DECLARED_DISPLAY:
            return intent.strip()
        raw_intent = value.get("intent")
        if raw_intent == "withdraw":
            return "withdraw"
        if raw_intent == "declare":
            level = value.get("level")
            if isinstance(level, str) and level.strip() in {"sexual_suggestive", "explicit_adult"}:
                return level.strip()
    return None


def _slim_matters_bp(value: object) -> int:
    """Her own weight on this reading; it decides whether it comes back to her.

    An omitted or malformed number stays at the middling default rather than
    being guessed from wording, so the host never decides that something
    mattered more (or less) to her than she said.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        return _SLIM_APPRAISAL_DEFAULT_CONFIDENCE_BP
    if not 1 <= value <= 10_000:
        return _SLIM_APPRAISAL_DEFAULT_CONFIDENCE_BP
    return value


def _slim_affect_dimension(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    dimension = value.strip().lower()
    if dimension not in _SLIM_AFFECT_DIMENSIONS:
        return None
    return dimension


def _slim_appraisal_draft(
    *,
    meaning_of_this: str,
    my_state: str,
    label: str,
    affect: object = None,
    episode_id: object = None,
    components: object = None,
    resolution_summary: object = None,
    matters_bp: object = None,
    keep_impression: bool = False,
    stuck_with_me: str = "",
    has_relationship_residue: bool = False,
) -> dict[str, object]:
    """Keep counterpart appraisal and present self-state as distinct role texts."""

    weight = _slim_matters_bp(matters_bp)
    affect_operation = (
        affect.strip().lower()
        if isinstance(affect, str) and affect.strip().lower() in _SLIM_AFFECT_OPERATIONS
        else None
    )
    common: dict[str, object] = {
        "affect": "no_change",
        "brief_rationale": (meaning_of_this or my_state)[:240],
        "behavior_tendency": label,
        "stance": label,
        "display_strategy": label,
        "confidence": weight,
    }
    if affect_operation is not None and affect_operation != "no_change":
        common["affect"] = affect_operation
        if isinstance(episode_id, str) and episode_id.strip():
            common["episode_id"] = episode_id.strip()
        if isinstance(resolution_summary, str) and resolution_summary.strip():
            common["resolution_summary"] = resolution_summary.strip()[:240]
        if isinstance(components, list) and components:
            common["components"] = [
                {
                    k: v
                    for k, v in c.items()
                    if k in {"component_id", "dimension", "target_intensity_bp"}
                    and (k != "component_id" or (isinstance(v, str) and v.strip()))
                }
                if isinstance(c, dict)
                else c
                for c in components
            ]
    meaning = _clip_text(meaning_of_this, 128).rstrip()
    if not meaning and keep_impression and stuck_with_me.strip():
        meaning = _clip_text(stuck_with_me, 128).rstrip()
    if not meaning and common["affect"] != "no_change":
        if isinstance(resolution_summary, str) and resolution_summary.strip():
            meaning = _clip_text(resolution_summary, 128).rstrip()
        else:
            meaning = _clip_text(my_state, 128).rstrip() or "affect"
    if not meaning and (
        keep_impression
        or weight > _SLIM_APPRAISAL_DEFAULT_CONFIDENCE_BP
        or has_relationship_residue
    ):
        # When she weighed this turn above the unweighted default (matters_bp > 5000),
        # asked to keep a private impression, or authored a relationship shift,
        # her full inner state anchors the appraisal so short-term residue,
        # background life_reflection, and private_impression_reflection receive it.
        meaning = _clip_text(my_state, 128).rstrip()
    if not meaning:
        return {"appraise": False, **common}

    return {
        "appraise": True,
        **common,
        "meanings": [{"meaning": meaning, "confidence": weight}],
        "attribution": "unknown",
        "severity": 5000,
    }


def slim_relationship_deltas(value: object) -> dict[str, int] | None:
    """Read her own signed movement on the six axes; omitted axes stay at zero.

    The host never derives movement from prose, so an absent or malformed
    object means the relationship did not move this turn.
    """

    if not isinstance(value, Mapping) or not value:
        return None
    if set(value) - set(_SLIM_ZERO_RELATIONSHIP_DELTAS):
        return None
    deltas = dict(_SLIM_ZERO_RELATIONSHIP_DELTAS)
    for axis, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None
        if abs(raw) > _SLIM_RELATIONSHIP_DELTA_LIMIT_BP:
            return None
        deltas[axis] = raw
    if not any(deltas.values()):
        return None
    return deltas


def attach_hitchhiked_relationship_residue(
    value: Mapping[str, object],
    *,
    authored: Mapping[str, object] | None = None,
) -> dict[str, object]:
    appraisal = value.get("appraisal_draft")
    expression = value.get("expression_draft")
    if not isinstance(appraisal, dict) or not isinstance(expression, dict):
        return dict(value)
    state = expression.get("private_turn_state")
    if not isinstance(state, dict):
        return dict(value)
    if authored is not None:
        _raise_if_incomplete_relationship_residue(authored)
    next_appraisal = dict(appraisal)
    changed = False
    if next_appraisal.get("relationship_signal") is None:
        about_us = _clip_text(state.get("about_us"), 128)
        why_us = _clip_text(state.get("why_us"), 128)
        if about_us and why_us:
            authored_deltas = (
                slim_relationship_deltas(authored.get("us_deltas"))
                if authored is not None
                else None
            )
            next_appraisal["relationship_signal"] = {
                "signal_code": about_us,
                "rationale_code": why_us,
                "confidence_bp": 5_000,
                "persistence": "durable",
                "suggested_deltas": authored_deltas
                or dict(_SLIM_ZERO_RELATIONSHIP_DELTAS),
            }
            changed = True
    if next_appraisal.get("relationship_commitment") is None:
        we_are = state.get("we_are")
        calling_it = _clip_text(state.get("calling_it"), 128)
        said_as = _clip_text(state.get("said_as"), 512)
        if we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as:
            next_appraisal["relationship_commitment"] = {
                "target_stage": we_are,
                "commitment_code": calling_it,
                "persistence": "durable",
                "visible_text_span": said_as,
            }
            changed = True
    if not changed:
        return dict(value)
    return {**dict(value), "appraisal_draft": next_appraisal}


def _proactive_has_visible_speech(bound: Mapping[str, object]) -> bool:
    if bound.get("timing_choice") == "silent":
        return False
    beats = bound.get("beats")
    if not isinstance(beats, list):
        return False
    return any(
        isinstance(beat, dict) and bool(str(beat.get("text") or "").strip())
        for beat in beats
    )


def hitchhike_proactive_authored_decisions(
    bound: dict[str, object],
    *,
    authored: Mapping[str, object],
) -> dict[str, object]:
    """Copy inbound slim residue onto a proactive ExpressionDraft-shaped dict.

    Incomplete or unreadable pairs raise.  Omission stays omission.  The host
    never invents a reading, a stage, or a display intent.
    """

    _raise_if_incomplete_relationship_residue(authored)
    _raise_if_incomplete_commitment_triplet(authored)
    _raise_if_invalid_declared_display(authored)
    if (
        _slim_field_attempted(authored, "we_are")
        or _slim_field_attempted(authored, "calling_it")
        or _slim_field_attempted(authored, "said_as")
    ) and not _proactive_has_visible_speech(bound):
        raise ValueError(
            SLIM_COMMITMENT_REQUIRES_SPEECH
            + "。宿主不会替你改成开口，也不会把 we_are 丢掉后假装没写。"
        )
    state = bound.get("private_turn_state")
    next_state = dict(state) if isinstance(state, dict) else {}
    added = False
    stuck_with_me = _clip_text(authored.get("stuck_with_me"), 1_200)
    if stuck_with_me:
        next_state["stuck_with_me"] = stuck_with_me
        added = True
    noticed = _clip_text(authored.get("noticed"), 720)
    if noticed:
        next_state["noticed"] = noticed
        added = True
    keep_impression = authored.get("keep_impression")
    if keep_impression is True:
        next_state["keep_impression"] = True
        added = True
    elif keep_impression is False:
        next_state["keep_impression"] = False
        added = True
    about_us = _clip_text(authored.get("about_us"), 128)
    why_us = _clip_text(authored.get("why_us"), 128)
    if about_us and why_us:
        next_state["about_us"] = about_us
        next_state["why_us"] = why_us
        added = True
    we_are = authored.get("we_are")
    calling_it = _clip_text(authored.get("calling_it"), 128)
    said_as = _clip_text(authored.get("said_as"), 512)
    if we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as:
        next_state["we_are"] = we_are
        next_state["calling_it"] = calling_it
        next_state["said_as"] = said_as
        added = True
    declared_display = _slim_declared_display(authored.get("declared_display"))
    if declared_display is not None:
        next_state["declared_display"] = declared_display
        added = True
    if isinstance(state, dict) or added:
        bound["private_turn_state"] = next_state
    needs_appraisal_anchor = bool(
        (about_us and why_us)
        or keep_impression is True
        or (we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as)
    )
    felt = (
        _clip_text(bound.get("brief_rationale"), 240)
        or _clip_text(bound.get("impulse_summary"), 240)
        or _clip_text(bound.get("stance"), 128)
    )
    label = _clip_text(bound.get("stance"), 64) or felt[:64]
    appraisal = bound.get("appraisal_draft")
    if isinstance(appraisal, dict):
        from .character_interior.inbound_appraisal_wire import (
            canonicalize_appraisal_draft_wire,
        )

        repaired_appraisal = dict(appraisal)
        outer_conf = bound.get("confidence")
        conf_val = (
            outer_conf
            if isinstance(outer_conf, int) and not isinstance(outer_conf, bool) and 0 <= outer_conf <= 10_000
            else 5_000
        )
        if not isinstance(repaired_appraisal.get("brief_rationale"), str) or not str(repaired_appraisal.get("brief_rationale")).strip():
            if felt:
                repaired_appraisal["brief_rationale"] = felt
        for lbl_key in ("behavior_tendency", "stance", "display_strategy"):
            if not isinstance(repaired_appraisal.get(lbl_key), str) or not str(repaired_appraisal.get(lbl_key)).strip():
                if label:
                    repaired_appraisal[lbl_key] = label
        if not isinstance(repaired_appraisal.get("confidence"), int) or isinstance(repaired_appraisal.get("confidence"), bool):
            repaired_appraisal["confidence"] = conf_val
        affect_op = repaired_appraisal.get("affect", "no_change")
        if affect_op in {"open", "update", "resolve", "supersede"} and repaired_appraisal.get("appraise") is not True:
            repaired_appraisal["appraise"] = True
        if repaired_appraisal.get("appraise") is True:
            rationale_text = _clip_text(repaired_appraisal.get("brief_rationale"), 128) or felt[:128]
            if not isinstance(repaired_appraisal.get("meanings"), list) or not repaired_appraisal.get("meanings"):
                if rationale_text:
                    repaired_appraisal["meanings"] = [
                        {"meaning": rationale_text, "confidence": repaired_appraisal.get("confidence", conf_val)}
                    ]
            if repaired_appraisal.get("attribution") not in {
                "user", "companion", "npc", "situation", "third_party", "unknown"
            }:
                repaired_appraisal["attribution"] = "unknown"
            sev = repaired_appraisal.get("severity")
            if not isinstance(sev, int) or isinstance(sev, bool) or not 0 <= sev <= 10_000:
                repaired_appraisal["severity"] = 5_000
        try:
            canonicalize_appraisal_draft_wire(repaired_appraisal)
            bound["appraisal_draft"] = repaired_appraisal
            appraisal = repaired_appraisal
        except ValueError:
            bound["appraisal_draft"] = None
            appraisal = None
    if needs_appraisal_anchor and not isinstance(appraisal, dict):
        if felt:
            bound["appraisal_draft"] = _slim_appraisal_draft(
                meaning_of_this=felt,
                my_state=felt,
                label=label or felt[:64],
                keep_impression=keep_impression is True,
            )
            appraisal = bound["appraisal_draft"]
    if isinstance(appraisal, dict):
        hitchhiked = attach_hitchhiked_relationship_residue(
            {
                "appraisal_draft": appraisal,
                "expression_draft": {"private_turn_state": next_state},
            },
            authored=authored,
        )
        bound["appraisal_draft"] = hitchhiked["appraisal_draft"]
    return bound


def compile_slim_interior_envelope(
    value: Mapping[str, object],
    *,
    reply_only: bool,
) -> dict[str, object] | None:
    compiled = compile_slim_consider_payload(value, allow_nontext=True)
    if compiled is None:
        return None
    # Attach before transport expansion so the compact gate actually carries the
    # relationship reading she authored instead of dropping it on the cheap path.
    compiled = attach_hitchhiked_relationship_residue(compiled, authored=value)
    expression = compiled["expression_draft"]
    if not isinstance(expression, dict):
        return None
    beats = expression.get("beats")
    if not isinstance(beats, list):
        return None
    if reply_only:
        timing = expression.get("timing_choice")
        media_request = expression.get("media_request", "none")
        media_source_refs = list(expression.get("media_source_refs") or [])
        if media_request not in {"none", "consider_available_candidate"}:
            raise ValueError("reply_only slim payload exceeds text-only capability")
        if media_request == "none" and media_source_refs:
            raise ValueError("reply_only slim payload exceeds text-only capability")
        # Media intent is expressible on reply_only, but only with an immediate
        # send. later/silent still cannot carry photo—that stays a visible reject.
        if media_request != "none" and timing != "now":
            raise ValueError("reply_only slim payload exceeds text-only capability")
        delay_seconds = expression.get("delay_seconds")
        expires_after_seconds = expression.get("expires_after_seconds")
        extra_beats: list[object] | None = None
        if timing == "now":
            if (
                not (1 <= len(beats) <= SLIM_REPLY_ONLY_MAX_TEXT_BEATS)
                or delay_seconds is not None
                or expires_after_seconds is not None
                or any(
                    not isinstance(item, dict)
                    or item.get("modality") not in {"text", "sticker"}
                    for item in beats
                )
            ):
                raise ValueError("reply_only slim payload exceeds text-only capability")
            beat: dict[str, object] | None = beats[0] if len(beats) == 1 else None
            if len(beats) > 1:
                extra_beats = beats
        elif timing == "later":
            if (
                not (1 <= len(beats) <= SLIM_REPLY_ONLY_MAX_TEXT_BEATS)
                or not isinstance(delay_seconds, int)
                or isinstance(delay_seconds, bool)
                or not isinstance(expires_after_seconds, int)
                or isinstance(expires_after_seconds, bool)
                or any(
                    not isinstance(item, dict) or item.get("modality") != "text"
                    for item in beats
                )
            ):
                raise ValueError("reply_only slim payload exceeds text-only capability")
            beat = beats[0] if len(beats) == 1 else None
            if len(beats) > 1:
                extra_beats = beats
        elif timing == "silent":
            if beats or delay_seconds is not None or expires_after_seconds is not None:
                raise ValueError("reply_only slim payload exceeds text-only capability")
            beat = None
        else:
            raise ValueError("reply_only slim payload exceeds text-only capability")
        raw_posture = expression.get("turn_posture")
        allowed_postures = (
            {"continue", "interject"}
            if timing == "now"
            else {"continue"}
        )
        head = {
            "type": "head",
            "private_turn_state": expression["private_turn_state"],
            "timing_choice": timing,
            "turn_posture": raw_posture if raw_posture in allowed_postures else None,
            "cadence": expression.get("cadence", "conversational"),
            "beat": beat,
            "stance": expression["stance"],
            "brief_rationale": expression["brief_rationale"],
            "confidence": expression["confidence"],
            "response_expectation": expression.get("response_expectation"),
            "response_expectation_assessment": expression.get(
                "response_expectation_assessment"
            ),
            "revisit": expression.get("revisit"),
            "world_claims": expression["world_claims"],
            "media_request": media_request,
            "media_source_refs": media_source_refs,
        }
        if extra_beats is not None:
            head["beats"] = extra_beats
        if timing == "later":
            head["delay_seconds"] = delay_seconds
            head["expires_after_seconds"] = expires_after_seconds
    else:
        head = {
            "type": "head",
            "private_turn_state": expression["private_turn_state"],
            "timing_choice": expression["timing_choice"],
            "turn_posture": expression.get("turn_posture"),
            "cadence": expression.get("cadence", "conversational"),
            "beats": beats,
            "stance": expression["stance"],
            "brief_rationale": expression["brief_rationale"],
            "confidence": expression["confidence"],
            "response_expectation": expression.get("response_expectation"),
            "response_expectation_assessment": expression.get(
                "response_expectation_assessment"
            ),
            "revisit": expression.get("revisit"),
            "world_claims": expression["world_claims"],
            "media_request": expression.get("media_request", "none"),
            "media_source_refs": list(expression.get("media_source_refs") or []),
        }
        if expression.get("timing_choice") == "later":
            head["delay_seconds"] = expression.get("delay_seconds")
            head["expires_after_seconds"] = expression.get("expires_after_seconds")
    return {
        "protocol": "character-interior-events.1",
        "appraisal_draft": compiled["appraisal_draft"],
        "events": [head, {"type": "end"}],
    }
