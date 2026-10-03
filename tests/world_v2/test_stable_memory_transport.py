from copy import deepcopy

import pytest

from companion_daemon.world_v2.memory_consolidation_contract import (
    HOST_SOURCE_BINDING, UNDERSTANDING_CONTRACT, expand_understanding_dispositions, tool_contract, validate_payload,
)


def capability(sources, predecessors):
    return {"source_binding": HOST_SOURCE_BINDING,
            "understanding_contract": UNDERSTANDING_CONTRACT,
            "understanding_source_tokens": sources, "understanding_tokens": predecessors}


def test_new_transport_keeps_schema_stable_but_request_authority_distinct():
    first = capability(["m0", "k0"], [])
    second = capability(["m0", "k0"], ["u0", "u1"])
    tools = [tool_contract(capability_payload=c, source_refs=(f"source:{i}",),
                           recall_allowed=False, compact_synthesis_transport=True)
             for i, c in enumerate((first, second))]
    assert tools[0].provider_tools == tools[1].provider_tools
    assert tools[0].identity != tools[1].identity
    assert tools[0].provider_tools[0]["function"]["name"] == "character_memory_consolidation_v4"
    old = [tool_contract(capability_payload=c, source_refs=(f"source:{i}",), recall_allowed=False)
           for i, c in enumerate((first, second))]
    assert old[0].provider_tools != old[1].provider_tools
    assert old[0].provider_tools[0]["function"]["name"] == "character_memory_consolidation_v2"


def payload():
    return {"choices": [{"candidate_token": "m0", "disposition": "retain", "next_review_hours": 72}],
            "understandings": [{"operation": "retain", "predecessor_tokens": [],
                "facet": "自己的理解", "summary": "我可以犹豫，再决定是否开口。", "confidence_bp": 6500,
                "evidence_quotes": [{"source_token": "m0", "quote": "曾经犹豫"}]}]}


@pytest.mark.parametrize("operation,predecessors", [
    ("retain", []), ("revise", ["u0"]), ("merge", ["u0", "u1"]), ("release", ["u0"]),
])
def test_disposition_transport_preserves_each_canonical_character_choice(operation, predecessors):
    value = payload()
    update = value["understandings"][0]
    update.pop("operation")
    update.update(disposition="release" if operation == "release" else "retain", predecessor_tokens=predecessors)
    decoded = expand_understanding_dispositions(value)
    assert decoded["understandings"][0] == {
        **{key: item for key, item in update.items() if key != "disposition"}, "operation": operation,
    }
    assert "operation" not in update


@pytest.mark.parametrize("violation", ["unknown_source", "unknown_predecessor", "false_quote", "forgotten"])
def test_stable_schema_never_weakens_pinned_source_and_quote_acceptance(violation):
    value = deepcopy(payload())
    update = value["understandings"][0]
    if violation == "unknown_source":
        update["evidence_quotes"][0]["source_token"] = "m9"
    elif violation == "unknown_predecessor":
        update["operation"] = "revise"
        update["predecessor_tokens"] = ["u9"]
    elif violation == "false_quote":
        update["evidence_quotes"][0]["quote"] = "没有发生的内容"
    else:
        value["choices"][0]["disposition"] = "forget"
    with pytest.raises(ValueError):
        validate_payload(value, {"m0"}, source_tokens={"m0"}, predecessor_tokens=set(),
                         readings={"m0": {"source_excerpts": [{"text": "她曾经犹豫。"}]}})
