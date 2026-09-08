"""The public environmental producer cannot turn prose into role execution."""

import json

import pytest

from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore, life_content_payload_hash,
)
from companion_daemon.world_v2.occurrence_content_coordinator import (
    OccurrenceContentCommitRequest, OccurrenceContentCoordinator, OutcomeCandidateContent,
)
from test_occurrence_content_coordinator import WORLD_ID, _request, _seed


def _current():
    request = _request()
    body = {"contract": "world-consequence.2", "environment_text": "院内的树枝被冰雹打断。"}
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value = request.candidate_contents[0].model_dump()
    value.update(result_contract="world-consequence.2", text=text,
                 result_payload_hash=life_content_payload_hash(text))
    candidate = OutcomeCandidateContent.model_validate(value)
    return OccurrenceContentCommitRequest.model_validate(
        request.model_copy(update={"candidate_contents": (candidate,)}).model_dump(),
    )


def test_public_writer_records_explicit_environment_contract_and_exact_content():
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    store = InMemoryImmutableLifeContentStore()
    _seed(ledger)
    request = _current()
    result = OccurrenceContentCoordinator(ledger=ledger, store=store).commit(request)
    assert result.world_revision == 2
    descriptor = ledger.project().world_occurrences[0].candidate_outcomes[0]
    assert descriptor.result_contract == "world-consequence.2"
    assert descriptor.result_payload_hash == descriptor.content_payload_hash
    assert store.read_exact(content_ref=descriptor.content_ref).text == request.candidate_contents[0].text
    assert "result_contract" not in _request().candidate_contents[0].descriptor().model_dump()


@pytest.mark.parametrize("invalid", ["plain_text", "hash", "attempt"])
def test_public_writer_rejects_forged_current_input_before_writing(invalid):
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    store = InMemoryImmutableLifeContentStore()
    _seed(ledger)
    before = ledger.project()
    request = _current()
    candidate = request.candidate_contents[0]
    text = candidate.text
    if invalid == "plain_text":
        text = "她跑出去收起手账。"
    elif invalid == "attempt":
        body = json.loads(text)
        body["authorized_attempt_result"] = {
            "text": "手账没有淋湿。",
            "execution_binding": {
                "source_kind": "activity_execution", "source_event_type": "ActivityStarted",
                "actor_ref": "actor:companion", "source_event_ref": "event:unproved-start",
                "source_world_revision": 1, "source_payload_hash": "a" * 64,
                "privacy_class": "private", "plan_id": "plan:unproved",
                "activity_id": "activity:unproved", "plan_entity_revision": 2,
            },
        }
        text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    candidate = candidate.model_copy(update={
        "text": text,
        "result_payload_hash": "b" * 64 if invalid == "hash" else life_content_payload_hash(text),
    })
    request = request.model_copy(update={"candidate_contents": (candidate,)})
    with pytest.raises(ValueError):
        OccurrenceContentCoordinator(ledger=ledger, store=store).commit(request)
    assert ledger.project() == before
    assert store.read_exact(content_ref=candidate.content_ref) is None
