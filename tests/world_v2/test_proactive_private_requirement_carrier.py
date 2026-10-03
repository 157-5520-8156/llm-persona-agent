"""Exact private source evidence survives the bounded proactive capability."""

import base64
import zlib

import pytest

from companion_daemon.world_v2 import visible_source_proactive as wire


def test_large_private_requirement_round_trips_without_expanding_the_capability():
    requirement = '{"original":"' + '往事与来源\\"细节' * 30_000 + '"}'
    payload = {"contract": wire.CAPABILITY_CONTRACT}
    carrier = wire._requirement_carrier(payload=payload, requirement=requirement)
    assert isinstance(carrier, dict)
    assert len(wire.canonical({**payload, wire.REQUIREMENT_KEY: carrier})) < 262_144
    assert wire._read_requirement(carrier) == requirement
    assert wire._requirement_carrier(payload=payload, requirement="{}") == "{}"


@pytest.mark.parametrize("mutation", ["hash", "length", "truncated", "trailing", "bomb", "base64", "extra"])
def test_corrupt_private_requirement_is_rejected(mutation):
    carrier = wire._pack_requirement('中文来源' * 1000)
    if mutation == "hash":
        carrier["sha256"] = "0" * 64
    elif mutation == "length":
        carrier["utf8_bytes"] += 1
    elif mutation == "truncated":
        carrier["data"] = base64.b64encode(base64.b64decode(carrier["data"])[:-1]).decode()
    elif mutation == "trailing":
        carrier["data"] = base64.b64encode(base64.b64decode(carrier["data"]) + b"extra").decode()
    elif mutation == "bomb":
        carrier["utf8_bytes"] = 10
        carrier["data"] = base64.b64encode(zlib.compress(b"x" * 100_000)).decode()
    elif mutation == "base64":
        carrier["data"] = "not base64!"
    else:
        carrier["unknown"] = True
    with pytest.raises(ValueError):
        wire._read_requirement(carrier)


@pytest.mark.asyncio
async def test_compressed_original_pin_survives_proactive_acceptance_and_cold_replay(tmp_path, monkeypatch):
    from test_proactive_visible_source_gate import (
        test_reviewed_proactive_cold_replay_has_no_new_calls_or_duplicate_actions as cold_replay,
    )

    # Exercise the real author/source/receipt/replay chain with the new carrier,
    # using the existing small offline fixture and no provider access.
    monkeypatch.setattr(wire, "_requirement_carrier", lambda *, payload, requirement: wire._pack_requirement(requirement))
    await cold_replay(
        tmp_path, monkeypatch, pause_before_acceptance=True, review_version="6",
        legacy_claim_lanes=False, legacy_review_participants=False,
        scenario="valid_claim", history_subject="counterpart",
    )
