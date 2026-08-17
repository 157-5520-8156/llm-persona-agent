from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import companion_daemon.world_v2.dashboard_home_snapshot as snapshot_module
from companion_daemon.world_v2.audited_change_terminal import (
    RELATIONSHIP_COMMITMENT_TERMINAL_REASON,
    audited_change_terminal_event_id,
)
from companion_daemon.world_v2.dashboard_home_snapshot import (
    DASHBOARD_LEDGER_FIELD_POLICY,
    DashboardHomeSnapshotModule,
    DashboardRuntimeObservation,
    DashboardRuntimeReason,
)
from companion_daemon.world_v2.audited_proposal_settlement import (
    settle_terminal_audited_change,
)
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.schemas import (
    CommitResult,
    LedgerProjection,
    LegacyExperienceEvidenceRef,
    LegacyExperienceProjection,
)

from test_relationship_commitment_compiler import _compiler_fixture


CAPTURED_AT = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)
WORLD_ID = "world:dashboard-home"


def _runtime_observation(**changes: object) -> DashboardRuntimeObservation:
    return DashboardRuntimeObservation(
        observed_at=CAPTURED_AT,
        scheduler_state="ready",
        character_interior_state="ready",
        local_provider_capacity_state="ready",
        text_endpoint_state="ready",
        proactive_source_authority_state="ready",
        life_source_authority_state="ready",
        external_perception_upstream_state="ready",
        model_usage_budget_state="ready",
        process_latency_state="ready",
        storage_state="ready",
        expression_episode_state="disabled",
        expression_episode_mode="off",
        semantic_recall_state="ready",
        semantic_embedding_enabled=True,
    ).model_copy(update=changes)


def test_runtime_contract_preserves_busy_and_rejects_unclassified_reason_text() -> None:
    busy = _runtime_observation(local_provider_capacity_state="busy")

    assert busy.local_provider_capacity_state == "busy"
    with pytest.raises(ValidationError):
        DashboardRuntimeReason(
            signal="storage",
            reason_code="/private/secret.sqlite: connection failed",
        )


def test_dashboard_field_policy_requires_an_explicit_decision_for_every_projection_field() -> None:
    assert set(DASHBOARD_LEDGER_FIELD_POLICY) == set(LedgerProjection.model_fields)
    assert DASHBOARD_LEDGER_FIELD_POLICY["semantic_hash"].exposure == (
        "intentionally_withheld"
    )
    assert DASHBOARD_LEDGER_FIELD_POLICY["proposal_audits"].exposure == "count_only"
    assert DASHBOARD_LEDGER_FIELD_POLICY["private_impressions"].exposure == "typed_summary"
    assert DASHBOARD_LEDGER_FIELD_POLICY["facts"].exposure == "typed_summary"


@pytest.mark.asyncio
async def test_empty_owner_snapshot_is_stable_and_never_invents_room_presence() -> None:
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    module = DashboardHomeSnapshotModule(
        ledger=ledger,
        deployment_id="deployment:test",
        boot_id="boot:test",
        clock=lambda: CAPTURED_AT,
    )

    first = await module.capture()
    second = await module.capture()

    assert first.to_payload() == second.to_payload()
    payload = first.to_payload()
    assert payload["schema_version"] == "world-v2-dashboard-home.1"
    assert payload["policy_version"] == "dashboard-owner-policy.1"
    assert payload["owner"] == {
        "deployment_id": "deployment:test",
        "boot_id": "boot:test",
    }
    assert payload["world_id"] == WORLD_ID
    assert payload["generated_at"] == "2026-08-12T01:00:00Z"
    assert payload["cursor"] == {
        "world_revision": 0,
        "deliberation_revision": 0,
        "ledger_sequence": 0,
    }
    assert set(payload["sections"]) == {
        "room",
        "overview_life",
        "facts_memory_inner",
        "relationship_lifecycle",
        "operations",
        "perception_media",
        "authority_privacy",
        "ledger_qualification",
        "runtime_operations",
    }
    assert payload["sections"]["room"] == {
        "label": "房间",
        "state": "unavailable",
        "source": "ledger",
        "observed_at": None,
        "cursor": payload["cursor"],
        "coverage": {
            "known_count": 0,
            "included_count": 0,
            "truncated": False,
        },
        "reason_code": "world_not_initialized",
        "render_state": {
            "protocol": "pixel-home-state.2",
            "route": {
                "scene_id": "unavailable",
                "action_id": "idle",
                "availability": "unavailable",
            },
            "logical_time": None,
        },
    }
    for name in (
        "overview_life",
        "facts_memory_inner",
        "relationship_lifecycle",
        "operations",
        "perception_media",
        "authority_privacy",
        "ledger_qualification",
    ):
        assert payload["sections"][name]["state"] == "empty"
        assert payload["sections"][name]["label"]
        assert payload["sections"][name]["cursor"] == payload["cursor"]
        assert payload["sections"][name]["coverage"] == {
            "known_count": 0,
            "included_count": 0,
            "truncated": False,
        }
    assert payload["sections"]["runtime_operations"]["label"] == "运行时状态"
    assert payload["sections"]["runtime_operations"]["state"] == "unavailable"
    assert payload["sections"]["runtime_operations"]["reason_code"] == (
        "runtime_observation_not_supplied"
    )
    assert len(first.snapshot_hash) == 64
    assert ledger.project().ledger_sequence == 0


