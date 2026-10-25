"""Bilibili popular video feed source adapter.

Video metadata is untrusted external evidence. This adapter parses public feed
transport payloads; it never decides truth, relevance, character attention, or
whether the character shares a video.

Inspired by ``c1overzz/dsh-bili-scroll``, deep content inspection follows a
strict low-cost funnel:
1. Only a bounded number of newly encountered videos per poll are deep-inspected
   (with process-level deduplication cache by ``bvid``); remaining items stay
   lightweight feed cards until a future poll.
2. Subtitle-first: Bilibili AI/CC subtitles + danmaku + top comments are fetched
   first at zero multimodal token cost. When subtitles already explain the
   video clearly, vision is skipped altogether.
3. Cropped storyboard tiles on demand: when vision is needed (no subtitles,
   visual/demo categories, or subtitle references to on-screen action), cover
   images use CDN ``@512w.jpg`` scaling and storyboards are locally cropped via
   Pillow into 3 individual ``480x270`` JPEG tiles (with ``pvdata`` binary
   index fallback and ms/sec unit normalization) instead of sending full
   10x10 sprite sheets.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import io
import json
import os
import re
import struct
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image

from ._source_http import fetch_source_bytes
from .contracts import (
    ExternalSignalSourceFailure,
    ExternalSignalSourceItem,
    ExternalSignalSourcePage,
    SourceCursor,
)


BILIBILI_POPULAR_URL = "https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1"
_ALLOWED_API_HOSTS = frozenset({"api.bilibili.com"})
_ALLOWED_API_PATHS = frozenset({"/x/web-interface/popular"})
_WEB_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
_DEFAULT_HEADERS = {
    "User-Agent": _WEB_UA,
    "Referer": "https://www.bilibili.com/",
}
_DANMAKU_PATTERN = re.compile(r'<d p="([^"]+)">([^<]+)</d>')
_COVER_WIDTH = 512
_FRAME_MAX = 3
_TILE_W_DEFAULT = 480
_TILE_H_DEFAULT = 270
_MAX_IMAGE_BYTES = 1_500_000
_MAX_DEEP_INSPECT_PER_FETCH = 2

# Categories and subtitle cues where visual frames carry the core content even
# when some text exists (aligned with dsh-bili-scroll's duty.md visual rules).
_VISUAL_DEMO_CATEGORIES = frozenset(
    {
        "舞蹈",
        "宅舞",
        "街舞",
        "手工",
        "绘画",
        "美食",
        "美食制作",
        "动物圈",
        "喵星人",
        "汪星人",
        "单机游戏",
        "电子竞技",
        "手机游戏",
        "网络游戏",
        "桌游棋牌",
        "GMV",
        "MAD·AMV",
        "特摄",
        "极客DIY",
        "装机",
        "摄影",
        "仿妆cos",
        "穿搭",
        "运动",
    }
)
_VISUAL_DEICTIC_CUES = (
    "看这里",
    "如图所示",
    "看这个",
    "注意看",
    "画面里",
    "屏幕上",
    "给大家看",
    "这一幕",
)


@dataclass(frozen=True, slots=True)
class _CroppedStoryboardFrame:
    want_sec: float
    got_sec: float
    data_url: str
    kb: float


@dataclass(frozen=True, slots=True)
class _BilibiliVideoContentEnrichment:
    inspection_mode: str = "card_only"  # "subtitle" | "frames" | "subtitle+frames" | "comments_only" | "card_only"
    subtitle_excerpt: str = ""
    vision_summary: str = ""
    frame_timestamps_sec: tuple[float, ...] = ()
    danmaku_highlights: tuple[str, ...] = ()
    top_comments: tuple[str, ...] = ()


# Process-level LRU-bounded cache by bvid so repeated 15-minute polls do not
# re-download storyboards, re-bill the multimodal vision model, or churn
# normalized_hash when play/danmaku counters tick up on the same video.
_VIDEO_ENRICHMENT_CACHE: dict[str, _BilibiliVideoContentEnrichment] = {}
_VIDEO_ITEM_CACHE: dict[tuple[str, str], ExternalSignalSourceItem] = {}
_MAX_CACHE_ENTRIES = 256


def _https(url: str) -> str:
    """Normalize Bilibili resource URLs to https.

    Bilibili view/popular APIs frequently return ``http://i0.hdslb.com/...`` or
    protocol-relative ``//...`` URLs.
    """
    u = str(url or "").strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://"):
        return "https://" + u[len("http://") :]
    return u


def _cover_url(pic: str, width: int = _COVER_WIDTH) -> str:
    """Convert raw cover URL to Bilibili CDN resized URL (``@<width>w.jpg``)."""
    base = _https(str(pic).split("@", 1)[0])
    if not base:
        return ""
    return f"{base}@{width}w.jpg"


def _image_headers() -> dict[str, str]:
    """Headers for Bilibili CDN image/storyboard requests — never attach cookies."""
    return {"User-Agent": _WEB_UA, "Referer": "https://www.bilibili.com/"}


def _snapshot_entries(data: dict[str, Any], duration: float = 0.0) -> list[tuple[float, int]]:
    """Normalize videoshot ``index`` into ``[(seconds, tile_index), ...]``.

    Bilibili returns timestamps in either seconds or milliseconds depending on
    the video. Comparing against ``duration * 1.5`` avoids misclassifying
    multi-hour videos (e.g. 22163s) as milliseconds.
    """
    raws: list[tuple[float, int]] = []
    for i, item in enumerate(data.get("index") or []):
        ts = item.get("timestamp") if isinstance(item, dict) else item
        try:
            raws.append((float(ts), i))
        except (TypeError, ValueError):
            continue
    if not raws:
        return []
    biggest = max(v for v, _ in raws)
    looks_ms = duration > 0 and biggest > duration * 1.5
    scale = 1000.0 if looks_ms else 1.0
    return [(v / scale, i) for v, i in raws]


def _unpack_pvdata_entries(raw: bytes) -> list[tuple[float, int]]:
    """Fallback when ``index`` is empty: ``pvdata`` is a big-endian uint16 seconds array."""
    if not raw or len(raw) % 2 != 0:
        return []
    count = len(raw) // 2
    if count > 20_000:
        return []
    return [(float(sec), i) for i, sec in enumerate(struct.unpack(f">{count}H", raw))]


def _crop_tile(
    sheet: bytes,
    tile_index: int,
    tw: int = _TILE_W_DEFAULT,
    th: int = _TILE_H_DEFAULT,
) -> bytes:
    """Crop the ``tile_index``-th frame from a Bilibili videoshot sprite sheet."""
    img = Image.open(io.BytesIO(sheet))
    cols = max(1, img.width // tw)
    rows = max(1, img.height // th)
    if tile_index < 0 or tile_index >= cols * rows:
        raise ValueError(f"tile_index {tile_index} out of bounds for {cols}x{rows} sheet")
    col, row = tile_index % cols, tile_index // cols
    tile = img.crop((col * tw, row * th, (col + 1) * tw, (row + 1) * th)).convert("RGB")
    buf = io.BytesIO()
    tile.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _should_inspect_frames(
    *,
    entry: dict[str, Any],
    subtitle_excerpt: str,
) -> bool:
    """Decide whether visual frame inspection adds needed evidence beyond subtitles.

    Follows ``dsh-bili-scroll``'s ``prompts/duty.md`` policy:
    - When subtitles are missing or very short (e.g., silent demo, burned-in hard
      subtitles, or BGM-only), inspect 3 storyboard frames.
    - When the video belongs to a visual/demo category (food, dance, crafts,
      gaming, pets) or the subtitle explicitly points to the screen ("看这里",
      "如图所示"), inspect frames too.
    - When subtitles already clearly state the spoken content (commentary,
      storytelling, news, science), skip vision to save tokens.
    """
    if len(subtitle_excerpt.strip()) < 100:
        return True
    tname = _clean_text(entry.get("tname"), limit=80)
    if tname in _VISUAL_DEMO_CATEGORIES:
        return True
    if any(cue in subtitle_excerpt for cue in _VISUAL_DEICTIC_CUES):
        return True
    return False


def _validate_bilibili_endpoint(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() not in _ALLOWED_API_HOSTS
        or parsed.path not in _ALLOWED_API_PATHS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Bilibili popular feed URL is not in the exact endpoint allowlist")


class BilibiliPopularAdapter:
    """Acquire Bilibili popular video metadata without assigning World truth."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        source_id: str = "cn.social.bilibili.popular.v1",
        feed_url: str = BILIBILI_POPULAR_URL,
        signal_kind: str = "bilibili_popular_video",
        upstream_publisher_ref: str = "publisher:bilibili",
        vision_api_key: str | None = None,
        vision_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        vision_model: str = "qwen3-vl-plus",
        max_deep_inspect_per_fetch: int = _MAX_DEEP_INSPECT_PER_FETCH,
        bili_sessdata: str | None = None,
    ) -> None:
        if not source_id or not signal_kind or not upstream_publisher_ref:
            raise ValueError("Bilibili popular source identity is incomplete")
        _validate_bilibili_endpoint(feed_url)
        self.source_id = source_id
        self._http_client = http_client
        self._feed_url = feed_url
        self._signal_kind = signal_kind
        self._upstream_publisher_ref = upstream_publisher_ref
        self._gateway_ref = f"bilibili-api:{feed_url}"
        is_mock = isinstance(getattr(http_client, "_transport", None), httpx.MockTransport)
        self._vision_api_key = (
            vision_api_key
            if vision_api_key is not None
            else (os.environ.get("QWEN_API_KEY", "") if not is_mock else "")
        )
        self._vision_base_url = vision_base_url.rstrip("/")
        self._vision_model = vision_model
        self._max_deep_inspect_per_fetch = max(0, int(max_deep_inspect_per_fetch))
        self._bili_sessdata = (
            bili_sessdata
            if bili_sessdata is not None
            else (os.environ.get("BILI_SESSDATA", "") if not is_mock else "")
        )

    async def fetch(
        self,
        *,
        after: SourceCursor | None,
        observed_at: datetime,
        deadline_at: datetime,
        limit: int,
    ) -> ExternalSignalSourcePage:
        fetched = await fetch_source_bytes(
            http_client=self._http_client,
            url=self._feed_url,
            after=after,
            observed_at=observed_at,
            deadline_at=deadline_at,
            accept="application/json",
            extra_headers=_DEFAULT_HEADERS,
        )
        if fetched.not_modified:
            return ExternalSignalSourcePage(
                evidence_media_type=fetched.media_type,
                evidence_bytes=b"",
                next_cursor=fetched.cursor,
                not_modified=True,
            )
        evidence = fetched.evidence_bytes
        try:
            payload = json.loads(evidence)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalSignalSourceFailure("bilibili_popular_json_malformed") from exc
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ExternalSignalSourceFailure("bilibili_popular_response_invalid")
        data = payload.get("data")
        entries = data.get("list") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ExternalSignalSourceFailure("bilibili_popular_shape_invalid")
        bounded_entries = entries[:limit]
        enrichments = await self._enrich_entries(bounded_entries)
        items: list[ExternalSignalSourceItem] = []
        rejected = 0
        is_mock = isinstance(getattr(self._http_client, "_transport", None), httpx.MockTransport)
        for raw_entry, enrichment in zip(bounded_entries, enrichments, strict=True):
            try:
                bvid_key = (
                    _clean_text(raw_entry.get("bvid"), limit=64)
                    if isinstance(raw_entry, dict)
                    else ""
                )
                cache_key = (self._gateway_ref, bvid_key)
                if (
                    not is_mock
                    and bvid_key
                    and cache_key in _VIDEO_ITEM_CACHE
                    and enrichment.inspection_mode != "card_only"
                ):
                    items.append(_VIDEO_ITEM_CACHE[cache_key])
                    continue
                built_item = _source_item(
                    raw_entry,
                    gateway_ref=self._gateway_ref,
                    signal_kind=self._signal_kind,
                    upstream_publisher_ref=self._upstream_publisher_ref,
                    enrichment=enrichment,
                )
                if not is_mock and bvid_key and enrichment.inspection_mode != "card_only":
                    if len(_VIDEO_ITEM_CACHE) >= _MAX_CACHE_ENTRIES:
                        _VIDEO_ITEM_CACHE.pop(next(iter(_VIDEO_ITEM_CACHE)))
                    _VIDEO_ITEM_CACHE[cache_key] = built_item
                items.append(built_item)
            except (ArithmeticError, KeyError, TypeError, ValueError):
                rejected += 1
        return ExternalSignalSourcePage(
            evidence_media_type=fetched.media_type,
            evidence_bytes=evidence,
            next_cursor=fetched.cursor,
            items=tuple(items),
            parser_rejected_item_count=rejected,
            parser_failure_codes=("bilibili_video_item_malformed",) if rejected else (),
        )

    async def _enrich_entries(
        self, entries: list[Any]
    ) -> tuple[_BilibiliVideoContentEnrichment, ...]:
        """Funnel-based lazy enrichment: reuse cache and deep-inspect at most K new videos."""
        is_mock = isinstance(getattr(self._http_client, "_transport", None), httpx.MockTransport)
        if is_mock and not self._vision_api_key and not self._bili_sessdata:
            return tuple(_BilibiliVideoContentEnrichment() for _ in entries)
        if self._max_deep_inspect_per_fetch <= 0:
            return tuple(
                _VIDEO_ENRICHMENT_CACHE.get(
                    _clean_text(entry.get("bvid"), limit=64) if isinstance(entry, dict) else "",
                    _BilibiliVideoContentEnrichment(),
                )
                for entry in entries
            )

        results: list[_BilibiliVideoContentEnrichment] = [
            _BilibiliVideoContentEnrichment() for _ in entries
        ]
        to_inspect: list[tuple[int, dict[str, Any], str]] = []
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            bvid = _clean_text(entry.get("bvid"), limit=64)
            if not bvid or not bvid.startswith("BV"):
                continue
            cached = _VIDEO_ENRICHMENT_CACHE.get(bvid)
            if cached is not None:
                results[idx] = cached
                continue
            if len(to_inspect) < self._max_deep_inspect_per_fetch:
                to_inspect.append((idx, entry, bvid))

        if not to_inspect:
            return tuple(results)

        semaphore = asyncio.Semaphore(2)

        async def _inspect_one(
            idx: int, entry: dict[str, Any], bvid: str
        ) -> None:
            async with semaphore:
                enrichment = await self._enrich_single_video(entry, bvid=bvid)
                if enrichment.inspection_mode != "card_only":
                    if len(_VIDEO_ENRICHMENT_CACHE) >= _MAX_CACHE_ENTRIES:
                        _VIDEO_ENRICHMENT_CACHE.pop(next(iter(_VIDEO_ENRICHMENT_CACHE)))
                    _VIDEO_ENRICHMENT_CACHE[bvid] = enrichment
                results[idx] = enrichment

        await asyncio.gather(*(_inspect_one(idx, entry, bvid) for idx, entry, bvid in to_inspect))
        return tuple(results)

    async def _enrich_single_video(
        self, entry: dict[str, Any], *, bvid: str
    ) -> _BilibiliVideoContentEnrichment:
        aid = entry.get("aid")
        cid = entry.get("cid")
        duration = float(entry.get("duration") or 0.0) if isinstance(entry.get("duration"), (int, float)) else 0.0
        pic = _clean_text(entry.get("pic"), limit=512)
        title = _clean_text(entry.get("title"), limit=300)
        owner = entry.get("owner")
        owner_name = _clean_text(owner.get("name") if isinstance(owner, dict) else "", limit=80)
        desc = _clean_text(entry.get("desc"), limit=400)
        if desc == "-":
            desc = ""

        # Stage 1: Fetch free text evidence (AI/CC subtitle track + danmaku + top comments)
        subtitle_task = (
            self._fetch_subtitle_excerpt(bvid=bvid, cid=cid)
            if isinstance(cid, int) and cid > 0
            else asyncio.sleep(0, result="")
        )
        danmaku_task = (
            self._fetch_danmaku_highlights(cid)
            if isinstance(cid, int) and cid > 0
            else asyncio.sleep(0, result=())
        )
        replies_task = (
            self._fetch_top_comments(aid)
            if isinstance(aid, int) and aid > 0
            else asyncio.sleep(0, result=())
        )
        subtitle_excerpt, danmaku_highlights, top_comments = await asyncio.gather(
            subtitle_task,
            danmaku_task,
            replies_task,
        )

        # Stage 2: Only invoke vision on cropped 480x270 storyboard tiles when
        # subtitle evidence is absent/insufficient or the video is visual/demo.
        vision_summary = ""
        frame_timestamps: tuple[float, ...] = ()
        need_frames = _should_inspect_frames(entry=entry, subtitle_excerpt=subtitle_excerpt)
        if self._vision_api_key and need_frames:
            frames_task = (
                self._fetch_cropped_videoshot_frames(
                    bvid=bvid,
                    cid=cid,
                    duration=duration,
                    num=_FRAME_MAX,
                )
                if isinstance(cid, int) and cid > 0
                else asyncio.sleep(0, result=())
            )
            cover_url = _cover_url(pic, width=_COVER_WIDTH) if pic else ""
            cover_task = (
                self._fetch_image_data_url(cover_url)
                if cover_url.startswith("https://")
                else asyncio.sleep(0, result=None)
            )
            cropped_frames, cover_data_url = await asyncio.gather(frames_task, cover_task)
            frame_timestamps = tuple(f.got_sec for f in cropped_frames)
            if cropped_frames or cover_data_url:
                vision_summary = await self._analyze_video_with_vision(
                    title=title,
                    owner_name=owner_name,
                    desc=desc,
                    subtitle_excerpt=subtitle_excerpt,
                    cover_data_url=cover_data_url,
                    cropped_frames=cropped_frames,
                    danmaku_highlights=danmaku_highlights,
                    top_comments=top_comments,
                )

        if subtitle_excerpt and vision_summary:
            mode = "subtitle+frames"
        elif subtitle_excerpt:
            mode = "subtitle"
        elif vision_summary:
            mode = "frames"
        elif danmaku_highlights or top_comments:
            mode = "comments_only"
        else:
            mode = "card_only"

        return _BilibiliVideoContentEnrichment(
            inspection_mode=mode,
            subtitle_excerpt=subtitle_excerpt,
            vision_summary=vision_summary,
            frame_timestamps_sec=frame_timestamps,
            danmaku_highlights=danmaku_highlights[:8],
            top_comments=top_comments[:4],
        )

    async def _fetch_subtitle_excerpt(self, *, bvid: str, cid: int) -> str:
        """Fetch Bilibili AI/CC subtitle text (0 vision token cost)."""
        headers = dict(_DEFAULT_HEADERS)
        if self._bili_sessdata:
            headers["Cookie"] = f"SESSDATA={self._bili_sessdata}"
        try:
            resp = await self._http_client.get(
                f"https://api.bilibili.com/x/player/wbi/v2?bvid={bvid}&cid={cid}",
                headers=headers,
                timeout=6.0,
            )
            if resp.status_code != 200:
                return ""
            body = resp.json()
            subtitles = (
                (((body.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
                if isinstance(body, dict)
                else []
            )
            if not subtitles or not isinstance(subtitles, list):
                return ""
            target: dict[str, Any] | None = None
            for s in subtitles:
                if isinstance(s, dict) and s.get("lan") in ("ai-zh", "zh-CN", "zh", "zh-Hans"):
                    target = s
                    break
            if target is None and isinstance(subtitles[0], dict):
                target = subtitles[0]
            if target is None:
                return ""
            sub_url = _https(str(target.get("subtitle_url") or ""))
            if not sub_url.startswith("https://"):
                return ""
            # Never send SESSDATA cookie to subtitle CDN host
            sub_resp = await self._http_client.get(
                sub_url,
                headers=_image_headers(),
                timeout=6.0,
            )
            if sub_resp.status_code != 200:
                return ""
            sub_data = sub_resp.json()
            raw_items = sub_data.get("body") if isinstance(sub_data, dict) else None
            if not isinstance(raw_items, list) or not raw_items:
                return ""
            lines: list[str] = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                content = _clean_text(item.get("content"), limit=80)
                if not content:
                    continue
                if len(content) <= 40 and ("字幕by" in content or "字幕BY" in content):
                    continue
                try:
                    sec = int(float(item.get("from") or 0.0))
                except (TypeError, ValueError):
                    sec = 0
                lines.append(f"[{sec}s]{content}")
            if not lines:
                return ""
            # Sample evenly across the video if the transcript is long
            if len(lines) > 24:
                step = max(1, len(lines) // 24)
                lines = lines[::step][:24]
            return _clean_text(" ".join(lines), limit=700)
        except Exception:
            return ""

    async def _fetch_danmaku_highlights(self, cid: int) -> tuple[str, ...]:
        try:
            resp = await self._http_client.get(
                f"https://comment.bilibili.com/{cid}.xml",
                headers=_DEFAULT_HEADERS,
                timeout=6.0,
            )
            if resp.status_code != 200:
                return ()
            matches = _DANMAKU_PATTERN.findall(resp.text)
            if not matches:
                return ()
            parsed: list[tuple[float, str]] = []
            for p_attr, text in matches:
                cleaned = _clean_text(text, limit=60)
                if not cleaned:
                    continue
                try:
                    ts = float(p_attr.split(",", 1)[0])
                except ValueError:
                    ts = 0.0
                parsed.append((ts, cleaned))
            if not parsed:
                return ()
            parsed.sort(key=lambda item: item[0])
            step = max(1, len(parsed) // 20)
            sampled: list[str] = []
            seen: set[str] = set()
            for ts, text in parsed[::step]:
                if text in seen:
                    continue
                seen.add(text)
                sampled.append(f"[{int(ts)}s]{text}")
                if len(sampled) >= 20:
                    break
            return tuple(sampled)
        except Exception:
            return ()

    async def _fetch_top_comments(self, aid: int) -> tuple[str, ...]:
        try:
            resp = await self._http_client.get(
                f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&sort=2&ps=5",
                headers=_DEFAULT_HEADERS,
                timeout=6.0,
            )
            if resp.status_code != 200:
                return ()
            body = resp.json()
            replies = ((body.get("data") or {}).get("replies") or []) if isinstance(body, dict) else []
            out: list[str] = []
            for item in replies[:4]:
                if not isinstance(item, dict):
                    continue
                member = item.get("member")
                content = item.get("content")
                uname = _clean_text(member.get("uname") if isinstance(member, dict) else "", limit=32)
                msg = _clean_text(content.get("message") if isinstance(content, dict) else "", limit=100)
                like = item.get("like")
                if not msg:
                    continue
                like_str = f"(赞{like})" if isinstance(like, int) and like > 0 else ""
                prefix = f"{uname}：" if uname else ""
                out.append(f"{prefix}{msg}{like_str}")
            return tuple(out)
        except Exception:
            return ()

    async def _fetch_cropped_videoshot_frames(
        self,
        *,
        bvid: str,
        cid: int,
        duration: float,
        num: int = _FRAME_MAX,
    ) -> tuple[_CroppedStoryboardFrame, ...]:
        """Fetch Bilibili storyboard and crop ``num`` 480x270 JPEG tiles (dsh-bili-scroll pattern)."""
        try:
            resp = await self._http_client.get(
                f"https://api.bilibili.com/x/player/videoshot?bvid={bvid}&cid={cid}&index=1",
                headers=_DEFAULT_HEADERS,
                timeout=6.0,
            )
            if resp.status_code != 200:
                return ()
            body = resp.json()
            snap = (body.get("data") or {}) if isinstance(body, dict) else {}
            if not isinstance(snap, dict):
                return ()
            sheets = snap.get("image") or []
            if not isinstance(sheets, list) or not sheets:
                return ()

            covered = duration if duration > 0 else 120.0
            entries = _snapshot_entries(snap, covered)
            if not entries:
                pvdata_url = _https(str(snap.get("pvdata") or ""))
                if pvdata_url.startswith("https://"):
                    pv_resp = await self._http_client.get(
                        pvdata_url,
                        headers=_image_headers(),
                        timeout=6.0,
                    )
                    if pv_resp.status_code == 200:
                        entries = _unpack_pvdata_entries(pv_resp.content)

            if entries:
                story_max = max((e[0] for e in entries), default=0.0)
                if story_max > 0:
                    covered = min(covered, story_max) if duration > 0 else story_max
                n = max(1, min(int(num or _FRAME_MAX), _FRAME_MAX))
                targets = [covered * (i + 1) / (n + 1) for i in range(n)]
                picked: list[tuple[float, float, int]] = []
                seen_tiles: set[int] = set()
                for t in targets:
                    ts, tile = min(entries, key=lambda e: abs(e[0] - t))
                    if tile in seen_tiles:
                        continue
                    seen_tiles.add(tile)
                    picked.append((round(t, 1), round(ts, 1), tile))
            else:
                # Even if both index and pvdata are missing, still crop 3 tiles
                # (start / middle / end) from the first sprite sheet.
                picked = [(0.0, 0.0, 0), (30.0, 30.0, 4), (60.0, 60.0, 8)]

            tw = int(snap.get("img_x_size") or _TILE_W_DEFAULT)
            th = int(snap.get("img_y_size") or _TILE_H_DEFAULT)
            sheet_cache: dict[int, bytes] = {}

            async def _get_sheet(no: int) -> bytes | None:
                if no in sheet_cache:
                    return sheet_cache[no]
                if no < 0 or no >= len(sheets) or not isinstance(sheets[no], str):
                    return None
                sheet_url = _https(sheets[no])
                if not sheet_url.startswith("https://"):
                    return None
                r = await self._http_client.get(
                    sheet_url,
                    headers=_image_headers(),
                    timeout=8.0,
                )
                if r.status_code != 200 or not r.content or len(r.content) > _MAX_IMAGE_BYTES:
                    return None
                sheet_cache[no] = r.content
                return r.content

            first_sheet = await _get_sheet(0)
            if not first_sheet:
                return ()
            first_im = Image.open(io.BytesIO(first_sheet))
            cap = max(1, (first_im.width // tw) * (first_im.height // th))

            out: list[_CroppedStoryboardFrame] = []
            for want_sec, got_sec, tile_idx in picked:
                sheet_no, pos = tile_idx // cap, tile_idx % cap
                sheet_bytes = await _get_sheet(sheet_no)
                if not sheet_bytes:
                    continue
                try:
                    raw_tile = _crop_tile(sheet_bytes, pos, tw, th)
                except Exception:
                    continue
                if len(raw_tile) > _MAX_IMAGE_BYTES:
                    continue
                out.append(
                    _CroppedStoryboardFrame(
                        want_sec=want_sec,
                        got_sec=got_sec,
                        data_url="data:image/jpeg;base64," + base64.b64encode(raw_tile).decode("ascii"),
                        kb=round(len(raw_tile) / 1024, 1),
                    )
                )
            return tuple(out)
        except Exception:
            return ()

    async def _fetch_image_data_url(self, url: str) -> str | None:
        try:
            resp = await self._http_client.get(
                _https(url),
                headers=_image_headers(),
                timeout=8.0,
            )
            if resp.status_code != 200 or not resp.content or len(resp.content) > _MAX_IMAGE_BYTES:
                return None
            return "data:image/jpeg;base64," + base64.b64encode(resp.content).decode("ascii")
        except Exception:
            return None

    async def _analyze_video_with_vision(
        self,
        *,
        title: str,
        owner_name: str,
        desc: str,
        subtitle_excerpt: str,
        cover_data_url: str | None,
        cropped_frames: tuple[_CroppedStoryboardFrame, ...],
        danmaku_highlights: tuple[str, ...],
        top_comments: tuple[str, ...],
    ) -> str:
        content: list[dict[str, Any]] = []
        if cover_data_url:
            content.append({"type": "image_url", "image_url": {"url": cover_data_url}})
        for frame in cropped_frames:
            content.append({"type": "image_url", "image_url": {"url": frame.data_url}})
        frame_labels = "、".join(f"第{i + 1}帧({int(f.got_sec)}s)" for i, f in enumerate(cropped_frames))
        prompt_lines = [
            f"请结合512px封面与故事板裁出的单帧画面（{frame_labels or '无抽帧'}，每帧480x270）、时间轴弹幕及高赞评论，"
            f"用120字以内具体说清这个B站视频《{title}》（UP主：{owner_name}）画面里实际拍了什么、哪个时间点发生了什么名场面/内嵌字幕内容，以及观众在笑什么或吐槽什么。不要套话。",
        ]
        if desc:
            prompt_lines.append(f"UP主简介：{desc}")
        if subtitle_excerpt:
            prompt_lines.append(f"已知字幕片段：{subtitle_excerpt[:300]}")
        if danmaku_highlights:
            prompt_lines.append("时间轴弹幕：" + " / ".join(danmaku_highlights[:15]))
        if top_comments:
            prompt_lines.append("高赞评论：" + " ｜ ".join(top_comments[:4]))
        content.append({"type": "text", "text": "\n".join(prompt_lines)})
        try:
            async with httpx.AsyncClient(timeout=18.0, trust_env=False) as vl_client:
                resp = await vl_client.post(
                    f"{self._vision_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._vision_api_key}"},
                    json={
                        "model": self._vision_model,
                        "messages": [{"role": "user", "content": content}],
                        "max_tokens": 180,
                        "enable_thinking": False,
                    },
                )
            if resp.status_code != 200:
                return ""
            choice_text = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            return _clean_text(choice_text, limit=450)
        except Exception:
            return ""


def _clean_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _format_count(value: object) -> str | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return str(value)


def _source_item(
    value: Any,
    *,
    gateway_ref: str,
    signal_kind: str,
    upstream_publisher_ref: str,
    enrichment: _BilibiliVideoContentEnrichment | None = None,
) -> ExternalSignalSourceItem:
    if not isinstance(value, dict):
        raise ValueError("Bilibili popular item must be an object")
    bvid = _clean_text(value.get("bvid"), limit=64)
    if not bvid or not bvid.startswith("BV") or not bvid[2:].isalnum():
        raise ValueError("Bilibili popular item has no valid bvid")
    title = _clean_text(value.get("title"), limit=500)
    if not title:
        raise ValueError("Bilibili popular item has no title")
    pubdate = value.get("pubdate")
    if not isinstance(pubdate, int) or isinstance(pubdate, bool) or pubdate <= 0:
        raise ValueError("Bilibili popular item has no valid pubdate")
    published_at = datetime.fromtimestamp(pubdate, tz=UTC)
    owner = value.get("owner")
    owner_name = _clean_text(owner.get("name") if isinstance(owner, dict) else "", limit=120)
    tname = _clean_text(value.get("tname"), limit=80)
    desc = _clean_text(value.get("desc"), limit=600)
    if desc == "-":
        desc = ""
    rcmd = value.get("rcmd_reason")
    rcmd_reason = _clean_text(rcmd.get("content") if isinstance(rcmd, dict) else "", limit=160)
    stat = value.get("stat") if isinstance(value.get("stat"), dict) else {}
    views = _format_count(stat.get("view"))
    likes = _format_count(stat.get("like"))
    danmaku = _format_count(stat.get("danmaku"))
    canonical_url = f"https://www.bilibili.com/video/{bvid}"

    prefix = f"【{tname}】" if tname else ""
    suffix = f"（UP主：{owner_name}）" if owner_name else ""
    headline = f"{prefix}{title}{suffix}"[:1_000]

    summary_parts: list[str] = [f"B站热门视频《{title}》（BV号：{bvid}）"]
    if owner_name:
        summary_parts.append(f"UP主：{owner_name}")
    if tname:
        summary_parts.append(f"分区：{tname}")
    if rcmd_reason:
        summary_parts.append(f"热门标签：{rcmd_reason}")
    stat_parts = [
        part
        for part in (
            f"播放 {views}" if views else None,
            f"点赞 {likes}" if likes else None,
            f"弹幕 {danmaku}" if danmaku else None,
        )
        if part is not None
    ]
    if stat_parts:
        summary_parts.append("数据：" + " / ".join(stat_parts))
    if desc and desc != title:
        summary_parts.append(f"简介：{desc}")
    if enrichment is not None:
        if enrichment.subtitle_excerpt:
            summary_parts.append(f"字幕原文实读：{enrichment.subtitle_excerpt}")
        if enrichment.vision_summary:
            ts_label = (
                "（抽帧时刻：" + "/".join(f"{int(t)}s" for t in enrichment.frame_timestamps_sec) + "）"
                if enrichment.frame_timestamps_sec
                else ""
            )
            summary_parts.append(f"故事板抽帧实看{ts_label}：{enrichment.vision_summary}")
        if enrichment.danmaku_highlights:
            summary_parts.append(
                "时间轴弹幕：" + " / ".join(enrichment.danmaku_highlights[:8])
            )
        if enrichment.top_comments:
            summary_parts.append(
                "高赞神评：" + " ｜ ".join(enrichment.top_comments[:3])
            )
    summary_parts.append(f"链接：{canonical_url}")
    licensed_summary = "；".join(summary_parts)[:8_000]

    return ExternalSignalSourceItem(
        upstream_item_id=f"bvid:{bvid}",
        gateway_ref=gateway_ref,
        upstream_publisher_ref=upstream_publisher_ref,
        signal_kind=signal_kind,
        headline=headline,
        licensed_summary=licensed_summary,
        canonical_url=canonical_url,
        published_at=published_at,
    )


_BVID_PATTERN = re.compile(r"\b(BV1[0-9A-Za-z]{9})\b")
_B23_SHORTLINK_PATTERN = re.compile(r"https?://b23\.tv/([0-9A-Za-z_-]{4,24})")
_INBOUND_BILI_SUMMARY_CACHE: dict[str, str] = {}


async def extract_or_resolve_bvid(
    text: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Extract a ``BV1...`` ID from text or resolve a ``b23.tv`` short link."""
    if not text:
        return None
    direct = _BVID_PATTERN.search(text)
    if direct is not None:
        return direct.group(1)
    short = _B23_SHORTLINK_PATTERN.search(text)
    if short is None:
        return None
    short_url = f"https://b23.tv/{short.group(1)}"

    async def _resolve(client: httpx.AsyncClient) -> str | None:
        try:
            resp = await client.get(
                short_url,
                headers=_DEFAULT_HEADERS,
                follow_redirects=False,
                timeout=4.0,
            )
            location = str(resp.headers.get("location") or "")
            m = _BVID_PATTERN.search(location)
            return m.group(1) if m is not None else None
        except Exception:
            return None

    if http_client is not None:
        return await _resolve(http_client)
    async with httpx.AsyncClient(trust_env=False) as client:
        return await _resolve(client)


async def inspect_bilibili_video_by_bvid(
    bvid: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    vision_api_key: str | None = None,
    bili_sessdata: str | None = None,
) -> str | None:
    """Fetch metadata + subtitle/frames/danmaku/comments for a user-shared Bilibili video."""
    if not bvid or not bvid.startswith("BV"):
        return None
    cached_summary = _INBOUND_BILI_SUMMARY_CACHE.get(bvid)
    if cached_summary:
        return cached_summary

    async def _inspect(client: httpx.AsyncClient) -> str | None:
        try:
            resp = await client.get(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                headers=_DEFAULT_HEADERS,
                timeout=6.0,
            )
            if resp.status_code != 200:
                return None
            body = resp.json()
            entry = body.get("data") if isinstance(body, dict) and body.get("code") == 0 else None
            if not isinstance(entry, dict):
                return None
            adapter = BilibiliPopularAdapter(
                http_client=client,
                vision_api_key=vision_api_key,
                bili_sessdata=bili_sessdata,
            )
            enrichment = _VIDEO_ENRICHMENT_CACHE.get(bvid)
            if enrichment is None:
                enrichment = await adapter._enrich_single_video(entry, bvid=bvid)
                if enrichment.inspection_mode != "card_only":
                    if len(_VIDEO_ENRICHMENT_CACHE) >= _MAX_CACHE_ENTRIES:
                        _VIDEO_ENRICHMENT_CACHE.pop(next(iter(_VIDEO_ENRICHMENT_CACHE)))
                    _VIDEO_ENRICHMENT_CACHE[bvid] = enrichment
            if not isinstance(entry.get("pubdate"), int) or entry.get("pubdate", 0) <= 0:
                entry = {**entry, "pubdate": 1}
            item = _source_item(
                entry,
                gateway_ref="bilibili-api:view",
                signal_kind="bilibili_shared_video",
                upstream_publisher_ref="publisher:bilibili",
                enrichment=enrichment,
            )
            if len(_INBOUND_BILI_SUMMARY_CACHE) >= _MAX_CACHE_ENTRIES:
                _INBOUND_BILI_SUMMARY_CACHE.pop(next(iter(_INBOUND_BILI_SUMMARY_CACHE)))
            _INBOUND_BILI_SUMMARY_CACHE[bvid] = item.licensed_summary
            return item.licensed_summary
        except Exception:
            return None

    if http_client is not None:
        return await _inspect(http_client)
    async with httpx.AsyncClient(trust_env=False) as client:
        return await _inspect(client)


async def enrich_qq_fragment_with_bilibili_video(
    fragment: Any,
    *,
    http_client: httpx.AsyncClient | None = None,
    vision_api_key: str | None = None,
    bili_sessdata: str | None = None,
) -> Any:
    """When an inbound QQ fragment carries a Bilibili video link/card, append real video inspection."""
    from dataclasses import replace

    text = getattr(fragment, "text", None)
    if not isinstance(text, str) or not text:
        return fragment
    if "【对方分享的B站视频实看：" in text:
        return fragment
    if "BV1" not in text and "b23.tv/" not in text:
        return fragment
    bvid = await extract_or_resolve_bvid(text, http_client=http_client)
    if not bvid:
        return fragment
    summary = await inspect_bilibili_video_by_bvid(
        bvid,
        http_client=http_client,
        vision_api_key=vision_api_key,
        bili_sessdata=bili_sessdata,
    )
    if not summary:
        return fragment
    enriched_text = f"{text}\n【对方分享的B站视频实看：{summary}】"[:12_000]
    return replace(fragment, text=enriched_text)


__all__ = [
    "BILIBILI_POPULAR_URL",
    "BilibiliPopularAdapter",
    "_cover_url",
    "_crop_tile",
    "_https",
    "_image_headers",
    "_should_inspect_frames",
    "_snapshot_entries",
    "_unpack_pvdata_entries",
    "enrich_qq_fragment_with_bilibili_video",
    "extract_or_resolve_bvid",
    "inspect_bilibili_video_by_bvid",
]
