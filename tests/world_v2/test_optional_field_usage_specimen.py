"""Gate: optional fields must ship with filled usage specimens."""

from __future__ import annotations

from companion_daemon.world_v2.optional_field_usage_specimen import (
    LEGITIMATELY_MOSTLY_EMPTY,
    OPTIONAL_FIELD_SPECS,
    assert_optional_field_usage_specimen_coverage,
)
from companion_daemon.world_v2.present_prompt import (
    SLIM_OPTIONAL_SPECIMEN_KEYS,
    compact_gate_usage_specimens_prompt,
    later_usage_specimen,
    matters_bp_usage_specimen,
    photo_usage_specimen,
    relationship_commitment_usage_specimen,
    waiting_for_usage_specimen,
)


def test_optional_field_usage_specimen_coverage_holds() -> None:
    assert_optional_field_usage_specimen_coverage()


def test_slim_optional_keys_are_registered_or_whitelisted() -> None:
    covered = {item.field_id for item in OPTIONAL_FIELD_SPECS}
    missing = [
        key
        for key in SLIM_OPTIONAL_SPECIMEN_KEYS
        if key not in covered and key not in LEGITIMATELY_MOSTLY_EMPTY
    ]
    assert not missing, f"missing registry entries: {missing}"


def test_compact_gate_usage_prompt_precedes_null_shape_fields() -> None:
    prompt = compact_gate_usage_specimens_prompt()
    usage_pos = prompt.index("RELATIONSHIP DECLARATION USAGE EXAMPLE JSON")
    later_pos = prompt.index('"later":300')
    photo_pos = prompt.index('"photo":true')
    assert usage_pos < later_pos < photo_pos
    assert "不需要时留空或 null" in prompt
    assert "省略是常态" not in prompt


def test_new_usage_specimens_compile() -> None:
    from companion_daemon.world_v2.present_prompt import compile_slim_consider_payload

    for specimen in (
        waiting_for_usage_specimen(),
        later_usage_specimen(),
        photo_usage_specimen(),
        relationship_commitment_usage_specimen(),
        matters_bp_usage_specimen(),
    ):
        compiled = compile_slim_consider_payload(specimen)
        assert compiled is not None
