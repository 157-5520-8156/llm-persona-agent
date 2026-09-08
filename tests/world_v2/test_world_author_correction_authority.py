"""Correction requests retain readable authority at the public SQLite/HTTP seam.

The focused verdicts below are supplied protocol fixtures, not semantic
qualification of a real critic. All HTTP uses an in-process MockTransport.
"""

import hashlib
import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import WORLD_ID, _seed_clock
from test_world_author_request_audit import (
    _advance,
    _audited,
    _interrupt_before_final,
    _json,
    _runtime,
)


class _AuthorHTTP:
    def __init__(self, store, outputs):
        self.store = store
        self.outputs = list(outputs)
        self.requests = []
        self.stored = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        raw = _json(body["messages"])
        self.stored.append(
            self.store.read_exact(
                content_ref="content:world-author-request:" + hashlib.sha256(raw.encode()).hexdigest()
            )
        )
        assert self.outputs, "cold recovery must not send another HTTP request"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": self.outputs.pop(0)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )


def _model(transport):
    return DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(transport),
    )


def _assert_authority_locator(messages, correction):
    locator = correction["original_authority"]
    assert locator == {
        "message_index": 1,
        "request_hash": hashlib.sha256(_json(messages[:2]).encode()).hexdigest(),
        "fields": {
            "capability_manifest": "capability_manifest",
            "hard_boundary_contract": "cross_field_authority",
            "output_contract": "output_contract",
            "timing_coordinates": "timing_coordinates",
        },
    }
    original = json.loads(messages[locator["message_index"]]["content"])
    for name, original_field in locator["fields"].items():
        assert name not in correction
        assert original[original_field]  # Readable material remains in this same request.


@pytest.mark.asyncio
@pytest.mark.parametrize("current", [False, True])
async def test_structure_correction_preserves_authority_and_cold_request_bytes(
    tmp_path, monkeypatch, current
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "correction.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wire = _AuthorHTTP(store, ("{", '{"decision":"no_op"}'))
    model = _model(wire)
    try:
        wake = _seed_clock(ledger)
        await _interrupt_before_final(
            ledger, _runtime(ledger, store, wake, model, current=current), wake, monkeypatch
        )
        assert len(wire.requests) == 2
        first, second = [item["messages"] for item in wire.requests]
        assert second[:-1] == first
        assert [item["role"] for item in second] == ["system", "user", "user"]
        correction = json.loads(second[-1]["content"])
        assert correction["validation_failure"]["code"] == "invalid_json"
        assert correction["validation_failure"]["detail"]
        assert correction["rejected_draft_hash"] == hashlib.sha256(_json("{").encode()).hexdigest()
        assert "repair_coordinates" in correction
        if current:
            _assert_authority_locator(second, correction)
            for body, stored in zip(wire.requests, wire.stored, strict=True):
                assert stored is not None and stored.text == _json(body["messages"])
        else:
            assert "original_authority" not in correction
            assert wire.stored == [None, None]
            # Frozen from the public 84781344 HTTP boundary before this change.
            assert [
                hashlib.sha256(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                for item in wire.requests
            ] == [
                "f2b392c5e2da907ce7fd3d109014518a9b6cf3e76f407415d78ae3704922e57a",
                "1f24626e909ce74fd3d84c3262ab32a5f827f45f34d1323e36eb19c73aa3dcdf",
            ]
        metadata, audits = _audited(ledger)
        old_audits = tuple(item.audit_json for item in audits)
        old_bindings = metadata.get("request_bindings")
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        cold_wire = _AuthorHTTP(store, ())
        cold_model = _model(cold_wire)
        try:
            result = await _advance(
                _runtime(ledger, store, wake, cold_model, current=current), wake
            )
            assert result.status == "no_op"
            assert cold_wire.requests == []
            new_metadata, new_audits = _audited(ledger)
            assert tuple(item.audit_json for item in new_audits) == old_audits
            assert new_metadata.get("request_bindings") == old_bindings
        finally:
            await cold_model.aclose()
    finally:
        await model.aclose()
        store.close()
        ledger.close()
