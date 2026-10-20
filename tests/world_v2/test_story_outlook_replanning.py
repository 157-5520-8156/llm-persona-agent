"""Annual story possibilities change only through accepted World sources."""
from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.world_outlook import WorldOutlookDraft, OutlookSource
from companion_daemon.world_v2.world_outlook_runtime import WorldOutlookRuntime
from test_world_outlook import setup, draft_value


def story_draft(wake):
    value = draft_value(wake)
    for thread in value["threads"]:
        thread["developments"] = ["外部合作获得持续支持。", "合作条件变化，原有机会可能结束。"]
    value["long_term"] = [{"stage_id": "year", "starts_on": wake.logical_time.date().isoformat(),
        "ends_on": (wake.logical_time + timedelta(days=365)).date().isoformat(),
        "situation": "多条尚可改变的生活线并存。", "thread_refs": ["books", "study", "friends"],
        "conditions": []}]
    value["horizon_end"] = (wake.logical_time + timedelta(days=30)).isoformat()
    return value


def apply_story_contract(value, packet):
    """Legal protocol fixture; real story quality is tested by provider runs."""
    if packet.get("draft_schema_contract") not in {"world-outlook-author-schema.8", "world-outlook-author-schema.9"}:
        return value
    if len(value["threads"]) < 2:
        value["threads"].append({"thread_id": "independent-family", "situation": "家中的经营条件。",
                                 "open_question": "外部供货与租约会怎样变化？"})
    for thread in value["threads"]:
        thread.setdefault("developments", ["外部机会仍可继续。", "条件发生变化，原有机会结束。"])
    for stage in value.get("long_term", ()):
        stage.setdefault("thread_refs", [t["thread_id"] for t in value["threads"] if t.get("status") != "closed"])
    value.setdefault("change_responses", [{"source_ref": c["source_ref"], "decision": "retain",
        "thread_refs": [], "planning_keys": [], "reason": "此测试保留独立安排，不推定角色的新选择。"}
        for c in packet.get("world_changes", ())])
    return value


def test_annual_stages_link_independent_threads_without_changing_legacy_bytes(tmp_path):
    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    try:
        old = WorldOutlookDraft.model_validate_json(json.dumps(draft_value(wake)))
        assert "developments" not in old.model_dump(mode="json")["threads"][0]
        draft = WorldOutlookDraft.model_validate_json(json.dumps(story_draft(wake)))
        runtime._validate_preparation(draft, {
            "draft_schema_contract": "world-outlook-author-schema.8",
            "rolling_preparation": {"due": [], "known_publications": [], "outline_due": False},
            "current_outlook": {"opportunities": []}, "world_changes": [],
        }, ledger.project())
        broken = story_draft(wake)
        broken["long_term"][0]["thread_refs"] = ["missing"]
        with pytest.raises(ValueError, match="thread"):
            WorldOutlookDraft.model_validate_json(json.dumps(broken))
    finally:
        runtime.close()


def test_material_own_consequence_cannot_be_suppressed_as_ordinary_script_progress():
    source = OutlookSource(source_ref="occurrence:own", source_kind="occurrence", status="settled",
        token="a" * 64, allowed_statuses=("active", "settled"))
    offers = ({"state": "considered", "payload": {"effect_refs": [source.source_ref]}},)
    assert not WorldOutlookRuntime._sources_changed({}, {source.source_ref: source}, offers)
    assert WorldOutlookRuntime._sources_changed({}, {source.source_ref: source}, offers,
                                               material_refs={source.source_ref})


def test_dirty_story_wakes_before_weekly_review_without_role_cadence(tmp_path, monkeypatch):
    ledger, wake, store, _, runtime = setup(tmp_path, lambda w: [])
    runtime._rolling_planning = True
    view = {"needs_revision": True, "review_at": (wake.logical_time + timedelta(days=7)).isoformat(),
        "rolling_preparation": {"published": [], "due": [], "outline_due": False}}
    monkeypatch.setattr(runtime, "_refresh_identity", lambda *_: ({}, None, "dirty-story"))
    try:
        assert runtime.next_preparation_due(projection=ledger.project(), view=view) == wake.logical_time
        store.claim("refresh:dirty-story", kind="refresh", payload={"input_key": "dirty-story"})
        store.finish("refresh:dirty-story", state="technical_failure", payload={"code": "fixture_timeout"})
        assert runtime.next_preparation_due(projection=ledger.project(), view=view) > wake.logical_time
    finally:
        runtime.close()


