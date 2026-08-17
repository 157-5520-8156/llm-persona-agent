"""Background lanes must not inherit the interactive cancellation ceiling.

Production evidence 2026-08-14: three proactive contacts in 24h, all cancelled
at exactly 10.99s. The primary returned valid output every time (4.2-7.9s,
billed), then the constrained reselection was cut off because both legs shared
one 11.0s author deadline sized for a human watching a typing indicator.
"""

from __future__ import annotations

from companion_daemon.world_v2.interactive_turn_budget import (
    InteractiveTurnBudgetPolicy,
    background_turn_budget_policy,
)
from companion_daemon.world_v2.production_turn_application import (
    WorldV2TurnApplicationConfig,
)


# world_v2_model_usage.latency_ms, character_interior succeeded n=695.
_PRIMARY_P99_SECONDS = 8.508


def _author_window(policy: InteractiveTurnBudgetPolicy) -> float:
    return policy.total_seconds - policy.acceptance_dispatch_reserve_seconds


def test_background_window_holds_two_serial_calls_at_p99() -> None:
    policy = background_turn_budget_policy()

    assert _author_window(policy) >= 2 * _PRIMARY_P99_SECONDS


def test_interactive_window_is_unchanged() -> None:
    policy = InteractiveTurnBudgetPolicy()

    assert _author_window(policy) == 11.0


def test_background_window_is_wider_than_interactive() -> None:
    assert _author_window(background_turn_budget_policy()) > _author_window(
        InteractiveTurnBudgetPolicy()
    )


def test_production_config_gives_background_lanes_their_own_policy() -> None:
    config = WorldV2TurnApplicationConfig(
        world_id="world:test",
        companion_actor_ref="actor:companion",
        reply_target="target:test",
        action_pump_owner="owner:test",
    )

    assert _author_window(config.background_turn_budget_policy) >= (
        2 * _PRIMARY_P99_SECONDS
    )
    assert _author_window(config.interactive_turn_budget_policy) == 11.0


def test_background_policy_keeps_hedge_and_recovery_semantics() -> None:
    policy = background_turn_budget_policy()
    interactive = InteractiveTurnBudgetPolicy()

    assert policy.hedge_after_seconds == interactive.hedge_after_seconds
    assert policy.validation_recovery_seconds == interactive.validation_recovery_seconds
    assert (
        policy.validation_reselection_seconds
        == interactive.validation_reselection_seconds
    )
