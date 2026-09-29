"""Serve current Dashboard assets against the existing owner's read-only DTO.

Run from the repository root:
    .venv/bin/python scripts/preview_world_v2_dashboard.py --port 8790

Uses the existing .env authentication setting and dedicated owner credential.
It has no world creation, tick, dispatch, chat or provider routes. Restarting
this preview does not restart the running QQ owner.
"""

from __future__ import annotations

import argparse

import uvicorn

from companion_daemon.app import create_http_asgi_app
from companion_daemon.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    app = create_http_asgi_app(settings=Settings())
    allowed = {
        "/dashboard",
        "/world-v2/dashboard/home",
        "/world-v2/dashboard/app.js",
        "/world-v2/dashboard/session",
        "/world-v2/dashboard/logout",
    }
    app.router.routes[:] = [
        route for route in app.router.routes if getattr(route, "path", None) in allowed
    ]
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
