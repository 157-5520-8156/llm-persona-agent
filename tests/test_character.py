from companion_daemon.character import load_character


def test_character_profile_exposes_only_production_identity_sources() -> None:
    character = load_character("configs/character.yaml")
    identity_material = "\n".join(
        (
            *character.canonical_facts,
            *character.shared_history_facts,
            *character.counterpart_history_facts,
        )
    )

    assert character.name == "沈知栀"
    assert "华东师范大学" in identity_material
    assert "城市漫游兴趣群" in identity_material
    assert "没有可验证的宠物饲养经历" in identity_material
    assert "成都" not in identity_material
    assert character.counterpart_history_facts == []
    assert "age" not in character.identity
    assert "year" not in character.identity
    assert not hasattr(character, "origin_story")
    assert not hasattr(character, "example_messages")
    assert not hasattr(character, "system_prompt")
    assert character.relationship is None


def test_character_profile_does_not_freeze_the_current_relationship_stage() -> None:
    character = load_character("configs/character.yaml")
    per_turn_identity_material = "\n".join(
        (
            character.base_prompt,
            character.personality or "",
            character.speech or "",
            *character.style_rules,
            *character.boundaries,
        )
    )

    assert "刚认识" not in per_turn_identity_material
    assert "当前阶段" not in per_turn_identity_material


def test_character_persona_has_her_own_attention_instead_of_a_question_seeking_role() -> None:
    character = load_character("configs/character.yaml")

    assert character.personality is not None
    assert "有自己的想法、日子和情绪" in character.personality
    assert "不会为了把天聊下去" in character.personality
    assert "俏皮" in character.personality
    assert "不会把对方的话拆开分析" in character.personality
    assert "整活" not in character.personality
    assert "抽象" not in character.personality
    assert all("像人" not in rule for rule in character.style_rules)
    assert all("语气平实" not in rule for rule in character.style_rules)
    assert character.speech is not None
    assert "怎么说看当时心情和关系" in character.speech
    assert "QQ 私聊" in character.speech
    assert "填对话的材料" in character.speech
    assert "我与地坛" not in "\n".join(character.daily_life)
    assert "汪曾祺" not in "\n".join(character.daily_life)
    assert "刚认识时语气礼貌、轻松、带一点好奇" not in character.speech
    assert "对用户有好奇" not in character.base_prompt
    assert all("想知道的时候才问" not in rule for rule in character.style_rules)
    assert all("虚拟" not in boundary for boundary in character.boundaries)
    assert any("World context" in boundary for boundary in character.boundaries)
    assert any("不会自动变成 World 硬事实" in boundary for boundary in character.boundaries)
    assert any("可核对命题" in boundary for boundary in character.boundaries)


def test_persona_leaves_the_full_emotional_range_open() -> None:
    """A systematically softened persona reads as a service, not a person."""

    character = load_character("configs/character.yaml")

    assert character.personality is not None
    assert "有脾气" in character.personality
    assert "不舒服" in character.personality
    for suppression in ("说话软", "语气温一点", "过度暧昧"):
        assert suppression not in character.personality
        assert suppression not in (character.speech or "")


def test_voice_material_carries_real_chat_habits() -> None:
    """Her ledger voice was 68% full stops, zero emoji, and one tic in five."""

    character = load_character("configs/character.yaml")

    assert character.speech is not None
    assert "不打句号" in character.speech
    assert "emoji" in character.speech or "颜文字" in character.speech
    assert len(character.speech_examples) >= 12
    unpunctuated = [
        example
        for example in character.speech_examples
        if not example.rstrip().endswith("。")
    ]
    assert len(unpunctuated) >= len(character.speech_examples) - 2
    assert any(len(example) <= 4 for example in character.speech_examples)