@pytest.mark.asyncio
async def test_room_fails_closed_for_mixed_privacy_duplicate_heads() -> None:
    base = WorldLedger.in_memory(world_id=WORLD_ID).project()
    public_location = SimpleNamespace(
        actor_ref="actor:companion",
        updated_at=CAPTURED_AT,
        values=SimpleNamespace(
            location_ref="location:ecnu-dorm-room",
            privacy_class="shareable",
            scene_visibility="shareable",
        ),
    )
    private_location = SimpleNamespace(
        actor_ref="actor:companion",
        updated_at=CAPTURED_AT,
        values=SimpleNamespace(
            location_ref="location:somewhere-private",
            privacy_class="private",
            scene_visibility="private",
        ),
    )
    public_plan = SimpleNamespace(
        plan_id="plan:public",
        owner_actor_ref="actor:companion",
        activity_kind="study.focused_reading",
        status="active",
        privacy_class="shareable",
        location_ref="location:ecnu-dorm-room",
        importance_bp=9000,
        last_transitioned_at=CAPTURED_AT,
    )
    private_plan = SimpleNamespace(
        plan_id="plan:private",
        owner_actor_ref="actor:companion",
        activity_kind="shared.movie_call",
        status="active",
        privacy_class="private",
        location_ref="location:somewhere-private",
        importance_bp=9100,
        last_transitioned_at=CAPTURED_AT,
    )
    projection = base.model_copy(
        update={
            "logical_time": CAPTURED_AT,
            "locations": (public_location, private_location),
            "plans": (public_plan, private_plan),
        }
    )

    class _Ledger:
        world_id = WORLD_ID
        blocks_event_loop = False

        def project(self):  # type: ignore[no-untyped-def]
            return projection

        def lookup_event_commit(self, _event_id):  # type: ignore[no-untyped-def]
            return None

    room = (
        await DashboardHomeSnapshotModule(
            ledger=_Ledger(),  # type: ignore[arg-type]
            deployment_id="deployment:test",
            boot_id="boot:test",
            clock=lambda: CAPTURED_AT,
        ).capture()
    ).to_payload()["sections"]["room"]

    assert room["state"] == "unavailable"
    assert room["render_state"]["route"]["availability"] == "unavailable"


