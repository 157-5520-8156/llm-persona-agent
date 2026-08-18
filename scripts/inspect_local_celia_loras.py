#!/usr/bin/env python3
"""Inspect local Celia LoRA checkpoints. Never POSTs. Never uploads.

Reads only the safetensors header plus a streamed SHA256. Writes
output/lora-restore/inventory.json. Optional GET-only Civitai hash lookup.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "output" / "lora-restore"

CANDIDATES = (
    ROOT / "Celia.safetensors",
    ROOT / "training/runs/celia-v2-v0.1-direct-smoke/pytorch_lora_weights.safetensors",
    ROOT / "training/runs/celia-v2-v0.1-direct-smoke/pytorch_lora_weights_kohya.safetensors",
    ROOT / "training/runs/celia-v2-v0.1-direct-smoke/checkpoint-1/pytorch_lora_weights.safetensors",
    ROOT / "training/runs/celia-v2-v0.2-600steps/pytorch_lora_weights.safetensors",
    ROOT / "training/runs/celia-v3-v1-512px-pilot/checkpoint-200/pytorch_lora_weights.safetensors",
)

KREA2_IDENTITY_AIR = "urn:air:krea2:lora:civitai:2787068@3140284"
TEMPLATE_PATH = ROOT / "configs/civitai-krea2-celia-realism-template.json"


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_header(path: Path) -> tuple[int, dict[str, Any]]:
    with path.open("rb") as handle:
        header_nbytes = struct.unpack("<Q", handle.read(8))[0]
        header = json.loads(handle.read(header_nbytes))
    if not isinstance(header, dict):
        raise ValueError(f"invalid safetensors header: {path}")
    return header_nbytes, header


def _classify(keys: list[str]) -> dict[str, Any]:
    joined = " ".join(keys[:80])
    kohya = any(k.startswith("lora_unet") or k.startswith("lora_te") for k in keys)
    diffusers = any(k.startswith("unet.") or k.startswith("text_encoder") for k in keys)
    flux_like = any("double_blocks" in k or "single_blocks" in k for k in keys)
    has_txtfusion = any("txtfusion" in k.lower() for k in keys)
    has_diffusion_model = any(k.startswith("diffusion_model.") for k in keys)
    has_krea = "krea" in joined.lower() or has_txtfusion
    if kohya:
        fmt = "kohya"
    elif any("lora_A" in k or "lora_B" in k for k in keys) and has_diffusion_model:
        fmt = "peft_dit"
    elif diffusers:
        fmt = "diffusers"
    else:
        fmt = "unknown"
    if has_txtfusion and has_diffusion_model:
        ecosystem = "krea2_dit"
    elif flux_like:
        ecosystem = "flux_dit"
    elif any(k.startswith("unet.") or k.startswith("lora_unet") for k in keys):
        ecosystem = "sdxl_unet"
    elif has_diffusion_model:
        ecosystem = "dit_generic"
    else:
        ecosystem = "unknown"
    prefixes = collections.Counter(k.split(".")[0] for k in keys)
    two_level = collections.Counter(".".join(k.split(".")[:2]) for k in keys)
    return {
        "format": fmt,
        "ecosystem": ecosystem,
        "n_tensors": len(keys),
        "has_text_encoder": any("text_encoder" in k or k.startswith("lora_te") for k in keys),
        "flux_like": flux_like,
        "krea_or_dit_keys": has_krea or has_diffusion_model,
        "has_txtfusion": has_txtfusion,
        "compatible_with_krea2_template": ecosystem == "krea2_dit",
        "key_prefixes": dict(prefixes.most_common(20)),
        "key_two_level": dict(two_level.most_common(20)),
        "sample_keys": keys[:12],
    }


def inspect_safetensors(path: Path) -> dict[str, Any]:
    header_nbytes, header = _read_header(path)
    meta = header.get("__metadata__") or {}
    if not isinstance(meta, dict):
        meta = {}
    keys = [key for key in header if key != "__metadata__"]
    dtypes = collections.Counter()
    for key in keys:
        tensor = header[key]
        if isinstance(tensor, dict):
            dtypes[str(tensor.get("dtype"))] += 1
    lora_a_in = sorted(
        {
            int(header[key]["shape"][0])
            for key in keys
            if isinstance(header.get(key), dict)
            and "lora_A" in key
            and isinstance(header[key].get("shape"), list)
            and header[key]["shape"]
        }
    )
    info = _classify(keys)
    rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    return {
        "path": rel,
        "bytes": path.stat().st_size,
        "sha256": _stream_sha256(path),
        "autov2_prefix10": None,
        "mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "header_nbytes": header_nbytes,
        "header_metadata": {str(k): v for k, v in meta.items()},
        "dtypes": dict(dtypes),
        "lora_rank_from_lora_A": lora_a_in,
        **info,
    }


def _template_airs() -> list[str]:
    if not TEMPLATE_PATH.is_file():
        return []
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    steps = template.get("steps") or []
    if steps and isinstance(steps[0], dict):
        loras = ((steps[0].get("input") or {}).get("loras") or {})
        if isinstance(loras, dict):
            return list(loras)
    return []


def _lookup_hashes(digests: list[str]) -> dict[str, Any]:
    import os

    import httpx

    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    api_key = os.environ.get("CIVITAI_API_KEY", "").strip()
    proxy = os.environ.get("CIVITAI_PROXY_URL") or os.environ.get("OPENAI_PROXY_URL") or None
    found: dict[str, Any] = {}
    headers = {"User-Agent": "girl-agent-lora-restore/1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    with httpx.Client(timeout=25, proxy=proxy, follow_redirects=True) as client:
        for digest in digests:
            response = client.get(
                f"https://civitai.com/api/v1/model-versions/by-hash/{digest.upper()}",
                headers=headers,
            )
            payload: Any
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                payload = None
            slim: dict[str, Any] = {
                "http_status": response.status_code,
                "matched": response.status_code == 200,
            }
            if isinstance(payload, dict) and response.status_code == 200:
                slim.update(
                    {
                        "id": payload.get("id"),
                        "modelId": payload.get("modelId"),
                        "air": payload.get("air"),
                        "name": payload.get("name"),
                        "baseModel": payload.get("baseModel"),
                        "availability": payload.get("availability"),
                        "canGenerate": payload.get("canGenerate"),
                    }
                )
            elif isinstance(payload, dict):
                slim["error"] = payload.get("error") or payload.get("message")
            found[digest] = slim
    return found


def _conclusion(files: list[dict[str, Any]], hash_lookup: dict[str, Any]) -> str:
    krea = [item for item in files if item.get("ecosystem") == "krea2_dit"]
    if not krea:
        return (
            "No local Krea2 DiT LoRA. SDXL UNet files cannot replace "
            f"{KREA2_IDENTITY_AIR}."
        )
    matched = [
        hash_lookup[str(item["sha256"])]
        for item in krea
        if str(item.get("sha256")) in hash_lookup
        and hash_lookup[str(item["sha256"])].get("matched")
    ]
    if matched:
        airs = [row.get("air") for row in matched if row.get("air")]
        return f"Krea2 identity LoRA hash-matched on Civitai. New AIR: {airs}"
    return (
        "Celia.safetensors is a Krea2 DiT LoRA (ss_base_model_version=krea2) "
        "but Civitai GET by-hash and the old AIR both 404. Do not swap the "
        "template until a schedulable AIR exists."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--check-civitai-hash",
        action="store_true",
        help="GET /api/v1/model-versions/by-hash/{sha256} only. Never POST.",
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    files = [inspect_safetensors(path) for path in CANDIDATES if path.is_file()]
    for item in files:
        sha = str(item.get("sha256") or "")
        item["autov2_prefix10"] = sha[:10].upper() if sha else None

    hash_lookup: dict[str, Any] = {}
    extra_hashes: list[str] = []
    if args.check_civitai_hash:
        extra_hashes = sorted({str(item["sha256"]) for item in files})
        for item in files:
            meta = item.get("header_metadata") or {}
            model_hash = meta.get("sshs_model_hash")
            if isinstance(model_hash, str) and len(model_hash) >= 8:
                extra_hashes.append(model_hash)
        hash_lookup = _lookup_hashes(sorted(set(extra_hashes)))

    report = {
        "contract": "local-celia-lora-inventory.2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "krea2_identity_air": KREA2_IDENTITY_AIR,
        "template_path": str(TEMPLATE_PATH.relative_to(ROOT)),
        "template_lora_airs": _template_airs(),
        "conclusion": _conclusion(files, hash_lookup),
        "files": files,
        "civitai_hash_lookup": hash_lookup,
        "posts": 0,
        "uploads": 0,
        "yellow_buzz": 0,
    }
    out = args.out_dir / "inventory.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
