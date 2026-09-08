"""Shape diagnostics preserve role authority and the frozen forced-tool wire."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_inbound_atomic_result_v2 import _contract, _decision


def _wire(version, value):
    return json.dumps(value if version == "1" else {"result": value})


@pytest.mark.parametrize("version", ["1", "2"])
def test_missing_strict_union_fields_name_the_original_transport_location(version):
    value = _decision()
    del value["private_turn_state"], value["recall_request"]
    with pytest.raises(ValueError) as caught:
        _contract(version=version).unwrap(_wire(version, value))
    detail = str(caught.value)
    location = "$.result" if version == "2" else "$"
    assert f"shape invalid at {location}:" in detail
    assert 'missing_fields=["private_turn_state","recall_request"]' in detail
    assert "extra_fields=[]" in detail
    assert "no character choice was evaluated" in detail
    assert value["expression_draft"]["private_turn_state"]["inner_state_summary"] not in detail


@pytest.mark.parametrize("version", ["1", "2"])
def test_extra_and_missing_fields_are_distinguished_and_values_never_echoed(version):
    value = _decision()
    del value["recall_request"]
    value['unexpected"\nfield'] = {"private_payload": "do not echo this value"}
    with pytest.raises(ValueError) as caught:
        _contract(version=version).unwrap(_wire(version, value))
    detail = str(caught.value)
    assert 'missing_fields=["recall_request"]' in detail
    assert 'extra_fields=["unexpected\\"\\nfield"]' in detail
    assert "\n" not in detail
    assert "private_payload" not in detail and "do not echo this value" not in detail


@pytest.mark.parametrize("version", ["1", "2"])
def test_unknown_extra_field_diagnostics_have_count_and_literal_length_bounds(version):
    value = _decision()
    for i in range(100):
        value[f"{i:03}:" + "\u202e" * 1000] = "PRIVATE_VALUE"
    with pytest.raises(ValueError) as caught:
        _contract(version=version).unwrap(_wire(version, value))
    detail = str(caught.value)
    assert "extra_fields_omitted=92" in detail
    assert "extra_fields_truncated=8" in detail
    assert len(detail.encode("utf-8")) < 3000
    assert detail.isascii()
    assert "PRIVATE_VALUE" not in detail


@pytest.mark.parametrize(
    "value,missing,extra",
    [({}, '["result"]', "[]"), ({"result": {}, "surplus": None}, "[]", '["surplus"]')],
)
def test_v2_root_field_mismatch_has_its_own_location(value, missing, extra):
    with pytest.raises(ValueError) as caught:
        _contract().unwrap(json.dumps(value))
    detail = str(caught.value)
    assert "shape invalid at $:" in detail
    assert f"missing_fields={missing}" in detail
    assert f"extra_fields={extra}" in detail


@pytest.mark.parametrize("value", [None, [], "private-string-value"])
def test_v2_result_type_failure_names_location_without_leaking_value(value):
    with pytest.raises(ValueError) as caught:
        _contract().unwrap(json.dumps({"result": value}))
    detail = str(caught.value)
    assert "shape invalid at $.result: expected object" in detail
    assert "private-string-value" not in detail


@pytest.mark.parametrize("version", ["1", "2"])
def test_duplicate_key_acceptance_keeps_each_versions_existing_policy(version):
    raw = _wire(version, _decision()).replace(
        '"confidence": 7000', '"confidence": 1, "confidence": 7000', 1
    )
    if version == "2":
        with pytest.raises(ValueError, match="duplicate field"):
            _contract(version=version).unwrap(raw)
    else:
        assert _contract(version=version).unwrap(raw) == _contract(version=version).unwrap(
            _wire(version, _decision())
        )


def test_v1_v2_tools_identities_and_accepted_wire_keep_pre_diagnostic_bytes():
    source = """
import hashlib,json,itertools
from test_inbound_atomic_result_v2 import _contract,_decision
rows=[]
for version,phase,recall in itertools.product(('1','2'),('initial','after_recall','final'),(False,True)):
    c=_contract(version=version,phase=phase,recall_allowed=recall)
    value=_decision()
    if phase!='initial':
        del value['private_turn_state'],value['recall_request']
    raw=json.dumps(value if version=='1' else {'result':value})
    rows.append({'coordinates':[version,phase,recall],'tools':c.provider_tools,'choice':c.provider_tool_choice,'identity':c.identity.request_identity_material(),'accepted_wire':c.unwrap(raw)})
