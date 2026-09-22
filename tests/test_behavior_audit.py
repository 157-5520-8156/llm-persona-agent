from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from behavior_audit.ledger import LedgerIndex
from behavior_audit.rules import audit_b09, audit_b44, run_audits


def _event(
    *,
    seq: int,
    event_type: str,
    correlation_id: str,
    payload: dict,
    logical_time: str = "2026-08-20T12:00:00+00:00",
) -> tuple[int, str]:
    event = {
        "event_type": event_type,
        "correlation_id": correlation_id,
        "logical_time": logical_time,
        "payload_json": json.dumps(payload, ensure_ascii=False),
    }
    return seq, json.dumps(event, ensure_ascii=False)


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE world_v2_events (
            world_id TEXT NOT NULL,
            ledger_sequence INTEGER NOT NULL,
            world_revision INTEGER NOT NULL DEFAULT 0,
            deliberation_revision INTEGER NOT NULL DEFAULT 0,
            commit_id TEXT NOT NULL DEFAULT 'c',
            event_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            event_json TEXT NOT NULL,
            event_hash TEXT NOT NULL,
            PRIMARY KEY (world_id, ledger_sequence)
        )
        """
    )
    world_id = "world:test"
    corr = "qq:1:qq-coalesced:abc"
    rows = [
        _event(
            seq=100,
            event_type="ObservationRecorded",
            correlation_id=corr,
            payload={"text": "给我看张照片嘛", "received_at": "2026-08-20T12:00:00+00:00"},
        ),
        _event(
            seq=101,
            event_type="ProposalRecorded",
            correlation_id=corr,
            payload={
                "proposal_json": json.dumps(
                    {
                        "timing_choice": "now",
                        "proposed_changes": [
                            {
                                "payload": {
                                    "canonical_json": json.dumps(
                                        {
                                            "beat_drafts": [{"text": "不拍。"}],
                                        }
                                    )
                                }
                            }
                        ],
                    }
                )
            },
        ),
        _event(
            seq=102,
            event_type="MessagePayloadStored",
            correlation_id=corr,
            payload={"message": {"text": "不拍。"}},
        ),
        _event(
            seq=200,
            event_type="ObservationRecorded",
            correlation_id="qq:1:qq-coalesced:silent",
            payload={"text": "在吗", "received_at": "2026-08-20T13:00:00+00:00"},
        ),
        _event(
            seq=201,
            event_type="ProposalRecorded",
            correlation_id="qq:1:qq-coalesced:silent",
            payload={
                "proposal_json": json.dumps(
                    {
                        "timing_choice": "silent",
                        "impulse_summary": "不想回",
                    }
                )
            },
        ),
    ]
    for seq, event_json in rows:
        conn.execute(
            """
            INSERT INTO world_v2_events (
                world_id, ledger_sequence, event_id, idempotency_key, event_json, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (world_id, seq, f"event:{seq}", f"key:{seq}", event_json, f"hash:{seq}"),
        )
    conn.commit()
    conn.close()


def test_fixture_detects_photo_refusal_and_silent(tmp_path: Path) -> None:
    db = tmp_path / "fixture.sqlite"
    _make_db(db)
    conn = sqlite3.connect(db)
    index = LedgerIndex.load(conn, world_id="world:test")
    conn.close()

    b44 = audit_b44(index)
    assert b44.state == "occurred"
    assert b44.occurred_count >= 1
    assert any("不拍" in item.quote for item in b44.instances)

    b09 = audit_b09(index)
    assert b09.state == "occurred"
    assert b09.occurred_count == 1

    verdicts = run_audits(index)
    assert len(verdicts) == 12
