import json
from datetime import timedelta

import pytest
from jsonschema import Draft202012Validator

from companion_daemon.world_v2.life_development_source_closure import (
    source_closure_review_tool_contract, parse_life_development_source_closure_review,
    LifeDevelopmentSourceClosureError,
)
from companion_daemon.world_v2.life_development_draft import LifeDevelopmentPossibilityDraft
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from test_world_consequence_producer import _draft, _runtime
from test_world_author_request_audit import _ReceivedAuthor, _advance, _json
from test_life_development_runtime import WORLD_ID, _seed_clock, _SequenceModel, _source_closure_review, _novel_origin_review
from test_world_consequence_aftermath import _aftermath, _NoCharacterCalls, _assert_published_result


def test_native_shape_rejects_extra_fields_without_relaxing_canonical_reason_limit():
    schema = source_closure_review_tool_contract()["tools"][0]["function"]["parameters"]
    good = {"review": {"decision": "supported", "unsupported_claim_ids": [], "undeclared_fact_paths": [],
                       "undeclared_fact_fragments": [], "typed_location_conflicts": [], "reason": "Evidence supports the claim."}}
    assert not list(Draft202012Validator(schema).iter_errors(good))
    bad = json.loads(json.dumps(good))
    bad["review"]["typed_location_conflicts_note"] = "invented key"
    assert list(Draft202012Validator(schema).iter_errors(bad))
    oversized = json.loads(json.dumps(good))
    oversized["review"]["reason"] = "x" * 2001
    assert list(Draft202012Validator(schema).iter_errors(oversized))


@pytest.mark.asyncio
async def test_native_general_review_reaches_settlement_with_original_authority(tmp_path):
    ledger = SQLiteWorldLedger(path=tmp_path / "native.sqlite", world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=tmp_path / "native.sqlite", world_id=WORLD_ID)
    class Reviewer(_SequenceModel):
        supports_strict_tool_choice = True
        def __init__(self):
            super().__init__(model="fixture:strict-general", outputs=())
            self.tools_seen = []
        async def complete_json(self, messages, *, temperature, tools, tool_choice):
            self.tools_seen.append(tools)
            assert tool_choice["function"]["name"] == "life_source_closure_review_v1"
            return _source_closure_review(decision="supported")
    reviewer = Reviewer()
    try:
        wake = _seed_clock(ledger)
        draft = _draft(wake)
        raw = _json(draft)
        runtime = _runtime(ledger, store, wake, _ReceivedAuthor(store, (raw,)), reviewer,
                           _SequenceModel(model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),)))
        result = await _advance(runtime, wake)
        assert result.status == "occurrence_committed"
        assert len(reviewer.tools_seen) == 1
        due = _seed_clock(ledger, event_id="event:clock:strict-due", logical_time=wake.logical_time + timedelta(minutes=20),
                          logical_time_from=wake.logical_time)
        settled = await _advance(_aftermath(ledger, store, _NoCharacterCalls()), due)
        assert settled.status == "settled"
        _assert_published_result(ledger, store, ledger.project().world_occurrences[0], draft)
        parsed = LifeDevelopmentPossibilityDraft.model_validate_json(raw)
        oversized = json.loads(_source_closure_review(decision="supported"))
        oversized['reason'] = 'x' * 2001
        with pytest.raises(LifeDevelopmentSourceClosureError):
            parse_life_development_source_closure_review(raw=json.dumps(oversized), draft=parsed)
    finally:
        store.close()
        ledger.close()
