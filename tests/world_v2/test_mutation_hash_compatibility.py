"""Freeze existing mutation identities across shared codec refactors."""

from datetime import datetime, timedelta, timezone

import pytest

from companion_daemon.world_v2.commitment_events import commitment_mutation_hash
from companion_daemon.world_v2.experience_events import experience_mutation_hash
from companion_daemon.world_v2.fact_events import fact_mutation_hash
from companion_daemon.world_v2.memory_events import memory_candidate_mutation_hash
from companion_daemon.world_v2.schema_core import (
    canonicalize_json_value,
    canonicalize_utc_json_value,
)


@pytest.mark.parametrize("hash_mutation", [
    commitment_mutation_hash, experience_mutation_hash,
    fact_mutation_hash, memory_candidate_mutation_hash,
])
def test_persisted_mutation_hash_preserves_the_pre_cleanup_identity(hash_mutation):
    # Recorded from all four independent implementations at fa6c3700.
    material = {
        "acceptance_id": "acceptance:first",
        "proposal_id": "proposal:first",
        "accepted_change_hash": "excluded",
        "logical_time": "2026-10-13T04:05:06+00:00",
        "nested": [{
            "text": "还记得那天吗？",
            "when": "2026-10-13T04:05:06Z",
            "positive": "2026-10-13T12:05:06+08:00",
            "negative": "2026-10-13T00:05:06-04:00",
            "invalid": "not-a-dateZ",
            "empty": None,
        }],
        "clock": datetime(2026, 10, 13, 12, 5, 6, tzinfo=timezone(timedelta(hours=8))),
        "n": 7,
        "flags": [True, False],
    }
    expected = "528a131bc8d9de471278c3cdac2516f685a2ebecb86e900f684cbdb24ace2474"
    assert hash_mutation(material) == expected
    assert hash_mutation({
        **material,
        "acceptance_id": "acceptance:second",
        "proposal_id": "proposal:second",
        "accepted_change_hash": "different",
    }) == expected
    assert hash_mutation({**material, "n": 8}) != expected
    assert material["logical_time"] == "2026-10-13T04:05:06+00:00"


def test_historical_and_modern_offset_codecs_remain_distinct():
    value = "2026-10-13T12:05:06+08:00"
    assert canonicalize_utc_json_value(value) == value
    assert canonicalize_json_value(value) == "2026-10-13T04:05:06Z"
