"""Repeated projections share immutable fields, not mutable validation results."""
from datetime import datetime, timezone, tzinfo
import gc
from typing import Any
from weakref import ref

import pytest

from companion_daemon.world_v2 import schema_core


class Frame(schema_core.FrozenModel):
    entries: tuple[Any, ...]


def test_new_projection_reuses_same_immutable_field_but_checks_replacement(monkeypatch):
    original = schema_core._contains_naive_datetime
    visits = []

    def counted(value):
        if isinstance(value, tuple) and len(value) >= 64:
            visits.append(value)
        return original(value)

    monkeypatch.setattr(schema_core, '_contains_naive_datetime', counted)
    first = Frame(entries=tuple(str(i) for i in range(64)))
    second = first.model_copy()
    second.datetimes_are_timezone_aware()
    assert len(visits) == 1
    changed = second.model_copy(update={'entries': (*second.entries, datetime(2026, 1, 1))})
    with pytest.raises(ValueError, match='timezone-aware'):
        changed.datetimes_are_timezone_aware()
    assert len(visits) == 2


def test_mutable_children_and_custom_timezone_cannot_reuse_a_previous_success():
    mutable = {'value': datetime(2026, 1, 1, tzinfo=timezone.utc)}
    frame = Frame(entries=(*range(64), mutable))
    mutable['value'] = datetime(2026, 1, 1)
    with pytest.raises(ValueError, match='timezone-aware'):
        frame.datetimes_are_timezone_aware()

    class MutableZone(tzinfo):
        enabled = True

        def utcoffset(self, dt):
            return timezone.utc.utcoffset(dt) if self.enabled else None

    zone = MutableZone()
    frame = Frame(entries=(*range(64), datetime(2026, 1, 1, tzinfo=zone)))
    zone.enabled = False
    with pytest.raises(ValueError, match='timezone-aware'):
        frame.datetimes_are_timezone_aware()


def test_field_reuse_does_not_keep_its_owner_alive():
    frame = Frame(entries=tuple(str(i) for i in range(64)))
    pointer = ref(frame)
    del frame
    gc.collect()
    assert pointer() is None


def test_field_cache_stays_bounded_and_nonfrozen_owners_are_not_anchors():
    frames = [Frame(entries=tuple(range(i, i+64))) for i in range(300)]
    assert len(schema_core._TIMEZONE_FIELD_CACHE) <= schema_core._TIMEZONE_FIELD_CACHE_LIMIT
    for frame in frames:
        frame.datetimes_are_timezone_aware()
    assert len(schema_core._TIMEZONE_FIELD_CACHE) <= schema_core._TIMEZONE_FIELD_CACHE_LIMIT
