"""Public day opening and lifecycle into the existing World Author request.

All model choices are offline HTTP/provider fixtures. The application, role
compiler, SQLite acceptance, author manifest and request audit remain real.
"""

from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest

import test_day_open_self_directed_intent as day_fixture
from test_life_development_runtime import _seed_clock, _SequenceModel
from test_world_author_request_audit import _ReceivedAuthor, _json
from test_world_consequence_producer import _advance, _assert_original_requests
from test_world_stimulus_life_intent import ACTOR, NOW, WORLD, _http_result, _model

from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope, context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_capability import (
    ProjectionLifeCapabilityManifestCompiler,
)
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentCapabilityManifest, parse_world_author_draft,
)
from companion_daemon.world_v2.world_consequence_author_audit import (
    read_world_consequence_author_evidence,
)
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_turn_runtime import InboundTurn


INTENTION = "我想把去年在旧书店买的两本书叠好。"
build_app = day_fixture.build_app


class _LifecycleHTTP(day_fixture._DayOpenHTTP):
    """Select an actual offered lifecycle token, without seeding transitions."""

    async def __call__(self, request):
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"])
        if material.get("inner_turn", {}).get("purpose") is None:
            from test_world_consequence_agency_qualification import _QualificationHTTP

            return await _QualificationHTTP.__call__(self, request)
        capability = material.get("capability_manifest", {})
        openings = capability.get("payload", {}).get("openings", ())
        if openings and self.lifecycle_choice in {"pause", "resume", "complete"}:
            self.requests.append(body)
            self.lifecycle_requests.append(body)
            prefix = {
                "pause": "pause the current abstract activity",
                "resume": "resume an abstract paused activity",
                "complete": "finish the current abstract activity",
            }[self.lifecycle_choice]
            selected = next(item for item in openings if item["safe_summary"].startswith(prefix))
            return _http_result(body, {
                "status": "decision", "summary": "我选择这次活动变化。",
                "attended_source_refs": [], "recall_query": None, "proposals": [],
                "decision": {
                    "source_refs": capability["source_refs"],
                    "payload": {"decision": "select", "selected_token": selected["opening_token"]},
                },
            })
        return await super().__call__(request)


async def _tick(app, tick, at):
    before = app.export_replay_evidence().projection.logical_time
    await app.tick(
        tick_id=tick, logical_time_from=before, logical_time_to=at, observed_at=at,
        trace_id="trace:" + tick, causation_id="clock:" + tick,
        correlation_id="day-open-consequence", reason="test_clock",
    )


async def _day_activity(path, build_app, monkeypatch, phase):
    monkeypatch.setattr(day_fixture, "INTENT", {**day_fixture.INTENT, "intention": INTENTION})
    provider = _LifecycleHTTP()
    model = _model(provider)
    app = build_app(path, model, ecology=True)
    try:
        await _tick(app, "plan", NOW + timedelta(minutes=1))
        if phase != "planned":
            await _tick(app, "start", NOW + timedelta(minutes=1, seconds=1))
        if phase in {"paused", "resumed"}:
            provider.lifecycle_choice = "pause"
            await app.respond(InboundTurn(
                platform="test", platform_user_id="user.1", platform_message_id="interrupt",
                text="有空的时候聊聊。", observed_at=NOW + timedelta(minutes=1, seconds=1),
                trace_id="trace:interrupt",
            ))
            await _tick(app, "pause", NOW + timedelta(minutes=1, seconds=2))
        if phase == "resumed":
            provider.lifecycle_choice = "resume"
            await _tick(app, "resume", NOW + timedelta(minutes=1, seconds=3))
        if phase == "completed":
            provider.lifecycle_choice = "complete"
            await _tick(app, "complete", NOW + timedelta(minutes=6))
        evidence = app.export_replay_evidence()
        (plan,) = evidence.projection.plans
        assert len(provider.day_requests) == 1
        assert len(provider.lifecycle_requests) == {
            "planned": 0, "started": 1, "paused": 2, "resumed": 3, "completed": 2,
        }[phase]
        assert not evidence.projection.experiences
        return plan
    finally:
        await app.aclose()
        await model.aclose()


