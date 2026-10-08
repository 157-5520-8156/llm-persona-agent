"""Configured initial memories enter public Recall without a role-model gate."""

import pytest

from test_character_prehistory import ACTOR, START, started_ledger, reviewed_archive
from test_prehistory_memory_source import _read, _persist
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime


def test_direct_initialization_creates_source_bound_memory_without_model_and_replays(tmp_path):
    from companion_daemon.world_v2.prehistory_memory_initialization import (
        PrehistoryMemoryInitializer,
    )

    ledger = started_ledger(tmp_path / "direct.sqlite")
    try:
        (row,) = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            reviewed_archive(), created_at=START
        )
        initialize = PrehistoryMemoryInitializer(ledger=ledger, owner_actor_ref=ACTOR)
        result = initialize.advance_once()
        assert result["status"] == "initialized" and result["model_calls"] == 0
        (item,) = _read(ledger).items
        assert item.source_excerpts[0].text == row.record.statement
        assert item.assessment_scope == "initialization_defaults_not_character_judgment"
        assert len(ledger.project().memory_candidates) == 1
        before = ledger.project()
        assert initialize.advance_once()["status"] == "idle"
        assert (
            before.semantic_hash == ledger.project().semantic_hash == ledger.rebuild().semantic_hash
        )
        assert not ledger.recent_events_by_type(
            event_types=frozenset({"PrehistoryMemoryDecisionRecorded", "ModelResultRecorded"}),
            since=START,
            limit=32,
        )
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_default_application_installs_configured_history_without_provider(tmp_path):
    import json
    import httpx
    import test_world_stimulus_life_intent as shared
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
    from test_character_prehistory import reapprove

    data = reviewed_archive().document.model_dump(mode="json")
    data.update(world_id=shared.WORLD, actor_ref=shared.ACTOR)
    archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(data)))
    requests = []

    async def forbidden(request):
        requests.append(request)
        raise AssertionError("initialization must not call a provider")

    model = DeepSeekChatModel(
        "fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(forbidden),
    )
    app = shared._build(tmp_path / "app.sqlite", model, reviewed_prehistory=archive)
    try:
        projection = app.export_replay_evidence().projection
        assert len(projection.memory_candidates) == 1
        assert projection.memory_candidates[0].values.status == "active"
        assert not requests
        result = await app.initialize_prehistory_once()
        assert (
            result["status"] == "idle" and result["mode"] == "direct" and result["model_calls"] == 0
        )
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,expected",
    [(True, "world-v2-recall-index.hybrid.17"), (False, "world-v2-recall-index.hybrid.14")],
)
async def test_user_conversation_host_resolves_shared_story_setting(tmp_path, enabled, expected):
    from companion_daemon.config import Settings
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
    from test_qq_c2c_host_migration import _NamedNoCallModel, NOW

    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "host.sqlite",
        WORLD_V2_RECALL_STORY_CONTEXT=enabled,
        WORLD_V2_RECALL_SEMANTIC_ENABLED=False,
    )
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id="10001",
        bootstrap_at=NOW,
        model=_NamedNoCallModel("composition-only"),
        use_configured_recall_embedding=False,
    )
    try:
        profile = host.memory_runtime_profile()
        assert profile["recall_policy"] == expected
        assert profile["initialization_mode"] == "direct"
    finally:
        await host.aclose()


