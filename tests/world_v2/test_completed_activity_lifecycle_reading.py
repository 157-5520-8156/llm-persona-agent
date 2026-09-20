"""Same-plan lifecycle readings are proven from the original author prefix.

These offline checks prove transported authority and audit recovery, not an LLM
reviewer's semantic accuracy. No lifecycle reading establishes action success.
"""

from copy import deepcopy
from datetime import timedelta
import json

import pytest
import test_day_open_self_directed_intent as day_fixture

from companion_daemon.world_v2.completed_activity_consequence import (
    CompletedActivityConsequence, read_completed_activity_consequence,
    validate_completed_activity_consequence,
)
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_context import compile_life_review_context
from companion_daemon.world_v2.life_development_source_closure import (
    life_development_novel_origin_messages, life_development_source_closure_messages,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_consequence_author_audit import read_world_consequence_author_evidence
from test_day_open_world_consequence_material import _author_runtime, _day_activity, _wake
from test_world_consequence_agency_qualification import _AttemptAuthor
from test_world_stimulus_life_intent import ACTOR, WORLD


build_app = day_fixture.build_app


@pytest.mark.asyncio
async def test_author_and_both_reviewers_receive_only_original_verified_lifecycle_reading(
    tmp_path, monkeypatch, build_app,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "lifecycle-reading.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "completed")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    captured = {}
    try:
        started = next(row.event for row in ledger.export_replay_evidence().events
                       if row.event.event_type == "ActivityStarted")
        wake = _wake(ledger, "lifecycle-reading")
        author = _AttemptAuthor(store, wake, started.event_id)
        runtime = _author_runtime(ledger, store, author)

        async def interrupt_after_author(**kwargs):
            captured.update(kwargs)
            raise InterruptedError("durable author before source review")

        monkeypatch.setattr(runtime, "_source_close_world_author_result", interrupt_after_author)
        with pytest.raises(InterruptedError, match="durable author"):
            await runtime.advance_completed_activity_once(
                completion_event_ref=plan.authority_origin.accepted_event_ref,
                wake_event_ref=wake.event_id, trace_id="trace:lifecycle-reading",
                correlation_id="lifecycle-reading",
            )
        arguments = dict(
            ledger=ledger, content_store=store, manifest=captured["manifest"], actor_ref=ACTOR,
            draft=captured["draft"], raw=captured["raw"],
            author_deliberation=captured["author_deliberation"].authority_payload(),
        )
        evidence = read_world_consequence_author_evidence(**arguments)
        reading = evidence["completed_activity_lifecycle"]
        request = json.loads(json.loads(author.received[0])[-1]["content"])
        assert request["capability_manifest"]["completed_activity_consequence"]["lifecycle_reading"] == reading
        assert reading["plan_id"] == plan.plan_id and reading["owner_actor_ref"] == ACTOR
        execution, completion = reading["execution"], reading["completion"]
        assert execution["event_ref"] == started.event_id
        assert completion["event_ref"] == plan.authority_origin.accepted_event_ref
        assert execution["transition_ref"] != completion["transition_ref"]
        assert execution["expected_plan_revision"] == 1
        assert completion["expected_plan_revision"] == 2
        assert execution["resulting_status"] == "active"
        assert completion["resulting_status"] == "completed"
        assert reading["authority_scope"] == "recorded_lifecycle_status_and_time_only"
        assert "concrete_behavior" in reading["excluded_authority"]
        assert "emotion" in reading["excluded_authority"]
        assert not ledger.project().world_occurrences and not ledger.project().experiences

        review_arguments = dict(
            context={**captured["context"], **compile_life_review_context(captured["capsule"])},
            manifest=captured["manifest"],
            draft=captured["draft"], execution_authority=evidence,
        )
        general = life_development_source_closure_messages(**review_arguments, cited_events=())
        focused = life_development_novel_origin_messages(**review_arguments)
        for messages, key in ((general, "pinned_source_evidence"), (focused, "pinned_authority")):
            packet = json.loads(messages[-1]["content"])
            assert packet[key]["execution_authority"]["completed_activity_lifecycle"] == reading
            assert "different transition identifiers do not imply different activities" in messages[0]["content"]
            assert "no concrete intended action happened or succeeded" in messages[0]["content"]
        for changed in ({k: v for k, v in evidence.items() if k != "completed_activity_lifecycle"},
                        {**evidence, "completed_activity_lifecycle": {**reading, "plan_id": "plan:other"}}):
            for compiler in (life_development_source_closure_messages, life_development_novel_origin_messages):
                extras = {"cited_events": ()} if compiler is life_development_source_closure_messages else {}
                with pytest.raises(ValueError, match="lifecycle reading differs"):
                    compiler(**{**review_arguments, "execution_authority": changed}, **extras)

        descriptor = captured["manifest"].completed_activity_consequence
        pinned = ledger.project_at(captured["manifest"].pinned_cursor)
        for field, value in (("transition_ref", "transition:substitute"),
                             ("logical_time", reading["completion"]["logical_time"])):
            changed = deepcopy(descriptor.model_dump(mode="json"))
            changed["lifecycle_reading"]["execution"][field] = value
            with pytest.raises(ValueError, match="original_pair_mismatch"):
                validate_completed_activity_consequence(
                    ledger=ledger, pinned_state=pinned, actor_ref=ACTOR,
                    descriptor=CompletedActivityConsequence.model_validate_json(json.dumps(changed)),
                )
        # Absence is an explicit historical wire identity, not auto-upgraded.
        historical = read_completed_activity_consequence(
            ledger=ledger, pinned_state=pinned, actor_ref=ACTOR,
            completion_event_ref=descriptor.completion.event_ref,
        )
        assert historical.lifecycle_reading is None
        assert set(historical.model_dump()) == {"contract", "completion", "execution_binding"}
        assert historical.model_dump(mode="json") == {
            key: value for key, value in descriptor.model_dump(mode="json").items()
            if key != "lifecycle_reading"
        }
    finally:
        store.close()
        ledger.close()

    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        _wake(ledger, "later-reading-head")
        assert read_world_consequence_author_evidence(**{
            **arguments, "ledger": ledger, "content_store": store,
        }) == evidence
        pinned = ledger.project_at(captured["manifest"].pinned_cursor)
        later = descriptor.lifecycle_reading.completion.logical_time + timedelta(seconds=1)
        forged = descriptor.model_copy(update={"lifecycle_reading": descriptor.lifecycle_reading.model_copy(
            update={"completion": descriptor.lifecycle_reading.completion.model_copy(update={"logical_time": later})}
        )})
        with pytest.raises(ValueError, match="original_pair_mismatch"):
            validate_completed_activity_consequence(
                ledger=ledger, pinned_state=pinned, actor_ref=ACTOR, descriptor=forged,
            )
        assert len(author.received) == 1
    finally:
        store.close()
        ledger.close()