def _manifest_compiler(store, *, actor=ACTOR):
    catalog = ReviewedLifeSeedCatalog.from_yaml(
        path=Path(__file__).resolve().parents[2] / "configs/world_seed.yaml",
        chronology=LocalChronology("Asia/Shanghai"),
    )
    return ProjectionLifeCapabilityManifestCompiler(
        owner_actor_ref=actor, catalog=catalog, content_store=store,
    )


class _HistoricalCompiler:
    def __init__(self, store):
        self.current = _manifest_compiler(store)

    def compile(self, **kwargs):
        return self.current.compile(**kwargs).model_copy(
            update={"execution_intention_sources_version": None}
        )


def _author_runtime(ledger, store, author, *, compiler=None, critic=None, actor=ACTOR):
    return LifeDevelopmentRuntime(
        ledger=ledger, content_store=store, world_author=author,
        character_interior=_SequenceModel(model="fixture:unused-choice", outputs=()),
        source_closure_reviewer=None, novel_origin_critic=critic,
        capsule_compiler=context_capsule_compiler_from_ledger(
            ledger=ledger, life_content_store=store,
            relevance_scope=ContextRelevanceScope(actor_ref=actor),
        ),
        capability_manifest_compiler=compiler or _manifest_compiler(store, actor=actor),
        owner_actor_ref=actor,
    )


def _wake(ledger, suffix="author"):
    state = ledger.project()
    return _seed_clock(
        ledger, event_id="event:clock:" + suffix,
        logical_time_from=state.logical_time,
        logical_time=state.logical_time + timedelta(seconds=1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["started", "resumed"])
async def test_public_day_open_attempt_is_readable_in_the_real_author_request(
    tmp_path, monkeypatch, build_app, phase,
):
    path = tmp_path / "day.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, phase)
    assert plan.status == "active"
    source_ref = plan.authority_origin.accepted_event_ref
    assert plan.authority_origin.accepted_event_type == {
        "started": "ActivityStarted", "resumed": "ActivityResumed",
    }[phase]
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        author = _ReceivedAuthor(store, (_json({"decision": "no_op"}),))
        await _advance(_author_runtime(ledger, store, author), _wake(ledger))
        assert len(author.received) == 1
        actual = json.loads(json.loads(author.received[0])[-1]["content"])
        assert actual["capability_manifest"]["execution_intention_sources_version"] == "2"
        assert source_ref in actual["capability_manifest"]["anchor_refs"]
        assert [item["source_event_ref"] for item in actual["execution_authority"]["execution_bindings"]] == [source_ref]
        (material,) = actual["execution_materials"]
        intention = material["authorized_intention"]
        assert intention["text"] == INTENTION
        assert intention["epistemic_scope"] == "authorized_intention_not_embedded_history_or_execution_success"
        assert material["execution_binding"]["source_event_type"] == plan.authority_origin.accepted_event_type
        _assert_original_requests(ledger, store, author)
        assert not ledger.project().experiences
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["chat", "world_response"])
async def test_existing_public_intention_entries_remain_readable_in_new_author_requests(
    tmp_path, monkeypatch, entry,
):
    if entry == "chat":
        from test_chat_life_intent_runtime import _run_http_journey, INTENT

        result, events, _, output, _ = await _run_http_journey(tmp_path, monkeypatch, followup=True)
        assert result["completed"], result["stop_reason"]
        started = next(item for item in events if item["event_type"] == "ActivityStarted")
        path, world = output / "world.sqlite", started["world_id"]
        source_ref, expected = started["event_id"], INTENT["intention"]
    else:
        import test_world_stimulus_life_intent as intent_fixture
        from test_world_stimulus_life_response import _ResponseHTTP, _settled

        path, world = tmp_path / "response.sqlite", WORLD
        provider = _ResponseHTTP(text=None, intent="choose")
        model = _model(provider)
        app = intent_fixture._build(path, model, ecology=True)
        try:
            await _settled(app)
            await app.drain_background_once()
            before = app.export_replay_evidence().projection
            await _tick(app, "response-start", before.logical_time + timedelta(seconds=1))
            (plan,) = app.export_replay_evidence().projection.plans
            assert plan.status == "active"
            source_ref, expected = plan.authority_origin.accepted_event_ref, intent_fixture.INTENTION
            assert len(provider.stimulus_requests) == len(provider.lifecycle_requests) == 1
        finally:
            await app.aclose()
            await model.aclose()
    ledger = SQLiteWorldLedger(path=path, world_id=world)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=world)
    try:
        source = ledger.lookup_event_commit(source_ref)[0]
        actor = next(item.owner_actor_ref for item in ledger.project().plans
                     if item.plan_id == source.payload()["plan_id"])
        author = _ReceivedAuthor(store, (_json({"decision": "no_op"}),))
        await _advance(_author_runtime(ledger, store, author, actor=actor), _wake(ledger))
        assert len(author.received) == 1
        actual = json.loads(json.loads(author.received[0])[-1]["content"])
        assert actual["capability_manifest"]["execution_intention_sources_version"] == "2"
        (material,) = actual["execution_materials"]
        assert material["execution_binding"]["source_event_ref"] == source_ref
        assert material["authorized_intention"]["text"] == expected
        _assert_original_requests(ledger, store, author)
    finally:
        store.close()
        ledger.close()


