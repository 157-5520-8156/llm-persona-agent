"""Run a fresh, capture-only virtual journey; real provider calls require opt-in."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def parse_options(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", type=Path, default=ROOT / "fixtures/world_v2/longitudinal_week.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-mode", choices=("fixture", "real-provider"), default="fixture")
    parser.add_argument("--allow-real-provider", action="store_true")
    parser.add_argument(
        "--max-cost-cny",
        type=float,
        default=0.5,
        help="One experiment's shared month/day/soft-day hard ceiling (default: 0.5).",
    )
    parser.add_argument("--heartbeat-seconds", type=float, default=300)
    parser.add_argument("--max-steps", type=int, default=5000)
    parser.add_argument("--max-wall-seconds", type=float, default=1800)
    parser.add_argument("--drain-passes", type=int, default=8)
    parser.add_argument("--background-units", type=int, default=4)
    options = parser.parse_args(argv)
    if options.model_mode == "real-provider" and not options.allow_real_provider:
        parser.error("real-provider requires --allow-real-provider")
    if options.allow_real_provider and options.model_mode != "real-provider":
        parser.error("--allow-real-provider requires --model-mode real-provider")
    if not math.isfinite(options.max_cost_cny) or not 0 < options.max_cost_cny <= 100:
        parser.error("--max-cost-cny must be finite, greater than 0 and at most 100")
    if options.output.exists() or options.output.is_symlink():
        parser.error("output must be a fresh path; existing data is never removed")
    return options


def experiment_settings(*, database: Path, synthetic: bool, max_cost_cny: float):
    from companion_daemon.config import Settings

    overrides = dict(
        database_path=database,
        character_path=ROOT / "configs/character.yaml",
        world_seed_path=ROOT / "configs/world_seed.yaml",
        stickers_path=ROOT / "configs/stickers.yaml",
        primary_user_id="longitudinal-audit",
        qq_adapter="napcat",
        monthly_budget_cny=max_cost_cny,
        daily_budget_cny=max_cost_cny,
        soft_daily_budget_cny=max_cost_cny,
        world_v2_text_endpoint_enabled=False,
        world_v2_media_preview_enabled=False,
        allow_auto_image_generation=False,
        world_v2_external_perception_mode="off",
        world_v2_recall_semantic_enabled=False,
        hermes_private_prompt_enabled=False,
        qq_turn_observation_path=None,
        attachment_cache_path=database.parent / "attachments",
        world_v2_external_perception_sidecar_path=database.parent / "external-perception.sqlite",
    )
    if synthetic:
        # Explicit fixture settings never inherit .env, credentials, or endpoints.
        return Settings.model_construct(**overrides)
    # Environment-only credentials; never read, print or copy the production .env.
    aliases = {Settings.model_fields[key].alias or key: value for key, value in overrides.items()}
    settings = Settings(_env_file=None, **aliases)
    if not settings.deepseek_debug_api_key:
        raise ValueError("real-provider requires DEEPSEEK_DEBUG_API_KEY for the isolated database")
    return settings


def code_identity() -> dict:
    """Record only the commit and a tracked-change flag, never diff contents."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"head": None, "tracked_dirty": None}
    commit = head.stdout.strip()
    valid = len(commit) in {40, 64} and all(char in "0123456789abcdef" for char in commit)
    return {
        "head": commit if head.returncode == 0 and valid else None,
        "tracked_dirty": bool(dirty.returncode) if dirty.returncode in {0, 1} else None,
    }


def model_identity(settings, *, synthetic: bool) -> dict:
    """Allowlist model names without serializing settings or credentials."""
    if synthetic:
        from companion_daemon.world_v2.longitudinal_fixture_model import LongitudinalFixtureModel

        return {
            "character": LongitudinalFixtureModel.model,
            "thinking_character": LongitudinalFixtureModel.model,
            "world_support": LongitudinalFixtureModel.model,
        }
    return {
        "character": settings.deepseek_model,
        "thinking_character": (
            settings.deepseek_character_thinking_model
            if settings.deepseek_character_thinking_enabled
            else None
        ),
        "world_support": settings.deepseek_model,
    }


