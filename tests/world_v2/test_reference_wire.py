from copy import deepcopy
import hashlib

import pytest

from companion_daemon.world_v2.reference_wire import (
    FIELD, expand_reference_values, expand_reference_view, pack_reference_view,
    prepare_reference_view, reference_schema,
)


def _ref(name):
    return "event:" + name + ":" + hashlib.sha256(name.encode()).hexdigest()


def test_reference_wire_round_trip_keeps_all_facts_and_order():
    first, second = _ref("first"), _ref("second")
    original = {"facts": [{"source_ref": first, "text": "她尝试了，尚未完成。"}] * 4,
                "sources": [first, second, first, second], "privacy": "personal",
                "state": {"integer": 0, "boolean": False, "empty": None}}
    before = deepcopy(original)
    packed = pack_reference_view(original)
    assert FIELD in packed
    assert original == before
    assert expand_reference_view(packed) == original
    aliases = packed[FIELD]["entries"]
    alias = next(k for k, v in aliases.items() if v == first)
    assert expand_reference_values({"source_refs": [alias, second]}, packed[FIELD]) == {
        "source_refs": [first, second],
    }


def test_reference_aliases_do_not_renumber_when_another_reference_is_added():
    first = pack_reference_view({"refs": [_ref("a")] * 12})[FIELD]["entries"]
    second = pack_reference_view({"refs": [_ref("b")] * 12 + [_ref("a")] * 12})[FIELD]["entries"]
    assert all(second[alias] == value for alias, value in first.items())


def test_reference_wire_does_not_alias_prose_or_unique_values():
    prose = "这是一段很长的中文材料。" * 30
    original = {"prose": [prose, prose], "unique": _ref("once")}
    assert pack_reference_view(original) == original


def test_literal_alias_prefix_is_not_mistaken_for_a_reference():
    original = {"text": "@r:12345678", "refs": [_ref("a")] * 12}
    packed = pack_reference_view(original)
    assert packed[FIELD]["prefix"] != "@r:"
    assert expand_reference_view(packed) == original


def test_modified_or_unknown_reference_dictionary_fails_closed():
    packed = pack_reference_view({"refs": [_ref("a")] * 12})
    with pytest.raises(ValueError, match="unresolved"):
        expand_reference_values({"source": "@r:00000000"}, packed[FIELD])
    broken = deepcopy(packed)
    alias = next(iter(broken[FIELD]["entries"]))
    broken[FIELD]["entries"][alias] = _ref("changed")
    with pytest.raises(ValueError, match="exact identifier"):
        expand_reference_view(broken)


def test_legacy_payloads_are_not_upgraded():
    legacy = {"refs": [_ref("a")], "text": "原样保留"}
    assert expand_reference_view(legacy) == legacy
    assert expand_reference_values(legacy, None) == legacy


def test_host_bindings_are_not_sent_and_literal_text_is_never_encoded():
    ref = _ref("exact")
    original = {"facts": [{"source_ref": _ref(str(i)), "text": ref, "value": {"hash": "a" * 64}} for i in range(20)]}
    view, bindings = prepare_reference_view(original)
    assert "entries" not in view[FIELD]
    assert view["facts"][0]["text"] == ref
    assert view["facts"][0]["value"]["hash"] == "a" * 64
    assert view["facts"][0]["source_ref"].startswith(bindings["prefix"])
    assert expand_reference_view(view, bindings) == original
    with pytest.raises(ValueError, match="pinned request"):
        expand_reference_view(view)
    changed = deepcopy(bindings)
    changed["entries"].pop(next(iter(changed["entries"])))
    with pytest.raises(ValueError, match="pinned request"):
        expand_reference_view(view, changed)


def test_reference_schema_enum_and_canonical_round_trip():
    from jsonschema import Draft202012Validator

    values = [_ref(str(i)) for i in range(20)]
    view, bindings = prepare_reference_view({"source_refs": values})
    original = {"type": "string", "enum": values, "pattern": "^event:"}
    encoded = reference_schema(original, bindings)
    alias = view["source_refs"][0]
    Draft202012Validator(encoded).validate(alias)
    restored = expand_reference_values(alias, bindings)
    Draft202012Validator(original).validate(restored)
    assert restored == values[0]


def test_same_pin_correction_keeps_aliases_and_rejected_bytes_literal():
    original = {"source_refs": [_ref(str(i)) for i in range(20)]}
    first, bindings = prepare_reference_view(original)
    alias = first["source_refs"][0]
    correction = {"rejected_raw": '{"source_ref":"' + alias + '"}', "rejected_source": alias}
    corrected, corrected_bindings = prepare_reference_view({**original, "correction": correction})
    assert corrected_bindings == bindings
    assert corrected["source_refs"] == first["source_refs"]
    assert corrected["correction"] == correction
    assert expand_reference_view(corrected, bindings) == {**original, "correction": correction}


def test_request_local_ordinals_keep_schema_prefix_without_reusing_authority():
    before = {"source_refs": [_ref(f"old-{i}") for i in range(20)]}
    after = {"source_refs": [_ref(f"new-{i}") for i in range(20)]}
    first, first_bindings = prepare_reference_view(before)
    second, second_bindings = prepare_reference_view(after)
    assert first["source_refs"] == second["source_refs"]
    assert first[FIELD]["bindings_sha256"] != second[FIELD]["bindings_sha256"]
    assert reference_schema({"type": "string", "enum": before["source_refs"]}, first_bindings) == reference_schema(
        {"type": "string", "enum": after["source_refs"]}, second_bindings,
    )
    with pytest.raises(ValueError, match="pinned request"):
        expand_reference_view(first, second_bindings)
    assert expand_reference_view(first, first_bindings) == before
    assert expand_reference_view(second, second_bindings) == after


def test_table_columns_preserve_reference_types_and_literal_prose():
    rows = [[_ref(str(i)), [_ref("source-" + str(i))], _ref("literal"), i] for i in range(20)]
    original = {"appraisals": {"columns": ["ref", "source_refs", "summary", "revision"],
                                "stable_rows": rows[:-1], "volatile_last_row": rows[-1]},
                "affect": {"columns": ["appraisal_id", "source_refs", "text", "revision"], "rows": rows}}
    view, bindings = prepare_reference_view(original)
    assert view["appraisals"]["stable_rows"][0][0].startswith("@r:")
    assert view["appraisals"]["stable_rows"][0][1][0].startswith("@r:")
    assert view["appraisals"]["stable_rows"][0][2] == _ref("literal")
    assert view["appraisals"]["volatile_last_row"][0].startswith("@r:")
    assert view["affect"]["rows"][0][0].startswith("@r:")
    assert view["affect"]["rows"][0][2] == _ref("literal")
    assert expand_reference_view(view, bindings) == original
