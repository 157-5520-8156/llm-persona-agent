"""A role's own time choice survives HTTP, correction, acceptance and replay."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior import CharacterInterior
from companion_daemon.world_v2.character_interior.structured_role import (
    StructuredCharacterRoleFaculty,
)
from companion_daemon.world_v2.batch_invariants import validate_commit_batch
from companion_daemon.world_v2.schemas import WorldEvent
from companion_daemon.world_v2.ledger import WorldLedger
from test_character_interior import _Projection
from test_life_development_runtime import (
    NOW,
    WORLD_ID,
    _SequenceModel,
    _location_bound_world_draft,
    _location_capability,
    _runtime,
    _seed_clock,
)


class PinnedProjection(_Projection):
    async def project(self, *, subject):
        value = await super().project(subject=subject)
        value["logical_time"] = subject.logical_time
        value["facets"]["private_self"]["source_refs"] = tuple(subject.source_refs)
        return value


@pytest.mark.asyncio
@pytest.mark.parametrize("omission", ["absent", "null"])
async def test_same_role_corrects_missing_times_then_commits_only_its_selected_window(omission):
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    location = _location_capability()
    starts = NOW + timedelta(minutes=7)
    ends = NOW + timedelta(minutes=19)
    bodies = []
    responses = []

    async def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        view = json.loads(body["messages"][1]["content"])
        completion = {
            "decision": "accept",
            "intention_summary": "I choose a brief visit during this opportunity.",
            "importance_bp": 4500,
            "participant_refs": [],
        }
        if len(bodies) == 1:
            if omission == "null":
                completion.update(opens_at=None, closes_at=None)
        else:
            completion.update(opens_at=starts.isoformat(), closes_at=ends.isoformat())
        raw = json.dumps(
            {
                "status": "decision",
                "summary": "My own bounded plan.",
                "attended_source_refs": [],
                "decision": {
                    "source_refs": view["capability_manifest"]["source_refs"],
                    "payload": {"completion": completion},
                },
                "recall_query": None,
                "proposals": [],
            },
            ensure_ascii=False,
        )
        responses.append(raw)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": body["tool_choice"]["function"]["name"],
                                        "arguments": raw,
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
            },
        )

    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(respond),
    )
    interior = CharacterInterior(
        projection=PinnedProjection(),
        role=StructuredCharacterRoleFaculty(model=model, model_id="deepseek-v4-flash"),
    )
    runtime, _ = _runtime(
        ledger=ledger,
        wake=wake,
        world_author=_SequenceModel(
            model="offline-world-author",
            outputs=(
                _location_bound_world_draft(
                    wake=wake,
                    capability=location,
                    timing={"mode": "now", "duration_minutes": 60},
                    privacy_class="shareable",
                    causal_authority="character_choice",
                    outcome_resolution_authority="character_choice",
                ),
            ),
        ),
        character_interior=interior,
        location_capability=location,
    )
    try:
        result = await runtime.advance_once(
            wake_event_ref=wake.event_id,
            trace_id="trace:personal-window-http",
            correlation_id="correlation:personal-window-http",
        )
    finally:
        await model.aclose()
    assert result.status == "plan_committed", result
    assert len(bodies) == 2
    projection = ledger.project()
    assert projection == ledger.rebuild()
    assert len(projection.plans) == 1
    assert projection.plans[0].scheduled_window.opens_at == starts
    assert projection.plans[0].scheduled_window.closes_at == ends
    proposal = ledger.lookup_event_commit(result.proposal_event_ref)[0].payload()
    assert proposal["character_choice"]["opens_at"] == starts.isoformat()
    assert proposal["character_choice"]["closes_at"] == ends.isoformat()
    binding = proposal["character_interior_decision"]
    assert (
        binding["decision"]["payload"]["contract"] == "character-interior-life-development-choice.2"
    )
    assert binding["author_lineage"]["attempt_ordinal"] == 1
    assert binding["author_lineage"]["parent_model_call_id"]
    assert (
        binding["author_lineage"]["response_hash"]
        == "sha256:" + hashlib.sha256(responses[1].encode()).hexdigest()
    )
    assert (
        bodies[0]["tool_choice"]["function"]["name"] == "character_role_life_development_choice_v2"
    )

    # Even a coordinated downstream edit within availability cannot replace
    # the role's hash-bound, narrower personal window.
    proposal_event, commit = ledger.lookup_event_commit(result.proposal_event_ref)
    plan_event = next(
        ledger.lookup_event_commit(ref)[0]
        for ref in commit.event_ids
        if ledger.lookup_event_commit(ref)[0].event_type == "ActivityPlanned"
    )
    changed_end = (ends + timedelta(minutes=1)).isoformat()
    edited_plan = plan_event.payload()
    edited_plan["plan"]["scheduled_window"]["closes_at"] = changed_end
    edited_proposal = proposal_event.payload()
    edited_proposal["character_choice"]["closes_at"] = changed_end
    edited_proposal["character_choice_hash"] = hashlib.sha256(
        json.dumps(
            edited_proposal["character_choice"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    edited_events = tuple(
        WorldEvent.from_payload(
            payload=payload,
            **event.model_dump(exclude={"payload_json", "payload_hash"}),
        )
        for event, payload in ((proposal_event, edited_proposal), (plan_event, edited_plan))
    )
    with pytest.raises(ValueError, match="Plan timing differs from character-authored window"):
        validate_commit_batch(
            edited_events,
            expected_world_revision=0,
            accepted_manifest_v3_authorized=True,
        )


def _personal_choice_raw():
    return json.dumps(
        {
            "decision": "accept",
            "intention_summary": "A short visit of my own choosing.",
            "importance_bp": 4500,
            "opens_at": (NOW + timedelta(minutes=7)).isoformat(),
            "closes_at": (NOW + timedelta(minutes=19)).isoformat(),
            "participant_refs": [],
        }
    )


def _world_offer(wake, location):
    return _location_bound_world_draft(
        wake=wake,
        capability=location,
        timing={"mode": "now", "duration_minutes": 60},
        privacy_class="shareable",
        causal_authority="character_choice",
        outcome_resolution_authority="character_choice",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("changed_content", [False, True])
async def test_cold_recovery_requires_exact_durable_role_audit_and_preserves_versioned_time(
    tmp_path,
    monkeypatch,
    historical,
    changed_content,
):
    """V1 fixture uses its original subject hash and complete audit chain.

    The historical fixture producer changes the recorded InnerDecision before
    persistence, including response lineage. Recovery gets no flag or parser
    override: only the actual SQLite ModelResult/Proposal/content prefix.
    """
    from companion_daemon.world_v2.character_interior import InnerDecision
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from companion_daemon.world_v2.proposal_audit_schemas import canonical_json
    import sqlite3

    path = tmp_path / "world.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    location = _location_capability()
    runtime, _ = _runtime(
        ledger=ledger,
        wake=wake,
        store=store,
        location_capability=location,
        world_author=_SequenceModel(model="world-author", outputs=(_world_offer(wake, location),)),
        character_interior=_SequenceModel(model="character", outputs=(_personal_choice_raw(),)),
    )
    choose = runtime._character_initial_choice
    record = runtime._record_character_interior_decision
    subject = {}
    content_ref = None

    async def capture_subject(**kwargs):
        subject.update(kwargs)
        return await choose(**kwargs)

    def persist_then_crash(**kwargs):
        nonlocal content_ref
        if historical:
            # Faithful frozen-v1 wire semantics: null explicitly meant the
            # offered envelope. All model/Proposal/sidecar hashes are original
            # to these fixture bytes; no existing event is rewritten.
            value = kwargs["decision"].model_dump(mode="json")
            payload = value["decision"]["payload"]
            payload["contract"] = "character-interior-life-development-choice.1"
            payload["completion"].update(opens_at=None, closes_at=None)
            response_hash = (
                "sha256:"
                + hashlib.sha256(canonical_json(payload["completion"]).encode()).hexdigest()
            )
            value["author_lineage"]["response_hash"] = response_hash
            for key in ("initial_author_lineage", "final_author_lineage"):
                value["private_self_lineage"][key]["response_hash"] = response_hash
            kwargs["decision"] = InnerDecision.model_validate_json(canonical_json(value))
            kwargs["decision_subject_hash"] = hashlib.sha256(
                canonical_json(
                    {
                        "external_opportunity": subject["draft"].model_dump(mode="json"),
                        "offered_window": subject["offered_window"].model_dump(mode="json"),
                        "active_aspiration_source_refs": subject["active_aspiration_source_refs"],
                    }
                ).encode()
            ).hexdigest()
        recorded = record(**kwargs)
        content_ref = recorded.inner_decision_content_ref
        raise RuntimeError("fixture stopped after durable role audit")

    monkeypatch.setattr(runtime, "_character_initial_choice", capture_subject)
    monkeypatch.setattr(runtime, "_record_character_interior_decision", persist_then_crash)
    with pytest.raises(RuntimeError, match="after durable role audit"):
        await runtime.advance_once(
            wake_event_ref=wake.event_id,
            trace_id="trace:crash-role-audit",
            correlation_id="correlation:personal-window",
        )
    assert ledger.project().plans == ()
    original_events = {
        item.event_id: ledger.lookup_event_commit(item.event_id)[0].payload_json
        for item in ledger.project().committed_world_event_refs
    }
    store.close()
    ledger.close()
    if changed_content:
        with sqlite3.connect(path) as db:
            db.execute(
                "UPDATE world_v2_life_content SET text = text || ' ' WHERE content_ref = ?",
                (content_ref,),
            )
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
    forbidden_world = _SequenceModel(model="world-author", outputs=())
    forbidden_role = _SequenceModel(model="character", outputs=())
    cold, _ = _runtime(
        ledger=ledger,
        wake=wake,
        store=store,
        location_capability=location,
        world_author=forbidden_world,
        character_interior=forbidden_role,
    )
    try:
        if changed_content:
            with pytest.raises(ValueError, match="decision bytes are invalid"):
                await cold.advance_once(
                    wake_event_ref=wake.event_id,
                    trace_id="trace:cold-role-audit",
                    correlation_id="correlation:personal-window",
                )
            assert ledger.project().plans == ()
        else:
            result = await cold.advance_once(
                wake_event_ref=wake.event_id,
                trace_id="trace:cold-role-audit",
                correlation_id="correlation:personal-window",
            )
            assert result.status == "plan_committed", result
            window = ledger.project().plans[0].scheduled_window
            assert window.opens_at == (NOW if historical else NOW + timedelta(minutes=7))
            assert window.closes_at == NOW + timedelta(minutes=60 if historical else 19)
            assert ledger.project() == ledger.rebuild()
            proposal = ledger.lookup_event_commit(result.proposal_event_ref)[0].payload()
            contract = proposal["character_interior_decision"]["decision"]["payload"]["contract"]
            assert (
                contract == f"character-interior-life-development-choice.{1 if historical else 2}"
            )
        assert forbidden_world.calls == forbidden_role.calls == 0
        assert all(
            ledger.lookup_event_commit(ref)[0].payload_json == raw
            for ref, raw in original_events.items()
        )
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_fresh_v1_result_cannot_become_legacy_recovery_on_retry():
    from companion_daemon.world_v2.character_interior import InnerDecision
    from companion_daemon.world_v2.proposal_audit_schemas import canonical_json
    from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit

    class DowngradedRole(_SequenceModel):
        async def consider(self, opportunity):
            result = await super().consider(opportunity)
            value = result.model_dump(mode="json")
            value["decision"]["payload"]["contract"] = (
                "character-interior-life-development-choice.1"
            )
            return InnerDecision.model_validate_json(canonical_json(value))

    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    location = _location_capability()
    role = DowngradedRole(
        model="character", outputs=(_personal_choice_raw(), _personal_choice_raw())
    )
    world = _SequenceModel(model="world-author", outputs=(_world_offer(wake, location),))
    runtime, _ = _runtime(
        ledger=ledger,
        wake=wake,
        location_capability=location,
        world_author=world,
        character_interior=role,
    )
    for _ in range(2):
        result = await runtime.advance_once(
            wake_event_ref=wake.event_id,
            trace_id="trace:no-fresh-downgrade",
            correlation_id="correlation:personal-window",
        )
        assert result.status == "technical_failure"
        assert result.reason_code == "life_development.character_interior_decision_invalid"
    assert world.calls == 1
    assert role.calls == 2
    assert ledger.project().plans == ()
    assert all(
        RecordedModelResultAudit.model_validate_json(item.audit_json).route.reason_code
        != "life_development.character_interior"
        for item in ledger.project().model_result_audits
    )
