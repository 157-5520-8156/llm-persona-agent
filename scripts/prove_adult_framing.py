#!/usr/bin/env python3
"""Prove explicit_adult no longer inherits the more conservative frame.

Dry-runs both Civitai payloads (no POST), then submits at most one
explicit_adult workflow for the framing fix. A second POST is only for
same-prompt seed variance when FRAMING_SEED_VARIANCE=1.

Never writes data/, never talks to 8787/NapCat.
Artifacts go to output/adult-framing/.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.prove_adult_charge_intensity import (  # noqa: E402
    OnePostTracingTransport,
    PRODUCTION_DB,
    _is_production_path,
    authorize_and_plan,
    clone_ledger,
    dump_json,
    extract_civitai_cost,
    preflight_airs,
    template_layer,
    unwrap,
)
from scripts import prove_adult_regions as regions_script  # noqa: E402
from scripts.prove_adult_regions import (  # noqa: E402
    civitai_payload_for,
    payload_text_block,
    region_audit,
    render_once,
    write_hermes_prompt,
)

OUTPUT = (REPO / "output" / "adult-framing").resolve()
SUGGESTIVE_BASELINE = (REPO / "output" / "adult-charge" / "sexual_suggestive.jpg").resolve()
POLL_SLEEP_SECONDS = 20
ABANDON_AFTER = timedelta(hours=4)


def _stop(report: dict[str, Any], *, cell: str, conclusion: str) -> dict[str, Any]:
    report["stopped_at"] = cell
    report["conclusion"] = conclusion
    print(f"STOPPED at {cell}: {conclusion}", flush=True)
    return report


class MaxPostsTracingTransport(OnePostTracingTransport):
    """Trace Civitai HTTP and refuse POSTs beyond a hard cap."""

    def __init__(self, inner: httpx.AsyncBaseTransport, *, max_posts: int) -> None:
        super().__init__(inner)
        self._max_posts = max_posts

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        from scripts.prove_adult_p3_render_v2 import HttpCall, _preview, _redact_url
        import time as time_mod

        started = time_mod.perf_counter()
        req_preview = None
        url = _redact_url(str(request.url))
        if request.method == "POST" and url.rstrip("/").endswith("/workflows"):
            if self.workflow_posts >= self._max_posts:
                raise RuntimeError("refusing extra Civitai workflow POST")
            self.workflow_posts += 1
        if request.content:
            try:
                req_preview = _preview(json.loads(request.content.decode("utf-8")))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                req_preview = f"<body {len(request.content)} bytes>"
        try:
            response = await self._inner.handle_async_request(request)
            raw = await response.aread()
            elapsed = (time_mod.perf_counter() - started) * 1000
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
                    elapsed_ms=round((time_mod.perf_counter() - started) * 1000, 1),
                    error=f"{type(exc).__name__}: {exc}",
                    request_preview=req_preview,
                )
            )
            raise


async def run(*, seed_variance: bool) -> dict[str, Any]:
    from companion_daemon.config import Settings
    from companion_daemon.db import CompanionStore
    from companion_daemon.event_media import _compile_krea2_private_prompt
    from companion_daemon.image_generation import (
        CivitaiTemplateWorkflowImageGenerator,
        bind_image_spend_store,
    )
    from companion_daemon.world_v2.qq_media_deployment import build_qq_media_preview_deployment

    OUTPUT.mkdir(parents=True, exist_ok=True)
    regions_script.OUTPUT = OUTPUT
    clone = OUTPUT / "clone.sqlite"
    generated_dir = OUTPUT / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    max_posts = 2 if seed_variance else 1
    report: dict[str, Any] = {
        "contract": "prove-adult-framing.1",
        "started_at": datetime.now(UTC).isoformat(),
        "clone": str(clone),
        "production_source": str(PRODUCTION_DB),
        "max_civitai_posts": max_posts,
        "seed_variance": seed_variance,
        "suggestive_baseline": str(SUGGESTIVE_BASELINE),
        "gates": {},
    }
    if _is_production_path(clone):
        return _stop(report, cell="clone", conclusion="拒绝写 data/")
    clone_ledger(PRODUCTION_DB, clone)
    os.environ["DATABASE_PATH"] = str(clone)
    settings = Settings(database_path=clone)
    bind_image_spend_store(CompanionStore(clone))
    template_path = REPO / "configs" / "civitai-krea2-celia-realism-template.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    report["gates"]["template"] = template_layer()
    preflight = await preflight_airs(settings)
    dump_json(OUTPUT / "air-preflight.json", preflight)
    report["gates"]["air_preflight"] = preflight
    if not preflight["all_http_200"]:
        return _stop(report, cell="preflight", conclusion="不通：三个 AIR 没有全部 GET 200")

    layers: list[dict[str, Any]] = []
    prepared: dict[str, dict[str, Any]] = {}
    for intent in ("sexual_suggestive", "explicit_adult"):
        prepared[intent] = await authorize_and_plan(
            intent=intent, settings=settings, generated_dir=generated_dir
        )
        layers.append(prepared[intent]["layers"])
    dump_json(OUTPUT / "layer-table.json", layers)
    report["gates"]["layers"] = layers

    form_rows = {}
    for intent in ("sexual_suggestive", "explicit_adult"):
        plan = prepared[intent]["plan"]
        embodied = plan.embodied_presentation
        catalog = list(embodied.allowed_regions) if embodied is not None else []
        facts = layers[0 if intent == "sexual_suggestive" else 1]["plan"]["frozen_prompt_facts"]
        form_rows[intent] = {
            "visual_form": plan.visual_form,
            "shot_distance": None
            if plan.camera_geometry is None
            else plan.camera_geometry.shot_distance,
            "occupancy": None
            if plan.camera_geometry is None
            else plan.camera_geometry.subject_occupancy,
            "disclosure": None
            if plan.media_address_strategy is None
            else plan.media_address_strategy.disclosure_mode,
            "coverage": None if embodied is None else embodied.coverage_mode,
            "complete_candidate_id": layers[
                0 if intent == "sexual_suggestive" else 1
            ]["plan"]["complete_candidate_id"],
            **region_audit(facts, catalog),
            "krea2_short_prompt": _compile_krea2_private_prompt(plan),
        }
    dump_json(OUTPUT / "framing-audit.json", form_rows)
    report["gates"]["framing_audit"] = form_rows
    explicit_audit = form_rows["explicit_adult"]
    ranking = {
        "suggestive_visual_form": form_rows["sexual_suggestive"]["visual_form"],
        "explicit_visual_form": form_rows["explicit_adult"]["visual_form"],
        "suggestive_shot_distance": form_rows["sexual_suggestive"]["shot_distance"],
        "explicit_shot_distance": form_rows["explicit_adult"]["shot_distance"],
        "suggestive_occupancy": form_rows["sexual_suggestive"]["occupancy"],
        "explicit_occupancy": form_rows["explicit_adult"]["occupancy"],
        "explicit_is_closeup": explicit_audit["visual_form"] == "portrait_closeup",
        "explicit_not_wider_than_suggestive": not (
            form_rows["sexual_suggestive"]["visual_form"] == "portrait_closeup"
            and explicit_audit["visual_form"] == "portrait_context"
        ),
    }
    report["gates"]["visual_form_ranking"] = ranking
    if not ranking["explicit_is_closeup"]:
        return _stop(
            report,
            cell="ranking",
            conclusion=(
                "不通：explicit_adult 仍不是 portrait_closeup "
                f"({explicit_audit['visual_form']})，未 POST"
            ),
        )
    if explicit_audit["shoulder_only"] or not explicit_audit["includes_non_key_body"]:
        return _stop(
            report,
            cell="regions",
            conclusion="不通：explicit_adult 提示词事实仍是肩以上，未 POST",
        )

    hermes_rows = []
    for intent in ("sexual_suggestive", "explicit_adult"):
        item = prepared[intent]
        hermes = await write_hermes_prompt(
            item["bundle"], item["plan"], OUTPUT / f"hermes-{intent}.txt"
        )
        (OUTPUT / f"author-system-{intent}.txt").write_text(
            hermes["author_system"], encoding="utf-8"
        )
        (OUTPUT / f"author-user-{intent}.txt").write_text(
            hermes["author_user"], encoding="utf-8"
        )
        hermes_rows.append({"intent": intent, **hermes})
    dump_json(
        OUTPUT / "hermes-prompts.json",
        [{key: row[key] for key in row if key != "prompt"} for row in hermes_rows],
    )
    report["gates"]["hermes"] = [
        {key: row[key] for key in row if key not in {"prompt", "author_system", "author_user"}}
        for row in hermes_rows
    ]
    explicit_system = hermes_rows[1]["author_system"]
    if not hermes_rows[1]["contains_hard_boundary"]:
        return _stop(
            report,
            cell="hermes",
            conclusion="不通：explicit_adult 作者合同丢掉了无性行为/不露关键部位，未 POST",
        )
    if "close, body-inclusive frame" not in explicit_system:
        return _stop(
            report,
            cell="hermes",
            conclusion="不通：explicit_adult 作者合同没有近景身体取景说明，未 POST",
        )
    if "visual form: portrait closeup" not in hermes_rows[1]["author_user"]:
        return _stop(
            report,
            cell="hermes",
            conclusion="不通：explicit_adult 作者仍看到 portrait context，未 POST",
        )

    world_id = f"world:companion-v2:qq-c2c:{settings.primary_user_id}"
    render_bundle = build_qq_media_preview_deployment(
        settings=settings, world_id=world_id, output_dir=generated_dir
    )
    if render_bundle is None:
        return _stop(report, cell="factory", conclusion="不通：生产工厂没装上媒体车道")
    renderer = render_bundle.transport._renderer
    sizes = {
        intent: renderer._render_size(prepared[intent]["plan"])
        for intent in ("sexual_suggestive", "explicit_adult")
    }
    payloads = {
        intent: civitai_payload_for(
            prompt=hermes_rows[index]["prompt"],
            size=sizes[intent],
            template=template,
        )
        for index, intent in enumerate(("sexual_suggestive", "explicit_adult"))
    }
    dump_json(OUTPUT / "civitai-payloads.json", payloads)
    comparison = payload_text_block("sexual_suggestive", payloads["sexual_suggestive"])
    comparison += payload_text_block("explicit_adult", payloads["explicit_adult"])
    (OUTPUT / "civitai-payload-comparison.txt").write_text(comparison, encoding="utf-8")
    report["gates"]["payload_dry_run"] = {
        intent: {
            "prompt_len": len(str(payload["prompt"])),
            "negative": payload["negativePrompt"],
            "loras": payload["loras"],
            "steps": payload["steps"],
            "cfgScale": payload["cfgScale"],
            "sampler": payload["sampler"],
            "scheduler": payload["scheduler"],
            "seed": payload["seed"],
            "size": f"{payload['width']}x{payload['height']}",
            "allowMatureContent": payload["allowMatureContent"],
            "visual_form_in_author_user": (
                "portrait closeup"
                if "visual form: portrait closeup" in hermes_rows[index]["author_user"]
                else "portrait context"
                if "visual form: portrait context" in hermes_rows[index]["author_user"]
                else "other"
            ),
        }
        for index, (intent, payload) in enumerate(payloads.items())
    }
    if payloads["sexual_suggestive"]["prompt"] == payloads["explicit_adult"]["prompt"]:
        return _stop(report, cell="payload", conclusion="不通：两档最终 prompt 相同，未 POST")

    specialized_wrapped = renderer.specialized_generators.get("adult_suggestive")
    specialized = unwrap(specialized_wrapped)
    if not isinstance(specialized, CivitaiTemplateWorkflowImageGenerator):
        return _stop(
            report,
            cell="factory",
            conclusion=f"不通：generator 是 {type(specialized).__name__}",
        )
    proxy = settings.civitai_proxy_url or settings.openai_proxy_url
    inner_transport = (
        specialized.transport
        if isinstance(specialized.transport, httpx.AsyncBaseTransport)
        else httpx.AsyncHTTPTransport(proxy=proxy)
    )
    tracer = MaxPostsTracingTransport(inner=inner_transport, max_posts=max_posts)
    specialized.transport = tracer

    print("RENDER explicit_adult framing (POST 1)", flush=True)
    rendered = await render_once(
        bundle=render_bundle,
        plan=prepared["explicit_adult"]["plan"],
        tracer=tracer,
        generated_dir=generated_dir,
        label="explicit_adult",
        prompt=hermes_rows[1]["prompt"],
    )
    dump_json(OUTPUT / "civitai-http.json", [call.__dict__ for call in tracer.calls])
    report["gates"]["render_explicit_adult"] = rendered
    report["civitai_cost_fields"] = extract_civitai_cost(tracer.calls)
    report["posts"] = tracer.post_count()
    if tracer.post_count() > max_posts:
        return _stop(report, cell="render", conclusion="不通：Civitai POST 超过上限")
    if not rendered.get("ok"):
        return _stop(
            report,
            cell="render_explicit_adult",
            conclusion=f"不通：explicit_adult 渲染失败 {rendered.get('reason')}",
        )

    image_path = Path(str(rendered.get("path") or ""))
    final_path = OUTPUT / "explicit_adult.jpg"
    if image_path.is_file():
        final_path.write_bytes(image_path.read_bytes())
    report["explicit_image"] = {
        "path": str(final_path if final_path.is_file() else image_path),
        "bytes": final_path.stat().st_size if final_path.is_file() else rendered.get("bytes"),
        "seed": payloads["explicit_adult"]["seed"],
    }
    report["suggestive_baseline_image"] = {
        "path": str(SUGGESTIVE_BASELINE),
        "bytes": SUGGESTIVE_BASELINE.stat().st_size if SUGGESTIVE_BASELINE.is_file() else None,
    }

    if seed_variance:
        import companion_daemon.image_generation as image_generation

        first_seed = int(payloads["explicit_adult"]["seed"])
        second_seed = (first_seed + 7919) % 2**32
        original_seed = image_generation._civitai_template_seed

        def _forced_seed(prompt: str, *, width: int, height: int) -> int:
            del prompt, width, height
            return second_seed

        image_generation._civitai_template_seed = _forced_seed  # type: ignore[method-assign]
        variance_tracer = MaxPostsTracingTransport(inner=inner_transport, max_posts=1)
        specialized.transport = variance_tracer
        try:
            print("RENDER explicit_adult seed-variance (POST 2)", flush=True)
            variance_dir = generated_dir / "seed-variance"
            variance_dir.mkdir(parents=True, exist_ok=True)
            rendered_seed = await render_once(
                bundle=render_bundle,
                plan=prepared["explicit_adult"]["plan"],
                tracer=variance_tracer,
                generated_dir=variance_dir,
                label="explicit_adult_seed",
                prompt=hermes_rows[1]["prompt"],
            )
        finally:
            image_generation._civitai_template_seed = original_seed
        dump_json(
            OUTPUT / "civitai-http.json",
            [call.__dict__ for call in tracer.calls]
            + [call.__dict__ for call in variance_tracer.calls],
        )
        report["gates"]["render_explicit_adult_seed"] = rendered_seed
        report["civitai_cost_fields"] = extract_civitai_cost(tracer.calls + variance_tracer.calls)
        report["posts"] = tracer.post_count() + variance_tracer.post_count()
        if not rendered_seed.get("ok"):
            return _stop(
                report,
                cell="render_seed_variance",
                conclusion=f"不通：同 prompt 换 seed 失败 {rendered_seed.get('reason')}",
            )
        seed_path = Path(str(rendered_seed.get("path") or ""))
        seed_final = OUTPUT / "explicit_adult_seed.jpg"
        if seed_path.is_file():
            seed_final.write_bytes(seed_path.read_bytes())
        report["explicit_seed_image"] = {
            "path": str(seed_final if seed_final.is_file() else seed_path),
            "bytes": seed_final.stat().st_size if seed_final.is_file() else rendered_seed.get("bytes"),
            "seed": second_seed,
            "same_prompt": True,
        }

    report["conclusion"] = (
        "explicit_adult 已用 portrait_closeup 出图。请对照 "
        "output/adult-charge/sexual_suggestive.jpg，看这一档是否更明确；"
        "硬边界（无性行为、不露关键部位）仍在作者合同和最终 prompt 里。"
    )
    report["finished_at"] = datetime.now(UTC).isoformat()
    dump_json(OUTPUT / "report.json", report)
    return report


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    seed_variance = os.environ.get("FRAMING_SEED_VARIANCE") == "1"
    try:
        report = asyncio.run(run(seed_variance=seed_variance))
    except Exception as exc:
        report = {
            "contract": "prove-adult-framing.1",
            "conclusion": f"崩溃：{type(exc).__name__}: {exc}",
        }
        raise
    finally:
        if "report" in locals():
            dump_json(OUTPUT / "report.json", report)
    keys = (
        "conclusion",
        "stopped_at",
        "explicit_image",
        "explicit_seed_image",
        "suggestive_baseline_image",
        "civitai_cost_fields",
        "posts",
        "gates",
    )
    slim = {key: report.get(key) for key in keys}
    if isinstance(slim.get("gates"), dict):
        slim["gates"] = {
            name: slim["gates"].get(name)
            for name in ("visual_form_ranking", "payload_dry_run")
            if name in slim["gates"]
        }
    print(json.dumps(slim, default=str, ensure_ascii=False)[:6000], flush=True)


if __name__ == "__main__":
    main()
