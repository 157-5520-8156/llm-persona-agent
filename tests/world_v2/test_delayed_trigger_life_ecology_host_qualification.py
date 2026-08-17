from __future__ import annotations

from datetime import UTC, datetime, timedelta
import asyncio
from pathlib import Path

import pytest

from companion_daemon.config import Settings
from companion_daemon.delayed_trigger_catalog import load_delayed_trigger_catalog
from companion_daemon.llm import FakeCompanionModel
from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host


HOST_QUALIFICATION_DECLARATION = {
    "qualification_layer": "public_host_scenario",
    "scenario_id": "life.ecology-clock-wake-retry-isolation.1",
    "mechanisms": ("life.ecology",),
    "public_seams": (
        "QQC2CHost.tick",
        "QQC2CHost.inbound_text",
        "QQC2CHost.drain",
        "QQC2CHost.export_replay_evidence",
        "QQC2CHost.world_health_diagnostics",
        "QQC2CHost.aclose",
    ),
    "qualification_scope": "public_host_life_scheduler_terminal",
}

_CATALOG = Path("configs/delayed_trigger_qualification.v1.yaml")


class _UnavailableLifeWorldAuthor:
    """A bounded World Author outage for scheduler-failure qualification."""

    model = "fixture:life-ecology-world-author-unavailable"
    semantic_authority_id = "semantic-authority:fixture:life-ecology-unavailable"

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
        raise TimeoutError("qualified World Author outage")


class _Delivery:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send_text(self, _recipient_id: str, text: str) -> dict[str, object]:
        self.texts.append(text)
        return {
            "status": "ok",
            "data": {"message_id": f"message:life-qualification:{len(self.texts)}"},
        }

    async def send_reaction(
        self,
        _recipient_id: str,
        *,
        message_id: str,
        reaction_id: str,
    ) -> dict[str, object]:
        del message_id, reaction_id
        return {"status": "failed"}

    async def send_sticker(
        self,
        _recipient_id: str,
        *,
        sticker_id: str,
    ) -> dict[str, object]:
        del sticker_id
        return {"status": "failed"}

    async def send_typing(
        self,
        _recipient_id: str,
        *,
        state: str,
    ) -> dict[str, object]:
        del state
        return {"status": "ok", "data": {"message_id": "typing"}}

    async def get_message(
        self,
        _recipient_id: str,
        *,
        message_id: str,
    ) -> dict[str, object]:
        return {
            "status": "ok",
            "retcode": 0,
            "data": {"message_id": message_id, "message": self.texts[-1]},
        }


def _host_scenario(nodeid: str) -> object:
    evidence = load_delayed_trigger_catalog(_CATALOG).host_scenario(
        "life.ecology-clock-wake-retry-isolation.1"
    )
    assert evidence.test_nodeid == nodeid
    assert evidence.mechanism_ids == ("life.ecology",)
    assert evidence.qualification_scope == HOST_QUALIFICATION_DECLARATION[
        "qualification_scope"
    ]
    return evidence


@pytest.mark.asyncio
async def test_public_host_life_ecology_wake_terminal_and_retry_does_not_block_inbound(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    _host_scenario(request.node.nodeid)
    started_at = datetime.now(UTC).replace(microsecond=0)
    scheduler_clock = {"now": started_at}
    pacing_clock = {"now": started_at}
    delivery = _Delivery()
    world_author = _UnavailableLifeWorldAuthor()
    database = tmp_path / "life-ecology-host-qualification.sqlite"
    settings = Settings(
        _env_file=None,
        database_path=database,
        PRIMARY_USER_ID="geoff",
        WORLD_V2_EXPRESSION_EPISODE_MODE="off",
    )

    async def skip_pacing(seconds: float) -> None:
        pacing_clock["now"] += timedelta(seconds=max(0.0, seconds))
        await asyncio.sleep(0)

    def build():
        return build_qq_c2c_host(
            settings=settings,
            recipient_id="10001",
            bootstrap_at=started_at,
            model=FakeCompanionModel(),
            world_support_model=world_author,
            delivery=delivery,
            ingress_now=lambda: pacing_clock["now"],
            ingress_sleep=skip_pacing,
            action_due_now=lambda: scheduler_clock["now"],
        )

    first_due = started_at + timedelta(minutes=10)
    first = build()
    try:
        assert (
            await first.tick(
                tick_id="life-ecology-host-first",
                logical_time_from=started_at,
                logical_time_to=first_due,
                observed_at=first_due,
                reason="life_ecology_host_qualification_first_wake",
                run_life_ecology=True,
            )
            == "observed_only"
        )
        # The World Author may be consulted on a life wake; what must not happen
        # is a second consultation from replay or from a repeated drain.
        first_wake_calls = world_author.calls
        scheduler_clock["now"] = first_due + timedelta(seconds=1)
        pacing_clock["now"] = scheduler_clock["now"]
        inbound = await first.inbound_text(
            message_id="life-ecology-host-inbound",
            recipient_id="10001",
            text="你好",
            observed_at=scheduler_clock["now"],
        )
        assert inbound.status == "action_authorized"
        assert delivery.texts == ["我在，刚刚这句我有接到。"]
        after_inbound = first.export_replay_evidence()
    finally:
        await first.aclose()

    second = build()
    try:
        rebuilt = second.export_replay_evidence()
        assert rebuilt.projection.semantic_hash == after_inbound.projection.semantic_hash
        await second.drain(max_action_units=8, max_background_units=0)
        after_first_drain = second.export_replay_evidence()
        await second.drain(max_action_units=8, max_background_units=0)
        after_drain = second.export_replay_evidence()
        assert after_drain.projection.semantic_hash == after_first_drain.projection.semantic_hash
    finally:
        await second.aclose()

    cold = build()
    try:
        replayed = cold.export_replay_evidence()
    finally:
        await cold.aclose()
    assert replayed.projection.semantic_hash == after_drain.projection.semantic_hash
    assert replayed.replay.semantic_hash == after_drain.replay.semantic_hash
    assert world_author.calls == first_wake_calls