raw=json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
print(json.dumps({'contracts':len(rows),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}))
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=root,
        env=os.environ
        | {
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(root / "src") + os.pathsep + str(root / "tests/world_v2"),
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    # Captured from unchanged 43da429c before the diagnostic implementation.
    assert json.loads(result.stdout) == {
        "contracts": 12,
        "bytes": 954083,
        "sha256": "6f3473b30b301d0de133f7bc496bd7de7ac58e70947980c30a6e7ec05ef28929",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["1", "2"])
@pytest.mark.parametrize("review_valid", [True, False])
async def test_exact_shape_failure_reaches_same_role_once_before_whole_source_review(
    tmp_path,
    monkeypatch,
    version,
    review_valid,
):
    import asyncio
    from dataclasses import replace

    import httpx
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
    from world_v2_application import (
        build_sqlite_world_v2_test_application,
        compose_fixture_character_interior,
    )
    from test_whole_candidate_author import BEATS, _decision as authored_decision, _inbound
    from test_world_stimulus_life_intent import _http_result
    from test_production_turn_application import (
        _config,
        _Identities,
        _Router,
        _DeliveredTransport,
        NOW,
    )

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    requests, authors = [], []
    review_started, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert not body.get("stream")
        name = body["tool_choice"]["function"]["name"]
        if name == f"character_inbound_initial_v{version}":
            authors.append(body)
            assert len(authors) <= 2, "more than one same-role correction"
            value = authored_decision()
            if len(authors) > 1:
                value.update(private_turn_state=None, recall_request=None)
            return _http_result(body, value if version == "1" else {"result": value})
        review_started.set()
        await release.wait()
        decisions = [
            {
                "beat_index": index,
                "verdict": "source_free",
                "semantic_role": "commitment",
                "subject_role": "companion",
                "source_ref_indexes": [],
            }
            for index in range(len(BEATS))
        ]
        if not review_valid:
            decisions.pop()  # Incomplete review cannot authorize any beat.
        return _http_result(
            body, {"contract": "visible-beat-source-verdict.1", "decisions": decisions}
        )

    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(respond),
    )
    author = _InboundCharacterAuthor(
        flash_model=model,
        whole_candidate_mode=True,
        visible_source_review_model=model,
        atomic_tool_envelope_version=version,
    )
    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "world.sqlite",
        config=replace(_config(), visible_source_review_required=True),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(inbound_author=author),
        transport=_DeliveredTransport(),
        now=NOW,
    )
    task = asyncio.create_task(app.respond(_inbound()))
    try:
        await asyncio.wait_for(review_started.wait(), 3)
        assert not app.export_replay_evidence().projection.actions
        assert len(authors) == 2
        original, corrected = [json.loads(body["messages"][-1]["content"]) for body in authors]
        for field in (
            "capsule_id",
            "trigger_ref",
            "evaluated_world_revision",
            "evaluated_deliberation_revision",
            "evaluated_ledger_sequence",
            "attempt_id",
        ):
            assert corrected["request"][field] == original["request"][field]
        snapshot = dict(corrected["inner_life_snapshot"])
        correction = snapshot.pop("role_result_correction")
        assert snapshot == original["inner_life_snapshot"]
        assert correction["failure_code"] == "role_result_schema_invalid"
        assert correction["task"] == "return_one_fresh_complete_role_result"
        detail = correction["failure_detail"]
        location = "$.result" if version == "2" else "$"
        assert f"shape invalid at {location}:" in detail
        assert 'missing_fields=["private_turn_state","recall_request"]' in detail
        assert "extra_fields=[]" in detail
        assert detail in authors[1]["messages"][0]["content"]
        assert authors[0]["tools"] == authors[1]["tools"]
        assert authors[0]["model"] == authors[1]["model"]
        release.set()
        outcome = await task
        evidence = app.export_replay_evidence()
        assert len(authors) == 2 and len(requests) == 3
        if review_valid:
            assert outcome.status == "action_authorized"
            assert tuple(item.text for item in evidence.projection.stored_message_payloads) == BEATS
        else:
            assert outcome.status != "action_authorized"
            assert not evidence.projection.actions
            assert not evidence.projection.stored_message_payloads
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        app.close()
        await model.aclose()