def test_every_accepted_change_needs_explicit_story_response(tmp_path):
    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    packet = {"draft_schema_contract": "world-outlook-author-schema.8",
        "rolling_preparation": {"due": [], "known_publications": [], "outline_due": False},
        "current_outlook": {"opportunities": []},
        "world_changes": [{"source_ref": "occurrence:loss", "token": "a" * 64}]}
    try:
        value = story_draft(wake)
        draft = WorldOutlookDraft.model_validate_json(json.dumps(value))
        packet["current_outlook"] = draft.model_dump(mode="json")
        with pytest.raises(ValueError, match="change|response"):
            runtime._validate_preparation(draft, packet, ledger.project())
        value["change_responses"] = [{"source_ref": "occurrence:loss", "decision": "revise",
            "thread_refs": ["books", "study"], "planning_keys": [],
            "reason": "已接受的资源损失改变家庭经营与学习机会。"}]
        with pytest.raises(ValueError, match="actually changed"):
            runtime._validate_preparation(WorldOutlookDraft.model_validate_json(json.dumps(value)), packet, ledger.project())
        value["long_term"][0]["conditions"] = [{"source_ref": "occurrence:loss", "ready_statuses": ["settled"]}]
        with pytest.raises(ValueError, match="actually changed"):
            runtime._validate_preparation(WorldOutlookDraft.model_validate_json(json.dumps(value)), packet, ledger.project())
        value["opportunities"][0]["conditions"] = value["long_term"][0]["conditions"]
        value["opportunities"][0]["execution"]["text"] = "仅重新措辞的书店筹备公告。"
        with pytest.raises(ValueError, match="actually changed"):
            runtime._validate_preparation(WorldOutlookDraft.model_validate_json(json.dumps(value)), packet, ledger.project())
        value["opportunities"][0]["opens_at"] = (wake.logical_time + timedelta(days=2)).isoformat()
        value["opportunities"][0]["execution"]["text"] = "书店公告：新的固定租赁支出已生效，旧筹备窗口延期。"
        runtime._validate_preparation(WorldOutlookDraft.model_validate_json(json.dumps(value)), packet, ledger.project())
    finally:
        runtime.close()


def test_retained_change_can_keep_exact_future_but_not_hide_a_revision(tmp_path):
    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    value = story_draft(wake)
    old = WorldOutlookDraft.model_validate_json(json.dumps(value))
    response = {"source_ref": "plan:ordinary", "decision": "retain", "thread_refs": [],
                "planning_keys": [], "reason": "这次个人安排仍在原来的外部条件内，不改变未来机会。"}
    value["change_responses"] = [response]
    packet = {"response_tool_contract": "world-outlook-native.10", "world_changes": [{"source_ref": "plan:ordinary"}],
              "current_outlook": old.model_dump(mode="json"),
              "rolling_preparation": {"published": [], "due": [], "outline_due": False}}
    try:
        runtime._validate_story_revision(WorldOutlookDraft.model_validate_json(json.dumps(value)), packet, kept_current=True)
        for change in ({"change_responses": []}, {"change_responses": [{**response, "decision": "revise"}]},
                       {"threads": [{**value["threads"][0], "situation": "偷偷改写外部条件"}, *value["threads"][1:]]}):
            with pytest.raises(ValueError):
                runtime._validate_story_revision(WorldOutlookDraft.model_validate_json(json.dumps({**value, **change})), packet, kept_current=True)
        with pytest.raises(ValueError):
            runtime._validate_story_revision(WorldOutlookDraft.model_validate_json(json.dumps(value)),
                {**packet, "response_tool_contract": "world-outlook-native.9"}, kept_current=True)
    finally:
        runtime.close()


