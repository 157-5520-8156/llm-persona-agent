"""Original WA requests at public runtime, SQLite recovery and sidecar seams.

Only the model provider and explicit storage/commit faults are fixtures. No
accepted model result, audit Proposal or final effect is seeded by these tests.
"""

import hashlib
import json
import sqlite3

import pytest

from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import (
    OWNER,
    WORLD_ID,
    _PinnedCapsuleCompiler,
    _SequenceModel,
    _StaticManifestCompiler,
    _replace_event_payload,
    _seed_clock,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _CurrentManifest(_StaticManifestCompiler):
    def compile(self, **kwargs):
        return (
            super().compile(**kwargs).model_copy(update={"outcome_contract": "world-consequence.2"})
        )


class _ReceivedAuthor:
    model = "fixture:request-audit-author"

    def __init__(self, store, outputs):
        self.store = store
        self.outputs = list(outputs)
        self.received = []
        self.stored_before_call = []

    async def complete(self, messages, *, temperature=0.2):
        raw = _json(messages)
        self.received.append(raw)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        self.stored_before_call.append(
            self.store.read_exact(content_ref="content:world-author-request:" + digest)
        )
        assert self.outputs, "cold recovery must not make a new provider call"
        result = self.outputs.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _runtime(ledger, store, wake, author, *, current=True):
    return LifeDevelopmentRuntime(
        ledger=ledger,
        content_store=store,
        world_author=author,
        character_interior=_SequenceModel(model="fixture:no-character-call", outputs=()),
        source_closure_reviewer=None,
        novel_origin_critic=None,
        capsule_compiler=_PinnedCapsuleCompiler(ledger=ledger),
        capability_manifest_compiler=(
            _CurrentManifest(wake=wake) if current else _StaticManifestCompiler(wake=wake)
        ),
        owner_actor_ref=OWNER,
    )


async def _advance(runtime, wake):
    return await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:original-request",
        correlation_id="correlation:original-request",
    )


def _audited(ledger):
    projection = ledger.project()
    assert projection.model_result_audits
    metadata = []
    for audit in projection.proposal_audits:
        response = json.loads(audit.proposal_json).get("response_text")
        if response is not None:
            value = json.loads(response)
            if value.get("model_role") == "world_author":
                metadata.append(value)
    assert len(metadata) == 1
    attempts = sorted(projection.model_result_audits, key=lambda item: item.attempt_index)
    return metadata[0], attempts


class _BeforeFinalEffect(RuntimeError):
    pass


async def _interrupt_before_final(ledger, runtime, wake, monkeypatch):
    original = ledger.commit_at_cursor

    def stop(events, **kwargs):
        if any(
            event.event_type == "ProposalRecorded"
            and event.payload().get("proposal_kind") == "life_development"
            for event in events
        ):
            _audited(ledger)  # The actual model/audit commit precedes this fault.
            raise _BeforeFinalEffect("final effect has not been committed")
        return original(events, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "commit_at_cursor", stop)
        with pytest.raises(_BeforeFinalEffect):
            await _advance(runtime, wake)


