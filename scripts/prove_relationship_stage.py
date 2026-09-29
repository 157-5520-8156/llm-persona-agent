#!/usr/bin/env python
"""Clone verification for relationship stage: we_are path + ladder preview.

Writes JSON under output/relationship-stage/.  Uses overlay inbound (no model
cost for the forced we_are turn).  Does not touch production.

    .venv/bin/python scripts/prove_relationship_stage.py
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

OUTPUT = ROOT / "output" / "relationship-stage"
PRODUCTION_DB = ROOT / "data" / "companion.epoch2.sqlite"
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
NOW = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)

from companion_daemon.world_v2.relationship_reducers import (  # noqa: E402
    _PRIMARY_LADDER_AXES,
    _POLICY,
    _stage_ladder_score,
    _derive_stage,
)
from companion_daemon.world_v2.schemas import (  # noqa: E402
    RelationshipHysteresisProjection,
    RelationshipVariablesProjection,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger  # noqa: E402


def _open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def _relationship_head(db: Path) -> dict[str, Any]:
    ledger = SQLiteWorldLedger(path=db, world_id=WORLD_ID)
    projection = ledger.project()
    states = [
        item
        for item in projection.relationship_states
        if item.subject_ref in {"user:primary", "user:geoff"}
    ]
    if not states:
        variables = RelationshipVariablesProjection()
        return {
            "stage": "stranger",
            "derived_stage": "stranger",
            "variables": variables.model_dump(mode="json"),
            "four_axis_mean_bp": 0,
            "six_axis_mean_bp": 0,
            "hysteresis": RelationshipHysteresisProjection().model_dump(mode="json"),
            "derived_hysteresis": RelationshipHysteresisProjection().model_dump(mode="json"),
            "enter_bp": dict(_POLICY["enter_bp"]),
        }
    state = states[0]
    variables = state.variables
    four_axis = _stage_ladder_score(variables, use_six_axis=False)
    six_axis = _stage_ladder_score(variables, use_six_axis=True)
    derived, hysteresis = _derive_stage(
        state.stage,
        variables,
        state.hysteresis,
        projection.logical_time,
    )
    return {
        "stage": state.stage,
        "derived_stage": derived,
        "entity_revision": state.entity_revision,
        "policy_digest": state.policy_digest,
        "variables": variables.model_dump(mode="json"),
        "four_axis_mean_bp": four_axis,
        "six_axis_mean_bp": six_axis,
        "hysteresis": state.hysteresis.model_dump(mode="json"),
        "derived_hysteresis": hysteresis.model_dump(mode="json"),
        "enter_bp": dict(_POLICY["enter_bp"]),
    }


async def _drive_we_are_on_clone(clone: Path) -> dict[str, Any]:
    import scripts.drive_production_lanes as drive

    we_are_payload = {
        "messages": ["我们就算朋友吧"],
        "felt": "我想把我们算成朋友",
        "stuck_with_me": "他问我们算什么",
        "wants": "把这一层说清楚",
        "photo": False,
        "we_are": "friend",
        "calling_it": "朋友",
        "said_as": "我们就算朋友吧",
    }
    overlay = drive._authored_inbound_payload(we_are_payload)  # noqa: SLF001
    session = await drive.open_session(
        database=clone,
        output_dir=OUTPUT,
        inbound_payload=overlay,
        enable_media=False,
    )
    try:
        await session.inbound("我们现在算什么关系")
        drains = await session.drain_loop(rounds=12, background=8)
        return {"drains": drains, "sent": list(session.delivery.sent)}
    finally:
        await session.close()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    import scripts.drive_production_lanes as drive

    clone = OUTPUT / "prove_clone.sqlite"
    drive.assert_clone_is_safe(source=PRODUCTION_DB, target=clone)
    drive.clone_ledger(PRODUCTION_DB, clone)

    before = _relationship_head(clone)
    report: dict[str, Any] = {
        "production_db": str(PRODUCTION_DB),
        "clone": str(clone),
        "before": before,
        "ladder_policy": {
            "aggregation": _POLICY["aggregation"],
            "primary_axes": list(_PRIMARY_LADDER_AXES),
            "enter_bp": dict(_POLICY["enter_bp"]),
            "exit_bp": dict(_POLICY["exit_bp"]),
        },
    }

    asyncio.run(_drive_we_are_on_clone(clone))
    after_we_are = _relationship_head(clone)

    vars_ = RelationshipVariablesProjection.model_validate(before["variables"])

    conn = _open_ro(clone)
    commits = conn.execute(
        "SELECT COUNT(*) FROM world_v2_events"
        " WHERE world_id=? AND json_extract(event_json,'$.event_type')="
        "'RelationshipCommitmentAccepted'",
        (WORLD_ID,),
    ).fetchone()[0]

    report["after_we_are_overlay"] = after_we_are
    report["relationship_commitment_accepted_count"] = int(commits)
    report["stage_changed_via_we_are"] = (
        before["stage"] != after_we_are["stage"] and after_we_are["stage"] == "friend"
    )

    derived, hyst = _derive_stage(
        before["stage"],
        vars_,
        RelationshipHysteresisProjection.model_validate(before["hysteresis"]),
        NOW,
    )
    report["ladder_preview_on_production_vars"] = {
        "four_axis_mean_bp": before["four_axis_mean_bp"],
        "would_derive_stage_now": derived,
        "hysteresis": hyst.model_dump(mode="json"),
        "acquaintance_enter_bp": _POLICY["enter_bp"]["acquaintance"],
        "friend_enter_bp": _POLICY["enter_bp"]["friend"],
    }

    out = OUTPUT / "prove_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
