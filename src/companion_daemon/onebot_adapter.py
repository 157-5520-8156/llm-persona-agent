from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from companion_daemon.models import IncomingMessage, MessageAttachment

logger = logging.getLogger(__name__)

_BILIBILI_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?bilibili\.com/video/(BV1[A-Za-z0-9]{9})(?:[/?#][^\s\u4e00-\u9fff，。！？；：”’）\]》]*)?",
    re.IGNORECASE,
)
_STANDALONE_BV_RE = re.compile(r"(?<![A-Za-z0-9])(BV1[A-Za-z0-9]{9})(?![A-Za-z0-9])")
_BILIBILI_META_CACHE: dict[str, tuple[str, str, str]] = {}
_RECENT_MINIAPP_SENT: dict[tuple[str, str], float] = {}


def _extract_bilibili_bvid_and_clean_text(text: str) -> tuple[str | None, str]:
    """Extract a Bilibili BV ID from outbound text and strip raw URL clutter."""

    if not text:
        return None, text
    url_match = _BILIBILI_URL_RE.search(text)
    if url_match is not None:
        bvid = url_match.group(1)
        cleaned = _BILIBILI_URL_RE.sub("", text)
        # Also remove any duplicate bare BV ID if she wrote both URL and BV ID
        cleaned = re.sub(
            rf"[（(]?\s*{re.escape(bvid)}\s*[)）]?",
            "",
            cleaned,
        )
        cleaned = re.sub(r"(?:链接|网址|地址|指路)\s*[：:]\s*$", "", cleaned.strip())
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" \t\n，,：:；;-—")
        return bvid, cleaned
    bv_match = _STANDALONE_BV_RE.search(text)
    if bv_match is not None:
        bvid = bv_match.group(1)
        cleaned = _STANDALONE_BV_RE.sub("", text)
        cleaned = re.sub(r"[（(]\s*[)）]", "", cleaned)
        cleaned = re.sub(r"(?:BV号|链接|指路)\s*[：:]\s*$", "", cleaned.strip())
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" \t\n，,：:；;-—")
        return bvid, cleaned
    return None, text


async def _fetch_bilibili_card_meta(bvid: str) -> tuple[str, str, str]:
    """Return (title, owner_name, pic_url) for a Bilibili BV ID."""

    cached = _BILIBILI_META_CACHE.get(bvid)
    if cached is not None:
        return cached
    default_pic = "https://i0.hdslb.com/bfs/app/8920e6741fc2808cce5b81bc27abdbda291655d3.png"
    try:
        async with httpx.AsyncClient(
            timeout=3.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
            },
        ) as client:
            resp = await client.get(
                "https://api.bilibili.com/x/web-interface/view",
                params={"bvid": bvid},
            )
            data = resp.json().get("data") if resp.status_code == 200 else None
            if isinstance(data, dict):
                title = str(data.get("title") or "").strip() or f"哔哩哔哩视频 {bvid}"
                owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
                owner_name = str(owner.get("name") or "").strip()
                pic = str(data.get("pic") or "").strip() or default_pic
                if pic.startswith("http://"):
                    pic = "https://" + pic.removeprefix("http://")
                result = (title, owner_name, pic)
                _BILIBILI_META_CACHE[bvid] = result
                return result
    except Exception:
        pass
    return (f"哔哩哔哩视频 {bvid}", "", default_pic)


def _ensure_clickable_bilibili_url(text: str, bvid: str) -> str:
    """Ensure a bare BV ID is expanded into a clickable Bilibili URL when card signing is unavailable."""

    if _BILIBILI_URL_RE.search(text) is not None:
        return text
    canonical_url = f"https://www.bilibili.com/video/{bvid}"
    return _STANDALONE_BV_RE.sub(canonical_url, text, count=1)


_LEADING_CQ_REPLY_RE = re.compile(
    r"^\s*\[CQ:reply,id=(?:qq-message:)?([^\],]+)\]\s*(.+)\Z",
    re.DOTALL,
)


def _normalize_outbound_onebot_message(
    message: str | list[dict[str, Any]],
) -> str | list[dict[str, Any]]:
    """Convert a leading ``[CQ:reply,id=...]`` tag into a native OneBot reply segment."""

    if not isinstance(message, str):
        return message
    match = _LEADING_CQ_REPLY_RE.match(message)
    if match is None:
        return message
    reply_id = match.group(1).strip()
    remainder = match.group(2).strip()
    if not reply_id or not remainder:
        return message
    return [
        {"type": "reply", "data": {"id": reply_id}},
        {"type": "text", "data": {"text": remainder}},
    ]


