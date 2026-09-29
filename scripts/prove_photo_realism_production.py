#!/usr/bin/env python3
"""Production-path photo realism / identity-ref A-B / outfit variance prove.

Walks real MediaPlanner v5 → compile_media_prompt → MediaRenderer →
OpenAIImageGenerator. Never touches production ledgers, QQ, or scheduler.

Phases:
  ab      — same frozen plans, refs=2 vs refs=1 (n=4 each)
  outfit  — after outfit fix, n≥4 scene-varied renders (refs=1)
  all     — ab then outfit

Outputs under output/photo-realism/ with full prompt archives.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "tests") not in sys.path:
    sys.path.insert(0, str(REPO / "tests"))

OUT = REPO / "output" / "photo-realism"
SPEND_DB = OUT / "debug-spend.sqlite"
VISUAL_IDENTITY = REPO / "configs" / "visual_identity.yaml"

# Candidate scenes; planner keeps the first four that freeze identity_anchor + angle_support.
SCENE_POOL: tuple[dict[str, object], ...] = (
    {
        "id": "library-afternoon",
        "activity": {"kind": "study", "description": "在图书馆靠窗座位复习"},
        "location": {"name": "大学图书馆", "kind": "public", "mirror_available": False},
        "environment": {"lighting": "overcast window light", "weather": "cloudy"},
        "objects": [{"id": "obj:notes", "kind": "object", "description": "打开的笔记本和笔"}],
        "event_summary": "图书馆自习",
        "logical_at": "2026-08-19T15:20:00+08:00",
        "content_domain": "activity_process",
        "primary": "/activity/description",
        "support": ["/location/name", "/environment/lighting"],
    },
    {
        "id": "campus-morning-walk",
        "activity": {"kind": "walking", "description": "早上穿过校园去上课"},
        "location": {"name": "校园主路", "kind": "public", "mirror_available": False},
        "environment": {"lighting": "clear morning sun", "weather": "sunny"},
        "objects": [{"id": "obj:tote", "kind": "object", "description": "帆布包"}],
        "event_summary": "清晨校园赶课",
        "logical_at": "2026-08-19T08:20:00+08:00",
        "content_domain": "activity_process",
        "primary": "/activity/description",
        "support": ["/location/name", "/environment/lighting"],
    },
    {
        "id": "gym-mirror-rest",
        "activity": {"kind": "workout", "description": "健身房力量训练后在镜前缓一会"},
        "location": {"name": "校园健身房", "kind": "public", "mirror_available": True},
        "environment": {"lighting": "bright overhead lights", "weather": "indoor"},
        "objects": [{"id": "obj:bottle", "kind": "object", "description": "运动水瓶"}],
        "event_summary": "健身后休息",
        "logical_at": "2026-08-19T18:40:00+08:00",
        "content_domain": "activity_process",
        "primary": "/activity/description",
        "support": ["/location/name", "/environment/lighting"],
        "capture_mode": "mirror",
    },
    {
        "id": "dorm-kitchen-cook",
        "activity": {"kind": "cooking", "description": "在宿舍小厨房煮面"},
        "location": {"name": "宿舍厨房", "kind": "private", "mirror_available": False},
        "environment": {"lighting": "warm kitchen light", "weather": "indoor"},
        "objects": [{"id": "obj:bowl", "kind": "object", "description": "刚盛出的面"}],
        "event_summary": "宿舍煮面",
        "logical_at": "2026-08-19T19:30:00+08:00",
        "content_domain": "activity_process",
        "primary": "/activity/description",
        "support": ["/location/name", "/environment/lighting"],
    },
    {
        "id": "bookstore-browse",
        "activity": {"kind": "study", "description": "在二手书店角落翻书"},
        "location": {"name": "二手书店", "kind": "public", "mirror_available": False},
        "environment": {"lighting": "soft aisle lamps", "weather": "indoor"},
        "objects": [{"id": "obj:book", "kind": "object", "description": "翻开的旧书"}],
        "event_summary": "书店翻书",
        "logical_at": "2026-08-19T16:10:00+08:00",
        "content_domain": "activity_process",
        "primary": "/activity/description",
        "support": ["/location/name", "/environment/lighting"],
    },
    {
        "id": "restaurant-dinner",
        "activity": {"kind": "eating", "description": "晚上在小餐馆吃饭"},
        "location": {"name": "小餐馆", "kind": "public", "mirror_available": False},
        "environment": {"lighting": "warm restaurant light", "weather": "indoor"},
        "objects": [{"id": "food:bowl", "kind": "food", "description": "一碗热汤面"}],
        "event_summary": "餐馆晚饭",
        "logical_at": "2026-08-19T19:05:00+08:00",
        "content_domain": "food_drink",
        "primary": "/activity/description",
        "support": ["/location/name", "/objects/0/description"],
    },
)


class _PassInspector:
    async def inspect(self, path: Path, *, plan, prompt: str, reference_images=()):
        from test_event_media import _inspection

        del path, plan, prompt, reference_images
        return _inspection(True, reason="photo_realism_production_probe")


def _v5_proposal(**kwargs: object) -> dict[str, object]:
    from test_event_media import _proposal

    payload = _proposal(**kwargs)
    payload["interaction_bid_id"] = str(kwargs.get("interaction_bid_id") or "share_presence")
    for field in (
        "composition",
        "action",
        "camera_direction",
        "sharing_motive",
        "subject_variant_id",
    ):
        payload.pop(field, None)
    return payload


def _desensitized(*, prompt: str, references: list[str], size: str, quality: str) -> dict:
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


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def _provider_refs(plan, *, mode: str) -> tuple[Path, ...]:
    """mode=frozen_pair uses the plan's frozen selection (≤2); mode=anchor_only keeps canonical."""
    selection = plan.identity_reference_selection
    if selection is None:
        return ()
    paired = tuple(
        (Path(asset_id), role)
        for asset_id, role in zip(selection.asset_ids, selection.roles, strict=False)
    )
    existing = tuple((path, role) for path, role in paired if path.is_file())
    if mode == "frozen_pair":
        return tuple(path for path, _role in existing)[:2]
    anchors = tuple(path for path, role in existing if role == "identity_anchor")
    if anchors:
        return anchors[:1]
    return tuple(path for path, _role in existing)[:1]


