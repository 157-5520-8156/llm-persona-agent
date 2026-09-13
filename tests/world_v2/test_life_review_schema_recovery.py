"""A focused schema upgrade separates pending requests and preserves committed bytes.

Verdicts are explicit provider fixtures; these tests establish request identity
and SQLite recovery, not the semantic accuracy of a real reviewer.
"""

import hashlib
import json
import sqlite3

import pytest

from companion_daemon.world_v2 import life_development_source_closure as closure
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentProposalReader
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import (
    WORLD_ID,
    _SequenceModel,
    _novel_origin_review,
    _seed_clock,
    _source_closure_review,
)
from test_world_author_request_audit import _ReceivedAuthor, _json
from test_world_consequence_producer import _advance, _assert_occurrence, _draft, _runtime
from test_world_consequence_source_closure import (
    _consequence_material,
    _legacy_material,
    _review_authority,
)


# Captured by loading the untouched compiler at 1eb6d1c5, not synthesized audit IDs.
_BASELINE_FOCUSED_OUTPUT_HASH = "e43148d5d9c5f13245d618e7622423e03121f4d81c4cdeaf3df9052961f1f390"
_BASELINE_REQUEST_HASHES = {
    (False, False): "b85745a997bd6f181ad0c7a6c18bb66a4efa1233347a37ef43de9b7f202612dc",
    (False, True): "d7eea5b8e2d56c58e5d4bf53c4283997f34b8306ca844b438b8ce65f0d46a4d6",
    (True, False): "d0e8a51adf943fcea07a6cf93499b4ba63b119df9c7ff30a9ecbcfc7d6a93716",
    (True, True): "d605118cde050a4c873690b4b63b5c39d31cfa130abd95e8ee020e57a7b386fd",
}


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _use_baseline_focused_compiler(patch):
    """Emit the old complete schema through the real message/audit producer."""
    original = closure._review_output_contract

    def baseline_output_contract(**kwargs):
        output = original(**kwargs)
        if kwargs["contract"] == "life-development-novel-origin-review.6":
            kinds = output["review_schema"]["$defs"][
                "LifeDevelopmentWorldConsequenceFinding"
            ]["properties"]["violation_kinds"]
            kinds.pop("contains", None)
            kinds.pop("uniqueItems", None)
            assert _hash(output) == _BASELINE_FOCUSED_OUTPUT_HASH
        return output

    patch.setattr(closure, "_review_output_contract", baseline_output_contract)


@pytest.mark.parametrize("current", [False, True], ids=["legacy", "world-consequence"])
@pytest.mark.parametrize("focused", [False, True], ids=["general", "focused"])
def test_only_current_focused_schema_changes_the_real_request_hash(monkeypatch, current, focused):
    manifest, draft = _consequence_material() if current else _legacy_material()
    arguments = dict(
        context={},
        manifest=manifest,
        draft=draft,
        execution_authority=_review_authority(manifest) if current else {},
    )

    def compile_messages():
        if focused:
            return closure.life_development_novel_origin_messages(**arguments)
        return closure.life_development_source_closure_messages(**arguments, cited_events=())

    with monkeypatch.context() as patch:
        _use_baseline_focused_compiler(patch)
        old_messages = compile_messages()
    assert _hash(old_messages) == _BASELINE_REQUEST_HASHES[current, focused]
    current_messages = compile_messages()
    assert closure.life_development_review_packet_identity(current_messages) == (
        closure.life_development_review_packet_identity(old_messages)
    )
    if current and focused:
        assert _hash(current_messages) != _hash(old_messages)
        current_packet = json.loads(current_messages[-1]["content"])
        old_packet = json.loads(old_messages[-1]["content"])
        kinds = current_packet["output_contract"]["review_schema"]["$defs"][
            "LifeDevelopmentWorldConsequenceFinding"
        ]["properties"]["violation_kinds"]
        assert kinds.pop("contains")["enum"]
        assert kinds.pop("uniqueItems") is True
        assert current_packet == old_packet
        assert current_messages[:-1] == old_messages[:-1]
    else:
        assert current_messages == old_messages


def _stored_bytes(path):
    """Read physical event and content payload bytes, not regenerated model dumps."""
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        events = tuple(connection.execute(
            "SELECT ledger_sequence, CAST(event_json AS BLOB), event_hash "
            "FROM world_v2_events WHERE world_id = ? ORDER BY ledger_sequence",
            (WORLD_ID,),
        ))
        content = dict(connection.execute(
            "SELECT content_ref, CAST(text AS BLOB) FROM world_v2_life_content "
            "WHERE world_id = ? ORDER BY content_ref",
            (WORLD_ID,),
        ))
        return events, content
    finally:
        connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True], ids=["pending-new-review", "committed-replay"])