@pytest.mark.asyncio
async def test_runtime_operations_require_typed_observation_and_change_cache_key() -> None:
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    module = DashboardHomeSnapshotModule(
        ledger=ledger,
        deployment_id="deployment:test",
        boot_id="boot:test",
        clock=lambda: CAPTURED_AT,
    )

    without_runtime = await module.capture()
    with_runtime = await module.capture(
        DashboardRuntimeObservation(
            observed_at=CAPTURED_AT,
            scheduler_state="ready",
            character_interior_state="ready",
            local_provider_capacity_state="degraded",
            text_endpoint_state="unavailable",
            proactive_source_authority_state="ready",
            life_source_authority_state="ready",
            external_perception_upstream_state="warming",
            model_usage_budget_state="ready",
            process_latency_state="ready",
            storage_state="ready",
            expression_episode_state="disabled",
            expression_episode_mode="off",
            semantic_recall_state="ready",
            semantic_embedding_enabled=True,
            reasons=(
                DashboardRuntimeReason(
                    signal="text_endpoint",
                    reason_code="primary_timeout",
                ),
            ),
        )
    )

    section = with_runtime.to_payload()["sections"]["runtime_operations"]
    assert without_runtime.snapshot_hash != with_runtime.snapshot_hash
    assert section["state"] == "ready"
    assert section["reason_code"] is None
    assert section["data"]["signals"] == [
        {"key": "scheduler", "label": "时钟还在走", "state": "ready", "state_label": "正常"},
        {
            "key": "character_interior",
            "label": "她自己的判断",
            "state": "ready",
            "state_label": "正常",
        },
        {
            "key": "local_provider_capacity",
            "label": "本地小模型",
            "state": "degraded",
            "state_label": "降级",
        },
        {
            "key": "text_endpoint",
            "label": "对话入口",
            "state": "unavailable",
            "state_label": "不可用",
        },
        {
            "key": "proactive_source_authority",
            "label": "主动找你这条路",
            "state": "ready",
            "state_label": "正常",
        },
        {
            "key": "life_source_authority",
            "label": "生活事件这条路",
            "state": "ready",
            "state_label": "正常",
        },
        {
            "key": "external_perception_upstream",
            "label": "外面的新闻天气",
            "state": "warming",
            "state_label": "预热中",
        },
        {
            "key": "model_usage_budget",
            "label": "模型预算",
            "state": "ready",
            "state_label": "正常",
        },
        {
            "key": "process_latency",
            "label": "这一下快不快",
            "state": "ready",
            "state_label": "正常",
        },
        {"key": "storage", "label": "账本存储", "state": "ready", "state_label": "正常"},
        {
            "key": "expression_episode",
            "label": "说话节奏",
            "state": "disabled",
            "state_label": "未启用",
        },
        {
            "key": "semantic_recall",
            "label": "记得的事",
            "state": "ready",
            "state_label": "正常",
        },
    ]
    assert section["data"]["notice_count"] == 1
    assert section["data"]["notices"] == [
        {
            "signal": "text_endpoint",
            "signal_label": "对话入口",
            "reason_code": "primary_timeout",
            "reason_label": "主文本请求超时",
        }
    ]
    assert section["data"]["expression_episode"] == {
        "mode": "off",
        "mode_label": "说话节奏未启用",
    }
    assert section["data"]["semantic_recall"] == {
        "semantic_embedding_enabled": True,
        "semantic_embedding_label": "语义嵌入已启用",
    }


