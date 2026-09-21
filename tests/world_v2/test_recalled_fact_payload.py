"""Accepted Fact recall preserves exact value authority and historical readings."""

from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from companion_daemon.world_v2.context_capsule import FactRecallItem, HistoricalFactRecallItem
from companion_daemon.world_v2.fact_observation_value import FactObservationValueBinding
from companion_daemon.world_v2.recall_corpus import RecallCorpusCompiler, RecallCorpusSources
from companion_daemon.world_v2.recall_index import RecallDocument, RecallSourceBinding
from companion_daemon.world_v2.recall_model_reading import (
    RECALL_INDEX_POLICY_VERSION, interior_recall_item, supports_fact_reading,
)
from test_recall_corpus import CURSOR, NOW, _sources


VALUE = "乌龙茶"
EXCERPT = "我喜欢乌龙茶，昨天还给同事送了一杯。"


def _fact(*, historical=False, bound=True, excerpt=EXCERPT):
    values = _sources().relevant_facts[0].model_dump(exclude={"status"})
    value_hash = hashlib.sha256(VALUE.encode()).hexdigest()
    values.update(source_excerpt=excerpt, accepted_value_binding=(
        FactObservationValueBinding(value_ref="value:observation:" + value_hash, value_hash=value_hash)
        if bound else None
    ))
    if historical:
        return HistoricalFactRecallItem(**values, valid_from=values["updated_at"], valid_to=NOW)
    return FactRecallItem(**values)


def _document(fact=None):
    fact = fact or _fact()
    bindings = (
        RecallSourceBinding(source_kind="committed_event", authority_type="FactCommittedV2",
                            ref=fact.accepted_fact_event_ref,
                            source_world_revision=fact.accepted_fact_world_revision,
                            immutable_hash=fact.accepted_fact_payload_hash),
        RecallSourceBinding(source_kind="committed_event", authority_type="ObservationRecorded",
                            ref=fact.observation_event_ref,
                            source_world_revision=fact.observation_world_revision,
                            immutable_hash=fact.observation_event_payload_hash),
    )
    sources = RecallCorpusSources(
        relevant_facts=(fact,) if fact.status == "active" else (),
        historical_facts=(fact,) if fact.status == "historical" else (),
        authority_bindings=bindings,
    )
    return RecallCorpusCompiler().compile(
        cursor=CURSOR, actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"), sources=sources,
    )[0]


def test_legacy_documents_without_value_binding_keep_exact_serialized_shape_and_hash():
    document = _document(_fact(bound=False))
    legacy = document.model_dump(mode="json")
    assert "accepted_fact" not in legacy
    canonical = json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Frozen against the pre-payload RecallDocument at be407fe0.
    assert hashlib.sha256(canonical.encode()).hexdigest() == (
        "c23b7e5f71213ea3fc4880aa87a3d629e4388ccf73c1bc2c6d4f08d9a7cdcad8"
    )
    legacy_wire = document.model_dump_json()
    assert RecallDocument.model_validate_json(legacy_wire).model_dump_json() == legacy_wire
    explicit_default = {**legacy, "accepted_fact": None}
    assert RecallDocument.model_validate_json(json.dumps(explicit_default)).model_dump_json() == legacy_wire
    for version in (RECALL_INDEX_POLICY_VERSION, "world-v2-recall-index.hybrid.8"):
        reading = interior_recall_item(document, index_version=version)
        assert reading["text"] == EXCERPT and "accepted_fact" not in reading


@pytest.mark.parametrize("prefix", ["", "长" * 1_100])
def test_new_reading_exposes_only_accepted_value_even_beyond_legacy_excerpt_prefix(prefix):
    fact = _fact(excerpt=prefix + EXCERPT)
    document = _document(fact)
    assert document.accepted_fact == fact
    assert document.text == fact.source_excerpt[:1_024]
    reading = interior_recall_item(document)
    assert reading["text"] == VALUE
    assert EXCERPT not in json.dumps(reading, ensure_ascii=False)
    assert reading["subject_refs"] == [fact.subject_ref]
    assert reading["accepted_fact"] == {
        "fact_ref": fact.fact_id, "accepted_event_ref": fact.accepted_fact_event_ref,
        "subject_ref": fact.subject_ref, "predicate_code": fact.predicate_code,
        "status": "active", "confidence_bp": fact.confidence_bp,
        "occurred_at": fact.occurred_at.isoformat(), "committed_at": fact.committed_at.isoformat(),
        "updated_at": fact.updated_at.isoformat(), "valid_from": fact.updated_at.isoformat(),
        "valid_to": None,
    }
    restored = RecallDocument.model_validate_json(document.model_dump_json())
    assert restored.accepted_fact == fact and interior_recall_item(restored) == reading