@pytest.mark.parametrize('plan_count', [1, 7])
@pytest.mark.parametrize('terminal', [True, False])
@pytest.mark.asyncio
async def test_native_keep_acknowledges_real_plan_choices_and_survives_reopen(tmp_path, plan_count, terminal):
    from test_world_outlook_continuity import npc_setup, npc_manifest, retime
    from test_life_projection import commit, event, mutation, world_evidence
    from test_life_development_runtime import _seed_clock
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
    from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore

    ledger, wake, _, runtime, _, _ = npc_setup(tmp_path, lambda d: None)

    class NativeAuthor:
        model = 'fixture:explicit-retain'
        single_tool_response_carrier = 'json_object'

        def __init__(self):
            self.calls, self.fail_next = [], False

        async def complete_json(self, messages, **kwargs):
            packet = decode_outlook_request(messages[1]['content'])
            self.calls.append(packet)
            if packet['parent_revision'] is None:
                value = retime(story_draft(wake), ledger.project(), wake)
                value['horizon_end'] = (wake.logical_time + timedelta(days=30)).isoformat()
                for index, node in enumerate(value['opportunities']):
                    node['opportunity_id'] = packet['new_opportunity_ids'][index]
                return json.dumps({'decision': 'revise', 'draft': value}, ensure_ascii=False)
            if self.fail_next:
                self.fail_next = False
                raise TimeoutError('offline batch retry control')
            return json.dumps({'decision': 'keep',
                'review_at': (wake.logical_time + timedelta(days=2)).isoformat(),
                'reason': '自主安排有变化，外部机会仍可继续。',
                'change_responses': [{'source_ref': x['source_ref'], 'decision': 'retain', 'thread_refs': [],
                    'planning_keys': [], 'reason': '这次选择没有改变已有外部条件。'}
                    for x in packet['world_changes']]}, ensure_ascii=False)

    author = NativeAuthor()
    runtime.author, runtime._rolling_planning = author, True
    try:
        async def refresh():
            return await runtime.refresh(projection=ledger.project(), context={}, manifest=npc_manifest(ledger, wake))

        assert (await refresh())['status'] == 'ready'
        before = runtime.store.head()[1].model_dump(mode='json')
        template = ledger.project().plans[0].model_dump(mode='json')
        template.pop('authority_origin', None)
        refs = [f'plan:ordinary-abandoned:{i}' for i in range(plan_count)]
        for ref in refs:
            plan = {**template, 'plan_id': ref, 'activity_id': 'activity:' + ref,
                    'participant_refs': [], 'location_ref': None, 'scheduled_window': None}
            commit(ledger, [event('planned:' + ref, 'ActivityPlanned', {
                **mutation('planned:' + ref, expected_revision=0, evidence_refs=plan['evidence_refs']),
                'plan': plan}, at=wake.logical_time)])
            if terminal:
                commit(ledger, [event('abandoned:' + ref, 'ActivityAbandoned', {
                    **mutation('abandoned:' + ref, expected_revision=1,
                        evidence_refs=[world_evidence(ledger, wake.event_id, 'life_transition')]),
                    'plan_id': ref, 'transitioned_at': wake.logical_time.isoformat(), 'reason_ref': wake.event_id},
                    at=wake.logical_time)])
        failed_attempts = []
        if plan_count > 1:
            pending = runtime.view()['changed_source_refs']
            author.fail_next = True
            assert (await refresh())['status'] == 'technical_failure'
            assert runtime.view()['changed_source_refs'] == pending
            assert runtime.store.head()[1].model_dump(mode='json') == before
            failed_attempts = [r for r in runtime.store._db.execute('SELECT payload FROM world_outlook_attempts')
                               if json.loads(r[0]).get('status') == 'technical_failure']
            assert failed_attempts
            calls = len(author.calls)
            await refresh()
            assert len(author.calls) == calls  # Original failed receipt still waits for its retry epoch.
            wake = _seed_clock(ledger, event_id='clock:batch-retry', logical_time_from=wake.logical_time,
                               logical_time=wake.logical_time + timedelta(hours=1))
        acknowledged = []
        for count in [min(3, plan_count-i) for i in range(0, plan_count, 3)]:
            assert len(runtime.view()['changed_source_refs']) == count
            assert (await refresh())['status'] == 'ready', runtime.health()
            after = runtime.store.head()[1].model_dump(mode='json')
            assert all(after[k] == before[k] for k in ('threads', 'opportunities', 'long_term'))
            acknowledged.extend(x['source_ref'] for x in after['change_responses'])
            runtime.store.close()
            runtime.store = SQLiteWorldOutlookStore(path=tmp_path/'outlook.sqlite',
                world_id=ledger.world_id, actor_ref=runtime.actor_ref)
        assert acknowledged == refs and len(author.calls) == 1 + (plan_count+2)//3 + bool(failed_attempts)
        assert all(r in runtime.store._db.execute('SELECT payload FROM world_outlook_attempts').fetchall()
                   for r in failed_attempts)
        assert runtime.view()['changed_source_refs'] == []
        assert (await refresh())['status'] == 'unchanged'
        assert all(p.status == ('abandoned' if terminal else 'planned')
                   for p in ledger.project().plans if p.plan_id in refs)
        assert ledger.rebuild() == ledger.project()
    finally:
        runtime.close()


def test_new_story_schema_cannot_advertise_unoffered_change_responses():
    from jsonschema import Draft202012Validator
    from companion_daemon.world_v2.world_outlook_author_schema import outlook_author_schema
    schema = outlook_author_schema(rolling_planning=True, story_planning=True)
    assert schema["properties"]["change_responses"]["maxItems"] == 0
    assert schema["$defs"]["OutlookStage"]["properties"]["thread_refs"]["minItems"] == 1
    assert schema["$defs"]["OutlookThread"]["properties"]["developments"]["minItems"] == 2
    Draft202012Validator.check_schema(schema)


def test_outlook_fact_presentation_retains_all_semantics_and_privacy():
    from companion_daemon.world_v2.world_outlook_runtime import _outlook_presentation_context
    value = {"subject_ref": "actor:companion", "source": "家里的来信", "statement": "租约持续有效。"}
    context = {"slices": {"relevant_facts": {"items": [{"item_ref": "fact:lease", "value": value,
        "privacy_class": "private", "source_hash": "a" * 64, "value_hash": "b" * 64,
        "source_bindings": [{"ref": "event:fact"}]}]}}}
    shown = _outlook_presentation_context(context)["slices"]["relevant_facts"]["items"]
    assert shown == [{"item_ref": "fact:lease", "privacy_class": "private", "value": value}]
    assert "source_bindings" in context["slices"]["relevant_facts"]["items"][0]


