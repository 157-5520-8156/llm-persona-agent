"""Versioned diagnostic transport preserves whole-Beat source authority."""

import json

import pytest

from companion_daemon.world_v2 import visible_source_closure_protocol as protocol


def _decision(index=0, *, verdict="unclosed", refs=(), subject="companion"):
    return {
        "beat_index": index,
        "verdict": verdict,
        "semantic_role": "private_state" if verdict == "source_free" else "external_proposition",
        "subject_role": subject,
        "source_ref_indexes": list(refs),
    }


def _diagnostic(index=0, *, start=1, end=3, refs=(), problem="没有同主体、时间与状态的来源。"):
    return {
        "beat_index": index,
        "char_start": start,
        "char_end": end,
        "related_source_ref_indexes": list(refs),
        "source_problem": problem,
    }


def _raw(*, decisions=None, rejections=None, contract="visible-beat-source-verdict.2"):
    return json.dumps(
        {
            "contract": contract,
            "decisions": [_decision()] if decisions is None else decisions,
            "rejections": [_diagnostic()] if rejections is None else rejections,
        },
        ensure_ascii=False,
    )


def test_v2_contract_has_separate_identity_and_exhaustive_diagnostics():
    v1 = protocol.visible_source_verdict_provider_request_contract()
    v2 = protocol.visible_source_verdict_provider_request_contract(version="2")
    assert v2["contract"] == "visible-beat-source-verdict.2"
    assert v2["tool_choice"]["function"]["name"] == "visible_beat_source_verdict_v2"
    assert v2["schema_digest"] != v1["schema_digest"]
    schema = v2["tools"][0]["function"]["parameters"]
    assert schema["required"] == ["contract", "decisions", "rejections"]
    assert schema["additionalProperties"] is False
    assert (
        schema["properties"]["decisions"]
        == v1["tools"][0]["function"]["parameters"]["properties"]["decisions"]
    )


def test_v2_diagnostic_uses_unicode_offsets_and_keeps_whole_beat_unclosed():
    parsed = protocol.parse_visible_source_verdict(
        _raw(),
        version="2",
        visible_beats=("甲😀乙e\u0301尾",),
        source_ref_kinds=(),
    )
    assert parsed.verdict.contract == "visible-beat-source-verdict.1"
    assert parsed.verdict.segments[0].decision == "unclosed"
    assert parsed.verdict.segments[0].locator.text == "甲😀乙e\u0301尾"
    assert parsed.rejections[0].model_dump(mode="json") == _diagnostic()
    diagnostic = parsed.rejections[0]
    assert (
        parsed.verdict.segments[0].locator.text[diagnostic.char_start : diagnostic.char_end]
        == "😀乙"
    )
    assert parsed.verdict.segments[0].source_ref_indexes == ()
    with pytest.raises(ValueError):
        parsed.rejections = ()
    with pytest.raises(ValueError):
        diagnostic.char_start = 0


def _parse(raw, *, beats=("甲😀乙e\u0301尾",), kinds=()):
    return protocol.parse_visible_source_verdict(
        raw,
        version="2",
        visible_beats=beats,
        source_ref_kinds=kinds,
    )


