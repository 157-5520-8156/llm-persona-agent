#!/usr/bin/env python3
"""Ordinary-lane identity consistency check: canonical-only vs prior multi-ref.

Does not touch production ledgers, scheduler, or QQ. Writes under
output/identity-consistency/. Budget target: four medium gpt-image-2 edits.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

from companion_daemon.config import Settings
from companion_daemon.event_media import MediaPlan, MediaRenderer, compile_media_prompt
from companion_daemon.image_generation import OpenAIImageGenerator
from companion_daemon.visual_identity import load_visual_identity

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "identity-consistency"
BEFORE_DIR = OUT / "before"
AFTER_DIR = OUT / "after"
CANONICAL = ROOT / "assets" / "reference" / "08-cafe-phone-canonical.png"

# Prior ordinary-lane shots that used refs=1 or refs=2 (for side-by-side review).
BEFORE_SOURCES = (
    ROOT / "output" / "first-photo" / "accepted" / "first-photo-captured.png",
    ROOT / "output" / "first-photo" / "accepted" / "she-selected-closer-background-1.png",
    ROOT / "output" / "first-real-photo-2" / "delivered" / "delivered-1.png",
    ROOT / "output" / "photo-visibility" / "captured-conv" / "captured-3.png",
)

SCENES = (
    {
        "id": "campus-morning-front",
        "label": "morning campus front selfie",
        "view_axis": "front",
        "hair": "natural_down",
        "place": "sunlit campus path with bike racks behind her",
        "action": "holding a paper coffee cup near her chest while looking into the phone",
        "time": "clear morning",
        "outfit": "oversized cream sweatshirt and tote strap",
    },
    {
        "id": "library-afternoon-three-quarter",
        "label": "afternoon library three-quarter",
        "view_axis": "left_three_quarter",
        "hair": "low_ponytail",
        "place": "quiet university library aisle between tall wooden shelves",
        "action": "pausing mid-browse with an open paperback in one hand",
        "time": "overcast afternoon window light",
        "outfit": "knit cardigan over a white tee",
    },
    {
        "id": "night-street-seated",
        "label": "night street seated check-in",
        "view_axis": "right_three_quarter",
        "hair": "hair_down_tucked",
        "place": "night sidewalk bench under cool street lamps with distant pedestrians",
        "action": "sitting forward with elbows on knees, phone raised for a casual check-in",
        "time": "late evening",
        "outfit": "denim jacket over dark layers",
    },
    {
        "id": "dorm-desk-evening",
        "label": "dorm desk evening selfie",
        "view_axis": "front",
        "hair": "loose_small_bun",
        "place": "small dorm desk with warm lamp, laptop edge, and stacked notes",
        "action": "leaning toward the camera after pausing homework",
        "time": "warm evening lamp light",
        "outfit": "soft ribbed long-sleeve top",
    },
)


class _PassInspector:
    async def inspect(self, path: Path, *, plan: MediaPlan, prompt: str, reference_images=()):
        from companion_daemon.event_media import MediaInspection

        del path, plan, prompt, reference_images
        return MediaInspection(
            passed=True,
            reason="identity_consistency_probe",
            observed_summary="probe",
            observed_facts=(),
            deviations=(),
            inspector_model="deterministic:identity-consistency",
            identity_consistency_ok=True,
        )


def _prompt_for(scene: dict[str, str]) -> str:
    identity = load_visual_identity(str(ROOT / "configs" / "visual_identity.yaml"))
    return (
        "Create one believable fictional personal-media photograph of 沈知栀 / Celia Shen. "
        "No text or watermark. Handheld front-camera phone photo, imperfect casual framing.\n"
        f"Scene: {scene['place']}; time of day: {scene['time']}.\n"
        f"Pose/action: {scene['action']}. Camera view axis: {scene['view_axis']}.\n"
        f"Shot-local hair arrangement: {scene['hair'].replace('_', ' ')}; outfit: {scene['outfit']}.\n"
        "Identity references define identity only: do not copy their wardrobe, scene, smile, "
        "head tilt, or framing. Keep her recognizably the same person as the identity reference.\n"
        + identity.prompt_block()
    )


def _desensitized_request(*, prompt: str, references: list[str], size: str, quality: str) -> dict:
    return {
        "method": "POST",
        "url": "https://api.openai.com/v1/images/edits",
        "headers": {"Authorization": "Bearer ***"},
        "data": {
            "model": "gpt-image-2",
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "output_format": "png",
        },
        "files": [
            {
                "field": "image[]",
                "filename": Path(path).name,
                "content_type": "image/png",
                "bytes_omitted": True,
            }
            for path in references
        ],
        "reference_count": len(references),
        "reference_paths": references,
    }


async def _generate_after() -> dict:
    settings = Settings()
    api_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        raise SystemExit("OPENAI_API_KEY missing")
    generator = OpenAIImageGenerator(
        api_key=api_key,
        proxy_url=getattr(settings, "openai_proxy_url", None) or os.environ.get("OPENAI_PROXY_URL"),
        model="gpt-image-2",
    )
    spend: list[dict] = []
    AFTER_DIR.mkdir(parents=True, exist_ok=True)
    requests: list[dict] = []
    for scene in SCENES:
        prompt = _prompt_for(scene)
        refs = (CANONICAL,)
        req = _desensitized_request(
            prompt=prompt,
            references=[str(path.relative_to(ROOT)) for path in refs],
            size="1024x1536",
            quality="medium",
        )
        requests.append({"scene_id": scene["id"], "request": req})
        out = AFTER_DIR / f"{scene['id']}.png"
        generated = await generator.generate(
            prompt,
            output_path=out,
            size="1024x1536",
            quality="medium",
            reference_images=refs,
        )
        spend.append(
            {
                "scene_id": scene["id"],
                "path": str(out.relative_to(ROOT)),
                "bytes": out.stat().st_size if out.is_file() else 0,
                "prompt_chars": len(prompt),
            }
        )
        del generated
    return {"requests": requests, "renders": spend}


def _stage_before() -> list[dict]:
    BEFORE_DIR.mkdir(parents=True, exist_ok=True)
    staged = []
    for index, src in enumerate(BEFORE_SOURCES, start=1):
        if not src.is_file():
            continue
        dest = BEFORE_DIR / f"before-{index}-{src.stem}.png"
        shutil.copy2(src, dest)
        staged.append({"source": str(src.relative_to(ROOT)), "staged": str(dest.relative_to(ROOT))})
    return staged


def _dump_live_plan_evidence() -> dict:
    """Capture real ledger evidence of prior refs=2 ordinary renders."""
    import sqlite3

    db = ROOT / "output" / "photo-visibility" / "conv.sqlite"
    if not db.is_file():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    plans = []
    for (body,) in con.execute(
        "select body from world_v2_media_payload where content_type like '%media-plan%'"
    ):
        plan = json.loads(body)
        plans.append(
            {
                "plan_id": plan.get("plan_id"),
                "identity_reference_selection": plan.get("identity_reference_selection"),
                "appearance": (plan.get("subject_presentation") or {}).get("appearance"),
                "view_axis": (plan.get("camera_geometry") or {}).get("view_axis"),
            }
        )
    usage = [
        dict(zip([c[1] for c in con.execute("pragma table_info(usage_events)")], row))
        for row in con.execute("select * from usage_events")
    ]
    appearance_hits = con.execute(
        "select count(*) from world_v2_events where event_json like '%AppearanceStateRecorded%'"
    ).fetchone()[0]
    con.close()
    sample_plan = MediaPlan.from_payload(
        json.loads(
            sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            .execute(
                "select body from world_v2_media_payload where content_type like '%media-plan%' limit 1"
            )
            .fetchone()[0]
        )
    )
    renderer = MediaRenderer(
        generator=None,  # type: ignore[arg-type]
        inspector=_PassInspector(),  # type: ignore[arg-type]
        output_dir=AFTER_DIR,
        visual_identity_path=ROOT / "configs" / "visual_identity.yaml",
    )
    provider_refs = [str(path) for path in renderer._references(sample_plan)]
    compiled = compile_media_prompt(sample_plan, ROOT / "configs" / "visual_identity.yaml")
    return {
        "prior_plans": plans,
        "prior_usage_events": usage,
        "appearance_state_recorded_events": appearance_hits,
        "post_fix_provider_refs_for_sample_plan": provider_refs,
        "sample_compiled_prompt_chars": len(compiled),
        "sample_compiled_prompt_head": compiled[:1200],
        "sample_desensitized_request_shape": _desensitized_request(
            prompt=compiled,
            references=provider_refs,
            size="1024x1536",
            quality="medium",
        ),
    }


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    before = _stage_before()
    evidence = _dump_live_plan_evidence()
    after = await _generate_after()
    report = {
        "verdict_inputs": {
            "canonical_asset": str(CANONICAL.relative_to(ROOT)),
            "fix": "ordinary OpenAI provider call now sends only identity_anchor",
            "before_images": before,
        },
        "ledger_evidence": evidence,
        "after_renders": after,
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "REQUEST_BODY_DESENSITIZED.json").write_text(
        json.dumps(
            {
                "prior_production_note": evidence.get("prior_usage_events"),
                "post_fix_sample": evidence.get("sample_desensitized_request_shape"),
                "generated_requests": after.get("requests"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "out": str(OUT), "after": after["renders"]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
