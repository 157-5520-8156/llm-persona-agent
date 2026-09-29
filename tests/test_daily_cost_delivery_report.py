"""Cost reports must distinguish a deferred reply from a lost reply."""
import importlib.util
import json
from pathlib import Path


def test_final_receipts_reconcile_deferred_ingress_without_counting_provider_ack():
    path = Path(__file__).parents[1] / "scripts/benchmark_daily_cost.py"
    spec = importlib.util.spec_from_file_location("daily_cost_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def event(kind, correlation, time, payload):
        return {"event_type": kind, "correlation_id": correlation,
                "logical_time": f"2026-09-27T{time}Z", "payload_json": json.dumps(payload)}

    events = [
        event("ObservationRecorded", "a", "10:00:00", {"coalescing_metadata": {"source_event_ids": ["m1"]}}),
        event("ObservationRecorded", "b", "10:00:00", {"coalescing_metadata": {"source_event_ids": ["m2"]}}),
        event("ActionProviderAccepted", "b", "10:00:03", {"action_id": "ack-only"}),
        event("ActionDelivered", "unrelated", "10:00:03", {"action_id": "other"}),
        event("ActionDelivered", "a", "10:10:00", {"action_id": "reply"}),
        event("ActionDelivered", "a", "10:10:00", {"action_id": "reply"}),
    ]
    rows = module.chat_delivery_outcomes(events, [
        {"message_id": key, "status": "deferred", "visible": []} for key in ("m1", "m2", "missing")
    ])
    assert [row["eventual_capture_delivery"] for row in rows] == [True, False, False]
    assert rows[0]["delivered_actions"] == 1
    assert rows[0]["virtual_elapsed_seconds"] == 600
    assert not rows[0]["immediate_visible"]
    assert rows[1]["observation_found"]
    assert not rows[2]["observation_found"]
