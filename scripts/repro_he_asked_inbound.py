#!/usr/bin/env python3
"""Reproduce inbound full_turn failure when he asks for a photo.

Never writes ``data/``, never talks to 8787 / NapCat, never commits.
Clones the already-opened photo-candidate ledger under ``output/reselection/``.

Usage::

    .venv/bin/python scripts/repro_he_asked_inbound.py --trials 1
    .venv/bin/python scripts/repro_he_asked_inbound.py --trials 6
    .venv/bin/python scripts/repro_he_asked_inbound.py --relationship-only
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime
import json
import logging
import sqlite3
import sys
import traceback
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
import prove_first_photo as prove

from companion_daemon.world_v2.character_interior import inbound_author as inbound_author_mod
from companion_daemon.world_v2.relationship_reducers import (
    RELATIONSHIP_POLICY_DIGEST,
    RETIRED_RELATIONSHIP_POLICY_DIGESTS,
    relationship_state_policy_is_readable,
)

WORLD_ID = drive.WORLD_ID
DEFAULT_SOURCE = (REPO / "output" / "first-photo" / "candidate-open.sqlite").resolve()
DEFAULT_OUTPUT = (REPO / "output" / "reselection").resolve()
HE_ASKED_TEXT = prove.HE_ASKED_TEXT
_LOG = logging.getLogger("repro_he_asked_inbound")


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _exception_detail(exc: BaseException) -> dict[str, Any]:
    pydantic_errors: list[dict[str, Any]] = []
    errors_fn = getattr(exc, "errors", None)
    if callable(errors_fn):
        try:
            raw_errors = errors_fn()
        except Exception:
            raw_errors = ()
        for item in list(raw_errors)[:12]:
            if not isinstance(item, dict):
                continue
            pydantic_errors.append(
                {
                    "loc": item.get("loc"),
                    "type": item.get("type"),
                    "msg": item.get("msg"),
                }
            )
    return {
        "error_type": type(exc).__name__,
        "str": str(exc),
        "repr": repr(exc)[:2_000],
        "pydantic_errors": pydantic_errors,
        "pydantic_join": " | ".join(
            f"{'.'.join(str(part) for part in item.get('loc') or ())}:{item.get('type')}:{item.get('msg')}"
            for item in pydantic_errors
        ),
    }


class Capture:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.parse_failures: list[dict[str, Any]] = []
        self.expression_failures: list[dict[str, Any]] = []
        self.combined_raws: list[str] = []
        self.validation_failures: list[dict[str, Any]] = []

    _installed_originals: dict[str, Any] | None = None

    def install(self) -> None:
        if Capture._installed_originals is None:
            Capture._installed_originals = {
                "parse": inbound_author_mod._parse_combined,
                "materialize": inbound_author_mod.materialize_expression_draft,
            }
        original_parse = Capture._installed_originals["parse"]
        original_materialize = Capture._installed_originals["materialize"]

        def parse_combined(raw: str) -> dict[str, dict[str, Any]]:
            self.combined_raws.append(raw if isinstance(raw, str) else repr(raw))
            try:
                return original_parse(raw)
            except Exception as exc:
                self.parse_failures.append(
                    {
                        "raw": raw if isinstance(raw, str) else repr(raw),
                        "violation": _exception_detail(exc),
                    }
                )
                raise

        def materialize_expression_draft(**kwargs: Any) -> Any:
            raw = kwargs.get("raw")
            try:
                return original_materialize(**kwargs)
            except Exception as exc:
                self.expression_failures.append(
                    {
                        "raw": raw if isinstance(raw, str) else None,
                        "violation": _exception_detail(exc),
                    }
                )
                raise

        inbound_author_mod._parse_combined = parse_combined  # type: ignore[assignment]
        inbound_author_mod.materialize_expression_draft = materialize_expression_draft  # type: ignore[assignment]

        capture = self

        class Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    message = record.getMessage()
                except Exception:
                    message = str(record.msg)
                if any(
                    marker in message
                    for marker in (
                        "combined expression failed",
                        "corrective retry",
                        "compact gate",
                        "paired_expression",
                        "ROLE RESULT",
                    )
                ):
                    capture.logs.append(f"{record.name}:{record.levelname}:{message[:4_000]}")

        handler = Handler()
        handler.setLevel(logging.DEBUG)
        for name in (
            "companion_daemon.world_v2.character_interior.inbound_author",
            inbound_author_mod.__name__,
            "companion_daemon.world_v2.character_interior.inbound_wire",
            "companion_daemon.world_v2.character_interior.inbound_tool_contract",
            "companion_daemon.world_v2.character_interior.core",
        ):
            logging.getLogger(name).addHandler(handler)
            logging.getLogger(name).setLevel(logging.DEBUG)

    def snapshot(self) -> dict[str, Any]:
        return {
            "combined_raws": self.combined_raws,
            "parse_failures": self.parse_failures,
            "expression_failures": self.expression_failures,
            "logs": self.logs,
            "validation_failures": self.validation_failures,
        }


async def run_one_trial(
    *,
    source: Path,
    output_dir: Path,
    trial_id: str,
) -> dict[str, Any]:
    clone = output_dir / f"{trial_id}.sqlite"
    drive.clone_ledger(source, clone)
    usage_from = drive.current_usage_id(clone)
    capture = Capture()
    capture.install()
    session = await prove.open_prove_session(
        database=clone,
        output_dir=output_dir,
        inbound_payload=None,
        force_media_select=False,
        enable_media=True,
    )
    asked_step: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    try:
        asked_step = await session.inbound_without_background(HE_ASKED_TEXT)
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)[:2_000]}
        capture.validation_failures.append(
            {
                "error": error,
                "traceback": traceback.format_exc()[-4_000:],
            }
        )
    finally:
        await session.close()

    visible = (asked_step or {}).get("visible") or []
    status = (asked_step or {}).get("status")
    first_raw = capture.combined_raws[0] if capture.combined_raws else None
    first_expression_failure = (
        capture.expression_failures[0] if capture.expression_failures else None
    )
    first_parse_failure = capture.parse_failures[0] if capture.parse_failures else None
    return {
        "trial_id": trial_id,
        "clone": str(clone),
        "asked_text": HE_ASKED_TEXT,
        "status": status,
        "error": error,
        "visible": visible,
        "visible_ok": bool(visible) and status not in {None, "deferred"},
        "first_combined_raw": first_raw,
        "first_parse_failure": first_parse_failure,
        "first_expression_failure": first_expression_failure,
        "capture": capture.snapshot(),
        "cost": drive.cost_report(clone, since_id=usage_from),
    }


def inspect_relationship(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        events: list[dict[str, Any]] = []
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? AND json_extract(event_json,'$.event_type') IN "
            "('RelationshipSlowVariableAdjusted','RelationshipCommitmentAccepted',"
            "'RelationshipStateSeeded','RelationshipInitialized') "
            "ORDER BY ledger_sequence",
            (WORLD_ID,),
        ):
            event = json.loads(raw)
            payload = event.get("payload")
            if isinstance(event.get("payload_json"), str):
                try:
                    decoded = json.loads(event["payload_json"])
                except json.JSONDecodeError:
                    decoded = {}
                if isinstance(decoded, dict):
                    payload = decoded
            if not isinstance(payload, dict):
                payload = {}
            events.append(
                {
                    "seq": seq,
                    "event_type": event.get("event_type"),
                    "policy_version": payload.get("policy_version"),
                    "policy_digest": payload.get("policy_digest"),
                    "stage": payload.get("stage")
                    or payload.get("stage_after")
                    or payload.get("committed_stage"),
                    "stage_before": payload.get("stage_before"),
                    "stage_after": payload.get("stage_after"),
                }
            )
        # Latest carried digest: last adjustment/commitment, else genesis-like.
        latest = events[-1] if events else None
        digest = (latest or {}).get("policy_digest")
        readable = None
        if isinstance(digest, str):

            class _Stamp:
                policy_version = "relationship-policy.1"
                policy_digest = digest

            readable = relationship_state_policy_is_readable(_Stamp())
        return {
            "path": str(path),
            "installed_digest": RELATIONSHIP_POLICY_DIGEST,
            "retired_digests": sorted(RETIRED_RELATIONSHIP_POLICY_DIGESTS),
            "event_count": len(events),
            "latest": latest,
            "latest_digest_is_installed": digest == RELATIONSHIP_POLICY_DIGEST,
            "latest_digest_is_retired": digest in RETIRED_RELATIONSHIP_POLICY_DIGESTS,
            "relationship_state_policy_is_readable": readable,
            "events_tail": events[-8:],
        }
    finally:
        conn.close()


def write_report(output_dir: Path, bundle: dict[str, Any]) -> None:
    trials = bundle.get("trials") or []
    lines = [
        "# 开口要照片 inbound 复现",
        "",
        f"生成时间：{datetime.now(UTC).isoformat()}",
        "",
        f"问句：`{HE_ASKED_TEXT}`",
        "",
        "## 关系摘要",
        "",
        "```json",
        json.dumps(bundle.get("relationship") or {}, ensure_ascii=False, indent=2)[:6_000],
        "```",
        "",
        "## 回合结果",
        "",
    ]
    rates = bundle.get("rates") or {}
    lines += [
        f"- n={rates.get('n')}",
        f"- 自然回复发出：{rates.get('visible_ok')}",
        f"- deferred / 空可见：{rates.get('deferred')}",
        f"- 成功率：{rates.get('success_rate')}",
        "",
    ]
    for row in trials:
        lines += [
            f"### {row.get('trial_id')} — `{row.get('status')}`",
            "",
            f"可见：{json.dumps(row.get('visible'), ensure_ascii=False)[:800]}",
            "",
        ]
        failure = row.get("first_expression_failure") or row.get("first_parse_failure")
        if failure:
            lines += [
                "**第一次草稿失败原因：**",
                "",
                "```json",
                json.dumps(failure.get("violation"), ensure_ascii=False, indent=2)[:4_000],
                "```",
                "",
                "**第一次原始草稿：**",
                "",
                "```json",
                (failure.get("raw") or row.get("first_combined_raw") or "")[:12_000],
                "```",
                "",
            ]
        elif row.get("first_combined_raw"):
            lines += [
                "**第一次原始草稿（未在 materialize 处失败）：**",
                "",
                "```json",
                str(row.get("first_combined_raw"))[:12_000],
                "```",
                "",
            ]
        logs = (row.get("capture") or {}).get("logs") or []
        if logs:
            lines += ["日志摘录：", "", "```", *logs[:20], "```", ""]
        cost = row.get("cost") or {}
        lines += [f"花费：`{cost}`", ""]
    (output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--relationship-only", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source = args.source.resolve()
    if drive._is_production_path(source):  # noqa: SLF001
        raise SystemExit(f"refusing production source as write clone origin: {source}")
    if not source.exists():
        raise SystemExit(f"missing source clone: {source}")

    relationship = {
        "production_ro": inspect_relationship(drive.PRODUCTION_DB),
        "source_clone": inspect_relationship(source),
    }
    dump_json(output_dir / "relationship.json", relationship)
    if args.relationship_only:
        dump_json(output_dir / "BUNDLE.json", {"relationship": relationship})
        write_report(output_dir, {"relationship": relationship, "trials": [], "rates": {}})
        print(json.dumps(relationship, ensure_ascii=False, indent=2, default=str))
        return

    rows: list[dict[str, Any]] = []
    for index in range(1, args.trials + 1):
        trial_id = f"he-asked-{index}"
        _LOG.info("trial %s", trial_id)
        row = await run_one_trial(source=source, output_dir=output_dir, trial_id=trial_id)
        rows.append(row)
        dump_json(output_dir / f"{trial_id}.json", row)
        _LOG.info(
            "trial %s status=%s visible_ok=%s expression_failures=%s",
            trial_id,
            row.get("status"),
            row.get("visible_ok"),
            len((row.get("capture") or {}).get("expression_failures") or []),
        )

    visible_ok = sum(1 for row in rows if row.get("visible_ok"))
    deferred = sum(
        1
        for row in rows
        if row.get("status") == "deferred" or not row.get("visible_ok")
    )
    bundle = {
        "asked_text": HE_ASKED_TEXT,
        "source": str(source),
        "relationship": relationship,
        "trials": rows,
        "rates": {
            "n": len(rows),
            "visible_ok": visible_ok,
            "deferred": deferred,
            "success_rate": (visible_ok / len(rows) if rows else None),
            "statuses": dict(Counter(row.get("status") for row in rows)),
        },
    }
    dump_json(output_dir / "BUNDLE.json", bundle)
    write_report(output_dir, bundle)
    print(json.dumps(bundle["rates"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
