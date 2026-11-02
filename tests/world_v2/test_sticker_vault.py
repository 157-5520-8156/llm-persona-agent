from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from companion_daemon.config import Settings
from companion_daemon.qq_delivery import QQDelivery
from companion_daemon.world_v2.expression_draft import (
    ExpressionBeatDraftChoice,
    ExpressionDraft,
    materialize_expression_plan_beats,
    qq_expression_capabilities,
    validate_expression_draft_capabilities,
)
from companion_daemon.world_v2.expression_payload_contract import (
    validate_materialized_expression_payload,
)
from companion_daemon.world_v2.sticker_vault import (
    BUILTIN_MEME_ENTRIES,
    ensure_sticker_vault_ready,
    is_valid_sticker_vault_id,
    resolve_sticker_vault_image_path,
    sticker_vault_option_tuples,
)


def test_sticker_vault_renders_curated_everyday_cat_memes(tmp_path: Path) -> None:
    vault_dir = tmp_path / "sticker_vault"
    entries = ensure_sticker_vault_ready(vault_dir)
    assert len(entries) >= len(BUILTIN_MEME_ENTRIES)

    labels_by_id = {entry.option_id: entry.label for entry in entries}
    expected_labels = {
        "meme:cat_peek": "猫猫探头",
        "meme:cat_eyeroll": "猫猫无语翻白眼",
        "meme:cat_slump": "猫猫瘫倒",
        "meme:cat_sigh": "猫猫叹气",
        "meme:cat_smug": "猫猫偷笑",
        "meme:cat_wronged": "猫猫委屈",
        "meme:cat_question": "猫猫问号",
    }
    for option_id, required_prefix in expected_labels.items():
        assert option_id in labels_by_id
        assert labels_by_id[option_id].startswith(required_prefix)
        assert is_valid_sticker_vault_id(option_id, vault_dir=vault_dir)
        image_path = resolve_sticker_vault_image_path(option_id, vault_dir=vault_dir)
        assert image_path is not None and image_path.exists()
        with Image.open(image_path) as img:
            assert img.format == "PNG"
            assert img.size == (360, 360)


def test_sticker_vault_capabilities_and_materialized_payload_validation() -> None:
    ensure_sticker_vault_ready()
    capabilities = qq_expression_capabilities("napcat", include_sticker_vault=True)
    sticker_ids = {item.option_id for item in capabilities.sticker_options}

    for option_id, _ in sticker_vault_option_tuples():
        assert option_id in sticker_ids
        draft = ExpressionDraft(
            timing_choice="now",
            beats=(
                ExpressionBeatDraftChoice(modality="sticker", sticker_id=option_id),
                ExpressionBeatDraftChoice(modality="text", text="刚才白跑一趟"),
            ),
            stance="顺手发个猫猫表情吐槽",
            brief_rationale="用表情包配合文字表达当下状态",
        )
        validate_expression_draft_capabilities(
            draft=draft,
            capabilities=capabilities,
            provider_message_id="msg-100",
        )
        materialized = materialize_expression_plan_beats(
            draft=draft,
            identity="test-plan",
            namespace="inbound",
            change_id="change:1",
            target="qq:private:2759284998",
            provider_message_id="msg-100",
            effective_windows=(None, None),
        )
        sticker_beat = materialized.beat_values[0]
        sticker_intent = materialized.intents[0]
        assert sticker_intent.kind == "sticker"
        validate_materialized_expression_payload(
            action_kind=sticker_intent.kind,
            content_type=str(sticker_beat["content_type"]),
            body=str(sticker_beat["inline_text"]),
            expected_provider_message_id="msg-100",
        )


@pytest.mark.asyncio
async def test_qq_delivery_sends_sticker_vault_image_via_onebot_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    vault_dir = tmp_path / "sticker_vault"
    ensure_sticker_vault_ready(vault_dir)
    monkeypatch.setattr(
        "companion_daemon.world_v2.sticker_vault._DEFAULT_VAULT_DIR",
        vault_dir,
    )

    settings = Settings(qq_adapter="napcat", primary_user_id="2759284998")
    delivery = QQDelivery(settings)
    fake_target = MagicMock()
    fake_target.send_image = AsyncMock(return_value={"status": "ok", "data": {"message_id": "msg-sticker-1"}})
    fake_target.send_face = AsyncMock(return_value={"status": "ok", "data": {"message_id": "msg-face-1"}})
    monkeypatch.setattr(delivery, "_onebot_target", lambda _recipient_id: fake_target)

    resp = await delivery.send_sticker("2759284998", sticker_id="meme:cat_peek")
    assert resp == {"status": "ok", "data": {"message_id": "msg-sticker-1"}}
    assert fake_target.send_face.await_count == 0
    fake_target.send_image.assert_awaited_once()
    sent_path = fake_target.send_image.await_args.args[0]
    assert isinstance(sent_path, Path)
    assert sent_path.name == "cat_peek.png"
    assert sent_path.exists()