async def _plan_scenes(*, require_paired_refs: bool = True, limit: int = 4) -> list[dict]:
    from test_event_media import V5SelectingModel, _opportunity, _snapshot
    from companion_daemon.event_media import MediaPlanner, PlannedMedia, compile_media_prompt

    os.environ["COMPANION_EVENT_MEDIA_ENABLED"] = "1"
    os.environ["COMPANION_EVENT_MEDIA_V5_ENABLED"] = "1"
    planned: list[dict] = []
    for scene in SCENE_POOL:
        snap = _snapshot(
            activity=scene["activity"],
            location=scene["location"],
            environment=scene["environment"],
            objects=scene["objects"],
            event={
                "event_id": f"event:{scene['id']}",
                "type": "daily_activity",
                "status": "committed",
                "logical_at": scene["logical_at"],
                "summary": scene["event_summary"],
                "outcome": "ok",
            },
        )
        opp = replace(
            _opportunity(snapshot=snap, privacy="ordinary"),
            opportunity_id=f"opportunity:{scene['id']}",
            privacy_ceiling="ordinary",
        )
        proposal = _v5_proposal(
            content_domain=scene["content_domain"],
            primary_evidence_ref=scene["primary"],
            supporting_evidence_refs=list(scene["support"]),
            privacy="ordinary",
            capture_mode=str(scene.get("capture_mode") or "character_front_camera"),
        )
        result = await MediaPlanner(V5SelectingModel(proposal)).plan(opp)
        if not isinstance(result, PlannedMedia):
            print(f"skip {scene['id']}: {result}", flush=True)
            continue
        plan = result.plan
        if plan.identity_reference_selection is None:
            print(f"skip {scene['id']}: no identity refs", flush=True)
            continue
        n_refs = len(plan.identity_reference_selection.asset_ids)
        if require_paired_refs and n_refs < 2:
            print(f"skip {scene['id']}: froze {n_refs} refs", flush=True)
            continue
        prompt = compile_media_prompt(plan, VISUAL_IDENTITY)
        planned.append(
            {
                "scene_id": str(scene["id"]),
                "plan": plan,
                "plan_payload": plan.to_payload(),
                "prompt": prompt,
                "appearance": plan.subject_presentation.appearance.to_payload()
                if plan.subject_presentation
                else None,
                "frozen_refs": list(plan.identity_reference_selection.asset_ids),
                "frozen_roles": list(plan.identity_reference_selection.roles),
                "view_axis": plan.camera_geometry.view_axis if plan.camera_geometry else None,
            }
        )
        if len(planned) >= limit:
            break
    if len(planned) < limit:
        raise SystemExit(f"only planned {len(planned)}/{limit} usable scenes")
    return planned


