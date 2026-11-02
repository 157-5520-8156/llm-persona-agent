"""Bidirectional QQ voice message support (Inbound ASR+Tone & Outbound Character TTS).

Architecture boundaries (per ADR 0010 & AGENTS.md):
- Inbound: When the user sends a OneBot ``record`` (voice) message, this adapter-boundary
  helper resolves the audio via NapCat ``/get_record`` (or URL/file fallback) and uses
  DashScope ``qwen3-omni-flash`` (with ``qwen3-asr-flash`` fallback) to extract both the
  verbatim spoken transcript and brief paralinguistic cues (tone, emotion, background
  sound) into the normalized ``QQIngressFragment.text`` so the character hears what the
  counterpart said and how they sounded on the same turn.
- Outbound: Never uses random dice rolls, keyword triggers, or regex heuristics to force
  voice over text. The character model autonomously chooses when to speak a bubble as a
  voice message (via ``{"voice": "..."}`` or a leading ``[CQ:voice]`` tag) based on her
  current situation (e.g., alone in dorm/walking vs. library/class) and mood. When she
  chooses voice, ``synthesize_companion_voice_note`` synthesizes natural speech via
  DashScope ``qwen3-tts-flash`` using the existing ``QWEN_API_KEY`` and archives a
  playable copy under ``output/voice-delivered/``.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave
from typing import Any, Mapping

import httpx

from companion_daemon.config import Settings, get_settings
from .qq_ingress_policy import onebot_attachment_ref


_LOG = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VOICE_DELIVERED_DIR = _REPO_ROOT / "output" / "voice-delivered"
_VOICE_TRANSCRIPT_CACHE_DIR = _REPO_ROOT / "data" / "attachments" / "voice-transcripts"

_MAX_AUDIO_BYTES = 12 * 1024 * 1024
_MAX_CACHE_ENTRIES = 256
_VOICE_TRANSCRIPT_CACHE: OrderedDict[str, str] = OrderedDict()

_FFMPEG_FALLBACK_PATHS = (
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/Users/geoff/homebrew/Caskroom/miniconda/base/envs/openface-env/bin/ffmpeg",
)

# Matches leading [CQ:voice], [voice], or [语音] tags, optionally preceded by [CQ:reply,id=...]
_LEADING_VOICE_TAG_RE = re.compile(
    r"^\s*(?:\[CQ:reply,id=(?:qq-message:)?[^\],]+\]\s*)?(?:\[CQ:voice\]|\[voice\]|\[语音\])\s*(.+)\Z",
    re.DOTALL | re.IGNORECASE,
)

# Strip non-spoken stage direction brackets like （轻声） or (笑) when synthesizing speech
_STAGE_DIRECTION_RE = re.compile(
    r"[（(][^）)]{0,14}(?:声|笑|叹气|哈欠|语气|小声|嘀咕|停顿|沉默|吸鼻子|揉眼睛)[）)]"
)


def _find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for path in _FFMPEG_FALLBACK_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def extract_voice_tag_and_clean_text(text: str) -> tuple[bool, str]:
    """Return ``(is_voice, cleaned_text)`` for an outbound message bubble.

    If the bubble carries a leading ``[CQ:voice]`` / ``[voice]`` / ``[语音]`` tag,
    returns ``(True, spoken_text)`` with the voice marker stripped.
    """
    if not isinstance(text, str) or not text.strip():
        return False, ""
    match = _LEADING_VOICE_TAG_RE.match(text)
    if match is None:
        return False, text
    remainder = match.group(1).strip()
    if not remainder:
        return False, text
    return True, remainder


def _extract_acting_cue_and_spoken_text(text: str) -> tuple[str, str]:
    """Return ``(acting_cue, spoken_text)`` from an outbound voice bubble."""
    cleaned = re.sub(r"\[CQ:[^\]]+\]", "", text)
    cleaned = re.sub(r"^(?:\[CQ:voice\]|\[voice\]|\[语音\])\s*", "", cleaned, flags=re.IGNORECASE)
    cues = [m.group(0).strip("（）()") for m in _STAGE_DIRECTION_RE.finditer(cleaned)]
    spoken = _STAGE_DIRECTION_RE.sub("", cleaned).strip()
    return ("、".join(cues), spoken or text.strip())


def clean_spoken_text_for_tts(text: str) -> str:
    """Clean stage directions or CQ tags from text before passing to TTS."""
    _, spoken = _extract_acting_cue_and_spoken_text(text)
    return spoken


_FRIENDLY_VOICE_LABELS: dict[str, str] = {
    "longwan_v3": "1号(当前默认)·龙婉v3·细腻温柔真人女声+手机环境音",
    "longanxuan_v3": "2号·龙安宣v3·温和亲切真人女声+手机环境音",
    "longanling_v3": "3号·龙安灵v3·清澈灵动真人女声+手机环境音",
    "longanhuan_v3": "4号·龙安欢v3·轻快陪伴真人女声+手机环境音",
    "longshanshan_v3": "5号·龙姗姗v3·清甜自然真人女声+手机环境音",
}


def _friendly_voice_label(voice_name: str) -> str:
    return _FRIENDLY_VOICE_LABELS.get(voice_name, voice_name)


def _inject_cosyvoice_breath_tags(spoken_text: str, acting_cue: str) -> str:
    """Inject natural ``[breath]`` / ``[laughter]`` paralinguistic tokens for CosyVoice."""
    text = spoken_text.strip()
    if not text or "[breath]" in text or "[laughter]" in text:
        return text
    prefix = "[laughter][breath]" if "笑" in acting_cue else "[breath]"
    # Insert one gentle mid-sentence breath on longer multi-clause bubbles
    clauses = re.split(r"([，。！？；、])", text)
    if len(clauses) >= 5 and len(text) >= 16:
        mid = (len(clauses) // 2) | 1
        clauses = [*clauses[: mid + 1], "[breath]", *clauses[mid + 1 :]]
        text = "".join(clauses)
    return f"{prefix}{text}"


def apply_phone_mic_voice_realism(wav_bytes: bytes) -> bytes:
    """Transform sterile studio TTS audio into a realistic smartphone QQ/WeChat voice recording.

    Applies an FFmpeg acoustic filterchain that:
    1. Adds a 0.11s thumb-press lead-in and 0.18s release tail (``adelay`` + ``apad``).
    2. Filters to a smartphone MEMS microphone + mobile codec band (``220Hz-3600Hz``) to
       strip 8-12kHz digital AI sibilance.
    3. Adds subtle 38ms early bedroom/dorm reflection (``aecho``) and phone AGC compression
       (``compand``) so the voice feels close-mic'd in a physical room.
    4. Mixes a continuous low-level pink + brown room/mic noise floor (``anoisesrc`` + ``amix``)
       so the message never has artificial digital-zero silence.
    5. Resamples through a 12kHz->16kHz mobile voice codec path.
    """
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None or not wav_bytes.startswith(b"RIFF"):
        return wav_bytes
    filter_complex = (
        "[0:a]adelay=110|110,apad=pad_dur=0.18,"
        "highpass=f=220,lowpass=f=3600,"
        "aecho=0.8:0.86:38:0.15,"
        "compand=attacks=0.01:decays=0.15:points=-80/-85|-45/-32|-15/-10|0/-6:gain=2[v];"
        "anoisesrc=r=16000:c=pink:a=0.022,highpass=f=140,lowpass=f=2200[amb];"
        "anoisesrc=r=16000:c=brown:a=0.030,highpass=f=60,lowpass=f=380[hand];"
        "[v][amb][hand]amix=inputs=3:duration=first:dropout_transition=0:weights=1.0 0.28 0.22,"
        "aresample=12000,aresample=16000,volume=1.4[out]"
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as src_f:
            src_f.write(wav_bytes)
            src_path = src_f.name
        dst_path = src_path + ".phone.wav"
        try:
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    src_path,
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[out]",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-f",
                    "wav",
                    dst_path,
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0 and os.path.isfile(dst_path):
                out_bytes = Path(dst_path).read_bytes()
                if out_bytes.startswith(b"RIFF") and len(out_bytes) > 44:
                    return out_bytes
        finally:
            for p in (src_path, dst_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    except Exception:
        _LOG.debug("Phone mic realism filter failed; using raw audio", exc_info=True)
    return wav_bytes


def _pcm24k_to_wav_bytes(pcm_bytes: bytes, *, sample_rate: int = 24000) -> bytes:
    """Wrap raw 16-bit mono little-endian PCM bytes in a standard RIFF WAV container."""
    if pcm_bytes.startswith(b"RIFF"):
        return pcm_bytes
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _wav_duration_seconds(wav_bytes: bytes) -> float | None:
    if not wav_bytes.startswith(b"RIFF"):
        return None
    try:
        import io

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            channels = max(1, wf.getnchannels())
            sampwidth = max(1, wf.getsampwidth())
            bytes_per_frame = channels * sampwidth
            max_possible_frames = max(0, (len(wav_bytes) - 44) // bytes_per_frame)
            if frames <= 0 or frames > max_possible_frames:
                frames = max_possible_frames
            if rate > 0 and frames > 0:
                return round(frames / float(rate), 2)
    except Exception:
        return None
    return None


@dataclass(frozen=True, slots=True)
class SynthesizedVoiceNote:
    """Result of synthesizing one outbound character voice note."""

    voice_id: str
    spoken_text: str
    voice_name: str
    model_name: str
    audio_bytes: bytes
    audio_path: Path | None = None
    duration_seconds: float | None = None


def _persist_delivered_voice_note(
    *,
    spoken_text: str,
    voice_name: str,
    model_name: str,
    audio_bytes: bytes,
    delivered_dir: Path | None = None,
) -> SynthesizedVoiceNote:
    target_dir = delivered_dir or _VOICE_DELIVERED_DIR
    digest = hashlib.sha256(audio_bytes).hexdigest()[:16]
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    voice_id = f"voice-{stamp}-{digest[:10]}"
    duration = _wav_duration_seconds(audio_bytes)
    audio_path: Path | None = None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        audio_filename = f"{voice_id}.wav"
        audio_path = target_dir / audio_filename
        audio_path.write_bytes(audio_bytes)
        meta = {
            "voice_id": voice_id,
            "text": spoken_text,
            "voice": voice_name,
            "model": model_name,
            "created_at": now.isoformat(),
            "audio_filename": audio_filename,
            "duration_seconds": duration,
            "byte_length": len(audio_bytes),
        }
        meta_path = target_dir / f"{voice_id}.meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        _LOG.debug("Failed to persist delivered voice note archive", exc_info=True)
    return SynthesizedVoiceNote(
        voice_id=voice_id,
        spoken_text=spoken_text,
        voice_name=voice_name,
        model_name=model_name,
        audio_bytes=audio_bytes,
        audio_path=audio_path,
        duration_seconds=duration,
    )


def recent_delivered_voice_notes(
    *,
    delivered_dir: Path | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return metadata for the most recently synthesized outbound voice notes."""
    target_dir = delivered_dir or _VOICE_DELIVERED_DIR
    if not target_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for meta_path in sorted(target_dir.glob("*.meta.json"), reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        voice_id = str(meta.get("voice_id") or "").strip()
        audio_filename = str(meta.get("audio_filename") or "").strip()
        if not voice_id or not audio_filename:
            continue
        if not (target_dir / audio_filename).is_file():
            continue
        items.append(
            {
                "voice_id": voice_id,
                "text": str(meta.get("text") or ""),
                "voice": str(meta.get("voice") or "Cherry"),
                "model": str(meta.get("model") or "qwen3-tts-flash"),
                "created_at": meta.get("created_at"),
                "duration_seconds": meta.get("duration_seconds"),
                "audio_url": f"/world-v2/dashboard/voice/{voice_id}/audio",
            }
        )
        if len(items) >= limit:
            break
    return items


def resolve_delivered_voice_audio_path(
    voice_id: str,
    *,
    delivered_dir: Path | None = None,
) -> Path | None:
    """Safely resolve a ``voice_id`` to its archived ``.wav`` file path."""
    if not re.fullmatch(r"voice-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{10}", voice_id):
        return None
    target_dir = (delivered_dir or _VOICE_DELIVERED_DIR).resolve()
    candidate = (target_dir / f"{voice_id}.wav").resolve()
    if candidate.parent != target_dir or not candidate.is_file():
        return None
    return candidate


async def _synthesize_via_omni_acting(
    *,
    spoken_text: str,
    acting_cue: str,
    voice_name: str,
    model_name: str,
    api_key: str,
    base_url: str,
    client: httpx.AsyncClient,
) -> bytes | None:
    """Use ``qwen3-omni-flash`` end-to-end audio generation for natural, non-robotic speech."""
    cue_clause = (
        f"说话时的神态与语气提示：{acting_cue}（只体现在声音情绪、呼吸感与语调里，绝对不要把括号提示词念出来）。"
        if acting_cue
        else "语气自然松弛、像20岁女大学生在微信/QQ上给熟人发私聊语音一样，带一点真实生活气息、轻微呼吸感与自然的停顿起伏，绝对不要像新闻播音员或客服AI那样字正腔圆地端着念。"
    )
    payload = {
        "model": model_name,
        "modalities": ["text", "audio"],
        "audio": {"voice": voice_name, "format": "wav"},
        "stream": True,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是20岁女大学生沈知栀的原声配音演员。"
                    "你的唯一任务是把用户给出的台词【逐字原样】用真实、松弛的日常语音口吻读出来。"
                    f"{cue_clause}"
                    "严格要求：一个字都不要增删或改写，绝对禁止接话或回答台词里的问题，只原样读出给定台词！"
                ),
            },
            {
                "role": "user",
                "content": f"请逐字原样念出这句语音台词，不要多说任何一个字：\n{spoken_text}",
            },
        ],
    }
    pcm_chunks: list[bytes] = []
    async with client.stream(
        "POST",
        f"{base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20.0,
    ) as resp:
        if resp.status_code != 200:
            return None
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: ") or line.strip() == "data: [DONE]":
                continue
            try:
                chunk = json.loads(line[6:])
            except Exception:
                continue
            if "error" in chunk:
                return None
            for choice in chunk.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                audio_obj = delta.get("audio") if isinstance(delta, dict) else None
                if isinstance(audio_obj, dict) and isinstance(audio_obj.get("data"), str):
                    b64_part = audio_obj["data"].strip()
                    if b64_part:
                        pcm_chunks.append(base64.b64decode(b64_part, validate=False))
    raw_pcm = b"".join(pcm_chunks)
    if not raw_pcm:
        return None
    return _pcm24k_to_wav_bytes(raw_pcm, sample_rate=24000)