def test_reinstallation_preserves_later_forgetting_and_existing_character_choice(tmp_path):
    from companion_daemon.world_v2.prehistory_memory_initialization import (
        PrehistoryMemoryInitializer,
    )

    ledger = started_ledger(tmp_path / "forget.sqlite")
    try:
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR)
        installation = runtime.install_reviewed(reviewed_archive(), created_at=START)
        (row,) = installation.records
        (active,) = ledger.project().memory_candidates
        import test_memory_candidate_authority as memory
        from companion_daemon.world_v2.memory_events import MemoryDeliberativeForgetAuthority
        from companion_daemon.world_v2.schemas import memory_candidate_semantic_fingerprint

        values = active.values.model_copy(
            update={"status": "forgotten", "forgotten_at": START, "retrieval_strength_bp": 0}
        )
        after = active.model_copy(
            update={
                "entity_revision": active.entity_revision + 1,
                "values": values,
                "semantic_fingerprint": memory_candidate_semantic_fingerprint(
                    values=values, policy_refs=active.origin.policy_refs
                ),
                "origin": active.origin.model_copy(
                    update={
                        "change_id": "change:forget-direct",
                        "transition_id": "transition:forget-direct",
                        "accepted_event_ref": "event:forget-direct",
                    }
                ),
            }
        )
        _persist(
            ledger,
            memory.mutation(
                after,
                before=active,
                operation="forget",
                evaluated_world_revision=ledger.project().world_revision,
                forget_authority=MemoryDeliberativeForgetAuthority(),
            ),
        )
        before = ledger.project()
        result = runtime.install_reviewed(reviewed_archive(), created_at=START)
        assert result.memory_initialization["existing_inactive"] == 1
        assert result.memory_initialization["initialized"] == 0
        assert ledger.project().semantic_hash == before.semantic_hash
        assert not _read(ledger).items
        assert (
            PrehistoryMemoryInitializer(
                ledger=ledger, owner_actor_ref="actor:other"
            ).advance_once()["status"]
            == "idle"
        )
    finally:
        ledger.close()


@pytest.mark.parametrize("break_at", ["MemoryCandidateOpened", "MemoryCandidateAccepted"])
def test_direct_initialization_recovers_storage_failure_without_duplicate_grant(
    tmp_path, monkeypatch, break_at
):
    from companion_daemon.world_v2.prehistory_memory_initialization import (
        PrehistoryMemoryInitializer,
        EVENT_TYPE,
    )

    ledger = started_ledger(tmp_path / "recover.sqlite")
    original = ledger.commit
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            reviewed_archive(), created_at=START
        )

        def unavailable(events, **kwargs):
            if any(e.event_type == break_at for e in events):
                raise OSError("fixture storage unavailable")
            return original(events, **kwargs)

        monkeypatch.setattr(ledger, "commit", unavailable)
        with pytest.raises(OSError):
            PrehistoryMemoryInitializer(ledger=ledger, owner_actor_ref=ACTOR).advance_once()
        monkeypatch.setattr(ledger, "commit", original)
        result = PrehistoryMemoryInitializer(ledger=ledger, owner_actor_ref=ACTOR).advance_once()
        assert result["initialized"] == 1 and result["model_calls"] == 0
        assert len(ledger.project().memory_candidates) == 1
        assert (
            len(
                ledger.recent_events_by_type(
                    event_types=frozenset({EVENT_TYPE}), since=START, limit=32
                )
            )
            == 1
        )
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        ledger.close()


