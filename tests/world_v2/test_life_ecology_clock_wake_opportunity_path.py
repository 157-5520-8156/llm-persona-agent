from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
    compose_fixture_character_purpose,
)

from companion_daemon.world_v2.life_development_capability import (
    ProjectionLifeCapabilityManifestCompiler,
)
from companion_daemon.world_v2.platform_host import WorldV2PlatformHost
from companion_daemon.world_v2.production_turn_application import (
    LifeEcologyComposition,
    WorldV2TurnApplicationConfig,
)
from companion_daemon.world_v2.qq_c2c_host import QQC2CHost

from test_life_development_production import (
    NOW,
    _AcceptCharacterModel,
    _Identities,
    _MainModel,
    _NoOpDayOpenRole,
    _PlanWorldAuthor,
    _Router,
    _SupportingSourceReviewer,
    _Transport,
    _force_life_development_draw,
    _open_life_seed,
)


SCHEDULER_TIME = NOW + timedelta(minutes=10)
DIRECT_WAKE_REF = "event:trigger:clock:direct-final"
SCHEDULER_TICK_ID = f"tick:qq-c2c-v2:{SCHEDULER_TIME.isoformat()}"
SCHEDULER_WAKE_REF = f"event:trigger:clock:{SCHEDULER_TICK_ID}"


class _NoOpLifeChoice:
    model = "test-life-choice-no-op"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        _messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> str:
        del temperature
        self.calls += 1
        return '{"decision":"no_op"}'


class _RecordingPlatformHost(WorldV2PlatformHost):
    """Keep the real QQ scheduler and platform clock seam, with fake providers."""

    def __init__(self, *, application) -> None:  # type: ignore[no-untyped-def]
        super().__init__(application=application)
        self.life_results: list[object] = []

    async def advance_life_ecology_once(self, **kwargs):  # type: ignore[no-untyped-def]
        result = await super().advance_life_ecology_once(**kwargs)
        self.life_results.append(result)
        return result


def _config(seed_path: Path) -> WorldV2TurnApplicationConfig:
    return WorldV2TurnApplicationConfig(
        world_id="world:life-ecology-clock-wake-test",
        companion_actor_ref="actor:companion",
        reply_target="user:user.1",
        action_pump_owner="pump:life-ecology-clock-wake-test",
        character_memory_enabled=False,
        life_ecology=LifeEcologyComposition.production_v1(seed_catalog_path=seed_path),
    )


def _install_legacy_text_manifest_for_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixtureManifestCompiler(ProjectionLifeCapabilityManifestCompiler):
        def compile(self, **kwargs):  # type: ignore[no-untyped-def]
            return super().compile(**kwargs).model_copy(
                update={
                    "version": "life-development-capability.production.2",
                    "outcome_contract": None,
                    "execution_intention_sources_version": None,
                    "pinned_source_materials_version": None,
                    "semantic_source_review_version": None,
                }
            )

    monkeypatch.setattr(
        "companion_daemon.world_v2.production_turn_application.ProjectionLifeCapabilityManifestCompiler",
        FixtureManifestCompiler,
    )


async def _make_application(
    *,
    tmp_path: Path,
    seed_path: Path,
    route: str,
    decision: str,
):
    wake_ref = DIRECT_WAKE_REF if route == "direct" else SCHEDULER_WAKE_REF
    world_author = _PlanWorldAuthor(wake_event_ref=wake_ref)
    character_model = _AcceptCharacterModel() if decision == "accept" else _NoOpLifeChoice()
    day_open_role = _NoOpDayOpenRole()
    application = build_sqlite_world_v2_test_application(
        path=tmp_path / f"{route}-{decision}.sqlite",
        config=_config(seed_path),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_MainModel(),
            purpose_faculties=(
                compose_fixture_character_purpose(
                    purpose="life_development_choice",
                    provider=character_model,
                ),
                compose_fixture_character_purpose(
                    purpose="activity_lifecycle_choice",
                    provider=day_open_role,
                ),
            ),
        ),
        transport=_Transport(),
        life_world_author_model=world_author,
        life_source_closure_reviewer=_SupportingSourceReviewer(),
        now=NOW,
    )
    return application, world_author, character_model


