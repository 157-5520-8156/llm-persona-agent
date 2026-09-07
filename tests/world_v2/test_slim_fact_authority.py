"""The compact transport must not erase facts the character declares."""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES


@pytest.mark.parametrize("result_kind", ["reply_only", "full_turn"])
def test_slim_transport_preserves_authored_fact_scope_and_sources(result_kind):
    contract = InboundToolContracts().compact_gate_for(
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=False,
    )
    claims = [
        {
            "claim_text": "刚才在图书馆看了会儿书",
            "scope": "past_world",
            "source_refs": ["event:accepted-library-experience"],
        }
    ]
    authored = {
        "messages": ["刚才在图书馆看了会儿书"],
        "meaning_of_this": "他问我刚才在做什么",
        "my_state": "想随口说说",
        "world_claims": claims,
    }

    expanded = contract.decode(
        json.dumps({"result_kind": result_kind, "payload_json": json.dumps(authored)})
    )
    if result_kind == "full_turn":
        expanded = json.loads(expanded["full_turn_json"])

    assert expanded["events"][0]["world_claims"] == claims
