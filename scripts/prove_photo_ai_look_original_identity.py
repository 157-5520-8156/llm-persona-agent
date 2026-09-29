#!/usr/bin/env python3
"""Original-identity-only photo realism comparison.

Uses the real MediaPlanner v5 -> compile_media_prompt -> MediaRenderer ->
OpenAIImageGenerator path. Provider references are either the untouched July 15
canonical image or an exact, non-resampled crop of that same image.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for source_root in (REPO / "src", REPO / "tests", REPO / "scripts"):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

OUT = REPO / "output" / "photo-ai-look" / "original-identity"
SPEND_DB = OUT / "debug-spend.sqlite"
VISUAL_IDENTITY = REPO / "configs" / "visual_identity.yaml"
ORIGINAL = REPO / "assets" / "reference" / "08-cafe-phone-canonical.png"
CROP = REPO / "assets" / "reference" / "08-cafe-phone-canonical-identity-crop.png"
DELIVERED = REPO / "output" / "media-delivered" / "00d2e812a1f5198b82744ab8.png"
CROP_BOX = (330, 170, 800, 1080)
BUDGET_CAP_CNY = 4.0


class _PassInspector:
    async def inspect(self, path: Path, *, plan, prompt: str, reference_images=()):
        from test_event_media import _inspection

        del path, plan, prompt, reference_images
        return _inspection(True, reason="original_identity_human_review")


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPO))


def _verify_crop() -> dict:
    from PIL import Image, ImageChops

    with Image.open(ORIGINAL) as source:
        expected = source.crop(CROP_BOX).convert("RGBA")
    with Image.open(CROP) as candidate:
        actual = candidate.convert("RGBA")
    if expected.size != actual.size or ImageChops.difference(expected, actual).getbbox() is not None:
        raise SystemExit("identity crop is not an exact source-pixel crop")
    return {
        "source": _relative(ORIGINAL),
        "crop": _relative(CROP),
        "crop_box": list(CROP_BOX),
        "source_size": list(Image.open(ORIGINAL).size),
        "crop_size": list(actual.size),
        "resampled": False,
        "pixel_difference": None,
    }


def _request(*, prompt: str, references: tuple[Path, ...]) -> dict:
    return {
        "method": "POST",
        "url": "https://api.openai.com/v1/images/edits",
        "headers": {"Authorization": "Bearer ***"},
        "data": {
            "model": "gpt-image-2",
            "prompt": prompt,
            "size": "1024x1536",
            "quality": "medium",
            "output_format": "png",
        },
        "files": [
            {
                "field": "image[]",
                "filename": path.name,
                "content_type": "image/png",
                "bytes_omitted": True,
            }
            for path in references
        ],
        "reference_count": len(references),
        "reference_paths": [_relative(path) for path in references],
    }


def _spend_total() -> float:
    if not SPEND_DB.is_file():
        return 0.0
    con = sqlite3.connect(f"file:{SPEND_DB}?mode=ro", uri=True)
    total = float(
        con.execute("select coalesce(sum(estimated_cny), 0) from usage_events").fetchone()[0]
    )
    con.close()
    return total


def _record_spend(note: str) -> float:
    from companion_daemon.budget import image_render_estimate
    from companion_daemon.db import CompanionStore

    estimate = image_render_estimate(
        reference_count=1, size="1024x1536", quality="medium", attempts=1
    )
    if _spend_total() + estimate.cny > BUDGET_CAP_CNY:
        raise SystemExit(f"debug image budget would exceed ¥{BUDGET_CAP_CNY:.2f}")
    CompanionStore(SPEND_DB).record_usage("image_generation", estimate.cny, note=note)
    return estimate.cny


def _generator():
    from companion_daemon.config import Settings
    from companion_daemon.image_generation import OpenAIImageGenerator

    settings = Settings()
    api_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        raise SystemExit("OPENAI_API_KEY missing")
    return OpenAIImageGenerator(
        api_key=api_key,
        proxy_url=getattr(settings, "openai_proxy_url", None)
        or os.environ.get("OPENAI_PROXY_URL"),
        model="gpt-image-2",
        spend_store=None,
    )


def _write_contact_sheet(paths: list[Path], labels: list[str]) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    thumb_w, thumb_h, label_h = 320, 480, 28
    cols = 4
    rows = (len(paths) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (path, label) in enumerate(zip(paths, labels, strict=True)):
        image = Image.open(path).convert("RGB")
        image.thumbnail((thumb_w, thumb_h))
        cell_x = (index % cols) * thumb_w
        cell_y = (index // cols) * (thumb_h + label_h)
        x = cell_x + (thumb_w - image.width) // 2
        y = cell_y + label_h + (thumb_h - image.height) // 2
        sheet.paste(image, (x, y))
        draw.text((cell_x + 5, cell_y + 7), label[:50], fill=(235, 235, 235), font=font)
    destination = OUT / "contact-sheet-08-identity-first.png"
    sheet.save(destination)
    return destination


async def main() -> None:
    from companion_daemon.budget import image_render_estimate
    from companion_daemon.event_media import MediaRenderer, RenderedMedia
    from prove_photo_realism_production import _plan_scenes

    OUT.mkdir(parents=True, exist_ok=True)
    crop_proof = _verify_crop()
    planned = await _plan_scenes(require_paired_refs=False, limit=2)
    prompts_dir = OUT / "prompts"
    plans_dir = OUT / "plans"
    renders_dir = OUT / "renders"
    for path in (prompts_dir, plans_dir, renders_dir):
        path.mkdir(parents=True, exist_ok=True)

    renderer = MediaRenderer(
        generator=_generator(),
        inspector=_PassInspector(),  # type: ignore[arg-type]
        output_dir=renders_dir,
        visual_identity_path=VISUAL_IDENTITY,
        quality="medium",
    )
    production_references = renderer._references
    renders: list[dict] = []
    for item in planned:
        plan = item["plan"]
        prompt = item["prompt"]
        production_refs = production_references(plan)
        if tuple(path.resolve() for path in production_refs) != (ORIGINAL.resolve(),):
            raise SystemExit(f"production renderer must use only original 08, got {production_refs!r}")
        scene_id = item["scene_id"]
        plan_path = plans_dir / f"{scene_id}.json"
        prompt_path = prompts_dir / f"{scene_id}.txt"
        plan_path.write_text(
            json.dumps(item["plan_payload"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        prompt_path.write_text(prompt, encoding="utf-8")
        for variant, reference in (("original-08", ORIGINAL), ("exact-crop-08", CROP)):
            references = (reference,)
            request_path = prompts_dir / f"{scene_id}.{variant}.request.json"
            request_path.write_text(
                json.dumps(
                    _request(prompt=prompt, references=references),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            target = renders_dir / f"{scene_id}.{variant}.png"
            if not target.is_file():
                estimate = image_render_estimate(
                    reference_count=1, size="1024x1536", quality="medium", attempts=1
                )
                if _spend_total() + estimate.cny > BUDGET_CAP_CNY:
                    raise SystemExit(f"debug image budget would exceed ¥{BUDGET_CAP_CNY:.2f}")
                renderer._references = lambda _plan, _refs=references: _refs  # type: ignore[method-assign]
                result = await renderer.render(plan)
                if not isinstance(result, RenderedMedia):
                    raise SystemExit(f"render failed {scene_id}/{variant}: {result}")
                target.write_bytes(result.path.read_bytes())
                renderer._references = production_references  # type: ignore[method-assign]
                cost = _record_spend(
                    f"photo-ai-look-original-identity:{scene_id}:{variant}:gpt-image-2"
                )
            else:
                cost = 0.0
            renders.append(
                {
                    "scene_id": scene_id,
                    "variant": variant,
                    "path": _relative(target),
                    "prompt_path": _relative(prompt_path),
                    "request_path": _relative(request_path),
                    "plan_path": _relative(plan_path),
                    "prompt_chars": len(prompt),
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "reference_path": _relative(reference),
                    "cost_cny": cost,
                }
            )
            print(json.dumps(renders[-1], ensure_ascii=False), flush=True)

    sheet = _write_contact_sheet(
        [
            ORIGINAL,
            CROP,
            DELIVERED,
            *(REPO / item["path"] for item in renders),
        ],
        [
            "IDENTITY AUTHORITY: original 08",
            "exact source-pixel crop of 08",
            "delivered 2026-08-19",
            *(f"{item['scene_id']}:{item['variant']}" for item in renders),
        ],
    )
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "identity_authority": _relative(ORIGINAL),
        "crop_proof": crop_proof,
        "production_change": "provider receives only frozen identity_anchor; angle_support remains audit-only",
        "production_state_touched": False,
        "pipeline": "MediaPlanner v5 -> compile_media_prompt -> MediaRenderer -> OpenAIImageGenerator",
        "renders": renders,
        "contact_sheet": _relative(sheet),
        "budget_cap_cny": BUDGET_CAP_CNY,
        "spend_cny": _spend_total(),
        "finished_at": datetime.now(UTC).isoformat(),
    }
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"ok": True, "spend_cny": report["spend_cny"]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