async def _prime_clock(application) -> None:  # type: ignore[no-untyped-def]
    await application.tick(
        tick_id="shared-prefix",
        logical_time_from=NOW,
        logical_time_to=NOW + timedelta(minutes=9),
        observed_at=NOW + timedelta(minutes=9),
        trace_id="trace:life-ecology-clock-wake-test",
        causation_id="scheduler:life-ecology-clock-wake-test",
        correlation_id="correlation:life-ecology-clock-wake-test",
        reason="life-ecology-clock-wake-test-prefix",
        run_life_ecology=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ("direct", "scheduler"))
@pytest.mark.parametrize("decision", ("accept", "no_op"))
async def test_same_due_life_opportunity_separates_clock_wake_generation_and_character_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    decision: str,
) -> None:
    """No provider/network calls: the same due instant reaches generation and choice.

    The direct lane is the consumer control. The scheduler lane traverses the
    real QQ clock-wake selector, PlatformHost clock adapter, and Life Ecology
    handoff. Only the route and CharacterInterior's authored response vary.
    """

    from companion_daemon.world_v2.life_development_runtime import (
        LIFE_DEVELOPMENT_OPPORTUNITY_REF,
    )

    _install_legacy_text_manifest_for_fixture(monkeypatch)
    _force_life_development_draw(monkeypatch, LIFE_DEVELOPMENT_OPPORTUNITY_REF)
    seed_path = _open_life_seed(tmp_path / "life-seed.yaml")
    application, world_author, character_model = await _make_application(
        tmp_path=tmp_path,
        seed_path=seed_path,
        route=route,
        decision=decision,
    )
    qq_host = None
    try:
        await _prime_clock(application)
        if route == "direct":
            await application.tick(
                tick_id="direct-final",
                logical_time_from=NOW + timedelta(minutes=9),
                logical_time_to=SCHEDULER_TIME,
                observed_at=SCHEDULER_TIME,
                trace_id="trace:life-ecology-clock-wake-test:direct",
                causation_id="scheduler:life-ecology-clock-wake-test:direct",
                correlation_id="correlation:life-ecology-clock-wake-test",
                reason="life-ecology-clock-wake-test-final",
                run_life_ecology=False,
            )
            assert await application.life_ecology_next_due() == SCHEDULER_TIME
            life_result = await application.advance_life_ecology_once(
                wake_event_ref=DIRECT_WAKE_REF,
                trace_id="trace:life-ecology-clock-wake-test:direct",
                correlation_id="correlation:life-ecology-clock-wake-test",
            )
        else:
            assert await application.life_ecology_next_due() == NOW + timedelta(minutes=9)
            platform_host = _RecordingPlatformHost(application=application)
            qq_host = QQC2CHost(
                host=platform_host,
                recipient_id="10001",
                canonical_user_id="geoff",
                ingress_now=lambda: SCHEDULER_TIME,
            )
            await qq_host.scheduler_once(
                observed_at=SCHEDULER_TIME,
                max_action_units=0,
                max_background_units=0,
            )
            assert len(platform_host.life_results) == 1
            life_result = platform_host.life_results[0]
            assert application._ledger.project().logical_time == SCHEDULER_TIME  # noqa: SLF001

        # These assertions separate a missing call to the producer from a
        # producer that ran and offered an opportunity the character declined.
        assert world_author.calls == 1
        assert character_model.calls == 1
        assert life_result.life_development_followup_status == (
            "plan_committed" if decision == "accept" else "no_op"
        )
        plans = tuple(
            item
            for item in application._ledger.project().plans  # noqa: SLF001
            if item.owner_actor_ref == "actor:companion"
            and item.activity_kind.startswith("open_life.")
        )
        assert bool(plans) is (decision == "accept")
    finally:
        if qq_host is not None:
            await qq_host.aclose()
        application.close()