@pytest.mark.asyncio
async def test_nonempty_projection_compiles_every_section_without_raw_sensitive_material() -> None:
    secret = "secret-marker:prompt:provider:raw-payload:evidence-hash"
    base = WorldLedger.in_memory(world_id=WORLD_ID).project()
    plan = SimpleNamespace(
        plan_id="plan:study",
        owner_actor_ref="actor:companion",
        activity_kind="study.focused_reading",
        status="active",
        privacy_class="shareable",
        location_ref="location:ecnu-dorm-room",
        importance_bp=8100,
        last_transitioned_at=CAPTURED_AT,
    )
    location = SimpleNamespace(
        actor_ref="actor:companion",
        updated_at=CAPTURED_AT,
        values=SimpleNamespace(
            location_ref="location:ecnu-dorm-room",
            privacy_class="shareable",
            scene_visibility="shareable",
        ),
    )
    fact = SimpleNamespace(
        fact_id=secret,
        committed_at=CAPTURED_AT,
        updated_at=CAPTURED_AT,
        values=SimpleNamespace(
            predicate_code="likes_drink",
            value_ref=secret,
            status="active",
            privacy_class="private",
            confidence_bp=9300,
        ),
    )
    withheld_fact = SimpleNamespace(
        fact_id="fact:withheld",
        committed_at=CAPTURED_AT,
        updated_at=CAPTURED_AT,
        values=SimpleNamespace(
            predicate_code=secret,
            value_ref=secret,
            status=secret,
            privacy_class="withhold",
            confidence_bp=9300,
        ),
    )
    npc = SimpleNamespace(
        npc_id=secret,
        status="active",
        privacy_class="private",
        known_trait_refs=("trait:patient",),
        subjective_state=None,
    )
    private_impression = SimpleNamespace(
        impression_id="impression:withheld",
        subject_ref="user:owner",
        status="active",
        reflection_summary="说话有点急，但不是故意的。",
        last_supported=CAPTURED_AT,
        confidence_bp=7400,
    )
    relationship_state = SimpleNamespace(
        relationship_id=secret,
        subject_ref=secret,
        stage="acquaintance",
        last_adjusted_at=CAPTURED_AT,
        variables=SimpleNamespace(
            trust_bp=5000,
            closeness_bp=5000,
            respect_bp=5000,
            reliability_bp=5000,
            mutuality_bp=5000,
            repair_confidence_bp=5000,
        ),
    )
    boundary = SimpleNamespace(
        boundary_id=secret,
        scope_ref=secret,
        status="active",
        updated_at=CAPTURED_AT,
        strength_bp=7000,
    )
    commitment = SimpleNamespace(
        commitment_id=secret,
        updated_at=CAPTURED_AT,
        values=SimpleNamespace(
            content_ref=secret,
            status="active",
            privacy_class="private",
            importance_bp=7600,
        ),
    )
    action = SimpleNamespace(
        action_id=secret,
        kind="proactive_message",
        state="failed",
        created_at=CAPTURED_AT,
        layer="external_action",
        dispatch_pending=False,
        payload_ref=secret,
        payload_hash=secret,
    )
    signal = SimpleNamespace(
        snapshot_ref="external-snapshot:1",
        headline="校园展览本周开幕",
        licensed_summary="一个可公开查看的展览摘要",
        occurred_at=CAPTURED_AT,
        published_at=CAPTURED_AT,
        observed_at=CAPTURED_AT,
        may_quote=True,
        may_expose_to_character_model=True,
        model_visible_material_json=secret,
        source_payload_hash=secret,
    )
    capability = SimpleNamespace(
        grant_id=secret,
        updated_at=CAPTURED_AT,
        values=SimpleNamespace(
            capability_kind="reply",
            state="active",
            target_scope_refs=("user:owner",),
        ),
    )
    receipt = SimpleNamespace(
        receipt_id="receipt:failed",
        receipt_kind="provider_result",
        observed_state="failed",
        error_class=secret,
        received_at=CAPTURED_AT,
        is_terminal=True,
    )
    retry = SimpleNamespace(
        source_event_ref="event:life-retry",
        lane="life",
        failure_code=secret,
        failed_at=CAPTURED_AT,
        retry_ordinal=1,
        consecutive_technical_failures=1,
    )
    inspection = SimpleNamespace(
        inspection_id="inspection:failed",
        passed=False,
        reason_code=secret,
        repairable=False,
    )
    budget_account = SimpleNamespace(
        account_id="budget:chat",
        category="chat",
        limit=100,
        reserved=20,
        spent=30,
        overrun=0,
    )
    budget_reservation = SimpleNamespace(
        reservation_id="reservation:chat",
        category="chat",
        state="reserved",
        amount_limit=20,
        settled_cost=0,
    )
    budget_settlement = SimpleNamespace(
        settlement_id="settlement:chat",
        settlement_kind="terminal",
        state="settled",
        previous_cost=0,
        cost_actual=10,
        cost_delta=10,
    )
    reconciliation = SimpleNamespace(
        reconciliation_id="reconciliation:chat",
        reason="terminal_conflict",
        observed_state="failed",
        existing_state="delivered",
    )
    projection = base.model_copy(
        update={
            "world_revision": 7,
            "deliberation_revision": 3,
            "ledger_sequence": 19,
            "logical_time": CAPTURED_AT,
            "locations": (location,),
            "plans": (plan,),
            "facts": (fact, withheld_fact),
            "npcs": (npc,),
            "private_impressions": (private_impression,),
            "relationship_states": (relationship_state,),
            "boundaries": (boundary,),
            "commitments": (commitment,),
            "actions": (action,),
            "pending_actions": (action,),
            "external_signal_snapshots": (signal,),
            "capability_grants": (capability,),
            "execution_receipts": (receipt,),
            "contextual_life_retries": (retry,),
            "media_inspections": (inspection,),
            "budget_accounts": (budget_account,),
            "budget_reservations": (budget_reservation,),
            "budget_settlements": (budget_settlement,),
            "reconciliations": (reconciliation,),
            "proposal_ids": ("proposal:visible-count",),
            "model_result_audits": (SimpleNamespace(audit_json=secret),),
        }
    )

    class _Ledger:
        world_id = WORLD_ID
        blocks_event_loop = False

        def project(self):  # type: ignore[no-untyped-def]
            return projection

        def lookup_event_commit(self, _event_id):  # type: ignore[no-untyped-def]
            raise AssertionError("the fixture has no typed terminal")

    payload = (
        await DashboardHomeSnapshotModule(
            ledger=_Ledger(),  # type: ignore[arg-type]
            deployment_id="deployment:test",
            boot_id="boot:test",
            clock=lambda: CAPTURED_AT,
        ).capture(_runtime_observation())
    ).to_payload()

    assert payload["sections"]["room"]["state"] == "ready"
    assert payload["sections"]["room"]["render_state"]["route"] == {
        "scene_id": "zhizhi-home",
        "action_id": "study",
        "availability": "active",
    }
    for section_name in (
        "overview_life",
        "facts_memory_inner",
        "relationship_lifecycle",
        "operations",
        "perception_media",
        "authority_privacy",
        "ledger_qualification",
        "runtime_operations",
    ):
        section = payload["sections"][section_name]
        assert section["state"] == "ready"
        assert section["label"]
        assert section["cursor"] == payload["cursor"]
        assert all(metric["label"] for metric in section.get("data", {}).get("metrics", ()))
    for section_name in (
        "overview_life",
        "facts_memory_inner",
        "relationship_lifecycle",
        "operations",
        "perception_media",
        "authority_privacy",
    ):
        section = payload["sections"][section_name]
        assert section["coverage"]["known_count"] >= 1
        assert section["coverage"]["included_count"] >= 1
        assert all(item["kind_label"] and item["title"] for item in section["data"]["highlights"])
    serialized = json.dumps(payload, ensure_ascii=False)
    assert secret not in serialized
    assert "location:ecnu-dorm-room" not in str(payload["sections"]["room"])
    assert "study.focused_reading" not in str(payload["sections"]["room"])
    operation_kinds = {
        item["kind"] for item in payload["sections"]["operations"]["data"]["highlights"]
    }
    assert {
        "budget_account",
        "budget_reservation",
        "budget_settlement",
        "action_reconciliation",
    } <= operation_kinds
    relationship = payload["sections"]["relationship_lifecycle"]["data"]
    assert next(
        metric["count"]
        for metric in relationship["metrics"]
        if metric["key"] == "private_impressions"
    ) == 1
    assert any(
        item["kind"] == "private_impression"
        and item["title"] == "说话有点急，但不是故意的。"
        for item in relationship["highlights"]
    )
    overview = payload["sections"]["overview_life"]["data"]
    plan_highlight = next(item for item in overview["highlights"] if item["kind"] == "plan")
    location_highlight = next(
        item for item in overview["highlights"] if item["kind"] == "location"
    )
    assert plan_highlight["title"] == "专注读书"
    assert location_highlight["title"] == "华东师大宿舍"
    assert location_highlight["status_label"] == "可以给人看"


