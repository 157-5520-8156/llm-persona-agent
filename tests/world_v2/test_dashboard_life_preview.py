"""Synthetic layout fixture, deliberately separate from a real owner snapshot.

Run this file with --preview for a loopback-only visual check. No World events
are accepted, no SQLite file is opened, and no model or owner service is used.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

from companion_daemon.world_v2.dashboard_home_snapshot import (
    DashboardEntitySummary,
    DashboardHomeSnapshot,
    DashboardHomeSnapshotModule,
    DashboardLabeledValue,
)
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.world_v2_dashboard_ui import DASHBOARD_APP_JS, DASHBOARD_HTML


NOW = datetime(2026, 9, 8, 6, 25, tzinfo=UTC)
PREVIEW_NOTICE = "离线布局样例 · 以下均为手工 UI 数据，不是真实角色记录"


def preview_snapshot(*, empty: bool = False) -> DashboardHomeSnapshot:
    base = asyncio.run(
        DashboardHomeSnapshotModule(
            ledger=WorldLedger.in_memory(world_id="world:layout-fixture"),
            deployment_id="deployment:layout-fixture",
            boot_id="boot:layout-fixture",
            clock=lambda: NOW,
        ).capture()
    )
    if empty:
        return base

    def value(key: str, label: str, content: str) -> DashboardLabeledValue:
        return DashboardLabeledValue(key=key, label=label, value=content)

    def item(
        kind: str,
        label: str,
        title: str,
        status: str,
        status_label: str,
        hour: int = 6,
        minute: int = 10,
        **kwargs: object,
    ) -> DashboardEntitySummary:
        return DashboardEntitySummary(
            kind=kind,
            kind_label=label,
            title=title,
            status_code=status,
            status_label=status_label,
            occurred_at=NOW.replace(hour=hour, minute=minute),
            privacy_class="shareable",
            **kwargs,
        )

    sections = base.sections
    groups = {
        "overview_life": [
            item(
                "plan",
                "活动与计划",
                "把这周的阅读笔记重新理一理",
                "active",
                "进行中",
                values=(
                    value(
                        "intention",
                        "原意图",
                        "想把散在几页里的想法整理到一起，先用半小时读读看，再决定要不要继续写。",
                    ),
                    value("intent_source", "意图来源", "当天自主选择"),
                    value("scheduled_start", "计划开始", "2026-09-08T14:00:00+08:00"),
                    value("scheduled_end", "计划结束", "2026-09-08T14:30:00+08:00"),
                ),
            ),
            item(
                "plan",
                "活动与计划",
                "傍晚留一点时间写随笔",
                "planned",
                "计划中",
                5,
                40,
                values=(value("scheduled_start", "计划开始", "2026-09-08T18:00:00+08:00"),),
            ),
            item(
                "experience",
                "经历",
                "一段已经记录的经历",
                "committed",
                "已记录",
                4,
                15,
                values=(value("source_kind", "来源", "世界事件"),),
            ),
            item(
                "world_occurrence",
                "世界事件",
                "世界事件",
                "settled",
                "已结算",
                3,
                20,
                values=(value("world_environment_status", "环境正文", "未读取"),),
            ),
            item("location", "位置", "位置记录", "visible", "未指定地点", 6, 5),
            item("resource", "身体状态", "精力", "steady", "平稳", 6, 5),
        ],
        "facts_memory_inner": [
            item(
                "memory_candidate",
                "记忆候选",
                "一次谈话留下的线索",
                "pending",
                "待复核",
                5,
                15,
                values=(value("retention_rationales", "保留依据", "后续相关性"),),
            ),
            item("memory_candidate", "记忆候选", "一条暂时保留的记忆", "active", "已保留", 4, 20),
            item("affect_episode", "情绪", "好奇 · 平静", "active", "活跃", 6, 0),
        ],
        "relationship_lifecycle": [
            item("relationship_state", "关系状态", "熟悉", "active", "已记录", 4, 0),
            item("npc", "人物", "一位已知的人物", "active", "可见", 3, 0),
        ],
    }
    for name, highlights in groups.items():
        section = getattr(sections, name)
        updated = section.model_copy(
            update={
                "state": "ready",
                "observed_at": NOW,
                "data": section.data.model_copy(
                    update={"highlights": tuple(highlights), "metrics": ()}
                ),
                "coverage": section.coverage.model_copy(
                    update={
                        "known_count": len(highlights),
                        "included_count": len(highlights),
                        "truncated": False,
                    }
                ),
            }
        )
        sections = sections.model_copy(update={name: updated})
    staged = base.model_copy(update={"sections": sections, "logical_time": NOW})
    payload = staged.model_dump(mode="json", exclude={"snapshot_hash"})
    payload["snapshot_hash"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return DashboardHomeSnapshot.model_validate_json(json.dumps(payload, ensure_ascii=False))


def test_offline_preview_uses_a_valid_typed_snapshot_and_labels_its_synthetic_scope() -> None:
    for empty in (False, True):
        snapshot = preview_snapshot(empty=empty)
        assert DashboardHomeSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    assert "不是真实角色记录" in PREVIEW_NOTICE
    assert preview_snapshot(empty=True).sections.overview_life.state == "empty"


def run_preview(*, port: int, state: str) -> None:
    snapshot = preview_snapshot(empty=state == "empty")
    payload = snapshot.model_dump_json().encode()
    html = DASHBOARD_HTML.replace(
        '<main class="wrap">',
        '<p class="capture-notice" role="note">' + PREVIEW_NOTICE + '</p><main class="wrap">',
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        calls = 0

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in ("/", "/dashboard"):
                code, content_type, body = 200, "text/html; charset=utf-8", html
            elif path == "/world-v2/dashboard/app.js":
                code, content_type, body = (
                    200,
                    "application/javascript; charset=utf-8",
                    DASHBOARD_APP_JS.encode(),
                )
            elif path == "/world-v2/dashboard/home":
                Handler.calls += 1
                fail = state == "failure" or (state == "stale" and Handler.calls > 1)
                code, content_type, body = (
                    (503, "application/json", b"{}") if fail else (200, "application/json", payload)
                )
            else:
                code, content_type, body = 404, "text/plain", b"fixture route unavailable"
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            pass

    with HTTPServer(("127.0.0.1", port), Handler) as server:
        print(
            f"{PREVIEW_NOTICE}\nhttp://127.0.0.1:{server.server_port}/dashboard ({state})",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", required=True)
    parser.add_argument("--port", type=int, default=18941)
    parser.add_argument(
        "--state", choices=("normal", "empty", "failure", "stale"), default="normal"
    )
    args = parser.parse_args()
    run_preview(port=args.port, state=args.state)