@pytest.mark.parametrize("version,attributed", [
    ("world-v2-recall-index.hybrid.8", True),
    ("world-v2-recall-index.hybrid.8+embedding:fixture.1", True),
    ("world-v2-recall-index.hybrid.7+embedding:fixture.1", False),
    ("recall-index:legacy", False),
])
def test_recorded_index_version_selects_exact_old_shape_even_with_new_fact_payload(version, attributed):
    document = _document()
    assert not supports_fact_reading(version)
    assert interior_recall_item(document, index_version=version) == {
        "source_ref": document.source_item_ref, "memory_kind": "semantic",
        "source_slice": "relevant_facts", "authority": "world_fact", "epistemic_scope": "world_fact",
        "text": EXCERPT, "occurred_from": document.occurred_from.isoformat(),
        "occurred_to": None, "privacy_class": document.privacy_class,
        **({"subject_refs": [document.accepted_fact.subject_ref]} if attributed else {}),
    }


def test_historical_payload_roundtrip_preserves_canonical_fact_alias_and_validity():
    fact = _fact(historical=True)
    document = _document(fact)
    restored = RecallDocument.model_validate_json(document.model_dump_json())
    assert restored == document and isinstance(restored.accepted_fact, HistoricalFactRecallItem)
    reading = interior_recall_item(restored, index_version=RECALL_INDEX_POLICY_VERSION + "+embedding:fixture.1")
    assert supports_fact_reading(RECALL_INDEX_POLICY_VERSION + "+embedding:fixture.1")
    assert document.source_item_ref == f"{fact.fact_id}:historical:{fact.accepted_fact_world_revision}"
    assert reading["source_ref"] == fact.accepted_fact_event_ref
    assert reading["accepted_fact"]["fact_ref"] == fact.fact_id
    assert reading["accepted_fact"]["accepted_event_ref"] == fact.accepted_fact_event_ref
    assert reading["accepted_fact"]["status"] == "historical"
    assert reading["accepted_fact"]["valid_from"] == fact.valid_from.isoformat()
    assert reading["accepted_fact"]["valid_to"] == fact.valid_to.isoformat()


@pytest.mark.parametrize("fault", [
    "subject", "fact_subject", "privacy", "status", "occurrence", "validity", "excerpt",
    "fact_hash", "observation_hash", "fact_type", "observation_type", "extra_authority", "value_hash",
])
def test_forged_fact_payload_cannot_change_document_scope_or_authority(fault):
    value = deepcopy(_document().model_dump(mode="json"))
    fact = value["accepted_fact"]
    if fault == "subject":
        value["subject_refs"] = ["agent:companion"]
    elif fault == "fact_subject":
        fact["subject_ref"] = "agent:companion"
    elif fault == "privacy":
        value["privacy_class"] = "public"
    elif fault == "status":
        value["status"] = "historical"
    elif fault == "occurrence":
        value["occurred_from"] = (NOW - timedelta(days=4)).isoformat()
    elif fault == "validity":
        value["valid_to"] = NOW.isoformat()
    elif fault == "excerpt":
        value["text"] = VALUE
    elif fault in {"fact_hash", "observation_hash"}:
        key = "accepted_fact_payload_hash" if fault == "fact_hash" else "observation_event_payload_hash"
        fact[key] = "f" * 64
    elif fault in {"fact_type", "observation_type"}:
        ref = fact["accepted_fact_event_ref"] if fault == "fact_type" else fact["observation_event_ref"]
        binding = next(item for item in value["source_bindings"] if item["ref"] == ref)
        binding["authority_type"] = "ObservationRecorded" if fault == "fact_type" else "FactCommittedV2"
        value["source_bindings"].sort(key=lambda item: (
            item["source_kind"], item["authority_type"], item["ref"], item["source_world_revision"], item["immutable_hash"],
        ))
    elif fault == "extra_authority":
        binding = {**value["source_bindings"][0], "ref": "event:extra"}
        value["source_bindings"].append(binding)
        value["source_bindings"].sort(key=lambda item: (
            item["source_kind"], item["authority_type"], item["ref"], item["source_world_revision"], item["immutable_hash"],
        ))
        value["source_refs"] = sorted([*value["source_refs"], binding["ref"]])
    else:
        value_hash = hashlib.sha256("不在原话里的值".encode()).hexdigest()
        fact["accepted_value_binding"] = {
            "contract": "fact-observation-value-binding.1",
            "value_ref": "value:observation:" + value_hash, "value_hash": value_hash,
        }
    with pytest.raises(ValueError, match="accepted Fact"):
        RecallDocument.model_validate_json(json.dumps(value))
