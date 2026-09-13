"""Real ledger -> chat Capsule -> review source table, without provider calls."""
import json
import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.context_capsule import ResolvedSourceBinding, source_bindings_hash
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.deliberation import TriggerMessage
from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_source_closure_protocol import _review_material
from companion_daemon.world_v2.visible_source_evidence_cards import compile_visible_evidence_cards
from companion_daemon.world_v2.visible_source_review_receipt import (
    prepare_visible_source_review, verify_visible_source_review_receipt,
    VisibleSourceReviewReceipt,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

from test_character_prehistory import ACTOR, started_ledger
from test_prehistory_memory_source import _choice, _install
from test_visible_source_composer import _request
from test_life_projection import WORLD_ID, event, commit
from test_interaction_fact_trigger_runtime import _observation
from test_visible_source_review_receipt import _candidate, _record


def _capsule(ledger, actor=ACTOR, trigger="world-start"):
    return context_capsule_compiler_from_ledger(ledger=ledger).compile(
        query_from_projection(ledger.project(), actor_ref=actor, trigger_ref=trigger),
    )


def _table(capsule):
    return compile_visible_source_table(request=_request(capsule), capsule=capsule).as_dict()


def test_retained_history_reaches_chat_and_review_with_two_bound_sources(tmp_path):
    ledger = started_ledger(tmp_path / "chat.sqlite")
    try:
        row = _install(ledger)
        assert not _capsule(ledger).active_memory_candidates.items
        pending = _choice(ledger, prehistory_memory_binding(row))
        assert not _capsule(ledger).active_memory_candidates.items
        _choice(ledger, prehistory_memory_binding(row), before=pending, status="active")
        capsule = _capsule(ledger)
        item, = capsule.active_memory_candidates.items
        value = json.loads(item.payload_json)
        excerpt, = value["source_excerpts"]
        assert excerpt["text"] == row.record.statement
        assert excerpt["prehistory"]["occurred_until"] == row.record.model_dump(mode="json")["occurred_until"]
        refs = {binding.ref for binding in item.source_bindings}
        assert refs == {row.accepted_event_ref, excerpt["prehistory"]["archive_event_ref"]}
        table = _table(capsule)
        assert table["contract"] == "visible-source-row-table.4"
        material, = [entry["material"] for entry in table["source_materials"]
                     if entry["material"].get("lane") == "active_memory_candidates"]
        assert material["item"]["value"] == value
        assert "runtime_occurrence_or_current_activity" in material["does_not_authorize"]
        source_rows = [source for source in table["source_references"]
                       if source["source_ref"] in refs]
        assert len(source_rows) == 2
        assert all(source["support_eligibility"] == "eligible"
                   and source["support_subject_role"] == "companion" for source in source_rows)
        cards = compile_visible_evidence_cards(references=table["source_references"], materials=[material])
        assert cards["source_materials"][0]["item"]["value"] == value
        assert row.record.statement in capsule.model_content_json
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
        ledger.close()
        ledger = SQLiteWorldLedger(path=tmp_path / "chat.sqlite", world_id=WORLD_ID)
        assert _capsule(ledger).model_dump_json() == capsule.model_dump_json()
    finally:
        ledger.close()


@pytest.mark.parametrize("mutation", ["missing_archive", "wrong_archive_hash", "changed_excerpt"])
def test_review_refuses_history_with_incomplete_or_changed_source_material(tmp_path, mutation):
    ledger = started_ledger(tmp_path / "proof.sqlite")
    try:
        row = _install(ledger)
        source = prehistory_memory_binding(row)
        pending = _choice(ledger, source)
        _choice(ledger, source, before=pending, status="active")
        table = _table(_capsule(ledger))
        material, = [entry["material"] for entry in table["source_materials"]
                     if entry["material"].get("lane") == "active_memory_candidates"]
        changed = deepcopy(material)
        item = changed["item"]
        archive_ref = item["value"]["source_excerpts"][0]["prehistory"]["archive_event_ref"]
        if mutation == "changed_excerpt":
            item["value"]["source_excerpts"][0]["text"] = "今天又去了校刊编辑室。"
        else:
            if mutation == "missing_archive":
                item["source_bindings"] = [binding for binding in item["source_bindings"]
                                           if binding["ref"] != archive_ref]
            else:
                for binding in item["source_bindings"]:
                    if binding["ref"] == archive_ref:
                        binding["immutable_hash"] = "f" * 64
            # The outer binding digest is still internally consistent; the
            # typed historical reading must independently require both proofs.
            item["source_hash"] = source_bindings_hash(tuple(
                ResolvedSourceBinding.model_validate(binding) for binding in item["source_bindings"]
            ))
        assert _review_material(material)[1]
        assert not _review_material(changed)[1]
    finally:
        ledger.close()


@pytest.mark.parametrize("version", ["1", "8"])
def test_history_table_reaches_review_request_and_receipt_protocol(tmp_path, version):
    ledger = started_ledger(tmp_path / "receipt.sqlite")
    try:
        row = _install(ledger)
        source = prehistory_memory_binding(row)
        pending = _choice(ledger, source)
        _choice(ledger, source, before=pending, status="active")
        observation, _ = _observation()
        now = ledger.project().logical_time
        text = "你高中参与校刊时做过什么？"
        observation = observation.model_copy(update={
            "world_id": WORLD_ID, "logical_time": now, "created_at": now,
            "received_at": now, "text": text,
            "payload_hash": hashlib.sha256(text.encode()).hexdigest(),
        })
        incoming = event("event:history-question", "ObservationRecorded",
                         observation.model_dump(mode="json"), at=now).model_copy(update={
                             "actor": observation.actor, "source": observation.source,
                             "trace_id": observation.trace_id, "causation_id": observation.causation_id,
                             "correlation_id": observation.correlation_id,
                         })
        commit(ledger, [incoming])
        recorded, cursor = ledger.lookup_event_commit(incoming.event_id)
        capsule = _capsule(ledger, trigger=incoming.event_id)
        request = _request(capsule).model_copy(update={"trigger_message": TriggerMessage(
            event_ref=incoming.event_id, event_payload_hash="sha256:" + recorded.payload_hash,
            source_world_revision=cursor.world_revision, observation_ref=observation.observation_id,
            actor=observation.actor, channel=observation.channel,
            reply_target="fixture:local", text=text,
        )})
        table = compile_visible_source_table(request=request, capsule=capsule)
        assert table.as_dict()["contract"] == "visible-source-row-table.4"
        prepared = prepare_visible_source_review(
            candidate=_candidate(SimpleNamespace(request=request), texts=(row.record.statement,)),
            source_table=table, source_ref_aliases={}, review_version=version,
        )
        assert row.record.statement in json.dumps(prepared.as_dict()["request"], ensure_ascii=False)
        assert "occurred_until" in json.dumps(prepared.as_dict()["request"])
        if version == "1":
            index = next(source["source_ref_index"] for source in table.source_references()
                         if source["source_ref"] == row.accepted_event_ref)
            # Fixture verdict checks receipt mechanics only, not LLM semantics.
            raw = json.dumps({"contract": "visible-beat-source-verdict.1", "decisions": [{
                "beat_index": 0, "verdict": "closed", "semantic_role": "external_proposition",
                "subject_role": "companion", "source_ref_indexes": [index],
            }]})
            receipt, author, review = _record(prepared, raw=raw)
            restored = VisibleSourceReviewReceipt.model_validate_json(receipt.model_dump_json())
            assert verify_visible_source_review_receipt(
                receipt=restored, expected_prepared=prepared,
                expected_author=author, expected_review=review,
            ) == receipt
    finally:
        ledger.close()


def test_forgotten_history_leaves_chat_and_review_while_archive_remains(tmp_path):
    ledger = started_ledger(tmp_path / "forget.sqlite")
    try:
        row = _install(ledger)
        source = prehistory_memory_binding(row)
        pending = _choice(ledger, source)
        active = _choice(ledger, source, before=pending, status="active")
        assert _capsule(ledger).active_memory_candidates.items
        assert not _capsule(ledger, "actor:other").active_memory_candidates.items
        _choice(ledger, source, before=active, status="forgotten")
        capsule = _capsule(ledger)
        assert not capsule.active_memory_candidates.items
        assert row.record.statement not in capsule.model_content_json
        assert row.record.statement not in json.dumps(_table(capsule), ensure_ascii=False)
        assert ledger.project().prehistory_records[0].record.statement == row.record.statement
    finally:
        ledger.close()
