from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import json

from companion_daemon.world_v2.appraisal_proposal_compiler import (
    DEFAULT_APPRAISAL_WINDOW,
)
from companion_daemon.world_v2.present_prompt import compile_slim_consider_payload
from companion_daemon.world_v2.reflection_scheduler import (
    REFLECTION_UNWEIGHTED_BP,
    ReflectionScheduler,
    reflection_revisit_gap,
)


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
SOURCE = "event:appraisal-accepted:wound"


class _StubAppraisal:
    def __init__(
        self,
        event_ref: str,
        *,
        confidence_bp: int = 9_000,
        status: str = "active",
        expires_at: datetime | None = None,
    ) -> None:
        self.appraisal_id = f"appraisal:{event_ref}"
        self.confidence_bp = confidence_bp
        self.status = status
        self.expires_at = expires_at or datetime(2099, 1, 1, tzinfo=UTC)
        self.origin = SimpleNamespace(accepted_event_ref=event_ref)


class _StubProcess:
    def __init__(
        self, source_evidence_ref: str, *, state: str = "terminal", ordinal: int = 1
    ) -> None:
        self.process_kind = "life_reflection"
        self.source_evidence_ref = source_evidence_ref
        self.state = state
        suffix = "" if ordinal <= 1 else f":{ordinal}"
        self.trigger_id = f"reflection:{source_evidence_ref}{suffix}"
        self.trigger_ref = f"reflection:{source_evidence_ref}"


class _StubEvent:
    def __init__(
        self, event_id: str, *, logical_time: datetime, trigger_id: str | None = None
    ) -> None:
        self.event_id = event_id
        self.logical_time = logical_time
        self.created_at = logical_time
        payload = {"trigger_id": trigger_id} if trigger_id is not None else {}
        self.payload_json = json.dumps(payload)


class _StubLedger:
    world_id = "world:unsettled-feeling"

    def __init__(
        self,
        appraisals,
        processes,
        *,
        logical_time: datetime = NOW,
        opened_at: dict[str, datetime] | None = None,
        accepted_trigger_ids: dict[str, str] | None = None,
    ) -> None:
        self._projection = SimpleNamespace(
            appraisals=tuple(appraisals),
            trigger_processes=tuple(processes),
            logical_time=logical_time,
            world_revision=7,
            deliberation_revision=3,
            ledger_sequence=11,
            committed_world_event_refs=(),
        )
        self.commits: list = []
        self._opened_at = dict(opened_at or {})
        self._accepted_trigger_ids = dict(accepted_trigger_ids or {})

    def project(self):
        return self._projection

    def commit_at_cursor(self, events, *, expected_cursor, commit_id):
        self.commits.append((events, commit_id))

    def lookup_event_commit(self, event_id):
        if event_id in self._opened_at:
            return (_StubEvent(event_id, logical_time=self._opened_at[event_id]), SimpleNamespace())
        trigger_id = self._accepted_trigger_ids.get(event_id)
        if trigger_id is not None:
            return (
                _StubEvent(event_id, logical_time=NOW, trigger_id=trigger_id),
                SimpleNamespace(),
            )
        return None


def _opened_process(payload: dict) -> dict:
    return json.loads(payload)["process"]


def test_fixed_two_reflection_cap_does_not_block_a_later_revisit() -> None:
    wound = _StubAppraisal(SOURCE)
    first = _StubProcess(SOURCE, state="terminal", ordinal=1)
    ledger = _StubLedger(
        [wound],
        [first],
        logical_time=NOW + reflection_revisit_gap(1) + timedelta(seconds=1),
        opened_at={f"event:life-reflection:opened:{SOURCE}": NOW},
    )
    result = ReflectionScheduler(ledger=ledger, actor="worker:reflection").open_once(
        trace_id="t",
        correlation_id="c",
    )
    assert result.opened == 1
    process = _opened_process(ledger.commits[0][0][0].payload_json)
    assert process["source_evidence_ref"] == SOURCE
    assert process["trigger_ref"] == f"reflection:{SOURCE}"
    assert process["trigger_id"] == f"reflection:{SOURCE}:2"


def test_same_wound_is_not_reopened_before_the_growing_interval() -> None:
    wound = _StubAppraisal(SOURCE)
    first = _StubProcess(SOURCE, state="terminal", ordinal=1)
    ledger = _StubLedger(
        [wound],
        [first],
        logical_time=NOW + timedelta(minutes=10),
        opened_at={f"event:life-reflection:opened:{SOURCE}": NOW},
    )
    result = ReflectionScheduler(ledger=ledger, actor="worker:reflection").open_once(
        trace_id="t",
        correlation_id="c",
    )
    assert result.opened == 0
    assert ledger.commits == []


