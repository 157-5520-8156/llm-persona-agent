"""A real capture host can continue without replaying inputs or losing receipt IDs."""

from datetime import UTC, datetime, timedelta
import hashlib
import json
import shutil

import httpx
import pytest

from companion_daemon.world_v2.longitudinal_checkpoint import restore_closed_journey
from test_longitudinal_cli import _cli


@pytest.mark.asyncio
async def test_closed_capture_continuation_preserves_history_across_two_copies(tmp_path, monkeypatch):
    import companion_daemon.world_v2.longitudinal_journey as runner

    def no_network(*args, **kwargs):
        pytest.fail("synthetic continuation must not construct external clients")

    monkeypatch.setattr(httpx, "AsyncClient", no_network)
    monkeypatch.setattr(httpx, "Client", no_network)
    real_run = runner.run_journey
    at = datetime(2026, 9, 1, tzinfo=UTC)
    source = None
    manifests = []
    hashes = {}
    for index in range(3):
        async def continue_run(**kwargs):
            return await real_run(**kwargs, resume_from=source)

        monkeypatch.setattr(runner, "run_journey", continue_run)
        scenario = tmp_path / f"scenario-{index}.json"
        scenario.write_text(json.dumps({
            "scenario_id": f"continuation-{index}", "started_at": at.isoformat(),
            "duration_minutes": 3 * (index + 1),
            "restart_minutes": [3 * index + 2] if index else [],
            "turns": [{"id": f"input-{index}", "at_minutes": 3 * index + (1 if index else 0), "text": "今天怎么样？"}],
        }))
        output = tmp_path / f"run-{index}"
        cli = _cli()
        result = await cli.run(cli.parse_options([
            "--scenario", str(scenario), "--output", str(output), "--max-wall-seconds", "30",
        ]))
        assert result["completed"], result["stop_reason"]
        assert result["safety"]["fresh_database"] is (index == 0)
        assert result["turns_consumed"] == 1
        timeline = [json.loads(line) for line in (output / "timeline.jsonl").read_text().splitlines()]
        deliveries = [d for row in timeline for d in row["deliveries"] if d["kind"] == "text"]
        assert len(deliveries) == 1
        history = json.loads((output / "capture-delivery-history.json").read_text())
        assert [d["message_id"] for d in history] == [f"journey-message-{n + 1}" for n in range(index + 1)]
        if index:
            assert result["continuation"]["source_final_ledger_sequence"] == manifests[-1]["final_ledger_sequence"]
            assert result["continuation"]["inherited_delivery_count"] == index
            assert all(r["same_state"] and r["construction_delivery_delta"] == 0 for r in result["restarts"])
        for path, digest in hashes.items():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for name in ("manifest.json", "evidence.jsonl", "timeline.jsonl", "provider-usage.json"):
            path = output / name
            hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        source = output
        manifests.append(result)

    existing = tmp_path / "existing.sqlite"
    existing.write_bytes(b"keep")
    with pytest.raises(ValueError, match="new database"):
        restore_closed_journey(source=source, destination=existing, started_at=at)
    assert existing.read_bytes() == b"keep"
    with pytest.raises(ValueError, match="calendar origin"):
        restore_closed_journey(source=source, destination=tmp_path / "wrong-calendar.sqlite", started_at=at + timedelta(days=1))
    damaged = tmp_path / "damaged"
    shutil.copytree(source, damaged)
    with (damaged / "evidence.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="artifact changed"):
        restore_closed_journey(source=damaged, destination=tmp_path / "damaged.sqlite", started_at=at)
    unsafe = json.loads((damaged / "manifest.json").read_text())
    unsafe["safety"]["real_qq"] = True
    (damaged / "manifest.json").write_text(json.dumps(unsafe))
    with pytest.raises(ValueError, match="closed, verified capture"):
        restore_closed_journey(source=damaged, destination=tmp_path / "unsafe.sqlite", started_at=at)
