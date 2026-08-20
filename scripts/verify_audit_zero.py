#!/usr/bin/env python3
"""Verify context-truth audit zero fixes on a production clone.

Never writes ``data/``. Artifacts: ``output/audit-zero/``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
from context_truth.compile_seen import compile_seen_at_head_sync
from context_truth.ledger_truth import collect_ledger_truth
from context_truth.slots import _compare_head_included, _compare_subset, _count, _dialogue_texts

OUTPUT = (REPO / "output" / "audit-zero").resolve()
WORLD_ID = drive.WORLD_ID
ACTOR = "agent:companion"
USER = "user:geoff"
MESSAGES = (
    "27号报到那事我记着了",
    "你刚才说的开学时间我看到了",
    "要是困了就先睡，不用硬撑",
)
COST_CAP_CNY = 4.0


def _compile_evidence(clone: Path) -> dict[str, Any]:
    compiled = compile_seen_at_head_sync(
        database=clone,
        world_id=WORLD_ID,
        actor_ref=ACTOR,
        counterpart_actor_ref=USER,
        timezone_name="Asia/Shanghai",
        seed_path=REPO / "configs" / "world_seed.yaml",
    )
    seen = compiled.seen
    truth = collect_ledger_truth(
        ledger=compiled.ledger,
        projection=compiled.projection,
        counterpart_actor_ref=USER,
        sidecar_path=REPO / "data" / "external-world-perception.sqlite",
    )
    texts = _dialogue_texts(seen)
    view = compiled.model_view
    materials = view.get("materials") if isinstance(view.get("materials"), dict) else {}
    return {
        "trigger_ref": compiled.trigger_ref,
        "compile_ms": round(compiled.compile_ms, 1),
        "model_view_chars": len(json.dumps(view, ensure_ascii=False)),
        "counterpart_head_ok": _compare_head_included(texts, truth.counterpart_recent) is None,
        "no_phantom_ok": _compare_subset(
            texts,
            (
                *(truth.counterpart_all or truth.counterpart_recent),
                *(truth.companion_all_settled or truth.companion_recent_settled),
                *truth.companion_waiting,
            ),
        )
        is None,
        "affect_seen": _count(materials, "affect"),
        "affect_ledger": len(truth.active_affect_ids),
        "last_counterpart": truth.last_counterpart.text if truth.last_counterpart else None,
        "conversation_tail": (seen.conversation or [])[-5:],
        "recent_counterpart_tail": [
            item.get("text")
            for item in (materials.get("recent_dialogue") or [])
            if isinstance(item, dict) and item.get("speaker") == "counterpart"
        ][-5:],
    }


def _usage_cost(records: list[object]) -> float:
    from companion_daemon.usage_metrics import estimate_model_cost_usd

    total = 0.0
    for item in records:
        usd, _version = estimate_model_cost_usd(
            model=str(getattr(item, "model", "") or "__unpriced__"),
            prompt_tokens=int(getattr(item, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(item, "completion_tokens", 0) or 0),
            cache_hit_tokens=int(getattr(item, "cache_hit_tokens", 0) or 0),
            cache_miss_tokens=int(getattr(item, "cache_miss_tokens", 0) or 0),
        )
        total += usd * 7.2
    return round(total, 4)


async def _speak_trials(clone: Path) -> list[dict[str, Any]]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.character_interior.ports import _InteriorRoleRequest
    from companion_daemon.world_v2.character_interior.structured_role import (
        StructuredCharacterRoleFaculty,
        StructuredRoleResultError,
    )

    compiled = compile_seen_at_head_sync(
        database=clone,
        world_id=WORLD_ID,
        actor_ref=ACTOR,
        counterpart_actor_ref=USER,
        timezone_name="Asia/Shanghai",
        seed_path=REPO / "configs" / "world_seed.yaml",
    )
    snapshot = compiled.snapshot
    manifest = compiled.snapshot.capability_manifest if hasattr(compiled.snapshot, "capability_manifest") else None
    if manifest is None:
        from companion_daemon.world_v2.character_interior.inbound_author import (
            build_inbound_capability_manifest,
        )

        manifest = build_inbound_capability_manifest(snapshot=snapshot)
    settings = Settings()
    model = DeepSeekChatModel(settings=settings)
    role = StructuredCharacterRoleFaculty(
        model=model, model_id=str(getattr(model, "model", "deepseek"))
    )
    usages: list[Any] = []
    trials: list[dict[str, Any]] = []
    spent = 0.0
    for index, message in enumerate(MESSAGES, start=1):
        if spent >= COST_CAP_CNY:
            trials.append({"index": index, "status": "skipped_budget"})
            continue
        request = _InteriorRoleRequest(
            inner_turn_id=f"character-inner-turn:audit-zero:{index}",
            phase="consider",
            subject_ref=f"opportunity:audit-zero:{index}",
            trigger_ref=compiled.trigger_ref,
            purpose="inbound_turn",
            context_note=f"Counterpart just said: {message}",
            subject_source_refs=manifest.source_refs,
            capability_manifest=manifest,
            snapshot=snapshot,
        )
        before = len(usages)
        started = time.perf_counter()
        try:
            result = await role.consider(request)
        except StructuredRoleResultError as exc:
            request = request.model_copy(
                update={
                    "correction_ordinal": 1,
                    "correction_failure_code": (exc.code or "role_result_schema_invalid")[:128],
                    "correction_failure_detail": (exc.detail or str(exc))[:4096],
                }
            )
            result = await role.consider(request)
        except Exception as exc:
            trials.append(
                {
                    "index": index,
                    "inbound": message,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
            continue
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        decision = result.get("decision") if isinstance(result, dict) else None
        her_texts: list[str] = []
        if isinstance(decision, dict):
            payload = decision.get("payload")
            root = payload if isinstance(payload, dict) else decision
            beats = root.get("beats") if isinstance(root, dict) else None
            if isinstance(beats, list):
                for beat in beats:
                    if isinstance(beat, dict) and isinstance(beat.get("text"), str):
                        her_texts.append(beat["text"].strip())
        turn_cost = _usage_cost(usages[before:])
        spent += turn_cost
        prompt_tokens = sum(int(getattr(u, "prompt_tokens", 0) or 0) for u in usages[before:])
        trials.append(
            {
                "index": index,
                "inbound": message,
                "status": "ran",
                "her_texts": her_texts,
                "summary": result.get("summary") if isinstance(result, dict) else None,
                "prompt_tokens": prompt_tokens,
                "latency_ms": latency_ms,
                "cost_cny": turn_cost,
            }
        )
    return trials


def _audit_finding_count() -> int:
    proc = subprocess.run(
        [str(REPO / ".venv/bin/python"), str(REPO / "scripts/audit_context_truth.py"), "--skip-behavior"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    summary_path = REPO / "output" / "context-audit" / "summary.json"
    if not summary_path.is_file():
        raise SystemExit(f"audit failed: {proc.stderr[-500:]}")
    return int(json.loads(summary_path.read_text())["finding_count"])


def _before_after_view_chars(clone: Path) -> dict[str, int]:
    after = _compile_evidence(clone)["model_view_chars"]
    stash = subprocess.run(
        [
            "git",
            "stash",
            "push",
            "-k",
            "-m",
            "audit-zero-before",
            "--",
            "src/companion_daemon/world_v2/conversation_continuity.py",
            "src/companion_daemon/world_v2/living_state_inventory.py",
            "src/companion_daemon/world_v2/character_interior/snapshot_compiler.py",
            "scripts/context_truth/ledger_truth.py",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    try:
        before = _compile_evidence(clone)["model_view_chars"]
    finally:
        subprocess.run(["git", "stash", "pop"], cwd=REPO, capture_output=True)
    return {"before_model_view_chars": before, "after_model_view_chars": after}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-speak", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    finding_count = _audit_finding_count()
    evidence = _compile_evidence(clone)
    cost = _before_after_view_chars(clone)
    speak: list[dict[str, Any]] = []
    if not args.skip_speak:
        speak = asyncio.run(_speak_trials(clone))
    report = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "finding_count": finding_count,
        "compile_evidence": evidence,
        "model_view_chars": cost,
        "speak_trials": speak,
        "speak_cost_cny": round(sum(float(t.get("cost_cny") or 0) for t in speak), 4),
    }
    (OUTPUT / "REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Audit zero verification",
        "",
        f"- finding_count: **{finding_count}**",
        f"- counterpart_head_ok: **{evidence['counterpart_head_ok']}**",
        f"- no_phantom_ok: **{evidence['no_phantom_ok']}**",
        f"- affect seen/ledger: **{evidence['affect_seen']}/{evidence['affect_ledger']}**",
        f"- last counterpart in materials: `{evidence['last_counterpart']}`",
        f"- model_view chars before/after: {cost['before_model_view_chars']} → {cost['after_model_view_chars']}",
        "",
        "## Conversation tail (Path A)",
        "",
    ]
    for line in evidence["conversation_tail"]:
        lines.append(f"- {line}")
    if speak:
        lines.extend(["", "## Clone speak trials (her words)", ""])
        for trial in speak:
            if trial.get("status") != "ran":
                lines.append(f"- trial {trial.get('index')}: {trial.get('status')}")
                continue
            lines.append(f"- inbound: {trial.get('inbound')}")
            for text in trial.get("her_texts") or []:
                lines.append(f"  - 她：{text}")
            if trial.get("prompt_tokens"):
                lines.append(f"  - prompt_tokens: {trial.get('prompt_tokens')}")
    (OUTPUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"finding_count": finding_count, "out": str(OUTPUT)}, ensure_ascii=False))
    return 0 if finding_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
