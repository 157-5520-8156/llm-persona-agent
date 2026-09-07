from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
    compose_fixture_character_purpose,
)

from companion_daemon.world_v2.activity_plan_runtime import (
    ActivityPlanCommand,
    ActivityPlanTransitionCommand,
)
from companion_daemon.world_v2.production_turn_application import (
    LifeEcologyComposition,
    WorldV2TurnApplicationConfig,
)
from companion_daemon.world_v2.world_turn_runtime import InboundTurn

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


class _Identities:
    def resolve(self, *, platform: str, platform_user_id: str) -> tuple[str, str]:
        return f"user:{platform_user_id}", f"user:{platform_user_id}"


class _Router:
    async def route(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("open-world production seam does not deliberate a reply")


class _MainModel:
    async def propose(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("open-world production seam does not deliberate a reply")


class _Transport:
    provider = "platform:test"

    async def send(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("the scenario never drains a chat Action")

    async def lookup(self, **_kwargs):  # type: ignore[no-untyped-def]
        return None


class _OpenWorldModel:
    model = "test-open-world-model"

    async def complete(self, messages, *, temperature: float = 0.4):  # type: ignore[no-untyped-def]
        del temperature
        payload = json.loads(messages[-1]["content"])
        selected = payload["situations"][0]
        return json.dumps(
            {
                "decision": "select",
                "situation_token": selected["token"],
                "moment": "她在活动间隙注意到一处细小变化，顺手记在了心里。",
                "moment_scope": "subjective",
                "user_channel_completion": "none",
            },
            ensure_ascii=False,
        )


class _OutcomeModel:
    model = "test-open-world-outcome-model"
    supports_required_tool_choice = True

    async def complete_json(
        self,
        messages,
        *,
        temperature: float = 0.8,
        tools,
        tool_choice,
    ) -> str:  # type: ignore[no-untyped-def]
        assert tools and tools[0]["function"]["name"] == "character_role_outcome_selection_v1"
        assert tool_choice == {
            "type": "function",
            "function": {"name": "character_role_outcome_selection_v1"},
        }
        return await self.complete(messages, temperature=temperature)

    async def complete(self, messages, *, temperature: float = 0.8):  # type: ignore[no-untyped-def]
        assert temperature == 0.8
        request = json.loads(messages[-1]["content"])
        capability = request["capability_manifest"]
        source_ref = capability["source_refs"][0]
        selected_token = capability["payload"]["offered_tokens"][0]
        return json.dumps(
            {
                "status": "decision",
                "summary": "The lived result now feels settled.",
                "attended_source_refs": [source_ref],
                "decision": {
                    "source_refs": [source_ref],
                    "payload": {
                        "selected_token": selected_token,
                        "adopt_proposed_life_direction": False,
                        "character_life_direction": None,
                    },
                },
                "recall_query": None,
                "proposals": [],
            }
        )


@pytest.mark.asyncio
async def test_production_paid_attention_does_not_bypass_world_authority(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "open-world-seed.yaml"
    seed.write_text(
        """
world_id: open-world-production
life_author_catalog:
  version: open-world-test.1
  story_candidate_role: legacy_replay_and_fixture
  locations: []
  npcs: []
  openings: []
  future_openings: []
  npc_initiated_events: []
  aspiration_seeds: []
""".strip(),
        encoding="utf-8",
    )
    config = WorldV2TurnApplicationConfig(
        world_id="world:open-world-production",
        companion_actor_ref="agent:companion",
        reply_target="user:user.1",
        action_pump_owner="pump:open-world-production",
        character_memory_enabled=False,
        life_ecology=LifeEcologyComposition.production_v1(
            seed_catalog_path=seed
        ),
    )
    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "open-world-production.sqlite",
        config=config,
        identities=_Identities(),
        router=_Router(),
            character_interior=compose_fixture_character_interior(
                inbound_author=_MainModel(),
                purpose_faculties=(
                    compose_fixture_character_purpose(
                        purpose="outcome_selection",
                        provider=_OutcomeModel(),
                    ),
                ),
            ),
        transport=_Transport(),
        open_world_event_model=_OpenWorldModel(),
        now=NOW,
    )
    try:
        await app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id="message:open-world-source",
                text="我去公园走走。",
                observed_at=NOW,
                trace_id="trace:open-world-source",
            )
        )
        source = "observation:test:user.1:message:open-world-source"
        planned = await app.plan_activity(
            ActivityPlanCommand(
                command_id="command:open-world-plan",
                world_id=config.world_id,
                source_observation_id=source,
                plan_id="plan:open-world",
                activity_id="activity:open-world",
                activity_kind="walk",
                importance_bp=4_000,
                location_ref="location:park",
                privacy_class="shareable",
            ),
            logical_time=NOW,
            created_at=NOW,
            trace_id="trace:open-world-plan",
            causation_id=source,
            correlation_id="correlation:open-world",
        )
        started = await app.transition_activity(
            ActivityPlanTransitionCommand(
                command_id="command:open-world-start",
                world_id=config.world_id,
                source_observation_id=source,
                plan_id="plan:open-world",
                operation="start",
            ),
            logical_time=NOW,
            created_at=NOW,
            trace_id="trace:open-world-start",
            causation_id=planned.event_ids[-1],
            correlation_id="correlation:open-world",
        )
        open_world = app._life_ecology._open_world_followup  # noqa: SLF001
        hitch = open_world.commit_from_paid_moment(
            moment="她在公园看见一只猫停了一会儿。",
            wake_event_ref=started.event_ids[-1],
            model="paid-turn:test",
            raw_output="她在公园看见一只猫停了一会儿。",
            trace_id="trace:open-world-hitch",
            correlation_id="correlation:open-world",
        )
        assert hitch.status == "rejected"

        result = await app.advance_life_ecology_once(
            wake_event_ref=started.event_ids[-1],
            trace_id="trace:open-world-wake",
            correlation_id="correlation:open-world",
        )

        assert result.status == "idle"
        assert result.open_world_followup_status == "no_op"
        projection = app._ledger.project()  # noqa: SLF001
        assert projection.world_occurrences == ()
        assert not any(
            item.event.event_type == "ProposalRecorded"
            and item.event.payload().get("proposal_kind") == "open_world_event"
            for item in app._ledger.export_replay_evidence().events  # noqa: SLF001
        )
    finally:
        app.close()
