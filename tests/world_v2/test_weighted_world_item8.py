from __future__ import annotations

from pathlib import Path

import pytest

from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.npc_initiative_weight_policy import NOTHING_CANDIDATE_REF
from companion_daemon.world_v2.weighted_table import pick_weighted_token
from test_npc_ecology import _actor, _runtime, _world


_SEED = """
world_id: weighted-world-item8
life_author_catalog:
  version: weighted-world-item8.1
  locations:
    - id: campus-library
      location_ref: location:campus-library
      privacy: shareable
      local_windows: ["00:00-23:59"]
      weekdays: [0, 1, 2, 3, 4, 5, 6]
  npcs:
    - id: lin
      npc_id: lin
      stable_identity_ref: identity:npc:lin
      privacy: personal
      location_id: campus-library
      local_windows: ["00:00-23:59"]
      weekdays: [0, 1, 2, 3, 4, 5, 6]
  openings:
    - id: morning-reading
      activity_kind: study.reading
      source: routine
      domain: study_class
      social_shape: alone
      deviation: persist
      visual_potential: object
      privacy: personal
      local_windows: ["07:00-08:00"]
      weekdays: [0, 1, 2, 3, 4, 5, 6]
      duration_minutes: 30
      importance_bp: 4000
  npc_initiated_events:
    - id: npc-borrow-book
      initiative_kind: small_favor
      npc_id: lin
      location_id: campus-library
      summary: 林忽然来找她借一本读书会提到的书。
      privacy: personal
      local_windows: ["00:00-23:59"]
      weekdays: [0, 1, 2, 3, 4, 5, 6]
      duration_minutes: 10
      base_chance_bp: 10000
      outcomes:
        - {id: borrow-warm, text: 林忽然来借书，顺带聊了两句，气氛轻松。, privacy: personal}
        - {id: borrow-reluctant, text: 那本书她还没读完，犹豫了一下还是借了。, privacy: personal}
"""


def _catalog(path: Path) -> ReviewedLifeSeedCatalog:
    path.write_text(_SEED.strip(), encoding="utf-8")
    return ReviewedLifeSeedCatalog.from_yaml(
        path=path,
        chronology=LocalChronology("Asia/Shanghai"),
    )


def test_weighted_pick_is_a_pure_function_of_seed_and_mass() -> None:
    weights = {"event-a": 1_200, "event-b": 900, NOTHING_CANDIDATE_REF: 7_900}
    seed = {
        "wake_event_ref": "clock-life",
        "catalog_hash": "a" * 64,
        "weights": dict(sorted(weights.items())),
    }
    first = pick_weighted_token(weights, seed)
    second = pick_weighted_token(weights, seed)
    assert first == second
    assert first in weights


def test_weighted_pick_with_only_nothing_mass_selects_nothing() -> None:
    weights = {NOTHING_CANDIDATE_REF: 10_000}
    assert (
        pick_weighted_token(weights, {"wake_event_ref": "clock-life"})
        == NOTHING_CANDIDATE_REF
    )


@pytest.mark.asyncio
async def test_reviewed_catalog_still_asks_the_npc_itself(tmp_path: Path) -> None:
    """The catalog is material for the NPC actor, never a decision for it.

    Drawing an NPC's move from a weighted table turned the people around her
    into props; the seed catalog stays available, but the actor model is what
    decides whether anything happens.
    """

    catalog = _catalog(tmp_path / "seed.yaml")
    _ledger, _store, actor, world, runtime = _runtime(_actor("no_op"), _world())
    runtime._catalog = catalog  # noqa: SLF001

    result = await runtime.advance_once(
        wake_event_ref="clock-life",
        trace_id="trace",
        correlation_id="correlation",
    )

    assert len(actor.calls) == 1
    assert result.status in {"state_advanced", "occurrence_committed"}


@pytest.mark.asyncio
async def test_an_npc_who_acts_reaches_world_adjudication(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path / "seed.yaml")
    _ledger, _store, actor, world, runtime = _runtime(
        _actor("propose"), {"decision": "no_op"}
    )
    runtime._catalog = catalog  # noqa: SLF001

    await runtime.advance_once(
        wake_event_ref="clock-life",
        trace_id="trace",
        correlation_id="correlation",
    )

    assert len(actor.calls) == 1
    assert len(world.calls) == 1