async def _synthesize_via_cosyvoice_ws(
    *,
    spoken_text: str,
    voice_name: str,
    model_name: str,
    api_key: str,
) -> bytes | None:
    """Synthesize natural speech via DashScope CosyVoice v3/v2 WebSocket API."""
    import uuid
    import websockets

    task_id = uuid.uuid4().hex
    audio_bytes = bytearray()
    async with websockets.connect(
        "wss://dashscope.aliyuncs.com/api-ws/v1/inference/",
        additional_headers={"Authorization": f"bearer {api_key}"},
        proxy=None,
        open_timeout=10,
        close_timeout=5,
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
                    "payload": {
                        "task_group": "audio",
                        "task": "tts",
                        "function": "SpeechSynthesizer",
                        "model": model_name,
                        "parameters": {
                            "text_type": "PlainText",
                            "voice": voice_name,
                            "format": "wav",
                            "sample_rate": 24000,
                        },
                        "input": {},
                    },
                },
                ensure_ascii=False,
            )
        )
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                audio_bytes.extend(msg)
                continue
            data = json.loads(msg)
            event = (data.get("header") or {}).get("event")
            if event == "task-started":
                await ws.send(
                    json.dumps(
                        {
                            "header": {
                                "action": "continue-task",
                                "task_id": task_id,
                                "streaming": "duplex",
                            },
                            "payload": {"input": {"text": spoken_text}},
                        },
                        ensure_ascii=False,
                    )
                )
                await ws.send(
                    json.dumps(
                        {
                            "header": {
                                "action": "finish-task",
                                "task_id": task_id,
                                "streaming": "duplex",
                            },
                            "payload": {"input": {}},
                        },
                        ensure_ascii=False,
                    )
                )
            elif event == "task-finished":
                break
            elif event == "task-failed":
                return None
    return bytes(audio_bytes) if audio_bytes else None


