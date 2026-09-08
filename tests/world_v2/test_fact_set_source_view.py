"""Compact model materials preserve exact host-bound Fact withdrawal authority."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.fact_draft_adapter import FactObservationProposalAdapter
from companion_daemon.world_v2.interaction_fact_decision import (
    InteractionFactDecisionRecordedPayload,
)
from companion_daemon.world_v2.interaction_fact_trigger_runtime import InteractionFactTriggerRuntime
from companion_daemon.world_v2.schemas import Observation
from test_fact_member_withdrawal import _record, _runtime, _two_members, _withdraw


_VIEW_FIELDS = {
    "fact_id",
    "predicate_code",
    "source_text",
    "fact_committed_at",
    "fact_updated_at",
    "source_observation_logical_time",
    "source_observation_received_at",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _response(request, result):
    return httpx.Response(
        200,
        request=request,
        json={
            "id": "offline-fact-source-view",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": _json(result)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        },
    )


def _provider(handler):
    return DeepSeekChatModel(
        api_key="offline-fixture",
        base_url="https://offline.invalid",
        model="deepseek-v4-flash",
        thinking_enabled=False,
        max_completion_tokens=4096,
        transport=httpx.MockTransport(handler),
    )


def _decisions(ledger):
    events = ledger.recent_events_by_type(
        event_types=frozenset({"InteractionFactDecisionRecorded"}),
        since=datetime(2026, 1, 1, tzinfo=UTC),
        limit=32,
    )
    return [
        InteractionFactDecisionRecordedPayload.model_validate_json(event.payload_json)
        for event in events
    ]


@pytest.mark.asyncio
async def test_actual_http_compact_set_view_keeps_text_times_and_exact_withdrawal(tmp_path):
    ledger, issuer, _, _ = await _two_members(tmp_path)
    target, sibling = ledger.project().facts
    source_event, source_commit = ledger.lookup_event_commit("event:observation:member:1")
    old_observation = Observation.model_validate_json(source_event.payload_json)
    observed = []

    def handler(request):
        observed.append(json.loads(request.content))
        return _response(request, _withdraw(target))

    provider = _provider(handler)
    try:
        observation = _record(ledger, 3, "The source retracts this exact commitment.")
        assert (await _runtime(ledger, issuer, provider).drain_one()).work_status == "accepted"
        assert len(observed) == 1
        packet = json.loads(observed[0]["messages"][1]["content"])
        assert packet["current_single_facts"] == []
        row = next(
            item for item in packet["current_set_facts"] if item["fact_id"] == target.fact_id
        )
        assert set(row) == _VIEW_FIELDS
        assert row == {
            "fact_id": target.fact_id,
            "predicate_code": target.values.predicate_code,
            "source_text": old_observation.text,
            "fact_committed_at": target.committed_at.isoformat(),
            "fact_updated_at": target.updated_at.isoformat(),
            "source_observation_logical_time": old_observation.logical_time.isoformat(),
            "source_observation_received_at": old_observation.received_at.isoformat(),
        }
        assert packet["current_set_facts_view"] == "fact-set-source-view.1"
        final = ledger.project()
        assert final.facts[0].values.status == "withdrawn"
        assert final.facts[1] == sibling
        choice = next(
            item
            for item in _decisions(ledger)
            if item.source_observation_ref == observation.observation_id
        )
        assert choice.adapter_version == "fact-observation-draft.4"
        bound = json.loads(choice.decision_json)["target_binding"]
        authority = next(
            item
            for item in final.committed_world_event_refs
            if item.event_id == target.origin.accepted_event_ref
        )
        assert bound == {
            "entity_revision": target.entity_revision,
            "authority_event_ref": target.origin.accepted_event_ref,
            "authority_payload_hash": authority.payload_hash,
            "value_hash": target.values.value_hash,
        }
    finally:
        await provider.aclose()
        ledger.close()


@pytest.mark.asyncio
async def test_view_preserves_unicode_distinct_times_and_original_single_materials():
    from test_interaction_fact_trigger_runtime import _observation

    observation, event = _observation()
    single = {
        "fact_id": "fact:single:unchanged",
        "subject_ref": observation.actor,
        "fact_entity_revision": 9,
        "source_text": "Original single materials.",
        "other_original_field": {"value_hash": "f" * 64},
    }
    member = {
        "fact_id": "fact:set:unchanged",
        "predicate_code": "schedule.commitment",
        "source_text": ('完整原文 😀 e\u0301\n第二行 "quotes" \\path\n' * 256),
        "fact_committed_at": "2026-09-08T03:00:00+00:00",
        "fact_updated_at": "2026-09-08T04:00:00+00:00",
        "source_observation_logical_time": "2026-09-08T01:00:00+00:00",
        "source_observation_received_at": "2026-09-08T02:00:00+00:00",
        "subject_ref": observation.actor,
        "fact_entity_revision": 4,
        "fact_accepted_event_ref": "event:fact:private-host-binding",
        "fact_accepted_payload_hash": "a" * 64,
        "fact_accepted_world_revision": 20,
        "source_observation_id": "observation:old-source",
        "value_hash": "b" * 64,
    }
    original = _json(member)
    packets = []

    def handler(request):
        packets.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        return _response(request, {"retain": False})

    provider = _provider(handler)
    try:
        result = await FactObservationProposalAdapter(model=provider).propose(
            observation=observation,
            observation_event=event,
            source_world_revision=1,
            current_single_fact_sources=(single,),
            current_set_fact_sources=(member,),
        )
        assert result is None
        assert packets[0]["current_single_facts"] == [single]
        assert packets[0]["current_set_facts"] == [{key: member[key] for key in _VIEW_FIELDS}]
        assert _json(member) == original  # Presentation did not mutate the host's source row.
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
async def test_corrective_http_call_keeps_the_same_compact_sources(tmp_path, batch):
    ledger, issuer, _, _ = await _two_members(tmp_path)
    target, sibling = ledger.project().facts
    first = _record(ledger, 3, "An exact source update.")
    second = _record(ledger, 4, "Another source update.") if batch else None
    observed = []

    def handler(request):
        body = json.loads(request.content)
        observed.append(body)
        answer = _withdraw(target)
        if len(observed) == 1:
            answer["target_fact_ref"] = "fact:outside-the-supplied-set"
        if second is not None:
            answer = {
                "decisions": [
                    {"observation_id": first.observation_id, "result": answer},
                    {"observation_id": second.observation_id, "result": {"retain": False}},
                ]
            }
        return _response(request, answer)

    provider = _provider(handler)
    try:
        runtime = _runtime(ledger, issuer, provider)
        assert (await runtime.drain_one()).work_status == "accepted"
        if second is not None:
            assert (await runtime.drain_one()).work_status == "no_change"
        assert len(observed) == 2  # The existing one correction, no new model lane.
        assert observed[0]["messages"][1] == observed[1]["messages"][1]
        packet = json.loads(observed[0]["messages"][1]["content"])
        assert packet["current_set_facts_view"] == "fact-set-source-view.1"
        assert all(set(row) == _VIEW_FIELDS for row in packet["current_set_facts"])
        assert ledger.project().facts[1] == sibling
        assert ledger.project().facts[0].values.status == "withdrawn"
    finally:
        await provider.aclose()
        ledger.close()


class _FullSourceAdapter(FactObservationProposalAdapter):
    """The actual pre-presentation .4 packet (5a2388f1), used only to create history."""

    @staticmethod
    def _messages(observation, **kwargs):
        messages = FactObservationProposalAdapter._messages(observation, **kwargs)
        packet = json.loads(messages[1]["content"])
        packet.pop("current_set_facts_view")
        packet["current_set_facts"] = kwargs["current_set_fact_sources"]
        messages[1]["content"] = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
        return messages




class _FullSourceRuntime(InteractionFactTriggerRuntime):
    async def _record_decision(self, **kwargs):
        source = kwargs["source_event"]
        # Original .4 request identity, before the independent presentation contract.
        kwargs["request_hash"] = hashlib.sha256(
            _json(
                {
                    "adapter_version": "fact-observation-draft.4",
                    "source_event_ref": source.event_id,
                    "source_payload_hash": source.payload_hash,
                    "evaluated_cursor": kwargs["evaluated_cursor"].model_dump(mode="json"),
                    "current_single_fact_sources": kwargs["current_single_fact_sources"],
                    "current_set_fact_sources": kwargs["current_set_fact_sources"],
                    "fact_context_hash": kwargs["fact_context_hash"],
                }
            ).encode()
        ).hexdigest()
        return await super()._record_decision(**kwargs)


def _view_runtime(ledger, issuer, provider, *, full=False):
    from companion_daemon.world_v2.fact_v2_acceptance_runtime import FactV2AcceptanceRuntime

    return (_FullSourceRuntime if full else InteractionFactTriggerRuntime)(
        ledger=ledger,
        acceptance=FactV2AcceptanceRuntime.compose(ledger=ledger, batch_issuer=issuer),
        adapter=(_FullSourceAdapter if full else FactObservationProposalAdapter)(model=provider),
        owner_id="worker:interaction-fact",
    )


@pytest.mark.asyncio
async def test_new_wire_identity_differs_while_old_and_new_durable_choices_recover_unchanged(
    tmp_path, monkeypatch
):
    from companion_daemon.world_v2.fact_trigger import interaction_fact_decision_event_id
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from test_fact_member_withdrawal import WORLD_ID

    runs = []
    for full in (True, False):
        directory = tmp_path / ("old-view" if full else "new-view")
        directory.mkdir()
        ledger, issuer, _, _ = await _two_members(directory)
        target, sibling = ledger.project().facts
        observed = []

        def handler(request):
            observed.append(json.loads(request.content))
            return _response(request, _withdraw(target))

        provider = _provider(handler)
        _record(ledger, 3, "An exact source retraction.")
        original_commit = ledger.commit_at_cursor

        def crash_after_record(events, **kwargs):
            result = original_commit(events, **kwargs)
            if any(event.event_type == "InteractionFactDecisionRecorded" for event in events):
                raise RuntimeError("stopped after durable decision")
            return result

        try:
            with monkeypatch.context() as patch:
                patch.setattr(ledger, "commit_at_cursor", crash_after_record)
                with pytest.raises(RuntimeError, match="durable decision"):
                    await _view_runtime(ledger, issuer, provider, full=full).drain_one()
            assert len(observed) == 1
            decision = ledger.project().interaction_fact_decisions[-1]
            event_id = interaction_fact_decision_event_id(
                trigger_id=decision.trigger_id, fact_context_hash=decision.fact_context_hash
            )
            stored = ledger.lookup_event_commit(event_id)[0].model_dump_json()
        finally:
            await provider.aclose()
            ledger.close()
        reopened = SQLiteWorldLedger(
            path=directory / "members.sqlite", world_id=WORLD_ID, accepted_batch_issuer=issuer
        )
        calls = []

        def forbidden_call(request):
            calls.append(request)
            raise AssertionError("a durable choice must not call the new presentation")

        current_provider = _provider(forbidden_call)
        try:
            assert (
                await _view_runtime(reopened, issuer, current_provider).drain_one()
            ).work_status == "accepted"
            assert calls == []
            assert reopened.lookup_event_commit(event_id)[0].model_dump_json() == stored
            assert reopened.project().facts[0].values.status == "withdrawn"
            assert reopened.project().facts[1] == sibling
            runs.append((observed[0], decision))
        finally:
            await current_provider.aclose()
            reopened.close()
    old_request, old_decision = runs[0]
    new_request, new_decision = runs[1]
    assert len(_json(new_request).encode()) < len(_json(old_request).encode())
    assert old_decision.request_hash != new_decision.request_hash
    assert old_decision.decision_id == new_decision.decision_id
    assert old_decision.decision_hash == new_decision.decision_hash
    assert old_decision.fact_context_hash == new_decision.fact_context_hash
    assert (
        old_decision.adapter_version == new_decision.adapter_version == "fact-observation-draft.4"
    )
    old_packet = json.loads(old_request["messages"][1]["content"])
    new_packet = json.loads(new_request["messages"][1]["content"])
    assert "current_set_facts_view" not in old_packet
    assert new_packet.pop("current_set_facts_view") == "fact-set-source-view.1"
    old_rows = old_packet.pop("current_set_facts")
    new_rows = new_packet.pop("current_set_facts")
    assert old_packet == new_packet
    assert new_rows == [{key: row[key] for key in _VIEW_FIELDS} for row in old_rows]
    old_request["messages"][1] = new_request["messages"][1]
    assert (
        old_request == new_request
    )  # Model, sampling, output ceiling and system contract unchanged.