def test_changed_branch_cannot_silently_rewrite_an_independent_thread(tmp_path):
    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    value = story_draft(wake)
    old = WorldOutlookDraft.model_validate_json(json.dumps(value))
    packet = {"world_changes": [{"source_ref": "occurrence:loss"}],
        "current_outlook": {"threads": old.model_dump(mode="json")["threads"]},
        "rolling_preparation": {"published": []}}
    value["change_responses"] = [{"source_ref": "occurrence:loss", "decision": "revise",
        "thread_refs": ["books"], "planning_keys": [], "reason": "只有经营条件发生变化。"}]
    value["threads"][1]["situation"] = "系统未经说明替换了独立学业线。"
    try:
        with pytest.raises(ValueError, match="unaffected|without a change response"):
            runtime._validate_story_revision(WorldOutlookDraft.model_validate_json(json.dumps(value)), packet)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_settled_material_result_drives_annual_revision_and_survives_two_days(tmp_path):
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore
    from test_life_development_runtime import (WORLD_ID, OWNER, _seed_clock, _manifest,
        _projection_cursor, _SequenceModel, _source_closure_review, _novel_origin_review)
    from test_world_outlook import Author
    from test_world_consequence_producer import _draft, _runtime, _advance
    from test_world_consequence_aftermath import _aftermath, _NoCharacterCalls

    path = tmp_path / "material.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    content = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    store = SQLiteWorldOutlookStore(path=path, world_id=WORLD_ID, actor_ref=OWNER)
    wake = _seed_clock(ledger)
    author = Author([{"decision": "revise", "draft": story_draft(wake)}])
    outlook = WorldOutlookRuntime(ledger=ledger, store=store, author=author, actor_ref=OWNER,
                                  content_store=content, rolling_planning=True)
    try:
        assert (await outlook.refresh(projection=ledger.project(), context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger))))["status"] == "ready"
        value = _draft(wake)
        value.update(privacy_class="private", location_ref=None, location_capability_ref=None)
        for outcome in value["outcomes"]:
            outcome.update(privacy_class="private", visual_evidence=None,
                objective_biographical_transition={"coordinate_ref": "biography:family.resources",
                    "summary": "新租约开始履行，家庭书店每周有固定营业成本。",
                    "context_tags": ["family.resources:lease_obligation"],
                    "replaces_context_tag_prefixes": ["family.resources:"], "privacy_class": "private"})
        producer = _runtime(ledger, content, wake, _SequenceModel(model="fixture:author", outputs=[json.dumps(value)]),
            _SequenceModel(model="fixture:review", outputs=[_source_closure_review(decision="supported")]),
            _SequenceModel(model="fixture:novel", outputs=[_novel_origin_review(decision="supported")]))
        result = await _advance(producer, wake)
        assert result.status == "occurrence_committed", result
        due = _seed_clock(ledger, event_id="material-due", logical_time_from=wake.logical_time,
                          logical_time=wake.logical_time + timedelta(minutes=20))
        assert (await _advance(_aftermath(ledger, content, _NoCharacterCalls()), due)).status == "settled"
        state = ledger.project()
        assert state.biographical_coordinates[0].coordinate_ref == "biography:family.resources"
        view = outlook.view(projection=state)
        assert result.occurrence_id in view["changed_source_refs"]
        assert outlook.next_preparation_due(projection=state, view=view) == state.logical_time
        revised = story_draft(due)
        revised["opportunities"][0]["conditions"] = [{"source_ref": result.occurrence_id, "ready_statuses": ["settled"]}]
        revised["opportunities"][0]["opens_at"] = (due.logical_time + timedelta(days=2)).isoformat()
        revised["opportunities"][0]["execution"]["text"] = "书店公告：固定租赁支出已生效，筹备窗口推迟两日。"
        revised["parent_revision"] = view["revision"]
        revised["change_responses"] = [{"source_ref": result.occurrence_id, "decision": "revise",
            "thread_refs": ["books", "study"], "planning_keys": [], "reason": "固定租赁支出改变两线的未来资源条件。"}]
        author.outputs.append({"decision": "revise", "draft": revised})
        refreshed = await outlook.refresh(projection=state, context={},
            manifest=_manifest(due, pinned_cursor=_projection_cursor(ledger)))
        assert refreshed["status"] == "ready", refreshed
        from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
        packet = decode_outlook_request(author.calls[-1][1]["content"])
        change, = packet["world_changes"]
        assert change["objective_transition"]["coordinate_ref"] == "biography:family.resources"
        assert change["consequence"]["environment"]["text"]
        assert not outlook.view()["changed_source_refs"], "accepted response must consume only its offered changes"
        _seed_clock(ledger, event_id="material-two-days", logical_time_from=state.logical_time,
                    logical_time=state.logical_time + timedelta(days=2))
        coordinate = ledger.project().biographical_coordinates[0]
        assert "lease_obligation" in coordinate.context_tags[0]
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        outlook.close()
        content.close()
        ledger.close()
    cold = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    try:
        assert cold.project().biographical_coordinates[0] == coordinate
    finally:
        cold.close()


