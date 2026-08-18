#!/usr/bin/env python3
"""GET-only Civitai workflow reconcile. Never POSTs. Writes under output/civitai-async/."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "output" / "civitai-async"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow_id")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    _load_dotenv(ROOT / ".env")
    api_key = os.environ.get("CIVITAI_API_KEY", "").strip()
    if not api_key:
        print("CIVITAI_API_KEY missing", file=sys.stderr)
        return 2
    proxy = os.environ.get("CIVITAI_PROXY_URL") or os.environ.get("OPENAI_PROXY_URL") or None
    args.out_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://orchestration.civitai.com/v2/consumer/workflows/{args.workflow_id}"
    with httpx.Client(timeout=60.0, trust_env=False, proxy=proxy) as client:
        response = client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    (args.out_dir / f"{args.workflow_id}.status.json").write_text(response.text, encoding="utf-8")
    if response.status_code == 404:
        print(json.dumps({"workflow_id": args.workflow_id, "http": 404, "image": None}))
        return 1
    payload = response.json() if "json" in response.headers.get("content-type", "") else {}
    status = payload.get("status") if isinstance(payload, dict) else None
    image_path = None
    steps = payload.get("steps") if isinstance(payload, dict) else None
    image = None
    if isinstance(steps, list) and steps and isinstance(steps[0], dict):
        output = steps[0].get("output")
        images = output.get("images") if isinstance(output, dict) else None
        if isinstance(images, list) and images and isinstance(images[0], dict):
            image = images[0]
    if isinstance(image, dict) and image.get("available") is not False and image.get("url"):
        blob = httpx.get(str(image["url"]), timeout=60.0, follow_redirects=True, trust_env=False, proxy=proxy)
        if blob.is_success and blob.content:
            image_path = args.out_dir / f"{args.workflow_id}.jpg"
            image_path.write_bytes(blob.content)
    summary = {
        "workflow_id": args.workflow_id,
        "http": response.status_code,
        "status": status,
        "image": str(image_path) if image_path else None,
        "bytes": image_path.stat().st_size if image_path else 0,
        "posted": False,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if image_path else 1


if __name__ == "__main__":
    raise SystemExit(main())
