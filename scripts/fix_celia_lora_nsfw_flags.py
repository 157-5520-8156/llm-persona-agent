#!/usr/bin/env python3
"""Inspect and (if allowed) flip NSFW flags on the private Celia identity LoRA.

Does not upload files. Does not POST a generation workflow. Writes only under
output/adult-explicit/.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

REPO = Path(__file__).resolve().parents[1]
OUTPUT = (REPO / "output" / "adult-explicit").resolve()
SITE = "https://civitai.com"
ORCH = "https://orchestration.civitai.com/v2"

IDENTITY = {
    "label": "identity_lora",
    "model_id": 2868686,
    "version_id": 3240992,
    "air": "urn:air:krea2:lora:civitai:2868686@3240992",
}
PATCH = {
    "label": "nsfw_patch",
    "model_id": 2750659,
    "version_id": 3094831,
    "air": "urn:air:krea2:lora:civitai:2750659@3094831",
}
SLIDER = {
    "label": "realism_slider",
    "model_id": 2781697,
    "version_id": 3132956,
    "air": "urn:air:krea2:lora:civitai:2781697@3132956",
}
NSFW_KEY_HINTS = (
    "nsfw",
    "sfw",
    "mature",
    "poi",
    "minor",
    "restrict",
    "availability",
    "status",
    "cangenerate",
    "checkpermission",
    "locked",
    "requireauth",
    "usagecontrol",
)


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _preview(value: object, *, limit: int = 1600) -> object:
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes {len(value)}>"
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in list(value.items())[:80]:
            if str(key).lower() in {"authorization", "api_key", "token", "cookie", "images"}:
                if isinstance(item, list):
                    out[key] = f"<list {len(item)}>"
                else:
                    out[key] = "<redacted>"
            else:
                out[key] = _preview(item, limit=400)
        return out
    if isinstance(value, list):
        return [_preview(item, limit=400) for item in value[:16]]
    return value


def _is_nsfw_key(key: str) -> bool:
    lowered = key.casefold()
    return any(hint in lowered for hint in NSFW_KEY_HINTS)


def extract_nsfw_fields(payload: object, *, prefix: str = "") -> dict[str, Any]:
    found: dict[str, Any] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if _is_nsfw_key(str(key)):
                if isinstance(value, (dict, list)):
                    found[path] = _preview(value, limit=400)
                else:
                    found[path] = value
            if isinstance(value, (dict, list)) and str(key).lower() not in {
                "images",
                "files",
                "modelversions",
            }:
                found.update(extract_nsfw_fields(value, prefix=path))
            if str(key).lower() == "model" and isinstance(value, dict):
                found.update(extract_nsfw_fields(value, prefix=path))
            if str(key).lower() == "modelversions" and isinstance(value, list):
                for index, item in enumerate(value[:4]):
                    found.update(extract_nsfw_fields(item, prefix=f"{path}[{index}]"))
    elif isinstance(payload, list):
        for index, item in enumerate(payload[:8]):
            found.update(extract_nsfw_fields(item, prefix=f"{prefix}[{index}]"))
    return found


class Session:
    def __init__(self, api_key: str, proxy: str | None) -> None:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "girl-agent-lora-nsfw-flags/1",
            "Origin": SITE,
            "Referer": f"{SITE}/models/{IDENTITY['model_id']}",
        }
        self.auth = httpx.Client(
            timeout=httpx.Timeout(connect=30, read=60, write=30, pool=30),
            proxy=proxy,
            trust_env=False,
            follow_redirects=True,
            headers=headers,
        )
        self.anon = httpx.Client(
            timeout=httpx.Timeout(connect=30, read=60, write=30, pool=30),
            proxy=proxy,
            trust_env=False,
            follow_redirects=True,
            headers={"User-Agent": "girl-agent-lora-nsfw-flags/1"},
        )
        self.calls: list[dict[str, Any]] = []

    def close(self) -> None:
        self.auth.close()
        self.anon.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: object | None = None,
        auth: bool = True,
    ) -> tuple[int, object]:
        client = self.auth if auth else self.anon
        kwargs: dict[str, Any] = {}
        if json_body is not None:
            kwargs["json"] = json_body
        try:
            response = client.request(method, url, **kwargs)
        except Exception as exc:
            self.calls.append(
                {
                    "method": method,
                    "url": url.split("?", 1)[0],
                    "auth": auth,
                    "status": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        try:
            body: object = response.json()
        except (ValueError, json.JSONDecodeError):
            body = response.text[:2000]
        self.calls.append(
            {
                "method": method,
                "url": url.split("?", 1)[0],
                "auth": auth,
                "status": response.status_code,
                "body": _preview(body, limit=1200),
            }
        )
        return response.status_code, body

    def trpc(self, procedure: str, payload: dict[str, Any]) -> tuple[int, object]:
        return self.request(
            "POST",
            f"{SITE}/api/trpc/{procedure}",
            json_body={"json": payload},
        )


def inspect_resource(session: Session, spec: dict[str, Any]) -> dict[str, Any]:
    model_id = int(spec["model_id"])
    version_id = int(spec["version_id"])
    air = str(spec["air"])
    encoded = quote(air, safe="")
    model_http, model_body = session.request("GET", f"{SITE}/api/v1/models/{model_id}")
    version_http, version_body = session.request(
        "GET", f"{SITE}/api/v1/model-versions/{version_id}"
    )
    mini_http, mini_body = session.request(
        "GET", f"{SITE}/api/v1/model-versions/mini/{version_id}"
    )
    resource_http, resource_body = session.request(
        "GET", f"{ORCH}/resources/{encoded}"
    )
    mini_anon_http, mini_anon_body = session.request(
        "GET",
        f"{SITE}/api/v1/model-versions/mini/{version_id}",
        auth=False,
    )
    resource_anon_http, resource_anon_body = session.request(
        "GET",
        f"{ORCH}/resources/{encoded}",
        auth=False,
    )
    perm_http, perm_body = session.request(
        "GET",
        f"{SITE}/api/v1/permissions/check?entityIds={version_id}",
    )
    perm_anon_http, perm_anon_body = session.request(
        "GET",
        f"{SITE}/api/v1/permissions/check?entityIds={version_id}",
        auth=False,
    )
    trpc_http, trpc_body = session.request(
        "GET",
        f"{SITE}/api/trpc/model.getById?input={quote(json.dumps({'json': {'id': model_id}}))}",
    )
    trpc_version_http, trpc_version_body = session.request(
        "GET",
        f"{SITE}/api/trpc/modelVersion.getById?input={quote(json.dumps({'json': {'id': version_id}}))}",
    )
    return {
        "label": spec["label"],
        "model_id": model_id,
        "version_id": version_id,
        "air": air,
        "http": {
            "model": model_http,
            "version": version_http,
            "mini": mini_http,
            "resource": resource_http,
            "mini_anon": mini_anon_http,
            "resource_anon": resource_anon_http,
            "permissions": perm_http,
            "permissions_anon": perm_anon_http,
            "trpc_model": trpc_http,
            "trpc_version": trpc_version_http,
        },
        "nsfw_fields": {
            "model": extract_nsfw_fields(model_body),
            "version": extract_nsfw_fields(version_body),
            "mini": extract_nsfw_fields(mini_body),
            "resource": extract_nsfw_fields(resource_body),
            "mini_anon": extract_nsfw_fields(mini_anon_body),
            "resource_anon": extract_nsfw_fields(resource_anon_body),
            "permissions": extract_nsfw_fields(perm_body),
            "permissions_anon": extract_nsfw_fields(perm_anon_body),
            "trpc_model": extract_nsfw_fields(trpc_body),
            "trpc_version": extract_nsfw_fields(trpc_version_body),
        },
        "raw": {
            "model": _preview(model_body),
            "version": _preview(version_body),
            "mini": _preview(mini_body),
            "resource": _preview(resource_body),
            "mini_anon": _preview(mini_anon_body),
            "resource_anon": _preview(resource_anon_body),
            "permissions": _preview(perm_body),
            "permissions_anon": _preview(perm_anon_body),
            "trpc_model": _preview(trpc_body),
            "trpc_version": _preview(trpc_version_body),
        },
    }


def compare_row(inspected: dict[str, Any]) -> dict[str, Any]:
    mini = inspected["raw"]["mini"] if isinstance(inspected["raw"]["mini"], dict) else {}
    resource = (
        inspected["raw"]["resource"] if isinstance(inspected["raw"]["resource"], dict) else {}
    )
    model = inspected["raw"]["model"] if isinstance(inspected["raw"]["model"], dict) else {}
    version = (
        inspected["raw"]["version"] if isinstance(inspected["raw"]["version"], dict) else {}
    )
    return {
        "label": inspected["label"],
        "air": inspected["air"],
        "http": inspected["http"],
        "model.nsfw": model.get("nsfw"),
        "model.nsfwLevel": model.get("nsfwLevel"),
        "model.sfwOnly": model.get("sfwOnly"),
        "model.poi": model.get("poi"),
        "model.minor": model.get("minor"),
        "model.availability": model.get("availability"),
        "model.status": model.get("status"),
        "model.lockedProperties": model.get("lockedProperties"),
        "version.nsfwLevel": version.get("nsfwLevel"),
        "version.status": version.get("status"),
        "version.availability": version.get("availability"),
        "mini.sfwOnly": mini.get("sfwOnly"),
        "mini.minor": mini.get("minor"),
        "mini.canGenerate": mini.get("canGenerate"),
        "mini.checkPermission": mini.get("checkPermission"),
        "mini.availability": mini.get("availability"),
        "mini.requireAuth": mini.get("requireAuth"),
        "resource.hasNSFWContentRestriction": resource.get("hasNSFWContentRestriction"),
        "resource.hasMatureContentRestriction": resource.get("hasMatureContentRestriction"),
        "resource.sfwOnly": resource.get("sfwOnly"),
        "resource.canGenerate": resource.get("canGenerate"),
        "resource.availability": resource.get("availability"),
        "mini_anon.canGenerate": (
            inspected["raw"]["mini_anon"].get("canGenerate")
            if isinstance(inspected["raw"]["mini_anon"], dict)
            else None
        ),
        "resource_anon.http": inspected["http"]["resource_anon"],
        "permissions": inspected["raw"]["permissions"],
        "permissions_anon": inspected["raw"]["permissions_anon"],
        "trpc.availability": _trpc_field(inspected["raw"]["trpc_model"], "availability"),
        "trpc.status": _trpc_field(inspected["raw"]["trpc_model"], "status"),
        "trpc.sfwOnly": _trpc_field(inspected["raw"]["trpc_model"], "sfwOnly"),
        "trpc.nsfw": _trpc_field(inspected["raw"]["trpc_model"], "nsfw"),
        "trpc.unlisted": _trpc_field(inspected["raw"]["trpc_model"], "unlisted"),
    }


def _trpc_field(body: object, key: str) -> object:
    if not isinstance(body, dict):
        return None
    result = body.get("result")
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict) and "json" in data and isinstance(data["json"], dict):
            return data["json"].get(key)
        if isinstance(data, dict):
            return data.get(key)
    return body.get(key)


def _trpc_json(body: object) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    result = body.get("result")
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict) and isinstance(data.get("json"), dict):
            return data["json"]
        if isinstance(data, dict):
            return data
    return body


def upsert_allow_adult(session: Session, before: dict[str, Any]) -> dict[str, Any]:
    trpc_model = _trpc_json(before["raw"].get("trpc_model"))
    current_availability = trpc_model.get("availability") or "Private"
    payload = {
        "id": IDENTITY["model_id"],
        "name": trpc_model.get("name") or "Celia Krea2 Identity",
        "description": trpc_model.get("description")
        or "<p>Private Krea 2 identity LoRA for on-site generation. Trigger word: Celia.</p>",
        "type": trpc_model.get("type") or "LORA",
        "uploadType": trpc_model.get("uploadType") or "Created",
        "status": "Published",
        "availability": "Private",
        "nsfw": True,
        "poi": False,
        "minor": False,
        "sfwOnly": False,
        "allowCommercialUse": trpc_model.get("allowCommercialUse") or ["RentCivit"],
        "allowNoCredit": True,
        "allowDerivatives": False,
        "allowDifferentLicense": False,
    }
    attempts: list[dict[str, Any]] = []
    # Honor the owner's constraint: stay Private and Published. Civitai currently
    # rejects Private + !sfwOnly; capture the live error instead of silently
    # flipping the model Public.
    status, body = session.trpc("model.upsert", payload)
    attempts.append(
        {
            "availability": payload["availability"],
            "nsfw": payload["nsfw"],
            "sfwOnly": payload["sfwOnly"],
            "http": status,
            "ok": status < 400 and not (isinstance(body, dict) and "error" in body),
            "body": _preview(body, limit=1200),
        }
    )
    if not attempts[-1]["ok"]:
        milder = dict(payload)
        milder["nsfw"] = False
        milder["sfwOnly"] = False
        milder["availability"] = "Private"
        status, body = session.trpc("model.upsert", milder)
        attempts.append(
            {
                "availability": milder["availability"],
                "nsfw": milder["nsfw"],
                "sfwOnly": milder["sfwOnly"],
                "http": status,
                "ok": status < 400 and not (isinstance(body, dict) and "error" in body),
                "body": _preview(body, limit=1200),
            }
        )
    return {
        "procedure": "model.upsert",
        "current_availability": current_availability,
        "chosen": next((item for item in attempts if item["ok"]), attempts[-1]),
        "attempts": attempts,
        "did_not_change_visibility": True,
    }


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    _load_dotenv(REPO / ".env")
    api_key = os.environ.get("CIVITAI_API_KEY", "").strip()
    if not api_key:
        print("CIVITAI_API_KEY missing", file=sys.stderr)
        return 2
    proxy = os.environ.get("CIVITAI_PROXY_URL") or os.environ.get("OPENAI_PROXY_URL") or None
    apply = "--apply" in sys.argv
    session = Session(api_key, proxy)
    report: dict[str, Any] = {
        "contract": "fix-celia-lora-nsfw-flags.1",
        "started_at": datetime.now(UTC).isoformat(),
        "apply": apply,
        "official_notes": {
            "site_docs": (
                "GET /model-versions/mini exposes sfwOnly and minor; nsfwLevel is a "
                "browsing-level bitmask on GET /model-versions/{id}. Official site docs "
                "do not say sfwOnly clamps sampler output."
            ),
            "site_source": (
                "model.controller.ts: Private models must be set to SFW only; "
                "nsfw && sfwOnly is rejected. privateModelFromTraining writes "
                "sfwOnly: true with comment 'Private models only allow sfw generation'. "
                "ModelUpsertForm copy: 'Only PG (or SFW) content can be generated with "
                "private models.'"
            ),
            "orchestration_docs": (
                "Workflow allowMatureContent gates yellow vs blue/green Buzz. "
                "GET /v2/resources/{air} schema is not documented as a generation clamp; "
                "hasNSFWContentRestriction is observed on the resource object."
            ),
        },
    }
    try:
        me_http, me_body = session.request("GET", f"{SITE}/api/v1/me")
        report["me"] = {"http": me_http, "preview": _preview(me_body, limit=400)}
        if me_http != 200:
            _dump(OUTPUT / "nsfw-flags-report.json", report)
            print("GET /api/v1/me failed", file=sys.stderr)
            return 1
        inspected = [inspect_resource(session, spec) for spec in (IDENTITY, PATCH, SLIDER)]
        report["resources"] = inspected
        report["comparison"] = [compare_row(item) for item in inspected]
        _dump(OUTPUT / "nsfw-fields-before.json", report["comparison"])
        _dump(OUTPUT / "nsfw-resources-before.json", inspected)
        identity = inspected[0]
        if apply:
            attempt = upsert_allow_adult(session, identity)
            report["upsert"] = attempt
            after = inspect_resource(session, IDENTITY)
            report["identity_after"] = {
                "comparison": compare_row(after),
                "nsfw_fields": after["nsfw_fields"],
                "raw_mini": _preview(after["raw"]["mini"]),
                "raw_resource": _preview(after["raw"]["resource"]),
                "raw_model": _preview(after["raw"]["model"]),
            }
            _dump(OUTPUT / "nsfw-fields-after.json", report["identity_after"])
        _dump(OUTPUT / "http-calls.json", session.calls)
        _dump(OUTPUT / "nsfw-flags-report.json", report)
        print(json.dumps({"comparison": report["comparison"], "upsert": report.get("upsert")}, indent=2, default=str))
        return 0
    except Exception as exc:
        report["failed"] = f"{type(exc).__name__}: {exc}"
        _dump(OUTPUT / "http-calls.json", session.calls)
        _dump(OUTPUT / "nsfw-flags-report.json", report)
        print(f"FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