class _EnvironmentAuthor(_ReceivedAuthor):
    """Fixture environmental proposal; no result is inferred from the intention."""

    def __init__(self, store, wake):
        super().__init__(store, ())
        self.wake = wake

    async def complete(self, messages, *, temperature=0.2):
        from test_world_consequence_producer import _draft

        value = _draft(self.wake)
        value.update(location_ref=None, location_capability_ref=None, privacy_class="private")
        for outcome in value["outcomes"]:
            outcome.update(privacy_class="private", visual_evidence=None)
        self.raw = _json(value)
        self.outputs.append(self.raw)
        return await super().complete(messages, temperature=temperature)


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [False, True])
async def test_day_open_reader_identity_survives_original_author_request_cold_recovery(
    tmp_path, monkeypatch, build_app, historical,
):
    from test_world_author_request_audit import _audited, _interrupt_before_final
    from test_life_development_runtime import _novel_origin_review
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        build_world_consequence_authoring_context,
        validate_world_consequence_authoring_context,
    )

    path = tmp_path / "cold.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "started")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        wake = _wake(ledger)
        author = _EnvironmentAuthor(store, wake)
        critic = _SequenceModel(model="fixture:critic", outputs=(_novel_origin_review(decision="supported"),))
        await _interrupt_before_final(
            ledger,
            _author_runtime(ledger, store, author, critic=critic,
                            compiler=_HistoricalCompiler(store) if historical else None),
            wake, monkeypatch,
        )
        assert len(author.received) == critic.calls == 1
        actual = json.loads(json.loads(author.received[0])[-1]["content"])
        metadata, _ = _audited(ledger)
        stored_manifest = store.read_exact(content_ref=metadata["capability_manifest_binding"]["content_ref"])
        raw_manifest = json.loads(stored_manifest.text)
        assert ("execution_intention_sources_version" not in raw_manifest) == historical
        manifest = LifeDevelopmentCapabilityManifest.model_validate_json(stored_manifest.text)
        # Historical serialization and hash stay byte-identical, including absence.
        assert _json(manifest.model_dump(mode="json", exclude_computed_fields=True)) == stored_manifest.text
        assert manifest.manifest_hash == actual["capability_manifest"]["manifest_hash"]
        assert manifest.manifest_hash == hashlib.sha256(_json(manifest.model_dump(mode="json")).encode()).hexdigest()
        assert plan.authority_origin.accepted_event_ref in manifest.anchor_refs
        context = build_world_consequence_authoring_context(
            ledger=ledger, content_store=store, manifest=manifest, actor_ref=ACTOR,
        )
        assert len(context.execution_materials) == int(not historical)
        assert context.model_dump(mode="json") == {
            "authority": actual["execution_authority"], "execution_materials": actual["execution_materials"],
        }
        original_audits = tuple(item.audit_json for item in ledger.project().model_result_audits)
        _assert_original_requests(ledger, store, author)
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
        validate_world_consequence_authoring_context(
            ledger=ledger, content_store=store, manifest=manifest, actor_ref=ACTOR,
            authority=context.authority, execution_materials=context.execution_materials,
        )
        # Recovery deliberately has the NEW default compiler and no model outputs.
        recovered_author = _ReceivedAuthor(store, ())
        recovered_critic = _SequenceModel(model="fixture:critic", outputs=())
        result = await _advance(
            _author_runtime(ledger, store, recovered_author, critic=recovered_critic), wake,
        )
        assert result.status == "occurrence_committed"
        assert recovered_author.received == [] and recovered_critic.calls == 0
        assert tuple(item.audit_json for item in ledger.project().model_result_audits) == original_audits
        final = ledger.lookup_event_commit(result.proposal_event_ref)[0].payload()
        assert final["world_author_deliberation"]["capability_manifest"] == raw_manifest
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["planned", "completed"])
async def test_day_open_intention_or_finished_timing_does_not_grant_attempt_result_authority(
    tmp_path, monkeypatch, build_app, phase,
):
    path = tmp_path / "no-execution.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, phase)
    assert plan.status == phase
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        author = _ReceivedAuthor(store, (_json({"decision": "no_op"}),))
        await _advance(_author_runtime(ledger, store, author), _wake(ledger))
        actual = json.loads(json.loads(author.received[0])[-1]["content"])
        assert actual["capability_manifest"]["execution_intention_sources_version"] == "2"
        assert actual["execution_authority"]["execution_bindings"] == []
        assert actual["execution_materials"] == []
        assert not ledger.project().experiences
    finally:
        store.close()
        ledger.close()


