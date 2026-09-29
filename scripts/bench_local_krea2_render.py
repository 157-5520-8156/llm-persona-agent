#!/usr/bin/env python3
"""Local Krea2 Turbo GGUF Q4 bench. Does not talk to production (8787/NapCat/data/)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PROMPT = (
    "Celia, a 20-year-old original fictional Chinese university student. "
    "Natural black medium-length hair with soft bangs and slightly puffy volume, "
    "delicate but not overly polished features, quiet slightly tired eyes, "
    "a faint light mole on the right cheek. Handheld front-camera phone selfie "
    "at a sunlit cafe window, cream knit cardigan over a plain white tee, "
    "a ceramic coffee cup on the table, natural daylight, candid imperfect framing, "
    "photorealistic everyday photo, not an influencer, not a celebrity, not glamour."
)
NEGATIVE = (
    "celebrity likeness, influencer styling, heavy makeup, extra fingers, "
    "watermark, text, logo, nsfw, nude"
)
SEEDS = (11, 22, 33)


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def parse_vm_stat() -> dict[str, float]:
    page = 16384.0
    out = _run(["vm_stat"])
    pages: dict[str, int] = {}
    for line in out.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            pages[key.strip()] = int(digits)
    def gb(name: str) -> float:
        return pages.get(name, 0) * page / (1024**3)

    free = gb("Pages free")
    active = gb("Pages active")
    inactive = gb("Pages inactive")
    speculative = gb("Pages speculative")
    wired = gb("Pages wired down")
    compressor = gb("Pages occupied by compressor")
    return {
        "page_size": page,
        "free_gb": round(free, 3),
        "active_gb": round(active, 3),
        "inactive_gb": round(inactive, 3),
        "speculative_gb": round(speculative, 3),
        "wired_gb": round(wired, 3),
        "compressor_occupied_gb": round(compressor, 3),
        "anonymous_gb": round(gb("Anonymous pages"), 3),
        "file_backed_gb": round(gb("File-backed pages"), 3),
        "swapins": pages.get("Swapins", 0),
        "swapouts": pages.get("Swapouts", 0),
    }


def parse_swap() -> dict[str, float]:
    raw = _run(["sysctl", "-n", "vm.swapusage"])
    values: dict[str, float] = {}
    key = None
    for part in raw.replace("=", " ").replace("M", " ").split():
        token = part.strip(":")
        if token in {"total", "used", "free"}:
            key = token
            continue
        if key is not None:
            try:
                values[f"{key}_mb"] = float(part)
            except ValueError:
                pass
            key = None
    return {
        "total_gb": round(values.get("total_mb", 0) / 1024, 3),
        "used_gb": round(values.get("used_mb", 0) / 1024, 3),
        "free_gb": round(values.get("free_mb", 0) / 1024, 3),
        "raw": raw,
    }


def memory_free_pct() -> str:
    out = _run(["memory_pressure"])
    for line in out.splitlines():
        if "free percentage" in line.lower():
            return line.strip()
    return out.splitlines()[-1] if out else ""


def therm_snapshot() -> str:
    return _run(["pmset", "-g", "therm"])


def pid_rss_mb(pid: int) -> float | None:
    raw = _run(["ps", "-p", str(pid), "-o", "rss="])
    if not raw:
        return None
    try:
        return round(int(raw.split()[0]) / 1024, 1)
    except ValueError:
        return None


def daemon_pid() -> int | None:
    raw = _run(["pgrep", "-f", "companion_daemon.napcat_cli"])
    if not raw:
        return None
    try:
        return int(raw.splitlines()[0])
    except ValueError:
        return None


def snapshot(comfy_pid: int | None, extra: dict | None = None) -> dict:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "vm": parse_vm_stat(),
        "swap": parse_swap(),
        "memory_pressure": memory_free_pct(),
        "daemon_pid": daemon_pid(),
        "daemon_rss_mb": None,
        "comfy_rss_mb": None,
        "therm": therm_snapshot(),
    }
    dpid = row["daemon_pid"]
    if dpid:
        row["daemon_rss_mb"] = pid_rss_mb(dpid)
    if comfy_pid:
        row["comfy_rss_mb"] = pid_rss_mb(comfy_pid)
    if extra:
        row.update(extra)
    return row


class Sampler:
    def __init__(self, path: Path, comfy_pid: int | None, interval: float = 5.0):
        self.path = path
        self.comfy_pid = comfy_pid
        self.interval = interval
        self.rows: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_swap_used_gb = 0.0
        self.peak_wired_gb = 0.0
        self.peak_comfy_rss_mb = 0.0
        self.daemon_died = False
        self.initial_daemon_pid = daemon_pid()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            row = snapshot(self.comfy_pid)
            self.rows.append(row)
            swap_used = float(row["swap"].get("used_gb") or 0)
            wired = float(row["vm"].get("wired_gb") or 0)
            comfy_rss = float(row.get("comfy_rss_mb") or 0)
            self.peak_swap_used_gb = max(self.peak_swap_used_gb, swap_used)
            self.peak_wired_gb = max(self.peak_wired_gb, wired)
            self.peak_comfy_rss_mb = max(self.peak_comfy_rss_mb, comfy_rss)
            if self.initial_daemon_pid and daemon_pid() is None:
                self.daemon_died = True
            self.path.write_text(json.dumps(self.rows, indent=2), encoding="utf-8")
            self._stop.wait(self.interval)


def http_json(url: str, payload: dict | None = None, timeout: float = 30) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))


def wait_ready(base: str, timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            http_json(f"{base}/system_stats", timeout=5)
            return
        except Exception as exc:
            last = str(exc)
            time.sleep(1.5)
    raise TimeoutError(f"ComfyUI did not become ready: {last}")


def workflow(*, seed: int, width: int, height: int, steps: int, cfg: float, prefix: str) -> dict:
    return {
        "1": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": "krea2_turbo_bf16-Q4_0.gguf"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen3vl_4b_fp8_scaled.safetensors",
                "type": "krea2",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "qwen_image_vae.safetensors"},
        },
        "4": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["1", 0],
                "lora_name": "Celia.safetensors",
                "strength_model": 1.0,
            },
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": PROMPT, "clip": ["2", 0]},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": NEGATIVE, "clip": ["2", 0]},
        },
        "7": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["5", 0],
                "negative": ["6", 0],
                "latent_image": ["7", 0],
                "denoise": 1.0,
            },
        },
        "9": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["8", 0], "vae": ["3", 0]},
        },
        "10": {
            "class_type": "SaveImage",
            "inputs": {"images": ["9", 0], "filename_prefix": prefix},
        },
    }


def wait_history(base: str, prompt_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        hist = http_json(f"{base}/history/{prompt_id}", timeout=30)
        last = hist.get(prompt_id) or hist
        status = (last.get("status") or {})
        if status.get("completed") or last.get("outputs"):
            return last
        messages = status.get("messages") or []
        for item in messages:
            if isinstance(item, list) and item and item[0] == "execution_error":
                return last
        time.sleep(2)
    raise TimeoutError(f"prompt {prompt_id} did not finish within {timeout}s")


def copy_output(history: dict, dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    outputs = history.get("outputs") or {}
    for node in outputs.values():
        for image in node.get("images") or []:
            filename = image.get("filename")
            subfolder = image.get("subfolder") or ""
            kind = image.get("type") or "output"
            if not filename:
                continue
            params = f"filename={filename}&subfolder={subfolder}&type={kind}"
            url = f"{os.environ.get('COMFY_BASE', 'http://127.0.0.1:8198')}/view?{params}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read()
            target = dest / filename
            target.write_bytes(payload)
            copied.append(str(target))
    return copied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8198")
    parser.add_argument("--out", default="/Users/geoff/Projects/Girl-Agent/output/local-render")
    parser.add_argument("--comfy-pid", type=int, default=0)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1536)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--cfg", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--seeds", default="", help="comma-separated seeds; default 11,22,33")
    parser.add_argument("--start-index", type=int, default=1)
    args = parser.parse_args()
    seeds = tuple(int(x) for x in args.seeds.split(",")) if args.seeds.strip() else SEEDS
    os.environ["COMFY_BASE"] = args.base
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    metrics_path = out / "metrics-samples.json"
    summary_path = out / "metrics.json"

    wait_ready(args.base)
    info = http_json(f"{args.base}/object_info", timeout=60)
    missing = [name for name in ("UnetLoaderGGUF", "CLIPLoader", "EmptySD3LatentImage", "LoraLoaderModelOnly") if name not in info]
    if missing:
        raise SystemExit(f"ComfyUI is missing nodes: {missing}")

    sampler = Sampler(metrics_path, args.comfy_pid or None, interval=5.0)
    baseline = snapshot(args.comfy_pid or None, extra={"phase": "baseline"})
    sampler.rows.append(baseline)
    sampler.start()
    results = []
    aborted = None
    try:
        for offset, seed in enumerate(seeds):
            index = args.start_index + offset
            if sampler.daemon_died:
                aborted = "production daemon pid disappeared during bench"
                break
            prefix = f"celia-q4-1024x1536-{index}"
            payload = {"prompt": workflow(seed=seed, width=args.width, height=args.height, steps=args.steps, cfg=args.cfg, prefix=prefix)}
            t0 = time.monotonic()
            queued = http_json(f"{args.base}/prompt", payload, timeout=60)
            prompt_id = str(queued.get("prompt_id") or "")
            if not prompt_id:
                raise RuntimeError(f"ComfyUI did not queue: {queued}")
            history = wait_history(args.base, prompt_id, args.timeout)
            elapsed = round(time.monotonic() - t0, 1)
            status = history.get("status") or {}
            error = None
            for item in status.get("messages") or []:
                if isinstance(item, list) and item and item[0] == "execution_error":
                    error = item
            copied = []
            if not error:
                copied = copy_output(history, out)
            results.append(
                {
                    "index": index,
                    "seed": seed,
                    "prompt_id": prompt_id,
                    "elapsed_s": elapsed,
                    "error": error,
                    "files": copied,
                    "status": status,
                    "daemon_alive": daemon_pid() is not None,
                    "snapshot_after": snapshot(args.comfy_pid or None),
                }
            )
            if error:
                aborted = f"execution_error on image {index}"
                break
    finally:
        sampler.stop()

    summary = {
        "started": baseline,
        "config": {
            "unet": "krea2_turbo_bf16-Q4_0.gguf",
            "text_encoder": "qwen3vl_4b_fp8_scaled.safetensors",
            "vae": "qwen_image_vae.safetensors",
            "lora": "Celia.safetensors",
            "size": f"{args.width}x{args.height}",
            "steps": args.steps,
            "cfg": args.cfg,
            "sampler": "euler",
            "scheduler": "simple",
            "prompt": PROMPT,
            "negative": NEGATIVE,
            "seeds": list(SEEDS),
        },
        "results": results,
        "peak_swap_used_gb": sampler.peak_swap_used_gb,
        "peak_wired_gb": sampler.peak_wired_gb,
        "peak_comfy_rss_mb": sampler.peak_comfy_rss_mb,
        "daemon_died": sampler.daemon_died,
        "aborted": aborted,
        "final": snapshot(args.comfy_pid or None),
        "elapsed_range_s": [row["elapsed_s"] for row in results],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"metrics": str(summary_path), "images": [r.get("files") for r in results], "aborted": aborted}, indent=2))
    return 1 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
