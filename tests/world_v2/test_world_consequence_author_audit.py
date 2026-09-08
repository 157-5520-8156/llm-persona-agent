"""A real public author run, interrupted after its audit and before acceptance.

These tests prove request / response / execution-input provenance. They do not
qualify the later semantic reviewer or the not-yet-migrated Experience writer.
"""

import json
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_consequence_author_audit import read_world_consequence_author_evidence
from test_life_development_runtime import (
    OWNER, WORLD_ID, _SequenceModel, _location_bound_world_draft, _location_capability,
    _runtime, _seed_clock,
)


async def _audited(tmp_path, monkeypatch, *, invalid_attempt=False):
    ledger = SQLiteWorldLedger(path=tmp_path / "audit.sqlite", world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=tmp_path / "audit.sqlite", world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    capability = _location_capability()
    value = json.loads(_location_bound_world_draft(
        wake=wake, capability=capability,
        timing={"mode": "now", "duration_minutes": 30}, privacy_class="shareable",
        causal_authority="world_contingency", outcome_resolution_authority="world_contingency",
    ))
    for outcome in value["outcomes"]:
        outcome.pop("text")
        outcome["world_consequence"] = {
            "contract": "world-consequence.2", "environment_text": "冰雹打断了院内的树枝。",
        }
    raw = json.dumps(value, ensure_ascii=False)
    outputs = (raw,)
    if invalid_attempt:
        value["outcomes"][0]["world_consequence"]["authorized_attempt_result"] = {
            "text": "手账没有淋湿。",
            "execution_binding": {
                "source_kind": "activity_execution", "source_event_type": "ActivityStarted",
                "actor_ref": OWNER, "source_event_ref": wake.event_id,
                "source_world_revision": 1, "source_payload_hash": wake.payload_hash,
                "privacy_class": "shareable", "plan_id": "plan:unstarted",
                "activity_id": "activity:unstarted", "plan_entity_revision": 2,
            },
        }
        outputs = (json.dumps(value, ensure_ascii=False), raw)
    model = _SequenceModel(model="test-world-author", outputs=outputs)
    runtime, _ = _runtime(
        ledger=ledger, wake=wake, store=store, world_author=model,
        location_capability=capability,
        character_interior=_SequenceModel(model="test-character", outputs=()),
    )
    original = runtime._manifest_compiler  # noqa: SLF001

    class CurrentManifest:
        def compile(self, **kwargs):
            return original.compile(**kwargs).model_copy(update={"outcome_contract": "world-consequence.2"})

    monkeypatch.setattr(runtime, "_manifest_compiler", CurrentManifest())
    captured = {}

    async def interrupt_after_author(**kwargs):
        captured.update(kwargs)
        raise InterruptedError("test checkpoint after committed author audit")

    monkeypatch.setattr(runtime, "_source_close_world_author_result", interrupt_after_author)
    with pytest.raises(InterruptedError, match="test checkpoint"):
        await runtime.advance_once(wake_event_ref=wake.event_id, trace_id="trace:test-audit",
                                   correlation_id="correlation:test-audit")
    arguments = dict(
        ledger=ledger, content_store=store, manifest=captured["manifest"], actor_ref=OWNER,
        draft=captured["draft"], raw=captured["raw"],
        author_deliberation=captured["author_deliberation"].authority_payload(),
    )
    assert ledger.project().world_occurrences == ()
    assert ledger.project().plans == ()
    return ledger, store, model, arguments


@pytest.mark.asyncio
async def test_original_request_and_complete_author_audit_survive_sqlite_restart(tmp_path, monkeypatch):
    ledger, store, model, arguments = await _audited(tmp_path, monkeypatch)
    before = read_world_consequence_author_evidence(**arguments)
    assert before["authority"]["execution_bindings"] == []
    assert before["execution_materials"] == []
    user = json.loads(model.messages[0][1]["content"])
    assert before["authority"] == user["execution_authority"]
    assert "outcome_text" not in user["cross_field_authority"]
    ledger.close()
    store.close()
    reopened = SQLiteWorldLedger(path=tmp_path / "audit.sqlite", world_id=WORLD_ID)
    reopened_store = SQLiteImmutableLifeContentStore(path=tmp_path / "audit.sqlite", world_id=WORLD_ID)
    assert read_world_consequence_author_evidence(**{
        **arguments, "ledger": reopened, "content_store": reopened_store,
    }) == before
    assert model.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["response", "manifest", "binding", "missing", "kind"])
async def test_candidate_cannot_borrow_changed_or_unreadable_original_evidence(tmp_path, monkeypatch, invalid):
    ledger, store, _, arguments = await _audited(tmp_path, monkeypatch)
    if invalid == "response":
        arguments["raw"] += " "
    elif invalid == "manifest":
        arguments["manifest"] = arguments["manifest"].model_copy(update={"version": "changed-version"})
    elif invalid == "binding":
        value = json.loads(json.dumps(arguments["author_deliberation"]))
        value["request_bindings"][0]["content_payload_hash"] = "a" * 64
        arguments["author_deliberation"] = value
    else:
        class WrongRequestStore:
            def read_exact(self, *, content_ref):
                record = store.read_exact(content_ref=content_ref)
                if record and record.content_kind == "raw_model_request":
                    if invalid == "missing":
                        return None
                    return SimpleNamespace(**{
                        "content_ref": record.content_ref, "content_payload_hash": record.content_payload_hash,
                        "text": record.text, "content_kind": "occurrence_result",
                    })
                return record

        arguments["content_store"] = WrongRequestStore()
    before = ledger.project()
    with pytest.raises(ValueError):
        read_world_consequence_author_evidence(**arguments)
    assert ledger.project() == before


@pytest.mark.asyncio
async def test_unoffered_attempt_returns_exact_failure_to_same_author_once(tmp_path, monkeypatch):
    _, _, model, arguments = await _audited(tmp_path, monkeypatch, invalid_attempt=True)
    assert model.calls == 2
    correction = json.loads(model.messages[1][-1]["content"])
    assert correction["validation_failure"]["code"] == "execution_binding_not_offered"
    assert "outcome_text" not in correction["hard_boundary_contract"]
    assert len(arguments["author_deliberation"]["request_bindings"]) == 2
    assert read_world_consequence_author_evidence(**arguments)["authority"]["execution_bindings"] == []
