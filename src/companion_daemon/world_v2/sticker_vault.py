"""Autonomous Sticker & Meme Vault with pragmatic subtext labels and inbound harvesting.

This module bridges platform capability declaration, vision perception harvesting,
and OneBot image delivery without taking semantic control away from the character
model:
- The vault provides executable ``meme:<id>`` options alongside ``qq-face:<id>``
  in ``ExpressionDraftCapabilities.sticker_options``, each annotated with both
  surface visuals and pragmatic subtext (including Tieba-style irony/aggression).
- When the user sends a meme/sticker image analyzed by vision perception, the
  vault automatically harvests the image bytes and subtext summary so it becomes
  available in the companion's sticker repertoire.
- Deterministic code only manages image persistence, catalog bounds, and file
  resolution; the character model decides whether and when to send any sticker.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import io
import json
import logging
from pathlib import Path
import re
import threading
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont


_LOG = logging.getLogger(__name__)
_VAULT_LOCK = threading.RLock()
_DEFAULT_VAULT_DIR = Path("data/sticker_vault")
_MAX_HARVESTED_MEMES = 8
_MEME_ID_PATTERN = re.compile(r"^meme:[a-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class StickerVaultEntry:
    option_id: str
    label: str
    filename: str
    source: str = "builtin"


BUILTIN_MEME_ENTRIES: tuple[StickerVaultEntry, ...] = (
    StickerVaultEntry(
        option_id="meme:cat_peek",
        label="猫猫探头[画面:小猫从墙角悄悄探出半个脑袋睁圆眼睛张望]",
        filename="cat_peek.png",
    ),
    StickerVaultEntry(
        option_id="meme:cat_eyeroll",
        label="猫猫无语翻白眼[画面:小猫半耷拉眼皮翻白眼]",
        filename="cat_eyeroll.png",
    ),
    StickerVaultEntry(
        option_id="meme:cat_slump",
        label="猫猫瘫倒[画面:小猫四仰八叉瘫成一张猫饼]",
        filename="cat_slump.png",
    ),
    StickerVaultEntry(
        option_id="meme:cat_sigh",
        label="猫猫叹气[画面:小猫垂着耳朵低头吐出一口小白气]",
        filename="cat_sigh.png",
    ),
    StickerVaultEntry(
        option_id="meme:cat_smug",
        label="猫猫偷笑[画面:小猫捂嘴弯眼偷笑露出小虎牙]",
        filename="cat_smug.png",
    ),
    StickerVaultEntry(
        option_id="meme:cat_wronged",
        label="猫猫委屈[画面:小猫飞机耳眼眶含泪低头瘪嘴]",
        filename="cat_wronged.png",
    ),
    StickerVaultEntry(
        option_id="meme:cat_question",
        label="猫猫问号[画面:小猫歪着脑袋满脸茫然头顶三个问号]",
        filename="cat_question.png",
    ),
    StickerVaultEntry(
        option_id="meme:cat_bonk",
        label="猫猫举锤·敲打[画面:小猫举起玩具小锤子]",
        filename="cat_bonk.png",
    ),
)


def _load_cjk_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_centered_caption(
    draw: ImageDraw.ImageDraw,
    *,
    text: str,
    y: int,
    width: int = 360,
    font_size: int = 36,
    fill: tuple[int, int, int] = (20, 20, 20),
    stroke_fill: tuple[int, int, int] = (255, 255, 255),
) -> None:
    font = _load_cjk_font(font_size)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=3)
    text_w = bbox[2] - bbox[0]
    x = max(12, (width - text_w) // 2)
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=3,
        stroke_fill=stroke_fill,
    )


def _render_builtin_meme_png(option_id: str, target_path: Path) -> None:
    """Render a crisp 360x360 cartoon meme sticker PNG on disk."""

    img = Image.new("RGBA", (360, 360), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    if option_id == "meme:tieba_sweat":
        # Classic Tieba sweating yellow bean (流汗黄豆)
        draw.ellipse((45, 35, 315, 295), fill=(255, 214, 51), outline=(180, 125, 10), width=6)
        # Arched awkward eyebrows
        draw.arc((85, 80, 155, 125), start=200, end=340, fill=(90, 55, 10), width=6)
        draw.arc((205, 80, 275, 125), start=200, end=340, fill=(90, 55, 10), width=6)
        # Eyes looking slightly aside
        draw.ellipse((95, 115, 150, 150), fill=(255, 255, 255), outline=(70, 45, 10), width=4)
        draw.ellipse((210, 115, 265, 150), fill=(255, 255, 255), outline=(70, 45, 10), width=4)
        draw.ellipse((118, 122, 142, 144), fill=(35, 25, 10))
        draw.ellipse((232, 122, 256, 144), fill=(35, 25, 10))
        # Awkward open grin with white upper teeth
        draw.chord((95, 165, 265, 260), start=0, end=180, fill=(175, 45, 45), outline=(90, 45, 10), width=5)
        draw.rectangle((108, 168, 252, 192), fill=(255, 255, 255), outline=(90, 45, 10), width=3)
        # Giant blue sweat drop on temple
        draw.polygon([(285, 52), (258, 118), (312, 118)], fill=(70, 175, 255))
        draw.ellipse((258, 98, 312, 152), fill=(70, 175, 255), outline=(25, 105, 190), width=4)
        _draw_centered_caption(draw, text="呃呃…你没事吧", y=302, font_size=34)

    elif option_id == "meme:tieba_huaji":
        # Classic Tieba Huaji (贴吧滑稽)
        draw.ellipse((45, 35, 315, 295), fill=(255, 218, 68), outline=(180, 125, 10), width=6)
        # Raised mischievous eyebrows
        draw.arc((75, 65, 155, 110), start=190, end=350, fill=(80, 50, 10), width=5)
        draw.arc((205, 65, 285, 110), start=190, end=350, fill=(80, 50, 10), width=5)
        # Narrow curved eyes looking right
        draw.rounded_rectangle((80, 112, 162, 138), radius=12, fill=(255, 255, 255), outline=(80, 50, 10), width=4)
        draw.rounded_rectangle((198, 112, 280, 138), radius=12, fill=(255, 255, 255), outline=(80, 50, 10), width=4)
        draw.ellipse((132, 115, 156, 135), fill=(30, 20, 10))
        draw.ellipse((250, 115, 274, 135), fill=(30, 20, 10))
        # Pink blush ovals
        draw.ellipse((68, 145, 118, 172), fill=(255, 135, 145))
        draw.ellipse((242, 145, 292, 172), fill=(255, 135, 145))
        # Wide smirk arc
        draw.arc((90, 135, 270, 255), start=15, end=165, fill=(90, 45, 10), width=6)
        _draw_centered_caption(draw, text="懂的都懂", y=302, font_size=36)

    elif option_id in {"meme:panda_jile", "meme:panda_sip_tea"}:
        # Classic Biaoqingbao Panda Head (熊猫头)
        draw.ellipse((55, 30, 120, 95), fill=(20, 20, 20))
        draw.ellipse((240, 30, 305, 95), fill=(20, 20, 20))
        draw.ellipse((55, 45, 305, 275), fill=(255, 255, 255), outline=(20, 20, 20), width=6)
        # Inner creepy/funny human-like face contour
        draw.ellipse((95, 85, 265, 245), fill=(250, 250, 250), outline=(40, 40, 40), width=3)
        # Panda eye patches tilted
        draw.ellipse((110, 110, 165, 150), fill=(25, 25, 25))
        draw.ellipse((195, 110, 250, 150), fill=(25, 25, 25))
        draw.ellipse((132, 122, 148, 138), fill=(255, 255, 255))
        draw.ellipse((218, 122, 234, 138), fill=(255, 255, 255))
        # Nose
        draw.polygon([(180, 152), (170, 168), (190, 168)], fill=(25, 25, 25))
        if option_id == "meme:panda_jile":
            # Mocking grin + pointing hand
            draw.chord((125, 175, 235, 230), start=0, end=180, fill=(255, 255, 255), outline=(20, 20, 20), width=4)
            draw.line((135, 198, 225, 198), fill=(20, 20, 20), width=3)
            # Pointing finger
            draw.rounded_rectangle((225, 215, 320, 265), radius=18, fill=(255, 255, 255), outline=(20, 20, 20), width=5)
            draw.ellipse((200, 210, 255, 268), fill=(255, 255, 255), outline=(20, 20, 20), width=5)
            _draw_centered_caption(draw, text="急了急了", y=296, font_size=40)
        else:
            # Calm smirk + teacup
            draw.arc((145, 175, 215, 205), start=10, end=170, fill=(20, 20, 20), width=4)
            draw.rounded_rectangle((145, 212, 220, 272), radius=10, fill=(220, 240, 255), outline=(20, 20, 20), width=4)
            draw.arc((205, 222, 240, 260), start=270, end=90, fill=(20, 20, 20), width=4)
            _draw_centered_caption(draw, text="静静看你装", y=296, font_size=36)

    else:
        # Expressive Cat Meme Heads (猫猫头表情包)
        bg_colors = {
            "meme:cat_peek": (252, 249, 242),
            "meme:cat_eyeroll": (245, 246, 250),
            "meme:cat_slump": (244, 247, 244),
            "meme:cat_sigh": (242, 245, 250),
            "meme:cat_smug": (255, 248, 238),
            "meme:cat_bonk": (255, 242, 242),
            "meme:cat_wronged": (240, 248, 255),
            "meme:cat_question": (254, 250, 238),
        }
        draw.rectangle((0, 0, 360, 360), fill=bg_colors.get(option_id, (250, 250, 250)))
        if option_id == "meme:cat_peek":
            # Wall edge on left + peeking cat head & paws
            draw.rectangle((0, 0, 110, 360), fill=(215, 205, 192), outline=(60, 45, 35), width=5)
            draw.polygon([(125, 130), (145, 42), (210, 88)], fill=(242, 222, 195), outline=(60, 45, 35), width=5)
            draw.polygon([(315, 130), (295, 42), (235, 88)], fill=(242, 222, 195), outline=(60, 45, 35), width=5)
            draw.ellipse((105, 76, 330, 276), fill=(250, 238, 220), outline=(60, 45, 35), width=5)
            # Big curious round eyes
            draw.ellipse((148, 124, 198, 174), fill=(35, 30, 25))
            draw.ellipse((238, 124, 288, 174), fill=(35, 30, 25))
            draw.ellipse((158, 132, 176, 150), fill=(255, 255, 255))
            draw.ellipse((248, 132, 266, 150), fill=(255, 255, 255))
            draw.polygon([(210, 180), (226, 180), (218, 192)], fill=(245, 145, 155))
            # Paws gripping wall edge
            draw.ellipse((85, 165, 135, 205), fill=(250, 238, 220), outline=(60, 45, 35), width=4)
            draw.ellipse((85, 220, 135, 260), fill=(250, 238, 220), outline=(60, 45, 35), width=4)
            _draw_centered_caption(draw, text="暗中观察…", y=296, font_size=36)
        elif option_id == "meme:cat_slump":
            # Flattened pancake cat (猫猫瘫倒)
            draw.polygon([(60, 175), (78, 122), (132, 158)], fill=(242, 222, 195), outline=(60, 45, 35), width=5)
            draw.polygon([(300, 175), (282, 122), (228, 158)], fill=(242, 222, 195), outline=(60, 45, 35), width=5)
            draw.ellipse((35, 145, 325, 275), fill=(250, 238, 220), outline=(60, 45, 35), width=5)
            # Closed exhausted eyes (- -)
            draw.line((102, 195, 152, 195), fill=(60, 45, 35), width=5)
            draw.line((208, 195, 258, 195), fill=(60, 45, 35), width=5)
            draw.polygon([(172, 208), (188, 208), (180, 218)], fill=(245, 145, 155))
            # Little soul puff floating up
            draw.ellipse((255, 65, 305, 108), fill=(255, 255, 255), outline=(140, 140, 150), width=3)
            draw.line((242, 130, 265, 105), fill=(140, 140, 150), width=3)
            _draw_centered_caption(draw, text="彻底瘫倒…勿扰", y=296, font_size=35)
        else:
            # Standard upright cat head
            draw.polygon([(65, 125), (85, 38), (155, 85)], fill=(242, 222, 195), outline=(60, 45, 35), width=5)
            draw.polygon([(295, 125), (275, 38), (205, 85)], fill=(242, 222, 195), outline=(60, 45, 35), width=5)
            draw.polygon([(82, 112), (92, 58), (140, 88)], fill=(255, 185, 190))
            draw.polygon([(278, 112), (268, 58), (220, 88)], fill=(255, 185, 190))
            draw.ellipse((55, 72, 305, 282), fill=(250, 238, 220), outline=(60, 45, 35), width=5)
            draw.polygon([(172, 178), (188, 178), (180, 190)], fill=(245, 145, 155))
            draw.line((50, 180, 95, 185), fill=(80, 65, 55), width=3)
            draw.line((50, 200, 95, 198), fill=(80, 65, 55), width=3)
            draw.line((265, 185, 310, 180), fill=(80, 65, 55), width=3)
            draw.line((265, 198, 310, 200), fill=(80, 65, 55), width=3)

            if option_id == "meme:cat_eyeroll":
                # Half-lidded eye-roll
                draw.ellipse((100, 122, 155, 168), fill=(255, 255, 255), outline=(60, 45, 35), width=4)
                draw.ellipse((205, 122, 260, 168), fill=(255, 255, 255), outline=(60, 45, 35), width=4)
                draw.ellipse((118, 120, 140, 142), fill=(35, 30, 25))
                draw.ellipse((223, 120, 245, 142), fill=(35, 30, 25))
                draw.line((98, 132, 158, 132), fill=(60, 45, 35), width=5)
                draw.line((203, 132, 263, 132), fill=(60, 45, 35), width=5)
                draw.line((158, 212, 202, 210), fill=(60, 45, 35), width=5)
                _draw_centered_caption(draw, text="无语凝噎…", y=296, font_size=36)
            elif option_id == "meme:cat_sigh":
                # Droopy eyes + white sigh puff
                draw.arc((98, 132, 158, 165), start=15, end=170, fill=(60, 45, 35), width=5)
                draw.arc((202, 132, 262, 165), start=10, end=165, fill=(60, 45, 35), width=5)
                draw.arc((158, 208, 202, 228), start=195, end=345, fill=(60, 45, 35), width=4)
                draw.ellipse((230, 195, 315, 242), fill=(255, 255, 255), outline=(130, 145, 165), width=3)
                _draw_centered_caption(draw, text="唉…心累", y=296, font_size=36)
            elif option_id == "meme:cat_smug":
                # Curved happy/smug eyes + paw covering mouth snicker
                draw.arc((98, 125, 158, 160), start=190, end=355, fill=(60, 45, 35), width=5)
                draw.arc((202, 125, 262, 160), start=185, end=350, fill=(60, 45, 35), width=5)
                draw.arc((145, 185, 225, 230), start=10, end=145, fill=(60, 45, 35), width=5)
                _draw_centered_caption(draw, text="噗…偷笑", y=296, font_size=36)
            elif option_id == "meme:cat_question":
                # Wide blank eyes + question marks above head
                draw.ellipse((104, 125, 152, 173), fill=(35, 30, 25))
                draw.ellipse((208, 125, 256, 173), fill=(35, 30, 25))
                draw.ellipse((116, 134, 132, 150), fill=(255, 255, 255))
                draw.ellipse((220, 134, 236, 150), fill=(255, 255, 255))
                draw.ellipse((172, 208, 188, 222), outline=(60, 45, 35), width=4)
                _draw_centered_caption(draw, text="？ ？ ？", y=12, font_size=42, fill=(195, 45, 45))
                _draw_centered_caption(draw, text="猫猫问号", y=296, font_size=36)
            elif option_id == "meme:cat_bonk":
                # Fierce eyes + toy hammer
                draw.line((98, 118, 155, 135), fill=(60, 45, 35), width=6)
                draw.line((262, 118, 205, 135), fill=(60, 45, 35), width=6)
                draw.ellipse((110, 132, 148, 168), fill=(40, 30, 25))
                draw.ellipse((212, 132, 250, 168), fill=(40, 30, 25))
                draw.arc((155, 198, 205, 230), start=190, end=350, fill=(60, 45, 35), width=5)
                draw.line((270, 250, 315, 135), fill=(140, 90, 45), width=10)
                draw.rounded_rectangle((265, 85, 350, 145), radius=12, fill=(235, 85, 85), outline=(60, 45, 35), width=4)
                _draw_centered_caption(draw, text="再皮一下试试", y=296, font_size=36)
            else:
                # Teary wronged cat eyes (meme:cat_wronged)
                draw.ellipse((100, 122, 158, 178), fill=(35, 30, 25))
                draw.ellipse((202, 122, 260, 178), fill=(35, 30, 25))
                draw.ellipse((110, 130, 132, 152), fill=(255, 255, 255))
                draw.ellipse((212, 130, 234, 152), fill=(255, 255, 255))
                draw.ellipse((98, 168, 125, 205), fill=(95, 190, 255))
                draw.ellipse((235, 168, 262, 205), fill=(95, 190, 255))
                draw.arc((155, 205, 205, 232), start=195, end=345, fill=(60, 45, 35), width=4)
                _draw_centered_caption(draw, text="委屈巴巴…", y=296, font_size=36)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(target_path, format="PNG", optimize=True)


def ensure_sticker_vault_ready(vault_dir: Path = _DEFAULT_VAULT_DIR) -> list[StickerVaultEntry]:
    """Ensure built-in meme stickers and catalog.json exist on disk."""

    with _VAULT_LOCK:
        images_dir = vault_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        catalog_path = vault_dir / "catalog.json"

        for entry in BUILTIN_MEME_ENTRIES:
            image_path = images_dir / entry.filename
            if not image_path.exists() or image_path.stat().st_size == 0:
                try:
                    _render_builtin_meme_png(entry.option_id, image_path)
                except Exception:
                    _LOG.warning("failed rendering builtin meme %s", entry.option_id, exc_info=True)

        harvested: list[StickerVaultEntry] = []
        if catalog_path.exists():
            try:
                raw = json.loads(catalog_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    for item in raw:
                        if not isinstance(item, dict):
                            continue
                        oid = str(item.get("option_id") or "").strip()
                        label = str(item.get("label") or "").strip()
                        filename = str(item.get("filename") or "").strip()
                        source = str(item.get("source") or "harvested").strip()
                        if "【潜台词" in label:
                            head = label.split("【潜台词", 1)[0].rstrip(" ]")
                            label = f"{head}]" if head.startswith("收存表情包[") else head
                        if (
                            source != "builtin"
                            and _MEME_ID_PATTERN.match(oid)
                            and label
                            and filename
                            and (images_dir / filename).exists()
                        ):
                            harvested.append(
                                StickerVaultEntry(
                                    option_id=oid,
                                    label=label[:120],
                                    filename=filename,
                                    source=source,
                                )
                            )
            except Exception:
                _LOG.warning("failed reading sticker_vault catalog.json", exc_info=True)

        entries = [*BUILTIN_MEME_ENTRIES, *harvested[-_MAX_HARVESTED_MEMES:]]
        _write_catalog_unlocked(catalog_path, entries)
        return entries


def _write_catalog_unlocked(catalog_path: Path, entries: list[StickerVaultEntry]) -> None:
    payload = [
        {
            "option_id": item.option_id,
            "label": item.label,
            "filename": item.filename,
            "source": item.source,
        }
        for item in entries
    ]
    catalog_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sticker_vault_option_tuples(
    vault_dir: Path = _DEFAULT_VAULT_DIR,
) -> tuple[tuple[str, str], ...]:
    """Return ``(option_id, label)`` pairs for all active meme stickers in the vault."""

    entries = ensure_sticker_vault_ready(vault_dir)
    return tuple((item.option_id, item.label[:120]) for item in entries)


def is_valid_sticker_vault_id(
    sticker_id: str,
    *,
    vault_dir: Path = _DEFAULT_VAULT_DIR,
) -> bool:
    """Whether ``sticker_id`` is a valid ``meme:<id>`` backed by the sticker vault."""

    if not isinstance(sticker_id, str) or not _MEME_ID_PATTERN.match(sticker_id):
        return False
    return resolve_sticker_vault_image_path(sticker_id, vault_dir=vault_dir) is not None


def resolve_sticker_vault_image_path(
    sticker_id: str,
    *,
    vault_dir: Path = _DEFAULT_VAULT_DIR,
) -> Path | None:
    """Resolve ``meme:<id>`` to its on-disk image file path."""

    if not isinstance(sticker_id, str) or not _MEME_ID_PATTERN.match(sticker_id):
        return None
    entries = ensure_sticker_vault_ready(vault_dir)
    images_dir = vault_dir / "images"
    for item in entries:
        if item.option_id == sticker_id:
            candidate = images_dir / item.filename
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def harvest_inbound_meme_from_data_url(
    *,
    data_url: str,
    input_hash: str,
    vl_description: str,
    vault_dir: Path = _DEFAULT_VAULT_DIR,
) -> str | None:
    """Automatically save an inbound meme/sticker image into the sticker vault.

    Triggered after Qwen-VL analyzes an inbound user image. Only images whose
    vision description identifies them as a meme/sticker (``表情包`` / ``梗图``)
    are harvested.
    """

    desc = " ".join(str(vl_description or "").split()).strip()
    if not desc or not any(
        marker in desc
        for marker in (
            "表情包",
            "梗图",
            "动图",
            "搞抽象",
            "无厘头",
            "熊猫头",
            "猫猫头",
            "黄豆",
            "滑稽",
            "龙图",
        )
    ):
        return None
    if not data_url.startswith("data:image/") or ";base64," not in data_url:
        return None
    try:
        _, encoded = data_url.split(";base64,", 1)
        raw_bytes = base64.b64decode(encoded, validate=False)
    except Exception:
        return None
    if not raw_bytes or len(raw_bytes) > 4 * 1024 * 1024:
        return None

    is_gif = raw_bytes[:6] in (b"GIF87a", b"GIF89a") or data_url.startswith("data:image/gif;")
    short_hash = hashlib.sha256((input_hash or desc).encode("utf-8")).hexdigest()[:10]
    option_id = f"meme:user_{short_hash}"
    filename = f"user_{short_hash}.gif" if is_gif else f"user_{short_hash}.png"

    with _VAULT_LOCK:
        entries = ensure_sticker_vault_ready(vault_dir)
        images_dir = vault_dir / "images"
        target_path = images_dir / filename
        if not target_path.exists():
            try:
                if is_gif:
                    target_path.write_bytes(raw_bytes)
                else:
                    with Image.open(io.BytesIO(raw_bytes)) as im:
                        im.thumbnail((420, 420))
                        im.convert("RGBA").save(target_path, format="PNG", optimize=True)
            except Exception:
                _LOG.debug("inbound meme bytes could not be saved to sticker vault", exc_info=True)
                return None

        compact_desc = desc.split("【潜台词", 1)[0].replace("\n", " ").strip()
        prefix = "收存表情包["
        max_inner = 120 - len(prefix) - 1
        label = f"{prefix}{compact_desc[:max_inner]}]"

        harvested = [
            item
            for item in entries
            if item.source != "builtin" and item.option_id != option_id
        ]
        harvested.append(
            StickerVaultEntry(
                option_id=option_id,
                label=label,
                filename=filename,
                source="harvested_inbound",
            )
        )
        updated = [*BUILTIN_MEME_ENTRIES, *harvested[-_MAX_HARVESTED_MEMES:]]
        _write_catalog_unlocked(vault_dir / "catalog.json", updated)
        return option_id


async def search_and_harvest_online_meme(
    query: str,
    *,
    subtext_label: str | None = None,
    vault_dir: Path = _DEFAULT_VAULT_DIR,
    timeout_seconds: float = 8.0,
) -> str | None:
    """Search public Chinese meme repositories by keyword and save one into the vault."""

    clean_query = " ".join(str(query or "").split()).strip()
    if not clean_query:
        return None
    short_hash = hashlib.sha256(clean_query.encode("utf-8")).hexdigest()[:8]
    option_id = f"meme:web_{short_hash}"
    filename = f"web_{short_hash}.png"

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            resp = await client.get(
                "https://pic.sogou.com/napi/wap/emoji/searchlist",
                params={"keyword": clean_query, "spver": "0", "rcer": "", "tag": "0", "routeName": "emosearch"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code != 200:
                return None
            payload: dict[str, Any] = resp.json()
            emotions = ((payload.get("data") or {}).get("emotions") or [])
            image_url: str | None = None
            for item in emotions:
                if isinstance(item, dict):
                    candidate = str(item.get("thumbSrc") or item.get("picUrl") or "").strip()
                    if candidate.startswith("http"):
                        image_url = candidate
                        break
            if not image_url:
                return None
            img_resp = await client.get(image_url, headers={"User-Agent": "Mozilla/5.0"})
            if img_resp.status_code != 200 or not img_resp.content:
                return None
            raw_bytes = img_resp.content
    except Exception:
        _LOG.debug("online meme search failed for query=%s", clean_query, exc_info=True)
        return None

    with _VAULT_LOCK:
        entries = ensure_sticker_vault_ready(vault_dir)
        images_dir = vault_dir / "images"
        target_path = images_dir / filename
        try:
            with Image.open(io.BytesIO(raw_bytes)) as im:
                im.thumbnail((420, 420))
                im.convert("RGBA").save(target_path, format="PNG", optimize=True)
        except Exception:
            return None

        raw_label = subtext_label or f"网络梗图:{clean_query}"
        label = f"表情包[{raw_label[:110]}]"
        harvested = [
            item
            for item in entries
            if item.source != "builtin" and item.option_id != option_id
        ]
        harvested.append(
            StickerVaultEntry(
                option_id=option_id,
                label=label,
                filename=filename,
                source="web_search",
            )
        )
        updated = [*BUILTIN_MEME_ENTRIES, *harvested[-_MAX_HARVESTED_MEMES:]]
        _write_catalog_unlocked(vault_dir / "catalog.json", updated)
        return option_id


__all__ = [
    "BUILTIN_MEME_ENTRIES",
    "StickerVaultEntry",
    "ensure_sticker_vault_ready",
    "harvest_inbound_meme_from_data_url",
    "is_valid_sticker_vault_id",
    "resolve_sticker_vault_image_path",
    "search_and_harvest_online_meme",
    "sticker_vault_option_tuples",
]