@pytest.mark.asyncio
async def test_missed_plan_window_and_committed_experience_use_owner_labels() -> None:
    base = WorldLedger.in_memory(world_id=WORLD_ID).project()
    plan = SimpleNamespace(
        plan_id="plan:missed",
        owner_actor_ref="actor:companion",
        activity_kind="open_life.bookstore",
        status="planned",
        privacy_class="private",
        location_ref="location:jiaxing-family-bookstore",
        importance_bp=5000,
        last_transitioned_at=CAPTURED_AT - timedelta(hours=20),
        scheduled_window=SimpleNamespace(
            opens_at=CAPTURED_AT - timedelta(hours=8),
            closes_at=CAPTURED_AT - timedelta(hours=1),
        ),
    )
    experience = SimpleNamespace(
        experience_id="experience:committed",
        status="committed",
        occurred_to=CAPTURED_AT,
        privacy_class="private",
        participant_refs=("actor:companion",),
        values=None,
    )
    projection = base.model_copy(
        update={
            "logical_time": CAPTURED_AT,
            "plans": (plan,),
            "experiences": (experience,),
        }
    )

    class _Ledger:
        world_id = WORLD_ID
        blocks_event_loop = False

        def project(self):  # type: ignore[arg-type]
            return projection

        def lookup_event_commit(self, _event_id):  # type: ignore[arg-type]
            return None

    highlights = (
        await DashboardHomeSnapshotModule(
            ledger=_Ledger(),  # type: ignore[arg-type]
            deployment_id="deployment:test",
            boot_id="boot:test",
            clock=lambda: CAPTURED_AT,
        ).capture()
    ).to_payload()["sections"]["overview_life"]["data"]["highlights"]
    plan_highlight = next(item for item in highlights if item["kind"] == "plan")
    experience_highlight = next(
        item for item in highlights if item["kind"] == "experience"
    )

    assert plan_highlight["title"] == "她自己在过的一件事"
    assert plan_highlight["status_code"] == "window_missed"
    assert plan_highlight["status_label"] == "窗口过了还没开始"
    assert experience_highlight["status_code"] == "committed"
    assert experience_highlight["status_label"] == "已记下"


