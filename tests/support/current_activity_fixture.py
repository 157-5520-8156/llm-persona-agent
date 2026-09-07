"""Offline, accepted open-life activities for Context and expression tests.

The shared life-development test fixtures supply model replies only. Plans and
transitions are committed by the real runtime, worker, and acceptance boundary.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from typing import Literal

from character_interior import canonical_inner_decision
from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.activity_lifecycle_runtime import (
    ActivityLifecycleAcceptanceRuntime,
    ActivityLifecycleProposalRecorder,
)
from companion_daemon.world_v2.activity_lifecycle_worker import ActivityLifecycleWorker
from companion_daemon.world_v2.activity_timing import MIN_ACTIVITY_TRANSITION_DWELL_SECONDS
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore,
    SQLiteImmutableLifeContentStore,
)
from companion_daemon.world_v2.life_ecology_activity import ActivityOpeningCatalog
from companion_daemon.world_v2.life_ecology_contract import LifeEcologyRunKey
from companion_daemon.world_v2.life_ecology_trigger_store import LedgerLifeEcologyTriggerStore
from companion_daemon.world_v2.schema_core import PrivacyClass
from companion_daemon.world_v2.schemas import WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import (
    NOW,
    OWNER,
    _location_bound_world_draft,
    _location_capability,
    _runtime,
    _seed_clock,
    _SequenceModel,
)


CURRENT_ACTIVITY_INTENTION = "我想去看看校园征稿启事，顺便想想那篇还没写完的随笔。"
CURRENT_ACTIVITY_PREMISE = "校园院子里的公告栏贴出了一张面向学生的征稿启事。"
UNSETTLED_OUTCOME_TEXT = "未结算的结果绝不能进入当前聊天"


class _LifecycleChoice:
    """One test-authored choice, bound to an actually offered opaque token."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def consider(self, opportunity):
        manifest = opportunity.capability_manifest
        assert manifest is not None
        assert self._token in manifest.payload["offered_tokens"]
        return canonical_inner_decision(
            opportunity,
            identity="current-activity:" + self._token,
            decision={
                "contract": "character-interior-purpose-decision.1",
                "purpose": "activity_lifecycle_choice",
                "capability_ref": manifest.capability_ref,
                "capability_payload_hash": manifest.payload_hash,
                "source_refs": list(manifest.source_refs),
                "payload": {
                    "contract": "character-interior-activity-lifecycle-choice.1",
                    "decision": "select",
                    "selected_token": self._token,
                },
            },
        )


async def _accept_transition(
    *,
    ledger: WorldLedger | SQLiteWorldLedger,
    issuer: AcceptedLedgerBatchIssuer,
    wake: WorldEvent,
    operation: Literal["start", "pause", "resume"],
) -> None:
    catalog_version = "life-ecology.1"
    claim = await LedgerLifeEcologyTriggerStore(
        ledger=ledger, owner_id="worker:current-activity-fixture"
    ).claim_or_join(
        key=LifeEcologyRunKey(
            world_id=ledger.world_id,
            wake_event_ref=wake.event_id,
            catalog_version=catalog_version,
        ),
        trace_id="trace:current-activity:" + operation,
        correlation_id="correlation:current-activity",
    )
    assert claim.state == "owned"
    catalog = ActivityOpeningCatalog(owner_actor_ref=OWNER)
    offered = catalog.openings_for(projection=ledger.project(), wake_event_ref=wake.event_id)
    matching = [item for item in offered.openings if item.operation == operation]
    assert len(matching) == 1, (operation, offered)
    worker = ActivityLifecycleWorker(
        ledger=ledger,
        catalog=catalog,
        character_interior=_LifecycleChoice(matching[0].opening_token),
        owner_actor_ref=OWNER,
        proposal_recorder=ActivityLifecycleProposalRecorder(ledger=ledger),
        acceptance_runtime=ActivityLifecycleAcceptanceRuntime(ledger=ledger, batch_issuer=issuer),
        ecology_catalog_version=catalog_version,
    )
    result = await worker.advance_once(
        wake_event_ref=wake.event_id,
        trigger_id=claim.trigger_id,
        logical_time=wake.logical_time,
        actor="worker:current-activity-fixture",
        trace_id="trace:current-activity:" + operation,
        correlation_id="correlation:current-activity",
        renewed_plan_catalog=True,
    )
    assert result.status == "transitioned", result


