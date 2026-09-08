"""Branch transport requires a real source choice; it never repairs authority."""

import hashlib
import json

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.world_v2 import visible_source_closure_protocol as protocol


def _decision(verdict="closed", *, first=18, additional=()):
    value = {
        "beat_index": 0,
        "verdict": verdict,
        "semantic_role": "question" if verdict == "source_free" else "external_proposition",
        "subject_role": "counterpart",
    }
    if verdict == "closed":
        value.update(first_source_ref_index=first, additional_source_ref_indexes=list(additional))
    return value


def _wire(*, decisions=None, rejections=()):
    return {
        "contract": "visible-beat-source-verdict.3",
        "decisions": [_decision()] if decisions is None else decisions,
        "rejections": list(rejections),
    }


def _parse(value, **kwargs):
    return protocol.parse_visible_source_verdict(
        json.dumps(value, ensure_ascii=False),
        version="3",
        visible_beats=kwargs.pop("visible_beats", ("你忙完了。",)),
        source_ref_kinds=kwargs.pop("source_ref_kinds", ("current_counterpart_report",) * 19),
        **kwargs,
    )


def test_v3_schema_rejects_observed_v2_closed_with_empty_refs():
    schema = protocol.visible_source_closure_schema(version="3")
    observed = _wire(
        decisions=[
            {
                "beat_index": 0,
                "semantic_role": "external_proposition",
                "source_ref_indexes": [],
                "subject_role": "counterpart",
                "verdict": "closed",
            },
            {
                "beat_index": 1,
                "semantic_role": "question",
                "source_ref_indexes": [],
                "subject_role": "counterpart",
                "verdict": "source_free",
            },
        ]
    )
    # Exact Trial16 decision shapes, with only the top contract switched to .3.
    assert not Draft202012Validator(schema).is_valid(observed)
    branches = schema["properties"]["decisions"]["items"]["anyOf"]
    assert len(branches) == 3
    closed = next(
        branch for branch in branches if branch["properties"]["verdict"]["enum"] == ["closed"]
    )
    assert "first_source_ref_index" in closed["required"]
    assert closed["properties"]["first_source_ref_index"]["type"] == "integer"
    assert Draft202012Validator(schema).is_valid(_wire())
    assert not Draft202012Validator(schema).is_valid(
        _wire(decisions=[_decision() | {"first_source_ref_index": None}])
    )


def test_v3_explicit_source_choice_normalizes_through_original_closure():
    parsed = _parse(_wire(), source_ref_subject_roles=("counterpart",) * 19)
    assert parsed.verdict.contract == "visible-beat-source-verdict.1"
    assert parsed.verdict.segments[0].source_ref_indexes == (18,)
    assert parsed.verdict.segments[0].decision == "closed"
    assert parsed.rejections == ()


@pytest.mark.parametrize(
    "version,expected",
    [
        ("1", "ac9f97c9e4b3909219db752e1a48e0c4a54842d6e9099003162fabf8f75255c4"),
        ("2", "90775ac3fb757b423af6fea1821b587f4e0640e1bd9c97533bf3330a0570a85e"),
    ],
)
def test_older_request_messages_tools_and_repair_keep_frozen_bytes(version, expected):
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
        "tool": protocol.visible_source_verdict_provider_request_contract(version=version),
        "messages": protocol.visible_source_closure_messages(**params, version=version),
        "repair": protocol.visible_source_closure_messages(
            **params, version=version, invalid_reason=failure
        ),
    }
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(raw.encode()).hexdigest() == expected


def test_v3_schema_is_independent_strict_subset_with_complete_branches():
    before = [protocol.visible_source_closure_schema(version=v) for v in ("1", "2")]
    contract = protocol.visible_source_verdict_provider_request_contract(version="3")
    assert contract["contract"] == "visible-beat-source-verdict.3"
    assert contract["tool_choice"]["function"]["name"] == "visible_beat_source_verdict_v3"
    assert contract["schema_digest"] not in {
        protocol.visible_source_verdict_schema_digest(version=v) for v in ("1", "2")
    }
    schema = contract["tools"][0]["function"]["parameters"]

    def check(value):
        if isinstance(value, dict):
            assert not set(value) & {
                "$ref",
                "$defs",
                "minItems",
                "maxItems",
                "prefixItems",
                "minLength",
                "maxLength",
                "oneOf",
            }
            if value.get("type") == "object":
                assert set(value["required"]) == set(value["properties"])
                assert value["additionalProperties"] is False
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(schema)
    branches = schema["properties"]["decisions"]["items"]["anyOf"]
    for branch in branches:
        verdict = branch["properties"]["verdict"]["enum"][0]
        fields = set(branch["properties"])
        assert "source_ref_indexes" not in fields
        if verdict == "closed":
            assert fields == {
                "beat_index",
                "verdict",
                "semantic_role",
                "subject_role",
                "first_source_ref_index",
                "additional_source_ref_indexes",
            }
        else:
            assert fields == {"beat_index", "verdict", "semantic_role", "subject_role"}
    schema["properties"]["rejections"]["items"]["required"].clear()
    assert [protocol.visible_source_closure_schema(version=v) for v in ("1", "2")] == before
    assert (
        protocol.visible_source_closure_schema(version="3")["properties"]["rejections"]
        == before[1]["properties"]["rejections"]
    )


