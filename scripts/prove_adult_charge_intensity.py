#!/usr/bin/env python3
"""Trace P3 intensity from declared_display to the Civitai prompt, then render.

Charge mapping is not reversed (veiled is the higher rank). The previous
clean lifestyle photo dropped frozen wardrobe because FirstPersonPrivatePromptAuthor
replaced the compiled prompt. This script prints every layer, then POSTs at
most two Civitai workflows (sexual_suggestive, then explicit_adult if the
first image is not still a clean lifestyle shot).

Never writes data/, never talks to 8787/NapCat. Artifacts go to
output/adult-charge/.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import quote

import httpx

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.prove_adult_p3_render_v2 import (  # noqa: E402
    CountingInspector,
    HttpCall,
    OnePostTracingTransport,
    PRODUCTION_DB,
    UsageCapture,
    _is_production_path,
    clone_ledger,
    dump_json,
    extract_civitai_cost,
    summarize_plan,
    unwrap,
)

OUTPUT = (REPO / "output" / "adult-charge").resolve()
AIRS = (
    "urn:air:krea2:lora:civitai:2750659@3094831",
    "urn:air:krea2:lora:civitai:2868686@3240992",
    "urn:air:krea2:lora:civitai:2781697@3132956",
)
POLL_SLEEP_SECONDS = 20
ABANDON_AFTER = timedelta(hours=4)
NSFW_NEGATIVE_TOKENS = (
    "nsfw",
    "nude",
    "nudity",
    "naked",
    "sex",
    "sexual",
    "erotic",
    "porn",
    "explicit",
)


def _stop(report: dict[str, Any], *, cell: str, conclusion: str) -> dict[str, Any]:
    report["stopped_at"] = cell
    report["conclusion"] = conclusion
    print(f"STOPPED at {cell}: {conclusion}", flush=True)
    return report


class FixedPromptAuthor:
    """Replay one already-authored high-private prompt; do not call Hermes again."""

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt

    async def write(self, plan: object) -> str:
        del plan
        return self.prompt


class LanePicker:
    """Pick one already-legal complete candidate for the declared lane."""

    def __init__(self, *, lane: str, attraction_expression: str, prefer_charge: str) -> None:
        self.lane = lane
        self.attraction_expression = attraction_expression
        self.prefer_charge = prefer_charge
        self.selected: dict[str, Any] | None = None
        self.matrix: dict[str, Any] | None = None

    async def complete_json(self, messages, *, temperature=0.8):  # type: ignore[no-untyped-def]
        return await self.complete(messages, temperature=temperature)

    async def complete(self, messages, *, temperature=0.8):  # type: ignore[no-untyped-def]
        del temperature
        content = str(messages[-1]["content"])
        encoded = content.split("legal_complete_media_expression_candidates=", 1)[1].split(
            "\n", 1
        )[0]
        candidates = json.loads(encoded)
        lanes: dict[str, int] = {}
        for item in candidates:
            for lane in item.get("legal_media_lanes", []) or []:
                lanes[str(lane)] = lanes.get(str(lane), 0) + 1
        self.matrix = {
            "n": len(candidates),
            "legal_lanes": sorted(lanes),
            "lane_counts": lanes,
        }
        matching = [
            item
            for item in candidates
            if self.lane in (item.get("legal_media_lanes") or [])
        ]
        if not matching:
            raise ValueError(f"no {self.lane} complete candidate; lanes={sorted(lanes)}")
        preferred = [
            item
            for item in matching
            if str((item.get("media_address_strategy") or {}).get("expression_charge") or "")
            == self.prefer_charge
        ]
        pool = preferred or matching
        with_bid = [
            item for item in pool if "invite_desire" in (item.get("legal_interaction_bids") or [])
        ]
        candidate = (with_bid or pool)[0]
        self.selected = candidate
        embodied = candidate.get("embodied_presentation") or {}
        address = candidate.get("media_address_strategy") or {}
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
                "media_lane": self.lane,
                "recipient_access": "recipient_exclusive",
                "attraction_expression": self.attraction_expression,
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


def template_layer() -> dict[str, Any]:
    template = json.loads(
        (REPO / "configs" / "civitai-krea2-celia-realism-template.json").read_text(
            encoding="utf-8"
        )
    )
    step = (template.get("steps") or [{}])[0]
    image_input = step.get("input") or {}
    negative = str(image_input.get("negativePrompt") or "")
    lowered = negative.casefold()
    return {
        "allowMatureContent": template.get("allowMatureContent"),
        "identity_air": "urn:air:krea2:lora:civitai:2868686@3240992",
        "patch_air": "urn:air:krea2:lora:civitai:2750659@3094831",
        "slider_air": "urn:air:krea2:lora:civitai:2781697@3132956",
        "lora_weights": image_input.get("loras"),
        "negative_prompt": negative,
        "negative_excludes_nsfw_tokens": [
            token for token in NSFW_NEGATIVE_TOKENS if token in lowered
        ],
    }


async def preflight_airs(settings: Any) -> dict[str, Any]:
    base = str(settings.civitai_base_url).rstrip("/")
    root = (
        base[: -len("consumer")] + "resources"
        if base.endswith("/consumer")
        else base.rsplit("/", 1)[0] + "/resources"
    )
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for air in AIRS:
            response = await client.get(
                f"{root}/{quote(air, safe='')}",
                headers={"Authorization": f"Bearer {settings.civitai_api_key}"},
            )
            body: Any
            try:
                body = response.json()
            except json.JSONDecodeError:
                body = response.text[:400]
            rows.append(
                {
                    "air": air,
                    "http": response.status_code,
                    "canGenerate": body.get("canGenerate") if isinstance(body, dict) else None,
                    "sfwOnly": body.get("sfwOnly") if isinstance(body, dict) else None,
                    "hasNSFWContentRestriction": (
                        body.get("hasNSFWContentRestriction") if isinstance(body, dict) else None
                    ),
                    "hasMatureContentRestriction": (
                        body.get("hasMatureContentRestriction") if isinstance(body, dict) else None
                    ),
                    "modelName": body.get("modelName") if isinstance(body, dict) else None,
                }
            )
    return {
        "resource_root": root,
        "all_http_200": all(row["http"] == 200 for row in rows),
        "rows": rows,
    }


def plan_layer(plan: Any, picker: LanePicker) -> dict[str, Any]:
    from companion_daemon.event_media import _frozen_high_private_render_facts

    address = getattr(plan, "media_address_strategy", None)
    embodied = getattr(plan, "embodied_presentation", None)
    facts = _frozen_high_private_render_facts(plan)
    selected = picker.selected or {}
    return {
        "plan": summarize_plan(plan),
        "visual_form": getattr(plan, "visual_form", None),
        "capture_mode": getattr(plan, "capture_mode", None),
        "expression_charge": None if address is None else address.expression_charge,
        "disclosure_mode": None if address is None else address.disclosure_mode,
        "coverage_mode": None if embodied is None else embodied.coverage_mode,
        "sensual_charge": None if embodied is None else embodied.sensual_charge,
        "wardrobe_evidence_refs": (
            None if embodied is None else list(embodied.wardrobe_evidence_refs)
        ),
        "shot_distance": (
            None if plan.camera_geometry is None else plan.camera_geometry.shot_distance
        ),
        "complete_candidate_id": selected.get("complete_candidate_id"),
        "matrix": picker.matrix,
        "frozen_prompt_facts": facts,
        "compiled_v5_contains_bathrobe": "bathrobe"
        in json.dumps(getattr(plan, "evidence_values", {}), ensure_ascii=False).casefold(),
    }


async def authorize_and_plan(
    *,
    intent: str,
    settings: Any,
    generated_dir: Path,
) -> dict[str, Any]:
    from companion_daemon.event_media import MediaPlanner
    from companion_daemon.media_eligibility import CHARGE_RANK, MediaEligibilityRouter
    from companion_daemon.world_v2.event_media_planner_adapter import EventMediaPlannerAdapter
    from companion_daemon.world_v2.media_opportunity_authorizer import MediaOpportunityAuthorizer
    from companion_daemon.world_v2.media_v2 import StoredMediaPayload, planning_request_id
    from companion_daemon.world_v2.qq_media_deployment import build_qq_media_preview_deployment
    from tests.world_v2.test_adult_media_authorization import _authorize, _p3_world

    lane, charge = MediaOpportunityAuthorizer._p3_lane_for_stage(
        "close_friend", adult_eligible=True, declared_intent=intent
    )
    ledger, candidate = _p3_world(
        stage="close_friend", with_adult_grants=True, display_intent=intent
    )
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    dumped = compiled.snapshot.image_event_snapshot.model_dump(mode="json")
    appearance = (dumped.get("character") or {}).get("appearance_state") or {}
    display = compiled.snapshot.image_event_snapshot.relationship_media_context.declared_display
    authorization = compiled.snapshot.private_media_authorization
    world_id = f"world:companion-v2:qq-c2c:{settings.primary_user_id}"
    bundle = build_qq_media_preview_deployment(
        settings=settings, world_id=world_id, output_dir=generated_dir
    )
    if bundle is None:
        raise RuntimeError("production factory did not install the media lane")
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
    picker = LanePicker(
        lane=lane,
        attraction_expression=intent,
        prefer_charge=charge,
    )
    adapter._legacy_planner = MediaPlanner(picker, enabled=True, v5_enabled=True)
    result = await adapter.plan(
        opportunity=opportunity,
        planning_request_id=planning_request_id(opportunity.opportunity_id),
    )
    if result.plan is None or result.plan_payload is None:
        reason = None if result.not_renderable is None else result.not_renderable.reason
        raise RuntimeError(f"plan failed: {reason}")
    from companion_daemon.event_media import MediaPlan as LegacyMediaPlan

    plan = LegacyMediaPlan.from_payload(json.loads(result.plan_payload.body))
    eligibility = MediaEligibilityRouter().classify_recommendation(
        family="character_media",
        privacy_ceiling="intimate",
        expression_charge_ceiling=authorization.expression_charge_ceiling,
        event_snapshot=dumped,
        private_expression_basis=EventMediaPlannerAdapter._p3_basis(dumped),
        recipient_ref="user:1",
        recommendation=plan.media_lane,
        selected_expression_charge=(
            "none" if plan.media_address_strategy is None else plan.media_address_strategy.expression_charge
        ),
        selected_capture_mode=plan.capture_mode,
        selected_share_intent=plan.share_intent,
        selected_privacy=plan.privacy,
        selected_address_mode=(
            "" if plan.media_address_strategy is None else plan.media_address_strategy.address_mode
        ),
        selected_interaction_bid=(
            "" if plan.interaction_bid is None else plan.interaction_bid.communicative_goal
        ),
        selected_attraction_mechanism=(
            None if plan.media_address_strategy is None else plan.media_address_strategy.attraction_mechanism
        ),
        selected_coverage_mode=(
            None if plan.embodied_presentation is None else plan.embodied_presentation.coverage_mode
        ),
    )
    return {
        "intent": intent,
        "bundle": bundle,
        "plan": plan,
        "picker": picker,
        "layers": {
            "declared_display": None if display is None else display.media_intent,
            "authorizer": {
                "media_lane": opportunity.media_lane,
                "authorized_charge": authorization.expression_charge_ceiling,
                "relationship_stage": "close_friend",
                "mapped_lane": lane,
                "mapped_charge": charge,
                "charge_rank": CHARGE_RANK[charge],
            },
            "snapshot_appearance": {
                "outfit": (appearance.get("visible_attributes") or [{}])[0].get("description"),
                "coverage": (appearance.get("visible_attributes") or [{}, {}])[1].get(
                    "description"
                ),
                "location_kind": (dumped.get("location") or {}).get("kind"),
            },
            "eligibility": {
                "allowed": eligibility.allowed,
                "lane": eligibility.lane,
                "reason": eligibility.reason,
                "required_charge": eligibility.required_charge,
            },
            "planner_opportunity_ceilings": {
                "expression_charge_ceiling": authorization.expression_charge_ceiling,
                "sensual_charge_ceiling": authorization.expression_charge_ceiling,
            },
            "plan": plan_layer(plan, picker),
            "template": template_layer(),
        },
    }


async def write_hermes_prompt(bundle: Any, plan: Any, dest: Path) -> dict[str, Any]:
    from companion_daemon.event_media import FirstPersonPrivatePromptAuthor

    renderer = bundle.transport._renderer
    author = renderer.private_prompt_author
    if not isinstance(author, FirstPersonPrivatePromptAuthor):
        raise RuntimeError(f"unexpected author {type(author).__name__}")
    usage = UsageCapture()
    inner = unwrap(author.model) if author.model is not None else None
    previous = getattr(inner, "usage_observer", None) if inner else None
    started = time.perf_counter()

    def _observe(row: object) -> None:
        usage(row)
        if callable(previous):
            previous(row)

    if inner is not None:
        inner.usage_observer = _observe
    prompt = await author.write(plan)
    dest.write_text(prompt, encoding="utf-8")
    return {
        "path": str(dest),
        "len": len(prompt),
        "contains_bathrobe": "bathrobe" in prompt.casefold(),
        "contains_sexual_suggestive": "sexual_suggestive" in prompt,
        "contains_explicit_adult": "explicit_adult" in prompt,
        "contains_ordinary_daytime": "ordinary daytime" in prompt.casefold(),
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
    posts = tracer.post_count()
    cell: dict[str, Any] = {
        "label": label,
        "elapsed_ms": elapsed,
        "posts": posts,
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


async def run() -> dict[str, Any]:
    from companion_daemon.config import Settings
    from companion_daemon.db import CompanionStore
    from companion_daemon.image_generation import (
        CivitaiTemplateWorkflowImageGenerator,
        bind_image_spend_store,
    )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    generated_dir = OUTPUT / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "contract": "prove-adult-charge-intensity.1",
        "started_at": datetime.now(UTC).isoformat(),
        "clone": str(clone),
        "production_source": str(PRODUCTION_DB),
        "max_civitai_posts": 1,
        "gates": {},
    }
    if _is_production_path(clone):
        return _stop(report, cell="clone", conclusion="拒绝写 data/")
    clone_ledger(PRODUCTION_DB, clone)
    os.environ["DATABASE_PATH"] = str(clone)
    settings = Settings(database_path=clone)
    bind_image_spend_store(CompanionStore(clone))
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
    suggestive_facts = layers[0]["plan"]["frozen_prompt_facts"]
    explicit_facts = layers[1]["plan"]["frozen_prompt_facts"]
    report["gates"]["tier_difference"] = {
        "suggestive_lane": layers[0]["authorizer"]["media_lane"],
        "suggestive_charge": layers[0]["authorizer"]["authorized_charge"],
        "explicit_lane": layers[1]["authorizer"]["media_lane"],
        "explicit_charge": layers[1]["authorizer"]["authorized_charge"],
        "charge_ranks_differ": (
            layers[0]["authorizer"]["charge_rank"] < layers[1]["authorizer"]["charge_rank"]
        ),
        "prompt_facts_differ": suggestive_facts != explicit_facts,
        "suggestive_intent_contract": suggestive_facts.get("declared_intent"),
        "explicit_intent_contract": explicit_facts.get("declared_intent"),
        "suggestive_disclosure": layers[0]["plan"]["disclosure_mode"],
        "explicit_disclosure": layers[1]["plan"]["disclosure_mode"],
        "suggestive_wardrobe": suggestive_facts.get("wardrobe"),
        "explicit_wardrobe": explicit_facts.get("wardrobe"),
    }
    if not report["gates"]["tier_difference"]["charge_ranks_differ"]:
        return _stop(report, cell="layers", conclusion="不通：两档 charge 没有拉开")

    hermes_rows = []
    for intent in ("sexual_suggestive", "explicit_adult"):
        item = prepared[intent]
        hermes = await write_hermes_prompt(
            item["bundle"], item["plan"], OUTPUT / f"hermes-{intent}.txt"
        )
        hermes_rows.append({"intent": intent, **hermes})
    dump_json(OUTPUT / "hermes-prompts.json", hermes_rows)
    report["gates"]["hermes"] = [
        {key: row[key] for key in row if key != "prompt"} for row in hermes_rows
    ]
    if hermes_rows[0]["prompt"] == hermes_rows[1]["prompt"]:
        return _stop(report, cell="hermes", conclusion="不通：两档 Hermes 提示词没有不同")
    if not hermes_rows[0]["contains_bathrobe"]:
        return _stop(
            report,
            cell="hermes",
            conclusion="不通：sexual_suggestive 提示词仍然没有带上 bathrobe",
        )
    if not (
        hermes_rows[0]["contains_sexual_suggestive"]
        and hermes_rows[1]["contains_explicit_adult"]
    ):
        return _stop(
            report,
            cell="hermes",
            conclusion="不通：两档提示词没有分别带上 sexual_suggestive / explicit_adult",
        )

    world_id = f"world:companion-v2:qq-c2c:{settings.primary_user_id}"
    from companion_daemon.world_v2.qq_media_deployment import build_qq_media_preview_deployment

    render_bundle = build_qq_media_preview_deployment(
        settings=settings, world_id=world_id, output_dir=generated_dir
    )
    if render_bundle is None:
        return _stop(report, cell="factory", conclusion="不通：生产工厂没装上媒体车道")
    renderer = render_bundle.transport._renderer
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

    print("RENDER sexual_suggestive (first POST)", flush=True)
    first = await render_once(
        bundle=render_bundle,
        plan=prepared["sexual_suggestive"]["plan"],
        tracer=tracer,
        generated_dir=generated_dir,
        label="sexual_suggestive",
        prompt=hermes_rows[0]["prompt"],
    )
    report["gates"]["render_sexual_suggestive"] = first
    dump_json(OUTPUT / "civitai-http.json", [call.__dict__ for call in tracer.calls])
    report["civitai_cost_fields"] = extract_civitai_cost(tracer.calls)
    if not first.get("ok"):
        return _stop(
            report,
            cell="render_sexual_suggestive",
            conclusion=f"不通：sexual_suggestive 渲染失败 {first.get('reason')}",
        )
    report["first_image"] = {"path": first.get("path"), "bytes": first.get("bytes")}
    report["conclusion"] = (
        "sexual_suggestive 已出图。请看图是否含成人内容；"
        "若仍是干净生活照，不要继续烧 buzz。explicit_adult 尚未 POST。"
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
            "contract": "prove-adult-charge-intensity.1",
            "conclusion": f"崩溃：{type(exc).__name__}: {exc}",
        }
        raise
    finally:
        if "report" in locals():
            dump_json(OUTPUT / "report.json", report)
    print(json.dumps({k: report.get(k) for k in ("conclusion", "stopped_at", "first_image", "gates")}, default=str)[:4000], flush=True)


if __name__ == "__main__":
    main()
