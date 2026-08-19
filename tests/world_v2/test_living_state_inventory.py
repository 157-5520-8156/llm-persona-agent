from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.living_state_inventory import (
    install_living_state_context,
    living_appraisal_slice_items,
    living_impression_slice_items,
    living_thread_slice_items,
)

NOW = datetime(2026, 8, 19, 6, 0, 45, tzinfo=UTC)


def _origin(ref: str) -> SimpleNamespace:
    return SimpleNamespace(accepted_event_ref=ref)


def _appraisal(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        appraisal_id=f"appraisal:compiled:{index:02d}",
        status="active",
        subject_ref="user:geoff" if index % 2 else "agent:companion",
        source_cluster_ref=f"cluster:{index}",
        origin=_origin(f"event:appraisal-mutation:{index:02d}"),
        hypotheses=(
            SimpleNamespace(
                hypothesis_id=f"h:{index}",
                meaning=f"meaning-{index}",
                attribution="user",
                controllability="partly_controllable",
                severity="low",
                weight_bp=10_000,
            ),
        ),
        evidence_refs=(
            SimpleNamespace(evidence_type="observed_message", ref_id=f"obs:{index}"),
        ),
        confidence_bp=6_000 + index,
        accepted_at=NOW - timedelta(hours=index + 1),
        expires_at=NOW + timedelta(days=2),
    )


def _thread(*, thread_id: str, subject_ref: str) -> SimpleNamespace:
    return SimpleNamespace(
        thread_id=thread_id,
        values=SimpleNamespace(
            status="open",
            kind="topic_open",
            subject_ref=subject_ref,
            importance_bp=5500,
            due_window=None,
            privacy_class="private",
        ),
        origin=_origin("event:thread-open:1"),
    )


def _impression(
    *, impression_id: str, subject_ref: str, summary: str, origin: str
) -> SimpleNamespace:
    return SimpleNamespace(
        impression_id=impression_id,
        status="active",
        subject_ref=subject_ref,
        origin=_origin(origin),
        reflection_summary=summary,
        confidence_bp=7_000,
        first_seen=NOW - timedelta(days=1),
        last_supported=NOW,
        expiry_condition="until_contradicted",
        contradiction_refs=(),
    )


def test_living_appraisals_keep_every_active_unexpired_reading() -> None:
    projection = SimpleNamespace(
        logical_time=NOW,
        appraisals=tuple(_appraisal(index) for index in range(16)),
        threads=(),
        private_impressions=(),
    )
    items = living_appraisal_slice_items(projection, logical_time=NOW)
    assert len(items) == 16
    assert {item["source_ref"] for item in items} == {
        f"appraisal:compiled:{index:02d}" for index in range(16)
    }


def test_living_threads_include_event_subjects() -> None:
    projection = SimpleNamespace(
        threads=(
            _thread(
                thread_id="thread:a",
                subject_ref="event:appraisal-mutation:deadbeef",
            ),
            _thread(
                thread_id="thread:b",
                subject_ref="event:life-aftermath:settlement:cafe",
            ),
            SimpleNamespace(
                thread_id="thread:closed",
                values=SimpleNamespace(
                    status="resolved",
                    kind="topic_open",
                    subject_ref="user:geoff",
                    importance_bp=1000,
                    due_window=None,
                    privacy_class="private",
                ),
                origin=_origin("event:thread-open:closed"),
            ),
        )
    )
    items = living_thread_slice_items(projection)
    assert [item["source_ref"] for item in items] == ["thread:a", "thread:b"]
    assert items[0]["value"]["subject_ref"].startswith("event:")


def test_limited_impression_keeps_the_row_without_its_prose() -> None:
    limited = "impression:limited"
    projection = SimpleNamespace(
        private_impressions=(
            _impression(
                impression_id="impression:open",
                subject_ref="user:geoff",
                summary="他今天提起了雅思。",
                origin="event:world-v2-epoch:1",
            ),
            _impression(
                impression_id=limited,
                subject_ref="agent:companion",
                summary="不该出现在你们对话里的私下理解。",
                origin="event:private-impression:accepted:1",
            ),
            _impression(
                impression_id="impression:third",
                subject_ref="agent:companion",
                summary="他记得我说过的小事。",
                origin="event:private-impression:accepted:2",
            ),
        )
    )
    items = living_impression_slice_items(
        projection, user_channel_limited_ids=frozenset({limited})
    )
    assert len(items) == 3
    by_id = {item["source_ref"]: item["value"] for item in items}
    assert by_id[limited]["hold_reason"] == "user_channel_limited"
    assert "reflection_summary" not in by_id[limited]
    assert by_id["impression:open"]["reflection_summary"] == "他今天提起了雅思。"


def test_snapshot_prefers_living_lanes_over_truncated_capsule() -> None:
    projection = SimpleNamespace(
        logical_time=NOW,
        appraisals=tuple(_appraisal(index) for index in range(16)),
        threads=(
            _thread(
                thread_id="thread:a",
                subject_ref="event:appraisal-mutation:deadbeef",
            ),
            _thread(
                thread_id="thread:b",
                subject_ref="event:life-aftermath:settlement:cafe",
            ),
        ),
        private_impressions=(
            _impression(
                impression_id="impression:one",
                subject_ref="user:geoff",
                summary="第一条。",
                origin="event:world-v2-epoch:1",
            ),
            _impression(
                impression_id="impression:two",
                subject_ref="agent:companion",
                summary="第二条。",
                origin="event:world-v2-epoch:1",
            ),
            _impression(
                impression_id="impression:three",
                subject_ref="agent:companion",
                summary="第三条。",
                origin="event:private-impression:accepted:2",
            ),
        ),
    )
    context = {
        "world_id": "world:slots",
        "actor_ref": "agent:companion",
        "world_revision": 12,
        "deliberation_revision": 2,
        "ledger_sequence": 12,
        "logical_time": NOW.isoformat(),
        "slices": {
            "appraisals": {
                "availability": "available",
                "source_refs": ["appraisal:compiled:00"],
                "items": [
                    {
                        "source_ref": "appraisal:compiled:00",
                        "value": {
                            "subject_ref": "user:geoff",
                            "hypotheses": [{"hypothesis_id": "h:0", "meaning": "only-one"}],
                            "confidence_bp": 6000,
                            "accepted_at": NOW.isoformat(),
                            "expires_at": (NOW + timedelta(days=2)).isoformat(),
                        },
                    }
                ],
            },
            "open_threads": {
                "availability": "available",
                "source_refs": [],
                "items": [],
            },
            "private_impressions": {
                "availability": "available",
                "source_refs": ["impression:one"],
                "items": [
                    {
                        "source_ref": "impression:one",
                        "value": {
                            "subject_ref": "user:geoff",
                            "reflection_summary": "第一条。",
                            "confidence_bp": 7000,
                            "status": "active",
                        },
                    }
                ],
            },
        },
    }
    before = compile_inner_life_snapshot(context)
    before_materials = __import__("json").loads(before.materials_json)
    assert len(before_materials.get("appraisals") or []) == 1
    assert "unresolved" not in before_materials
    assert len(before_materials.get("private_impressions") or []) == 1

    installed = install_living_state_context(context, projection)
    after = compile_inner_life_snapshot(installed)
    after_materials = __import__("json").loads(after.materials_json)
    assert len(after_materials["appraisals"]) == 16
    assert len(after_materials["unresolved"]) == 2
    assert len(after_materials["private_impressions"]) == 3