def test_v1_default_and_explicit_request_keep_frozen_bytes():
    import hashlib

    params = {
        "visible_beats": ("我现在想去图书馆。", "你呢？"),
        "world_claims": (),
        "source_references": (),
    }
    failure = protocol.VisibleSourceClosureWireFailure(
        "ref_set_invalid",
        "content must stay absent",
        beat_index=1,
        field="decisions.1.source_ref_indexes",
    )
    value = {
        "tool": protocol.visible_source_verdict_provider_request_contract(),
        "messages": protocol.visible_source_closure_messages(**params),
        "repair": protocol.visible_source_closure_messages(**params, invalid_reason=failure),
    }
    assert value == {
        "tool": protocol.visible_source_verdict_provider_request_contract(version="1"),
        "messages": protocol.visible_source_closure_messages(**params, version="1"),
        "repair": protocol.visible_source_closure_messages(
            **params, invalid_reason=failure, version="1"
        ),
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Frozen before this change at d729c2f5; no process-dependent schema ordering.
    assert (
        hashlib.sha256(encoded.encode()).hexdigest()
        == "ac9f97c9e4b3909219db752e1a48e0c4a54842d6e9099003162fabf8f75255c4"
    )


def test_v2_schema_is_strict_subset_and_never_mutates_v1():
    original = protocol.visible_source_closure_schema()
    schema = protocol.visible_source_closure_schema(version="2")

    def check(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert set(value["required"]) == set(value["properties"])
                assert value["additionalProperties"] is False
            assert not set(value) & {
                "$defs",
                "$ref",
                "minLength",
                "maxLength",
                "minItems",
                "maxItems",
            }
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(schema)
    schema["properties"]["decisions"]["items"]["required"].clear()
    assert protocol.visible_source_closure_schema() == original
    assert protocol.visible_source_closure_schema(version="2")["properties"]["decisions"]["items"][
        "required"
    ]
    assert set(original["properties"]) == {"contract", "decisions"}


def test_v2_prompt_preserves_whole_review_and_marks_diagnostics_non_authoritative():
    params = {
        "visible_beats": ("我想出去。我已经到公园了。",),
        "world_claims": (),
        "source_references": (),
    }
    v1 = protocol.visible_source_closure_messages(**params)
    v2 = protocol.visible_source_closure_messages(**params, version="2")
    assert v2[0]["content"].startswith(v1[0]["content"])
    assert "Unicode code point" in v2[0]["content"]
    assert "never supply authority" in v2[0]["content"]
    packet = json.loads(v2[1]["content"])
    assert packet["visible_beats"] == [{"beat_index": 0, "text": params["visible_beats"][0]}]
    assert packet["output_contract"]["contract"] == "visible-beat-source-verdict.2"
    repair = protocol.visible_source_closure_messages(
        **params,
        version="2",
        invalid_reason=protocol.VisibleSourceClosureWireFailure(
            "diagnostic_locator_invalid",
            "SECRET invalid bytes",
            beat_index=0,
            field="rejections.char_end",
        ),
    )
    assert "SECRET" not in json.dumps(repair)
    assert (
        json.loads(repair[2]["content"])["correction_contract"]
        == "visible-beat-source-verdict-repair.2"
    )
    assert (
        json.loads(repair[2]["content"])["output_contract"]["contract"]
        == "visible-beat-source-verdict.2"
    )


@pytest.mark.parametrize("version", ["1", "2"])
def test_versions_cannot_accept_each_others_envelope(version):
    raw = _raw(contract=f"visible-beat-source-verdict.{3 - int(version)}")
    with pytest.raises(protocol.VisibleSourceClosureWireFailure):
        protocol.parse_visible_source_verdict(
            raw, version=version, visible_beats=("原文",), source_ref_kinds=()
        )
    with pytest.raises(protocol.VisibleSourceClosureWireFailure):
        protocol.parse_visible_source_closure(_raw(), visible_beats=("原文",), source_ref_kinds=())


def test_v1_wrapper_is_exact_normalization_and_rejects_diagnostics():
    value = {"contract": "visible-beat-source-verdict.1", "decisions": [_decision()]}
    raw = json.dumps(value)
    kwargs = {"visible_beats": ("整条原文",), "source_ref_kinds": ()}
    parsed = protocol.parse_visible_source_verdict(raw, **kwargs)
    assert parsed.verdict == protocol.parse_visible_source_closure(raw, **kwargs)
    assert parsed.rejections == ()
    value["rejections"] = []
    with pytest.raises(protocol.VisibleSourceClosureWireFailure):
        protocol.parse_visible_source_verdict(json.dumps(value), **kwargs)


@pytest.mark.parametrize("rejections", [[], [_diagnostic(), _diagnostic()], [_diagnostic(1)]])
def test_diagnostics_require_exact_unique_unclosed_beat_cover(rejections):
    with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
        _parse(_raw(rejections=rejections))
    assert caught.value.code == "diagnostic_coverage_invalid"


@pytest.mark.parametrize("verdict,refs", [("source_free", ()), ("closed", (0,))])
def test_non_rejected_beats_cannot_have_diagnostics(verdict, refs):
    with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
        _parse(_raw(decisions=[_decision(verdict=verdict, refs=refs)]), kinds=("recent_dialogue",))
    assert caught.value.code == "diagnostic_coverage_invalid"


@pytest.mark.parametrize(
    "start,end",
    [(0, 0), (3, 2), (-1, 2), (1, 7), (1, 11), (True, 3), (1, False), ("1", 3), (1.0, 3)],
)
def test_invalid_or_byte_based_offsets_fail_closed(start, end):
    with pytest.raises(protocol.VisibleSourceClosureWireFailure):
        _parse(_raw(rejections=[_diagnostic(start=start, end=end)]))


@pytest.mark.parametrize(
    "field,value",
    [
        ("beat_index", True),
        ("beat_index", 0.0),
        ("beat_index", "0"),
        ("related_source_ref_indexes", [True]),
        ("related_source_ref_indexes", [0.0]),
        ("related_source_ref_indexes", ["0"]),
        ("related_source_ref_indexes", [-1]),
        ("related_source_ref_indexes", [1]),
        ("related_source_ref_indexes", [0, 0]),
        ("related_source_ref_indexes", list(range(9))),
        ("source_problem", ""),
        ("source_problem", " \n "),
        ("source_problem", "字" * 65),
        ("source_problem", "\u0000" * 16),
        ("source_problem", True),
    ],
)
def test_invalid_diagnostic_fields_are_rejected_without_echoing_input(field, value):
    diagnostic = _diagnostic()
    diagnostic[field] = value
    with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
        _parse(_raw(rejections=[diagnostic]), kinds=("recent_dialogue",))
    assert "没有同主体" not in str(caught.value)


@pytest.mark.parametrize(
    "field",
    ["beat_index", "char_start", "char_end", "related_source_ref_indexes", "source_problem"],
)
def test_every_diagnostic_field_is_required(field):
    diagnostic = _diagnostic()
    del diagnostic[field]
    with pytest.raises(protocol.VisibleSourceClosureWireFailure):
        _parse(_raw(rejections=[diagnostic]))


def test_unknown_fields_duplicate_members_and_oversized_wire_are_not_repaired():
    cases = []
    value = json.loads(_raw())
    del value["rejections"]
    cases.append(json.dumps(value))
    value = json.loads(_raw())
    value["rejections"][0]["replacement_dialogue"] = "do not use me"
    cases.append(json.dumps(value))
    cases.append(_raw().replace('"char_start": 1', '"char_start": 0, "char_start": 1'))
    cases.append(_raw() + " " * protocol.MAX_VISIBLE_SOURCE_VERDICT_V2_BYTES)
    for raw in cases:
        with pytest.raises(protocol.VisibleSourceClosureWireFailure):
            _parse(raw)


def test_related_refs_only_explain_and_cannot_supply_closure():
    parsed = _parse(
        _raw(rejections=[_diagnostic(refs=(0,), problem="主体不同；不是角色的已发生经历。")]),
        kinds=("current_counterpart_report",),
    )
    assert parsed.rejections[0].related_source_ref_indexes == (0,)
    assert parsed.verdict.segments[0].decision == "unclosed"
    assert parsed.verdict.segments[0].source_ref_indexes == ()
    assert parsed.verdict.segments[0].source_relation == "unclosed"
    with pytest.raises(protocol.VisibleSourceClosureWireFailure, match="partial source authority"):
        _parse(
            _raw(decisions=[_decision(refs=(0,))], rejections=[_diagnostic(refs=(0,))]),
            kinds=("recent_dialogue",),
        )


@pytest.mark.parametrize(
    "decisions",
    [
        [],
        [_decision(0), _decision(0)],
        [_decision(1)],
        [_decision(verdict="source_free") | {"semantic_role": "mixed"}],
    ],
)
def test_diagnostics_do_not_bypass_original_whole_beat_rules(decisions):
    with pytest.raises(protocol.VisibleSourceClosureWireFailure):
        _parse(_raw(decisions=decisions))


def test_original_pinned_subject_and_material_guards_still_run():
    raw = _raw(decisions=[_decision(verdict="closed", refs=(0,))], rejections=[])
    with pytest.raises(protocol.VisibleSourceClosureWireFailure, match="actor does not match"):
        protocol.parse_visible_source_verdict(
            raw,
            version="2",
            visible_beats=("我出门了。",),
            source_ref_kinds=("current_counterpart_report",),
            source_ref_subject_roles=("counterpart",),
        )
    with pytest.raises(protocol.VisibleSourceClosureWireFailure, match="eligible readable"):
        protocol.parse_visible_source_verdict(
            raw,
            version="2",
            visible_beats=("我出门了。",),
            source_ref_kinds=("accepted_fact",),
            source_ref_subject_roles=("companion",),
            source_references=(
                {
                    "source_ref_index": 0,
                    "kind": "accepted_fact",
                    "subject_role": "companion",
                    "support_eligibility": "baseline_only",
                    "review_material": {},
                },
            ),
        )


def test_sixteen_diagnostics_are_retained_and_order_only_is_canonicalized():
    decisions = [_decision(index) for index in range(16)]
    diagnostics = [
        _diagnostic(index, start=0, end=1024, refs=range(8), problem="字" * 64)
        for index in range(16)
    ]
    parsed = _parse(
        _raw(decisions=decisions[::-1], rejections=diagnostics[::-1]),
        beats=("字" * 1024,) * 16,
        kinds=("accepted_fact",) * 8,
    )
    assert [item.model_dump(mode="json") for item in parsed.rejections] == diagnostics
    assert [item.locator.beat_index for item in parsed.verdict.segments] == list(range(16))
    assert all(item.locator.text == "字" * 1024 for item in parsed.verdict.segments)
    assert all(item.decision == "unclosed" for item in parsed.verdict.segments)
    with pytest.raises(protocol.VisibleSourceClosureWireFailure):
        _parse(
            _raw(decisions=decisions, rejections=diagnostics + [diagnostics[0]]),
            beats=("字" * 1024,) * 16,
            kinds=("accepted_fact",) * 8,
        )


def test_successful_v2_keeps_original_normalized_verdict_with_no_diagnostics():
    raw = _raw(decisions=[_decision(verdict="source_free")], rejections=[])
    parsed = _parse(raw)
    assert parsed.rejections == ()
    assert parsed.verdict.segments[0].decision == "source_free"
    assert "rejections" not in parsed.verdict.model_dump(mode="json")


@pytest.mark.parametrize(
    "field,value", [("beat_index", False), ("beat_index", "0"), ("source_ref_indexes", [True])]
)
def test_v2_original_decision_integer_fields_are_also_strict(field, value):
    decision = _decision(verdict="closed", refs=(0,))
    decision[field] = value
    with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
        _parse(_raw(decisions=[decision], rejections=[]), kinds=("recent_dialogue",))
    assert caught.value.code == "schema_invalid"


@pytest.mark.parametrize("version", ["0", "999", 2, None, True])
def test_unknown_versions_never_fall_back_to_v1(version):
    with pytest.raises(ValueError, match="version"):
        protocol.visible_source_verdict_provider_request_contract(version=version)
    with pytest.raises(ValueError, match="version"):
        protocol.visible_source_closure_messages(
            version=version, visible_beats=(), world_claims=(), source_references=()
        )
    with pytest.raises(ValueError, match="version"):
        protocol.parse_visible_source_verdict(
            _raw(), version=version, visible_beats=(), source_ref_kinds=()
        )