async def _synthesize_via_qwen_tts_http(
    *,
    spoken_text: str,
    voice_name: str,
    model_name: str,
    api_key: str,
    client: httpx.AsyncClient,
) -> bytes | None:
    """Synthesize speech via DashScope ``qwen3-tts-flash`` HTTP endpoint."""
    resp = await client.post(
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model_name,
            "input": {
                "text": spoken_text,
                "voice": voice_name,
            },
        },
        timeout=15.0,
    )
    if resp.status_code != 200:
        _LOG.warning(
            "DashScope TTS synthesis returned status=%s body=%s",
            resp.status_code,
            resp.text[:240],
        )
        return None
    body = resp.json()
    output = body.get("output") if isinstance(body, dict) else None
    audio_info = output.get("audio") if isinstance(output, dict) else None
    if not isinstance(audio_info, dict):
        return None
    inline_data = audio_info.get("data")
    if isinstance(inline_data, str) and inline_data.strip():
        return base64.b64decode(inline_data, validate=False)
    audio_url = str(audio_info.get("url") or "").strip()
    if audio_url.startswith(("http://", "https://")):
        dl = await client.get(audio_url, timeout=15.0)
        if dl.status_code == 200 and dl.content:
            return dl.content
    return None


async def synthesize_companion_voice_note(
    text: str,
    *,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
    delivered_dir: Path | None = None,
) -> SynthesizedVoiceNote | None:
    """Synthesize a spoken Chinese voice note (Omni emotional acting / CosyVoice v3 / Qwen-TTS)."""
    cfg = settings or get_settings()
    if not cfg.voice_enabled:
        return None
    api_key = (cfg.qwen_api_key or "").strip()
    if not api_key:
        return None
    acting_cue, spoken_text = _extract_acting_cue_and_spoken_text(text)
    if not spoken_text:
        return None
    spoken_text = spoken_text[:600]
    model_name = (cfg.voice_tts_model or "qwen3-omni-flash").strip()
    voice_name = (cfg.voice_tts_voice or "Momo").strip()
    base_url = (cfg.qwen_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")

    async def _run(client: httpx.AsyncClient) -> SynthesizedVoiceNote | None:
        audio_bytes: bytes | None = None
        used_model = model_name
        used_voice = voice_name

        if model_name.startswith("cosyvoice") or voice_name.startswith(("long", "cosyvoice-")):
            if voice_name.startswith("cosyvoice-v2-"):
                cosy_model = "cosyvoice-v2"
            elif voice_name.startswith("cosyvoice-v3-"):
                cosy_model = "cosyvoice-v3-flash"
            else:
                cosy_model = model_name if model_name.startswith("cosyvoice") else "cosyvoice-v3-flash"
            cosy_text = _inject_cosyvoice_breath_tags(spoken_text, acting_cue)
            audio_bytes = await _synthesize_via_cosyvoice_ws(
                spoken_text=cosy_text,
                voice_name=voice_name,
                model_name=cosy_model,
                api_key=api_key,
            )
            used_model = cosy_model
            used_voice = _friendly_voice_label(voice_name)
        elif "omni" in model_name:
            audio_bytes = await _synthesize_via_omni_acting(
                spoken_text=spoken_text,
                acting_cue=acting_cue,
                voice_name=voice_name,
                model_name=model_name,
                api_key=api_key,
                base_url=base_url,
                client=client,
            )
            used_voice = _friendly_voice_label(voice_name)
            if not audio_bytes:
                fallback_voice = voice_name if voice_name in {"Cherry", "Serena", "Chelsie", "Ethan"} else "Serena"
                audio_bytes = await _synthesize_via_qwen_tts_http(
                    spoken_text=spoken_text,
                    voice_name=fallback_voice,
                    model_name="qwen3-tts-flash",
                    api_key=api_key,
                    client=client,
                )
                used_model = "qwen3-tts-flash"
                used_voice = fallback_voice
        else:
            audio_bytes = await _synthesize_via_qwen_tts_http(
                spoken_text=spoken_text,
                voice_name=voice_name,
                model_name=model_name,
                api_key=api_key,
                client=client,
            )

        if not audio_bytes:
            return None
        if getattr(cfg, "voice_phone_mic_realism", True) and http_client is None:
            audio_bytes = apply_phone_mic_voice_realism(audio_bytes)
        return _persist_delivered_voice_note(
            spoken_text=spoken_text,
            voice_name=used_voice,
            model_name=used_model,
            audio_bytes=audio_bytes,
            delivered_dir=delivered_dir,
        )

    try:
        if http_client is not None:
            return await _run(http_client)
        async with httpx.AsyncClient(trust_env=False) as client:
            return await _run(client)
    except Exception:
        _LOG.warning("Companion voice note synthesis failed; falling back to text", exc_info=True)
        return None


# -- Inbound Voice Message Understanding (ASR + Paralinguistic Cues) ----------


def _sniff_audio_format(data: bytes) -> str | None:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data.startswith(b"OggS"):
        return "ogg"
    if data.startswith(b"#!AMR"):
        return "amr"
    if b"#!SILK_V3" in data[:16]:
        return "silk"
    return None


def _transcode_to_wav_if_needed(data: bytes) -> tuple[bytes, str]:
    """Ensure audio bytes are in a format DashScope accepts (wav or mp3)."""
    fmt = _sniff_audio_format(data)
    if fmt in {"wav", "mp3"}:
        return data, fmt
    ffmpeg = _find_ffmpeg()
    if ffmpeg is not None:
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{fmt or 'bin'}", delete=False) as src_f:
                src_f.write(data)
                src_path = src_f.name
            dst_path = src_path + ".wav"
            try:
                proc = subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-i",
                        src_path,
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        "-f",
                        "wav",
                        dst_path,
                    ],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                if proc.returncode == 0 and os.path.isfile(dst_path):
                    wav_bytes = Path(dst_path).read_bytes()
                    if wav_bytes.startswith(b"RIFF"):
                        return wav_bytes, "wav"
            finally:
                for p in (src_path, dst_path):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
        except Exception:
            _LOG.debug("ffmpeg audio transcode failed", exc_info=True)
    return data, fmt or "wav"