@pytest.mark.parametrize("nested_privacy,limited", [("withhold", False), ("private", True)])
def test_change_reader_preserves_nested_privacy_and_compensating_limits(tmp_path, monkeypatch, nested_privacy, limited):
    from types import SimpleNamespace as NS
    from companion_daemon.world_v2 import life_content
    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    effect = NS(privacy_class=nested_privacy)
    selected = NS(candidate_result_ref="result", privacy_class="private", content_ref="content:private",
                  objective_biographical_transition=effect, result_contract="legacy")
    occurrence = NS(occurrence_id="occurrence:private", status="settled", visibility="private",
        candidate_outcomes=(selected,), settled_outcome_ref="result", result_payload_ref="content:private",
        settlement_event_ref="event:settlement", model_dump=lambda **_: {"id": "private"})
    monkeypatch.setattr(life_content, "collect_user_channel_limited_content_refs",
        lambda **kwargs: frozenset({"content:private"}) if limited else frozenset())
    monkeypatch.setattr(runtime, "_entities", lambda *_: {"plans": (), "facts": (), "world_occurrences": (occurrence,)})
    # Only the descriptor is allowed. Any sidecar access would fail this test.
    selected.content_payload_hash = "a" * 64
    try:
        change, = runtime._world_changes(ledger.project(), (occurrence.occurrence_id,))
        assert change["objective_transition"] is None
        assert change["consequence"] is None
        assert change["content_unavailable"] is limited
        if not limited:
            assert not runtime._material_consequence(occurrence)
    finally:
        runtime.close()


def test_change_presentation_fits_actual_headroom_without_losing_source_or_hash():
    from companion_daemon.world_v2.world_outlook_runtime import _encode_story_request
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
    packet = {"condition_sources": [], "current_outlook": {}, "fixed_context": "x" * 97500,
        "world_changes": [{"source_ref": "occurrence:large", "status": "settled", "token": "a" * 64,
            "settlement_event_ref": "event:accepted", "objective_transition": {
                "summary": "长期义务" * 3000, "context_tags": ["resources:" + "a" * 110000]}}]}
    encoded = _encode_story_request(packet)
    assert len(encoded.encode()) <= 100000
    shown = decode_outlook_request(encoded)
    assert shown["fixed_context"] == packet["fixed_context"]
    change, = shown["world_changes"]
    assert change["source_ref"] == "occurrence:large" and change["token"] == "a" * 64
    assert change["settlement_event_ref"] == "event:accepted"
    assert change["material_truncated"] is True and len(change["presented_material_hash"]) == 64


def test_opaque_zero_evidence_cannot_be_used_to_fit_or_consume_a_change():
    from companion_daemon.world_v2.world_outlook_runtime import _encode_story_request
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
    packet = {"condition_sources": [], "fixed_context": "x" * 99400,
        "world_changes": [{"source_ref": "occurrence:large", "token": "a" * 64,
            "objective_transition": {"summary": "持续的租金义务" * 2000}}]}
    encoded = _encode_story_request(packet)
    assert len(encoded.encode()) > 100000  # Existing admission guard must fail, not consume it.
    change, = decode_outlook_request(encoded)["world_changes"]
    assert len(change["material_excerpt_json"]) == 512
    assert "持续的租金义务" in change["material_excerpt_json"]


def test_objective_tag_cannot_escape_exact_negative_source_review():
    from companion_daemon.world_v2.ledger import WorldLedger
    from companion_daemon.world_v2.life_development_draft import parse_world_author_draft
    from companion_daemon.world_v2.life_development_source_closure import (
        parse_life_development_novel_origin_review, _novel_origin_coordinate_catalog,
        _source_closure_coordinate_catalog,
    )
    from test_life_development_runtime import (WORLD_ID, NOW, _seed_clock, _location_capability,
        _location_bound_world_draft, _manifest, _projection_cursor, _novel_origin_review)
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    raw = json.loads(_location_bound_world_draft(wake=wake,
        capability=_location_capability(), timing={"mode": "now", "duration_minutes": 30},
        privacy_class="personal", objective_transition={"coordinate_ref": "biography:lease",
            "summary": "签署租约，承担押金义务；没有付款证据。",
            "context_tags": ["obligation:deposit_600_paid"],
            "replaces_context_tag_prefixes": ["obligation:"], "privacy_class": "personal"}))
    for outcome in raw["outcomes"]:
        outcome["world_consequence"] = {"contract": "world-consequence.2", "environment_text": outcome.pop("text")}
    manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger)).model_copy(update={"outcome_contract": "world-consequence.2"})
    draft = parse_world_author_draft(raw=json.dumps(raw), manifest=manifest, logical_time=NOW)
    path = "outcomes.0.objective_biographical_transition.context_tags.0"
    assert path in _novel_origin_coordinate_catalog(draft)["objective_transition_paths"]
    assert path in _source_closure_coordinate_catalog(draft)["undeclared_fact_paths"]
    finding = {"prose_path": path, "violation_kinds": ["objective_transition_not_entailed_by_candidate"],
               "exact_fragments": ["obligation:deposit_600_paid"]}
    review = parse_life_development_novel_origin_review(raw=_novel_origin_review(decision="unsupported",
        unsupported_objective_transitions=(finding,)), draft=draft)
    assert review.decision == "unsupported"
    bad = {**finding, "exact_fragments": ["obligation:other_paid"]}
    with pytest.raises(ValueError, match="fragment"):
        parse_life_development_novel_origin_review(raw=_novel_origin_review(decision="unsupported",
            unsupported_objective_transitions=(bad,)), draft=draft)


