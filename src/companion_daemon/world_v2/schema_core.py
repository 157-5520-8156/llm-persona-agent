"""Cycle-free primitive schema contracts shared by World v2 domains."""

from __future__ import annotations

from datetime import UTC, datetime
from collections import OrderedDict
from datetime import timezone
from threading import RLock
from typing import Any, Literal
from weakref import ref
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _contains_naive_datetime(value: Any) -> bool:
    if isinstance(value, datetime):
        return value.tzinfo is None or value.utcoffset() is None
    if isinstance(value, dict):
        return any(_contains_naive_datetime(item) for item in value.values())
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_naive_datetime(item) for item in value)
    return False


# Tuples cannot be weak-referenced. A live frozen owner anchors the exact
# field identity instead; the cache holds neither the tuple nor its contents.
# Only the pure timezone predicate is reused, never source/permission checks.
_TIMEZONE_FIELD_CACHE: OrderedDict = OrderedDict()
_TIMEZONE_FIELD_LOCK = RLock()
_TIMEZONE_FIELD_CACHE_LIMIT = 256


def _timezone_scan_is_immutable(value: Any) -> bool:
    if isinstance(value, datetime):
        return type(value) is datetime and type(value.tzinfo) in (timezone, ZoneInfo)
    if isinstance(value, (dict, list, set)):
        return False
    if isinstance(value, (tuple, frozenset)):
        if type(value) in (tuple, frozenset) and len(value) >= 16:
            with _TIMEZONE_FIELD_LOCK:
                entry = _TIMEZONE_FIELD_CACHE.get(id(value))
                owner = entry[0]() if entry is not None else None
                if (owner is not None and type(owner).model_config.get('frozen') is True
                        and getattr(owner, entry[1], None) is value):
                    return True
        return type(value) in (tuple, frozenset) and all(
            _timezone_scan_is_immutable(item) for item in value
        )
    # The original predicate treats models and other non-container values as
    # leaves. This does not cache any of their own independent validators.
    return True


def _field_contains_naive_datetime(owner, name: str, value: Any) -> bool:
    if (type(value) not in (tuple, frozenset) or len(value) < 16
            or type(owner).model_config.get('frozen') is not True):
        return _contains_naive_datetime(value)
    key = id(value)
    with _TIMEZONE_FIELD_LOCK:
        entry = _TIMEZONE_FIELD_CACHE.get(key)
        previous = entry[0]() if entry is not None else None
        if (previous is not None and type(previous).model_config.get('frozen') is True
                and getattr(previous, entry[1], None) is value):
            # Move the weak anchor to the newest owner so a receipt can reuse
            # unchanged fields after the preceding projection is collected.
            _TIMEZONE_FIELD_CACHE[key] = (ref(owner), name)
            _TIMEZONE_FIELD_CACHE.move_to_end(key)
            return False
    if _contains_naive_datetime(value):
        return True
    if _timezone_scan_is_immutable(value):
        with _TIMEZONE_FIELD_LOCK:
            _TIMEZONE_FIELD_CACHE[key] = (ref(owner), name)
            _TIMEZONE_FIELD_CACHE.move_to_end(key)
            while len(_TIMEZONE_FIELD_CACHE) > _TIMEZONE_FIELD_CACHE_LIMIT:
                _TIMEZONE_FIELD_CACHE.popitem(last=False)
    return False


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def datetimes_are_timezone_aware(self) -> FrozenModel:
        for name in type(self).model_fields:
            if _field_contains_naive_datetime(self, name, getattr(self, name)):
                raise ValueError(f"{name} must contain only timezone-aware datetimes")
        return self


PrivacyClass = Literal["public", "shareable", "personal", "private", "withhold"]


class EvidenceRef(FrozenModel):
    ref_id: str = Field(min_length=1)
    evidence_type: Literal[
        "committed_fact",
        "committed_experience",
        "committed_world_event",
        "settled_world_event",
        "settled_external_result",
        "observed_message",
        "active_plan",
        "operator_observation",
        "clock_observation",
    ]
    claim_purpose: Literal[
        "current_fact",
        "past_experience",
        "future_plan",
        "life_transition",
        "private_hypothesis",
        "action_authorization",
        "conversation_continuity",
    ]
    source_world_revision: int | None = Field(default=None, ge=1)
    immutable_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def committed_world_evidence_is_revision_pinned(self) -> EvidenceRef:
        if self.evidence_type in {
            "committed_world_event",
            "settled_world_event",
            "committed_fact",
            "committed_experience",
        }:
            if self.source_world_revision is None or self.immutable_hash is None:
                raise ValueError("world-event evidence requires revision and immutable hash")
        return self


def canonicalize_json_value(value: Any) -> Any:
    """Normalize nested datetime-like JSON values for stable authority hashes."""

    if isinstance(value, dict):
        return {key: canonicalize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [canonicalize_json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and (value.endswith("Z") or "+" in value[-6:]):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value


def canonicalize_utc_json_value(value: Any) -> Any:
    """Preserve the UTC-only codec used by persisted domain mutation hashes.

    Unlike canonicalize_json_value, non-UTC offset strings stay byte-exact.
    Broadening normalization would invalidate already-recorded hashes.
    """

    if isinstance(value, dict):
        return {key: canonicalize_utc_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [canonicalize_utc_json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and (value.endswith("Z") or value.endswith("+00:00")):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value