def _extract_record_segments(event: Mapping[str, Any]) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    if event.get("post_type") != "message" or event.get("message_type") == "group":
        return ()
    segments = event.get("message")
    if not isinstance(segments, list):
        return ()
    found: list[tuple[str, Mapping[str, Any]]] = []
    for segment in segments:
        if not isinstance(segment, Mapping) or str(segment.get("type") or "") != "record":
            continue
        data = segment.get("data")
        data = data if isinstance(data, Mapping) else {}
        found.append((onebot_attachment_ref("record", data), data))
    return tuple(found)


async def _fetch_record_audio_bytes(
    data: Mapping[str, Any],
    *,
    api_url: str,
    access_token: str | None,
    client: httpx.AsyncClient,
) -> bytes | None:
    """Resolve audio bytes for one OneBot ``record`` segment."""
    from companion_daemon.onebot_adapter import get_onebot_record

    file_id = str(data.get("file") or "").strip()
    if file_id and api_url:
        try:
            resolved = await get_onebot_record(
                api_url,
                file=file_id,
                out_format="wav",
                access_token=access_token,
            )
            encoded = resolved.get("base64")
            if isinstance(encoded, str) and encoded.strip():
                raw = base64.b64decode(encoded, validate=False)
                if raw:
                    return raw
            local = str(resolved.get("file") or resolved.get("path") or "").strip()
            if local.startswith("file://"):
                local = local[len("file://") :]
            if local and Path(local).is_file():
                raw = Path(local).read_bytes()
                if raw:
                    return raw
            resolved_url = str(resolved.get("url") or "").strip()
            if resolved_url.startswith(("http://", "https://")):
                resp = await client.get(resolved_url, timeout=12.0)
                if resp.status_code == 200 and resp.content:
                    return resp.content[:_MAX_AUDIO_BYTES]
        except Exception:
            _LOG.debug("OneBot get_record resolution failed; trying direct fallback", exc_info=True)

    for key in ("path", "file"):
        local_candidate = str(data.get(key) or "").strip()
        if local_candidate.startswith("file://"):
            local_candidate = local_candidate[len("file://") :]
        if local_candidate and Path(local_candidate).is_file():
            try:
                raw = Path(local_candidate).read_bytes()
                if raw:
                    return raw[:_MAX_AUDIO_BYTES]
            except OSError:
                pass

    url = str(data.get("url") or "").strip()
    if url.startswith(("http://", "https://")):
        try:
            resp = await client.get(url, timeout=12.0)
            if resp.status_code == 200 and resp.content:
                return resp.content[:_MAX_AUDIO_BYTES]
        except Exception:
            _LOG.debug("Direct record URL download failed", exc_info=True)
    return None


