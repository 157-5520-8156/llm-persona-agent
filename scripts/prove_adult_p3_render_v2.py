#!/usr/bin/env python3
"""One controlled P3 Civitai render on a production-ledger clone.

Unpatched production path: close_friend + declared sexual_suggestive →
suggestive_private / adult_suggestive. At most one Civitai workflow POST.
Later work is GET-only until the image lands or four hours elapse.

Never writes data/, never talks to 8787/NapCat. Artifacts go to
output/adult-render-v2/.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import logging
import os
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

OUTPUT = (REPO / "output" / "adult-render-v2").resolve()
PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
NEW_AIR = "urn:air:krea2:lora:civitai:2868686@3240992"
OLD_AIR = "urn:air:krea2:lora:civitai:2787068@3140284"
ABANDON_AFTER = timedelta(hours=4)
POLL_SLEEP_SECONDS = 20
_LOG = logging.getLogger("prove_adult_p3_render_v2")


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


class OnePostTracingTransport(httpx.AsyncBaseTransport):
    """Trace Civitai HTTP and refuse a second workflow POST."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.calls: list[HttpCall] = []
        self.workflow_posts = 0

    def post_count(self) -> int:
        return sum(
            1
            for call in self.calls
            if call.method == "POST" and call.url.rstrip("/").endswith("/workflows")
        )

    def get_count(self) -> int:
        return sum(
            1
            for call in self.calls
            if call.method == "GET" and "/workflows/" in call.url
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        started = time.perf_counter()
        req_preview = None
        url = _redact_url(str(request.url))
        if request.method == "POST" and url.rstrip("/").endswith("/workflows"):
            if self.workflow_posts >= 1:
                raise RuntimeError("refusing second Civitai workflow POST")
            self.workflow_posts += 1
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
                    url=url,
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
                    url=url,
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


class CachingAuthor:
    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.prompt: str | None = None
        self.error: str | None = None
        self.elapsed_ms: float | None = None
        self.calls = 0
        self.model = getattr(inner, "model", None)

    async def write(self, plan: object) -> str:
        if self.prompt is not None:
            return self.prompt
        started = time.perf_counter()
        self.calls += 1
        try:
            prompt = await self.inner.write(plan)  # type: ignore[misc]
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            raise
        self.prompt = prompt
        self.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return prompt


def _dump_complete_candidates(candidates: list[dict[str, Any]]) -> None:
    lanes: dict[str, int] = {}
    for item in candidates:
        for lane in item.get("legal_media_lanes", []) or []:
            lanes[str(lane)] = lanes.get(str(lane), 0) + 1
    dump_json(
        OUTPUT / "complete-candidates-summary.json",
        {
            "n": len(candidates),
            "legal_lanes": sorted(lanes),
            "lane_counts": lanes,
            "suggestive_private_count": lanes.get("suggestive_private", 0),
        },
    )


class HighLanePicker:
    """Pick one already-legal suggestive_private complete candidate. Not a role author."""

    async def complete_json(self, messages, *, temperature=0.8):  # type: ignore[no-untyped-def]
        return await self.complete(messages, temperature=temperature)

    async def complete(self, messages, *, temperature=0.8):  # type: ignore[no-untyped-def]
        del temperature
        content = str(messages[-1]["content"])
        encoded = content.split("legal_complete_media_expression_candidates=", 1)[1].split(
            "\n", 1
        )[0]
        candidates = json.loads(encoded)
        _dump_complete_candidates(candidates)
        suggestive = [
            item
            for item in candidates
            if "suggestive_private" in item.get("legal_media_lanes", [])
        ]
        if not suggestive:
            lanes = sorted(
                {lane for item in candidates for lane in item.get("legal_media_lanes", [])}
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
        cost = body.get("cost")
        if isinstance(cost, dict) and cost.get("total") not in (None, ""):
            buzz = cost.get("total")
        transactions = body.get("transactions")
        if isinstance(transactions, dict):
            items = transactions.get("list")
            if isinstance(items, list):
                total = 0
                for item in items:
                    if isinstance(item, dict) and item.get("type") == "debit":
                        amount = item.get("amount")
                        if isinstance(amount, int):
                            total += amount
                if total:
                    buzz = total
    return {"workflow_id": workflow_id, "last_status": status, "cost_field": buzz}


def clone_usage_rows(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        usage = []
        try:
            for row in conn.execute(
                "SELECT kind, estimated_cny, note, created_at FROM usage_events "
                "WHERE kind = 'civitai_buzz' ORDER BY id DESC LIMIT 8"
            ):
                usage.append(
                    {
                        "kind": row[0],
                        "estimated_cny": row[1],
                        "note": row[2],
                        "created_at": row[3],
                    }
                )
        except sqlite3.OperationalError as exc:
            usage = [{"error": str(exc)}]
        model_rows = []
        try:
            for row in conn.execute(
                "SELECT purpose, model, provider, status, error, created_at "
                "FROM model_usage_events WHERE provider = 'civitai' "
                "ORDER BY rowid DESC LIMIT 8"
            ):
                model_rows.append(
                    {
                        "purpose": row[0],
                        "model": row[1],
                        "provider": row[2],
                        "status": row[3],
                        "error": row[4],
                        "created_at": row[5],
                    }
                )
        except sqlite3.OperationalError as exc:
            model_rows = [{"error": str(exc)}]
        return {"usage_events_civitai_buzz": usage, "model_usage_events_civitai": model_rows}
    finally:
        conn.close()


def find_existing_receipt(generated_dir: Path) -> dict[str, Any] | None:
    if not generated_dir.is_dir():
        return None
    receipts = sorted(generated_dir.glob("*.civitai.json"))
    for path in receipts:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("workflow_id"):
            png = path.with_suffix("")
            if png.suffix != ".png":
                png = Path(str(path)[: -len(".civitai.json")])
            return {"receipt_path": str(path), "png_path": str(png), "receipt": payload}
    return None


def _stop(report: dict[str, Any], *, cell: str, conclusion: str) -> dict[str, Any]:
    report["stopped_at"] = cell
    report["conclusion"] = conclusion
    print(f"STOPPED at {cell}: {conclusion}", flush=True)
    return report


async def run() -> dict[str, Any]:
    from companion_daemon.config import Settings
    from companion_daemon.db import CompanionStore
    from companion_daemon.event_media import (
        MediaPlanner,
        MediaRenderFailure,
        RenderedMedia,
        _safe_filename,
    )
    from companion_daemon.image_generation import (
        CivitaiTemplateWorkflowImageGenerator,
        ImageGenerationProviderError,
        bind_image_spend_store,
    )
    from companion_daemon.world_v2.event_media_planner_adapter import EventMediaPlannerAdapter
    from companion_daemon.world_v2.media_v2 import StoredMediaPayload, planning_request_id
    from companion_daemon.world_v2.qq_media_deployment import build_qq_media_preview_deployment
    from companion_daemon.world_v2.sourced_life_media import sourced_life_closure_error
    from tests.world_v2.test_adult_media_authorization import _authorize, _p3_world

    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    generated_dir = OUTPUT / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "contract": "prove-adult-p3-render-v2.1",
        "started_at": datetime.now(UTC).isoformat(),
        "clone": str(clone),
        "production_source": str(PRODUCTION_DB),
        "stage_under_test": "close_friend",
        "snapshot_patched": False,
        "new_air": NEW_AIR,
        "old_air": OLD_AIR,
        "max_civitai_posts": 1,
        "gates": {},
    }

    equivalent_path = OUTPUT / "preflight-equivalent.json"
    if equivalent_path.is_file():
        equivalent = json.loads(equivalent_path.read_text(encoding="utf-8"))
        report["gates"]["preflight"] = equivalent.get("summary") or equivalent
        summary = report["gates"]["preflight"]
        if not summary.get("gate_http_200"):
            return _stop(
                report,
                cell="preflight",
                conclusion=f"不通：GET /v2/resources HTTP {summary.get('resource_http')}，不是 200",
            )
        if not summary.get("gate_equivalent_canGenerate"):
            return _stop(
                report,
                cell="preflight",
                conclusion="不通：resource 非 200 且等效 canGenerate 也没有成立",
            )
        report["gates"]["preflight"]["note"] = (
            "orchestration canGenerate is still false/unavailable (worker cache cold); "
            "HTTP 200 plus tRPC/mini/permissions equivalent canGenerate=true. "
            "Production preflight only rejects 404. First POST may sit in preparing."
        )

    template = json.loads(
        (REPO / "configs" / "civitai-krea2-celia-realism-template.json").read_text(
            encoding="utf-8"
        )
    )
    loras = template["steps"][0]["input"]["loras"]
    report["gates"]["template"] = {
        "identity_air": NEW_AIR,
        "identity_present": NEW_AIR in loras,
        "old_air_absent": OLD_AIR not in loras,
        "patch_air_present": "urn:air:krea2:lora:civitai:2750659@3094831" in loras,
        "slider_air_present": "urn:air:krea2:lora:civitai:2781697@3132956" in loras,
        "line_28_old": OLD_AIR,
        "line_28_new": NEW_AIR,
        "weight": loras.get(NEW_AIR),
    }
    if OLD_AIR in loras or NEW_AIR not in loras:
        return _stop(report, cell="template", conclusion="不通：受审模板身份 AIR 没有换成新值")

    existing = find_existing_receipt(generated_dir)
    report["existing_receipt"] = existing
    clone_ledger(PRODUCTION_DB, clone)
    os.environ["DATABASE_PATH"] = str(clone)

    settings = Settings(database_path=clone)
    world_id = f"world:companion-v2:qq-c2c:{settings.primary_user_id}"
    report["world_id"] = world_id
    spend_token = bind_image_spend_store(CompanionStore(clone))
    report["gates"]["env"] = {
        "civitai_api_key": bool(settings.civitai_api_key),
        "openrouter_api_key": bool(settings.openrouter_api_key),
        "world_v2_adult_media_enabled": bool(settings.world_v2_adult_media_enabled),
        "civitai_krea2_enabled": bool(settings.civitai_krea2_enabled),
        "database_path": str(settings.database_path),
        "template_path": str(settings.civitai_krea2_template_path),
    }

    bundle = build_qq_media_preview_deployment(
        settings=settings, world_id=world_id, output_dir=generated_dir
    )
    if bundle is None:
        return _stop(report, cell="factory", conclusion="不通：生产工厂没装上媒体车道")
    renderer = bundle.transport._renderer
    specialized_wrapped = renderer.specialized_generators.get("adult_suggestive")
    specialized = unwrap(specialized_wrapped) if specialized_wrapped is not None else None
    if not isinstance(specialized, CivitaiTemplateWorkflowImageGenerator):
        return _stop(
            report,
            cell="factory",
            conclusion=f"不通：specialized generator 是 {type(specialized).__name__}",
        )
    report["gates"]["factory"] = {
        "ok": True,
        "generator": type(specialized).__name__,
        "template": str(specialized.template_path),
        "template_identity_air": NEW_AIR
        in (specialized.template.get("steps") or [{}])[0]
        .get("input", {})
        .get("loras", {}),
    }

    proxy = settings.civitai_proxy_url or settings.openai_proxy_url
    inner_transport = (
        specialized.transport
        if isinstance(specialized.transport, httpx.AsyncBaseTransport)
        else httpx.AsyncHTTPTransport(proxy=proxy)
    )
    tracer = OnePostTracingTransport(inner=inner_transport)
    specialized.transport = tracer
    inspector = CountingInspector(renderer.inspector)
    renderer.inspector = inspector
    author = CachingAuthor(renderer.private_prompt_author)
    renderer.private_prompt_author = author

    hermes_usage = UsageCapture()
    inner_author_model = unwrap(author.model) if author.model is not None else None
    previous_observer = (
        getattr(inner_author_model, "usage_observer", None) if inner_author_model else None
    )

    def _author_observe(usage: object) -> None:
        hermes_usage(usage)
        if callable(previous_observer):
            previous_observer(usage)

    if inner_author_model is not None:
        inner_author_model.usage_observer = _author_observe

    try:
        ledger, candidate = _p3_world(
            stage="close_friend",
            with_adult_grants=True,
            display_intent="sexual_suggestive",
        )
        opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    except Exception as exc:
        return _stop(
            report,
            cell="authorize",
            conclusion=f"不通：构造/授权失败 {type(exc).__name__}: {exc}",
        )

    display = compiled.snapshot.image_event_snapshot.relationship_media_context.declared_display
    dumped = compiled.snapshot.image_event_snapshot.model_dump(mode="json")
    appearance = (dumped.get("character") or {}).get("appearance_state")
    location = dumped.get("location")
    dump_json(OUTPUT / "authorized-snapshot.json", dumped)
    report["gates"]["authorize"] = {
        "ok": True,
        "opportunity_id": opportunity.opportunity_id,
        "media_lane": opportunity.media_lane,
        "relationship_stage": "close_friend",
        "declared_media_intent": None if display is None else display.media_intent,
        "appearance_frozen": appearance is not None,
        "appearance_outfit": None
        if not isinstance(appearance, dict)
        else (appearance.get("visible_attributes") or [{}])[0].get("description"),
        "location_kind": None if not isinstance(location, dict) else location.get("kind"),
        "snapshot_patched": False,
    }
    if opportunity.media_lane != "suggestive_private":
        return _stop(
            report,
            cell="authorize",
            conclusion=f"不通：授权车道是 {opportunity.media_lane}，不是 suggestive_private",
        )

    adapter = bundle.deployment.planner
    assert isinstance(adapter, EventMediaPlannerAdapter)
    adapter._sidecar.put_if_absent(
        StoredMediaPayload(
            payload_ref=opportunity.event_snapshot_ref,
            payload_hash=opportunity.event_snapshot_hash,
            content_type="application/vnd.world-v2.media-opportunity+json",
            body=compiled.snapshot_body,
        )
    )
    adapter._legacy_planner = MediaPlanner(HighLanePicker(), enabled=True, v5_enabled=True)

    from companion_daemon.event_media import MediaPlan as LegacyMediaPlan

    result = await adapter.plan(
        opportunity=opportunity,
        planning_request_id=planning_request_id(opportunity.opportunity_id),
    )
    if result.plan is None or result.plan_payload is None:
        report["gates"]["plan"] = {
            "ok": False,
            "not_renderable": None
            if result.not_renderable is None
            else {
                "reason": result.not_renderable.reason,
                "detail": getattr(result.not_renderable, "detail", None),
            },
        }
        return _stop(
            report,
            cell="plan",
            conclusion=(
                "不通：生产 adapter 没有冻住计划 "
                f"{getattr(result.not_renderable, 'reason', None)}"
            ),
        )
    plan = LegacyMediaPlan.from_payload(json.loads(result.plan_payload.body))
    plan_summary = summarize_plan(plan)
    dump_json(OUTPUT / "frozen-plan.json", plan_summary)
    report["gates"]["plan"] = {
        "ok": True,
        "adapter_class": type(adapter).__name__,
        "snapshot_patched": False,
        "skipped_additional_deepseek": True,
        **plan_summary,
    }
    if plan_summary.get("lane") != "suggestive_private":
        return _stop(
            report,
            cell="plan",
            conclusion=f"不通：冻结车道是 {plan_summary.get('lane')}",
        )
    if plan_summary.get("render_route") != "adult_suggestive":
        return _stop(
            report,
            cell="plan",
            conclusion=f"不通：render_route 是 {plan_summary.get('render_route')}",
        )

    closure = sourced_life_closure_error(plan)
    report["gates"]["sourced_life_precheck"] = {"error": closure, "ok": closure is None}
    if closure is not None:
        return _stop(
            report,
            cell="sourced_life_precheck",
            conclusion=f"不通：SourcedLifeMediaRenderer 预检 {closure}（还没打到 Civitai）",
        )

    output_path = generated_dir / f"{_safe_filename(plan.plan_id)}.png"
    size = renderer._render_size(plan)

    if existing and Path(existing["png_path"]).resolve() == output_path.resolve():
        report["gates"]["render"] = {
            "resumed": True,
            "receipt": existing["receipt"],
            "note": "receipt already present; GET-only reconcile, no new POST",
        }
        print("RESUMING existing workflow GET poll", flush=True)
        first = None
    else:
        if existing:
            return _stop(
                report,
                cell="render",
                conclusion=(
                    "不通：generated/ 里已有另一张图的 receipt，拒绝再 POST。"
                    f" existing={existing['receipt_path']}"
                ),
            )
        started = time.perf_counter()
        print("RENDER first call (at most one POST)", flush=True)
        first = await renderer.render(plan)
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        dump_json(OUTPUT / "civitai-http.json", [call.__dict__ for call in tracer.calls])
        report["civitai_http"] = [call.__dict__ for call in tracer.calls]
        report["civitai_cost_fields"] = extract_civitai_cost(tracer.calls)
        report["gates"]["private_prompt"] = {
            "ok": author.prompt is not None and author.error is None,
            "error": author.error,
            "elapsed_ms": author.elapsed_ms,
            "prompt_len": len(author.prompt) if author.prompt else None,
            "calls": author.calls,
            "usage": hermes_usage.rows,
        }
        if author.prompt:
            (OUTPUT / "hermes-prompt.txt").write_text(author.prompt, encoding="utf-8")
        posts = tracer.post_count()
        report["gates"]["civitai_posts"] = posts
        print(f"POST count after first render: {posts}", flush=True)
        if posts > 1:
            return _stop(report, cell="render", conclusion="不通：发生了第二次 POST")
        if isinstance(first, RenderedMedia):
            report["gates"]["render"] = {
                "ok": True,
                "immediate": True,
                "elapsed_ms": elapsed,
                "attempts": first.attempts,
            }
        elif isinstance(first, MediaRenderFailure):
            report["gates"]["render"] = {
                "ok": False,
                "type": "MediaRenderFailure",
                "reason": first.reason,
                "attempts": first.attempts,
                "elapsed_ms": elapsed,
            }
            if not str(first.reason).startswith("image_provider_pending"):
                report["usage_on_clone"] = clone_usage_rows(clone)
                return _stop(
                    report,
                    cell="render",
                    conclusion=f"不通：卡在渲染格，原因 {first.reason}",
                )
            print(f"PENDING {first.reason}; GET-only poll up to 4h", flush=True)
        else:
            return _stop(
                report,
                cell="render",
                conclusion=f"不通：未知渲染结果 {type(first).__name__}",
            )

    if isinstance(first, RenderedMedia):
        rendered = first
    else:
        prompt = author.prompt or "resume-from-receipt"
        receipt_path = output_path.with_suffix(output_path.suffix + ".civitai.json")
        if not receipt_path.is_file() and author.prompt is None:
            return _stop(
                report,
                cell="render",
                conclusion="不通：pending 但没有 prompt/receipt，无法 GET reconcile",
            )
        deadline = datetime.now(UTC) + ABANDON_AFTER
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            submitted = receipt.get("submitted_at")
            abandon_after = receipt.get("abandon_after")
            report["gates"]["receipt"] = receipt
            if isinstance(abandon_after, str) and abandon_after:
                deadline = datetime.fromisoformat(abandon_after.replace("Z", "+00:00"))
            dump_json(OUTPUT / "civitai-receipt.json", receipt)
        polls: list[dict[str, Any]] = []
        rendered = None
        while datetime.now(UTC) < deadline:
            await asyncio.sleep(POLL_SLEEP_SECONDS)
            sample = {
                "t": datetime.now(UTC).isoformat(),
                "posts": tracer.post_count(),
                "gets": tracer.get_count(),
            }
            if receipt_path.is_file():
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                sample["status"] = receipt.get("status")
                sample["yellow_buzz"] = receipt.get("yellow_buzz")
                sample["preparing_resource"] = receipt.get("preparing_resource")
                sample["has_output"] = receipt.get("has_output")
            polls.append(sample)
            dump_json(OUTPUT / "render-poll.json", polls)
            dump_json(OUTPUT / "civitai-http.json", [call.__dict__ for call in tracer.calls])
            print(json.dumps(sample, ensure_ascii=False), flush=True)
            if tracer.post_count() > 1:
                return _stop(report, cell="render", conclusion="不通：轮询期间出现第二次 POST")
            try:
                generated = await specialized_wrapped.generate(
                    prompt,
                    output_path=output_path,
                    size=size,
                )
            except ImageGenerationProviderError as exc:
                if exc.kind == "pending":
                    continue
                report["gates"]["render_poll"] = {
                    "error": str(exc),
                    "kind": exc.kind,
                    "detail": exc.detail,
                    "polls": polls[-5:],
                }
                report["usage_on_clone"] = clone_usage_rows(clone)
                return _stop(
                    report,
                    cell="render",
                    conclusion=f"不通：GET 对账失败 {exc.kind}:{exc.detail}",
                )
            except RuntimeError as exc:
                report["usage_on_clone"] = clone_usage_rows(clone)
                return _stop(report, cell="render", conclusion=f"不通：{exc}")
            if output_path.is_file() and output_path.stat().st_size > 0:
                rendered = generated
                break
        else:
            report["gates"]["render_poll"] = {"polls": polls[-8:], "abandoned": True}
            report["usage_on_clone"] = clone_usage_rows(clone)
            return _stop(
                report,
                cell="render",
                conclusion="不通：4 小时 GET 对账仍无图（已放弃，没有第二次 POST）",
            )

    path = Path(getattr(rendered, "path", output_path))
    nbytes = path.stat().st_size if path.is_file() else 0
    magic = path.read_bytes()[:8] if path.is_file() else b""
    dest = OUTPUT / path.name
    if path.resolve() != dest.resolve() and path.is_file():
        dest.write_bytes(path.read_bytes())
    receipt_path = path.with_suffix(path.suffix + ".civitai.json")
    receipt_payload = None
    if receipt_path.is_file():
        receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        (OUTPUT / receipt_path.name).write_text(
            receipt_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    report["gates"]["render"] = {
        "ok": True,
        "type": "RenderedMedia",
        "path": str(dest if dest.is_file() else path),
        "bytes": nbytes,
        "magic": repr(magic),
        "png": magic[:8] == b"\x89PNG\r\n\x1a\n",
        "civitai_receipt": receipt_payload,
        "poll_gets": tracer.get_count(),
        "submit_posts": tracer.post_count(),
        "openai_inspect_called": bool(inspector.calls),
    }
    report["civitai_http"] = [call.__dict__ for call in tracer.calls]
    report["civitai_cost_fields"] = extract_civitai_cost(tracer.calls)
    report["usage_on_clone"] = clone_usage_rows(clone)
    report["images_spent"] = tracer.post_count()
    report["conclusion"] = "通：close_friend + sexual_suggestive 真实出图"
    report["stopped_at"] = None
    print(
        f"READY path={report['gates']['render']['path']} bytes={nbytes} "
        f"posts={tracer.post_count()}",
        flush=True,
    )
    del spend_token
    return report


def attach_costs(report: dict[str, Any]) -> None:
    from companion_daemon.usage_metrics import estimate_model_cost_usd

    hermes_rows = list(report.get("gates", {}).get("private_prompt", {}).get("usage") or [])
    usd = 0.0
    for row in hermes_rows:
        amount, _version = estimate_model_cost_usd(
            model=str(row.get("model") or "__unpriced__"),
            prompt_tokens=int(row.get("prompt_tokens") or 0),
            completion_tokens=int(row.get("completion_tokens") or 0),
            cache_hit_tokens=int(row.get("cache_hit_tokens") or 0),
            cache_miss_tokens=int(row.get("cache_miss_tokens") or 0),
        )
        usd += amount
    receipt = (report.get("gates") or {}).get("render", {}).get("civitai_receipt") or {}
    buzz = receipt.get("yellow_buzz") if isinstance(receipt, dict) else None
    if buzz is None:
        buzz = (report.get("civitai_cost_fields") or {}).get("cost_field")
    report["cost"] = {
        "openrouter_hermes": {
            "calls": len(hermes_rows),
            "cost_usd_price_table": round(usd, 6),
            "cost_cny_price_table": round(usd * 7.2, 6),
            "rows": hermes_rows,
        },
        "civitai": {
            "images_posted": report.get("images_spent")
            or (report.get("gates") or {}).get("civitai_posts"),
            "yellow_buzz_debited": buzz,
            "fields": report.get("civitai_cost_fields"),
        },
        "deepseek_planner": {"calls": 0, "note": "HighLanePicker; no additional DeepSeek"},
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        report = asyncio.run(run())
    except Exception as exc:
        report = {
            "contract": "prove-adult-p3-render-v2.1",
            "conclusion": f"不通：脚本异常 {type(exc).__name__}: {exc}",
            "stopped_at": "script",
            "traceback": traceback.format_exc()[-4000:],
        }
        print(f"STOPPED at script: {report['conclusion']}", flush=True)
    dump_json(OUTPUT / "report.pre-cost.json", report)
    try:
        attach_costs(report)
    except Exception as exc:
        report["cost_error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    dump_json(OUTPUT / "report.json", report)
    print(
        json.dumps(
            {
                "conclusion": report.get("conclusion"),
                "stopped_at": report.get("stopped_at"),
                "report": str(OUTPUT / "report.json"),
                "image": (report.get("gates") or {}).get("render", {}).get("path"),
                "bytes": (report.get("gates") or {}).get("render", {}).get("bytes"),
                "cost": report.get("cost"),
                "usage_on_clone": report.get("usage_on_clone"),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
