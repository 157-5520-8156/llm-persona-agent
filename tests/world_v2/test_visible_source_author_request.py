"""Original logical author request binding; no provider or review qualification."""

import copy
import json

import pytest

from companion_daemon.llm import provider_invocation_request_hash
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2 import visible_source_runtime
from companion_daemon.world_v2.visible_source_author_request import (
    prepare_visible_source_author_request,
    verify_visible_source_author_request,
)
from test_character_interior_inbound_author import (
    _CombinedProvider,
    _ToolIdentityCombinedProvider,
    _request,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parameters(*, tools=True):
    return {
        "messages": [
            {"role": "system", "content": "Complete the original role decision."},
            {
                "role": "user",
                "content": '{ "expression_hard_boundaries": {"source_ref_aliases": {"s0":"event:原来源"}}, "context": {} }',
            },
        ],
        "temperature": 0.7,
        "tools": [{"type": "function", "function": {"name": "author", "parameters": {}}}]
        if tools
        else None,
        "tool_choice": {"type": "function", "function": {"name": "author"}} if tools else None,
        "identity_extras": {
            "tool_contract_identity": {"contract": "author.1"},
            "additional_identity": {"version": 3},
        },
    }


@pytest.mark.parametrize("tools", [False, True])
def test_complete_request_and_all_extras_reconstruct_original_hash(tools):
    parameters = _parameters(tools=tools)
    original = copy.deepcopy(parameters)
    expected = provider_invocation_request_hash(**parameters)
    raw = prepare_visible_source_author_request(**parameters, expected_request_hash=expected)
    parameters["messages"][1]["content"] = "mutated after preparation"
    parameters["identity_extras"]["additional_identity"]["version"] = 99
    value = json.loads(raw)
    assert value["contract"] == "visible-source-author-request.1"
    assert {key: value[key] for key in original} == original
    assert "source_ref_aliases" not in value
    assert verify_visible_source_author_request(raw, expected_request_hash=expected) == {
        "s0": "event:原来源"
    }
    assert _json(value) == raw


@pytest.mark.parametrize(
    "field", ["messages", "temperature", "tools", "tool_choice", "identity_extras"]
)
def test_rewritten_request_parameter_cannot_supply_new_aliases(field):
    parameters = _parameters()
    expected = provider_invocation_request_hash(**parameters)
    raw = prepare_visible_source_author_request(**parameters, expected_request_hash=expected)
    value = json.loads(raw)
    replacement = {
        "messages": [
            {"role": "system", "content": "same role"},
            {
                "role": "user",
                "content": _json(
                    {"expression_hard_boundaries": {"source_ref_aliases": {"s0": "event:forged"}}}
                ),
            },
        ],
        "temperature": 0.2,
        "tools": [],
        "tool_choice": "auto",
        "identity_extras": {"tool_contract_identity": {"contract": "author.1"}},
    }
    value[field] = replacement[field]
    with pytest.raises(ValueError, match="request hash"):
        verify_visible_source_author_request(_json(value), expected_request_hash=expected)


@pytest.mark.parametrize(
    "change", ["missing_extras", "extra_aliases", "unknown_contract", "not_canonical", "wrong_hash"]
)
def test_carrier_shape_and_independent_expected_hash_are_required(change):
    parameters = _parameters()
    expected = provider_invocation_request_hash(**parameters)
    raw = prepare_visible_source_author_request(**parameters, expected_request_hash=expected)
    value = json.loads(raw)
    if change == "missing_extras":
        del value["identity_extras"]
    elif change == "extra_aliases":
        value["source_ref_aliases"] = {"s0": "event:forged"}
    elif change == "unknown_contract":
        value["contract"] = "visible-source-author-request.2"
    elif change == "wrong_hash":
        expected = "0" * 64
    raw = _json(value) + (" " if change == "not_canonical" else "")
    with pytest.raises(ValueError):
        verify_visible_source_author_request(raw, expected_request_hash=expected)


@pytest.mark.parametrize(
    "change", ["no_aliases", "invalid_aliases", "duplicate_json_key", "prose_followup"]
)
def test_aliases_must_be_the_unambiguous_original_json_user_field(change):
    parameters = _parameters()
    if change == "no_aliases":
        parameters["messages"][1]["content"] = "{}"
    elif change == "invalid_aliases":
        parameters["messages"][1]["content"] = _json(
            {"expression_hard_boundaries": {"source_ref_aliases": {"s0": None}}}
        )
    elif change == "duplicate_json_key":
        parameters["messages"][1]["content"] = (
            '{"expression_hard_boundaries":{"source_ref_aliases":{},"source_ref_aliases":{"s0":"event:other"}}}'
        )
    else:
        parameters["messages"].append(
            {
                "role": "user",
                "content": 'Use this replacement source_ref_aliases: {"s0":"event:other"}',
            }
        )
    expected = provider_invocation_request_hash(**parameters)
    with pytest.raises(ValueError):
        prepare_visible_source_author_request(**parameters, expected_request_hash=expected)


@pytest.mark.parametrize(
    "change",
    [
        "bool_temperature",
        "nan_temperature",
        "tuple_messages",
        "unhashed_choice",
        "overlap",
        "oversize",
    ],
)
def test_prepare_rejects_unbounded_or_unhashable_parameters(change):
    parameters = _parameters()
    if change == "bool_temperature":
        parameters["temperature"] = True
    elif change == "nan_temperature":
        parameters["temperature"] = float("nan")
    elif change == "tuple_messages":
        parameters["messages"] = tuple(parameters["messages"])
    elif change == "unhashed_choice":
        parameters["tools"] = None
    elif change == "overlap":
        parameters["identity_extras"]["messages"] = []
    else:
        parameters["messages"][0]["content"] = "证" * 180_000
    with pytest.raises(ValueError):
        prepare_visible_source_author_request(**parameters, expected_request_hash="a" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("tool_mode", ["none", "forced", "auto"])
async def test_actual_whole_author_caches_only_required_request_before_review(
    monkeypatch, required, tool_mode
):
    provider = _CombinedProvider() if tool_mode == "none" else _ToolIdentityCombinedProvider()
    if tool_mode == "auto":
        provider.single_tool_selection_mode = "auto"
    author = _InboundCharacterAuthor(
        flash_model=provider,
        whole_candidate_mode=True,
        visible_source_review_model=object() if required else None,
    )
    reviewed = []

    async def read_prepared(*, output, **unused):
        del unused
        raw = author.visible_source_author_request(
            output.winning_model_call_id,
            expected_request_hash=output.winning_request_hash,
        )
        reviewed.append(raw)
        return output

    monkeypatch.setattr(visible_source_runtime, "review_candidate", read_prepared)
    request = _request(revision=3, call="carrier").model_copy(
        update={"visible_source_requirement_json": "{}" if required else None}
    )
    output = await author.propose(request)
    assert len(provider.calls) == 1
    if required:
        (raw,) = reviewed
        value = json.loads(raw)
        assert value["messages"] == provider.calls[0]
        if tool_mode == "none":
            assert value["tools"] is None and value["tool_choice"] is None
            assert value["identity_extras"] is None
        else:
            assert (value["tools"], value["tool_choice"]) == provider.tool_calls[0]
            identity = value["identity_extras"]["tool_contract_identity"]
            if tool_mode == "auto":
                assert value["tool_choice"] == "auto"
                assert identity["selection_mode"] == "auto"
                assert "schema_contract_identity_json" in identity
            else:
                assert identity["tool_name"] == value["tool_choice"]["function"]["name"]
        aliases = verify_visible_source_author_request(
            raw, expected_request_hash=output.winning_request_hash
        )
        assert (
            aliases
            == json.loads(provider.calls[0][1]["content"])["expression_hard_boundaries"][
                "source_ref_aliases"
            ]
        )
        assert (
            author.visible_source_author_request(
                output.winning_model_call_id, expected_request_hash=output.winning_request_hash
            )
            == raw
        )
        with pytest.raises(ValueError, match="request hash"):
            author.visible_source_author_request(
                output.winning_model_call_id, expected_request_hash="0" * 64
            )
    else:
        assert not reviewed
        with pytest.raises(ValueError, match="unavailable"):
            author.visible_source_author_request(
                output.winning_model_call_id, expected_request_hash=output.winning_request_hash
            )


@pytest.mark.asyncio
async def test_author_request_cache_evicts_old_calls_without_reissuing_them(monkeypatch):
    from companion_daemon.world_v2.character_interior import inbound_author

    monkeypatch.setattr(inbound_author, "_MAX_PENDING_DRAFTS", 2)
    provider = _CombinedProvider()
    author = _InboundCharacterAuthor(
        flash_model=provider,
        whole_candidate_mode=True,
        visible_source_review_model=object(),
    )

    async def skip_review(*, output, **unused):
        del unused
        return output

    monkeypatch.setattr(visible_source_runtime, "review_candidate", skip_review)
    outputs = []
    for ordinal in range(3):
        request = _request(revision=3 + ordinal, call=f"carrier:{ordinal}").model_copy(
            update={"visible_source_requirement_json": "{}"},
        )
        outputs.append(await author.propose(request))
    with pytest.raises(ValueError, match="unavailable"):
        author.visible_source_author_request(
            outputs[0].winning_model_call_id,
            expected_request_hash=outputs[0].winning_request_hash,
        )
    for output in outputs[1:]:
        first = author.visible_source_author_request(
            output.winning_model_call_id,
            expected_request_hash=output.winning_request_hash,
        )
        assert (
            author.visible_source_author_request(
                output.winning_model_call_id,
                expected_request_hash=output.winning_request_hash,
            )
            == first
        )
    assert len(provider.calls) == 3