@pytest.mark.asyncio
async def test_legacy_experience_is_summarized_without_requiring_typed_values() -> None:
    base = WorldLedger.in_memory(world_id=WORLD_ID).project()
    legacy = LegacyExperienceProjection(
        experience_id="experience:legacy",
        entity_revision=1,
        summary_ref="summary:legacy",
        evidence_refs=(
            LegacyExperienceEvidenceRef(
                ref_id="event:legacy",
                evidence_type="committed_experience",
                claim_purpose="past_experience",
            ),
        ),
        occurred_from=CAPTURED_AT - timedelta(minutes=5),
        occurred_to=CAPTURED_AT,
        participant_refs=("actor:companion",),
        result_refs=("result:legacy",),
        privacy_class="private",
    )
    projection = base.model_copy(
        update={"logical_time": CAPTURED_AT, "experiences": (legacy,)}
    )

    class _Ledger:
        world_id = WORLD_ID
        blocks_event_loop = False

        def project(self):  # type: ignore[no-untyped-def]
            return projection

        def lookup_event_commit(self, _event_id):  # type: ignore[no-untyped-def]
            return None

    section = (
        await DashboardHomeSnapshotModule(
            ledger=_Ledger(),  # type: ignore[arg-type]
            deployment_id="deployment:test",
            boot_id="boot:test",
            clock=lambda: CAPTURED_AT,
        ).capture()
    ).to_payload()["sections"]["overview_life"]

    assert section["state"] == "ready"
    experience = next(item for item in section["data"]["highlights"] if item["kind"] == "experience")
    assert experience["status_code"] == "legacy-unverified"
    assert experience["privacy_class"] == "private"
    assert experience["occurred_at"] == "2026-08-12T01:00:00Z"