def test_revise_rejection_identifies_the_exact_source_and_linked_unchanged_future(tmp_path):
    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    value = story_draft(wake)
    value['change_responses'] = [{'source_ref': 'occurrence:one', 'decision': 'revise',
        'thread_refs': ['books'], 'planning_keys': [], 'reason': 'Only new wording.'}]
    previous = WorldOutlookDraft.model_validate_json(json.dumps(value))
    packet = {'current_outlook': previous.model_dump(mode='json'),
              'rolling_preparation': {'published': []},
              'world_changes': [{'source_ref': 'occurrence:one'}]}
    value['opportunities'][0]['conditions'] = [{'source_ref': 'occurrence:one', 'ready_statuses': ['settled']}]
    value['opportunities'][0]['execution']['text'] = 'Changed prose with the same external structure.'
    candidate = WorldOutlookDraft.model_validate_json(json.dumps(value))
    try:
        with pytest.raises(ValueError) as failure:
            runtime._validate_story_revision(candidate, packet)
        assert 'occurrence:one' in str(failure.value)
        assert candidate.opportunities[0].opportunity_id in str(failure.value)
        assert 'conditions' in str(failure.value)
        # Rejection must not choose retain, rewrite a node or accept anything.
        assert candidate.change_responses[0].decision == 'revise'
        assert runtime.store.head() is None
    finally:
        runtime.close()


@pytest.mark.parametrize('close_thread', [False, True])
@pytest.mark.parametrize('contract', ['world-outlook-author-schema.8', 'world-outlook-author-schema.9'])
def test_accepted_refusal_can_retire_unrealized_future_without_new_events(tmp_path, close_thread, contract):
    from copy import deepcopy

    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    try:
        old = WorldOutlookDraft.model_validate_json(json.dumps(story_draft(wake))).model_dump(mode="json")
        value = deepcopy(old)
        value['opportunities'] = [n for n in value['opportunities'] if 'books' not in n['thread_refs']]
        if close_thread:
            next(t for t in value['threads'] if t['thread_id'] == 'books')['status'] = 'closed'
        value['change_responses'] = [{'source_ref': 'occurrence:declined', 'decision': 'revise',
            'thread_refs': ['books'], 'planning_keys': [],
            'reason': 'The accepted refusal retires this future branch; no extra event is required.'}]
        packet = {'draft_schema_contract': contract, 'logical_time': wake.logical_time.isoformat(),
            'world_changes': [{'source_ref': 'occurrence:declined'}],
            'rolling_preparation': {'published': []}, 'current_outlook': old}
        draft = WorldOutlookDraft.model_validate_json(json.dumps(value))
        assert {n.opportunity_id for n in draft.opportunities} < {n['opportunity_id'] for n in old['opportunities']}
        if contract.endswith('.8'):
            with pytest.raises(ValueError, match='actually changed'):
                runtime._validate_story_revision(draft, packet)
        else:
            runtime._validate_story_revision(draft, packet)
        assert runtime.store.head() is None
    finally:
        runtime.close()