async def test_old_schema_audits_recover_at_the_final_effect_boundary(
    tmp_path, monkeypatch, committed
):
    path = tmp_path / "schema-recovery.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        draft = _draft(wake)
        author = _ReceivedAuthor(store, (_json(draft),))
        general = _SequenceModel(
            model="fixture:general", outputs=(_source_closure_review(decision="supported"),)
        )
        focused = _SequenceModel(
            model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),)
        )
        commit_at_cursor = ledger.commit_at_cursor
        checkpoints = []

        def stop_before_final(events, **kwargs):
            if any(
                event.event_type == "ProposalRecorded"
                and event.payload().get("proposal_kind") == "life_development"
                for event in events
            ):
                checkpoints.append(ledger.project())
                raise InterruptedError("fixture: before final consequence effect")
            return commit_at_cursor(events, **kwargs)

        with monkeypatch.context() as patch:
            _use_baseline_focused_compiler(patch)
            runtime = _runtime(ledger, store, wake, author, general, focused)
            if committed:
                old_result = await _advance(runtime, wake)
                _assert_occurrence(ledger, store, old_result, draft)
            else:
                patch.setattr(ledger, "commit_at_cursor", stop_before_final)
                with pytest.raises(InterruptedError, match="before final consequence effect"):
                    await _advance(runtime, wake)
                assert len(checkpoints) == 1
                assert checkpoints[0].world_occurrences == ()

        assert len(author.received) == general.calls == focused.calls == 1
        before = ledger.project()
        old_audits = tuple(item.audit_json for item in before.model_result_audits)
        old_proposals = tuple(item.proposal_json for item in before.proposal_audits)
        old_focused_hash = _hash(focused.messages[0])
        old_focused_projections = [
            item for item in before.model_result_audits
            if json.loads(item.audit_json)["model_id"] == "fixture:focused"
        ]
        old_focused_audits = [json.loads(item.audit_json) for item in old_focused_projections]
        assert len(old_focused_audits) == 1
        assert old_focused_audits[0]["request_hash"] == old_focused_hash
        assert old_focused_projections[0].proposal_hash is not None
        old_events, old_content = _stored_bytes(path)

        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        cold_author = _ReceivedAuthor(store, ())
        cold_general = _SequenceModel(model="fixture:general", outputs=())
        cold_focused = _SequenceModel(
            model="fixture:focused",
            outputs=() if committed else (_novel_origin_review(decision="supported"),),
        )
        result = await _advance(
            _runtime(ledger, store, wake, cold_author, cold_general, cold_focused), wake
        )
        after = _assert_occurrence(ledger, store, result, draft)
        assert cold_author.received == []
        assert cold_general.calls == 0
        assert cold_focused.calls == (0 if committed else 1)
        assert tuple(item.audit_json for item in after.model_result_audits)[:len(old_audits)] == (
            old_audits
        )
        assert tuple(item.proposal_json for item in after.proposal_audits)[:len(old_proposals)] == (
            old_proposals
        )
        new_events, new_content = _stored_bytes(path)
        assert new_events[:len(old_events)] == old_events
        assert {ref: new_content[ref] for ref in old_content} == old_content
        proposal, _ = ledger.lookup_event_commit(result.proposal_event_ref)
        review_metadata = proposal.payload()["world_author_novel_origin_deliberation"]
        assert LifeDevelopmentProposalReader(ledger=ledger, content_store=store).read_for_occurrence(
            occurrence=after.world_occurrences[0]
        ) is not None
        if committed:
            assert result.proposal_event_ref == old_result.proposal_event_ref
            assert result.occurrence_id == old_result.occurrence_id
            assert after == before
            assert new_events == old_events
            assert new_content == old_content
            assert review_metadata["request_hashes"] == [old_focused_hash]
        else:
            new_focused_hash = _hash(cold_focused.messages[0])
            assert new_focused_hash != old_focused_hash
            assert closure.life_development_review_packet_identity(cold_focused.messages[0]) == (
                closure.life_development_review_packet_identity(focused.messages[0])
            )
            assert review_metadata["request_hashes"] == [new_focused_hash]
            added_audits = after.model_result_audits[len(old_audits):]
            assert len(added_audits) == 1
            added = json.loads(added_audits[0].audit_json)
            assert added["model_id"] == "fixture:focused"
            assert added["request_hash"] == new_focused_hash
            assert added["decision_context"]["decision_subject_hash"] != (
                old_focused_audits[0]["decision_context"]["decision_subject_hash"]
            )
    finally:
        store.close()
        ledger.close()
