"""The author sees exact padding paths from its own frozen tool contract."""

import json
from copy import deepcopy

import httpx
import pytest

from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from test_character_interior_inbound_author import _request
from test_longitudinal_cli import (
    test_required_review_cli_host_holds_complete_candidate_until_review as _exercise_review_cli,
)
from test_inbound_atomic_result_v2 import _contract


_PADDING_MARKER = "Required explicit null padding paths by result_kind:\n"


@pytest.mark.parametrize(
    "phase,recall_allowed,expected",
    [
        ("initial", True, {
            "decision": ("$.result.private_turn_state", "$.result.recall_request"),
            "recall": ("$.result.appraisal_draft", "$.result.expression_draft"),
        }),
        ("initial", False, {
            "decision": ("$.result.private_turn_state", "$.result.recall_request"),
        }),
        ("after_recall", False, {"decision": ()}),
        ("final", False, {"decision": ()}),
    ],
)
def test_padding_paths_preserve_wire_and_do_not_advertise_unavailable_recall(
    phase, recall_allowed, expected,
):
    contract = _contract(phase=phase, recall_allowed=recall_allowed)
    frozen = deepcopy((contract.provider_tools, contract.identity.request_identity_material()))
    assert contract.required_null_padding_paths() == expected
    assert (contract.provider_tools, contract.identity.request_identity_material()) == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_version", ["1", "2", "3"])
async def test_captured_author_padding_hint_matches_initial_and_after_recall_schema(
    tmp_path, monkeypatch, tool_version,
):
    captured = []
    mock_transport = httpx.MockTransport

    def capture_transport(handler):
        async def respond(request):
            body = json.loads(request.content)
            if body["tools"][0]["function"]["name"].startswith("character_inbound_"):
                captured.append(body)
            return await handler(request)

        return mock_transport(respond)

    monkeypatch.setattr(httpx, "MockTransport", capture_transport)
    # Reuse the real CLI/host/Core scenario, including review-before-Action,
    # exact author-request binding, billing capture, and cold replay checks.
    await _exercise_review_cli(tmp_path, monkeypatch, recall_first=True, tool_version=tool_version)
    assert len(captured) == 2
    for index, body in enumerate(captured):
        system = body["messages"][0]["content"]
        if tool_version == "1":
            assert _PADDING_MARKER not in system
            continue
        if tool_version == "3":
            # This actual CLI/host/Core request uses canonical drafts. Slim
            # field names describe a different protocol and must not instruct
            # either the first author call or the resumed call after Recall.
            assert "一个 slim 对象就够了" not in system
            assert "photo 写 true" not in system
            assert "CANONICAL ATOMIC ROLE GUIDANCE .1" in system
            assert "appraisal_draft.life_intent" in system
            assert "expression_draft.media_request" in system
            assert "expression_draft.private_turn_state" in system
            assert _PADDING_MARKER not in system
            assert "ATOMIC TOOL ENVELOPE V2:" not in system
            assert "ATOMIC TOOL ENVELOPE V3:" in system
            hint = json.loads(system.rsplit("Exact result fields by available result_kind:\n", 1)[1])
            branches = body["tools"][0]["function"]["parameters"]["properties"]["result"]["anyOf"]
            assert hint == {
                branch["properties"]["result_kind"]["enum"][0]: branch["required"]
                for branch in branches
            }
            assert set(hint) == ({"decision", "recall"} if index == 0 else {"decision"})
            continue
        assert _PADDING_MARKER in system
        prefix, hint_json = system.rsplit(_PADDING_MARKER, 1)
        assert prefix.rfind("ATOMIC TOOL ENVELOPE V2:") > prefix.rfind("FORCED TOOL TRANSPORT")
        hint = json.loads(hint_json)
        assert hint == (
            {
                "decision": ["$.result.private_turn_state", "$.result.recall_request"],
                "recall": ["$.result.appraisal_draft", "$.result.expression_draft"],
            }
            if index == 0 else {"decision": []}
        )
        parameters = body["tools"][0]["function"]["parameters"]
        for branch in parameters["properties"]["result"]["anyOf"]:
            kind = branch["properties"]["result_kind"]["enum"][0]
            assert hint[kind] == sorted(
                f"$.result.{key}" for key in branch["required"]
                if branch["properties"][key] == {"type": "null"}
            )
    if tool_version == "2":
        # Final-tool preparation is also shared by retained internal callers.
        # Do not reopen the disabled same-contract retry path to check its wire.
        class Provider:
            supports_required_tool_choice = True
            supports_strict_tool_choice = True

            async def complete_json_with_usage(self, *args, **kwargs):
                pytest.fail("preparing a final request must not invoke the provider")

        provider = Provider()
        author = _InboundCharacterAuthor(
            flash_model=provider, whole_candidate_mode=True,
            visible_source_review_model=provider, atomic_tool_envelope_version="2",
        )
        original_messages = deepcopy(captured[0]["messages"])
        final_messages = list(original_messages)
        final = author._final_tool_reselection_kwargs(
            request=_request(revision=3, call="call:final-padding"), provider=provider,
            messages=final_messages,
        )
        assert final["tools"][0]["function"]["name"] == "character_inbound_final_atomic_v2"
        assert json.loads(final_messages[0]["content"].rsplit(_PADDING_MARKER, 1)[1]) == {
            "decision": [],
        }
        assert original_messages == captured[0]["messages"]
        with_suffix = deepcopy(original_messages)
        with_suffix[0]["content"] += "\nKeep all declared capability bounds."
        preserved = deepcopy(with_suffix)
        with pytest.raises(ValueError, match="padding table"):
            author._final_tool_reselection_kwargs(
                request=_request(revision=3, call="call:final-padding-suffix"), provider=provider,
                messages=with_suffix,
            )
        assert with_suffix == preserved
    if tool_version == "3":
        class Provider:
            supports_required_tool_choice = True
            supports_strict_tool_choice = True

            async def complete_json_with_usage(self, *args, **kwargs):
                pytest.fail("preparing a final request must not invoke the provider")

        provider = Provider()
        author = _InboundCharacterAuthor(
            flash_model=provider, whole_candidate_mode=True,
            visible_source_review_model=provider, atomic_tool_envelope_version="3",
        )
        original_messages = deepcopy(captured[0]["messages"])
        messages = list(original_messages)
        final = author._final_tool_reselection_kwargs(
            request=_request(revision=3, call="call:final-v3"), provider=provider,
            messages=messages,
        )
        assert final["tools"][0]["function"]["name"] == "character_inbound_final_atomic_v3"
        hint = json.loads(messages[0]["content"].rsplit("Exact result fields by available result_kind:\n", 1)[1])
        assert hint == {"decision": ["result_kind", "appraisal_draft", "expression_draft"]}
        assert original_messages == captured[0]["messages"]
        messages[0]["content"] += "\nTrailing unrelated text."
        preserved = deepcopy(messages)
        with pytest.raises(ValueError, match="branch table"):
            author._final_tool_reselection_kwargs(
                request=_request(revision=3, call="call:invalid-v3-suffix"), provider=provider,
                messages=messages,
            )
        assert messages == preserved
