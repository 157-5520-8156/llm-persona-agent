#!/usr/bin/env python3
"""Prove explicit_adult is no longer cropped to shoulders-only.

Dry-runs both Civitai payloads (no POST), then submits at most one
explicit_adult workflow. Never writes data/, never talks to 8787/NapCat.
Artifacts go to output/adult-regions/.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.prove_adult_charge_intensity import (  # noqa: E402
    CountingInspector,
    FixedPromptAuthor,
    OnePostTracingTransport,
    PRODUCTION_DB,
    UsageCapture,
    _is_production_path,
    authorize_and_plan,
    clone_ledger,
    dump_json,
    extract_civitai_cost,
    preflight_airs,
    template_layer,
    unwrap,
)

OUTPUT = (REPO / "output" / "adult-regions").resolve()
SUGGESTIVE_BASELINE = (REPO / "output" / "adult-charge" / "sexual_suggestive.jpg").resolve()
POLL_SLEEP_SECONDS = 20
ABANDON_AFTER = timedelta(hours=4)
HARD_BOUNDARY_PHRASES = (
    "no sexual act and no key-area exposure",
    "Still no sexual act and no key-area exposure",
    "still no sexual act and no key-area exposure",
)
SHOULDER_ONLY = "face, hair, neck, shoulders"


def _stop(report: dict[str, Any], *, cell: str, conclusion: str) -> dict[str, Any]:
    report["stopped_at"] = cell
    report["conclusion"] = conclusion
    print(f"STOPPED at {cell}: {conclusion}", flush=True)
    return report


def _image_input(template: dict[str, Any]) -> dict[str, Any]:
    step = (template.get("steps") or [{}])[0]
    input_payload = step.get("input")
    if not isinstance(input_payload, dict):
        raise RuntimeError("civitai template missing imageGen input")
    return input_payload


def civitai_payload_for(*, prompt: str, size: str, template: dict[str, Any]) -> dict[str, Any]:
    from companion_daemon.image_generation import _civitai_dimensions, _civitai_template_seed

    width, height = _civitai_dimensions(size)
    image_input = _image_input(template)
    return {
        "allowMatureContent": template.get("allowMatureContent"),
        "engine": image_input.get("engine"),
        "ecosystem": image_input.get("ecosystem"),
        "model": image_input.get("model"),
        "operation": image_input.get("operation"),
        "prompt": prompt,
        "negativePrompt": image_input.get("negativePrompt"),
        "width": width,
        "height": height,
        "steps": image_input.get("steps"),
        "cfgScale": image_input.get("cfgScale"),
        "sampler": image_input.get("sampler"),
        "scheduler": image_input.get("scheduler"),
        "seed": _civitai_template_seed(prompt, width=width, height=height),
        "quantity": image_input.get("quantity"),
        "loras": deepcopy(image_input.get("loras") or {}),
    }


def payload_text_block(label: str, payload: dict[str, Any]) -> str:
    loras = payload.get("loras") or {}
    lora_lines = "\n".join(f"  {air}: {weight}" for air, weight in loras.items())
    return "\n".join(
        (
            f"## {label}",
            f"allowMatureContent: {payload.get('allowMatureContent')}",
            f"engine/model: {payload.get('engine')} / {payload.get('ecosystem')} {payload.get('model')}",
            f"size: {payload.get('width')}x{payload.get('height')}",
            f"steps: {payload.get('steps')}",
            f"cfgScale: {payload.get('cfgScale')}",
            f"sampler: {payload.get('sampler')}",
            f"scheduler: {payload.get('scheduler')}",
            f"seed: {payload.get('seed')}",
            "loras:",
            lora_lines,
            "",
            "negativePrompt:",
            str(payload.get("negativePrompt") or ""),
            "",
            "prompt:",
            str(payload.get("prompt") or ""),
            "",
        )
    )


def classify_constraint(item: dict[str, Any]) -> dict[str, Any]:
    return item


CONSTRAINT_ROWS = (
    {
        "constraint": "allowed_regions catalog list",
        "where": "configs/media_embodiment_templates.yaml + EmbodiedPresentation",
        "before": "catalog often has arms/torso/waist/legs; prompt copied only first 4",
        "after": "prompt copies the full catalog list",
        "class": "extra_gate_removed",
        "note": "shoulder-only crop was stricter than no-key-area-exposure",
    },
    {
        "constraint": "allowed_regions[:4] truncation",
        "where": "event_media._frozen_high_private_render_facts",
        "before": "face, hair, neck, shoulders",
        "after": "removed; all catalog regions are copied",
        "class": "extra_gate_removed",
        "note": "this was the extra checkpoint owner asked not to keep",
    },
    {
        "constraint": "coverage_mode whitelist private_apparel / strategic_cover",
        "where": "media_eligibility.classify_recommendation",
        "before": "high private lanes require opaque private covering",
        "after": "unchanged",
        "class": "hard_boundary_equivalent",
        "note": "opaque covering is how 'no key-area exposure' is enforced photographically",
    },
    {
        "constraint": "explicit_reserved never renderable",
        "where": "media_eligibility + media_suggestive_lane",
        "before": "always NotRenderable",
        "after": "unchanged",
        "class": "hard_boundary_equivalent",
        "note": "that reserved lane is the capability beyond covering; owner boundary forbids it",
    },
    {
        "constraint": "disclosure_mode selective_focus vs partial_reveal",
        "where": "media_expression recipes keyed by charge",
        "before": "charged→selective_focus; veiled→partial_reveal",
        "after": "unchanged; this is intensity transmission, not a crop gate",
        "class": "intensity_transmission",
        "note": "explicit already gets the more revealing disclosure; host does not re-choose it",
    },
    {
        "constraint": "visual_form portrait_closeup vs portrait_context",
        "where": "media_expression._preferred_forms(attraction) / planner candidate",
        "before": "suggestive often closeup; explicit often portrait_context",
        "after": "unchanged (file not owned); prompt now forbids using the wider frame as a modest full-robe portrait",
        "class": "backfire_addressed_in_prompt",
        "note": "wider frame + shoulder-only regions produced a whole bathrobe; full regions + no-modest-wrap copy keep the wider frame inside the owner boundary",
    },
    {
        "constraint": "FirstPersonPrivatePromptAuthor hard boundary copy",
        "where": "event_media.FirstPersonPrivatePromptAuthor system/user + intent contract",
        "before": "no sexual act or key-area exposure on both tiers",
        "after": "kept; explicit also forbids face-and-shoulders crop and modest full-wrap",
        "class": "hard_boundary_kept",
        "note": "owner hard boundary; the added sentences remove extra crops, they do not add a new checkpoint",
    },
    {
        "constraint": "_compile_krea2_private_prompt two-tier copy",
        "where": "event_media._compile_krea2_private_prompt",
        "before": "explicit stronger semantically but still listed only truncated regions",
        "after": "full regions; explicit includes non-key body; both keep no sexual act / no key-area exposure",
        "class": "extra_gate_removed",
        "note": "short-prompt experiment path kept aligned with the production Hermes author",
    },
    {
        "constraint": "template negativePrompt",
        "where": "configs/civitai-krea2-celia-realism-template.json",
        "before": "child/minor/teen/loli + anatomy/quality; no nsfw tokens",
        "after": "unchanged",
        "class": "legal_safety_kept",
        "note": "under-18 tokens are illegal-content prevention, not an extra adult checkpoint",
    },
    {
        "constraint": "heightened_ecstasy face-first suffix",
        "where": "configs/media_suggestive_private_templates.yaml",
        "before": "tight shoulder-up only when that facial profile is selected",
        "after": "unchanged (catalog file not owned); default prove path uses natural_private",
        "class": "necessary_for_that_facial_profile",
        "note": "that expression is unreadable without a face-first crop; it is not a general explicit_adult gate",
    },
)


async def write_hermes_prompt(bundle: Any, plan: Any, dest: Path) -> dict[str, Any]:
    from companion_daemon.event_media import (
        FirstPersonPrivatePromptAuthor,
        _first_person_declared_intent_contract,
        _frozen_high_private_render_facts,
    )

    renderer = bundle.transport._renderer
    author = renderer.private_prompt_author
    if not isinstance(author, FirstPersonPrivatePromptAuthor):
        raise RuntimeError(f"unexpected author {type(author).__name__}")
    usage = UsageCapture()
    inner = unwrap(author.model) if author.model is not None else None
    previous = getattr(inner, "usage_observer", None) if inner else None
    started = time.perf_counter()
    captured: dict[str, str] = {}

    def _observe(row: object) -> None:
        usage(row)
        if callable(previous):
            previous(row)

    class RecordingModel:
        async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            captured["system"] = str(messages[0]["content"])
            captured["user"] = str(messages[-1]["content"])
            return await inner.complete(messages, **kwargs)

    if inner is not None:
        inner.usage_observer = _observe
        author.model = RecordingModel()  # type: ignore[assignment]
    prompt = await author.write(plan)
    dest.write_text(prompt, encoding="utf-8")
    facts = _frozen_high_private_render_facts(plan)
    return {
        "path": str(dest),
        "len": len(prompt),
        "contains_bathrobe": "bathrobe" in prompt.casefold(),
        "contains_sexual_suggestive": "sexual_suggestive" in prompt,
        "contains_explicit_adult": "explicit_adult" in prompt,
        "contains_ordinary_daytime": "ordinary daytime" in prompt.casefold(),
        "contains_face_and_shoulders_instruction": "face-and-shoulders" in captured.get("system", ""),
        "contains_hard_boundary": any(
            phrase in captured.get("system", "") or phrase in prompt for phrase in HARD_BOUNDARY_PHRASES
        ),
        "intent_contract": _first_person_declared_intent_contract(facts),
        "frozen_facts": facts,
        "author_system": captured.get("system", ""),
        "author_user": captured.get("user", ""),
        "usage": usage.rows,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "excerpt": prompt[:400],
        "prompt": prompt,
    }


async def render_once(
    *,
    bundle: Any,
    plan: Any,
    tracer: Any,
    generated_dir: Path,
    label: str,
    prompt: str,
) -> dict[str, Any]:
    from companion_daemon.event_media import MediaRenderFailure, RenderedMedia, _safe_filename
    from companion_daemon.image_generation import ImageGenerationProviderError

    renderer = bundle.transport._renderer
    specialized_wrapped = renderer.specialized_generators.get("adult_suggestive")
    inspector = CountingInspector(renderer.inspector)
    renderer.inspector = inspector
    renderer.private_prompt_author = FixedPromptAuthor(prompt)
    output_path = generated_dir / f"{_safe_filename(plan.plan_id)}.png"
    size = renderer._render_size(plan)
    started = time.perf_counter()
    first = await renderer.render(plan)
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    cell: dict[str, Any] = {
        "label": label,
        "elapsed_ms": elapsed,
        "posts": tracer.post_count(),
        "prompt_len": len(prompt),
        "output_path": str(output_path),
        "openai_inspect_called": bool(inspector.calls),
    }
    if isinstance(first, RenderedMedia):
        cell["ok"] = True
        cell["bytes"] = first.path.stat().st_size
        cell["path"] = str(first.path)
        cell["inspection"] = first.inspection.reason
        return cell
    if not isinstance(first, MediaRenderFailure):
        cell["ok"] = False
        cell["type"] = type(first).__name__
        return cell
    cell["reason"] = first.reason
    if not str(first.reason).startswith("image_provider_pending"):
        cell["ok"] = False
        return cell
    deadline = datetime.now(UTC) + ABANDON_AFTER
    polls: list[dict[str, Any]] = []
    while datetime.now(UTC) < deadline:
        await asyncio.sleep(POLL_SLEEP_SECONDS)
        sample = {
            "t": datetime.now(UTC).isoformat(),
            "posts": tracer.post_count(),
            "gets": tracer.get_count(),
        }
        polls.append(sample)
        dump_json(OUTPUT / f"render-poll-{label}.json", polls)
        print(json.dumps(sample, ensure_ascii=False), flush=True)
        if tracer.post_count() > 1:
            cell["ok"] = False
            cell["reason"] = "second_post_during_poll"
            return cell
        try:
            generated = await specialized_wrapped.generate(
                prompt, output_path=output_path, size=size
            )
        except ImageGenerationProviderError as exc:
            if exc.kind == "pending":
                continue
            cell["ok"] = False
            cell["reason"] = f"{exc.kind}:{exc.detail}"
            return cell
        cell["ok"] = True
        cell["bytes"] = generated.path.stat().st_size
        cell["path"] = str(generated.path)
        cell["polls"] = len(polls)
        return cell
    cell["ok"] = False
    cell["reason"] = "abandon_after_elapsed"
    return cell


def region_audit(facts: dict[str, str], embodied_regions: list[str]) -> dict[str, Any]:
    listed = [
        part.strip()
        for part in str(facts.get("allowed_regions") or "").split(",")
        if part.strip()
    ]
    return {
        "catalog_regions": embodied_regions,
        "prompt_regions": listed,
        "copies_full_catalog": listed == embodied_regions,
        "shoulder_only": ", ".join(listed) == SHOULDER_ONLY,
        "includes_non_key_body": bool(
            set(listed).intersection({"arms", "torso", "waist", "legs", "hands"})
        ),
    }


async def run() -> dict[str, Any]:
    from companion_daemon.config import Settings
    from companion_daemon.db import CompanionStore
    from companion_daemon.event_media import _compile_krea2_private_prompt
    from companion_daemon.image_generation import (
        CivitaiTemplateWorkflowImageGenerator,
        bind_image_spend_store,
    )
    from companion_daemon.world_v2.qq_media_deployment import build_qq_media_preview_deployment

    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    generated_dir = OUTPUT / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "contract": "prove-adult-regions.1",
        "started_at": datetime.now(UTC).isoformat(),
        "clone": str(clone),
        "production_source": str(PRODUCTION_DB),
        "max_civitai_posts": 1,
        "suggestive_baseline": str(SUGGESTIVE_BASELINE),
        "constraint_inventory": [classify_constraint(dict(row)) for row in CONSTRAINT_ROWS],
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

    region_rows = {}
    for intent in ("sexual_suggestive", "explicit_adult"):
        plan = prepared[intent]["plan"]
        embodied = plan.embodied_presentation
        catalog = list(embodied.allowed_regions) if embodied is not None else []
        facts = layers[0 if intent == "sexual_suggestive" else 1]["plan"]["frozen_prompt_facts"]
        region_rows[intent] = {
            "visual_form": plan.visual_form,
            "shot_distance": None if plan.camera_geometry is None else plan.camera_geometry.shot_distance,
            "disclosure": None
            if plan.media_address_strategy is None
            else plan.media_address_strategy.disclosure_mode,
            "coverage": None if embodied is None else embodied.coverage_mode,
            **region_audit(facts, catalog),
            "krea2_short_prompt": _compile_krea2_private_prompt(plan),
        }
    dump_json(OUTPUT / "region-audit.json", region_rows)
    report["gates"]["region_audit"] = region_rows
    explicit_audit = region_rows["explicit_adult"]
    backfire = {
        "suggestive_visual_form": region_rows["sexual_suggestive"]["visual_form"],
        "explicit_visual_form": region_rows["explicit_adult"]["visual_form"],
        "suggestive_shot_distance": region_rows["sexual_suggestive"]["shot_distance"],
        "explicit_shot_distance": region_rows["explicit_adult"]["shot_distance"],
        "hypothesis": (
            "portrait_context + shoulder-only regions makes a whole bathrobe; "
            "portrait_closeup crops to a more charged collar/shoulder."
        ),
        "confirmed_form_split": (
            region_rows["sexual_suggestive"]["visual_form"] == "portrait_closeup"
            and region_rows["explicit_adult"]["visual_form"] == "portrait_context"
        ),
        "explicit_no_longer_shoulder_only": not explicit_audit["shoulder_only"],
        "explicit_includes_non_key_body": explicit_audit["includes_non_key_body"],
    }
    report["gates"]["visual_form_backfire"] = backfire
    if explicit_audit["shoulder_only"] or not explicit_audit["includes_non_key_body"]:
        return _stop(
            report,
            cell="regions",
            conclusion="不通：explicit_adult 提示词事实仍是肩以上，未 POST",
        )
    if not explicit_audit["copies_full_catalog"]:
        return _stop(
            report,
            cell="regions",
            conclusion="不通：explicit_adult 仍没有原样复制目录 allowed_regions，未 POST",
        )

    hermes_rows = []
    for intent in ("sexual_suggestive", "explicit_adult"):
        item = prepared[intent]
        hermes = await write_hermes_prompt(
            item["bundle"], item["plan"], OUTPUT / f"hermes-{intent}.txt"
        )
        (OUTPUT / f"author-system-{intent}.txt").write_text(hermes["author_system"], encoding="utf-8")
        (OUTPUT / f"author-user-{intent}.txt").write_text(hermes["author_user"], encoding="utf-8")
        hermes_rows.append({"intent": intent, **hermes})
    dump_json(
        OUTPUT / "hermes-prompts.json",
        [{key: row[key] for key in row if key != "prompt"} for row in hermes_rows],
    )
    report["gates"]["hermes"] = [
        {
            key: row[key]
            for key in row
            if key not in {"prompt", "author_system", "author_user"}
        }
        for row in hermes_rows
    ]
    if not hermes_rows[1]["contains_hard_boundary"]:
        return _stop(
            report,
            cell="hermes",
            conclusion="不通：explicit_adult 作者合同丢掉了无性行为/不露关键部位，未 POST",
        )
    if "face-and-shoulders" not in hermes_rows[1]["author_system"]:
        return _stop(
            report,
            cell="hermes",
            conclusion="不通：explicit_adult 仍没有禁止肩以上裁切，未 POST",
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
            "prompt_has_torso_or_waist_or_legs": any(
                token in str(payload["prompt"]).casefold()
                for token in ("torso", "waist", "legs", "arms")
            ),
            "prompt_has_hard_boundary_or_frozen_regions": (
                "key-area" in str(payload["prompt"]).casefold()
                or "naturally_visible_regions=" in str(payload["prompt"])
            ),
        }
        for intent, payload in payloads.items()
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
    tracer = OnePostTracingTransport(inner=inner_transport)
    specialized.transport = tracer

    print("RENDER explicit_adult (only POST)", flush=True)
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
    if tracer.post_count() > 1:
        return _stop(report, cell="render", conclusion="不通：发生第二次 Civitai POST")
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
    }
    report["suggestive_baseline_image"] = {
        "path": str(SUGGESTIVE_BASELINE),
        "bytes": SUGGESTIVE_BASELINE.stat().st_size if SUGGESTIVE_BASELINE.is_file() else None,
    }
    report["conclusion"] = (
        "explicit_adult 已出图。请对照 output/adult-charge/sexual_suggestive.jpg，"
        "看这一档是否更明确；硬边界（无性行为、不露关键部位）仍在作者合同和最终 prompt 里。"
    )
    report["finished_at"] = datetime.now(UTC).isoformat()
    dump_json(OUTPUT / "report.json", report)
    return report


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        report = asyncio.run(run())
    except Exception as exc:
        report = {
            "contract": "prove-adult-regions.1",
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
        "suggestive_baseline_image",
        "civitai_cost_fields",
        "posts",
    )
    print(json.dumps({key: report.get(key) for key in keys}, default=str, ensure_ascii=False)[:4000], flush=True)


if __name__ == "__main__":
    main()
