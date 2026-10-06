"""Reason-before-type transport retains exact v25 claims and whole-Beat coverage."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.world_v2.grounded_review_wire import decode, tool_request

OLD = "grounded-review-ordered-wire.4"
NEW = "grounded-review-ordered-wire.5"


@pytest.mark.parametrize("fact", [False, True])
def test_reasoned_wire_roundtrips_current_expression_and_factual_obligation(fact):
    segment = {"kind":"non_record", "text":"我现在想去图书馆。", "rationale":"本轮形成的意愿，未声称行动已实施。"}
    if fact:
        segment = {"kind":"fact", "text":"我已经去了图书馆。", "rationale":"具体已发生行动，尚无相应执行证据。",
                   "proposition":"角色已经去过图书馆。", "claim_scope":"external_fact", "subject_role":"companion",
                   "reading_ids":[], "fact_value_selections":[], "source_support":False}
    old = {"beat_decisions":[{"beat_index":0,"review_complete":True,"segments":[segment]}]}
    value = {"beat_decisions":[{"beat_index":0,"segments":[{
        "text":segment["text"], "rationale":segment["rationale"],
        "assessment":{key:val for key,val in segment.items() if key not in {"text","rationale"}},
    }],"review_complete":True}]}
    schema = tool_request(NEW)["tools"][0]["function"]["parameters"]
    Draft202012Validator(schema).validate(value)
    beats = [{"beat_index":0,"text":segment["text"]}]
    assert decode(json.dumps(value), beats=beats, carrier=NEW) == decode(json.dumps(old), beats=beats, carrier=OLD)
    bad = deepcopy(value)
    bad["beat_decisions"][0]["segments"][0]["text"] = "少了原文。"
    with pytest.raises(ValueError, match="does not match"):
        decode(json.dumps(bad), beats=beats, carrier=NEW)
    # Fields shared by both decisions must precede the irreversible union choice.
    item = schema["properties"]["beat_decisions"]["items"]
    assert list(item["properties"]) == ["beat_index","segments","review_complete"]
    assert list(item["properties"]["segments"]["items"]["properties"]) == ["text","rationale","assessment"]


@pytest.mark.asyncio
async def test_reasoned_receipt_preserves_exact_fact_authority_and_cold_replay(tmp_path):
    from companion_daemon.world_v2.visible_grounded_review import (
        PROTOCOL_V2, prepare_grounded_review, record_grounded_review_receipt,
        GroundedVisibleReviewReceipt, GroundedVisibleReviewRejected,
        _compiled_grounded_bytes, _verified_prepared_bytes,
    )
    from companion_daemon.world_v2.proposal_envelope import DecisionProposal
    from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
    from test_visible_grounded_review import _prepare, _record_args
    old = await _prepare(tmp_path, texts=("你保留了周四的约定。",), retained_value="保留周四的约定")
    pin = old.as_dict()
    prepared = prepare_grounded_review(
        candidate=DecisionProposal.model_validate_json(pin["candidate_json"]),
        source_table=VisibleSourceTable(pin["source_table_json"]), source_ref_aliases=pin["source_ref_aliases"],
        protocol=PROTOCOL_V2, ordered_native=NEW,
    )
    reading = next(r for r in prepared.as_dict()["catalog"] if r.get("source_family") == "accepted_fact_value")
    assessment = {"kind":"fact", "proposition":"用户保留周四约定", "claim_scope":"accepted_fact",
                  "subject_role":"counterpart", "source_support":True, "reading_ids":[reading["reading_id"]],
                  "fact_value_selections":[{"reading_id":reading["reading_id"],"quoted_value":"保留周四的约定",
                                             "subject_ref":reading["source_owner_ref"]}]}
    raw = {"beat_decisions":[{"beat_index":0,"segments":[{"text":"你保留了周四的约定。",
          "rationale":"对应原值和主体支持该命题。","assessment":assessment}],"review_complete":True}]}
    receipt = record_grounded_review_receipt(**_record_args(prepared, raw))
    assert receipt.beat_outcomes == ("closed",)
    _compiled_grounded_bytes.cache_clear()
    _verified_prepared_bytes.cache_clear()
    assert GroundedVisibleReviewReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    for field,value in (("quoted_value","虚构原文"),("subject_ref","actor:other")):
        forged = deepcopy(raw)
        forged["beat_decisions"][0]["segments"][0]["assessment"]["fact_value_selections"][0][field] = value
        with pytest.raises(GroundedVisibleReviewRejected):
            record_grounded_review_receipt(**_record_args(prepared, forged))


def test_reasoned_reference_transport_never_decodes_free_prose():
    from companion_daemon.world_v2.grounded_review_wire import expand_response_references
    from companion_daemon.world_v2.reference_wire import prepare_reference_view
    _, bindings = prepare_reference_view({"source_refs":["event:" + "a" * 64] * 20})
    unknown = bindings["prefix"] + "9999"
    raw = {"beat_decisions":[{"beat_index":0,"review_complete":True,"segments":[{
        "text":"嗯。","rationale":unknown,"assessment":{"kind":"non_record"},
    }]}]}
    expanded = expand_response_references(raw, bindings)
    decoded = decode(json.dumps(expanded), beats=[{"beat_index":0,"text":"嗯。"}], carrier=NEW)
    assert decoded["beat_decisions"][0]["non_record_expressions"][0]["rationale"] == unknown