@pytest.mark.asyncio
async def test_unsettled_typed_change_candidate_is_not_counted_as_included() -> None:
    wrapped, _proposal, _audit_cursor, _current_cursor = _compiler_fixture(
        target_stage="close_friend"
    )
    ledger = wrapped._delegate

    section = (
        await DashboardHomeSnapshotModule(
            ledger=ledger,
            deployment_id="deployment:test",
            boot_id="boot:test",
            clock=lambda: CAPTURED_AT,
        ).capture()
    ).to_payload()["sections"]["relationship_lifecycle"]

    assert section["data"]["typed_change_candidate_count"] == 1
    assert section["data"]["typed_change_lookup_count"] == 1
    assert section["data"]["typed_change_terminal_count"] == 0
    assert section["data"]["typed_change_rejected_count"] == 0
    assert section["data"]["typed_change_stale_count"] == 0
    assert section["data"]["typed_change_unsettled_count"] == 1
    assert section["data"]["typed_change_terminals"] == []
    assert section["coverage"] == {
        "known_count": 0,
        "included_count": 0,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_concurrent_capture_is_single_flight_for_a_stable_cursor() -> None:
    projection = WorldLedger.in_memory(world_id=WORLD_ID).project()
    main_thread = threading.get_ident()

    class _BlockingLedger:
        world_id = WORLD_ID
        blocks_event_loop = True

        def __init__(self) -> None:
            self.project_thread_ids: list[int] = []

        def project(self):  # type: ignore[no-untyped-def]
            self.project_thread_ids.append(threading.get_ident())
            return projection

        def lookup_event_commit(self, _event_id):  # type: ignore[no-untyped-def]
            raise AssertionError("the fixture has no typed terminal")

    ledger = _BlockingLedger()
    clock_calls = 0

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return CAPTURED_AT + timedelta(microseconds=clock_calls)

    module = DashboardHomeSnapshotModule(
        ledger=ledger,  # type: ignore[arg-type]
        deployment_id="deployment:test",
        boot_id="boot:test",
        clock=clock,
    )

    first, second = await asyncio.gather(module.capture(), module.capture())

    assert first.to_payload() == second.to_payload()
    assert clock_calls == 1
    assert ledger.project_thread_ids
    assert all(thread_id != main_thread for thread_id in ledger.project_thread_ids)


@pytest.mark.asyncio
async def test_owner_snapshot_exposes_effect_free_terminal_without_closing_sibling_lanes() -> None:
    wrapped, proposal, audit_cursor, _current_cursor = _compiler_fixture(
        target_stage="close_friend"
    )
    ledger = wrapped._delegate
    audit = next(
        item
        for item in ledger.project_at(audit_cursor).proposal_audits
        if item.proposal_id == proposal.proposal_id
    )
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    terminal = settle_terminal_audited_change(
        ledger=ledger,
        audit=audit,
        change=change,
        current_cursor=audit_cursor,
        actor="worker:relationship-commitment",
        source="test:dashboard-terminal",
    )

    class _BlockingLedger:
        blocks_event_loop = True

        def __init__(self, delegate) -> None:  # type: ignore[no-untyped-def]
            self._delegate = delegate
            self.calls: list[tuple[str, int]] = []

        @property
        def world_id(self) -> str:
            return self._delegate.world_id

        def project(self):  # type: ignore[no-untyped-def]
            self.calls.append(("project", threading.get_ident()))
            return self._delegate.project()

        def lookup_event_commit(self, event_id):  # type: ignore[no-untyped-def]
            self.calls.append(("lookup", threading.get_ident()))
            return self._delegate.lookup_event_commit(event_id)

    observed_ledger = _BlockingLedger(ledger)
    main_thread = threading.get_ident()
    module = DashboardHomeSnapshotModule(
        ledger=observed_ledger,  # type: ignore[arg-type]
        deployment_id="deployment:test",
        boot_id="boot:test",
        clock=lambda: CAPTURED_AT,
    )

    payload = (await module.capture()).to_payload()
    section = payload["sections"]["relationship_lifecycle"]

    assert section["state"] == "ready"
    assert section["coverage"] == {
        "known_count": 1,
        "included_count": 1,
        "truncated": False,
    }
    assert section["data"]["relationship_state_count"] == 0
    assert section["data"]["commitment_count"] == 0
    assert section["data"]["interaction_act_count"] == 0
    assert section["data"]["typed_change_candidate_count"] == 1
    assert section["data"]["typed_change_lookup_count"] == 1
    assert section["data"]["typed_change_terminal_count"] == 1
    assert section["data"]["typed_change_rejected_count"] == 1
    assert section["data"]["typed_change_stale_count"] == 0
    assert section["data"]["typed_change_unsettled_count"] == 0
    assert section["data"]["typed_change_terminals"] == [
        {
            "status": "rejected",
            "status_label": "已拒绝",
            "reason_code": (
                "relationship_proposal_compiler.commitment_stage_transition_not_installed"
            ),
            "effect_applied": False,
            "target_stage": "close_friend",
            "target_stage_label": "很熟的朋友",
            "commitment_code": "mutual_friendship",
            "occurred_at": "2026-07-14T12:00:00Z",
            "cursor": {
                "world_revision": terminal.commit.world_revision,
                "deliberation_revision": terminal.commit.deliberation_revision,
                "ledger_sequence": terminal.commit.ledger_sequence,
            },
        }
    ]
    assert any(kind == "lookup" for kind, _thread_id in observed_ledger.calls)
    assert len({thread_id for _kind, thread_id in observed_ledger.calls}) == 1
    assert observed_ledger.calls[0][1] != main_thread


@pytest.mark.asyncio
async def test_same_cursor_semantic_change_cannot_reuse_an_old_cached_snapshot() -> None:
    first_projection = WorldLedger.in_memory(world_id=WORLD_ID).project()
    second_projection = first_projection.model_copy(
        update={"semantic_hash": "f" * 64}
    )

    class _Ledger:
        world_id = WORLD_ID
        blocks_event_loop = False

        def __init__(self) -> None:
            self.projections = iter((first_projection, second_projection))

        def project(self):  # type: ignore[no-untyped-def]
            return next(self.projections)

        def lookup_event_commit(self, _event_id):  # type: ignore[no-untyped-def]
            return None

    ticks = iter((CAPTURED_AT, CAPTURED_AT + timedelta(seconds=1)))
    module = DashboardHomeSnapshotModule(
        ledger=_Ledger(),  # type: ignore[arg-type]
        deployment_id="deployment:test",
        boot_id="boot:test",
        clock=lambda: next(ticks),
    )

    first = await module.capture()
    second = await module.capture()

    assert first.generated_at != second.generated_at
    assert first.snapshot_hash != second.snapshot_hash


@pytest.mark.asyncio
async def test_terminal_uses_its_exact_event_when_commit_contains_a_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapped, proposal, _audit_cursor, _current_cursor = _compiler_fixture(
        target_stage="close_friend"
    )
    projection = wrapped._delegate.project()
    audit = next(
        item
        for item in projection.proposal_audits
        if item.proposal_id == proposal.proposal_id
    )
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    exact_event_id = audited_change_terminal_event_id(audit=audit, change=change)
    commit = CommitResult(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
        event_ids=("event:sibling", exact_event_id),
    )
    settlement = SimpleNamespace(
        status="rejected",
        reason_code=RELATIONSHIP_COMMITMENT_TERMINAL_REASON,
        source_change_id=change.change_id,
        derived_proposal_id="proposal:terminal",
        commit=commit,
    )
    monkeypatch.setattr(
        snapshot_module,
        "find_terminal_audited_change",
        lambda **_kwargs: settlement,
    )

    class _Ledger:
        world_id = WORLD_ID
        blocks_event_loop = False

        def __init__(self) -> None:
            self.lookups: list[str] = []

        def project(self):  # type: ignore[no-untyped-def]
            return projection

        def lookup_event_commit(self, event_id):  # type: ignore[no-untyped-def]
            self.lookups.append(event_id)
            return SimpleNamespace(logical_time=CAPTURED_AT), commit

    ledger = _Ledger()
    section = (
        await DashboardHomeSnapshotModule(
            ledger=ledger,  # type: ignore[arg-type]
            deployment_id="deployment:test",
            boot_id="boot:test",
            clock=lambda: CAPTURED_AT,
        ).capture()
    ).to_payload()["sections"]["relationship_lifecycle"]

    assert ledger.lookups == [exact_event_id]
    assert not {
        "source_proposal_id",
        "source_change_id",
        "terminal_proposal_id",
        "terminal_event_ref",
        "subject_ref",
    } & section["data"]["typed_change_terminals"][0].keys()


@pytest.mark.asyncio
async def test_terminal_totals_are_exact_when_safe_details_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = WorldLedger.in_memory(world_id=WORLD_ID).project().model_copy(
        update={
            "world_revision": 70,
            "deliberation_revision": 70,
            "ledger_sequence": 70,
            "logical_time": CAPTURED_AT,
        }
    )
    candidates = tuple(
        (
            SimpleNamespace(proposal_id=f"proposal:{index}"),
            SimpleNamespace(change_id=f"change:{index}"),
        )
        for index in range(70)
    )
    commits = {
        f"event:terminal:{index}": CommitResult(
            world_revision=index + 1,
            deliberation_revision=index + 1,
            ledger_sequence=index + 1,
            event_ids=(f"event:terminal:{index}",),
        )
        for index in range(70)
    }

    monkeypatch.setattr(
        snapshot_module,
        "_relationship_terminal_candidates",
        lambda _projection: candidates,
    )
    monkeypatch.setattr(
        snapshot_module,
        "find_terminal_audited_change",
        lambda *, audit, **_kwargs: SimpleNamespace(
            status="stale" if int(audit.proposal_id.rsplit(":", 1)[1]) < 35 else "rejected",
            reason_code=RELATIONSHIP_COMMITMENT_TERMINAL_REASON,
            commit=commits[
                f"event:terminal:{int(audit.proposal_id.rsplit(':', 1)[1])}"
            ],
        ),
    )
    monkeypatch.setattr(
        snapshot_module,
        "audited_change_terminal_event_id",
        lambda *, audit, **_kwargs: (
            f"event:terminal:{int(audit.proposal_id.rsplit(':', 1)[1])}"
        ),
    )
    monkeypatch.setattr(
        snapshot_module,
        "terminal_relationship_commitment_payload",
        lambda _change: SimpleNamespace(
            subject_ref="user:withheld",
            target_stage="close_friend",
            commitment_code="mutual_friendship",
        ),
    )

    class _Ledger:
        world_id = WORLD_ID
        blocks_event_loop = False

        def project(self):  # type: ignore[no-untyped-def]
            return projection

        def lookup_event_commit(self, event_id):  # type: ignore[no-untyped-def]
            return SimpleNamespace(logical_time=CAPTURED_AT), commits[event_id]

    section = (
        await DashboardHomeSnapshotModule(
            ledger=_Ledger(),  # type: ignore[arg-type]
            deployment_id="deployment:test",
            boot_id="boot:test",
            clock=lambda: CAPTURED_AT,
        ).capture()
    ).to_payload()["sections"]["relationship_lifecycle"]

    data = section["data"]
    assert data["typed_change_candidate_count"] == 70
    assert data["typed_change_lookup_count"] == 70
    assert data["typed_change_terminal_count"] == 70
    assert data["typed_change_rejected_count"] == 35
    assert data["typed_change_stale_count"] == 35
    assert data["typed_change_unsettled_count"] == 0
    assert len(data["typed_change_terminals"]) == 64
    assert section["coverage"] == {
        "known_count": 70,
        "included_count": 64,
        "truncated": True,
    }