async def _render_group(
    *,
    planned: list[dict],
    group: str,
    ref_mode: str,
    out_dir: Path,
) -> dict:
    from companion_daemon.config import Settings
    from companion_daemon.db import CompanionStore
    from companion_daemon.event_media import MediaRenderer, RenderedMedia
    from companion_daemon.image_generation import OpenAIImageGenerator

    settings = Settings()
    api_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        raise SystemExit("OPENAI_API_KEY missing")
    out_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = out_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    SPEND_DB.parent.mkdir(parents=True, exist_ok=True)
    # Do not bind CompanionStore into OpenAIImageGenerator: production BudgetGate
    # enforces daily_image_limit=2 and a 2h min gap, which cannot run an A/B batch.
    # Record estimated spend into the debug sqlite ourselves after each success.
    store = CompanionStore(SPEND_DB)
    from companion_daemon.budget import image_render_estimate

    generator = OpenAIImageGenerator(
        api_key=api_key,
        proxy_url=getattr(settings, "openai_proxy_url", None)
        or os.environ.get("OPENAI_PROXY_URL"),
        model="gpt-image-2",
        spend_store=None,
    )
    renderer = MediaRenderer(
        generator=generator,
        inspector=_PassInspector(),  # type: ignore[arg-type]
        output_dir=out_dir,
        visual_identity_path=VISUAL_IDENTITY,
        quality="medium",
    )

    renders: list[dict] = []
    requests: list[dict] = []
    for item in planned:
        plan = item["plan"]
        refs = _provider_refs(plan, mode=ref_mode)
        if not refs:
            raise SystemExit(f"no provider refs for {item['scene_id']} mode={ref_mode}")
        # Force provider refs for this group; still use compile_media_prompt from the plan.
        renderer._references = lambda _plan, _refs=refs: _refs  # type: ignore[method-assign]
        prompt = item["prompt"]
        image_path = out_dir / f"{item['scene_id']}.png"
        prompt_path = prompts_dir / f"{item['scene_id']}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        req = _desensitized(
            prompt=prompt,
            references=[_relative(path) for path in refs],
            size="1024x1536",
            quality="medium",
        )
        (prompts_dir / f"{item['scene_id']}.request.json").write_text(
            json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        requests.append({"scene_id": item["scene_id"], "request": req})
        if image_path.is_file() and image_path.stat().st_size > 1000:
            print(
                json.dumps(
                    {
                        "group": group,
                        "scene_id": item["scene_id"],
                        "skipped_existing": True,
                        "path": _relative(image_path),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            renders.append(
                {
                    "scene_id": item["scene_id"],
                    "path": _relative(image_path),
                    "renderer_path": _relative(image_path),
                    "bytes": image_path.stat().st_size,
                    "prompt_chars": len(prompt),
                    "prompt_path": _relative(prompt_path),
                    "reference_count": len(refs),
                    "reference_paths": [_relative(path) for path in refs],
                    "outfit_role": (item.get("appearance") or {}).get("outfit_role"),
                    "appearance_source": (item.get("appearance") or {}).get("source"),
                    "view_axis": item.get("view_axis"),
                    "frozen_refs": item.get("frozen_refs"),
                    "skipped_existing": True,
                }
            )
            continue
        result = await renderer.render(plan)
        if not isinstance(result, RenderedMedia):
            raise SystemExit(f"render failed {group}/{item['scene_id']}: {result}")
        if result.path.is_file():
            image_path.write_bytes(result.path.read_bytes())
        target = image_path if image_path.is_file() else result.path
        estimate = image_render_estimate(
            reference_count=len(refs), size="1024x1536", quality="medium", attempts=1
        )
        store.record_usage(
            "image_generation",
            estimate.cny,
            note=(
                f"photo-realism:{group}:{item['scene_id']}:"
                f"gpt-image-2:1024x1536:medium:refs={len(refs)}"
            ),
        )
        renders.append(
            {
                "scene_id": item["scene_id"],
                "path": _relative(target),
                "renderer_path": _relative(result.path),
                "bytes": target.stat().st_size,
                "prompt_chars": len(prompt),
                "prompt_path": _relative(prompt_path),
                "reference_count": len(refs),
                "reference_paths": [_relative(path) for path in refs],
                "outfit_role": (item.get("appearance") or {}).get("outfit_role"),
                "appearance_source": (item.get("appearance") or {}).get("source"),
                "view_axis": item.get("view_axis"),
                "frozen_refs": item.get("frozen_refs"),
            }
        )
        print(
            json.dumps(
                {
                    "group": group,
                    "scene_id": item["scene_id"],
                    "refs": len(refs),
                    "outfit": (item.get("appearance") or {}).get("outfit_role"),
                    "path": renders[-1]["path"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return {"group": group, "ref_mode": ref_mode, "renders": renders, "requests": requests}


def _spend_summary() -> dict:
    if not SPEND_DB.is_file():
        return {"total_cny": 0.0, "events": []}
    con = sqlite3.connect(f"file:{SPEND_DB}?mode=ro", uri=True)
    rows = list(
        con.execute(
            "select id, kind, estimated_cny, note, created_at from usage_events order by id"
        )
    )
    total = con.execute("select coalesce(sum(estimated_cny), 0) from usage_events").fetchone()[0]
    con.close()
    return {
        "total_cny": float(total),
        "events": [
            {
                "id": row[0],
                "kind": row[1],
                "estimated_cny": row[2],
                "note": row[3],
                "created_at": row[4],
            }
            for row in rows
        ],
    }


def _write_contact_sheet(paths: list[Path], dest: Path, *, labels: list[str] | None = None) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return
    images = [Image.open(path).convert("RGB") for path in paths if path.is_file()]
    if not images:
        return
    thumb_w = 384
    thumb_h = int(thumb_w * images[0].height / images[0].width)
    cols = min(4, len(images))
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + 28)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, image in enumerate(images):
        thumb = image.resize((thumb_w, thumb_h))
        x = (index % cols) * thumb_w
        y = (index // cols) * (thumb_h + 28)
        sheet.paste(thumb, (x, y + 28))
        label = (labels or [p.name for p in paths])[index] if labels else paths[index].name
        draw.text((x + 6, y + 6), label[:48], fill=(230, 230, 230), font=font)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)


async def run_ab() -> dict:
    planned = await _plan_scenes()
    plans_dir = OUT / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    for item in planned:
        (plans_dir / f"{item['scene_id']}.json").write_text(
            json.dumps(item["plan_payload"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (plans_dir / f"{item['scene_id']}.prompt.txt").write_text(item["prompt"], encoding="utf-8")

    group_a = await _render_group(
        planned=planned,
        group="A_refs2",
        ref_mode="frozen_pair",
        out_dir=OUT / "A_refs2",
    )
    group_b = await _render_group(
        planned=planned,
        group="B_refs1",
        ref_mode="anchor_only",
        out_dir=OUT / "B_refs1",
    )
    sheet_paths = []
    labels = []
    for item in planned:
        sid = item["scene_id"]
        for group_name in ("A_refs2", "B_refs1"):
            path = OUT / group_name / f"{sid}.png"
            if path.is_file():
                sheet_paths.append(path)
                labels.append(f"{group_name}:{sid}")
    _write_contact_sheet(sheet_paths, OUT / "contact-sheet-A-vs-B.png", labels=labels)
    return {
        "planned": [
            {
                "scene_id": item["scene_id"],
                "prompt_chars": len(item["prompt"]),
                "appearance": item["appearance"],
                "frozen_refs": item["frozen_refs"],
                "frozen_roles": item["frozen_roles"],
                "view_axis": item["view_axis"],
                "prompt_has_camera_geometry": "Camera Geometry" in item["prompt"],
                "prompt_has_capture_physics": "Capture Physics" in item["prompt"],
                "prompt_has_moment_capture": "Moment Capture" in item["prompt"],
                "prompt_has_photographic_authenticity": "Photographic Authenticity"
                in item["prompt"],
                "prompt_has_facial_micro": "Facial Micro-Performance" in item["prompt"],
                "prompt_has_character_photo_realism": "Character-photo realism" in item["prompt"],
            }
            for item in planned
        ],
        "group_a": group_a,
        "group_b": group_b,
        "contact_sheet": _relative(OUT / "contact-sheet-A-vs-B.png"),
    }


async def run_outfit() -> dict:
    # Outfit variance does not need paired refs; identity check uses production default.
    planned = await _plan_scenes(require_paired_refs=False, limit=4)
    group = await _render_group(
        planned=planned,
        group="outfit_varied",
        ref_mode="anchor_only",
        out_dir=OUT / "outfit_varied",
    )
    paths = [OUT / "outfit_varied" / f"{item['scene_id']}.png" for item in planned]
    labels = [
        f"{item['scene_id']}:{(item.get('appearance') or {}).get('outfit_role', '')[:40]}"
        for item in planned
    ]
    _write_contact_sheet(paths, OUT / "contact-sheet-outfit-varied.png", labels=labels)
    outfits = [(item.get("appearance") or {}).get("outfit_role") for item in planned]
    return {
        "group": group,
        "outfit_roles": outfits,
        "outfit_roles_unique": len(set(outfits)),
        "contact_sheet": _relative(OUT / "contact-sheet-outfit-varied.png"),
        "planned_appearance": [item.get("appearance") for item in planned],
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("ab", "outfit", "all", "plan-only"), default="ab")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "phase": args.phase,
        "budget_cap_cny": 12.0,
        "output": _relative(OUT),
    }
    if args.phase in {"ab", "all", "plan-only"}:
        if args.phase == "plan-only":
            planned = await _plan_scenes()
            report["plan_only"] = [
                {
                    "scene_id": item["scene_id"],
                    "prompt_chars": len(item["prompt"]),
                    "appearance": item["appearance"],
                    "frozen_refs": item["frozen_refs"],
                    "checks": {
                        "Camera Geometry": "Camera Geometry" in item["prompt"],
                        "Capture Physics": "Capture Physics" in item["prompt"],
                        "Moment Capture": "Moment Capture" in item["prompt"],
                        "Photographic Authenticity": "Photographic Authenticity"
                        in item["prompt"],
                        "Facial Micro-Performance": "Facial Micro-Performance" in item["prompt"],
                    },
                }
                for item in planned
            ]
            for item in planned:
                print(item["scene_id"], item["appearance"], item["frozen_refs"])
        else:
            report["ab"] = await run_ab()
    if args.phase in {"outfit", "all"}:
        report["outfit"] = await run_outfit()
    report["spend"] = _spend_summary()
    report["finished_at"] = datetime.now(UTC).isoformat()
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "spend_cny": report["spend"]["total_cny"], "out": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