def _extract_signed_ark_json(response: dict[str, Any] | None) -> str | None:
    """Return a server-signed Ark JSON string from NapCat ``/get_mini_app_ark`` or None.

    Unsigned locally constructed ``com.tencent.miniapp_01`` JSON lacks a
    Tencent-issued ``config.token`` signature and is rejected by QQ clients with
    "发送端版本过低". Only accept payloads signed by ``/get_mini_app_ark``.
    """

    if not isinstance(response, dict):
        return None
    status = str(response.get("status") or "").lower()
    retcode = response.get("retcode")
    if status == "failed" or (retcode is not None and int(str(retcode)) != 0):
        return None
    data = response.get("data")
    raw_ark: str | None = None
    if isinstance(data, str) and data.strip():
        raw_ark = data.strip()
    elif isinstance(data, dict):
        nested = data.get("ark") or data.get("data")
        if isinstance(nested, str) and nested.strip():
            raw_ark = nested.strip()
        elif isinstance(nested, dict):
            raw_ark = json.dumps(nested, ensure_ascii=False)
        elif "app" in data and "meta" in data:
            raw_ark = json.dumps(data, ensure_ascii=False)
    if not raw_ark:
        return None
    try:
        parsed = json.loads(raw_ark)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    config = parsed.get("config")
    token = config.get("token") if isinstance(config, dict) else None
    if not isinstance(token, str) or not token.strip():
        return None
    return raw_ark