@pytest.mark.asyncio
async def test_npc_ecology_without_catalog_still_calls_the_model() -> None:
    _ledger, _store, actor, world, runtime = _runtime(_actor("no_op"), {"decision": "no_op"})
    assert runtime._catalog is None  # noqa: SLF001

    result = await runtime.advance_once(
        wake_event_ref="clock-life",
        trace_id="trace",
        correlation_id="correlation",
    )

    assert result.status == "state_advanced"
    assert len(actor.calls) == 1
    assert world.calls == []


def test_life_capability_compiler_exposes_the_reviewed_catalog(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path / "seed.yaml")
    from companion_daemon.world_v2.life_development_capability import (
        ProjectionLifeCapabilityManifestCompiler,
    )

    compiler = ProjectionLifeCapabilityManifestCompiler(
        owner_actor_ref="actor:companion",
        catalog=catalog,
    )
    assert compiler.catalog is catalog
    assert isinstance(compiler.catalog, ReviewedLifeSeedCatalog)


def test_life_development_opportunity_draw_is_replay_stable() -> None:
    from companion_daemon.world_v2.life_development_runtime import (
        LIFE_DEVELOPMENT_DISTURBANCE_REF,
        LIFE_DEVELOPMENT_NOTHING_REF,
        LIFE_DEVELOPMENT_OPPORTUNITY_REF,
        draw_life_development_opportunity,
        life_development_opportunity_weights,
        occasion_mode_for_draw,
        outcome_has_durable_world_consequence,
        validate_disturbance_consequence_closure,
    )
    from companion_daemon.world_v2.life_development_draft import (
        LifeDevelopmentDraftError,
        LifeDevelopmentOutcomeDraft,
    )

    weights = life_development_opportunity_weights()
    assert weights[LIFE_DEVELOPMENT_OPPORTUNITY_REF] == 2_000
    assert weights[LIFE_DEVELOPMENT_DISTURBANCE_REF] == 600
    assert weights[LIFE_DEVELOPMENT_NOTHING_REF] == 7_400
    seed = {
        "catalog_hash": "a" * 64,
        "wake_event_ref": "event:clock:life",
    }
    first = draw_life_development_opportunity(**seed)
    second = draw_life_development_opportunity(**seed)
    assert first == second
    assert first in {
        LIFE_DEVELOPMENT_NOTHING_REF,
        LIFE_DEVELOPMENT_OPPORTUNITY_REF,
        LIFE_DEVELOPMENT_DISTURBANCE_REF,
    }
    assert occasion_mode_for_draw(LIFE_DEVELOPMENT_DISTURBANCE_REF) == "disturbance"
    assert occasion_mode_for_draw(LIFE_DEVELOPMENT_OPPORTUNITY_REF) == "ordinary"

    durable = LifeDevelopmentOutcomeDraft(
        experienced_by_ref="actor:companion",
        text="书店临时闭店，她改去图书馆。",
        privacy_class="shareable",
        relative_plausibility_weight=1,
        claim_refs=["local:claim:closed"],
        dynamic_life_direction={
            "summary": "书店本周闭店装修。",
            "context_tags": ["constraint:bookstore-closed"],
        },
    )
    assert outcome_has_durable_world_consequence(durable)

    atmosphere_only = LifeDevelopmentOutcomeDraft(
        experienced_by_ref="actor:companion",
        text="风有点凉，她裹了裹外套。",
        privacy_class="shareable",
        relative_plausibility_weight=1,
        claim_refs=["local:claim:wind"],
    )
    assert not outcome_has_durable_world_consequence(atmosphere_only)

    from types import SimpleNamespace

    with pytest.raises(LifeDevelopmentDraftError, match="disturbance_missing_consequence"):
        validate_disturbance_consequence_closure(
            SimpleNamespace(outcomes=(atmosphere_only, atmosphere_only))
        )
