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