@dataclass
class OneBotReplyTarget:
    """Reply target for a local OneBot v11 HTTP API, including NapCat."""

    api_url: str
    user_id: int | None = None
    group_id: int | None = None
    access_token: str | None = None

    async def _request_signed_bilibili_miniapp_ark(self, bvid: str) -> str | None:
        title, owner_name, pic_url = await _fetch_bilibili_card_meta(bvid)
        card_title = f"{title} · UP主：{owner_name}" if owner_name else title
        canonical_url = f"https://www.bilibili.com/video/{bvid}"
        endpoint = f"{self.api_url.rstrip('/')}/get_mini_app_ark"
        resp = await _post(
            endpoint,
            {
                "type": "bili",
                "title": card_title[:120],
                "desc": (owner_name or title)[:120],
                "picUrl": pic_url,
                # Mini-app navigation uses its video page route; browser
                # fallback remains the canonical URL for the same video.
                "jumpUrl": f"pages/video/video?bvid={bvid}",
                "webUrl": canonical_url,
            },
            self.access_token,
        )
        return _extract_signed_ark_json(resp)

    async def reply(self, **kwargs: object) -> dict[str, Any]:
        content = str(kwargs.get("content", ""))
        if not content:
            return {}
        from companion_daemon.world_v2.qq_voice import (
            extract_voice_tag_and_clean_text,
            synthesize_companion_voice_note,
        )

        is_voice, voice_cleaned = extract_voice_tag_and_clean_text(content)
        if is_voice and voice_cleaned:
            try:
                synthesized = await synthesize_companion_voice_note(voice_cleaned)
                if synthesized is not None and synthesized.audio_bytes:
                    voice_resp = await self.send_voice(synthesized.audio_bytes)
                    retcode = voice_resp.get("retcode") if isinstance(voice_resp, dict) else None
                    status = (
                        str(voice_resp.get("status") or "").lower()
                        if isinstance(voice_resp, dict)
                        else ""
                    )
                    if status != "failed" and (retcode is None or int(str(retcode)) == 0):
                        return voice_resp
            except Exception:
                pass
            content = voice_cleaned
        bvid, cleaned_text = _extract_bilibili_bvid_and_clean_text(content)
        if bvid is not None:
            recipient_key = (str(self.user_id or self.group_id or ""), bvid)
            now_mono = time.monotonic()
            last_sent = _RECENT_MINIAPP_SENT.get(recipient_key)
            already_sent_recently = last_sent is not None and (now_mono - last_sent) < 600.0
            card_resp: dict[str, Any] | None = None
            if not already_sent_recently:
                try:
                    ark_json = await self._request_signed_bilibili_miniapp_ark(bvid)
                    if ark_json is not None:
                        card_resp = await self._send_message(
                            [{"type": "json", "data": {"data": ark_json}}]
                        )
                        retcode = card_resp.get("retcode") if isinstance(card_resp, dict) else None
                        status = str(card_resp.get("status") or "").lower() if isinstance(card_resp, dict) else ""
                        if status == "failed" or (retcode is not None and int(str(retcode)) != 0):
                            card_resp = None
                        else:
                            _RECENT_MINIAPP_SENT[recipient_key] = now_mono
                except Exception:
                    card_resp = None
            if card_resp is not None or already_sent_recently:
                if cleaned_text:
                    return await self._send_message(cleaned_text)
                if card_resp is not None:
                    return card_resp
            return await self._send_message(_ensure_clickable_bilibili_url(content, bvid))
        return await self._send_message(content)

    async def send_image(self, image_path: Path) -> dict[str, Any]:
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        return await self._send_message([
            {"type": "image", "data": {"file": f"base64://{b64}"}}
        ])

    async def send_voice(self, audio_bytes: bytes) -> dict[str, Any]:
        """Send one synthesized voice note as a native OneBot ``record`` segment."""
        if not audio_bytes:
            raise ValueError("OneBot send_voice requires non-empty audio bytes")
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        return await self._send_message(
            [{"type": "record", "data": {"file": f"base64://{b64}"}}]
        )

    async def react_with_emoji(self, message_id: str, emoji_id: str) -> dict[str, Any]:
        """Attach a QQ emoji reaction to a received message (NapCat set_msg_emoji_like)."""
        if not message_id or not emoji_id:
            return {}
        endpoint = f"{self.api_url.rstrip('/')}/set_msg_emoji_like"
        payload: dict[str, Any] = {
            "message_id": message_id,
            "emoji_id": emoji_id,
            "set": True,
        }
        return await _post(endpoint, payload, self.access_token)

    async def send_face(self, face_id: str) -> dict[str, Any]:
        """Send one standard OneBot ``face`` segment as a visible sticker beat."""

        if not face_id or not face_id.isdigit():
            raise ValueError("OneBot face id must be numeric")
        return await self._send_message(
            [{"type": "face", "data": {"id": face_id}}]
        )

    async def get_msg(self, message_id: str) -> dict[str, Any]:
        """Fetch one previously sent/received message by its OneBot message id.

        NapCat and most OneBot v11 implementations expose ``get_msg``.  A
        successful response proves the platform durably persisted the message,
        which is the strongest delivery evidence this API family offers.
        """

        if not message_id:
            raise ValueError("OneBot get_msg requires a message id")
        endpoint = f"{self.api_url.rstrip('/')}/get_msg"
        return await _post(endpoint, {"message_id": message_id}, self.access_token)

    async def set_input_status(self, *, event_type: int) -> dict[str, Any]:
        """Set NapCat's private-chat input state for this target.

        ``set_input_status`` is a NapCat extension rather than portable
        OneBot.  Callers expose it only from the NapCat deployment profile.
        """

        if self.user_id is None:
            raise ValueError("input status requires a private OneBot target")
        if event_type not in {0, 1}:
            raise ValueError("input status event type must be 0 or 1")
        endpoint = f"{self.api_url.rstrip('/')}/set_input_status"
        return await _post(
            endpoint,
            {"user_id": str(self.user_id), "event_type": event_type},
            self.access_token,
        )

    async def _send_message(self, message: str | list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": _normalize_outbound_onebot_message(message)}
        if self.group_id:
            payload["group_id"] = self.group_id
            endpoint = f"{self.api_url.rstrip('/')}/send_group_msg"
        elif self.user_id:
            payload["user_id"] = self.user_id
            endpoint = f"{self.api_url.rstrip('/')}/send_private_msg"
        else:
            return {}
        return await _post(endpoint, payload, self.access_token)


async def get_onebot_friend_msg_history(
    api_url: str,
    *,
    user_id: str,
    count: int = 30,
    access_token: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch the most recent private-chat history for one peer.

    ``get_friend_msg_history`` is a NapCat extension (also implemented by
    several other OneBot v11 forks).  ``message_seq`` 0 asks for the newest
    messages.  Callers own filtering and idempotency; this helper only
    returns the provider's raw message objects, newest last.
    """

    if not user_id:
        raise ValueError("friend message history requires a peer user id")
    if not 1 <= count <= 200:
        raise ValueError("friend message history count must be between 1 and 200")
    endpoint = f"{api_url.rstrip('/')}/get_friend_msg_history"
    response = await _post(
        endpoint,
        {"user_id": user_id, "message_seq": 0, "count": count, "reverseOrder": False},
        access_token,
    )
    data = response.get("data")
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, dict)]


async def get_onebot_image(
    api_url: str,
    *,
    file: str,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Resolve one received image segment through the OneBot ``get_image`` API.

    NapCat (and most OneBot v11 forks) return a ``data`` object that may
    carry a re-signed download ``url``, a provider-local ``file`` path, or a
    ``base64`` body depending on the implementation.  The caller owns
    choosing among them; this helper only performs the provider call.
    """

    if not file:
        raise ValueError("OneBot get_image requires the segment file identifier")
    endpoint = f"{api_url.rstrip('/')}/get_image"
    response = await _post(endpoint, {"file": file}, access_token)
    data = response.get("data")
    return data if isinstance(data, dict) else {}


async def get_onebot_record(
    api_url: str,
    *,
    file: str,
    out_format: str = "wav",
    access_token: str | None = None,
) -> dict[str, Any]:
    """Resolve one received voice segment through the OneBot ``get_record`` API.

    NapCat decodes QQ's proprietary SILK/AMR voice format into ``out_format``
    (e.g. ``wav``) and returns a ``data`` object with ``base64``, local ``file``
    path, or ``url``.
    """

    if not file:
        raise ValueError("OneBot get_record requires the segment file identifier")
    endpoint = f"{api_url.rstrip('/')}/get_record"
    response = await _post(
        endpoint,
        {"file": file, "out_format": out_format},
        access_token,
    )
    data = response.get("data")
    return data if isinstance(data, dict) else {}


async def send_onebot_emoji_like(
    api_url: str,
    *,
    message_id: str,
    emoji_id: str,
    access_token: str | None = None,
) -> dict[str, Any]:
    target = OneBotReplyTarget(api_url=api_url, access_token=access_token)
    return await target.react_with_emoji(message_id, emoji_id)


async def send_onebot_image(
    api_url: str,
    *,
    user_id: int | None = None,
    group_id: int | None = None,
    image_path: Path,
    access_token: str | None = None,
) -> dict[str, Any]:
    target = OneBotReplyTarget(
        api_url=api_url,
        user_id=user_id,
        group_id=group_id,
        access_token=access_token,
    )
    return await target.send_image(image_path)


def parse_onebot_event(event: dict[str, Any]) -> IncomingMessage | None:
    """Convert an OneBot v11 event dict to an IncomingMessage, or None."""
    if event.get("post_type") != "message":
        return None

    user_id = event.get("user_id")
    if not user_id:
        return None

    raw_message = event.get("raw_message") or ""
    message_id = str(event.get("message_id") or "")
    segments = event.get("message", [])

    text_parts: list[str] = []
    attachments: list[MessageAttachment] = []
    emoji: list[str] = []
    sticker_kind: str | None = None
    reply_target: str | None = None

    if isinstance(segments, str):
        text_parts.append(segments)
    elif isinstance(segments, list):
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            stype = seg.get("type", "")
            sdata = seg.get("data", {})
            if stype == "text":
                text_parts.append(str(sdata.get("text", "")))
            elif stype == "face":
                face_id = str(sdata.get("id") or "").strip()
                if face_id:
                    emoji.append(f"qq-face:{face_id}")
            elif stype in {"mface", "market_face"}:
                sticker_kind = str(
                    sdata.get("summary") or sdata.get("emoji_id") or "qq-market-face"
                )[:80]
            elif stype == "reply":
                reply_target = str(sdata.get("id") or "").strip()[:160] or None
            elif stype == "image":
                attachments.append(MessageAttachment(
                    kind="image", url=sdata.get("url"),
                    filename=sdata.get("file"), content_type="image/jpeg",
                ))
            elif stype == "record":
                attachments.append(MessageAttachment(
                    kind="audio", url=sdata.get("url"),
                    filename=sdata.get("file"), content_type="audio/amr",
                ))
            elif stype == "video":
                attachments.append(MessageAttachment(
                    kind="video", url=sdata.get("url"), filename=sdata.get("file"),
                ))
            elif stype == "file":
                attachments.append(MessageAttachment(
                    kind="file", url=sdata.get("url"),
                    filename=sdata.get("file"), size=sdata.get("size"),
                ))

    text = raw_message if raw_message else "".join(text_parts)
    if not text and not attachments and not emoji and not sticker_kind:
        return None

    channel_id = str(event.get("group_id") or "") if event.get("message_type") == "group" else None

    return IncomingMessage(
        platform="qq",
        platform_user_id=str(user_id),
        text=text.strip(),
        channel_id=channel_id or None,
        message_id=message_id,
        attachments=attachments,
        emoji=emoji[:16],
        sticker_kind=sticker_kind,
        reply_target=reply_target,
    )


def event_token_is_valid(
    expected_token: str | None,
    *,
    authorization: str | None,
    x_signature: str | None,
) -> bool:
    """Accept common OneBot token header styles."""
    if not expected_token:
        return True
    accepted_authorizations = {
        expected_token,
        f"Bearer {expected_token}",
        f"Token {expected_token}",
    }
    return authorization in accepted_authorizations or x_signature == expected_token


async def _post(url: str, payload: dict[str, Any], token: str | None) -> dict[str, Any]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
    return dict(resp.json())