@pytest.mark.asyncio
@pytest.mark.parametrize("corrective", [False, True])
async def test_original_request_is_audited_before_final_effect_and_cold_recovers(
    tmp_path, monkeypatch, corrective
):
    path = tmp_path / "request.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        outputs = ("{", '{"decision":"no_op"}') if corrective else ('{"decision":"no_op"}',)
        author = _ReceivedAuthor(store, outputs)
        await _interrupt_before_final(
            ledger, _runtime(ledger, store, wake, author), wake, monkeypatch
        )
        metadata, attempts = _audited(ledger)
        assert "request_bindings" in metadata
        bindings = metadata["request_bindings"]
        assert len(bindings) == len(attempts) == len(author.received) == 1 + int(corrective)
        if corrective:
            assert bindings[0] != bindings[1]
            assert json.loads(author.received[1])[:-1] == json.loads(author.received[0])
        for binding, audit, raw, before in zip(
            bindings, attempts, author.received, author.stored_before_call, strict=True
        ):
            digest = hashlib.sha256(raw.encode()).hexdigest()
            assert binding == {
                "contract": "world-author-request.1",
                "content_ref": "content:world-author-request:" + digest,
                "content_payload_hash": digest,
                "utf8_bytes": len(raw.encode()),
            }
            assert json.loads(audit.audit_json)["request_hash"] == digest
            assert before is not None and before.content_kind == "raw_model_request"
            assert before.text == raw
            assert store.read_exact(content_ref=binding["content_ref"]) == before
        original_audits = tuple(attempt.audit_json for attempt in attempts)
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
        recovered_author = _ReceivedAuthor(store, ())
        result = await _advance(_runtime(ledger, store, wake, recovered_author), wake)
        assert result.status == "no_op"
        assert recovered_author.received == []
        assert tuple(attempt.audit_json for attempt in _audited(ledger)[1]) == original_audits
        final = ledger.lookup_event_commit(result.proposal_event_ref)[0].payload()
        assert final["world_author_deliberation"]["request_bindings"] == bindings
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "wrong_kind", "corrupt"])
async def test_missing_or_changed_original_request_refuses_cold_recovery(
    tmp_path, monkeypatch, fault
):
    path = tmp_path / "request.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        author = _ReceivedAuthor(store, ('{"decision":"no_op"}',))
        await _interrupt_before_final(
            ledger, _runtime(ledger, store, wake, author), wake, monkeypatch
        )
        metadata, attempts = _audited(ledger)
        original_audits = tuple(attempt.audit_json for attempt in attempts)
        reference = metadata["request_bindings"][0]["content_ref"]
        store.close()
        ledger.close()
        # This fault targets only this test's temporary sidecar after shutdown.
        with sqlite3.connect(path) as database:
            if fault == "missing":
                database.execute(
                    "DELETE FROM world_v2_life_content WHERE world_id=? AND content_ref=?",
                    (WORLD_ID, reference),
                )
            elif fault == "wrong_kind":
                database.execute(
                    "UPDATE world_v2_life_content SET content_kind='raw_model_result' "
                    "WHERE world_id=? AND content_ref=?",
                    (WORLD_ID, reference),
                )
            else:
                database.execute(
                    "UPDATE world_v2_life_content SET text=text || ' ' "
                    "WHERE world_id=? AND content_ref=?",
                    (WORLD_ID, reference),
                )
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
        before = ledger.project()
        recovered_author = _ReceivedAuthor(store, ())
        with pytest.raises(ValueError, match="exact_bytes_unavailable|hash does not match"):
            await _advance(_runtime(ledger, store, wake, recovered_author), wake)
        assert recovered_author.received == []
        assert ledger.project() == before
        assert tuple(attempt.audit_json for attempt in _audited(ledger)[1]) == original_audits
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_request_storage_failure_blocks_the_provider_call(tmp_path, monkeypatch):
    path = tmp_path / "storage-failure.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        original = store.put_if_absent
        request_writes = []

        def fail_request(record):
            if record.content_kind == "raw_model_request":
                request_writes.append(record.content_ref)
                raise OSError("fixture request storage unavailable")
            return original(record)

        monkeypatch.setattr(store, "put_if_absent", fail_request)
        author = _ReceivedAuthor(store, ())
        result = await _advance(_runtime(ledger, store, wake, author), wake)
        assert result.status == "technical_failure"
        assert result.reason_code == "life_development.world_author_unavailable"
        assert len(request_writes) == 1
        assert author.received == []
        assert store.read_exact(content_ref=request_writes[0]) is None
        assert ledger.project().plans == ledger.project().world_occurrences == ()
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_failed_provider_call_still_has_its_original_pre_call_request(tmp_path):
    path = tmp_path / "failed-call.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        author = _ReceivedAuthor(store, (TimeoutError("fixture provider timeout"),))
        result = await _advance(_runtime(ledger, store, wake, author), wake)
        assert result.status == "technical_failure"
        assert len(author.received) == 1
        stored = author.stored_before_call[0]
        assert stored is not None and stored.content_kind == "raw_model_request"
        assert stored.text == author.received[0]
        audits = ledger.project().model_result_audits
        assert len(audits) == 1
        assert json.loads(audits[0].audit_json)["request_hash"] == stored.content_payload_hash
        assert store.read_exact(content_ref=stored.content_ref) == stored
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_legacy_manifest_adds_no_request_binding_or_sidecar(tmp_path):
    path = tmp_path / "legacy.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        author = _ReceivedAuthor(store, ('{"decision":"no_op"}',))
        result = await _advance(_runtime(ledger, store, wake, author, current=False), wake)
        assert result.status == "no_op"
        metadata, _ = _audited(ledger)
        assert "request_bindings" not in metadata
        final = ledger.lookup_event_commit(result.proposal_event_ref)[0].payload()
        assert "request_bindings" not in final["world_author_deliberation"]
        assert author.stored_before_call == [None]
        digest = hashlib.sha256(author.received[0].encode()).hexdigest()
        assert store.read_exact(content_ref="content:world-author-request:" + digest) is None
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_final_proposal_cannot_replace_the_original_request_binding(tmp_path, monkeypatch):
    path = tmp_path / "binding-replacement.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        author = _ReceivedAuthor(store, ('{"decision":"no_op"}',))
        original = ledger.commit_at_cursor
        before_final = []

        def replace_final_binding(events, **kwargs):
            changed = []
            for event in events:
                payload = event.payload()
                if (
                    event.event_type == "ProposalRecorded"
                    and payload.get("proposal_kind") == "life_development"
                ):
                    _audited(ledger)
                    before_final.append(ledger.project())
                    authority = payload["world_author_deliberation"]
                    authority["request_bindings"][0]["content_payload_hash"] = "0" * 64
                    # Keep the carrier's hash valid, so the audit join must reject it.
                    payload["world_author_deliberation_hash"] = hashlib.sha256(
                        _json(authority).encode()
                    ).hexdigest()
                    event = _replace_event_payload(event, payload=payload)
                changed.append(event)
            return original(tuple(changed), **kwargs)

        monkeypatch.setattr(ledger, "commit_at_cursor", replace_final_binding)
        with pytest.raises(ValueError, match="metadata|ModelResult"):
            await _advance(_runtime(ledger, store, wake, author), wake)
        assert len(author.received) == len(before_final) == 1
        assert ledger.project() == before_final[0]
    finally:
        store.close()
        ledger.close()


