"""Provider-local composition pacing between consecutive Expression Beats.

A person who decides to send several bubbles still has to *type* each one
after the previous one goes out.  Dispatching an already-authored follow-up
beat ~0.5s after its predecessor (the raw provider round-trip) produces four
bubbles in under two seconds, which no human thumb can do.

This module only sizes the physical typing gap before a follow-up beat that
the role model already authored.  It does not choose content, bubble count,
order, now/later/silent, or whether a beat is sent: those remain Character
Decisions.  The pause is provider-local like the ingress listening hold — it
never writes ledger state, changes Action identity, or affects replay — and
an interjection that lands during the pause is caught by the existing durable
pending-ingress check at ``ActionDispatchStarted`` so the role can reconsider.

Timing reference: casual smartphone pinyin entry is commonly reported around
50-80 characters/minute, with predictive IMEs reaching ~2-3 characters/second
for short colloquial phrases; the 2026-10-26 QQ volley showed this user at
roughly 2 characters/second between continuing bubbles.  The model below is a
bounded approximation, not a claim about any individual's typing speed.
"""

from __future__ import annotations

import hashlib

COMPOSITION_PACING_VERSION = "beat-composition-pacing.1"

_BASE_SECONDS = 0.5
_CHARACTERS_PER_SECOND = 2.5
_MIN_SECONDS = 0.9
_MAX_SECONDS = 6.0
_JITTER_FRACTION = 0.15
# Non-text beats (sticker, reaction) need a pick-and-tap, not typing.
_NON_TEXT_SECONDS = 1.2


def composition_seconds(*, character_count: int | None, jitter_key: str) -> float:
    """Return the bounded typing gap before one follow-up beat.

    ``character_count`` is the exact length of the beat's immutable text
    payload, or ``None`` for a non-text beat.  ``jitter_key`` (the beat's
    Action id) yields a deterministic ±15% variation so identical lengths do
    not produce a mechanically regular rhythm, while any retry of the same
    Action computes the same pause.
    """

    if not jitter_key:
        raise ValueError("composition pacing requires a stable jitter key")
    if character_count is None:
        base = _NON_TEXT_SECONDS
    else:
        if character_count < 0:
            raise ValueError("character count cannot be negative")
        base = _BASE_SECONDS + character_count / _CHARACTERS_PER_SECOND
    digest = hashlib.sha256(f"{COMPOSITION_PACING_VERSION}:{jitter_key}".encode()).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF  # 0..1
    jittered = base * (1.0 + _JITTER_FRACTION * (2.0 * unit - 1.0))
    return min(_MAX_SECONDS, max(_MIN_SECONDS, jittered))


__all__ = ["COMPOSITION_PACING_VERSION", "composition_seconds"]
