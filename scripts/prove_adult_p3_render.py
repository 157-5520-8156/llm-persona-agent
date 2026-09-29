#!/usr/bin/env python3
"""Prove or disprove that the P3 high-private Civitai Krea2 route can mint one image.

Read-only versus production: clones ``data/companion.epoch2.sqlite`` into
``output/adult-render/``, never writes ``data/``, never talks to 8787/NapCat,
never modifies ``src/``. At most one real Civitai render (suggestive_private).

Usage::

    .venv/bin/python scripts/prove_adult_p3_render.py
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import logging
import sqlite3
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

OUTPUT = (REPO / "output" / "adult-render").resolve()
PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
_LOG = logging.getLogger("prove_adult_p3_render")


def _is_production_path(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    data = (REPO / "data").resolve()
    try:
        resolved.relative_to(data)
    except ValueError:
        return False
    return True


def clone_ledger(source: Path, target: Path) -> None:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"production ledger missing: {source}")
    if _is_production_path(target):
        raise SystemExit(f"refusing to write a clone under data/: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _preview(value: object, *, limit: int = 800) -> object:
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes {len(value)}>"
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        keys = sorted(value)
        out: dict[str, object] = {}
        for key in keys[:40]:
            if key.lower() in {"authorization", "api_key", "token", "images"}:
                item = value[key]
                if isinstance(item, list):
                    out[key] = f"<list {len(item)}>"
                elif isinstance(item, str):
                    out[key] = f"<redacted len={len(item)}>"
                else:
                    out[key] = "<redacted>"
                continue
            out[key] = _preview(value[key], limit=240)
        if len(keys) > 40:
            out["_truncated_keys"] = len(keys) - 40
        return out
    if isinstance(value, list):
        return [_preview(item, limit=240) for item in value[:8]]
    return value


@dataclass
class HttpCall:
    method: str
    url: str
    status: int | None
    elapsed_ms: float
    error: str | None = None
    body_preview: object | None = None
    request_preview: object | None = None


class TracingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()
        self.calls: list[HttpCall] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        started = time.perf_counter()
        req_preview = None
        if request.content:
            try:
                req_preview = _preview(json.loads(request.content.decode("utf-8")))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                req_preview = f"<body {len(request.content)} bytes>"
        try:
            response = await self._inner.handle_async_request(request)
            raw = await response.aread()
            elapsed = (time.perf_counter() - started) * 1000
            body_preview = None
            try:
                body_preview = _preview(json.loads(raw.decode("utf-8")))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                body_preview = f"<bytes {len(raw)} magic={raw[:8]!r}>"
            self.calls.append(
                HttpCall(
                    method=request.method,
                    url=_redact_url(str(request.url)),
                    status=response.status_code,
                    elapsed_ms=round(elapsed, 1),
                    body_preview=body_preview,
                    request_preview=req_preview,
                )
            )
            headers = [
                (name, value)
                for name, value in response.headers.raw
                if name.lower() not in {b"content-length", b"content-encoding"}
            ]
            return httpx.Response(
                status_code=response.status_code,
                headers=headers,
                content=raw,
                request=request,
                extensions=response.extensions,
            )
        except Exception as exc:
            self.calls.append(
                HttpCall(
                    method=request.method,
                    url=_redact_url(str(request.url)),
                    status=None,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                    error=f"{type(exc).__name__}: {exc}",
                    request_preview=req_preview,
                )
            )
            raise

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if callable(close):
            await close()


def unwrap(obj: object) -> object:
    while hasattr(obj, "_delegate"):
        obj = obj._delegate
    return obj


class CountingInspector:
    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.calls: list[dict[str, object]] = []

    async def inspect(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        self.calls.append({"args": len(args), "kwargs": sorted(kwargs)})
        return await self.inner.inspect(*args, **kwargs)  # type: ignore[misc]


class RecordingAuthor:
    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.prompt: str | None = None
        self.error: str | None = None
        self.elapsed_ms: float | None = None
        self.model = getattr(inner, "model", None)

    async def write(self, plan: object) -> str:
        started = time.perf_counter()
        try:
            prompt = await self.inner.write(plan)  # type: ignore[misc]
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            raise
        self.prompt = prompt
        self.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return prompt


def _candidate_lane_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    lanes: dict[str, int] = {}
    for item in candidates:
        for lane in item.get("legal_media_lanes", []) or []:
            lanes[str(lane)] = lanes.get(str(lane), 0) + 1
    return {
        "n": len(candidates),
        "legal_lanes": sorted(lanes),
        "lane_counts": lanes,
        "suggestive_private_count": lanes.get("suggestive_private", 0),
        "explicit_private_count": lanes.get("explicit_private", 0),
    }


def _dump_complete_candidates(candidates: list[dict[str, Any]], *, label: str) -> None:
    summary = _candidate_lane_summary(candidates)
    suggestive = [
        {
            "complete_candidate_id": item.get("complete_candidate_id"),
            "legal_media_lanes": item.get("legal_media_lanes"),
            "legal_capture_modes": item.get("legal_capture_modes"),
            "legal_visual_forms": item.get("legal_visual_forms"),
            "legal_share_intents": item.get("legal_share_intents"),
            "legal_interaction_bids": item.get("legal_interaction_bids"),
            "coverage_mode": (item.get("embodied_presentation") or {}).get("coverage_mode"),
            "sensual_charge": (item.get("embodied_presentation") or {}).get("sensual_charge"),
            "expression_charge": (item.get("media_address_strategy") or {}).get(
                "expression_charge"
            ),
        }
        for item in candidates
        if "suggestive_private" in (item.get("legal_media_lanes") or [])
    ][:8]
    dump_json(
        OUTPUT / "complete-candidates-summary.json",
        {"label": label, **summary, "suggestive_samples": suggestive},
    )


class HighLanePicker:
    """Pick one already-legal suggestive_private complete candidate. Not a role author."""

    async def complete_json(self, messages, *, temperature=0.8):  # type: ignore[no-untyped-def]
        return await self.complete(messages, temperature=temperature)

    async def complete(self, messages, *, temperature=0.8):  # type: ignore[no-untyped-def]
        del temperature
        try:
            content = str(messages[-1]["content"])
            encoded = content.split("legal_complete_media_expression_candidates=", 1)[1].split(
                "\n", 1
            )[0]
            candidates = json.loads(encoded)
            _dump_complete_candidates(candidates, label="high-lane-picker")
            suggestive = [
                item
                for item in candidates
                if "suggestive_private" in item.get("legal_media_lanes", [])
            ]
            if not suggestive:
                lanes = sorted(
                    {
                        lane
                        for item in candidates
                        for lane in item.get("legal_media_lanes", [])
                    }
                )
                raise ValueError(
                    f"no suggestive_private complete candidate; legal_lanes={lanes} n={len(candidates)}"
                )
            candidate = next(
                (
                    item
                    for item in suggestive
                    if "invite_desire" in item.get("legal_interaction_bids", [])
                ),
                suggestive[0],
            )
        except Exception as exc:
            (OUTPUT / "high-lane-picker-error.txt").write_text(
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                encoding="utf-8",
            )
            raise
        embodied = candidate["embodied_presentation"]
        intents = [str(item) for item in candidate.get("legal_share_intents") or []]
        share_intent = "intimate_signal" if "intimate_signal" in intents else intents[0]
        bids = [str(item) for item in candidate.get("legal_interaction_bids") or []]
        bid = "invite_desire" if "invite_desire" in bids else bids[0]
        supporting = [
            "/relationship_media_context/declared_display",
            "/activity/kind",
        ]
        supporting.extend(
            ref
            for cue in embodied.get("physical_cues", [])
            for ref in cue.get("evidence_refs", [])
        )
        supporting.extend(embodied.get("wardrobe_evidence_refs", []))
        return json.dumps(
            {
                "content_domain": "activity_process",
                "visual_form": candidate["legal_visual_forms"][0],
                "share_intent": share_intent,
                "capture_mode": candidate["legal_capture_modes"][0],
                "character_visibility": "identifiable",
                "other_people_visibility": "none",
                "polish": "casual",
                "tone": "tender",
                "privacy": "intimate",
                "primary_evidence_ref": "/activity/private_transition",
                "supporting_evidence_refs": list(dict.fromkeys(supporting)),
                "constraints": [],
                "route": "generate",
                "interaction_bid_id": bid,
                "complete_candidate_id": candidate["complete_candidate_id"],
                "media_lane": "suggestive_private",
                "recipient_access": "recipient_exclusive",
                "attraction_expression": "sexual_suggestive",
                "private_flair": {
                    "action_beat": (
                        "she pauses midway through a plausible adjustment "
                        "already implied by the selected pose"
                    ),
                    "expression_beat": "a briefly mischievous, almost caught expression",
                    "gaze_beat": (
                        "she lets her eyes rest on the reflection as if waiting for one response"
                    ),
                    "recipient_subtext": "the pause is deliberately saved for the recipient",
                    "facial_profile": "natural_private",
                },
            },
            ensure_ascii=False,
        )


@dataclass
class UsageCapture:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, usage: object) -> None:
        self.rows.append(
            {
                "model": getattr(usage, "model", None),
                "provider": getattr(usage, "provider", None),
                "purpose": getattr(usage, "purpose", None),
                "status": getattr(usage, "status", None),
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
                "cache_hit_tokens": getattr(usage, "cache_hit_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
                "cost_cny": getattr(usage, "cost_cny", None),
                "error": getattr(usage, "error", None),
            }
        )


def inspect_production_adult_grants(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        grants = []
        for event_type in (
            "CapabilityGranted",
            "ConsentGranted",
            "ProviderMediaGrantRecorded",
            "DeclaredDisplayRecorded",
            "PhotoCandidateOpened",
        ):
            row = conn.execute(
                "SELECT COUNT(*) FROM world_v2_events WHERE world_id = ? "
                "AND json_extract(event_json, '$.event_type') = ?",
                (WORLD_ID, event_type),
            ).fetchone()
            grants.append({"event_type": event_type, "count": int(row[0])})
        adult_rows = conn.execute(
            "SELECT json_extract(event_json, '$.event_type') AS event_type, "
            "json_extract(event_json, '$.event_id') AS event_id "
            "FROM world_v2_events WHERE world_id = ? AND event_json LIKE '%adult-media%' "
            "LIMIT 12",
            (WORLD_ID,),
        ).fetchall()
        return {
            "counts": grants,
            "adult_media_mentions": [
                {"event_type": row[0], "event_id": row[1]} for row in adult_rows
            ],
        }
    finally:
        conn.close()


def env_presence(settings: object) -> dict[str, Any]:
    def present(name: str) -> dict[str, Any]:
        value = getattr(settings, name)
        if value is None or value == "":
            return {"present": False}
        text = str(value)
        if "key" in name or "token" in name:
            return {"present": True, "len": len(text), "prefix": text[:8]}
        return {"present": True, "value": text}

    template = getattr(settings, "civitai_krea2_template_path", None)
    return {
        "civitai_api_key": present("civitai_api_key"),
        "openrouter_api_key": present("openrouter_api_key"),
        "deepseek_api_key": present("deepseek_api_key"),
        "openai_api_key": present("openai_api_key"),
        "world_v2_adult_media_enabled": bool(settings.world_v2_adult_media_enabled),
        "civitai_krea2_enabled": bool(settings.civitai_krea2_enabled),
        "hermes_private_prompt_enabled": bool(settings.hermes_private_prompt_enabled),
        "hermes_private_prompt_model": settings.hermes_private_prompt_model,
        "civitai_base_url": settings.civitai_base_url,
        "template_path": str(template) if template else None,
        "template_is_file": bool(template and Path(template).is_file()),
        "world_v2_media_preview_enabled": bool(settings.world_v2_media_preview_enabled),
        "allow_auto_image_generation": bool(settings.allow_auto_image_generation),
    }


def summarize_plan(plan: object) -> dict[str, Any]:
    contract = getattr(plan, "private_render_contract", None) or getattr(
        plan, "suggestive_private_contract", None
    )
    lane = getattr(plan, "media_lane", None)
    return {
        "plan_id": getattr(plan, "plan_id", None),
        "event_id": getattr(plan, "event_id", None),
        "family": getattr(plan, "family", None),
        "privacy": getattr(plan, "privacy", None),
        "capture_mode": getattr(plan, "capture_mode", None),
        "primary_evidence_ref": getattr(plan, "primary_evidence_ref", None),
        "lane": getattr(lane, "lane", None),
        "render_route": getattr(contract, "render_route", None),
        "has_private_flair": getattr(plan, "private_flair", None) is not None,
        "declared_display": (
            (getattr(plan, "evidence_values", {}) or {}).get(
                "/relationship_media_context/declared_display"
            )
        ),
    }


def cost_from_usage(rows: list[Mapping[str, Any]], *, models: tuple[str, ...]) -> dict[str, Any]:
    matched = [
        row
        for row in rows
        if str(row.get("model") or "") in models
        or any(token in str(row.get("model") or "").lower() for token in models)
    ]
    prompt = sum(int(row.get("prompt_tokens") or 0) for row in matched)
    completion = sum(int(row.get("completion_tokens") or 0) for row in matched)
    cny = sum(float(row.get("cost_cny") or 0) for row in matched)
    return {
        "calls": len(matched),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cost_cny_recorded": round(cny, 6),
        "rows": matched,
    }


def extract_civitai_cost(calls: list[HttpCall]) -> dict[str, Any]:
    buzz = None
    status = None
    workflow_id = None
    for call in reversed(calls):
        body = call.body_preview
        if not isinstance(body, dict):
            continue
        workflow_id = workflow_id or body.get("id")
        status = status or body.get("status")
        for key in ("cost", "buzz", "price", "chargedBuzz", "totalCost"):
            if key in body and body[key] not in (None, ""):
                buzz = body[key]
                break
        steps = body.get("steps")
        if isinstance(steps, list) and steps and isinstance(steps[0], dict):
            for container in (steps[0], steps[0].get("output") or {}):
                if not isinstance(container, dict):
                    continue
                for key in ("cost", "buzz", "price", "chargedBuzz"):
                    if key in container and container[key] not in (None, ""):
                        buzz = container[key]
    return {"workflow_id": workflow_id, "last_status": status, "cost_field": buzz}


async def run() -> dict[str, Any]:
    from companion_daemon.config import Settings
    from companion_daemon.event_media import (
        MediaOpportunity as LegacyOpportunity,
        MediaPlanner,
        MediaRenderFailure,
        NotRenderable,
        PlannedMedia,
        RenderedMedia,
    )
    from companion_daemon.image_generation import CivitaiTemplateWorkflowImageGenerator
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.event_media_planner_adapter import (
        EventMediaPlannerAdapter,
        _snapshot_leaves,
    )
    from companion_daemon.world_v2.media_v2 import StoredMediaPayload
    from companion_daemon.world_v2.qq_media_deployment import (
        _compose_high_private_lane,
        build_qq_media_preview_deployment,
    )
    from companion_daemon.world_v2.sourced_life_media import sourced_life_closure_error
    from tests.world_v2.test_adult_media_authorization import _authorize, _p3_world

    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    generated_dir = OUTPUT / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "contract": "prove-adult-p3-render.1",
        "started_at": datetime.now(UTC).isoformat(),
        "clone": str(clone),
        "production_source": str(PRODUCTION_DB),
        "gates": {},
        "images_spent": 0,
        "max_images": 1,
    }

    report["production_adult_grants"] = inspect_production_adult_grants(PRODUCTION_DB)
    clone_ledger(PRODUCTION_DB, clone)

    settings = Settings(database_path=clone)
    report["env"] = env_presence(settings)
    world_id = f"world:companion-v2:qq-c2c:{settings.primary_user_id}"
    report["world_id"] = world_id

    from companion_daemon.world_v2.media_provider_transport import (
        MediaProviderDiagnosticRecorder,
    )

    composed = _compose_high_private_lane(
        settings,
        diagnostic_recorder=MediaProviderDiagnosticRecorder(),
        world_id=world_id,
    )
    routing: dict[str, Any] = {"compose_installed": composed is not None}
    if composed is not None:
        routes, author, choice = composed
        inner = unwrap(routes["adult_suggestive"])
        routing.update(
            {
                "routes": sorted(routes),
                "author_class": type(author).__name__,
                "author_model": getattr(author.model, "model", None),
                "author_provider": getattr(author.model, "provider", None),
                "author_via": choice.via,
                "generator_class": type(inner).__name__,
                "generator_is_civitai_template": isinstance(
                    inner, CivitaiTemplateWorkflowImageGenerator
                ),
            }
        )
    report["gates"]["compose_high_private"] = routing

    bundle = build_qq_media_preview_deployment(
        settings=settings, world_id=world_id, output_dir=generated_dir
    )
    factory: dict[str, Any] = {"bundle": bundle is not None}
    renderer = None
    if bundle is not None:
        renderer = bundle.transport._renderer
        high_plan = type("P", (), {})()
        high_plan.private_render_contract = type("C", (), {"render_route": "adult_suggestive"})()
        high_plan.suggestive_private_contract = None
        explicit_plan = type("P", (), {})()
        explicit_plan.private_render_contract = type("C", (), {"render_route": "adult_explicit"})()
        explicit_plan.suggestive_private_contract = None
        ordinary_plan = type("P", (), {})()
        ordinary_plan.private_render_contract = None
        ordinary_plan.suggestive_private_contract = None
        factory.update(
            {
                "renderer": type(renderer).__name__,
                "specialized_generators": sorted(renderer.specialized_generators),
                "private_prompt_author": type(renderer.private_prompt_author).__name__
                if renderer.private_prompt_author
                else None,
                "author_model": getattr(
                    getattr(renderer.private_prompt_author, "model", None), "model", None
                ),
                "author_provider": getattr(
                    getattr(renderer.private_prompt_author, "model", None), "provider", None
                ),
                "generator_for_adult_suggestive_is_none": renderer._generator_for(high_plan)
                is None,
                "generator_for_adult_explicit_is_none": renderer._generator_for(explicit_plan)
                is None,
                "ordinary_uses_default_generator": renderer._generator_for(ordinary_plan)
                is renderer.generator,
                "high_uses_specialized": renderer._generator_for(high_plan)
                is renderer.specialized_generators.get("adult_suggestive"),
            }
        )
    report["gates"]["factory"] = factory
    if bundle is None or renderer is None:
        report["conclusion"] = "不通：生产工厂没装上高档车道"
        report["stopped_at"] = "factory"
        return report

    specialized = unwrap(renderer.specialized_generators["adult_suggestive"])
    if not isinstance(specialized, CivitaiTemplateWorkflowImageGenerator):
        report["conclusion"] = "不通：specialized generator 不是 Civitai 模板生成器"
        report["stopped_at"] = "factory"
        return report
    inner_transport = (
        specialized.transport
        if isinstance(specialized.transport, httpx.AsyncBaseTransport)
        else None
    )
    tracer = TracingTransport(inner=inner_transport)
    specialized.transport = tracer

    inspector = CountingInspector(renderer.inspector)
    renderer.inspector = inspector
    author = RecordingAuthor(renderer.private_prompt_author)
    renderer.private_prompt_author = author

    try:
        ledger, candidate = _p3_world(
            stage="lover",
            with_adult_grants=True,
            display_intent="sexual_suggestive",
        )
    except Exception as exc:
        report["gates"]["candidate_freeze"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-2500:],
        }
        report["conclusion"] = "不通：构造 P3 世界失败"
        report["stopped_at"] = "candidate_freeze"
        return report

    report["gates"]["candidate_freeze"] = {
        "ok": True,
        "candidate_id": candidate.candidate_id,
        "family": candidate.family,
        "privacy_ceiling": candidate.privacy_ceiling,
        "ecology_category": candidate.ecology_category,
        "source_event_refs": list(candidate.source_event_refs),
        "capture_modes": list(candidate.character_media_contract.allowed_capture_modes),
        "stage": "lover",
        "close_friend_planner_block": {
            "authorizer_allows_close_friend": True,
            "embodiment": (
                "media_embodiment._relationship_allows_charge only admits "
                "subtle/charged for ambiguous|lover; close_friend yields charge=none"
            ),
            "invite_desire": (
                "event_media._interaction_bid_values skips invite_desire unless "
                "stage in {ambiguous, lover}"
            ),
            "prior_close_friend_probe": "legal_lanes=['ordinary_life'] n=24",
            "this_run_uses": "lover so the Civitai cell is reachable",
        },
        "note": "in-memory constructed world using the live authorization test fixture; not a production ecology freeze",
    }

    try:
        opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    except Exception as exc:
        report["gates"]["authorize"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-2500:],
        }
        report["conclusion"] = "不通：授权车道失败"
        report["stopped_at"] = "authorize"
        return report

    display = compiled.snapshot.image_event_snapshot.relationship_media_context.declared_display
    authz = compiled.snapshot.private_media_authorization
    report["gates"]["authorize"] = {
        "ok": True,
        "opportunity_id": opportunity.opportunity_id,
        "media_lane": opportunity.media_lane,
        "expression_charge_ceiling": authz.expression_charge_ceiling,
        "declared_media_intent": None if display is None else display.media_intent,
        "recipient_ref": opportunity.recipient_ref,
        "p3_authorization_digest": opportunity.p3_authorization_digest,
        "selection": {
            "family": "character_media",
            "media_privacy_ceiling": "intimate",
            "expression_charge_ceiling": "subtle",
            "note": "constructed P3 MediaSelection (not a live character-interior select); authorizer re-derives charged/suggestive_private from her declaration",
        },
    }
    if opportunity.media_lane != "suggestive_private":
        report["conclusion"] = f"不通：授权车道是 {opportunity.media_lane}，不是 suggestive_private"
        report["stopped_at"] = "authorize"
        return report

    sidecar = bundle.deployment.planner._sidecar
    sidecar.put_if_absent(
        StoredMediaPayload(
            payload_ref=opportunity.event_snapshot_ref,
            payload_hash=opportunity.event_snapshot_hash,
            content_type="application/vnd.world-v2.media-opportunity+json",
            body=compiled.snapshot_body,
        )
    )
    snapshot = json.loads(compiled.image_event_snapshot_body)
    character = snapshot.setdefault("character", {})
    if not isinstance(character, dict):
        character = {}
        snapshot["character"] = character
    if not character.get("appearance_state"):
        character["appearance_state"] = {
            "coverage_mode": "private_apparel",
            "outfit_role": "sleepwear",
            "outfit": "bathrobe",
        }
    location = snapshot.get("location")
    if not isinstance(location, dict) or not location:
        snapshot["location"] = {"kind": "private", "mirror_available": True}
    dump_json(OUTPUT / "authorized-snapshot.json", snapshot)
    report["gates"]["snapshot_wardrobe_patch"] = {
        "reason": (
            "PrivateMediaEvidenceSnapshotCompiler._freeze_historical_character_state "
            "returns immediately for basis.kind=private_transition and never copies "
            "appearance_state onto the image snapshot. build_embodied_candidates then "
            "skips private_apparel/strategic_cover because wardrobe evidence refs are "
            "empty. Probe injects the appearance/location the compiler should freeze."
        ),
        "file": "src/companion_daemon/world_v2/private_media_evidence_snapshot.py",
        "line": 264,
        "patched_appearance": character.get("appearance_state"),
        "patched_location": snapshot.get("location"),
    }
    legacy_opportunity = LegacyOpportunity(
        opportunity_id=opportunity.opportunity_id,
        family=opportunity.family,
        privacy_ceiling="intimate",
        event_snapshot=snapshot,
        delivery_mode="preview",
        audience_context=EventMediaPlannerAdapter._p3_audience(snapshot),
        expression_charge_ceiling=authz.expression_charge_ceiling,
        sensual_charge_ceiling=authz.expression_charge_ceiling,
        private_expression_basis=EventMediaPlannerAdapter._p3_basis(snapshot),
        allowed_evidence_refs=tuple(
            sorted(
                {
                    *_snapshot_leaves(snapshot),
                    "/relationship_media_context/declared_display",
                }
            )
        ),
        authorized_capture_modes=tuple(authz.allowed_capture_modes),
    )
    report["gates"]["adapter_wiring"] = {
        "production_adapter_sets_expression_charge_ceiling": True,
        "production_adapter_sets_sensual_charge_ceiling": False,
        "prior_probe_legal_lanes_with_production_adapter_shape": ["ordinary_life"],
        "prior_probe_complete_candidate_count": 24,
        "embodiment_catalog_reads": "MediaOpportunity.sensual_charge_ceiling",
        "file": "src/companion_daemon/world_v2/event_media_planner_adapter.py",
        "line": 347,
        "consumer": "src/companion_daemon/event_media.py:_planner_character_candidates:5815",
        "fix": (
            "EventMediaPlannerAdapter.plan must also set sensual_charge_ceiling="
            "p3_authorization.expression_charge_ceiling (currently left at default none). "
            "Until that lands, the complete-candidate matrix only emits ordinary_life."
        ),
        "local_probe_sets_both": True,
        "authorized_charge": authz.expression_charge_ceiling,
        "leaf_allow_list_omits_declared_display_parent": True,
        "declared_display_parent_added_locally": True,
        "declared_display_parent_fix": (
            "EventMediaPlannerAdapter.plan sets allowed_evidence_refs from "
            "_snapshot_leaves only. Freeze then rejects "
            "/relationship_media_context/declared_display as unapproved_evidence_ref, "
            "but _validate_frozen_plan_v5 requires that parent object for "
            "private_render_intent_evidence_missing. Production must allow that "
            "container pointer (or freeze the parent from its leaves)."
        ),
    }

    planner_usage = UsageCapture()
    deepseek_planner = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.world_v2_media_planner_model or settings.deepseek_model,
        thinking_enabled=False,
        usage_observer=planner_usage,
    )
    raw_planner_outputs: list[str] = []
    inner_complete_json = deepseek_planner.complete_json

    async def _complete_json(messages, *, temperature=0.65, **kwargs):  # type: ignore[no-untyped-def]
        content = str(messages[-1]["content"])
        marker = "legal_complete_media_expression_candidates="
        if marker in content:
            encoded = content.split(marker, 1)[1].split("\n", 1)[0]
            try:
                _dump_complete_candidates(json.loads(encoded), label="deepseek-planner")
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        raw = await inner_complete_json(messages, temperature=temperature, **kwargs)
        raw_planner_outputs.append(raw if isinstance(raw, str) else json.dumps(raw, default=str))
        (OUTPUT / "deepseek-planner-raw.json").write_text(
            raw_planner_outputs[-1], encoding="utf-8"
        )
        return raw

    deepseek_planner.complete_json = _complete_json  # type: ignore[method-assign]
    planner_gate: dict[str, Any] = {"provider": "deepseek", "model": deepseek_planner.model}
    planned = None
    try:
        started = time.perf_counter()
        planned = await MediaPlanner(
            deepseek_planner, enabled=True, v5_enabled=True
        ).plan(legacy_opportunity)
        planner_gate["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        planner_gate["result_type"] = type(planned).__name__
        planner_gate["reason"] = getattr(planned, "reason", None)
        planner_gate["detail"] = getattr(planned, "detail", None)
        if isinstance(planned, PlannedMedia):
            planner_gate["plan"] = summarize_plan(planned.plan)
        else:
            planner_gate["not_renderable"] = getattr(planned, "reason", str(planned))
            planner_gate["not_renderable_details"] = getattr(planned, "details", None)
        _LOG.info(
            "deepseek planner -> %s %s",
            type(planned).__name__,
            getattr(planned, "reason", None),
        )
    except Exception as exc:
        planner_gate["error"] = f"{type(exc).__name__}: {exc}"
        planner_gate["traceback"] = traceback.format_exc()[-2500:]
        planner_gate["content_policy"] = any(
            token in str(exc).lower()
            for token in ("content", "policy", "safety", "refused", "responsible")
        )
        _LOG.exception("deepseek planner raised")
    finally:
        close = getattr(deepseek_planner, "aclose", None)
        if callable(close):
            await close()
    planner_gate["usage"] = planner_usage.rows
    planner_gate["raw_output_preview"] = _preview(
        raw_planner_outputs[-1] if raw_planner_outputs else None
    )
    report["gates"]["plan"] = planner_gate
    dump_json(OUTPUT / "report.partial.json", report)

    plan = planned.plan if isinstance(planned, PlannedMedia) else None
    planner_bypass = False
    if plan is None or getattr(getattr(plan, "media_lane", None), "lane", None) != "suggestive_private":
        planner_bypass = True
        started = time.perf_counter()
        bypassed = await MediaPlanner(HighLanePicker(), enabled=True, v5_enabled=True).plan(
            legacy_opportunity
        )
        report["gates"]["plan"]["bypass"] = {
            "used": True,
            "reason": "production DeepSeek planner did not freeze suggestive_private; catalog picker used only to reach the Civitai cell",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "result_type": type(bypassed).__name__,
            "detail": getattr(bypassed, "reason", None)
            or (summarize_plan(bypassed.plan) if isinstance(bypassed, PlannedMedia) else None),
            "details": getattr(bypassed, "details", None),
        }
        if not isinstance(bypassed, PlannedMedia):
            report["conclusion"] = "不通：连合法候选挑选也无法冻计划"
            report["stopped_at"] = "plan"
            return report
        plan = bypassed.plan
    else:
        report["gates"]["plan"]["bypass"] = {"used": False}

    report["gates"]["plan"]["frozen"] = summarize_plan(plan)
    report["gates"]["plan"]["planner_bypass_used"] = planner_bypass
    dump_json(OUTPUT / "frozen-plan.json", summarize_plan(plan))

    closure = sourced_life_closure_error(plan)
    report["gates"]["sourced_life_precheck"] = {"error": closure, "ok": closure is None}
    if closure is not None:
        report["conclusion"] = f"不通：SourcedLifeMediaRenderer 预检 {closure}（还没打到 Civitai）"
        report["stopped_at"] = "sourced_life_precheck"
        return report

    hermes_usage_before = UsageCapture()
    inner_author_model = unwrap(author.model) if author.model is not None else None
    previous_observer = getattr(inner_author_model, "usage_observer", None) if inner_author_model else None

    def _author_observe(usage: object) -> None:
        hermes_usage_before(usage)
        if callable(previous_observer):
            previous_observer(usage)

    if inner_author_model is not None:
        inner_author_model.usage_observer = _author_observe

    started = time.perf_counter()
    try:
        rendered = await renderer.render(plan)
    except Exception as exc:
        report["gates"]["render"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4000:],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        report["conclusion"] = "不通：renderer.render 抛错"
        report["stopped_at"] = "render"
        report["civitai_http"] = [call.__dict__ for call in tracer.calls]
        return report

    elapsed = round((time.perf_counter() - started) * 1000, 1)
    civitai_http = [call.__dict__ for call in tracer.calls]
    dump_json(OUTPUT / "civitai-http.json", civitai_http)
    report["civitai_http"] = civitai_http
    report["civitai_cost_fields"] = extract_civitai_cost(tracer.calls)

    prompt_path = OUTPUT / "hermes-prompt.txt"
    if author.prompt:
        prompt_path.write_text(author.prompt, encoding="utf-8")
    report["gates"]["private_prompt"] = {
        "ok": author.prompt is not None and author.error is None,
        "error": author.error,
        "elapsed_ms": author.elapsed_ms,
        "prompt_path": str(prompt_path) if author.prompt else None,
        "prompt_len": len(author.prompt) if author.prompt else None,
        "excerpt": (" ".join(author.prompt.split())[:400] if author.prompt else None),
        "content_policy": bool(
            author.error
            and any(
                token in author.error.lower()
                for token in ("content", "policy", "safety", "refused", "responsible")
            )
        ),
        "usage": hermes_usage_before.rows,
        "author_model": getattr(author.model, "model", None),
        "author_provider": getattr(author.model, "provider", None),
    }

    inspect_gate = {
        "openai_inspect_called": bool(inspector.calls),
        "inspect_call_count": len(inspector.calls),
        "note": "specialized_private_workflow_direct must skip OpenAI visual inspect/repair",
    }
    report["gates"]["inspection"] = inspect_gate

    if isinstance(rendered, MediaRenderFailure):
        report["gates"]["render"] = {
            "ok": False,
            "type": "MediaRenderFailure",
            "reason": rendered.reason,
            "attempts": rendered.attempts,
            "elapsed_ms": elapsed,
            "inspection": None
            if rendered.last_inspection is None
            else {
                "passed": rendered.last_inspection.passed,
                "reason": rendered.last_inspection.reason,
                "inspector_model": rendered.last_inspection.inspector_model,
            },
        }
        report["images_spent"] = 1  # Civitai POST was accepted; tracer may miss calls
        report["conclusion"] = f"不通：卡在渲染格，原因 {rendered.reason}"
        report["stopped_at"] = "render"
        return report

    if not isinstance(rendered, RenderedMedia):
        report["gates"]["render"] = {
            "ok": False,
            "type": type(rendered).__name__,
            "elapsed_ms": elapsed,
        }
        report["conclusion"] = f"不通：未知渲染结果 {type(rendered).__name__}"
        report["stopped_at"] = "render"
        return report

    path = Path(rendered.path)
    nbytes = path.stat().st_size if path.is_file() else 0
    magic = path.read_bytes()[:8] if path.is_file() else b""
    dest = OUTPUT / path.name
    if path.resolve() != dest.resolve() and path.is_file():
        dest.write_bytes(path.read_bytes())
    receipt = Path(str(path) + ".civitai.json")
    receipt_payload = None
    if receipt.is_file():
        receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
        (OUTPUT / receipt.name).write_text(receipt.read_text(encoding="utf-8"), encoding="utf-8")

    report["gates"]["render"] = {
        "ok": True,
        "type": "RenderedMedia",
        "path": str(dest if dest.is_file() else path),
        "bytes": nbytes,
        "magic": repr(magic),
        "png": magic[:8] == b"\x89PNG\r\n\x1a\n",
        "attempts": rendered.attempts,
        "elapsed_ms": elapsed,
        "artifact_hash": rendered.artifact_hash,
        "inspection_reason": rendered.inspection.reason if rendered.inspection else None,
        "inspector_model": rendered.inspection.inspector_model if rendered.inspection else None,
        "inspection_passed": rendered.inspection.passed if rendered.inspection else None,
        "civitai_receipt": receipt_payload,
        "poll_gets": sum(
            1
            for call in tracer.calls
            if call.method == "GET" and "/workflows/" in call.url and call.status == 200
        ),
        "submit_posts": sum(
            1
            for call in tracer.calls
            if call.method == "POST" and call.url.endswith("/workflows")
        ),
    }
    inspect_gate["skipped_openai_inspect"] = (
        rendered.inspection is not None
        and rendered.inspection.reason == "specialized_private_workflow_direct"
        and not inspector.calls
    )
    report["images_spent"] = 1
    remaining_guard = (
        "冻结计划 + 受审 Civitai 模板 + 预算门 + provider/文件完整性；"
        "无 OpenAI 视觉审查、无 ≤1 次修复"
    )
    report["gates"]["inspection"]["remaining_guard"] = remaining_guard
    report["conclusion"] = "通：suggestive_private 真实出图"
    report["stopped_at"] = None
    return report


def attach_costs(report: dict[str, Any]) -> None:
    from companion_daemon.usage_metrics import estimate_model_cost_usd

    hermes_rows = list(report.get("gates", {}).get("private_prompt", {}).get("usage") or [])
    planner_rows = list(report.get("gates", {}).get("plan", {}).get("usage") or [])
    hermes = cost_from_usage(hermes_rows, models=("hermes", "nousresearch"))
    deepseek = cost_from_usage(planner_rows, models=("deepseek",))
    for bucket, rows in (("hermes", hermes_rows), ("deepseek", planner_rows)):
        usd = 0.0
        for row in rows:
            amount, _version = estimate_model_cost_usd(
                model=str(row.get("model") or "__unpriced__"),
                prompt_tokens=int(row.get("prompt_tokens") or 0),
                completion_tokens=int(row.get("completion_tokens") or 0),
                cache_hit_tokens=int(row.get("cache_hit_tokens") or 0),
                cache_miss_tokens=int(row.get("cache_miss_tokens") or 0),
            )
            usd += amount
        if bucket == "hermes":
            hermes["cost_usd_price_table"] = round(usd, 6)
            hermes["cost_cny_price_table"] = round(usd * 7.2, 6)
            hermes["price_table_note"] = (
                "nousresearch/hermes-4-70b is unpriced in MODEL_PRICES; "
                "table uses the conservative unpriced fallback"
            )
        else:
            deepseek["cost_usd_price_table"] = round(usd, 6)
            deepseek["cost_cny_price_table"] = round(usd * 7.2, 6)
    report["cost"] = {
        "deepseek_planner": deepseek,
        "openrouter_hermes": hermes,
        "civitai": report.get("civitai_cost_fields"),
        "images_spent": report.get("images_spent"),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        report = asyncio.run(run())
    except Exception as exc:
        report = {
            "contract": "prove-adult-p3-render.1",
            "conclusion": f"不通：脚本异常 {type(exc).__name__}: {exc}",
            "stopped_at": "script",
            "traceback": traceback.format_exc()[-4000:],
        }
    dump_json(OUTPUT / "report.pre-cost.json", report)
    try:
        attach_costs(report)
    except Exception as exc:
        report["cost_error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    dump_json(OUTPUT / "report.json", report)
    print(json.dumps(
        {
            "conclusion": report.get("conclusion"),
            "stopped_at": report.get("stopped_at"),
            "report": str(OUTPUT / "report.json"),
            "image": (report.get("gates") or {}).get("render", {}).get("path"),
            "bytes": (report.get("gates") or {}).get("render", {}).get("bytes"),
            "cost": report.get("cost"),
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
