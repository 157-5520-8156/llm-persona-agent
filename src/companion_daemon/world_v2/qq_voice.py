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
_DEFAULT_RVC_REF_WAV = (
    _REPO_ROOT / "data" / "voice_ref" / "shen_zhizhi_zhenti_clean_ref.wav"
    if (_REPO_ROOT / "data" / "voice_ref" / "shen_zhizhi_zhenti_clean_ref.wav").is_file()
    else _REPO_ROOT / "data" / "voice_ref" / "shen_zhizhi_zhenti_ref.wav"
)
_SEED_VC_GRADIO_BASE = "https://plachta-seed-vc.hf.space/gradio_api"

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

# Strip non-spoken stage direction brackets like （轻声）、（压低声音，带点困意的嘟囔） or (笑) when synthesizing speech
_STAGE_DIRECTION_RE = re.compile(
    r"[（(][^）)\n]{1,80}[）)]|【[^】\n]{1,80}】|\*[^*\n]{1,60}\*"
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


_LEADING_WRITTEN_LAUGH_RE = re.compile(r"^\s*(?:噗嗤|噗哈哈|噗)[，,。、…\s]*")


def _extract_acting_cue_and_spoken_text(text: str) -> tuple[str, str]:
    """Return ``(acting_cue, spoken_text)`` from an outbound voice bubble."""
    cleaned = re.sub(r"\[CQ:[^\]]+\]", "", text)
    cleaned = re.sub(r"^(?:\[CQ:voice\]|\[voice\]|\[语音\])\s*", "", cleaned, flags=re.IGNORECASE)
    cues = [m.group(0).strip("（）()【】* ") for m in _STAGE_DIRECTION_RE.finditer(cleaned)]
    spoken = _STAGE_DIRECTION_RE.sub("", cleaned).strip()
    if _LEADING_WRITTEN_LAUGH_RE.match(spoken):
        spoken = _LEADING_WRITTEN_LAUGH_RE.sub("", spoken, count=1).strip()
        if not any("笑" in c for c in cues):
            cues.append("带着忍俊不禁的轻笑意")
    return ("、".join(c for c in cues if c), spoken)


def clean_spoken_text_for_tts(text: str) -> str:
    """Clean stage directions or CQ tags from text before passing to TTS."""
    _, spoken = _extract_acting_cue_and_spoken_text(text)
    return spoken


_FRIENDLY_VOICE_LABELS: dict[str, str] = {
    "cosyvoice-v3-plus-zhizhip-9fc067564dad42f18084cf5a10aa47ef": "沈知栀真题女生定制音色(v3-plus+RVC)+MEMS/SILK手机声学仿真",
    "zh_female_vv_uranus_bigtts": "豆包Vivi 2.0·超拟真年轻女声+MEMS/SILK手机声学仿真",
    "zh_female_sajiaoxuemei_uranus_bigtts": "豆包撒娇学妹 2.0·超拟真女声+MEMS/SILK手机声学仿真",
    "zh_female_linjianvhai_uranus_bigtts": "豆包邻家女孩 2.0·超拟真女声+MEMS/SILK手机声学仿真",
    "zh_female_tianmeitaozi_uranus_bigtts": "豆包甜美桃子 2.0·超拟真女声+MEMS/SILK手机声学仿真",
    "zh_female_xiaohe_uranus_bigtts": "豆包小何 2.0·超拟真女声+MEMS/SILK手机声学仿真",
    "zh_female_cancan_uranus_bigtts": "豆包灿灿 2.0·超拟真女声+MEMS/SILK手机声学仿真",
    "zh_female_shuangkuaisisi_uranus_bigtts": "豆包爽快思思 2.0·超拟真女声+MEMS/SILK手机声学仿真",
    "longanhuan": "龙安欢(v3-plus旗舰)·20岁社交陪伴女声+MEMS/SILK手机声学仿真",
    "longanhuan_v3": "龙安欢v3·20岁社交陪伴女声+MEMS/SILK手机声学仿真",
    "longfeifei_v3": "龙菲菲v3·20岁甜美松弛陪伴女声+MEMS/SILK手机声学仿真",
    "longyingmu_v3": "龙樱沐v3·温柔细腻女声+MEMS/SILK手机声学仿真",
    "longyingtao_v3": "龙樱桃v3·清甜邻家少女音+MEMS/SILK手机声学仿真",
    "Sunny": "晴儿(Sunny)·甜美自然少女音+MEMS/SILK手机声学仿真",
    "Serena": "苏瑶(Serena)·温柔松弛女声+MEMS/SILK手机声学仿真",
    "Vivian": "十三(Vivian)·鲜活自然20岁女生声线+MEMS/SILK手机声学仿真",
    "Maia": "四月(Maia)·清爽自然大学生女声+MEMS/SILK手机声学仿真",
    "Cherry": "芊悦(Cherry)·阳光亲切自然女声+MEMS/SILK手机声学仿真",
    "Chelsie": "千雪(Chelsie)·轻柔细腻陪伴女声+MEMS/SILK手机声学仿真",
    "Momo": "茉兔(Momo)·软萌撒娇女声+MEMS/SILK手机声学仿真",
    "longwan_v3": "龙婉v3·细腻温柔女声+MEMS/SILK手机声学仿真",
}


def _friendly_voice_label(voice_name: str) -> str:
    return _FRIENDLY_VOICE_LABELS.get(voice_name, voice_name)


def _inject_cosyvoice_breath_tags(spoken_text: str, acting_cue: str) -> str:
    """Keep spoken text clean without prepending artificial ``[breath]`` gasps."""
    text = spoken_text.strip()
    if not text or "[breath]" in text or "[laughter]" in text:
        return text
    if any(k in acting_cue for k in ("笑出声", "轻笑", "扑哧", "咯咯")):
        return f"[laughter]{text}"
    return text


def _cosyvoice_emotion_instruction(model_name: str, voice_name: str, acting_cue: str) -> str | None:
    """Return official CosyVoice v3 emotion instruction for voices that support ``Instruct``."""
    if model_name != "cosyvoice-v3-flash":
        return None
    if voice_name.startswith("cosyvoice-v3-flash-"):
        cue = acting_cue.strip()
        if cue:
            return f"请用年轻女生跟亲近的人发微信语音时{cue}的自然语气说话，语调灵动有起伏。"
        return "请用年轻女生跟亲近的人发微信语音的轻快、温柔、自然语气说话，语调灵动有起伏。"
    if voice_name not in {"longanhuan", "longanhuan_v3"}:
        return None
    if any(k in acting_cue for k in ("笑", "开心", "雀跃", "得意", "轻快")):
        return "你说话的情感是happy。"
    if any(k in acting_cue for k in ("委屈", "难过", "叹气", "失落", "鼻音")):
        return "你说话的情感是sad。"
    if any(k in acting_cue for k in ("生气", "恼", "凶")):
        return "你说话的情感是angry。"
    return "你说话的情感是neutral。"


def _mix_stochastic_handheld_mic_layer(wav_bytes: bytes) -> bytes:
    """Simulate real indoor smartphone MEMS mic aeroacoustics & ASIC/AGC high-frequency edge.

    Based on smartphone MEMS transducer + WeChat/QQ voice message acoustic analysis:
    1. Speech-Correlated Mouth Air-Jet / Plosive Turbulence (no constant fake wind):
       When syllable onsets/plosives (positive envelope attack ``d_env``) hit the bottom
       0.8mm MEMS port at 6-12cm distance, they produce brief low-frequency diaphragm
       pressure puffs (75-210 Hz) and port-mesh vortex friction (1.8-4.8 kHz).
    2. Indoor ASIC/RF High-Frequency Crispy Floor + AGC Release Pumping:
       Smartphone MEMS pre-amp + 5-8kHz Helmholtz cavity resonance + aggressive AGC
       lifts a subtle, slightly harsh high-frequency sibilant/electronic air floor
       (2.8-6.8 kHz + narrowband spectral-gate shimmer) during speech and 180ms AGC
       release tails, while gating down during silence.
    """
    if not wav_bytes.startswith(b"RIFF"):
        return wav_bytes
    try:
        import array
        import io
        import math
        import random

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                return wav_bytes
            rate = wf.getframerate()
            raw_frames = wf.readframes(wf.getnframes())
        samples = array.array("h")
        samples.frombytes(raw_frames)
        n = len(samples)
        if n < 400 or rate <= 0:
            return wav_bytes

        seed = int.from_bytes(hashlib.sha256(raw_frames[:2048]).digest()[:8], "little")
        rng = random.Random(seed)

        fast_env = 0.0
        slow_env = 0.0
        agc_hold = 0.0
        plosive_lp = 0.0
        mesh_lp = 0.0
        asic_lp1 = 0.0
        asic_lp2 = 0.0
        rf_phase1 = rng.uniform(0.0, 2.0 * math.pi)
        rf_phase2 = rng.uniform(0.0, 2.0 * math.pi)
        w_res1 = 2.0 * math.pi * rng.uniform(4350.0, 4850.0) / rate
        w_res2 = 2.0 * math.pi * rng.uniform(5450.0, 6100.0) / rate

        for i in range(n):
            x = samples[i] / 32768.0
            abs_x = abs(x)
            fast_env = fast_env * 0.94 + abs_x * 0.06
            slow_env = slow_env * 0.9965 + abs_x * 0.0035

            # AGC envelope: attacks fast on speech, holds ~240ms release tail, gates down in silence
            if fast_env > agc_hold:
                agc_hold = agc_hold * 0.82 + fast_env * 0.18
            else:
                agc_hold = agc_hold * 0.9995

            # 1. Mouth air-jet / plosive turbulence on syllable onset (speech-correlated only)
            onset = max(0.0, fast_env - slow_env * 1.12)
            plosive_puff = 0.0
            if onset > 0.007:
                w_puff = rng.uniform(-1.0, 1.0)
                plosive_lp = 0.945 * plosive_lp + 0.055 * w_puff
                mesh_lp = 0.62 * mesh_lp + 0.38 * w_puff
                mesh_hi = w_puff - mesh_lp
                puff_gain = min(0.088, (onset - 0.007) * 1.15)
                plosive_puff = puff_gain * (0.68 * plosive_lp + 0.32 * mesh_hi)
            else:
                plosive_lp *= 0.93

            # 2. Indoor MEMS/ASIC high-frequency harsh edge + AGC-pumped spectral shimmer
            w_asic = rng.uniform(-1.0, 1.0)
            asic_lp1 = 0.48 * asic_lp1 + 0.52 * w_asic
            asic_lp2 = 0.82 * asic_lp2 + 0.18 * asic_lp1
            # High-passed 3.0-6.8 kHz crisp thermal/sibilant edge
            hf_crisp = (w_asic - asic_lp1) + 0.50 * (asic_lp1 - asic_lp2)
            # Narrowband MEMS cavity / spectral-gate shimmer ("刺耳的高频毛刺感")
            rf_phase1 += w_res1 + rng.uniform(-0.025, 0.025)
            rf_phase2 += w_res2 + rng.uniform(-0.030, 0.030)
            shimmer = 0.58 * math.sin(rf_phase1) + 0.42 * math.sin(rf_phase2)

            # Active AGC gain lifts harsh HF floor during speech & short breath gaps
            agc_gate = min(1.0, agc_hold * 11.5)
            hf_gain = 0.0015 + 0.0085 * agc_gate
            sibilant_grit = 0.014 * min(0.12, abs_x) * hf_crisp

            total_mod = (
                x
                + plosive_puff
                + hf_gain * (0.74 * hf_crisp + 0.26 * shimmer)
                + sibilant_grit
            )
            mixed = int(total_mod * 32767.0)
            if mixed > 32767:
                mixed = 32767
            elif mixed < -32768:
                mixed = -32768
            samples[i] = mixed

        out_buf = io.BytesIO()
        with wave.open(out_buf, "wb") as out_wf:
            out_wf.setnchannels(1)
            out_wf.setsampwidth(2)
            out_wf.setframerate(rate)
            out_wf.writeframes(samples.tobytes())
        return out_buf.getvalue()
    except Exception:
        _LOG.debug("Stochastic handheld mic layer failed; using input wav", exc_info=True)
        return wav_bytes


def apply_phone_mic_voice_realism(wav_bytes: bytes) -> bytes:
    """Transform studio TTS audio into an authentic smartphone QQ/WeChat voice message.

    1. Randomized thumb-press lead-in (85-140ms) and release tail (130-210ms).
    2. Physical smartphone MEMS transducer response:
       - Sub-bass HPF at 115 Hz + close-mic proximity warmth at 215 Hz (+2.0 dB)
       - High-Q MEMS Helmholtz cavity resonance peak at 5350 Hz (+5.5 dB) and 3450 Hz (+2.8 dB)
         that produces the characteristic crispy/slightly harsh smartphone sibilant edge.
    3. Speech-correlated mouth air-jet plosive turbulence & AGC-pumped high-frequency ASIC edge.
    4. Real low-bitrate SILK-WB / Opus VoIP (14 kbps) encode-decode pass via ``libopus``.
    """
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None or not wav_bytes.startswith(b"RIFF"):
        return wav_bytes
    seed = int.from_bytes(hashlib.sha256(wav_bytes[:1024]).digest()[:4], "little")
    lead_ms = 85 + (seed % 56)
    tail_s = round(0.13 + ((seed >> 8) % 85) / 1000.0, 3)
    filter_complex = (
        f"[0:a]adelay={lead_ms}|{lead_ms},apad=pad_dur={tail_s},"
        "highpass=f=110,lowpass=f=7200,"
        "equalizer=f=210:t=q:w=1.3:g=1.6,"
        "equalizer=f=3450:t=q:w=1.8:g=2.2,"
        "equalizer=f=5350:t=q:w=2.3:g=4.2,"
        "compand=attacks=0.008:decays=0.16:points=-80/-86|-48/-40|-18/-12|0/-3:gain=1.3,"
        "aresample=16000[out]"
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as src_f:
            src_f.write(wav_bytes)
            src_path = src_f.name
        mems_path = src_path + ".mems.wav"
        opus_path = src_path + ".silk.ogg"
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
                    mems_path,
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0 and os.path.isfile(mems_path):
                mems_bytes = Path(mems_path).read_bytes()
                if mems_bytes.startswith(b"RIFF") and len(mems_bytes) > 44:
                    # 1. Encode through 16kbps VoIP libopus (SILK-WB LPC mode) first so voice
                    #    timbre stays natural while acquiring subtle SILK codec texture
                    p_enc = subprocess.run(
                        [
                            ffmpeg,
                            "-y",
                            "-i",
                            mems_path,
                            "-c:a",
                            "libopus",
                            "-b:a",
                            "16k",
                            "-vbr",
                            "constrained",
                            "-application",
                            "voip",
                            "-cutoff",
                            "8000",
                            opus_path,
                        ],
                        capture_output=True,
                        timeout=8,
                        check=False,
                    )
                    if p_enc.returncode == 0 and os.path.isfile(opus_path):
                        p_dec = subprocess.run(
                            [
                                ffmpeg,
                                "-y",
                                "-i",
                                opus_path,
                                "-ac",
                                "1",
                                "-ar",
                                "16000",
                                "-f",
                                "wav",
                                dst_path,
                            ],
                            capture_output=True,
                            timeout=8,
                            check=False,
                        )
                        if p_dec.returncode == 0 and os.path.isfile(dst_path):
                            out_bytes = Path(dst_path).read_bytes()
                            if out_bytes.startswith(b"RIFF") and len(out_bytes) > 44:
                                # 2. Apply mouth air-jet plosive turbulence & AGC-pumped ASIC harsh
                                #    floor AFTER SILK decoding so the codec does not suppress them
                                return _mix_stochastic_handheld_mic_layer(out_bytes)
                    return _mix_stochastic_handheld_mic_layer(mems_bytes)
        finally:
            for p in (src_path, mems_path, opus_path, dst_path):
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
    for cue_to_use in ([acting_cue, ""] if acting_cue else [""]):
        cue_clause = (
            f"说话时的神态与语气提示：{cue_to_use}（只体现在声音情绪、呼吸感与语调里，绝对不要把提示词念出来）。"
            if cue_to_use
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
        transcript_parts: list[str] = []
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
                    if not isinstance(delta, dict):
                        continue
                    if isinstance(delta.get("content"), str):
                        transcript_parts.append(delta["content"])
                    audio_obj = delta.get("audio")
                    if isinstance(audio_obj, dict):
                        if isinstance(audio_obj.get("transcript"), str):
                            transcript_parts.append(audio_obj["transcript"])
                        if isinstance(audio_obj.get("data"), str):
                            b64_part = audio_obj["data"].strip()
                            if b64_part:
                                pcm_chunks.append(base64.b64decode(b64_part, validate=False))
        raw_pcm = b"".join(pcm_chunks)
        if not raw_pcm:
            continue
        transcript = "".join(transcript_parts).strip()
        if cue_to_use and transcript:
            cue_fragments = [
                frag.strip()
                for frag in re.split(r"[，,、；;。！!？?\s]+", cue_to_use)
                if len(frag.strip()) >= 2 and frag.strip() not in spoken_text
            ]
            if any(frag in transcript for frag in cue_fragments):
                _LOG.warning(
                    "Omni TTS leaked acting cue into spoken transcript (%r); retrying without cue",
                    transcript,
                )
                continue
        return _pcm24k_to_wav_bytes(raw_pcm, sample_rate=24000)
    return None


async def _synthesize_via_cosyvoice_ws(
    *,
    spoken_text: str,
    voice_name: str,
    model_name: str,
    api_key: str,
    instruction: str | None = None,
) -> bytes | None:
    """Synthesize natural speech via DashScope CosyVoice v3/v2 WebSocket API."""
    import uuid
    import websockets

    task_id = uuid.uuid4().hex
    audio_bytes = bytearray()
    params: dict[str, Any] = {
        "text_type": "PlainText",
        "voice": voice_name,
        "format": "wav",
        "sample_rate": 24000,
    }
    if voice_name.startswith("cosyvoice-v3-plus-zhizhip"):
        params["rate"] = 1.18
    elif voice_name.startswith("cosyvoice-v3-flash-zhizhif"):
        params["rate"] = 1.14
    if instruction:
        params["instruction"] = instruction
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
                        "parameters": params,
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
    if not audio_bytes:
        return None
    raw_wav = bytes(audio_bytes)
    if raw_wav.startswith(b"RIFF") and len(raw_wav) > 44:
        return _pcm24k_to_wav_bytes(raw_wav[44:], sample_rate=24000)
    return raw_wav


def _build_doubao_context_instruction(acting_cue: str, spoken_text: str = "") -> str:
    """Build a concise Doubao 2.0 console-style vocal acting instruction for ``context_texts``.

    Per Volcengine Doubao Seed-TTS 2.0 / Seed-ICL 2.0 official tuning guidelines:
    - Keep ``context_texts[0]`` concise, concrete, and affirmative (15-35 chars), like a
      director speaking to a voice actor (avoid long negative prompts such as "不要像播音员").
    - Explicitly pair paralinguistic cues (laughter, sigh, pause/breath) with textual hooks
      (``……``, ``——``, ``唉``, ``唔``, ``噗``, ``哈哈``) present in ``spoken_text`` or ``acting_cue``.
    """
    cue = acting_cue.strip().strip("。，、； ")
    text = spoken_text.strip()
    has_pause_hook = ("……" in text) or ("..." in text) or ("——" in text)
    has_laugh_hook = any(k in (cue + text) for k in ("笑", "噗", "哈哈", "嘿嘿", "忍俊不禁", "调侃", "打趣"))
    has_sigh_hook = any(k in (cue + text) for k in ("叹", "唉", "哈啊", "累", "酸", "困", "哈欠", "乏"))
    has_whisper_hook = any(k in cue for k in ("压低", "小声", "悄悄", "气声", "耳语"))

    if cue:
        extras: list[str] = []
        if has_laugh_hook and "笑" not in cue:
            extras.append("带一点自然的轻笑声")
        elif has_sigh_hook and "叹" not in cue and "气" not in cue:
            extras.append("带一点轻微叹气与呼吸感")
        if has_pause_hook and "停顿" not in cue:
            extras.append("注意停顿留白与气口")
        suffix = ("，" + "，".join(extras)) if extras else ""
        if has_whisper_hook:
            return f"你嗓门再小点，用{cue}的语气像说悄悄话一样轻声说{suffix}。"
        return f"请用{cue}的亲昵自然口语语气说话{suffix}。"

    if has_sigh_hook:
        return "请用稍微放缓、略带疲惫和轻微叹气的松弛语气说话，注意句中停顿与气口。"
    if has_laugh_hook:
        return "请用轻松亲昵、带着忍俊不禁轻笑意的温柔语气说话，语调灵动自然。"
    if has_pause_hook:
        return "请用温柔松弛的私聊语音口吻说话，语速稍缓，在省略号和转折处留出自然的呼吸停顿。"
    return "请用温柔松弛、像跟亲近的人发私聊语音一样的自然口语语气说话，注意气口与尾音。"


_LOCAL_SEED_VC_DIR = Path(__file__).resolve().parents[3] / "data" / "seed_vc_engine"


def _sync_local_seed_vc_convert(
    wav_bytes: bytes,
    *,
    ref_wav_path: Path,
) -> bytes | None:
    """Run Stage-2 Seed-VC timbre conversion locally on Apple Silicon M2 MPS if installed."""
    venv_py = _LOCAL_SEED_VC_DIR / ".venv" / "bin" / "python"
    runner_py = _LOCAL_SEED_VC_DIR / "local_convert.py"
    if not (venv_py.is_file() and runner_py.is_file()):
        return None
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as src_f:
        src_f.write(wav_bytes)
        src_p = src_f.name
    out_p = src_p + ".rvc.wav"
    trim_p = src_p + ".trim.wav"
    try:
        env = dict(os.environ)
        env.setdefault("HF_HUB_OFFLINE", "1")
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        proc = subprocess.run(
            [str(venv_py), str(runner_py), src_p, str(ref_wav_path), out_p, "20", "0"],
            cwd=str(_LOCAL_SEED_VC_DIR),
            env=env,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if proc.returncode != 0 or not os.path.isfile(out_p):
            return None
        raw_out = Path(out_p).read_bytes()
        if not raw_out.startswith(b"RIFF"):
            return None
        ffmpeg = _find_ffmpeg()
        if ffmpeg is not None:
            p_trim = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    "0.18",
                    "-i",
                    out_p,
                    "-af",
                    "highpass=f=110,equalizer=f=220:t=q:w=1.1:g=-3.0,equalizer=f=2600:t=q:w=1.2:g=2.2,treble=g=2.0:f=5500,alimiter=limit=0.92",
                    trim_p,
                ],
                capture_output=True,
                timeout=8,
                check=False,
            )
            if p_trim.returncode == 0 and os.path.isfile(trim_p):
                trimmed = Path(trim_p).read_bytes()
                if trimmed.startswith(b"RIFF") and len(trimmed) > 1000:
                    return trimmed
        return raw_out
    finally:
        for p in (src_p, out_p, trim_p):
            try:
                os.unlink(p)
            except OSError:
                pass


def _sync_seed_vc_convert(
    wav_bytes: bytes,
    *,
    acting_cue: str = "",
    ref_wav_path: Path | None = None,
) -> bytes | None:
    """Synchronous Seed-VC (RVC) call (prefers local M2 MPS engine, falls back to Gradio)."""
    import random
    import urllib.request
    import uuid

    ref_path = ref_wav_path or _DEFAULT_RVC_REF_WAV
    if not ref_path.is_file() or not wav_bytes.startswith(b"RIFF"):
        return None
    if os.environ.get("COMPANION_DISABLE_LOCAL_RVC", "").strip() not in {"1", "true", "yes"}:
        local_res = _sync_local_seed_vc_convert(wav_bytes, ref_wav_path=ref_path)
        if local_res:
            return local_res
    f0_cond = True
    client_ip = (
        f"{random.randint(11, 220)}.{random.randint(1, 254)}."
        f"{random.randint(1, 254)}.{random.randint(1, 254)}"
    )

    def _upload(name: str, data: bytes) -> str:
        boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{name}"\r\n'
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            f"{_SEED_VC_GRADIO_BASE}/upload",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Mozilla/5.0",
                "X-Forwarded-For": client_ip,
            },
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            return str(json.loads(r.read().decode("utf-8"))[0])

    ref_remote = _upload(ref_path.name, ref_path.read_bytes())
    src_remote = _upload("source.wav", wav_bytes)
    payload = {
        "data": [
            {"path": src_remote, "meta": {"_type": "gradio.FileData"}},
            {"path": ref_remote, "meta": {"_type": "gradio.FileData"}},
            28,
            1.0,
            0.7,
            f0_cond,
            f0_cond,
            0,
        ]
    }
    req_call = urllib.request.Request(
        f"{_SEED_VC_GRADIO_BASE}/call/predict_1",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "X-Forwarded-For": client_ip,
        },
    )
    with urllib.request.urlopen(req_call, timeout=25) as r:
        ev_id = (json.loads(r.read().decode("utf-8")) or {}).get("event_id")
    if not ev_id:
        return None

    final_url: str | None = None
    req_stream = urllib.request.Request(
        f"{_SEED_VC_GRADIO_BASE}/call/predict_1/{ev_id}",
        headers={"User-Agent": "Mozilla/5.0", "X-Forwarded-For": client_ip},
    )
    with urllib.request.urlopen(req_stream, timeout=90) as rs:
        for raw_line in rs:
            s = raw_line.decode("utf-8", errors="ignore").strip()
            if not s.startswith("data:"):
                continue
            d_str = s[5:].strip()
            if not d_str or d_str == "null":
                continue
            try:
                parsed = json.loads(d_str)
                if (
                    isinstance(parsed, list)
                    and len(parsed) > 1
                    and isinstance(parsed[1], dict)
                    and parsed[1].get("url")
                ):
                    final_url = str(parsed[1]["url"])
            except Exception:
                continue
    if not final_url:
        return None

    with urllib.request.urlopen(
        urllib.request.Request(
            final_url,
            headers={"User-Agent": "Mozilla/5.0", "X-Forwarded-For": client_ip},
        ),
        timeout=25,
    ) as dl:
        raw_content = dl.read()
    if not raw_content.startswith(b"RIFF"):
        return None

    ffmpeg = _find_ffmpeg()
    if ffmpeg is not None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(raw_content)
            raw_p = tf.name
        trim_p = raw_p + ".trim.wav"
        try:
            proc = subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-ss", "0.42", "-i", raw_p, trim_p],
                capture_output=True,
                timeout=8,
                check=False,
            )
            if proc.returncode == 0 and os.path.isfile(trim_p):
                trimmed = Path(trim_p).read_bytes()
                if trimmed.startswith(b"RIFF") and len(trimmed) > 1000:
                    return trimmed
        finally:
            for p in (raw_p, trim_p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    return raw_content


async def _convert_timbre_via_seed_vc(
    wav_bytes: bytes,
    *,
    acting_cue: str = "",
    ref_wav_path: Path | None = None,
) -> bytes | None:
    """Convert synthesized speech timbre to the real reference voice via Seed-VC (RVC)."""
    import asyncio

    try:
        return await asyncio.to_thread(
            _sync_seed_vc_convert,
            wav_bytes,
            acting_cue=acting_cue,
            ref_wav_path=ref_wav_path,
        )
    except Exception:
        _LOG.debug("Seed-VC RVC timbre conversion skipped; using base TTS audio", exc_info=True)
        return None


async def _synthesize_via_doubao_seed_tts(
    *,
    spoken_text: str,
    voice_name: str,
    resource_id: str,
    api_key: str,
    acting_cue: str = "",
    app_id: str | None = None,
    client: httpx.AsyncClient,
) -> bytes | None:
    """Synthesize speech via Volcengine Doubao Seed-TTS 2.0 / Seed-ICL 2.0 V3 streaming API."""
    is_cloned_speaker = voice_name.startswith("S_")
    if is_cloned_speaker:
        candidate_resources = (
            [resource_id, "seed-icl-2.0", "volc.seedicl.default"]
            if (resource_id and resource_id not in {"seed-icl-2.0", "volc.seedicl.default"})
            else ["seed-icl-2.0", "volc.seedicl.default", "volc.megatts.default"]
        )
    else:
        candidate_resources = [resource_id or "seed-tts-2.0"]
    for res_id in candidate_resources:
        pcm_chunks: list[bytes] = []
        headers: dict[str, str] = {
            "X-Api-Resource-Id": res_id,
            "Content-Type": "application/json",
        }
        if app_id:
            headers["X-Api-App-Key"] = app_id
            headers["X-Api-Access-Key"] = api_key
        else:
            headers["X-Api-Key"] = api_key
        req_params: dict[str, Any] = {
            "text": spoken_text,
            "speaker": voice_name,
            "audio_params": {
                "format": "pcm",
                "sample_rate": 24000,
            },
        }
        if res_id in {"seed-tts-2.0", "seed-icl-2.0", "volc.seedicl.default"}:
            additions_payload: dict[str, Any] = {
                "context_texts": [_build_doubao_context_instruction(acting_cue, spoken_text)],
                "disable_markdown_filter": True,
            }
            if is_cloned_speaker or res_id in {"seed-icl-2.0", "volc.seedicl.default"}:
                additions_payload["model_type"] = 4
            req_params["additions"] = json.dumps(
                additions_payload,
                ensure_ascii=False,
            )
        async with client.stream(
            "POST",
            "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
            headers=headers,
            json={"req_params": req_params},
            timeout=18.0,
        ) as resp:
            if resp.status_code != 200:
                _LOG.warning("Doubao TTS (%s) returned status=%s", res_id, resp.status_code)
                continue
            async for line in resp.aiter_lines():
                if not line or not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue
                if chunk.get("code") == 0 and isinstance(chunk.get("data"), str) and chunk["data"]:
                    pcm_chunks.append(base64.b64decode(chunk["data"], validate=False))
        raw_pcm = b"".join(pcm_chunks)
        if raw_pcm:
            return _pcm24k_to_wav_bytes(raw_pcm, sample_rate=24000)
    return None


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
    """Synthesize a spoken Chinese voice note (Doubao Seed-TTS 2.0 / CosyVoice v3-plus / Omni)."""
    cfg = settings or get_settings()
    if not cfg.voice_enabled:
        return None
    doubao_key = (getattr(cfg, "doubao_speech_api_key", None) or "").strip()
    api_key = (cfg.qwen_api_key or "").strip()
    if not doubao_key and not api_key:
        return None
    acting_cue, spoken_text = _extract_acting_cue_and_spoken_text(text)
    if not spoken_text:
        return None
    spoken_text = spoken_text[:600]
    model_name = (cfg.voice_tts_model or "cosyvoice-v3-plus").strip()
    voice_name = (cfg.voice_tts_voice or "longanhuan").strip()
    base_url = (cfg.qwen_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")

    async def _run(client: httpx.AsyncClient) -> SynthesizedVoiceNote | None:
        audio_bytes: bytes | None = None
        used_model = model_name
        used_voice = voice_name

        # 1. Prefer Volcengine Doubao Seed-TTS 2.0 / Seed-ICL 2.0 when DOUBAO_SPEECH_API_KEY is configured
        is_doubao_cloned = False
        if doubao_key and (
            model_name.startswith(("seed-tts", "seed-icl", "doubao-tts", "volc.seedicl", "volc.megatts"))
            or voice_name.endswith("_bigtts")
            or voice_name.startswith("S_")
            or model_name == "cosyvoice-v3-plus"
        ):
            configured_doubao_voice = (getattr(cfg, "doubao_speech_voice", None) or "").strip()
            doubao_voice = (
                configured_doubao_voice
                if configured_doubao_voice.startswith("S_")
                else (
                    voice_name
                    if (voice_name.endswith("_bigtts") or voice_name.startswith("S_"))
                    else (configured_doubao_voice or "S_jvguO05k2")
                )
            )
            doubao_res = getattr(cfg, "doubao_speech_resource_id", None) or (
                "seed-icl-2.0" if doubao_voice.startswith("S_") else "seed-tts-2.0"
            )
            doubao_app_id = (getattr(cfg, "doubao_speech_app_id", None) or "").strip() or None
            audio_bytes = await _synthesize_via_doubao_seed_tts(
                spoken_text=spoken_text,
                voice_name=doubao_voice,
                resource_id=doubao_res,
                api_key=doubao_key,
                acting_cue=acting_cue,
                app_id=doubao_app_id,
                client=client,
            )
            if audio_bytes:
                is_doubao_cloned = doubao_voice.startswith("S_")
                used_model = doubao_res
                used_voice = (
                    "沈知栀·豆包声音复刻2.0原声"
                    if is_doubao_cloned
                    else _friendly_voice_label(doubao_voice)
                )

        # 2. DashScope CosyVoice v3-plus / v3-flash / Omni / Qwen3-TTS
        if not audio_bytes and api_key:
            if model_name.startswith("cosyvoice") or voice_name.startswith(("long", "cosyvoice-")):
                if voice_name.startswith("cosyvoice-v2-"):
                    cosy_model = "cosyvoice-v2"
                elif voice_name.startswith("cosyvoice-v3-plus-"):
                    cosy_model = "cosyvoice-v3-plus"
                elif voice_name.startswith("cosyvoice-v3-"):
                    cosy_model = "cosyvoice-v3-flash"
                elif (
                    model_name == "cosyvoice-v3-plus"
                    and voice_name not in {"longanhuan", "longanyang"}
                    and not voice_name.startswith("cosyvoice-v3-plus-")
                ):
                    cosy_model = "cosyvoice-v3-flash"
                else:
                    cosy_model = model_name if model_name.startswith("cosyvoice") else "cosyvoice-v3-flash"
                cosy_text = _inject_cosyvoice_breath_tags(spoken_text, acting_cue)
                inst = _cosyvoice_emotion_instruction(cosy_model, voice_name, acting_cue)
                audio_bytes = await _synthesize_via_cosyvoice_ws(
                    spoken_text=cosy_text,
                    voice_name=voice_name,
                    model_name=cosy_model,
                    api_key=api_key,
                    instruction=inst,
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
                    fallback_voice = (
                        voice_name
                        if voice_name in {"Cherry", "Serena", "Chelsie", "Ethan", "Sunny", "Kiki"}
                        else "Serena"
                    )
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
        if getattr(cfg, "voice_phone_mic_realism", True) and http_client is None and not is_doubao_cloned:
            rvc_bytes = await _convert_timbre_via_seed_vc(
                audio_bytes,
                acting_cue=acting_cue,
            )
            if rvc_bytes:
                audio_bytes = rvc_bytes
                used_voice = "沈知栀·真人原声RVC声线"
            else:
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