def test_configured_native_package_installs_through_normal_deployment_loader(tmp_path):
    import json
    from test_native_story_compiler import source_value, authoring_brief
    from companion_daemon.world_v2.native_story import NativeStorySource, compile_native_story
    from companion_daemon.world_v2.prehistory_deployment import configured_prehistory

    package = compile_native_story(
        NativeStorySource.model_validate_json(json.dumps(source_value())),
        brief=authoring_brief(),
        archive_id="prehistory-archive:configured-native",
    )
    path = tmp_path / "native.json"
    path.write_text(package.model_dump_json())
    resolved = configured_prehistory(
        path=path, supplied=None, world_id=package.document.world_id, actor_ref=ACTOR
    )
    ledger = started_ledger(tmp_path / "native-world.sqlite")
    try:
        installation = PrehistoryArchiveRuntime(
            ledger=ledger, owner_actor_ref=ACTOR
        ).install_compiled(resolved, created_at=START)
        assert installation.memory_initialization["initialized"] == 1
        assert "不知道的事" not in _read(ledger).model_dump_json()
        assert len(_read(ledger).items) == 1
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_normal_chat_cli_fetches_initial_memory_from_user_text_only(tmp_path, monkeypatch):
    """Only HTTP is scripted: initialization, ingress, Recall and receipt are real.

    This proves wiring, not the semantic quality of a live model response.
    """
    import argparse
    import json
    import httpx
    import test_chat_with_world_v2 as cli_test
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
    from companion_daemon.world_v2.qq_c2c_host import qq_c2c_world_id
    from test_character_prehistory import reapprove
    from test_whole_candidate_author import _decision
    from test_world_stimulus_life_intent import _http_result

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    question = "高中校刊核对稿件那件事你还记得吗？"
    raw = reviewed_archive().document.model_dump(mode="json")
    raw.update(world_id=qq_c2c_world_id("geoff"), actor_ref="agent:companion")
    archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(raw)))
    package_path = tmp_path / "history.json"
    package_path.write_text(archive.model_dump_json())
    statement = archive.document.records[0].statement
    target = archive.document.records[0].record_id
    env = tmp_path / "chat.env"
    env.write_text(
        "\n".join(
            [
                "DEEPSEEK_API_KEY=offline-fixture",
                "DEEPSEEK_BASE_URL=https://fixture.invalid",
                "DEEPSEEK_MODEL=deepseek-v4-flash",
                "WORLD_V2_RECALL_SEMANTIC_ENABLED=false",
                "WORLD_V2_VISIBLE_EXPRESSION_PROFILE=whole_v3_review_v8",
                "WORLD_V2_EXPRESSION_EPISODE_MODE=off",
                f"WORLD_V2_PREHISTORY_PACKAGE_PATH={package_path}",
                "WORLD_V2_EXTERNAL_PERCEPTION_ENABLED=false",
            ]
        )
    )
    for line in env.read_text().splitlines():
        monkeypatch.delenv(line.split("=", 1)[0], raising=False)
    calls = []

    async def respond(request):
        body = json.loads(request.content)
        name = body["tool_choice"]["function"]["name"]
        calls.append(name)
        user = json.loads(body["messages"][-1]["content"])
        if name.startswith("character_inbound_"):
            materials = user["inner_life_snapshot"]["materials"]
            if materials.get("contract") == "shared-string-view.1":
                from companion_daemon.world_v2.shared_string_view import unpack_shared_strings

                materials = unpack_shared_strings(materials)
            reading = materials.get("selected_recall", {}).get(
                "content", materials.get("automatic_prefetch", {})
            )
            history = next(
                (item for item in reading.get("items", []) if item.get("source_ref") == target),
                None,
            )
            if history is None:
                assert name == "character_inbound_initial_v3", json.dumps(
                    materials, ensure_ascii=False
                )
                return _http_result(
                    body,
                    {
                        "result": {
                            "result_kind": "recall",
                            "private_turn_state": {
                                "contract": "private-turn-state.1",
                                "inner_state_summary": "想起校刊那次核对。",
                                "attended_source_refs": [],
                            },
                            "recall_request": {
                                "query_text": "高中校刊核对稿件",
                                "memory_kinds": ["episodic"],
                                "limit": 4,
                            },
                        }
                    },
                )
            assert history["text"] == statement
            value = _decision()
            value["expression_draft"]["beats"] = [{"modality": "text", "text": statement}]
            value["expression_draft"]["world_claims"] = [
                {"claim_text": statement, "scope": "past_world", "source_refs": [target]}
            ]
            return _http_result(body, {"result": value})
        assert name == "visible_beat_source_verdict_v8", name
        rows = [
            dict(zip(table["columns"], row, strict=True))
            for table in user["source_reference_tables"]
            for row in table["rows"]
        ]
        sources = [
            row
            for row in rows
            if target
            in json.dumps(user["source_materials"][row["material_index"]], ensure_ascii=False)
        ]
        assert sources, json.dumps(user["source_materials"], ensure_ascii=False)
        source = sources[0]
        assert source["support_eligibility"] == "eligible"
        return _http_result(
            body,
            {
                "contract": "visible-beat-source-verdict.8",
                "decisions": [
                    {
                        "beat_index": 0,
                        "verdict": "closed",
                        "semantic_role": "external_proposition",
                        "subject_role": "companion",
                        "first_source_ref_index": source["source_ref_index"],
                        "additional_source_ref_indexes": [],
                    }
                ],
                "rejections": [],
            },
        )

    errors = []

    async def checked(request):
        try:
            return await respond(request)
        except Exception as exc:
            import traceback

            errors.append(traceback.format_exc())
            raise exc

    original = DeepSeekChatModel.__init__

    def local_http(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(checked)
        original(self, *args, **kwargs)

    monkeypatch.setattr(DeepSeekChatModel, "__init__", local_http)
    inputs = iter([question, "/quit"])
    monkeypatch.setattr("builtins.input", lambda *args: next(inputs))
    transcript = tmp_path / "chat.jsonl"
    await cli_test._MODULE._run(
        argparse.Namespace(
            database=tmp_path / "chat.sqlite",
            clone=None,
            env_file=[env],
            primary_user_id="geoff",
            recipient_id="console-user",
            jsonl=transcript,
            background_units=0,
            disable_recall_embedding=False,
            burst_message=[],
            burst_interval_seconds=0,
        )
    )
    records = [json.loads(line) for line in transcript.read_text().splitlines()]
    started = next(r for r in records if r["record_type"] == "session_started")
    turn = next(r for r in records if r["record_type"] == "turn")
    assert started["memory_runtime_profile"]["recall_policy"] == "world-v2-recall-index.hybrid.17"
    assert started["memory_runtime_profile"]["initialization_mode"] == "direct"
    assert started["evaluation_scope"] == "user_conversation_entrypoint"
    assert started["semantic_quality"] == "not_evaluated"
    assert turn["user"]["text"] == question and statement not in question
    assert turn["result"]["status"] == "action_authorized", "\n".join([str(calls), *errors])
    assert [unit["body"] for unit in turn["role_units"] if unit["kind"] == "text"] == [statement]
    assert turn["evidence"]["replay_matches_live"]
    assert all("retention" not in name for name in calls)


def test_explicit_archive_only_and_withheld_sources_do_not_install_memory(tmp_path):
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
    from test_character_prehistory import reapprove

    ledger = started_ledger(tmp_path / "archive-only.sqlite")
    try:
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR)
        result = runtime.install_reviewed(
            reviewed_archive(), created_at=START, memory_mode="archive_only"
        )
        assert result.memory_initialization["status"] == "archive_only"
        assert not ledger.project().memory_candidates
    finally:
        ledger.close()
    raw = reviewed_archive().document.model_dump(mode="json")
    raw["records"][0]["privacy_class"] = "withhold"
    import json

    archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(raw)))
    ledger = started_ledger(tmp_path / "withheld.sqlite")
    try:
        result = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).install_reviewed(
            archive, created_at=START
        )
        assert result.memory_initialization["withheld"] == 1
        assert not ledger.project().memory_candidates
        assert not _read(ledger).items
    finally:
        ledger.close()


def test_initialization_reports_legacy_pending_without_overwriting_it(tmp_path):
    from test_prehistory_memory_source import _choice
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
    from companion_daemon.world_v2.prehistory_memory_initialization import (
        PrehistoryMemoryInitializer,
    )

    ledger = started_ledger(tmp_path / "legacy-pending.sqlite")
    try:
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR)
        (row,) = runtime.import_reviewed(reviewed_archive(), created_at=START)
        _choice(ledger, prehistory_memory_binding(row))
        before = ledger.project()
        result = PrehistoryMemoryInitializer(ledger=ledger, owner_actor_ref=ACTOR).advance_once()
        assert result["status"] == "pending_existing" and result["existing_pending"] == 1
        assert result["initialized"] == 0
        assert ledger.project().semantic_hash == before.semantic_hash
    finally:
        ledger.close()
