"""Bilibili popular video feed source adapter.

Video metadata is untrusted external evidence. This adapter parses public feed
transport payloads; it never decides truth, relevance, character attention, or
whether the character shares a video.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any
from urllib.parse import urlsplit

import httpx

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
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


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
        items: list[ExternalSignalSourceItem] = []
        rejected = 0
        for raw_entry in entries[:limit]:
            try:
                items.append(
                    _source_item(
                        raw_entry,
                        gateway_ref=self._gateway_ref,
                        signal_kind=self._signal_kind,
                        upstream_publisher_ref=self._upstream_publisher_ref,
                    )
                )
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


__all__ = ["BILIBILI_POPULAR_URL", "BilibiliPopularAdapter"]