@pytest.mark.parametrize("field", ["first_source_ref_index", "additional_source_ref_indexes"])
def test_closed_cannot_omit_any_source_field(field):
    value = _wire()
    del value["decisions"][0][field]
    assert not Draft202012Validator(protocol.visible_source_closure_schema(version="3")).is_valid(
        value
    )
    with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
        _parse(value)
    assert caught.value.code == "schema_invalid"


@pytest.mark.parametrize("verdict", ["source_free", "unclosed", "closed"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("source_ref_indexes", []),
        ("first_source_ref_index", None),
        ("additional_source_ref_indexes", None),
    ],
)
def test_foreign_branch_fields_and_null_padding_are_rejected(verdict, field, value):
    decision = _decision(verdict)
    decision[field] = value
    with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
        _parse(_wire(decisions=[decision]))
    assert caught.value.code == "schema_invalid"


@pytest.mark.parametrize(
    "first,additional",
    [(True, ()), ("18", ()), (18.0, ()), (18, (True,)), (18, ("1",)), (18, (None,))],
)
def test_source_indexes_are_strict_integers(first, additional):
    with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
        _parse(_wire(decisions=[_decision(first=first, additional=additional)]))
    assert caught.value.code == "schema_invalid"


@pytest.mark.parametrize(
    "first,additional",
    [(-1, ()), (19, ()), (18, (18,)), (18, (0, 0)), (18, tuple(range(8))), (18, (19,))],
)
def test_original_parser_enforces_total_eight_uniqueness_and_range(first, additional):
    with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
        _parse(_wire(decisions=[_decision(first=first, additional=additional)]))
    assert caught.value.code == "ref_set_invalid"


def test_all_eight_explicit_sources_are_retained_in_order():
    parsed = _parse(_wire(decisions=[_decision(first=18, additional=range(7))]))
    assert parsed.verdict.segments[0].source_ref_indexes == (18, 0, 1, 2, 3, 4, 5, 6)


@pytest.mark.parametrize(
    "role,subject",
    [
        ("private_state", "companion"),
        ("commitment", "companion"),
        ("generalization", "general"),
        ("question", "counterpart"),
    ],
)
def test_source_free_capabilities_are_preserved_without_source_fields(role, subject):
    decision = _decision("source_free") | {"semantic_role": role, "subject_role": subject}
    parsed = _parse(_wire(decisions=[decision]))
    assert parsed.verdict.segments[0].decision == "source_free"
    assert parsed.verdict.segments[0].source_ref_indexes == ()


@pytest.mark.parametrize(
    "verdict,role",
    [
        ("closed", "private_state"),
        ("unclosed", "question"),
        ("source_free", "mixed"),
        ("source_free", "external_proposition"),
    ],
)
def test_schema_and_parser_keep_original_verdict_semantic_matrix(verdict, role):
    value = _wire(decisions=[_decision(verdict) | {"semantic_role": role}])
    assert not Draft202012Validator(protocol.visible_source_closure_schema(version="3")).is_valid(
        value
    )
    with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
        _parse(value)
    assert caught.value.code == "schema_invalid"


def test_original_source_actor_and_eligibility_are_not_repaired():
    with pytest.raises(protocol.VisibleSourceClosureWireFailure, match="actor does not match"):
        _parse(_wire(), source_ref_subject_roles=("companion",) * 19)
    with pytest.raises(protocol.VisibleSourceClosureWireFailure, match="eligible readable"):
        _parse(
            _wire(decisions=[_decision(first=0)]),
            source_ref_kinds=("accepted_fact",),
            source_references=(
                {
                    "source_ref_index": 0,
                    "kind": "accepted_fact",
                    "subject_role": "counterpart",
                    "support_eligibility": "baseline_only",
                    "review_material": {},
                },
            ),
        )
    with pytest.raises(protocol.VisibleSourceClosureWireFailure, match="cannot change actor"):
        _parse(_wire(decisions=[_decision("source_free") | {"semantic_role": "commitment"}]))