@pytest.mark.parametrize("version", ["1", "3", 2, True, ""])
def test_manifest_rejects_unknown_execution_intention_reader_identity(version):
    from companion_daemon.world_v2.schemas import ProjectionCursor

    with pytest.raises(ValueError):
        LifeDevelopmentCapabilityManifest(
            version="fixture", pinned_cursor=ProjectionCursor(world_revision=0, deliberation_revision=0, ledger_sequence=0),
            outcome_contract="world-consequence.2", execution_intention_sources_version=version,
            max_future_days=1, max_window_minutes=60,
        )


def test_reader_identity_cannot_upgrade_a_legacy_outcome_contract():
    from companion_daemon.world_v2.schemas import ProjectionCursor

    with pytest.raises(ValueError, match="require world-consequence.2"):
        LifeDevelopmentCapabilityManifest(
            version="fixture", pinned_cursor=ProjectionCursor(world_revision=0, deliberation_revision=0, ledger_sequence=0),
            execution_intention_sources_version="2", max_future_days=1, max_window_minutes=60,
        )


@pytest.mark.asyncio
async def test_new_day_open_reader_does_not_expand_anchors_or_cross_actor_permission(
    tmp_path, monkeypatch, build_app,
):
    from test_world_author_request_audit import _audited
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        build_world_consequence_authoring_context,
    )

    path = tmp_path / "permission.sqlite"
    await _day_activity(path, build_app, monkeypatch, "started")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        author = _ReceivedAuthor(store, (_json({"decision": "no_op"}),))
        await _advance(_author_runtime(ledger, store, author), _wake(ledger))
        metadata, _ = _audited(ledger)
        original = LifeDevelopmentCapabilityManifest.model_validate_json(
            store.read_exact(content_ref=metadata["capability_manifest_binding"]["content_ref"]).text
        )
        for actor, manifest in (
            (ACTOR, original.model_copy(update={"anchor_refs": ()})),
            ("actor:another", original.model_copy(update={"owner_actor_ref": "actor:another"})),
        ):
            context = build_world_consequence_authoring_context(
                ledger=ledger, content_store=store, manifest=manifest, actor_ref=actor,
            )
            assert context.authority.execution_bindings == context.execution_materials == ()
        assert len(author.received) == 1
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [False, True])
async def test_changing_reader_marker_and_self_rehashing_cannot_replace_original_author_audit(
    tmp_path, monkeypatch, build_app, historical,
):
    from test_life_development_runtime import _novel_origin_review, _replace_event_payload
    from companion_daemon.world_v2.life_content_store import StoredLifeContent
    from companion_daemon.world_v2.world_author_request_audit import record_world_author_request
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        build_world_consequence_authoring_context,
    )

    path = tmp_path / "marker-substitution.sqlite"
    await _day_activity(path, build_app, monkeypatch, "started")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        wake = _wake(ledger)
        author = _EnvironmentAuthor(store, wake)
        critic = _SequenceModel(model="fixture:critic", outputs=(_novel_origin_review(decision="supported"),))
        original_commit = ledger.commit_at_cursor
        before_final = []

        def substitute(events, **kwargs):
            changed = []
            for event in events:
                payload = event.payload()
                if event.event_type == "ProposalRecorded" and payload.get("proposal_kind") == "life_development":
                    before_final.append(ledger.project())
                    authority = payload["world_author_deliberation"]
                    raw_manifest = authority["capability_manifest"]
                    if historical:
                        raw_manifest["execution_intention_sources_version"] = "2"
                    else:
                        raw_manifest.pop("execution_intention_sources_version")
                    fresh_manifest = LifeDevelopmentCapabilityManifest.model_validate_json(_json(raw_manifest))
                    fresh_context = build_world_consequence_authoring_context(
                        ledger=ledger, content_store=store, manifest=fresh_manifest, actor_ref=ACTOR,
                    )
                    # The replacement bytes and carrier hashes are internally
                    # consistent, but the original independent audit still rejects it.
                    messages = json.loads(author.received[-1])
                    user = json.loads(messages[-1]["content"])
                    user["capability_manifest"] = {
                        **fresh_manifest.model_dump(mode="json"),
                        "manifest_hash": fresh_manifest.manifest_hash,
                    }
                    user["execution_authority"] = fresh_context.authority.model_dump(mode="json")
                    user["execution_materials"] = [item.model_dump(mode="json") for item in fresh_context.execution_materials]
                    messages[-1]["content"] = _json(user)
                    fresh_request = record_world_author_request(content_store=store, messages=messages)
                    assert fresh_request.content_payload_hash != authority["request_hashes"][-1]
                    authority["request_hashes"][-1] = fresh_request.content_payload_hash
                    authority["request_bindings"][-1] = fresh_request.model_dump(mode="json")
                    text = _json(raw_manifest)
                    digest = hashlib.sha256(text.encode()).hexdigest()
                    content_ref = "content:fixture:reader-manifest:" + digest
                    store.put_if_absent(StoredLifeContent(
                        content_ref=content_ref, content_kind="outcome_candidate",
                        text=text, content_payload_hash=digest,
                    ))
                    authority["capability_manifest_content_ref"] = content_ref
                    authority["capability_manifest_content_hash"] = digest
                    payload["world_author_deliberation_hash"] = hashlib.sha256(_json(authority).encode()).hexdigest()
                    with pytest.raises(ValueError, match="binding changed audited metadata"):
                        read_world_consequence_author_evidence(
                            ledger=ledger, content_store=store, manifest=fresh_manifest, actor_ref=ACTOR,
                            draft=parse_world_author_draft(raw=author.raw, manifest=fresh_manifest, logical_time=wake.logical_time),
                            raw=author.raw, author_deliberation=authority,
                        )
                    event = _replace_event_payload(event, payload=payload)
                changed.append(event)
            return original_commit(tuple(changed), **kwargs)

        monkeypatch.setattr(ledger, "commit_at_cursor", substitute)
        with pytest.raises(ValueError, match="authored subject exceeds its pinned authority"):
            await _advance(_author_runtime(
                ledger, store, author, critic=critic,
                compiler=_HistoricalCompiler(store) if historical else None,
            ), wake)
        assert len(before_final) == len(author.received) == critic.calls == 1
        assert ledger.project() == before_final[0]
        assert ledger.project().world_occurrences == ()
    finally:
        store.close()
        ledger.close()
