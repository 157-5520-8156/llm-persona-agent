"""Operator provisioning writes the adult-media eligibility pair once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)

from companion_daemon.world_v2.adult_media_authority import (
    ADULT_MEDIA_CAPABILITY_ID,
    ADULT_MEDIA_CONSENT_ID,
    adult_media_is_authorized,
)
from companion_daemon.world_v2.adult_media_authority_provisioning import (
    AdultMediaAuthorityProvisioner,
)
from companion_daemon.world_v2.media_authority_provisioning import MediaAuthorityProvisioner
from companion_daemon.world_v2.production_turn_application import WorldV2TurnApplicationConfig
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

NOW = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)
WORLD_ID = "world:adult-media-authority"
TEST_ROOT_SEED = "11" * 32


class _Identities:
    def resolve(self, *, platform: str, platform_user_id: str) -> tuple[str, str]:
        return (f"user:{platform_user_id}", "user:user.1")


class _NoModel:
    async def propose(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("provisioning test must not deliberate")


class _Router:
    async def route(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("provisioning test must not route")


class _Transport:
    provider = "platform:test"

    async def send(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("provisioning test must not dispatch")

    async def lookup(self, **_kwargs):  # type: ignore[no-untyped-def]
        return None


async def _clocked_world(path: Path) -> None:
    app = build_sqlite_world_v2_test_application(
        path=path,
        config=WorldV2TurnApplicationConfig(
            world_id=WORLD_ID,
            companion_actor_ref="agent:companion",
            reply_target="user:user.1",
            action_pump_owner="pump:adult-media-authority",
        ),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_NoModel(),
        ),
        transport=_Transport(),
        now=NOW,
    )
    try:
        await app.tick(
            tick_id="adult-media-authority:1",
            logical_time_from=NOW,
            logical_time_to=NOW + timedelta(minutes=1),
            observed_at=NOW + timedelta(minutes=1),
            trace_id="trace:adult-media-authority",
            causation_id="cause:adult-media-authority",
            correlation_id="correlation:adult-media-authority",
            reason="test",
        )
    finally:
        app.close()


@pytest.mark.asyncio
async def test_provisioner_writes_idempotent_adult_eligibility_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORLD_V2_ENABLE_INSECURE_TEST_ROOT", "1")
    path = tmp_path / "adult-media-authority.sqlite"
    await _clocked_world(path)
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    try:
        first = AdultMediaAuthorityProvisioner(
            ledger=ledger, signing_key_hex=TEST_ROOT_SEED, subject_ref="user:user.1",
        ).ensure()
        rerun = AdultMediaAuthorityProvisioner(
            ledger=ledger, signing_key_hex=TEST_ROOT_SEED, subject_ref="user:user.1",
        ).ensure()
        projection = ledger.project()
    finally:
        ledger.close()

    assert ADULT_MEDIA_CAPABILITY_ID in {
        item.grant_id for item in projection.capability_grants
    }
    assert ADULT_MEDIA_CONSENT_ID in {item.consent_id for item in projection.consent_grants}
    assert adult_media_is_authorized(
        enabled=True, projection=projection, at_logical_time=projection.logical_time
    )
    assert not adult_media_is_authorized(
        enabled=False, projection=projection, at_logical_time=projection.logical_time
    )
    assert first.committed_event_ids
    assert rerun.committed_event_ids == ()
    assert ADULT_MEDIA_CAPABILITY_ID in rerun.already_present
    assert ADULT_MEDIA_CONSENT_ID in rerun.already_present


@pytest.mark.asyncio
async def test_provisioner_reuses_media_actor_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORLD_V2_ENABLE_INSECURE_TEST_ROOT", "1")
    path = tmp_path / "adult-media-after-media.sqlite"
    await _clocked_world(path)
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    try:
        MediaAuthorityProvisioner(
            ledger=ledger, signing_key_hex=TEST_ROOT_SEED, subject_ref="user:user.1",
        ).ensure()
        result = AdultMediaAuthorityProvisioner(
            ledger=ledger, signing_key_hex=TEST_ROOT_SEED, subject_ref="user:user.1",
        ).ensure()
        projection = ledger.project()
    finally:
        ledger.close()

    assert set(result.committed_event_ids) == {
        f"event:adult-media-authority:{ADULT_MEDIA_CAPABILITY_ID}",
        f"event:adult-media-authority:{ADULT_MEDIA_CONSENT_ID}",
    }
    assert adult_media_is_authorized(
        enabled=True, projection=projection, at_logical_time=projection.logical_time
    )


def test_provisioner_rejects_a_key_outside_the_installed_root_set() -> None:
    class _Ledger:
        world_id = WORLD_ID

    with pytest.raises(ValueError, match="installed deployment root"):
        AdultMediaAuthorityProvisioner(
            ledger=_Ledger(), signing_key_hex="22" * 32, subject_ref="user:user.1",
        )