@pytest.mark.parametrize('fault', ['renamed', 'expired', 'considered', 'delivery_pending'])
def test_retirement_proof_rejects_renaming_and_cleanup_of_past_or_pending_effects(tmp_path, fault):
    from copy import deepcopy

    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    try:
        old = WorldOutlookDraft.model_validate_json(json.dumps(story_draft(wake))).model_dump(mode='json')
        first = old['opportunities'][0]
        if fault == 'expired':
            first.update(opens_at=(wake.logical_time - timedelta(hours=2)).isoformat(),
                         closes_at=(wake.logical_time - timedelta(hours=1)).isoformat())
        elif fault in {'considered', 'delivery_pending'}:
            first['state'] = fault
        value = deepcopy(old)
        value['opportunities'] = [{k: v for k, v in n.items() if k != 'state'}
                                  for n in value['opportunities']]
        if fault == 'renamed':
            previous_id = first['opportunity_id']
            value['opportunities'][0]['opportunity_id'] = 'a-fresh-equivalent-id'
            value['opportunities'][0]['execution']['text'] = 'Only different wording of the same opportunity.'
            value['opportunities'][0]['conditions'] = [{'source_ref': 'occurrence:change', 'ready_statuses': ['settled']}]
            for node in value['opportunities']:
                node['after_opportunities'] = ['a-fresh-equivalent-id' if ref == previous_id else ref
                                               for ref in node.get('after_opportunities', ())]
        else:
            value['opportunities'] = value['opportunities'][1:]
        value['change_responses'] = [{'source_ref': 'occurrence:change', 'decision': 'revise',
            'thread_refs': list(first['thread_refs']), 'planning_keys': [], 'reason': 'Declared structural change.'}]
        packet = {'draft_schema_contract': 'world-outlook-author-schema.9',
            'logical_time': wake.logical_time.isoformat(), 'world_changes': [{'source_ref': 'occurrence:change'}],
            'rolling_preparation': {'published': []}, 'current_outlook': old}
        with pytest.raises(ValueError, match='actually changed'):
            runtime._validate_story_revision(WorldOutlookDraft.model_validate_json(json.dumps(value)), packet)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_real_abandoned_plan_retires_a_future_branch_through_public_refresh_and_reopen(tmp_path):
    from copy import deepcopy
    from test_world_outlook_continuity import npc_setup, npc_manifest, retime
    from test_life_projection import commit, event, mutation, world_evidence
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
    from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore

    ledger, wake, _, runtime, _, _ = npc_setup(tmp_path, lambda d: None)

    class Author:
        model = 'fixture:retired-branch'
        single_tool_response_carrier = 'json_object'

        def __init__(self):
            self.calls = []

        async def complete_json(self, messages, **kwargs):
            packet = decode_outlook_request(messages[1]['content'])
            self.calls.append(packet)
            if packet['parent_revision'] is None:
                value = retime(story_draft(wake), ledger.project(), wake)
                value['horizon_end'] = (wake.logical_time + timedelta(days=30)).isoformat()
                for index, node in enumerate(value['opportunities']):
                    node['opportunity_id'] = packet['new_opportunity_ids'][index]
            else:
                value = deepcopy(runtime.store.head()[1].model_dump(mode='json'))
                value['parent_revision'] = packet['parent_revision']
                value['opportunities'] = [n for n in value['opportunities'] if 'books' not in n['thread_refs']]
                next(t for t in value['threads'] if t['thread_id'] == 'books')['status'] = 'closed'
                value['change_responses'] = [{'source_ref': change['source_ref'], 'decision': 'revise',
                    'thread_refs': ['books'], 'planning_keys': [],
                    'reason': 'The accepted choice closes this unexecuted line without adding pressure.'}
                    for change in packet['world_changes']]
            return json.dumps({'decision': 'revise', 'draft': value})

    author = Author()
    runtime.author, runtime._rolling_planning = author, True
    try:
        async def refresh():
            return await runtime.refresh(projection=ledger.project(), context={}, manifest=npc_manifest(ledger, wake))

        assert (await refresh())['status'] == 'ready'
        original = runtime.store.head()[1]
        plan = ledger.project().plans[0].model_dump(mode='json')
        plan.pop('authority_origin', None)
        plan.update(plan_id='plan:voluntary-branch', activity_id='activity:voluntary-branch',
                    participant_refs=[], location_ref=None, scheduled_window=None)
        commit(ledger, [event('branch:planned', 'ActivityPlanned', {
            **mutation('branch:planned', expected_revision=0, evidence_refs=plan['evidence_refs']),
            'plan': plan}, at=wake.logical_time)])
        commit(ledger, [event('branch:abandoned', 'ActivityAbandoned', {
            **mutation('branch:abandoned', expected_revision=1,
                evidence_refs=[world_evidence(ledger, wake.event_id, 'life_transition')]),
            'plan_id': plan['plan_id'], 'transitioned_at': wake.logical_time.isoformat(),
            'reason_ref': wake.event_id}, at=wake.logical_time)])
        before = ledger.project()
        assert runtime.view()['changed_source_refs'] == [plan['plan_id']]
        assert (await refresh())['status'] == 'ready', runtime.health()
        retired = runtime.store.head()[1]
        assert len(author.calls) == 2
        assert {n.opportunity_id for n in retired.opportunities} < {n.opportunity_id for n in original.opportunities}
        assert next(t for t in retired.threads if t.thread_id == 'books').status == 'closed'
        assert ledger.project() == before
        runtime.store.close()
        runtime.store = SQLiteWorldOutlookStore(path=tmp_path/'outlook.sqlite',
            world_id=ledger.world_id, actor_ref=runtime.actor_ref)
        assert runtime.store.head()[1] == retired
        assert runtime.view()['changed_source_refs'] == []
        assert (await refresh())['status'] == 'unchanged'
        assert len(author.calls) == 2 and ledger.rebuild() == before
    finally:
        runtime.close()


@pytest.mark.parametrize('fault', [None, 'expired', 'stale_token', 'unreviewed_origin'])
def test_keep_menu_uses_the_same_authority_and_expiry_rules_as_admission(tmp_path, fault):
    from test_life_development_runtime import _manifest, _projection_cursor
    from companion_daemon.world_v2.world_outlook_history import OutlookHistoryOffer

    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    try:
        value = story_draft(wake)
        sources = {}
        if fault == 'expired':
            value['opportunities'][0].update(opens_at=(wake.logical_time - timedelta(hours=2)).isoformat(),
                closes_at=(wake.logical_time - timedelta(hours=1)).isoformat())
        if fault == 'stale_token':
            source = OutlookSource(source_ref='plan:condition', source_kind='plan', status='active',
                allowed_statuses=('planned', 'active', 'abandoned'), token='b' * 64)
            sources[source.source_ref] = source
            value['opportunities'][0]['conditions'] = [{'source_ref': source.source_ref,
                'ready_statuses': ['active'], 'unchanged_token': 'a' * 64}]
        draft = WorldOutlookDraft.model_validate_json(json.dumps(value))
        manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger))
        packet = {'basis_refs': list(manifest.anchor_refs),
                  'condition_sources': [source.model_dump(mode='json') for source in sources.values()]}
        before = ledger.project()
        if fault == 'unreviewed_origin':
            runtime._source_critic = object()
        available = runtime._keep_is_offered(packet=packet, projection=before, manifest=manifest,
            prior_head=('a' * 64, draft, {}), npc_refs=(), sources=sources,
            history_offer=OutlookHistoryOffer(items=()))
        assert available == (fault is None)
        assert ledger.project() == before and runtime.store.head() is None
    finally:
        runtime.close()


