"""Bounded internal sidecars and durable Life recovery, without provider calls."""

from dataclasses import replace
import json

import pytest

from companion_daemon.world_v2.life_capability_manifest_audit import (
    read_capability_manifest_audit, record_capability_manifest_audit,
    validate_capability_manifest_binding,
)
from companion_daemon.world_v2.life_content_events import LifeContentRecordedPayload
from companion_daemon.world_v2.life_content_store import (
    MAX_CAPABILITY_MANIFEST_AUDIT_UTF8_BYTES, InMemoryImmutableLifeContentStore,
    SQLiteImmutableLifeContentStore, StoredLifeContent, life_content_payload_hash,
)
from companion_daemon.world_v2.life_development_draft import LifeDevelopmentNoOpDraft
from companion_daemon.world_v2.life_development_runtime import _LifeDevelopmentAttempt, _LifeDevelopmentModelRun
from companion_daemon.world_v2.outcome_candidate_reader import OutcomeCandidateReader
from companion_daemon.world_v2.proposal_audit_schemas import canonical_json
from companion_daemon.world_v2.schemas import OutcomeCandidateDescriptor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import (
    WORLD_ID, _SequenceModel, _manifest, _projection_cursor, _runtime, _seed_clock,
)
from test_outcome_candidate_reader import _occurrence


def _case(tmp_path, *, large=True):
    path = tmp_path / "manifest-recovery.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    cursor = _projection_cursor(ledger)
    manifest = _manifest(wake, pinned_cursor=cursor)
    if large:
        # Transport-only references; this test authors no event or experience.
        manifest = manifest.model_copy(update={"grounding_refs": tuple(f"fixture:source:{i:04}:" + "x" * 120 for i in range(110))})
    runtime, _ = _runtime(ledger=ledger, wake=wake, store=store,
        world_author=_SequenceModel(model="must-not-call", outputs=()),
        character_interior=_SequenceModel(model="must-not-call", outputs=()))
    capsule = runtime._capsule_compiler.compile_for_deliberation(None).capsule
    run = _LifeDevelopmentModelRun(model_id="offline-fixture", parsed=LifeDevelopmentNoOpDraft(decision="no_op"),
        attempts=(_LifeDevelopmentAttempt(request_hash="a" * 64, raw_output='{"decision":"no_op"}',
                                         status="proposal_validated"),))
    args = dict(proposal_id="proposal:manifest-sidecar", role="world_author", run=run, wake=wake,
                capsule=capsule, manifest=manifest, decision_subject_hash="b" * 64,
                expected_cursor=cursor, trace_id="trace:manifest", correlation_id="correlation:manifest")
    return path, ledger, store, runtime, args


def test_large_manifest_is_durable_before_success_and_recovers_without_model(tmp_path):
    path, ledger, store, runtime, args = _case(tmp_path)
    raw = canonical_json(args["manifest"].model_dump(mode="json", exclude_computed_fields=True))
    assert 12_000 < len(raw) < MAX_CAPABILITY_MANIFEST_AUDIT_UTF8_BYTES
    before = ledger.export_replay_evidence()
    bound = runtime._record_model_run(**args)
    record = store.read_exact(content_ref=bound.capability_manifest_content_ref)
    assert record.content_kind == "capability_manifest_audit" and record.text == raw
    assert ledger.project().world_revision == before.projection.world_revision
    audits = tuple(item.audit_json for item in ledger.project().model_result_audits)
    store.close()
    ledger.close()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    try:
        restored, _ = _runtime(ledger=ledger, wake=args["wake"], store=store,
            world_author=_SequenceModel(model="must-not-call", outputs=()),
            character_interior=_SequenceModel(model="must-not-call", outputs=()))
        result = restored._recover_successful_model_run(proposal_id=args["proposal_id"], role="world_author",
            current_world_revision=ledger.project().world_revision, expected_subject_hash=args["decision_subject_hash"])
        assert result[0] == args["run"].final_raw and result[-1] == args["manifest"]
        assert tuple(item.audit_json for item in ledger.project().model_result_audits) == audits
    finally:
        store.close()
        ledger.close()


@pytest.mark.parametrize("failure", ["unavailable", "oversize"])
def test_manifest_persistence_failure_cannot_commit_recoverable_success(tmp_path, monkeypatch, failure):
    _, ledger, store, runtime, args = _case(tmp_path)
    before = ledger.export_replay_evidence()
    if failure == "unavailable":
        put = store.put_if_absent
        def unavailable(record):
            if record.content_kind == "capability_manifest_audit":
                raise OSError("fixture: manifest storage unavailable")
            return put(record)
        monkeypatch.setattr(store, "put_if_absent", unavailable)
    else:
        args["manifest"] = args["manifest"].model_copy(update={"grounding_refs": ("x" * MAX_CAPABILITY_MANIFEST_AUDIT_UTF8_BYTES,)})
    try:
        with pytest.raises((ValueError, OSError)):
            runtime._record_model_run(**args)
        assert ledger.export_replay_evidence() == before
        assert runtime._recover_successful_model_run(proposal_id=args["proposal_id"], role="world_author",
            current_world_revision=ledger.project().world_revision, expected_subject_hash=args["decision_subject_hash"]) is None
    finally:
        store.close()
        ledger.close()