def test_reflection_residue_does_not_open_a_new_wound() -> None:
    residue_ref = "event:appraisal-accepted:residue"
    residue = _StubAppraisal(residue_ref, confidence_bp=9_500)
    ledger = _StubLedger(
        [residue],
        [],
        accepted_trigger_ids={residue_ref: f"reflection:{SOURCE}"},
    )
    result = ReflectionScheduler(ledger=ledger, actor="worker:reflection").open_once(
        trace_id="t",
        correlation_id="c",
    )
    assert result.opened == 0
    assert ledger.commits == []


def test_resolved_wound_is_not_revisited() -> None:
    wound = _StubAppraisal(SOURCE, status="superseded")
    first = _StubProcess(SOURCE, state="terminal", ordinal=1)
    ledger = _StubLedger(
        [wound],
        [first],
        logical_time=NOW + reflection_revisit_gap(1) + timedelta(hours=1),
        opened_at={f"event:life-reflection:opened:{SOURCE}": NOW},
    )
    result = ReflectionScheduler(ledger=ledger, actor="worker:reflection").open_once(
        trace_id="t",
        correlation_id="c",
    )
    assert result.opened == 0
    assert ledger.commits == []


def test_expired_wound_is_not_revisited() -> None:
    wound = _StubAppraisal(SOURCE, status="expired", expires_at=NOW - timedelta(hours=1))
    ledger = _StubLedger([wound], [])
    result = ReflectionScheduler(ledger=ledger, actor="worker:reflection").open_once(
        trace_id="t",
        correlation_id="c",
    )
    assert result.opened == 0
    assert ledger.commits == []


def test_a_reading_she_never_weighed_does_not_come_back() -> None:
    """The unweighted default must never schedule her to think again."""

    wound = _StubAppraisal(SOURCE, confidence_bp=REFLECTION_UNWEIGHTED_BP)
    ledger = _StubLedger([wound], [])
    result = ReflectionScheduler(ledger=ledger, actor="worker:reflection").open_once(
        trace_id="t",
        correlation_id="c",
    )
    assert result.opened == 0
    assert result.reason == "nothing_weighed"
    assert ledger.commits == []


def test_only_the_heaviest_thing_she_carries_comes_back() -> None:
    """The bar is her own live maximum, not a constant she never reaches.

    Four recorded conversations produced weights of 4_500 through 7_000 against
    a fixed 8_500 bar, so nothing was ever revisited.
    """

    lighter = _StubAppraisal("event:appraisal-accepted:lighter", confidence_bp=5_600)
    heaviest = _StubAppraisal("event:appraisal-accepted:heaviest", confidence_bp=7_000)
    ledger = _StubLedger([lighter, heaviest], [])
    result = ReflectionScheduler(ledger=ledger, actor="worker:reflection").open_once(
        trace_id="t",
        correlation_id="c",
    )
    assert result.opened == 1
    opened = json.dumps(ledger.commits, default=str)
    assert "heaviest" in opened
    assert "lighter" not in opened


def test_revisit_interval_grows_with_each_thought() -> None:
    assert reflection_revisit_gap(1) < reflection_revisit_gap(2)
    assert reflection_revisit_gap(2) < reflection_revisit_gap(3)
    assert reflection_revisit_gap(4) == reflection_revisit_gap(8)


def test_her_own_weight_is_what_makes_a_wound_revisitable() -> None:
    """The production path carries the weight she wrote, or the neutral default."""

    weighed = compile_slim_consider_payload(
        {
            "messages": ["行吧。"],
            "felt": "他这么说我心里堵着",
            "matters_bp": 7_000,
            "mood": "resentment",
        }
    )
    assert weighed is not None
    assert weighed["appraisal_draft"]["confidence"] == 7_000
    assert weighed["appraisal_draft"]["confidence"] > REFLECTION_UNWEIGHTED_BP

    unweighed = compile_slim_consider_payload(
        {"messages": ["嗯。"], "felt": "没什么特别的"}
    )
    assert unweighed is not None
    assert unweighed["appraisal_draft"]["confidence"] == REFLECTION_UNWEIGHTED_BP


def test_default_appraisal_window_outlives_the_whole_revisit_ladder() -> None:
    """A 2h window killed every wound before its second thought."""

    ladder = sum(
        (reflection_revisit_gap(visit) for visit in range(1, 5)), timedelta()
    )
    assert DEFAULT_APPRAISAL_WINDOW > ladder
