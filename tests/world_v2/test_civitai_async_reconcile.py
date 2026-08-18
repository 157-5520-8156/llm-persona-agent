from datetime import UTC, datetime

from companion_daemon.world_v2.media_execution_runtime import (
    CIVITAI_ABANDON_AFTER,
    civitai_template_dispatch_pending,
    is_civitai_template_pending_reason,
)


def test_pending_reason_is_not_a_terminal_provider_failure() -> None:
    assert is_civitai_template_pending_reason("image_provider_pending")
    assert not is_civitai_template_pending_reason("image_provider_unknown")
    assert not is_civitai_template_pending_reason("image_provider_quota")


def test_civitai_dispatch_pending_keeps_effect_once_and_a_hours_long_deadline() -> None:
    now = datetime(2026, 8, 18, 5, 0, tzinfo=UTC)
    pending = civitai_template_dispatch_pending(
        action_id="action:media-render:1",
        idempotency_key="media-render:plan:1",
        provider="provider:event-media",
        now=now,
    )
    assert pending.idempotency_mode == "effect_once"
    assert pending.provider_ref is None
    assert pending.deadline - pending.dispatch_started_at == CIVITAI_ABANDON_AFTER
    assert pending.lookup_after > pending.dispatch_started_at
    assert pending.deadline > pending.lookup_after