def _parse_omni_voice_understanding(content: str) -> tuple[str, str]:
    """Extract ``(transcript, tone_note)`` from ``qwen3-omni-flash`` output."""
    text = content.strip()
    if not text:
        return "", ""
    # Try JSON first if the model returned a JSON block
    try:
        cleaned_json = text
        if "```" in cleaned_json:
            cleaned_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned_json.strip())
        parsed = json.loads(cleaned_json)
        if isinstance(parsed, dict):
            transcript = str(parsed.get("transcript") or parsed.get("text") or "").strip()
            tone = str(parsed.get("tone") or parsed.get("emotion") or "").strip()
            if transcript:
                return transcript, tone
    except Exception:
        pass
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    transcript = ""
    tone = ""
    for ln in lines:
        m_tr = re.match(r"^(?:[-*•\d.]+\s*)?(?:逐字)?(?:原文|转写|内容|台词)[：:]\s*(.+)$", ln)
        if m_tr and not transcript:
            transcript = m_tr.group(1).strip().strip("「」\"'")
            continue
        m_tn = re.match(r"^(?:[-*•\d.]+\s*)?(?:语气(?:简述|与听感)?|听感(?:与语气)?|情绪|声音)[：:]\s*(.+)$", ln)
        if m_tn and not tone:
            tone = m_tn.group(1).strip().rstrip("。.")
            continue
    if not transcript and lines:
        first = re.sub(r"^(?:[-*•\d.]+\s*)?(?:原文|转写|内容)[：:]\s*", "", lines[0]).strip()
        transcript = first.strip("「」\"'")
        if len(lines) >= 2 and not tone:
            second = re.sub(
                r"^(?:[-*•\d.]+\s*)?(?:语气(?:简述|与听感)?|听感(?:与语气)?|情绪)[：:]\s*",
                "",
                lines[1],
            ).strip()
            tone = second.rstrip("。.")
    return transcript, tone


