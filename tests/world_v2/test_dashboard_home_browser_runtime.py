from __future__ import annotations

import os
from pathlib import Path
import subprocess

from companion_daemon.world_v2.world_v2_dashboard_ui import DASHBOARD_APP_JS


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SOURCE = (
    REPOSITORY_ROOT / "prototypes" / "pixel-home" / "js" / "bridge.js"
).read_text(encoding="utf-8")


def test_dashboard_home_browser_javascript_contract(tmp_path: Path) -> None:
    script = tmp_path / "dashboard-home-app.js"
    script.write_text(DASHBOARD_APP_JS, encoding="utf-8")
    environment = dict(os.environ)
    environment["DASHBOARD_APP_JS_PATH"] = str(script)

    completed = subprocess.run(
        [
            "node",
            "--test",
            "tests/js/dashboard_home_client.test.js",
            "tests/js/pixel_home_bridge.test.js",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_dashboard_browser_has_no_health_or_domain_inference_fallbacks() -> None:
    browser_forbidden = (
        "/health",
        "/world-v2/life-state",
        "activity_kind",
        "location_ref",
        "ACTIVITY_LABELS",
        "NPC_NAMES",
        "HOME_LOCATION_REF",
    )
    bridge_forbidden = (
        "activity_kind",
        "location_ref",
        "ACTIVITY_KEY_RULES",
        "DEFAULT_KEYS",
        "TAKEOVER_MS",
    )

    assert not [token for token in browser_forbidden if token in DASHBOARD_APP_JS]
    assert not [token for token in bridge_forbidden if token in BRIDGE_SOURCE]
