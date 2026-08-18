#!/usr/bin/env python3
"""Upload Celia.safetensors to Civitai as Private + Published. One file only.

Uses CIVITAI_API_KEY from .env. Never uploads training images. Writes state
under output/adult-render-v2/. Resume-safe: a completed step is not repeated.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (ROOT / "output" / "adult-render-v2").resolve()
STATE_PATH = OUTPUT / "upload-state.json"
LORA_PATH = ROOT / "Celia.safetensors"
SITE = "https://civitai.com"
ORCH = "https://orchestration.civitai.com/v2"
EXPECTED_SHA256 = "b7f84ef93bf47fd23e1a7a4c63052285f76c48f44d75621b7c48ab86be43846f"
EXPECTED_BYTES = 228_587_760

MODEL_NAME = "Celia Krea2 Identity"
VERSION_NAME = "v1"
TRIGGER = "Celia"
BASE_MODEL = "Krea 2"
DESCRIPTION_HTML = (
    "<p>Private Krea 2 identity LoRA for on-site generation. "
    "Trigger word: Celia. Rank 32.</p>"
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


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"contract": "upload-celia-krea2-lora.1", "steps": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(UTC).isoformat()
    _dump(STATE_PATH, state)


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _preview(value: object, *, limit: int = 2000) -> object:
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes {len(value)}>"
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in list(value.items())[:60]:
            if str(key).lower() in {"authorization", "api_key", "token", "cookie"}:
                out[key] = "<redacted>"
            else:
                out[key] = _preview(item, limit=400)
        return out
    if isinstance(value, list):
        return [_preview(item, limit=400) for item in value[:12]]
    return value


class CivitaiSession:
    def __init__(self, api_key: str, proxy: str | None) -> None:
        self.api_key = api_key
        self.calls: list[dict[str, Any]] = []
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "girl-agent-lora-upload/1",
            "Origin": SITE,
            "Referer": f"{SITE}/models/create",
        }
        self.client = httpx.Client(
            timeout=httpx.Timeout(connect=30, read=180, write=180, pool=30),
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
            headers={"User-Agent": "girl-agent-lora-upload/1"},
        )

    def close(self) -> None:
        self.client.close()
        self.anon.close()

    def _record(
        self,
        method: str,
        url: str,
        status: int | None,
        body: object,
        *,
        error: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "method": method,
                "url": _redact_url(url),
                "status": status,
                "error": error,
                "body": _preview(body),
            }
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: object | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        auth: bool = True,
    ) -> httpx.Response:
        client = self.client if auth else self.anon
        kwargs: dict[str, Any] = {}
        if json_body is not None:
            kwargs["json"] = json_body
        if content is not None:
            kwargs["content"] = content
        if headers:
            kwargs["headers"] = headers
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            response = client.request(method, url, **kwargs)
        except Exception as exc:
            self._record(method, url, None, None, error=f"{type(exc).__name__}: {exc}")
            raise
        body: object
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            body = f"<bytes {len(response.content)} status={response.status_code}>"
        self._record(method, url, response.status_code, body)
        return response

    def trpc(self, procedure: str, payload: dict[str, Any]) -> tuple[int, object]:
        response = self.request(
            "POST",
            f"{SITE}/api/trpc/{procedure}",
            json_body={"json": payload},
        )
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            body = {"raw": response.text[:2000]}
        return response.status_code, body

    def trpc_result(self, procedure: str, payload: dict[str, Any]) -> object:
        status, body = self.trpc(procedure, payload)
        if status >= 400:
            raise RuntimeError(
                f"tRPC {procedure} HTTP {status}: {_preview(body, limit=1500)}"
            )
        if not isinstance(body, dict):
            raise RuntimeError(f"tRPC {procedure} non-object: {body!r}")
        if "error" in body:
            raise RuntimeError(
                f"tRPC {procedure} error: {_preview(body.get('error'), limit=1500)}"
            )
        result = body.get("result")
        if isinstance(result, dict) and "data" in result:
            data = result["data"]
            if isinstance(data, dict) and "json" in data:
                return data["json"]
            return data
        return body


def _trpc_error_text(body: object) -> str:
    if not isinstance(body, dict):
        return str(body)
    err = body.get("error")
    if isinstance(err, dict):
        json_err = err.get("json") if isinstance(err.get("json"), dict) else err
        if isinstance(json_err, dict):
            return str(json_err.get("message") or json_err)
    return str(_preview(body, limit=800))


def probe(session: CivitaiSession) -> dict[str, Any]:
    me = session.request("GET", f"{SITE}/api/v1/me")
    me_body = me.json() if me.headers.get("content-type", "").startswith("application/json") else me.text[:500]
    known = session.request(
        "GET",
        f"{ORCH}/resources/{quote('urn:air:krea2:lora:civitai:2750659@3094831', safe='')}",
    )
    enums = session.request("GET", f"{SITE}/api/v1/enums")
    enums_body: object
    try:
        enums_body = enums.json()
    except (ValueError, json.JSONDecodeError):
        enums_body = enums.text[:500]
    base_models = None
    if isinstance(enums_body, dict):
        for key in ("baseModels", "baseModel", "BaseModel"):
            if key in enums_body:
                base_models = enums_body[key]
                break
        if base_models is None:
            base_models = {
                k: _preview(v, limit=200)
                for k, v in list(enums_body.items())[:20]
            }
    return {
        "me_http": me.status_code,
        "me": _preview(me_body, limit=800),
        "known_lora_resource_http": known.status_code,
        "known_lora_resource": _preview(
            known.json() if known.content else None, limit=800
        ),
        "enums_http": enums.status_code,
        "base_models_preview": _preview(base_models, limit=1200),
    }


def upsert_model(session: CivitaiSession, state: dict[str, Any]) -> dict[str, Any]:
    existing = (state.get("steps") or {}).get("model")
    if isinstance(existing, dict) and existing.get("id"):
        return existing
    payload = {
        "name": MODEL_NAME,
        "description": DESCRIPTION_HTML,
        "type": "LORA",
        "uploadType": "Created",
        "status": "Draft",
        "availability": "Private",
        "nsfw": False,
        "poi": False,
        "minor": False,
        # Civitai 2026: Private models must be SFW-only. Live model.upsert
        # rejects Private + sfwOnly=false with 400. That flag is what
        # orchestration exposes as hasNSFWContentRestriction.
        "sfwOnly": True,
        "allowCommercialUse": ["RentCivit"],
        "allowNoCredit": True,
        "allowDerivatives": False,
        "allowDifferentLicense": False,
        "tagsOnModels": [{"name": "character"}],
    }
    status, body = session.trpc("model.upsert", payload)
    if status >= 400 or (isinstance(body, dict) and "error" in body):
        payload.pop("availability", None)
        status, body = session.trpc("model.upsert", payload)
    if status >= 400 or (isinstance(body, dict) and "error" in body):
        raise RuntimeError(f"model.upsert failed HTTP {status}: {_trpc_error_text(body)}")
    result = body
    if isinstance(body, dict) and "result" in body:
        data = body["result"].get("data") if isinstance(body["result"], dict) else None
        result = data.get("json") if isinstance(data, dict) and "json" in data else data
    if not isinstance(result, dict) or not result.get("id"):
        raise RuntimeError(f"model.upsert missing id: {_preview(result)}")
    record = {
        "id": result["id"],
        "name": result.get("name"),
        "status": result.get("status"),
        "availability": result.get("availability"),
        "raw": _preview(result),
    }
    state.setdefault("steps", {})["model"] = record
    _save_state(state)
    return record


def upsert_version(
    session: CivitaiSession, state: dict[str, Any], model_id: int
) -> dict[str, Any]:
    existing = (state.get("steps") or {}).get("version")
    if isinstance(existing, dict) and existing.get("id"):
        return existing
    payload = {
        "modelId": model_id,
        "name": VERSION_NAME,
        "baseModel": BASE_MODEL,
        "description": DESCRIPTION_HTML,
        "trainedWords": [TRIGGER],
        "skipTrainedWords": False,
        "steps": 6500,
        "epochs": 108,
        "uploadType": "Created",
        "usageControl": "Download",
        "requireAuth": True,
        "recommendedResources": [],
    }
    status, body = session.trpc("modelVersion.upsert", payload)
    if status >= 400 or (isinstance(body, dict) and "error" in body):
        payload.pop("usageControl", None)
        payload.pop("requireAuth", None)
        status, body = session.trpc("modelVersion.upsert", payload)
    if status >= 400 or (isinstance(body, dict) and "error" in body):
        raise RuntimeError(
            f"modelVersion.upsert failed HTTP {status}: {_trpc_error_text(body)}"
        )
    result = body
    if isinstance(body, dict) and "result" in body:
        data = body["result"].get("data") if isinstance(body["result"], dict) else None
        result = data.get("json") if isinstance(data, dict) and "json" in data else data
    if not isinstance(result, dict) or not result.get("id"):
        raise RuntimeError(f"modelVersion.upsert missing id: {_preview(result)}")
    record = {
        "id": result["id"],
        "name": result.get("name"),
        "baseModel": result.get("baseModel"),
        "status": result.get("status"),
        "availability": result.get("availability"),
        "raw": _preview(result),
    }
    state.setdefault("steps", {})["version"] = record
    _save_state(state)
    return record


def upload_weights(
    session: CivitaiSession, state: dict[str, Any], version_id: int
) -> dict[str, Any]:
    existing = (state.get("steps") or {}).get("file")
    if isinstance(existing, dict) and existing.get("created"):
        return existing
    size = LORA_PATH.stat().st_size
    filename = LORA_PATH.name
    sign = session.request(
        "POST",
        f"{SITE}/api/upload",
        json_body={"filename": filename, "type": "model", "size": size},
    )
    if sign.status_code >= 400:
        raise RuntimeError(
            f"/api/upload failed HTTP {sign.status_code}: {_preview(sign.json() if sign.content else sign.text)}"
        )
    signed = sign.json()
    if not isinstance(signed, dict) or "urls" not in signed:
        raise RuntimeError(f"/api/upload missing urls: {_preview(signed)}")
    urls = signed["urls"]
    if not isinstance(urls, list) or not urls:
        raise RuntimeError(f"/api/upload empty urls: {_preview(signed)}")
    chunk_size = int(signed.get("chunkSize") or (25 * 1024 * 1024))
    parts: list[dict[str, Any]] = []
    with LORA_PATH.open("rb") as handle:
        for item in urls:
            part_number = int(item["partNumber"])
            url = str(item["url"])
            start = (part_number - 1) * chunk_size
            handle.seek(start)
            remaining = size - start
            nbytes = remaining if part_number == len(urls) else min(chunk_size, remaining)
            blob = handle.read(nbytes)
            put = session.request(
                "PUT",
                url,
                content=blob,
                headers={"Content-Type": "application/octet-stream"},
                timeout=600,
                auth=False,
            )
            if put.status_code >= 400:
                try:
                    session.request(
                        "POST",
                        f"{SITE}/api/upload/abort",
                        json_body={
                            "bucket": signed.get("bucket"),
                            "key": signed.get("key"),
                            "type": "model",
                            "uploadId": signed.get("uploadId"),
                            "backend": signed.get("backend"),
                        },
                    )
                except Exception:
                    pass
                raise RuntimeError(
                    f"S3 PUT part {part_number} HTTP {put.status_code}: {put.text[:500]}"
                )
            etag = put.headers.get("ETag") or put.headers.get("etag")
            if not etag:
                raise RuntimeError(f"S3 PUT part {part_number} missing ETag")
            parts.append({"ETag": etag, "PartNumber": part_number})
            print(f"uploaded part {part_number}/{len(urls)} ({len(blob)} bytes)", flush=True)
    parts.sort(key=lambda item: int(item["PartNumber"]))
    complete_body = {
        "bucket": signed.get("bucket"),
        "key": signed.get("key"),
        "type": "model",
        "uploadId": signed.get("uploadId"),
        "parts": parts,
        "backend": signed.get("backend"),
    }
    complete = session.request(
        "POST", f"{SITE}/api/upload/complete", json_body=complete_body
    )
    if complete.status_code >= 400:
        raise RuntimeError(
            f"/api/upload/complete HTTP {complete.status_code}: "
            f"{_preview(complete.json() if complete.content else complete.text)}"
        )
    file_url = str(urls[0]["url"]).split("?", 1)[0]
    create_payload = {
        "name": filename,
        "url": file_url,
        "sizeKB": size / 1024.0,
        "type": "Model",
        "modelVersionId": version_id,
        "visibility": "Private",
        "metadata": {"format": "SafeTensor", "fp": "bf16", "size": "full"},
        "backend": signed.get("backend"),
        "s3Path": signed.get("key"),
    }
    status, body = session.trpc("modelFile.create", create_payload)
    if status >= 400 or (isinstance(body, dict) and "error" in body):
        create_payload.pop("visibility", None)
        create_payload.pop("backend", None)
        create_payload.pop("s3Path", None)
        status, body = session.trpc("modelFile.create", create_payload)
    if status >= 400 or (isinstance(body, dict) and "error" in body):
        raise RuntimeError(
            f"modelFile.create failed HTTP {status}: {_trpc_error_text(body)}"
        )
    result = body
    if isinstance(body, dict) and "result" in body:
        data = body["result"].get("data") if isinstance(body["result"], dict) else None
        result = data.get("json") if isinstance(data, dict) and "json" in data else data
    record = {
        "created": True,
        "url": file_url,
        "bucket": signed.get("bucket"),
        "key": signed.get("key"),
        "backend": signed.get("backend"),
        "parts": len(parts),
        "size_bytes": size,
        "raw": _preview(result),
    }
    state.setdefault("steps", {})["file"] = record
    _save_state(state)
    return record


def publish(session: CivitaiSession, state: dict[str, Any], model_id: int, version_id: int) -> dict[str, Any]:
    existing = (state.get("steps") or {}).get("publish")
    if isinstance(existing, dict) and existing.get("ok"):
        return existing
    attempts: list[dict[str, Any]] = []
    payloads = [
        ("model.publish", {"id": model_id, "versionIds": [version_id]}),
        ("modelVersion.publish", {"id": version_id}),
        (
            "model.upsert",
            {
                "id": model_id,
                "name": MODEL_NAME,
                "description": DESCRIPTION_HTML,
                "type": "LORA",
                "uploadType": "Created",
                "status": "Published",
                "availability": "Private",
                "nsfw": False,
                "poi": False,
                "minor": False,
                "sfwOnly": True,
            },
        ),
        (
            "model.privateModelFromTraining",
            {
                "id": model_id,
                "name": MODEL_NAME,
                "description": DESCRIPTION_HTML,
                "type": "LORA",
                "uploadType": "Created",
                "status": "Published",
                "availability": "Private",
                "nsfw": False,
                "poi": False,
                "minor": False,
                "sfwOnly": True,
                "modelVersionIds": [version_id],
            },
        ),
    ]
    last_error = None
    for procedure, payload in payloads:
        status, body = session.trpc(procedure, payload)
        ok = status < 400 and not (isinstance(body, dict) and "error" in body)
        attempts.append(
            {
                "procedure": procedure,
                "http": status,
                "ok": ok,
                "error": None if ok else _trpc_error_text(body),
                "body": _preview(body, limit=600),
            }
        )
        if ok:
            record = {"ok": True, "procedure": procedure, "attempts": attempts}
            state.setdefault("steps", {})["publish"] = record
            _save_state(state)
            return record
        last_error = _trpc_error_text(body)
    raise RuntimeError(f"publish failed: {last_error}; attempts={attempts}")


def _air_from_version(payload: dict[str, Any], model_id: int, version_id: int) -> str | None:
    air = payload.get("air")
    if isinstance(air, str) and air.startswith("urn:air:"):
        return air
    return f"urn:air:krea2:lora:civitai:{model_id}@{version_id}"


def wait_schedulable(
    session: CivitaiSession,
    state: dict[str, Any],
    *,
    model_id: int,
    version_id: int,
    deadline_s: float = 30 * 60,
) -> dict[str, Any]:
    started = time.time()
    samples: list[dict[str, Any]] = []
    air = None
    while time.time() - started < deadline_s:
        mini = session.request(
            "GET", f"{SITE}/api/v1/model-versions/mini/{version_id}"
        )
        version = session.request(
            "GET", f"{SITE}/api/v1/model-versions/{version_id}"
        )
        model = session.request("GET", f"{SITE}/api/v1/models/{model_id}")
        mini_body: object
        version_body: object
        try:
            mini_body = mini.json()
        except (ValueError, json.JSONDecodeError):
            mini_body = mini.text[:400]
        try:
            version_body = version.json()
        except (ValueError, json.JSONDecodeError):
            version_body = version.text[:400]
        if isinstance(mini_body, dict):
            air = mini_body.get("air") or air
        if isinstance(version_body, dict):
            air = version_body.get("air") or air
        if not air:
            air = f"urn:air:krea2:lora:civitai:{model_id}@{version_id}"
        resource = session.request(
            "GET", f"{ORCH}/resources/{quote(air, safe='')}"
        )
        resource_anon = session.request(
            "GET",
            f"{ORCH}/resources/{quote(air, safe='')}",
            auth=False,
        )
        mini_anon = session.request(
            "GET",
            f"{SITE}/api/v1/model-versions/mini/{version_id}",
            auth=False,
        )
        can_generate = None
        if isinstance(mini_body, dict):
            can_generate = mini_body.get("canGenerate")
        resource_body: object
        try:
            resource_body = resource.json() if resource.content else None
        except (ValueError, json.JSONDecodeError):
            resource_body = resource.text[:400]
        if isinstance(resource_body, dict) and can_generate is None:
            can_generate = resource_body.get("canGenerate") or resource_body.get(
                "hasAccess"
            )
        sample = {
            "elapsed_s": round(time.time() - started, 1),
            "mini_http": mini.status_code,
            "version_http": version.status_code,
            "model_http": model.status_code,
            "resource_http": resource.status_code,
            "resource_anon_http": resource_anon.status_code,
            "mini_anon_http": mini_anon.status_code,
            "air": air,
            "canGenerate": can_generate,
            "mini": _preview(mini_body, limit=700),
            "resource": _preview(resource_body, limit=700),
            "mini_anon": _preview(
                mini_anon.json() if mini_anon.content else mini_anon.text[:200],
                limit=400,
            ),
        }
        samples.append(sample)
        _dump(OUTPUT / "preflight-samples.json", samples)
        print(
            json.dumps(
                {
                    "elapsed_s": sample["elapsed_s"],
                    "mini_http": sample["mini_http"],
                    "resource_http": sample["resource_http"],
                    "canGenerate": can_generate,
                    "air": air,
                    "anon_resource_http": sample["resource_anon_http"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if resource.status_code == 200 and can_generate is True:
            record = {
                "ok": True,
                "air": air,
                "canGenerate": True,
                "resource_http": 200,
                "samples": samples[-3:],
                "resource_raw": resource_body,
                "mini_raw": mini_body,
                "anon": {
                    "resource_http": resource_anon.status_code,
                    "mini_http": mini_anon.status_code,
                    "resource": _preview(
                        resource_anon.json() if resource_anon.content else None,
                        limit=500,
                    ),
                    "mini": _preview(
                        mini_anon.json() if mini_anon.content else None, limit=500
                    ),
                },
            }
            state.setdefault("steps", {})["preflight"] = record
            _save_state(state)
            return record
        time.sleep(20)
    last = samples[-1] if samples else {}
    record = {
        "ok": False,
        "air": air,
        "reason": "timeout_waiting_for_resource_200_canGenerate",
        "last": last,
        "samples": samples[-5:],
    }
    state.setdefault("steps", {})["preflight"] = record
    _save_state(state)
    return record


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    _load_dotenv(ROOT / ".env")
    api_key = os.environ.get("CIVITAI_API_KEY", "").strip()
    if not api_key:
        print("CIVITAI_API_KEY missing", file=sys.stderr)
        return 2
    proxy = os.environ.get("CIVITAI_PROXY_URL") or os.environ.get("OPENAI_PROXY_URL") or None
    if not LORA_PATH.is_file():
        print(f"missing {LORA_PATH}", file=sys.stderr)
        return 2
    size = LORA_PATH.stat().st_size
    if size != EXPECTED_BYTES:
        print(f"unexpected size {size}, expected {EXPECTED_BYTES}", file=sys.stderr)
        return 2
    digest = hashlib.sha256()
    with LORA_PATH.open("rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    sha256 = digest.hexdigest()
    if sha256 != EXPECTED_SHA256:
        print(f"unexpected sha256 {sha256}", file=sys.stderr)
        return 2
    state = _load_state()
    state["lora_path"] = str(LORA_PATH)
    state["lora_bytes"] = size
    state["expected_sha256"] = EXPECTED_SHA256
    _save_state(state)
    session = CivitaiSession(api_key, proxy)
    try:
        probe_result = probe(session)
        state["probe"] = probe_result
        _save_state(state)
        _dump(OUTPUT / "upload-probe.json", probe_result)
        print(json.dumps({"probe": {
            "me_http": probe_result["me_http"],
            "known_lora_resource_http": probe_result["known_lora_resource_http"],
            "enums_http": probe_result["enums_http"],
        }}, ensure_ascii=False), flush=True)
        if probe_result["me_http"] != 200:
            print("GET /api/v1/me failed; stopping before upload", file=sys.stderr)
            _dump(OUTPUT / "http-calls.json", session.calls)
            return 1
        model = upsert_model(session, state)
        print(json.dumps({"model": {"id": model["id"], "availability": model.get("availability")}}, ensure_ascii=False), flush=True)
        version = upsert_version(session, state, int(model["id"]))
        print(json.dumps({"version": {"id": version["id"], "baseModel": version.get("baseModel")}}, ensure_ascii=False), flush=True)
        file_rec = upload_weights(session, state, int(version["id"]))
        print(json.dumps({"file": {"parts": file_rec.get("parts"), "created": True}}, ensure_ascii=False), flush=True)
        published = publish(session, state, int(model["id"]), int(version["id"]))
        print(json.dumps({"publish": {"ok": published.get("ok"), "procedure": published.get("procedure")}}, ensure_ascii=False), flush=True)
        preflight = wait_schedulable(
            session,
            state,
            model_id=int(model["id"]),
            version_id=int(version["id"]),
        )
        report = {
            "contract": "upload-celia-krea2-lora.1",
            "finished_at": datetime.now(UTC).isoformat(),
            "model_id": model["id"],
            "version_id": version["id"],
            "air": preflight.get("air"),
            "preflight_ok": preflight.get("ok"),
            "canGenerate": preflight.get("canGenerate"),
            "resource_http": preflight.get("resource_http"),
            "anon": preflight.get("anon"),
            "publish": published,
            "file_parts": file_rec.get("parts"),
        }
        state["report"] = report
        _save_state(state)
        _dump(OUTPUT / "upload-report.json", report)
        _dump(OUTPUT / "http-calls.json", session.calls)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if preflight.get("ok") else 1
    except Exception as exc:
        state["failed"] = f"{type(exc).__name__}: {exc}"
        _save_state(state)
        _dump(OUTPUT / "http-calls.json", session.calls)
        print(f"FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
