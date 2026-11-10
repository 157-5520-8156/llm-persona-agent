"""Tests for bidirectional QQ voice support (Inbound ASR+Tone & Outbound Character TTS)."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import wave

import httpx
import pytest

from companion_daemon.config import Settings
from companion_daemon.onebot_adapter import OneBotReplyTarget
from companion_daemon.world_v2.grounded_review_wire import REASONED_CONTRACT, decode
from companion_daemon.world_v2.present_prompt import _slim_beats
from companion_daemon.world_v2.qq_ingress_policy import normalize_onebot_qq_ingress
from companion_daemon.world_v2.qq_voice import (
    _VOICE_TRANSCRIPT_CACHE,
    SynthesizedVoiceNote,
    clean_spoken_text_for_tts,
    enrich_qq_fragment_with_voice_message,
    extract_voice_tag_and_clean_text,
    recent_delivered_voice_notes,
    resolve_delivered_voice_audio_path,
    synthesize_companion_voice_note,
)


def _sample_wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 8000)
    return buf.getvalue()


def test_slim_beats_accepts_voice_dict_and_cq_voice_prefix() -> None:
    beats = _slim_beats(
        [
            {"voice": "刚下课，在回宿舍路上呢"},
            "[CQ:voice]风还挺凉快的",
            "普通文字气泡",
        ],
        allow_nontext=True,
    )
    assert beats == [
        {"modality": "text", "text": "[CQ:voice]刚下课，在回宿舍路上呢"},
        {"modality": "text", "text": "[CQ:voice]风还挺凉快的"},
        {"modality": "text", "text": "普通文字气泡"},
    ]


def test_extract_voice_tag_and_clean_spoken_text() -> None:
    is_voice, cleaned = extract_voice_tag_and_clean_text("[CQ:voice]（轻声笑）我刚回寝室啦")
    assert is_voice is True
    assert cleaned == "（轻声笑）我刚回寝室啦"
    assert clean_spoken_text_for_tts(cleaned) == "我刚回寝室啦"

    # Real stage directions from production turns 3777 and 3779 must never be read aloud
    assert (
        clean_spoken_text_for_tts(
            "（压低声音，带点困意的嘟囔）那我真把手机扣过去睡了啊……你也赶紧闭眼，别熬太晚，晚安。"
        )
        == "那我真把手机扣过去睡了啊……你也赶紧闭眼，别熬太晚，晚安。"
    )
    assert (
        clean_spoken_text_for_tts("（轻轻笑着）好啦，真睡了，你也早点休息。")
        == "好啦，真睡了，你也早点休息。"
    )
    assert (
        clean_spoken_text_for_tts(
            "（气声很小，拖着一半睡意）刚闭上眼，突然想起明天上午还有古代汉语……得赶紧睡实了，不然明早肯定起不来。"
        )
        == "刚闭上眼，突然想起明天上午还有古代汉语……得赶紧睡实了，不然明早肯定起不来。"
    )

    is_plain, plain_text = extract_voice_tag_and_clean_text("普通打字消息")
    assert is_plain is False
    assert plain_text == "普通打字消息"


def test_grounded_review_wire_losslessly_binds_leading_cq_voice_tag() -> None:
    beat_text = "[CQ:voice]我刚回宿舍，今天风好大"
    raw_review = json.dumps(
        {
            "beat_decisions": [
                {
                    "beat_index": 0,
                    "segments": [
                        {
                            "text": "我刚回宿舍，今天风好大",
                            "rationale": "当前感受与已知活动",
                            "assessment": {"kind": "non_record"},
                        }
                    ],
                    "review_complete": True,
                }
            ]
        },
        ensure_ascii=False,
    )
    decoded = decode(
        raw_review,
        beats=[{"beat_index": 0, "text": beat_text}],
        carrier=REASONED_CONTRACT,
    )
    seg = decoded["beat_decisions"][0]["non_record_expressions"][0]
    assert seg["text"] == beat_text

    cued_beat_text = "[CQ:voice]（轻声笑）我刚回宿舍，今天风好大"
    decoded_cued = decode(
        raw_review,
        beats=[{"beat_index": 0, "text": cued_beat_text}],
        carrier=REASONED_CONTRACT,
    )
    cued_seg = decoded_cued["beat_decisions"][0]["non_record_expressions"][0]
    assert cued_seg["text"] == cued_beat_text


def test_proactive_and_expression_wire_accept_voice_beats() -> None:
    from companion_daemon.world_v2.expression_draft import (
        bind_proactive_expression_wire,
        normalize_expression_draft_wire,
    )

    normalized = normalize_expression_draft_wire(
        {
            "beats": [
                {"voice": "（打了个哈欠）刚忙完呢"},
                {"modality": "voice", "text": "你今天怎么没动静呀"},
            ]
        }
    )
    assert normalized["beats"] == [
        {"modality": "text", "text": "[CQ:voice]（打了个哈欠）刚忙完呢"},
        {"modality": "text", "text": "[CQ:voice]你今天怎么没动静呀"},
    ]

    bound = bind_proactive_expression_wire(
        {
            "timing_choice": "now",
            "beats": [
                {"voice": "（轻声笑）刚走回寝室呢"},
                {"modality": "voice", "text": "今天风还挺凉快的"},
            ],
            "stance": "relaxed",
            "brief_rationale": "share walk back",
        }
    )
    assert bound["beats"] == [
        {"modality": "text", "text": "[CQ:voice]（轻声笑）刚走回寝室呢"},
        {"modality": "text", "text": "[CQ:voice]今天风还挺凉快的"},
    ]


@pytest.mark.asyncio
async def test_inbound_voice_record_enriches_fragment_with_transcript_and_tone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _VOICE_TRANSCRIPT_CACHE.clear()
    monkeypatch.setattr(
        "companion_daemon.world_v2.qq_voice._VOICE_TRANSCRIPT_CACHE_DIR",
        tmp_path / "voice-cache",
    )
    wav_bytes = _sample_wav_bytes()

    async def fake_get_onebot_record(api_url: str, *, file: str, out_format: str = "wav", access_token: str | None = None):
        assert file == "voice_abc.amr"
        assert out_format == "wav"
        return {"base64": base64.b64encode(wav_bytes).decode("ascii")}

    monkeypatch.setattr(
        "companion_daemon.onebot_adapter.get_onebot_record",
        fake_get_onebot_record,
    )

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "原文：今晚去吃小火锅好不好？\n语气：语气轻快期待，背景安静"
                        }
                    }
                ]
            },
        )

    raw_event = {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10001,
        "user_id": 20002,
        "message_id": 8899,
        "time": 1760000000,
        "message": [
            {"type": "record", "data": {"file": "voice_abc.amr"}},
        ],
    }
    fragment = normalize_onebot_qq_ingress(raw_event)
    assert fragment is not None
    assert fragment.text is None
    assert fragment.content_shape == "attachment"

    settings = Settings(
        QWEN_API_KEY="test-qwen-key",
        NAPCAT_API_URL="http://127.0.0.1:3000",
        VOICE_ENABLED=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        enriched = await enrich_qq_fragment_with_voice_message(
            fragment,
            raw_event=raw_event,
            settings=settings,
            http_client=client,
        )
        assert enriched.content_shape == "mixed"
        assert (
            enriched.text
            == "【对方发来一条语音：「今晚去吃小火锅好不好？」（听感与语气：语气轻快期待，背景安静）】"
        )
        assert len(calls) == 1

        # Second call hits cache without extra HTTP calls
        enriched_again = await enrich_qq_fragment_with_voice_message(
            fragment,
            raw_event=raw_event,
            settings=settings,
            http_client=client,
        )
        assert enriched_again.text == enriched.text
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_outbound_tts_synthesis_and_archive_roundtrip(tmp_path: Path) -> None:
    wav_bytes = _sample_wav_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/services/aigc/multimodal-generation/generation"):
            body = json.loads(request.content.decode("utf-8"))
            assert body["model"] == "qwen3-tts-flash"
            assert body["input"]["voice"] == "Cherry"
            assert body["input"]["text"] == "刚回到寝室，脚都走酸了"
            return httpx.Response(
                200,
                json={"output": {"audio": {"url": "https://dashscope.example/audio/out.wav"}}},
            )
        if url == "https://dashscope.example/audio/out.wav":
            return httpx.Response(200, content=wav_bytes)
        return httpx.Response(404)

    settings = Settings(
        QWEN_API_KEY="test-qwen-key",
        VOICE_ENABLED=True,
        VOICE_TTS_MODEL="qwen3-tts-flash",
        VOICE_TTS_VOICE="Cherry",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        note = await synthesize_companion_voice_note(
            "[CQ:voice]（揉眼睛）刚回到寝室，脚都走酸了",
            settings=settings,
            http_client=client,
            delivered_dir=tmp_path,
        )
    assert note is not None
    assert note.spoken_text == "刚回到寝室，脚都走酸了"
    assert note.voice_name == "Cherry"
    assert note.audio_bytes == wav_bytes
    assert note.duration_seconds == 0.5

    recent = recent_delivered_voice_notes(delivered_dir=tmp_path)
    assert len(recent) == 1
    assert recent[0]["voice_id"] == note.voice_id
    assert recent[0]["text"] == "刚回到寝室，脚都走酸了"
    resolved = resolve_delivered_voice_audio_path(note.voice_id, delivered_dir=tmp_path)
    assert resolved is not None and resolved.read_bytes() == wav_bytes


@pytest.mark.asyncio
async def test_onebot_reply_target_sends_record_for_voice_tag_and_falls_back_to_text_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wav_bytes = _sample_wav_bytes()
    sent_payloads: list[object] = []

    async def fake_post(url: str, payload: dict[str, object], access_token: str | None = None):
        sent_payloads.append((url, payload))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 321}}

    monkeypatch.setattr("companion_daemon.onebot_adapter._post", fake_post)

    async def fake_synth(text: str):
        return SynthesizedVoiceNote(
            voice_id="voice-20261028T120000Z-1234567890",
            spoken_text=text,
            voice_name="Cherry",
            model_name="qwen3-tts-flash",
            audio_bytes=wav_bytes,
        )

    monkeypatch.setattr(
        "companion_daemon.world_v2.qq_voice.synthesize_companion_voice_note",
        fake_synth,
    )
    target = OneBotReplyTarget(api_url="http://127.0.0.1:3000", user_id=20002)
    resp = await target.reply(content="[CQ:voice]听得到我说话吗？")
    assert resp["retcode"] == 0
    assert len(sent_payloads) == 1
    _, payload = sent_payloads[0]
    assert isinstance(payload, dict)
    msg = payload["message"]
    assert isinstance(msg, list)
    assert msg[0]["type"] == "record"
    assert str(msg[0]["data"]["file"]).startswith("base64://")

    # Now test graceful fallback to text when synthesis returns None
    sent_payloads.clear()

    async def fake_synth_none(text: str):
        return None

    monkeypatch.setattr(
        "companion_daemon.world_v2.qq_voice.synthesize_companion_voice_note",
        fake_synth_none,
    )
    resp2 = await target.reply(content="[CQ:voice]（轻轻笑着）如果语音合成不可用就自动降级成文字")
    assert resp2["retcode"] == 0
    assert len(sent_payloads) == 1
    _, payload2 = sent_payloads[0]
    assert isinstance(payload2, dict)
    assert payload2["message"] == "如果语音合成不可用就自动降级成文字"


def test_phone_mic_voice_realism_produces_non_stationary_handheld_audio() -> None:
    from companion_daemon.world_v2.qq_voice import apply_phone_mic_voice_realism

    raw_wav = _sample_wav_bytes()
    out_wav = apply_phone_mic_voice_realism(raw_wav)
    assert out_wav.startswith(b"RIFF")
    assert len(out_wav) > len(raw_wav)
    with wave.open(io.BytesIO(out_wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() > 8000


@pytest.mark.asyncio
async def test_doubao_seed_tts_synthesis_when_doubao_key_configured(tmp_path: Path) -> None:
    pcm_chunk = base64.b64encode(b"\x01\x00" * 12000).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
        assert request.headers["X-Api-Key"] == "test-doubao-speech-key"
        assert request.headers["X-Api-Resource-Id"] == "seed-tts-2.0"
        body = json.loads(request.content.decode("utf-8"))
        assert body["req_params"]["speaker"] == "zh_female_vv_uranus_bigtts"
        assert body["req_params"]["text"] == "好啦，真睡了，你也早点休息。"
        stream_line = json.dumps({"code": 0, "data": pcm_chunk}) + "\n"
        return httpx.Response(200, content=stream_line.encode("utf-8"))

    settings = Settings(
        DOUBAO_SPEECH_API_KEY="test-doubao-speech-key",
        DOUBAO_SPEECH_RESOURCE_ID="seed-tts-2.0",
        DOUBAO_SPEECH_VOICE="zh_female_vv_uranus_bigtts",
        VOICE_TTS_VOICE="zh_female_vv_uranus_bigtts",
        VOICE_ENABLED=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        note = await synthesize_companion_voice_note(
            "[CQ:voice]（轻轻笑着）好啦，真睡了，你也早点休息。",
            settings=settings,
            http_client=client,
            delivered_dir=tmp_path,
        )
    assert note is not None
    assert note.spoken_text == "好啦，真睡了，你也早点休息。"
    assert note.model_name == "seed-tts-2.0"
    assert "Vivi 2.0" in note.voice_name
    assert note.audio_bytes.startswith(b"RIFF")


@pytest.mark.asyncio
async def test_doubao_seed_icl_2_0_cloned_voice_includes_model_type_4_and_concise_context_texts(
    tmp_path: Path,
) -> None:
    pcm_chunk = base64.b64encode(b"\x01\x00" * 12000).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
        assert request.headers["X-Api-Resource-Id"] == "seed-icl-2.0"
        body = json.loads(request.content.decode("utf-8"))
        assert body["req_params"]["speaker"] == "S_jvguO05k2"
        additions = json.loads(body["req_params"]["additions"])
        assert additions["model_type"] == 4
        assert additions["disable_markdown_filter"] is True
        assert len(additions["context_texts"]) == 1
        inst = additions["context_texts"][0]
        assert "播音员" not in inst
        assert "停顿" in inst
        stream_line = json.dumps({"code": 0, "data": pcm_chunk}) + "\n"
        return httpx.Response(200, content=stream_line.encode("utf-8"))

    settings = Settings(
        DOUBAO_SPEECH_API_KEY="test-doubao-speech-key",
        DOUBAO_SPEECH_RESOURCE_ID="volc.seedicl.default",
        DOUBAO_SPEECH_VOICE="S_jvguO05k2",
        VOICE_TTS_VOICE="S_jvguO05k2",
        VOICE_ENABLED=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        note = await synthesize_companion_voice_note(
            "[CQ:voice]（略带疲惫轻叹一口气）唉……今天走了一下午，腿都快断了。",
            settings=settings,
            http_client=client,
            delivered_dir=tmp_path,
        )
    assert note is not None
    assert note.spoken_text == "唉……今天走了一下午，腿都快断了。"
    assert "豆包声音复刻2.0原声" in note.voice_name