def life_review_profile(settings, *, synthetic: bool) -> dict:
    if synthetic:
        from companion_daemon.world_v2.longitudinal_fixture_model import LongitudinalFixtureModel

        return {
            "status": "offline_fixture",
            "model": LongitudinalFixtureModel.model,
            "richness_coverage": "fixture_cannot_assess_life_richness",
        }
    available = (
        settings.world_v2_life_source_review_enabled and settings.world_v2_life_self_review_allowed
    )
    return {
        "status": "configured_self_review" if available else "unavailable",
        "model": settings.deepseek_model if available else None,
        "richness_coverage": (
            "requires_manual_evaluation"
            if available
            else "missing_reviewer_excludes_life_richness_comparison"
        ),
    }


async def run(options: argparse.Namespace) -> dict:
    from companion_daemon.world_v2.longitudinal_journey import (
        Journey,
        JourneyLimits,
        RECIPIENT,
        run_journey,
    )
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    scenario_bytes = options.scenario.read_bytes()
    journey = Journey.parse(json.loads(scenario_bytes))
    limits = JourneyLimits(
        heartbeat_seconds=options.heartbeat_seconds,
        max_steps=options.max_steps,
        max_wall_seconds=options.max_wall_seconds,
        drain_passes=options.drain_passes,
        background_units=options.background_units,
    )
    synthetic = options.model_mode == "fixture"
    # Validate provider configuration before creating the experiment directory.
    configured = experiment_settings(
        database=options.output / "world.sqlite",
        synthetic=synthetic,
        max_cost_cny=options.max_cost_cny,
    )

    def host_factory(database, clock, delivery):
        settings = configured.model_copy(update={"database_path": database})
        injected = {}
        if synthetic:
            from companion_daemon.world_v2.longitudinal_fixture_model import (
                LongitudinalFixtureModel,
            )

            fixture = LongitudinalFixtureModel()
            injected = dict(
                model=fixture,
                thinking_model=fixture,
                world_support_model=fixture,
                life_source_closure_model=fixture,
            )
        return build_qq_c2c_host(
            settings=settings,
            recipient_id=RECIPIENT,
            bootstrap_at=journey.started_at,
            delivery=delivery,
            ingress_now=clock.presentation_now,
            ingress_sleep=clock.presentation_sleep,
            action_due_now=clock.now,
            action_due_sleep=clock.timer_sleep,
            use_configured_recall_embedding=False,
            **injected,
        )

    return await run_journey(
        journey=journey,
        output=options.output,
        host_factory=host_factory,
        synthetic=synthetic,
        limits=limits,
        provenance={
            "model_mode": options.model_mode,
            "scenario_sha256": hashlib.sha256(scenario_bytes).hexdigest(),
            "code": code_identity(),
            "models": model_identity(configured, synthetic=synthetic),
            "life_source_review": life_review_profile(configured, synthetic=synthetic),
            "max_cost_cny": options.max_cost_cny,
            "billing_clock": "real_utc_not_virtual",
            "context_input_verification": "unverified",
            "profile_differences": [
                "captured QQ delivery",
                "virtual calendar and presentation waits",
                "media disabled",
                "external feeds disabled",
                "semantic embedding disabled",
                "text endpoint disabled",
            ],
            "fixture_limitations": (
                [
                    "deterministic fixture choices",
                    "no generated rich life",
                    "cannot assess human likeness or life richness",
                ]
                if synthetic
                else []
            ),
        },
    )


def main(argv: list[str] | None = None) -> int:
    options = parse_options(argv)
    try:
        manifest = asyncio.run(run(options))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Settings/provider errors can include private input values; print only type.
        print(f"Longitudinal audit setup failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(options.output),
                "completed": manifest["completed"],
                "stop_reason": manifest["stop_reason"],
                "synthetic": manifest["synthetic"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if manifest["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