def _diagnostic(index=0):
    return {
        "beat_index": index,
        "char_start": 1,
        "char_end": 3,
        "related_source_ref_indexes": [18],
        "source_problem": "主体不匹配，没有本句所述经历的来源。",
    }


def test_unclosed_diagnostic_survives_without_becoming_source_authority():
    parsed = _parse(
        _wire(decisions=[_decision("unclosed")], rejections=[_diagnostic()]),
        visible_beats=("甲😀乙",),
    )
    assert parsed.rejections[0].model_dump(mode="json") == _diagnostic()
    assert parsed.verdict.segments[0].decision == "unclosed"
    assert parsed.verdict.segments[0].source_ref_indexes == ()
    assert parsed.verdict.segments[0].locator.text == "甲😀乙"


@pytest.mark.parametrize(
    "diagnostic",
    [
        None,
        _diagnostic() | {"char_end": 30},
        _diagnostic() | {"char_start": True},
        _diagnostic() | {"related_source_ref_indexes": [19]},
        _diagnostic() | {"source_problem": "字" * 65},
    ],
)
def test_v2_diagnostic_guards_remain_authoritative(diagnostic):
    with pytest.raises(protocol.VisibleSourceClosureWireFailure):
        _parse(
            _wire(
                decisions=[_decision("unclosed")],
                rejections=[] if diagnostic is None else [diagnostic],
            )
        )


def test_diagnostics_cannot_attach_to_closed_or_source_free():
    for verdict in ("closed", "source_free"):
        with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
            _parse(_wire(decisions=[_decision(verdict)], rejections=[_diagnostic()]))
        assert caught.value.code == "diagnostic_coverage_invalid"


def test_all_sixteen_rejections_keep_full_long_beat_and_unlimited_span():
    decisions = [_decision("unclosed") | {"beat_index": index} for index in range(16)]
    rejections = [_diagnostic(index) | {"char_start": 0, "char_end": 1024} for index in range(16)]
    parsed = _parse(
        _wire(decisions=decisions[::-1], rejections=rejections[::-1]),
        visible_beats=("字" * 1024,) * 16,
    )
    assert [item.model_dump(mode="json") for item in parsed.rejections] == rejections
    assert all(
        item.locator.text == "字" * 1024 and item.decision == "unclosed"
        for item in parsed.verdict.segments
    )


@pytest.mark.parametrize("version", ["1", "2"])
def test_old_closed_empty_source_failure_stays_exact(version):
    value = {
        "contract": f"visible-beat-source-verdict.{version}",
        "decisions": [
            {
                "beat_index": 0,
                "verdict": "closed",
                "semantic_role": "external_proposition",
                "subject_role": "counterpart",
                "source_ref_indexes": [],
            }
        ],
    }
    if version == "2":
        value["rejections"] = []
    with pytest.raises(protocol.VisibleSourceClosureWireFailure) as caught:
        protocol.parse_visible_source_verdict(
            json.dumps(value),
            version=version,
            visible_beats=("你忙完了。",),
            source_ref_kinds=("current_counterpart_report",),
        )
    assert caught.value.correction_coordinate() == {
        "code": "verdict_ref_invalid",
        "beat_index": 0,
        "field": "decisions.0.source_ref_indexes",
    }
    assert str(caught.value) == "closed Beat requires at least one pinned source"


def test_v3_prompt_and_repair_match_branch_fields_and_preserve_source_choice():
    kwargs = {
        "version": "3",
        "visible_beats": ("你忙完了。",),
        "world_claims": (),
        "source_references": (),
    }
    failure = protocol.VisibleSourceClosureWireFailure(
        "ref_set_invalid", "do not echo rejected raw"
    )
    messages = protocol.visible_source_closure_messages(**kwargs, invalid_reason=failure)
    assert "Select the actual first supporting source index yourself" in messages[0]["content"]
    assert "Never invent, guess or default a source index" in messages[0]["content"]
    assert (
        "If no eligible evidence supports an external proposition, return unclosed"
        in messages[0]["content"]
    )
    assert "source_ref_indexes must be empty unless verdict is closed" not in messages[0]["content"]
    assert "do not echo rejected raw" not in json.dumps(messages)
    repair = json.loads(messages[-1]["content"])
    assert repair["output_contract"]["contract"] == "visible-beat-source-verdict.3"
    matrix = repair["structural_constraints"]["verdict_role_ref_matrix"]
    assert "first_source_ref_index" in matrix["closed"]
    assert "additional_source_ref_indexes" in matrix["closed"]
    assert all("source_ref_indexes" not in constraints for constraints in matrix.values())