async def transcribe_and_understand_voice_bytes(
    audio_bytes: bytes,
    *,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Transcribe an inbound voice message and summarize its tone/emotion."""
    cfg = settings or get_settings()
    if not cfg.voice_enabled:
        return None
    api_key = (cfg.qwen_api_key or "").strip()
    if not api_key or not audio_bytes:
        return None
    norm_bytes, fmt = _transcode_to_wav_if_needed(audio_bytes)
    b64 = base64.b64encode(norm_bytes).decode("ascii")
    mime = "audio/mpeg" if fmt == "mp3" else "audio/wav"
    base_url = (cfg.qwen_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    asr_model = (cfg.voice_asr_model or "qwen3-omni-flash").strip()

    async def _call(client: httpx.AsyncClient) -> str | None:
        # 1. Primary: qwen3-omni-flash (transcript + paralinguistic tone/background cue)
        try:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": asr_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_audio",
                                    "input_audio": {
                                        "data": f"data:{mime};base64,{b64}",
                                        "format": fmt if fmt in {"wav", "mp3"} else "wav",
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": (
                                        "请逐字准确转写这段语音说了什么，并用15字以内简述说话人的语气、情绪或环境音。"
                                        "请严格按两行输出：\n原文：<逐字转写>\n语气：<语气与听感简述>"
                                    ),
                                },
                            ],
                        }
                    ],
                },
                timeout=15.0,
            )
            if resp.status_code == 200:
                body = resp.json()
                choices = body.get("choices") if isinstance(body, dict) else None
                if isinstance(choices, list) and choices:
                    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                    content = str((msg or {}).get("content") or "").strip()
                    transcript, tone = _parse_omni_voice_understanding(content)
                    if transcript:
                        if tone:
                            return f"【对方发来一条语音：「{transcript}」（听感与语气：{tone}）】"
                        return f"【对方发来一条语音：「{transcript}」】"
        except Exception:
            _LOG.debug("Primary qwen3-omni-flash voice understanding failed", exc_info=True)

        # 2. Fallback: qwen3-asr-flash (audio-only message with emotion annotation)
        try:
            resp2 = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "qwen3-asr-flash",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_audio",
                                    "input_audio": {
                                        "data": f"data:{mime};base64,{b64}",
                                        "format": fmt if fmt in {"wav", "mp3"} else "wav",
                                    },
                                }
                            ],
                        }
                    ],
                },
                timeout=12.0,
            )
            if resp2.status_code == 200:
                body2 = resp2.json()
                choices2 = body2.get("choices") if isinstance(body2, dict) else None
                if isinstance(choices2, list) and choices2:
                    msg2 = choices2[0].get("message") if isinstance(choices2[0], dict) else None
                    transcript = str((msg2 or {}).get("content") or "").strip()
                    annotations = (msg2 or {}).get("annotations")
                    emotion = ""
                    if isinstance(annotations, list):
                        for ann in annotations:
                            if isinstance(ann, dict) and ann.get("emotion"):
                                emotion = str(ann.get("emotion")).strip()
                                break
                    if transcript:
                        if emotion and emotion != "neutral":
                            return f"【对方发来一条语音：「{transcript}」（情绪听感：{emotion}）】"
                        return f"【对方发来一条语音：「{transcript}」】"
        except Exception:
            _LOG.debug("Fallback qwen3-asr-flash transcription failed", exc_info=True)
        return None

    if http_client is not None:
        return await _call(http_client)
    async with httpx.AsyncClient(trust_env=False) as client:
        return await _call(client)


def _read_cached_transcript(attachment_ref: str) -> str | None:
    cached = _VOICE_TRANSCRIPT_CACHE.get(attachment_ref)
    if cached:
        _VOICE_TRANSCRIPT_CACHE.move_to_end(attachment_ref)
        return cached
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", attachment_ref)[:200]
    cache_file = _VOICE_TRANSCRIPT_CACHE_DIR / f"{safe_name}.json"
    if cache_file.is_file():
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            summary = str(payload.get("summary") or "").strip()
            if summary:
                _VOICE_TRANSCRIPT_CACHE[attachment_ref] = summary
                return summary
        except Exception:
            pass
    return None


def _write_cached_transcript(attachment_ref: str, summary: str) -> None:
    if len(_VOICE_TRANSCRIPT_CACHE) >= _MAX_CACHE_ENTRIES:
        _VOICE_TRANSCRIPT_CACHE.popitem(last=False)
    _VOICE_TRANSCRIPT_CACHE[attachment_ref] = summary
    try:
        _VOICE_TRANSCRIPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", attachment_ref)[:200]
        cache_file = _VOICE_TRANSCRIPT_CACHE_DIR / f"{safe_name}.json"
        cache_file.write_text(
            json.dumps(
                {
                    "attachment_ref": attachment_ref,
                    "summary": summary,
                    "cached_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


async def enrich_qq_fragment_with_voice_message(
    fragment: Any,
    *,
    raw_event: Mapping[str, Any] | None = None,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> Any:
    """When an inbound QQ fragment contains a ``record`` (voice) segment, transcribe it."""
    if raw_event is None:
        return fragment
    cfg = settings or get_settings()
    if not cfg.voice_enabled or not (cfg.qwen_api_key or "").strip():
        return fragment
    existing_text = getattr(fragment, "text", None)
    if isinstance(existing_text, str) and "【对方发来一条语音：" in existing_text:
        return fragment
    records = _extract_record_segments(raw_event)
    if not records:
        return fragment

    async def _process(client: httpx.AsyncClient) -> list[str]:
        summaries: list[str] = []
        for attachment_ref, seg_data in records:
            cached = _read_cached_transcript(attachment_ref)
            if cached:
                summaries.append(cached)
                continue
            audio_bytes = await _fetch_record_audio_bytes(
                seg_data,
                api_url=cfg.napcat_api_url,
                access_token=cfg.napcat_access_token,
                client=client,
            )
            if not audio_bytes:
                continue
            summary = await transcribe_and_understand_voice_bytes(
                audio_bytes,
                settings=cfg,
                http_client=client,
            )
            if summary:
                _write_cached_transcript(attachment_ref, summary)
                summaries.append(summary)
        return summaries

    try:
        if http_client is not None:
            voice_lines = await _process(http_client)
        else:
            async with httpx.AsyncClient(trust_env=False) as client:
                voice_lines = await _process(client)
    except Exception:
        _LOG.warning("Inbound QQ voice enrichment failed; keeping original fragment", exc_info=True)
        return fragment

    if not voice_lines:
        return fragment

    joined_voice = "\n".join(voice_lines)
    if isinstance(existing_text, str) and existing_text.strip():
        enriched_text = f"{existing_text}\n{joined_voice}"[:12_000]
    else:
        enriched_text = joined_voice[:12_000]
    new_shape = (
        "mixed"
        if getattr(fragment, "content_shape", None) == "attachment"
        else getattr(fragment, "content_shape", "text")
    )
    return replace(fragment, text=enriched_text, content_shape=new_shape)


__all__ = [
    "SynthesizedVoiceNote",
    "clean_spoken_text_for_tts",
    "enrich_qq_fragment_with_voice_message",
    "extract_voice_tag_and_clean_text",
    "recent_delivered_voice_notes",
    "resolve_delivered_voice_audio_path",
    "synthesize_companion_voice_note",
    "transcribe_and_understand_voice_bytes",
]