def test_request_roundtrip_and_exact_utf8_byte_cap_survive_sqlite_restart(tmp_path):
    from companion_daemon.world_v2.world_author_request_audit import (
        read_world_author_request,
        record_world_author_request,
    )

    path = tmp_path / "byte-cap.sqlite"
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
    overhead = len(_json([{"role": "user", "content": ""}]).encode())
    multibyte, remainder = divmod(256_000 - overhead, len("界".encode()))
    messages = [{"role": "user", "content": "界" * multibyte + "a" * remainder}]
    try:
        assert len(_json(messages).encode()) == 256_000
        binding = record_world_author_request(content_store=store, messages=messages)
        assert binding.utf8_bytes == 256_000
        assert record_world_author_request(content_store=store, messages=messages) == binding
        store.close()
        store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD_ID)
        assert (
            read_world_author_request(
                content_store=store,
                binding=binding,
                expected_request_hash=binding.content_payload_hash,
            )
            == messages
        )
        with pytest.raises(ValueError, match="exact_bytes_unavailable"):
            read_world_author_request(
                content_store=store,
                binding=binding.model_copy(update={"utf8_bytes": 255_999}),
                expected_request_hash=binding.content_payload_hash,
            )
        oversized = [{"role": "user", "content": messages[0]["content"] + "a"}]
        with pytest.raises(ValueError, match="raw model request exceeds"):
            record_world_author_request(content_store=store, messages=oversized)
        digest = hashlib.sha256(_json(oversized).encode()).hexdigest()
        assert store.read_exact(content_ref="content:world-author-request:" + digest) is None
    finally:
        store.close()
