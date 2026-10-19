"""Offer legal execution shapes before the World Author generates a draft."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.world_v2.world_outlook import WorldOutlookDraft
from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
from test_world_outlook import setup, draft_value
from test_life_development_runtime import _manifest, _projection_cursor
from companion_daemon.world_v2.world_outlook_author_schema import outlook_author_schema


def test_rolling_planning_schema_matches_nonnull_runtime_contract():
    from datetime import datetime
    from test_rolling_world_planning import semester
    from companion_daemon.world_v2.world_planning import WorldPlanningDraft
    schema = outlook_author_schema(rolling_planning=True)
    assert schema["$defs"]["WorldPlanningDraft"]["properties"]["contract"]["const"] == "world-planning.3"
    assert schema["$defs"]["PlanningSlot"]["properties"]["prepare_details"] == {"type": "boolean", "const": True}
    validator = Draft202012Validator({"$ref": "#/$defs/WorldPlanningDraft", "$defs": schema["$defs"]})
    value = semester(datetime.fromisoformat("2026-10-17T21:25:02+08:00")).model_dump(mode="json")
    for field in ("review_at", "detailed_through"):
        bad = {**value, field: None}
        with pytest.raises(ValueError):
            WorldPlanningDraft.model_validate_json(json.dumps(bad))
        assert not validator.is_valid(bad)


def test_native_schema_requires_pinned_predecessor_and_due_publications():
    from test_life_development_runtime import NOW, WORLD_ID, OWNER
    from companion_daemon.world_v2.world_outlook_author_schema import outlook_result_tool
    schema = outlook_author_schema(rolling_planning=True, now=NOW,
        identity={"world_id": WORLD_ID, "actor_ref": OWNER, "parent_revision": "a" * 64},
        preparation={"due": [{"planning_key": "school", "supersedes_occurrence_ref": "occurrence:prior"}]})
    assert {"parent_revision", "opportunities"} <= set(schema["required"])
    tool = outlook_result_tool(schema, allow_keep=False)
    assert set(tool["function"]["parameters"]["properties"]) == {"decision", "draft"}
    assert set(tool["function"]["parameters"]["required"]) == {"decision", "draft"}
    validator = Draft202012Validator(tool["function"]["parameters"])
    assert not validator.is_valid({"decision": "keep", "review_at": NOW.isoformat(), "reason": "尚未准备"})


def test_json_result_branches_preserve_native_validation_without_mixed_field_inventory():
    from itertools import combinations
    from companion_daemon.world_v2.world_outlook_author_schema import outlook_result_tool

    schema = {"$defs": {}, "type": "object", "properties": {"value": {"type": "integer"}},
              "required": ["value"], "additionalProperties": False}
    native = outlook_result_tool(schema)["function"]["parameters"]
    text = outlook_result_tool(schema, json_object=True)["function"]["parameters"]
    assert "oneOf" in text and "properties" not in text
    validators = [Draft202012Validator(s) for s in (native, text)]
    fields = {"draft": {"value": 1}, "review_at": "2026-10-20T00:00:00Z", "reason": "仍适用", "metadata": True}
    for decision in ("keep", "revise", "unknown", None):
        for count in range(5):
            for keys in combinations(fields, count):
                value = {"decision": decision, **{k: fields[k] for k in keys}}
                assert validators[0].is_valid(value) == validators[1].is_valid(value)


def test_rolling_date_bounds_use_the_planning_timezone_across_utc_midnight():
    from datetime import datetime
    schema = outlook_author_schema(rolling_planning=True, now=datetime.fromisoformat("2026-10-17T20:00:00+00:00"))
    fields = schema["$defs"]["WorldPlanningDraft"]["properties"]
    assert "2026-10-25" in fields["detailed_through"]["description"]
    assert "2026-10-18" in schema["properties"]["long_term"]["description"]
    assert "one year" in schema["properties"]["long_term"]["description"]


def test_new_annual_outline_requirement_preserves_old_paid_schema(tmp_path):
    from datetime import timedelta

    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    try:
        now = ledger.project().logical_time
        value = draft_value(wake)
        value["long_term"] = [{"stage_id": "year", "starts_on": now.date().isoformat(),
                               "ends_on": (now + timedelta(days=90)).date().isoformat(),
                               "situation": "可调整的远期学校生活。"}]
        pinned = {"rolling_preparation": {"due": [], "known_publications": []},
                  "current_outlook": {"opportunities": []}}
        old = WorldOutlookDraft.model_validate_json(json.dumps(value, ensure_ascii=False))
        runtime._validate_preparation(old, {**pinned, "draft_schema_contract": "world-outlook-author-schema.6"}, ledger.project())
        historical_keep = {**pinned, "rolling_preparation": {"due": [],
            "outline_due": True, "known_publications": []}}
        runtime._validate_preparation(old, {**historical_keep, "draft_schema_contract": "world-outlook-author-schema.6"},
                                      ledger.project(), kept_current=True)
        with pytest.raises(ValueError, match="365|year"):
            runtime._validate_preparation(old, {**pinned, "draft_schema_contract": "world-outlook-author-schema.7"}, ledger.project())
        value["long_term"][0]["ends_on"] = (now + timedelta(days=365)).date().isoformat()
        runtime._validate_preparation(WorldOutlookDraft.model_validate_json(json.dumps(value, ensure_ascii=False)),
            {**pinned, "draft_schema_contract": "world-outlook-author-schema.7"}, ledger.project())
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,field,value", [
    ("environment", "location_ref", "location:library"),
    ("npc_statement", "participant_npc_refs", ["npc:lin"]),
    ("environment", "target_plan_ref", "plan:old"),
])
async def test_advertised_schema_rejects_captured_illegal_kind_fields(tmp_path, kind, field, value):
    ledger, wake, _, author, runtime = setup(tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}])
    try:
        assert (await runtime.refresh(projection=ledger.project(), context={},
                                      manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger))))["status"] == "ready"
        packet = decode_outlook_request(author.calls[0][1]["content"])
        assert packet["draft_schema"]["$defs"]["OutlookOpportunity"]["properties"]["basis_refs"]["items"]["enum"] == packet["basis_refs"]
        bad = deepcopy(draft_value(wake))
        node = bad["opportunities"][0]
        node["execution"].update(kind=kind, **{field: value})
        if kind.startswith("npc_"):
            node.update(lane="npc", npc_ref="npc:lin")
        with pytest.raises(ValueError):
            WorldOutlookDraft.model_validate_json(json.dumps(bad))
        assert not Draft202012Validator(packet["draft_schema"]).is_valid(bad)
    finally:
        runtime.close()


@pytest.mark.parametrize("kind,extra", [
    ("environment", {}), ("npc_statement", {}),
    ("npc_activity", {"activity_kind": "sort_books", "participant_npc_refs": ["npc:lin"]}),
    ("npc_cancel_activity", {"target_plan_ref": "plan:npc"}),
    ("npc_status", {"target_status": "dormant"}),
])
def test_every_canonical_execution_kind_preserves_runtime_semantics(kind, extra):
    from companion_daemon.world_v2.world_outlook import OutlookExecution
    execution = {"kind": kind, "text": "外部事件或NPC的动作。", **extra}
    schema = outlook_author_schema()
    validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/OutlookExecution"})
    assert validator.is_valid(execution)
    parsed = OutlookExecution.model_validate_json(json.dumps(execution))
    assert parsed.kind == kind
    # Historical flat/null carriers remain readable; provider instructions do
    # not change ledger parsing or invent/drop execution fields on a response.
    legacy = {**execution, **{k: None for k in ("activity_kind", "location_ref", "target_plan_ref", "target_status") if k not in extra}}
    assert OutlookExecution.model_validate_json(json.dumps(legacy)) == parsed
    assert "properties" in WorldOutlookDraft.model_json_schema()["$defs"]["OutlookExecution"]


@pytest.mark.parametrize("kind,field", [("npc_activity", "activity_kind"), ("npc_cancel_activity", "target_plan_ref"), ("npc_status", "target_status")])
def test_kind_specific_required_values_cannot_be_missing_or_null(kind, field):
    schema = outlook_author_schema()
    validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/OutlookExecution"})
    for value in ({"kind": kind, "text": "外部事件。"}, {"kind": kind, "text": "外部事件。", field: None}):
        assert not validator.is_valid(value)


def test_basis_schema_distinguishes_event_authority_from_visible_entity_id():
    schema = outlook_author_schema(basis_refs=("event:settlement",))
    validator = Draft202012Validator(schema["$defs"]["OutlookOpportunity"]["properties"]["basis_refs"])
    assert validator.is_valid(["event:settlement"])
    assert not validator.is_valid(["occurrence:visible-in-context"])


@pytest.mark.parametrize("with_planning", [False, True])
def test_unchanged_prior_opportunities_keep_their_committed_basis_only(tmp_path, with_planning):
    from companion_daemon.world_v2.world_outlook import validate_draft
    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    try:
        value = draft_value(wake)
        if with_planning:
            from test_world_planning import planning
            value["opportunities"][0]["execution"] = {
                "kind": "planning", "text": "学校发布课程安排。", "planning": planning().model_dump(mode="json"),
            }
        prior = WorldOutlookDraft.model_validate_json(json.dumps(value))
        draft = prior.model_copy(update={"parent_revision": "a" * 64})
        kwargs = dict(projection=ledger.project(), actor_ref=draft.actor_ref,
                      parent_revision=draft.parent_revision, basis_refs=("event:new-clock",),
                      sources={}, npc_refs=(), prior_draft=prior)
        validate_draft(draft, **kwargs)
        schema = outlook_author_schema(basis_refs=("event:new-clock",),
                                       prior_opportunities=prior.opportunities)
        assert schema["$defs"]["OutlookOpportunity"]["anyOf"][1]["enum"] == [
            node.model_dump(mode="json") for node in prior.opportunities]
        validator = Draft202012Validator(schema)
        # Exact persisted form includes old optional/default execution fields.
        value = draft.model_dump(mode="json")
        assert validator.is_valid(value)
        bad = deepcopy(value)
        bad["opportunities"][0]["external_possibility"] += " 改写。"
        with pytest.raises(ValueError, match="lacks offered basis"):
            validate_draft(WorldOutlookDraft.model_validate_json(json.dumps(bad)), **kwargs)
        assert not validator.is_valid(bad)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_npc_privacy_floor_is_visible_before_generation(tmp_path):
    from test_world_outlook_continuity import npc_setup, npc_manifest

    ledger, wake, author, runtime, _, _ = npc_setup(tmp_path, lambda draft: None)
    try:
        assert (await runtime.refresh(projection=ledger.project(), context={},
                                      manifest=npc_manifest(ledger, wake)))["status"] == "ready"
        packet = decode_outlook_request(author.calls[0][1]["content"])
        npc = next(n for n in ledger.project().npcs if n.npc_id == "lin")
        assert packet["npc_privacy_floors"] == [{"npc_ref": "npc:lin", "privacy_class": npc.privacy_class}]
        assert packet["draft_schema_contract"] == "world-outlook-author-schema.5"
    finally:
        runtime.close()


def test_schema_applies_npc_floor_without_restricting_unrelated_world_events():
    schema = outlook_author_schema(npc_privacy_floors=[{"npc_ref": "npc:lin", "privacy_class": "personal"}])
    validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/OutlookOpportunity"})
    node = {"opportunity_id": "x", "thread_refs": ["t"], "external_possibility": "可用外部机会",
            "lane": "npc", "npc_ref": "npc:lin", "execution": {"kind": "npc_statement", "text": "有人到了。", "privacy_class": "shareable"},
            "opens_at": "2026-10-09T10:00:00Z", "closes_at": "2026-10-09T11:00:00Z", "basis_refs": ["event:x"]}
    assert not validator.is_valid(node)
    node["execution"]["privacy_class"] = "personal"
    assert validator.is_valid(node)
    node.update(lane="world", npc_ref=None)
    node["execution"].update(kind="environment", privacy_class="public")
    assert validator.is_valid(node)


@pytest.mark.asyncio
async def test_old_pinned_outlook_request_cannot_gain_prepared_details(tmp_path, monkeypatch):
    import companion_daemon.world_v2.world_outlook_runtime as module
    encode = module.encode_outlook_request
    def legacy(value, **kwargs):
        return encode({**value, "draft_schema_contract":"world-outlook-author-schema.4"}, **kwargs)
    monkeypatch.setattr(module, "encode_outlook_request", legacy)
    def output(wake):
        value = draft_value(wake)
        value["opportunities"][0]["execution"]["prepare_details"] = True
        return [{"decision":"revise","draft":value}] * 2
    ledger, wake, store, author, runtime = setup(tmp_path, output)
    try:
        result = await runtime.refresh(projection=ledger.project(), context={},
            manifest=_manifest(wake,pinned_cursor=_projection_cursor(ledger)))
        assert result["status"] != "ready"
        assert store.head() is None and len(author.calls) == 2
        assert "did not offer planning or prepared details" in author.calls[1][-1]["content"]
    finally:
        runtime.close()


def test_expired_prior_nodes_are_not_offered_as_retainable_schema_values(tmp_path):
    from datetime import timedelta
    from test_life_development_runtime import NOW

    _, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    try:
        draft = WorldOutlookDraft.model_validate_json(json.dumps(draft_value(wake)))
        schema = outlook_author_schema(basis_refs=('event:new-clock',), prior_opportunities=draft.opportunities,
                                       now=NOW + timedelta(days=8))
        validator = Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/OutlookOpportunity'})
        assert all(not validator.is_valid(node.model_dump(mode='json')) for node in draft.opportunities)
    finally:
        runtime.close()


@pytest.mark.parametrize('json_object', [False, True])
def test_keep_with_changes_requires_exact_model_authored_retain_responses(json_object):
    from companion_daemon.world_v2.world_outlook_author_schema import outlook_result_tool
    from companion_daemon.world_v2.world_outlook import WorldOutlookDraft
    from jsonschema import Draft202012Validator

    schema = WorldOutlookDraft.model_json_schema()
    schema['properties']['change_responses'].update(minItems=1, maxItems=1)
    validator = Draft202012Validator(outlook_result_tool(schema, json_object=json_object,
        retained_changes=True)['function']['parameters'])
    response = {'source_ref': 'plan:one', 'decision': 'retain', 'thread_refs': [],
                'planning_keys': [], 'reason': 'The external future remains compatible.'}
    value = {'decision': 'keep', 'review_at': '2026-10-19T07:00:00+08:00', 'reason': 'Compatible', 'change_responses': [response]}
    assert validator.is_valid(value)
    assert not validator.is_valid({k: v for k, v in value.items() if k != 'change_responses'})
    for responses in ([], [response, response], [{**response, 'decision': 'revise'}],
                      [{**response, 'planning_keys': ['school']} ]):
        assert not validator.is_valid({**value, 'change_responses': responses})
    assert not Draft202012Validator(outlook_result_tool(schema, json_object=json_object)['function']['parameters']).is_valid(value)