async def accepted_current_activity(
    *,
    status: Literal["planned", "active", "paused", "resumed"] = "active",
    privacy_class: PrivacyClass = "personal",
    sqlite_path: Path | None = None,
) -> tuple[
    WorldLedger | SQLiteWorldLedger,
    InMemoryImmutableLifeContentStore | SQLiteImmutableLifeContentStore,
    str,
    str,
]:
    """Return ``ledger, store, plan_id, latest_activity_event_ref``.

    ``planned`` remains an unstarted intention. ``active`` has an accepted
    start; ``paused`` additionally has an accepted pause after the real dwell
    boundary. ``resumed`` has another dwell and an accepted resume: its returned
    Plan is active and its latest event is ActivityResumed, not a claim that
    activity continued throughout the pause. The intention's unfinished essay
    is subjective sidecar material, never a committed past experience.
    Candidate outcomes remain unsettled.

    A supplied SQLite path may already contain an app bootstrap before NOW.
    The caller owns closing the returned SQLite ledger and content store.
    """

    if status not in {"planned", "active", "paused", "resumed"}:
        raise ValueError("current activity fixture supports planned, active, paused, or resumed")
    issuer = AcceptedLedgerBatchIssuer()
    world_id = "world:current-activity-fixture"
    if sqlite_path is None:
        ledger = WorldLedger.in_memory(world_id=world_id, accepted_batch_issuer=issuer)
        supplied_store = None
    else:
        ledger = SQLiteWorldLedger(
            path=str(sqlite_path), world_id=world_id, accepted_batch_issuer=issuer
        )
        supplied_store = SQLiteImmutableLifeContentStore(path=str(sqlite_path), world_id=world_id)
    previous_time = ledger.project().logical_time
    if previous_time is not None and previous_time >= NOW:
        if supplied_store is not None:
            supplied_store.close()
            ledger.close()
        raise ValueError("existing fixture bootstrap must precede current_activity_fixture.NOW")
    wake = _seed_clock(ledger, logical_time_from=previous_time)
    capability = _location_capability(privacy_class=privacy_class)
    draft = json.loads(
        _location_bound_world_draft(
            wake=wake,
            capability=capability,
            timing={"mode": "now", "duration_minutes": 60},
            privacy_class=privacy_class,
            causal_authority="character_choice",
            outcome_resolution_authority="character_choice",
        )
    )
    draft["premise"] = CURRENT_ACTIVITY_PREMISE
    draft["claim_declarations"][0]["summary"] = CURRENT_ACTIVITY_PREMISE
    for outcome in draft["outcomes"]:
        outcome["text"] = UNSETTLED_OUTCOME_TEXT
    runtime, store = _runtime(
        ledger=ledger,
        wake=wake,
        location_capability=capability,
        store=supplied_store,
        world_author=_SequenceModel(
            model="current-activity-world-author",
            outputs=(json.dumps(draft, ensure_ascii=False),),
        ),
        character_interior=_SequenceModel(
            model="current-activity-character",
            outputs=(
                json.dumps(
                    {
                        "decision": "accept",
                        "intention_summary": CURRENT_ACTIVITY_INTENTION,
                        "importance_bp": 4300,
                        "opens_at": NOW.isoformat(),
                        "closes_at": (NOW + timedelta(hours=1)).isoformat(),
                        "participant_refs": [],
                    },
                    ensure_ascii=False,
                ),
            ),
        ),
    )
    accepted = await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:current-activity:plan",
        correlation_id="correlation:current-activity",
    )
    assert accepted.status == "plan_committed", accepted
    if status in {"active", "paused", "resumed"}:
        await _accept_transition(ledger=ledger, issuer=issuer, wake=wake, operation="start")
    if status in {"paused", "resumed"}:
        pause_wake = _seed_clock(
            ledger,
            event_id="event:clock:current-activity:pause",
            logical_time=NOW + timedelta(seconds=MIN_ACTIVITY_TRANSITION_DWELL_SECONDS),
            logical_time_from=NOW,
        )
        await _accept_transition(ledger=ledger, issuer=issuer, wake=pause_wake, operation="pause")
    if status == "resumed":
        resume_wake = _seed_clock(
            ledger,
            event_id="event:clock:current-activity:resume",
            logical_time=NOW + timedelta(seconds=2 * MIN_ACTIVITY_TRANSITION_DWELL_SECONDS),
            logical_time_from=NOW + timedelta(seconds=MIN_ACTIVITY_TRANSITION_DWELL_SECONDS),
        )
        await _accept_transition(ledger=ledger, issuer=issuer, wake=resume_wake, operation="resume")
    projection = ledger.project()
    assert len(projection.plans) == 1
    plan = projection.plans[0]
    assert plan.status == ("active" if status == "resumed" else status)
    assert plan.authority_origin is not None
    assert projection.experiences == ()
    assert not any(item.status == "settled" for item in projection.world_occurrences)
    return ledger, store, plan.plan_id, plan.authority_origin.accepted_event_ref
