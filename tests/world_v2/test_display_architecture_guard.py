from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from companion_daemon.world_v2.display_architecture_guard import (
    DisplayArchitectureError,
    assert_v2_display_architecture,
    scan_v2_display_architecture,
)


REPOSITORY_ROOT = Path(__file__).parents[2]


def test_selected_v2_display_consumers_remain_terminal_projection_readers() -> None:
    assert_v2_display_architecture(REPOSITORY_ROOT)


def test_display_guard_reports_a_missing_v2_read_seam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import companion_daemon.world_v2.display_architecture_guard as guard

    path = tmp_path / "consumer.gd"
    path.write_text("extends Node\n", encoding="utf-8")
    violation = guard.DisplayArchitectureViolation(path, "missing_v2_read_seam", "daemon_room_url")
    monkeypatch.setattr(guard, "scan_v2_display_architecture", lambda _root: (violation,))

    with pytest.raises(DisplayArchitectureError, match="missing_v2_read_seam"):
        guard.assert_v2_display_architecture(REPOSITORY_ROOT)


def test_display_guard_scans_selected_godot_consumers_not_user_dashboard_ui() -> None:
    paths = {
        violation.path.relative_to(REPOSITORY_ROOT)
        for violation in scan_v2_display_architecture(REPOSITORY_ROOT)
    }
    assert Path("src/companion_daemon/dashboard_ui.py") not in paths


def test_display_guard_rejects_legacy_relay_in_the_selected_world_v2_dashboard(
    tmp_path: Path,
) -> None:
    selected = (
        Path("src/companion_daemon/world_v2/dashboard_projection_adapter.py"),
        Path("src/companion_daemon/world_v2/world_v2_dashboard_ui.py"),
        Path("godot/project.godot"),
        Path("godot/scripts/main.gd"),
        Path("godot/topdown/scripts/topdown_home.gd"),
        Path("prototypes/pixel-home/js/bridge.js"),
    )
    for relative_path in selected:
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY_ROOT / relative_path, destination)
    dashboard_path = tmp_path / "src/companion_daemon/world_v2/world_v2_dashboard_ui.py"
    dashboard_path.write_text(
        dashboard_path.read_text(encoding="utf-8") + "\n# /world-v2/life-state\n",
        encoding="utf-8",
    )

    violations = scan_v2_display_architecture(tmp_path)

    assert any(
        violation.path == dashboard_path
        and violation.rule == "forbidden_dashboard_dependency"
        and violation.detail == "/world-v2/life-state"
        for violation in violations
    )
