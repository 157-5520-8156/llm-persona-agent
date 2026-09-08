"""Contract tests for the dashboard's independent pixel-home compatibility.

The life panel does not start the room. The original route conversion and
independent static mount remain available without inferring character movement.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

import companion_daemon.app as app_module
from companion_daemon.config import Settings
from companion_daemon.world_v2.world_v2_dashboard_ui import DASHBOARD_APP_JS, DASHBOARD_HTML


REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SOURCE = (REPO_ROOT / "prototypes" / "pixel-home" / "js" / "bridge.js").read_text(
    encoding="utf-8"
)
PROTOTYPE_INDEX = (REPO_ROOT / "prototypes" / "pixel-home" / "index.html").read_text(
    encoding="utf-8"
)


def test_dashboard_does_not_start_room_but_preserves_its_independent_entry() -> None:
    assert '<canvas id="stage" width="1120" height="640">' in PROTOTYPE_INDEX
    assert "<iframe" not in DASHBOARD_HTML
    assert 'href="/pixel-home/index.html"' in DASHBOARD_HTML
    assert "zhizhi-room-isometric" not in DASHBOARD_HTML
    assert "zhizhi-room-isometric" not in DASHBOARD_APP_JS
    assert "?edit=1" not in DASHBOARD_HTML
    assert "?embed=1" not in DASHBOARD_HTML
    assert 'id="roomRoute"' not in DASHBOARD_HTML


def test_dashboard_compatibility_converts_only_renderer_ready_snapshot_routes() -> None:
    assert "const DATA_URL='/world-v2/dashboard/home'" in DASHBOARD_APP_JS
    assert "credentials:'same-origin'" in DASHBOARD_APP_JS
    assert "headers['If-None-Match']=etag" in DASHBOARD_APP_JS
    assert "postMessage" not in DASHBOARD_APP_JS
    assert "room.render_state.route" in DASHBOARD_APP_JS
    assert "scene_id:route.scene_id" in DASHBOARD_APP_JS
    assert "action_id:route.action_id" in DASHBOARD_APP_JS
    assert "availability:route.availability" in DASHBOARD_APP_JS
    assert "return unavailableRoomMessage('unavailable',logicalTime)" in DASHBOARD_APP_JS
    for forbidden in (
        "/health",
        "/world-v2/life-state",
        "at_home",
        "activity_kind",
        "location_ref",
    ):
        assert forbidden not in DASHBOARD_APP_JS


def test_host_and_bridge_agree_on_message_type_and_version() -> None:
    host_type = re.search(r"ROOM_MESSAGE_TYPE='([^']+)'", DASHBOARD_APP_JS)
    bridge_type = re.search(r"MESSAGE_TYPE = '([^']+)'", BRIDGE_SOURCE)
    assert host_type is not None and bridge_type is not None
    assert host_type.group(1) == bridge_type.group(1) == "pixel-home-state"

    host_version = re.search(r"ROOM_MESSAGE_VERSION=(\d+)", DASHBOARD_APP_JS)
    bridge_version = re.search(r"MESSAGE_VERSION = (\d+)", BRIDGE_SOURCE)
    assert host_version is not None and bridge_version is not None
    assert host_version.group(1) == bridge_version.group(1) == "2"


def test_bridge_applies_explicit_routes_and_disables_embed_scheduling() -> None:
    assert "route.scene_id !== 'zhizhi-home'" in BRIDGE_SOURCE
    assert "route.availability === 'unavailable'" in BRIDGE_SOURCE
    assert "route.action_id" in BRIDGE_SOURCE
    assert "event.source !== window.parent" in BRIDGE_SOURCE
    assert "engine.autoLife = false" in BRIDGE_SOURCE
    assert "engine.timeScale = 0" in BRIDGE_SOURCE
    # The bridge resolves the explicit action against live interactions and
    # drives the engine only through its public dispatch command.
    for public_call in ("pickInteraction(", "dispatch(", "availableInteractions("):
        assert public_call in BRIDGE_SOURCE
    for forbidden in (
        "ACTIVITY_KEY_RULES",
        "DEFAULT_KEYS",
        "TAKEOVER_MS",
        "activity_kind",
        "location_ref",
    ):
        assert forbidden not in BRIDGE_SOURCE
    # index.html gains exactly one bridge script tag after main.js.
    assert PROTOTYPE_INDEX.index("js/main.js") < PROTOTYPE_INDEX.index("js/bridge.js")
    assert PROTOTYPE_INDEX.count("bridge.js") == 1
    assert "body.embed header" in PROTOTYPE_INDEX
    assert "body.embed .toolbar" in PROTOTYPE_INDEX
    assert "body.embed #iobox" in PROTOTYPE_INDEX
    assert "body.embed .stage-wrap{width:100%;border:0" in PROTOTYPE_INDEX


def test_pixel_home_prototype_is_mounted_read_only(tmp_path: Path) -> None:
    asgi_app = app_module.create_http_asgi_app(
        settings=Settings(_env_file=None, database_path=tmp_path / "pixel-home.sqlite")
    )

    with TestClient(asgi_app) as client:
        index = client.get("/pixel-home/index.html")
        bridge = client.get("/pixel-home/js/bridge.js")

    assert index.status_code == 200
    assert "js/bridge.js" in index.text
    assert bridge.status_code == 200
    assert "pixel-home-state" in bridge.text
    assert "MESSAGE_VERSION = 2" in bridge.text
    assert "zhizhi-scene-state" not in bridge.text