@pytest.mark.parametrize('contract', ['world-outlook-author-schema.8', 'world-outlook-author-schema.9'])
def test_actual_npc_routing_change_is_not_mistaken_for_a_text_only_revision(tmp_path, contract):
    from copy import deepcopy

    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    try:
        old = story_draft(wake)
        old['opportunities'][0].update(lane='npc', npc_ref='npc:original',
            execution={'kind': 'npc_statement', 'text': 'An offered statement.',
                       'duration_minutes': 1, 'privacy_class': 'private'})
        old = WorldOutlookDraft.model_validate_json(json.dumps(old)).model_dump(mode='json')
        value = deepcopy(old)
        value['opportunities'][0]['npc_ref'] = 'npc:different'
        value['opportunities'][0]['conditions'] = [{'source_ref': 'occurrence:change', 'ready_statuses': ['settled']}]
        value['change_responses'] = [{'source_ref': 'occurrence:change', 'decision': 'revise',
            'thread_refs': value['opportunities'][0]['thread_refs'], 'planning_keys': [],
            'reason': 'The actual external speaker changed.'}]
        packet = {'draft_schema_contract': contract, 'logical_time': wake.logical_time.isoformat(),
            'world_changes': [{'source_ref': 'occurrence:change'}],
            'rolling_preparation': {'published': []}, 'current_outlook': old}
        candidate = WorldOutlookDraft.model_validate_json(json.dumps(value))
        if contract.endswith('.8'):
            with pytest.raises(ValueError, match='actually changed'):
                runtime._validate_story_revision(candidate, packet)
        else:
            runtime._validate_story_revision(candidate, packet)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_keep_preflight_uses_the_full_current_offer_not_the_prior_tracking_catalog(tmp_path):
    from test_world_outlook_continuity import npc_setup, npc_manifest, retime
    from test_life_projection import commit, event, mutation, world_evidence
    from test_life_development_runtime import _seed_clock
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request

    ledger, wake, _, runtime, _, _ = npc_setup(tmp_path, lambda d: None)
    template = ledger.project().plans[0].model_dump(mode='json', exclude={'authority_origin'})
    ref = 'plan:closed-context-source'
    template.update(plan_id=ref, activity_id='activity:closed-context-source', owner_actor_ref=runtime.actor_ref,
                    participant_refs=[], location_ref=None, scheduled_window=None)
    commit(ledger, [event('context:planned', 'ActivityPlanned', {
        **mutation('context:planned', expected_revision=0, evidence_refs=template['evidence_refs']),
        'plan': template}, at=wake.logical_time)])
    commit(ledger, [event('context:abandoned', 'ActivityAbandoned', {
        **mutation('context:abandoned', expected_revision=1,
                   evidence_refs=[world_evidence(ledger, wake.event_id, 'life_transition')]),
        'plan_id': ref, 'transitioned_at': wake.logical_time.isoformat(), 'reason_ref': wake.event_id},
        at=wake.logical_time)])

    class Author:
        model = 'fixture:current-offer-keep'
        single_tool_response_carrier = 'json_object'
        async def complete_json(self, messages, **kwargs):
            packet = decode_outlook_request(messages[1]['content'])
            if packet['parent_revision'] is None:
                value = retime(story_draft(wake), ledger.project(), wake)
                for i, node in enumerate(value['opportunities']):
                    node['opportunity_id'] = packet['new_opportunity_ids'][i]
                return json.dumps({'decision': 'revise', 'draft': value})
            assert ref in {s['source_ref'] for s in packet['condition_sources']}
            return json.dumps({'decision': 'keep', 'review_at': (ledger.project().logical_time + timedelta(days=1)).isoformat(),
                               'reason': '已结束的历史意图不改变这些独立的外部安排。'})

    runtime.author, runtime._rolling_planning = Author(), True
    try:
        assert (await runtime.refresh(projection=ledger.project(), context={}, manifest=npc_manifest(ledger, wake)))['status'] == 'ready'
        before = runtime.store.head()[1]
        assert ref not in runtime.store.head()[2]['source_tokens']
        _seed_clock(ledger, event_id='clock:review-context', logical_time_from=ledger.project().logical_time,
                    logical_time=ledger.project().logical_time + timedelta(days=2))
        context = {'slices': {'world_life': {'availability': 'available', 'items': [{'item_ref': ref}]}}}
        assert (await runtime.refresh(projection=ledger.project(), context=context, manifest=npc_manifest(ledger, wake)))['status'] == 'ready'
        after = runtime.store.head()[1]
        assert (after.threads, after.opportunities, after.long_term) == (before.threads, before.opportunities, before.long_term)
        assert ref in runtime.store.head()[2]['source_tokens']
        assert ledger.rebuild() == ledger.project()
    finally:
        runtime.close()