@pytest.mark.parametrize("fault", ["missing", "hash", "text", "kind", "contract", "count", "downgrade", "extra", "missing_field"])
def test_exact_new_binding_rejects_unavailable_tampered_or_downgraded_sidecar(tmp_path, fault):
    _, ledger, source_store, _, args = _case(tmp_path, large=False)
    source_store.close()
    ledger.close()
    store = InMemoryImmutableLifeContentStore()
    binding = record_capability_manifest_audit(content_store=store, content_ref="audit:manifest", manifest=args["manifest"])
    value = binding.model_dump(mode="json")
    original = store.read_exact(content_ref=binding.content_ref)
    if fault == "missing":
        store._records.clear()
    elif fault == "hash":
        value["content_payload_hash"] = "c" * 64
    elif fault == "text":
        text = original.text + " "
        store._records[original.content_ref] = replace(original, text=text, content_payload_hash=life_content_payload_hash(text))
    elif fault == "kind":
        store._records[original.content_ref] = replace(original, content_kind="outcome_candidate")
    elif fault == "contract":
        value["contract"] = "unknown.2"
    elif fault == "count":
        value["utf8_bytes"] += 1
    elif fault == "downgrade":
        value = {key: value[key] for key in ("content_ref", "content_payload_hash")}
    elif fault == "missing_field":
        del value["content_kind"]
    else:
        value["inline_manifest"] = json.loads(original.text)
    with pytest.raises(ValueError):
        read_capability_manifest_audit(content_store=store, binding=value)


def test_historical_binding_requires_original_legacy_kind_and_exact_stored_bytes(tmp_path):
    _, ledger, source_store, _, args = _case(tmp_path, large=False)
    source_store.close()
    ledger.close()
    store = InMemoryImmutableLifeContentStore()
    text = canonical_json(args["manifest"].model_dump(mode="json", exclude_computed_fields=True))
    legacy = {"content_ref": "legacy:manifest", "content_payload_hash": life_content_payload_hash(text)}
    record = StoredLifeContent(**legacy, content_kind="outcome_candidate", text=text)
    store.put_if_absent(record)
    assert read_capability_manifest_audit(content_store=store, binding=legacy) == (record, args["manifest"])
    with pytest.raises(ValueError, match="sidecar is unavailable"):
        record_capability_manifest_audit(content_store=store, content_ref=legacy["content_ref"], manifest=args["manifest"])
    assert store.read_exact(content_ref=record.content_ref) == record
    store._records.clear()
    with pytest.raises(ValueError, match="sidecar is unavailable"):
        read_capability_manifest_audit(content_store=store, binding=legacy)
    with pytest.raises(ValueError):
        validate_capability_manifest_binding({**legacy, "contract": "unknown"})


def test_audit_limit_counts_utf8_bytes_and_kind_cannot_authorize_life_content():
    text = "界" * (MAX_CAPABILITY_MANIFEST_AUDIT_UTF8_BYTES // 3 + 1)
    assert len(text) < MAX_CAPABILITY_MANIFEST_AUDIT_UTF8_BYTES < len(text.encode())
    with pytest.raises(ValueError, match="audit byte limit"):
        StoredLifeContent(content_ref="audit:too-large", content_kind="capability_manifest_audit",
            text=text, content_payload_hash=life_content_payload_hash(text))
    descriptor = dict(content_id="content:fixture", content_kind="occurrence_result", content_ref="audit:only",
        content_payload_hash="a" * 64, privacy_class="private", source_kind="occurrence_settlement",
        source_event_ref="event:fixture", source_world_revision=1, source_payload_hash="b" * 64,
        source_entity_id="entity:fixture", source_entity_revision=1)
    LifeContentRecordedPayload.model_validate(descriptor)
    with pytest.raises(ValueError, match="content_kind"):
        LifeContentRecordedPayload.model_validate({**descriptor, "content_kind": "capability_manifest_audit"})
    store = InMemoryImmutableLifeContentStore()
    text = "internal audit fixture"
    record = StoredLifeContent(content_ref="audit:only", content_kind="capability_manifest_audit",
                              text=text, content_payload_hash=life_content_payload_hash(text))
    store.put_if_absent(record)
    candidate = OutcomeCandidateDescriptor(candidate_result_ref="candidate:fixture", result_id="result:fixture",
        result_payload_ref="payload:fixture", result_payload_hash=record.content_payload_hash,
        privacy_class="private", content_ref=record.content_ref, content_payload_hash=record.content_payload_hash)
    result = OutcomeCandidateReader(store=store).read(occurrence=_occurrence(candidate), viewer_privacy_ceiling="private")
    assert result.candidates == () and result.suppressions[0].reason == "hash_mismatch"
