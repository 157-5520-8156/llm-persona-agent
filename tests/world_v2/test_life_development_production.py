from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
    compose_fixture_character_purpose,
)

from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore,
    StoredLifeContent,
    life_content_payload_hash,
)
from companion_daemon.world_v2.life_development_capability import (
    ProjectionLifeCapabilityManifestCompiler,
)
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.production_turn_application import (
    LifeEcologyComposition,
    WorldV2TurnApplicationConfig,
)
from companion_daemon.world_v2.schemas import (
    CommittedWorldEventRef,
    DueWindow,
    ProjectionCursor,
    WorldEvent,
    WorldPlaceProjection,
)

NOW = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


def _force_life_development_draw(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setattr(
        "companion_daemon.world_v2.life_development_runtime.LifeDevelopmentRuntime._resolve_occasion_draw",
        lambda self, **_kwargs: token,
    )


class _Identities:
    def resolve(self, *, platform: str, platform_user_id: str) -> tuple[str, str]:
        return f"user:{platform_user_id}", f"user:{platform_user_id}"


class _Router:
    async def route(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("life development ecology does not deliberate a reply")


class _MainModel:
    async def propose(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("life development ecology does not deliberate a reply")


class _Transport:
    provider = "platform:test"

    async def send(self, _request):  # type: ignore[no-untyped-def]
        raise AssertionError("life development ecology does not send a chat Action")

    async def lookup(self, **_kwargs):  # type: ignore[no-untyped-def]
        return None


class _NoOpWorldAuthor:
    model = "test-production-world-author"
    semantic_authority_id = "semantic-authority:test:production-world-author"

    def __init__(self, *, forbidden: bool = False) -> None:
        self.calls = 0
        self._forbidden = forbidden

    async def complete(
        self,
        _messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> str:
        del temperature
        self.calls += 1
        if self._forbidden:
            raise AssertionError("cold recovery must not call the World Author")
        return '{"decision":"no_op"}'


class _NeverCharacterModel:
    model = "test-production-character-model"

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
        raise AssertionError("a World Author no_op must not call the Character Model")


class _NoOpDayOpenRole:
    """The same Interior may decline its independent empty-life opportunity."""

    model = "test-production-day-open-role"
    supports_required_tool_choice = True

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        _messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> str:
        raise AssertionError("day-open choice must use the required v2 tool")

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> str:
        del temperature
        self.calls += 1
        tool_name = "character_role_activity_lifecycle_choice_v2"
        assert tools and len(tools) == 1
        assert tools[0]["function"]["name"] == tool_name
        assert tool_choice == {
            "type": "function",
            "function": {"name": tool_name},
        }
        material = json.loads(messages[1]["content"])
        assert material["inner_turn"]["purpose"] == "activity_lifecycle_choice"
        capability = material["capability_manifest"]
        payload = capability["payload"]
        assert payload["contract"] == "character-interior-activity-lifecycle-capability.3"
        assert payload["offered_tokens"] == []
        assert payload["openings"] == []
        assert payload["self_directed_intent"]["execution_scope"] == "self_directed"
        return json.dumps(
            {
                "status": "decision",
                "summary": "我现在不想给自己增加一项安排。",
                "attended_source_refs": capability["source_refs"],
                "decision": {
                    "source_refs": capability["source_refs"],
                    "payload": {"decision": "no_op"},
                },
                "recall_query": None,
                "proposals": [],
            },
            ensure_ascii=False,
        )


class _UnavailableWorldAuthor:
    model = "test-production-unavailable-world-author"
    semantic_authority_id = "semantic-authority:test:production-unavailable-world-author"

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
        raise TimeoutError("test World Author outage")


class _PlanWorldAuthor:
    model = "test-production-plan-world-author"
    semantic_authority_id = "semantic-authority:test:production-plan-world-author"

    def __init__(self, *, wake_event_ref: str) -> None:
        self.calls = 0
        self.requests = []
        self._wake_event_ref = wake_event_ref

    async def complete(
        self,
        _messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> str:
        del temperature
        self.calls += 1
        self.requests.append(_messages)
        return json.dumps(
            {
                "decision": "propose",
                "authored_subject_ref": "actor:companion",
                "causal_authority": "character_choice",
                "outcome_resolution_authority": "world_contingency",
                "premise_scope": "external_opportunity",
                "premise": "公园今天临时有一小段露天电影。",
                "premise_claim_refs": ["local:claim:screening"],
                "claim_declarations": [
                    {
                        "claim_id": "local:claim:screening",
                        "summary": "公园存在一场可以自由参加的临时露天电影。",
                        "scope": "novel_world_generation",
                        "subject_scope": "world_environment",
                        "source_refs": [],
                    }
                ],
                "timing": {"mode": "now", "duration_minutes": 60},
                "anchor_refs": [self._wake_event_ref],
                "location_ref": None,
                "entity_refs": [],
                "privacy_class": "shareable",
                "outcomes": [
                    {
                        "experienced_by_ref": "actor:companion",
                        "text": "电影放完时风有点凉，人群慢慢散了。",
                        "privacy_class": "shareable",
                        "relative_plausibility_weight": 1,
                        "claim_refs": ["local:claim:screening"],
                        "provisional_npcs": [
                            {
                                "local_ref": "local:npc:screening-organizer",
                                "summary": "负责收放映设备的活动组织者。",
                                "narrative_tags": ["narrative:outdoor-film"],
                                "privacy_class": "personal",
                            }
                        ],
                        "dynamic_life_direction": None,
                    },
                    {
                        "experienced_by_ref": "actor:companion",
                        "text": "中途下了一点小雨，放映比预计早结束。",
                        "privacy_class": "shareable",
                        "relative_plausibility_weight": 2,
                        "claim_refs": ["local:claim:screening"],
                        "provisional_npcs": [
                            {
                                "local_ref": "local:npc:screening-organizer",
                                "summary": "负责收放映设备的活动组织者。",
                                "narrative_tags": ["narrative:outdoor-film"],
                                "privacy_class": "personal",
                            }
                        ],
                        "dynamic_life_direction": None,
                    },
                ],
            },
            ensure_ascii=False,
        )


class _AcceptCharacterModel:
    model = "test-production-accept-character"

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
        return json.dumps(
            {
                "decision": "accept",
                "intention_summary": "我想带杯水去后排坐一会儿。",
                "importance_bp": 4300,
                "opens_at": (NOW + timedelta(minutes=10)).isoformat(),
                "closes_at": (NOW + timedelta(minutes=70)).isoformat(),
                "participant_refs": [],
            },
            ensure_ascii=False,
        )


class _SupportingSourceReviewer:
    model = "test-production-independent-life-source-reviewer"
    semantic_authority_id = (
        "semantic-authority:test:production-independent-life-source-reviewer"
    )

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
    ) -> str:
        del temperature
        if "focused novel-origin critic" in messages[0]["content"]:
            return json.dumps(
                {
                    "decision": "supported",
                    "unsupported_claims": [],
                    "unsupported_provisional_npcs": [],
                    "unsupported_outcome_prerequisites": [],
                    "undeclared_premise_fragments": [],
                    "reason": "No prior history or imported prerequisite is present.",
                }
            )
        return json.dumps(
            {
                "decision": "supported",
                "unsupported_claim_ids": [],
                "undeclared_fact_fragments": [],
                "undeclared_fact_paths": [],
                "typed_location_conflicts": [],
                "reason": "Proposal-scoped novel facts are declared and source-closed.",
            }
        )


class _SelectFirstLifecycle:
    model = "test-production-select-lifecycle"
    supports_required_tool_choice = True

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> str:
        assert tools and len(tools) == 1
        assert tools[0]["function"]["name"] == (
            "character_role_activity_lifecycle_choice_v1"
        )
        assert tool_choice == {
            "type": "function",
            "function": {"name": "character_role_activity_lifecycle_choice_v1"},
        }
        return await self.complete(messages, temperature=temperature)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> str:
        del temperature
        material = json.loads(messages[1]["content"])
        capability = material["capability_manifest"]
        token = capability["payload"]["openings"][0]["opening_token"]
        return json.dumps(
            {
                "status": "decision",
                "summary": "我现在想开始这项已经安排好的活动。",
                "attended_source_refs": [],
                "decision": {
                    "source_refs": capability["source_refs"],
                    "payload": {
                        "decision": "select",
                        "selected_token": token,
                    },
                },
                "recall_query": None,
                "proposals": [],
            },
            ensure_ascii=False,
        )


def _open_life_seed(path: Path, *, role: bool = True) -> Path:
    path.write_text(
        f"""
world_id: open-life-production
life_author_catalog:
  version: open-life.1
  {"story_candidate_role: legacy_replay_and_fixture" if role else ""}
  locations:
    - id: public-park
      location_ref: location:public-park
      privacy: shareable
      local_windows: ["06:00-23:00"]
      weekdays: [0, 1, 2, 3, 4, 5, 6]
  npcs: []
  openings: []
  future_openings: []
  npc_initiated_events: []
  aspiration_seeds: []
""".strip(),
        encoding="utf-8",
    )
    return path


def _wake() -> WorldEvent:
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:clock:open-life",
        event_type="ClockAdvanced",
        world_id="world:open-life-production",
        logical_time=NOW,
        created_at=NOW,
        actor="system:clock",
        source="test",
        trace_id="trace:open-life",
        causation_id="scheduler:open-life",
        correlation_id="correlation:open-life",
        idempotency_key="clock:open-life",
        payload={"tick_id": "open-life"},
    )


def test_projection_manifest_compiler_exposes_facts_and_affordances_without_story_candidates(
    tmp_path: Path,
) -> None:
    wake = _wake()
    catalog = ReviewedLifeSeedCatalog.from_yaml(
        path=_open_life_seed(tmp_path / "open-life.yaml"),
        chronology=LocalChronology("Asia/Shanghai"),
    )
    place_summary = "她在一次已结算经历里发现的临河旧书摊。"
    place_summary_hash = life_content_payload_hash(place_summary)
    content_store = InMemoryImmutableLifeContentStore()
    content_store.put_if_absent(
        StoredLifeContent(
            content_ref="content:place:user-mentioned-shop",
            content_kind="provisional_place_introduction",
            content_payload_hash=place_summary_hash,
            text=place_summary,
        )
    )
    projection = SimpleNamespace(
        world_revision=7,
        deliberation_revision=3,
        ledger_sequence=11,
        logical_time=NOW,
        committed_world_event_refs=(
            CommittedWorldEventRef(
                event_id=wake.event_id,
                event_type=wake.event_type,
                world_revision=7,
                payload_hash=wake.payload_hash,
                logical_time=wake.logical_time,
            ),
        ),
        life_arcs=(),
        world_places=(
            WorldPlaceProjection(
                location_ref="location:open-life:" + "a" * 64,
                stable_identity_ref="content:place:user-mentioned-shop",
                summary_payload_hash=place_summary_hash,
                narrative_tags=("narrative:user_influence",),
                timezone_name="Asia/Shanghai",
                privacy_class="personal",
                access_assurance="attempt_only",
                source_event_ref=wake.event_id,
                effect_descriptor_hash="b" * 64,
                accepted_at=NOW,
            ),
        ),
        npcs=(
            SimpleNamespace(npc_id="friend", status="active", privacy_class="personal"),
            SimpleNamespace(npc_id="acquaintance", status="active", privacy_class="private"),
            SimpleNamespace(npc_id="retired", status="retired", privacy_class="withhold"),
        ),
        plans=(
            SimpleNamespace(
                owner_actor_ref="actor:companion",
                status="active",
                location_ref="location:current-cafe",
                evidence_refs=(),
                scheduled_window=None,
            ),
        ),
        locations=(
            SimpleNamespace(
                actor_ref="actor:companion",
                values=SimpleNamespace(
                    location_ref="location:current-cafe",
                    privacy_class="personal",
                    since=NOW - timedelta(hours=1),
                ),
                origin=SimpleNamespace(
                    accepted_event_ref="event:location:current-cafe",
                ),
            ),
        ),
    )
    capsule = SimpleNamespace(
        model_content_json=json.dumps(
            {"sources": [{"source_event_ref": wake.event_id}]},
            separators=(",", ":"),
        ),
        current_situation=SimpleNamespace(
            availability="available",
            source_refs=(wake.event_id,),
        ),
    )
    compiler = ProjectionLifeCapabilityManifestCompiler(
        owner_actor_ref="actor:companion",
        catalog=catalog,
        content_store=content_store,
    )

    manifest = compiler.compile(
        projection=projection,
        wake=wake,
        capsule=capsule,
    )

    assert manifest.pinned_cursor == ProjectionCursor(
        world_revision=7,
        deliberation_revision=3,
        ledger_sequence=11,
    )
    assert manifest.anchor_refs == (wake.event_id,)
    assert manifest.grounding_refs == (
        wake.event_id,
        "event:location:current-cafe",
        "policy:life-author-catalog:open-life.1:a0f25517add404cc466ad56f0bbd980db4b848c48ae5bf2959eb873622cbde92",
    )
    assert manifest.location_refs == (
        "location:current-cafe",
        "location:open-life:" + "a" * 64,
        "location:public-park",
    )
    current_presence = next(
        item
        for item in manifest.location_capabilities
        if item.location_ref == "location:current-cafe"
        and item.availability_kind == "current_presence"
    )
    assert current_presence.available_from == NOW - timedelta(hours=1)
    assert current_presence.available_to == NOW + timedelta(minutes=5)
    assert current_presence.authority_refs == (
        "event:clock:open-life",
        "event:location:current-cafe",
    )
    dynamic_place = next(
        item
        for item in manifest.location_capabilities
        if item.location_ref == "location:open-life:" + "a" * 64
    )
    assert dynamic_place.availability_kind == "settled_place"
    assert dynamic_place.identity_content_ref == "content:place:user-mentioned-shop"
    assert dynamic_place.identity_summary == place_summary
    assert dynamic_place.identity_payload_hash == place_summary_hash
    assert dynamic_place.authorizes(
        timing_mode="later",
        window=DueWindow(
            opens_at=NOW + timedelta(days=2),
            closes_at=NOW + timedelta(days=2, hours=1),
        ),
    )
    assert manifest.entity_refs == ("npc:acquaintance", "npc:friend")
    assert manifest.model_dump(mode="json")["npc_privacy_floors"] == [
        {"npc_ref": "npc:acquaintance", "privacy_class": "private"},
        {"npc_ref": "npc:friend", "privacy_class": "personal"},
    ]

    without_identity = ProjectionLifeCapabilityManifestCompiler(
        owner_actor_ref="actor:companion",
        catalog=catalog,
        content_store=InMemoryImmutableLifeContentStore(),
    ).compile(
        projection=projection,
        wake=wake,
        capsule=capsule,
    )
    assert "location:open-life:" + "a" * 64 not in without_identity.location_refs


@pytest.mark.asyncio
async def test_production_open_life_no_op_is_effect_once_across_cold_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from companion_daemon.world_v2.life_development_runtime import (
        LIFE_DEVELOPMENT_NOTHING_REF,
    )

    _force_life_development_draw(monkeypatch, LIFE_DEVELOPMENT_NOTHING_REF)
    database = tmp_path / "open-life-production.sqlite"
    seed = _open_life_seed(tmp_path / "production-seed.yaml")
    config = WorldV2TurnApplicationConfig(
        world_id="world:open-life-production",
        companion_actor_ref="actor:companion",
        reply_target="user:user.1",
        action_pump_owner="pump:open-life-production",
        character_memory_enabled=False,
        life_ecology=LifeEcologyComposition.production_v1(seed_catalog_path=seed),
    )
    world_author = _NoOpWorldAuthor()
    character_model = _NeverCharacterModel()
    day_open_role = _NoOpDayOpenRole()
    app = build_sqlite_world_v2_test_application(
        path=database,
        config=config,
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
        now=NOW,
    )
    wake_event_ref = "event:trigger:clock:open-life-production"
    try:
        await app.tick(
            tick_id="open-life-production",
            logical_time_from=NOW,
            logical_time_to=NOW.replace(minute=10),
            observed_at=NOW.replace(minute=10),
            trace_id="trace:open-life-production",
            causation_id="scheduler:open-life-production",
            correlation_id="correlation:open-life-production",
            reason="open-life-production",
            run_life_ecology=False,
        )
        first = await app.advance_life_ecology_once(
            wake_event_ref=wake_event_ref,
            trace_id="trace:open-life-production",
            correlation_id="correlation:open-life-production",
        )
        assert first.status == "idle"
        assert first.life_development_followup_status == "no_op"
        # The World Author is asked and may answer no_op; what must not happen
        # is a deterministic no_op that never asks.
        assert world_author.calls == 1
        assert character_model.calls == 0
        assert day_open_role.calls == 1
    finally:
        app.close()

    recovered_world_author = _NoOpWorldAuthor(forbidden=True)
    recovered_character_model = _NeverCharacterModel()
    recovered_day_open_role = _NoOpDayOpenRole()
    reopened = build_sqlite_world_v2_test_application(
        path=database,
        config=config,
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_MainModel(),
            purpose_faculties=(
                compose_fixture_character_purpose(
                    purpose="life_development_choice",
                    provider=recovered_character_model,
                ),
                compose_fixture_character_purpose(
                    purpose="activity_lifecycle_choice",
                    provider=recovered_day_open_role,
                ),
            ),
        ),
        transport=_Transport(),
        life_world_author_model=recovered_world_author,
        now=NOW.replace(minute=10),
    )
    try:
        recovered = await reopened.advance_life_ecology_once(
            wake_event_ref=wake_event_ref,
            trace_id="trace:open-life-production-recovery",
            correlation_id="correlation:open-life-production",
        )
        assert recovered.status == "joined_existing"
        assert recovered.reason_code == "life_ecology.run_completed"
        assert recovered.life_development_followup_status is None
        assert recovered_world_author.calls == 0
        assert recovered_character_model.calls == 0
        assert recovered_day_open_role.calls == 0
    finally:
        reopened.close()


def test_production_open_life_refuses_an_unmarked_legacy_story_catalog(
    tmp_path: Path,
) -> None:
    seed = _open_life_seed(tmp_path / "unmarked-seed.yaml", role=False)
    config = WorldV2TurnApplicationConfig(
        world_id="world:open-life-production",
        companion_actor_ref="actor:companion",
        reply_target="user:user.1",
        action_pump_owner="pump:open-life-production",
        character_memory_enabled=False,
        life_ecology=LifeEcologyComposition.production_v1(seed_catalog_path=seed),
    )

    with pytest.raises(ValueError, match="legacy_replay_and_fixture"):
        build_sqlite_world_v2_test_application(
            path=tmp_path / "unmarked.sqlite",
            config=config,
            identities=_Identities(),
            router=_Router(),
            character_interior=compose_fixture_character_interior(
                inbound_author=_MainModel(),
                purpose_faculties=(
                    compose_fixture_character_purpose(
                        purpose="life_development_choice",
                        provider=_NeverCharacterModel(),
                    ),
                ),
            ),
            transport=_Transport(),
            life_world_author_model=_NoOpWorldAuthor(),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_production_open_life_defers_when_the_world_author_is_unavailable(
    tmp_path: Path,
) -> None:
    """An unreachable author is a technical failure, not a quiet nothing."""

    database = tmp_path / "open-life-retry.sqlite"
    seed = _open_life_seed(tmp_path / "retry-seed.yaml")
    config = WorldV2TurnApplicationConfig(
        world_id="world:open-life-production",
        companion_actor_ref="actor:companion",
        reply_target="user:user.1",
        action_pump_owner="pump:open-life-production",
        character_memory_enabled=False,
        life_ecology=LifeEcologyComposition.production_v1(seed_catalog_path=seed),
    )
    world_author = _UnavailableWorldAuthor()
    day_open_role = _NoOpDayOpenRole()
    app = build_sqlite_world_v2_test_application(
        path=database,
        config=config,
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_MainModel(),
            purpose_faculties=(
                compose_fixture_character_purpose(
                    purpose="life_development_choice",
                    provider=_NeverCharacterModel(),
                ),
                compose_fixture_character_purpose(
                    purpose="activity_lifecycle_choice",
                    provider=day_open_role,
                ),
            ),
        ),
        transport=_Transport(),
        life_world_author_model=world_author,
        now=NOW,
    )
    try:
        await app.tick(
            tick_id="open-life-retry-first",
            logical_time_from=NOW,
            logical_time_to=NOW.replace(minute=10),
            observed_at=NOW.replace(minute=10),
            trace_id="trace:open-life-retry-first",
            causation_id="scheduler:open-life-retry",
            correlation_id="correlation:open-life-retry",
            reason="open-life-retry",
            run_life_ecology=False,
        )
        first = await app.advance_life_ecology_once(
            wake_event_ref="event:trigger:clock:open-life-retry-first",
            trace_id="trace:open-life-retry-first",
            correlation_id="correlation:open-life-retry",
        )
        assert first.status == "deferred"
        assert first.life_development_followup_status != "no_op"
        assert world_author.calls >= 1
        assert day_open_role.calls == 1
    finally:
        app.close()


@pytest.mark.asyncio
async def test_production_open_life_opportunity_draw_calls_world_author(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from companion_daemon.world_v2.life_development_runtime import (
        LIFE_DEVELOPMENT_OPPORTUNITY_REF,
    )

    _force_life_development_draw(monkeypatch, LIFE_DEVELOPMENT_OPPORTUNITY_REF)
    database = tmp_path / "open-life-opportunity.sqlite"
    seed = _open_life_seed(tmp_path / "opportunity-seed.yaml")
    config = WorldV2TurnApplicationConfig(
        world_id="world:open-life-production",
        companion_actor_ref="actor:companion",
        reply_target="user:user.1",
        action_pump_owner="pump:open-life-production",
        character_memory_enabled=False,
        life_ecology=LifeEcologyComposition.production_v1(seed_catalog_path=seed),
    )
    world_author = _NoOpWorldAuthor()
    day_open_role = _NoOpDayOpenRole()
    app = build_sqlite_world_v2_test_application(
        path=database,
        config=config,
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_MainModel(),
            purpose_faculties=(
                compose_fixture_character_purpose(
                    purpose="life_development_choice",
                    provider=_NeverCharacterModel(),
                ),
                compose_fixture_character_purpose(
                    purpose="activity_lifecycle_choice",
                    provider=day_open_role,
                ),
            ),
        ),
        transport=_Transport(),
        life_world_author_model=world_author,
        now=NOW,
    )
    try:
        await app.tick(
            tick_id="open-life-opportunity",
            logical_time_from=NOW,
            logical_time_to=NOW.replace(minute=10),
            observed_at=NOW.replace(minute=10),
            trace_id="trace:open-life-opportunity",
            causation_id="scheduler:open-life-opportunity",
            correlation_id="correlation:open-life-opportunity",
            reason="open-life-opportunity",
            run_life_ecology=False,
        )
        first = await app.advance_life_ecology_once(
            wake_event_ref="event:trigger:clock:open-life-opportunity",
            trace_id="trace:open-life-opportunity",
            correlation_id="correlation:open-life-opportunity",
        )
        assert first.life_development_followup_status == "no_op"
        assert world_author.calls >= 1
        assert day_open_role.calls == 1
    finally:
        app.close()


@pytest.mark.asyncio
async def test_production_open_life_plan_comes_from_the_world_author(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the production.2 historical Plan producer and original wire.

    The plan that lands must be the one the World Author actually wrote, and
    she has to accept it; nothing may appear from the catalog alone.
    """

    class LegacyManifestCompiler(ProjectionLifeCapabilityManifestCompiler):
        def compile(self, **kwargs):
            # This fixture deliberately exercises its original text contract.
            # Fresh production requests are covered separately through HTTP.
            return super().compile(**kwargs).model_copy(
                update={
                    "version": "life-development-capability.production.2",
                    "outcome_contract": None,
                    "execution_intention_sources_version": None,
                    "pinned_source_materials_version": None,
                }
            )

    monkeypatch.setattr(
        "companion_daemon.world_v2.production_turn_application.ProjectionLifeCapabilityManifestCompiler",
        LegacyManifestCompiler,
    )

    database = tmp_path / "open-life-dynamic-aftermath.sqlite"
    seed = _open_life_seed(tmp_path / "dynamic-aftermath-seed.yaml")
    config = WorldV2TurnApplicationConfig(
        world_id="world:open-life-production",
        companion_actor_ref="actor:companion",
        reply_target="user:user.1",
        action_pump_owner="pump:open-life-production",
        character_memory_enabled=False,
        life_ecology=LifeEcologyComposition.production_v1(seed_catalog_path=seed),
    )
    first_wake = "event:trigger:clock:open-life-plan"
    world_author = _PlanWorldAuthor(wake_event_ref=first_wake)
    character_model = _AcceptCharacterModel()
    app = build_sqlite_world_v2_test_application(
        path=database,
        config=config,
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
                    provider=_SelectFirstLifecycle(),
                ),
            ),
        ),
        transport=_Transport(),
        life_world_author_model=world_author,
        life_source_closure_reviewer=_SupportingSourceReviewer(),
        now=NOW,
    )
    try:
        await app.tick(
            tick_id="open-life-plan",
            logical_time_from=NOW,
            logical_time_to=NOW + timedelta(minutes=10),
            observed_at=NOW + timedelta(minutes=10),
            trace_id="trace:open-life-plan",
            causation_id="scheduler:open-life-plan",
            correlation_id="correlation:open-life-plan",
            reason="open-life-plan",
            run_life_ecology=False,
        )
        planned = await app.advance_life_ecology_once(
            wake_event_ref=first_wake,
            trace_id="trace:open-life-plan",
            correlation_id="correlation:open-life-plan",
        )
        assert planned.life_development_followup_status == "plan_committed"
        assert world_author.calls >= 1
        manifest = json.loads(world_author.requests[0][1]["content"])["capability_manifest"]
        # This fresh legacy-text fixture uses today's source-bound manifest
        # compiler. Historical persisted request bytes are frozen separately.
        assert manifest["manifest_hash"] == (
            "a5d9e0edfb1998d1e9f4d26d7079aa872be8765a3ffea1a9de167532af60f53b"
        )
        assert hashlib.sha256(
            json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest() == "362670dd45f2567f2da560e61b8c7472eca5d3fee10f332a47110d7a1afc8d8a"
        assert character_model.calls >= 1
        assert any(
            item.activity_kind.startswith("open_life.")
            for item in app._ledger.project().plans  # noqa: SLF001
        )
    finally:
        app.close()
