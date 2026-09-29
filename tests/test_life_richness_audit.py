"""The audit must not turn stored candidates or corrupt sidecars into lived facts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3


def test_only_settled_exact_content_counts_as_life(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "life_richness_audit", Path(__file__).parents[1] / "scripts/audit_life_richness.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = tmp_path / "world.sqlite"
    valid = json.dumps({"contract": "world-consequence.2", "environment_text": "设施关闭"})
    digest = hashlib.sha256(valid.encode()).hexdigest()
    with sqlite3.connect(database) as conn:
        conn.execute("create table world_v2_events(world_id,ledger_sequence,event_json)")
        conn.execute("create table world_v2_life_content(world_id,content_ref,content_payload_hash,text)")
        conn.execute("create table world_v2_day_open_opportunities(world_id,day_key,body)")
        conn.executemany("insert into world_v2_life_content values(?,?,?,?)", [
            ("w", "good", digest, valid), ("w", "corrupt", digest, "different"),
            ("w", "candidate", digest, valid),
        ])
        for index, ref in enumerate(("good", "corrupt"), 1):
            event = {"event_type": "WorldOccurrenceSettled", "event_id": f"e{index}",
                     "logical_time": "2026-09-27T00:00:00Z", "payload_json": json.dumps({
                         "occurrence_id": f"o{index}", "result_payload_ref": ref, "result_payload_hash": digest,
                     })}
            conn.execute("insert into world_v2_events values(?,?,?)", ("w", index, json.dumps(event)))
    result = module.audit(database)
    assert len(result["timeline"]) == 2
    assert result["timeline"][0]["settled_content"]["environment_text"] == "设施关闭"
    assert result["timeline"][1]["content_verified"] is False
    assert "settled_content" not in result["timeline"][1]
    assert result["exact_repeated_settlement_contents"] == 0
