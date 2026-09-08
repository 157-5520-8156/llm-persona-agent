"""Two-author Experience from the installed SQLite/HTTP response producer."""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.schemas import ProjectionCursor, WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_world_stimulus_life_intent import ACTOR, WORLD, _build, _model
from test_world_stimulus_life_response import _ResponseHTTP, _settled


def _cursor(ledger):
    p = ledger.project()
    return ProjectionCursor(
        world_revision=p.world_revision,
        deliberation_revision=p.deliberation_revision,
        ledger_sequence=p.ledger_sequence,
    )


async def _accepted_response(path, text):
    provider = _ResponseHTTP(text=text)
    model = _model(provider)
    app = _build(path, model)
    try:
        source = await _settled(app)
        await app.drain_background_once()
        p = app.export_replay_evidence().projection
        response = next(
            x
            for x in p.committed_world_event_refs
            if x.event_type == "CharacterLifeResponseRecorded"
        )
        assert len(provider.stimulus_requests) == 1
    finally:
        await app.aclose()
        await model.aclose()
    return source.event_id, response.event_id


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [None, "我听着雨后的动静，心里松了一点。"])
async def test_public_response_composes_exact_two_author_experience(tmp_path, monkeypatch, text):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "world.sqlite"
    source_ref, response_ref = await _accepted_response(path, text)
    from companion_daemon.world_v2.character_life_experience_runtime import (
        CharacterLifeExperienceRuntime,
    )

    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        runtime = CharacterLifeExperienceRuntime(
            ledger=ledger,
            content_store=store,
            owner_actor_ref=ACTOR,
        )
        experience_id = runtime.accept(
            world_id=WORLD,
            audit_cursor=_cursor(ledger),
            response_event_ref=response_ref,
        )
        p = ledger.project()
        experience = next(x for x in p.experiences if x.experience_id == experience_id)
        assert experience.authority_contract_version == "experience.2"
        (binding,) = experience.values.source_bindings
        assert binding.source_kind == "world_life_response"
        assert binding.settlement.authority_event_ref == source_ref
        assert binding.response_event_ref == response_ref
        assert binding.response.response_text == text
        body = store.read_exact(content_ref=experience.values.summary_ref)
        summary = json.loads(body.text)
        assert summary["character_response"]["response_text"] == text
        assert summary["world_consequence"]["authority_event_ref"] == source_ref
        assert experience.values.privacy_class == "private"
        before = ledger.export_replay_evidence()
        assert (
            runtime.accept(
                world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref
            )
            == experience_id
        )
        assert ledger.export_replay_evidence() == before
    finally:
        store.close()
        ledger.close()


class _FailingCommit:
    """Interrupt one local CAS, never fabricate an accepted event."""

    def __init__(self, ledger, fail_at):
        self.ledger = ledger
        self.fail_at = fail_at
        self.calls = 0
        self.candidate = None

    def __getattr__(self, name):
        return getattr(self.ledger, name)

    def commit_at_cursor(self, events, **kwargs):
        from companion_daemon.world_v2.errors import ConcurrencyConflict

        self.calls += 1
        if self.calls == self.fail_at:
            self.candidate = events
            raise ConcurrencyConflict("offline CAS interruption")
        return self.ledger.commit_at_cursor(events, **kwargs)


def _runtime(ledger, store):
    from companion_daemon.world_v2.character_life_experience_runtime import (
        CharacterLifeExperienceRuntime,
    )

    return CharacterLifeExperienceRuntime(ledger=ledger, content_store=store, owner_actor_ref=ACTOR)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at,advance", [(1, False), (2, False), (2, True)])
async def test_cold_recovery_uses_original_response_after_partial_cas(
    tmp_path,
    monkeypatch,
    fail_at,
    advance,
):
    from companion_daemon.world_v2.errors import ConcurrencyConflict
    from test_character_life_response_runtime import _advance_clock

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "world.sqlite"
    _, response_ref = await _accepted_response(path, None)
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    original_models = ledger.project().model_result_audits
    original_response = ledger.lookup_event_commit(response_ref)[0]
    interrupted = _FailingCommit(ledger, fail_at)
    with pytest.raises(ConcurrencyConflict, match="offline CAS"):
        _runtime(interrupted, store).accept(
            world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref
        )
    assert not ledger.project().experiences
    assert len(ledger.project().experience_proposals) == fail_at - 1
    if advance:
        _advance_clock(ledger, 30, "after-experience-cas")
    store.close()
    ledger.close()
    # New adapters have no role/model object; all recovery authority is durable.
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        runtime = _runtime(ledger, store)
        experience_id = runtime.accept(
            world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref
        )
        p = ledger.project()
        (experience,) = p.experiences
        assert experience.experience_id == experience_id
        assert experience.values.source_bindings[0].response.response_text is None
        assert p.model_result_audits == original_models
        assert ledger.lookup_event_commit(response_ref)[0] == original_response
        assert ledger.rebuild() == p
        before = ledger.export_replay_evidence()
        assert (
            runtime.accept(
                world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref
            )
            == experience_id
        )
        assert ledger.export_replay_evidence() == before
    finally:
        store.close()
        ledger.close()


def _event_like(event, *, value, kind=None, identity=None):
    from companion_daemon.world_v2.event_identity import domain_idempotency_key

    kind = kind or event.event_type
    identity = identity or event.event_id
    envelope = event.model_dump(exclude={"payload_json", "payload_hash"})
    envelope.update(
        event_type=kind,
        event_id=identity,
        idempotency_key=domain_idempotency_key(
            event_type=kind,
            world_id=event.world_id,
            payload=value,
        )
        or identity,
    )
    return WorldEvent.from_payload(payload=value, **envelope)


def _rewrite_proposal(event, mutate):
    from companion_daemon.world_v2.experience_events import experience_mutation_hash
    from companion_daemon.world_v2.schemas import ExperienceValues, experience_semantic_fingerprint

    raw = event.payload()
    mutation = json.loads(raw["proposed_mutation"]["payload_json"])
    mutate(mutation)
    experience = mutation["experience"]
    experience["origin"]["policy_refs"] = mutation["policy_refs"]
    values = ExperienceValues.model_validate_json(json.dumps(experience["values"]))
    experience["semantic_fingerprint"] = experience_semantic_fingerprint(
        values=values,
        policy_refs=tuple(mutation["policy_refs"]),
    )
    mutation["accepted_change_hash"] = experience_mutation_hash(mutation)
    for name in (
        "evidence_refs",
        "policy_refs",
        "evaluated_world_revision",
        "proposal_id",
        "change_id",
        "transition_id",
    ):
        raw[name] = mutation[name]
    raw["proposed_change_hash"] = mutation["accepted_change_hash"]
    raw["proposed_mutation"]["payload_json"] = json.dumps(
        mutation,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _event_like(event, value=raw)


def _submit_proposal_and_effect(ledger, proposal):
    raw = json.loads(proposal.payload()["proposed_mutation"]["payload_json"])
    ledger.commit_at_cursor((proposal,), expected_cursor=_cursor(ledger))
    accepted = _event_like(
        proposal,
        kind="AcceptanceRecorded",
        identity="event:test-acceptance",
        value={
            "status": "accepted",
            "acceptance_id": raw["acceptance_id"],
            "proposal_id": raw["proposal_id"],
            "evaluated_world_revision": raw["evaluated_world_revision"],
            "accepted_change_id": raw["change_id"],
            "accepted_change_hash": raw["accepted_change_hash"],
        },
    )
    committed = _event_like(
        proposal,
        kind="ExperienceCommitted",
        identity=raw["experience"]["origin"]["accepted_event_ref"],
        value=raw,
    )
    return ledger.commit_at_cursor((accepted, committed), expected_cursor=_cursor(ledger))


def _corrupt(mutation, case):
    values = mutation["experience"]["values"]
    binding = values["source_bindings"][0]
    if case == "response_ref":
        binding["response_event_ref"] = binding["settlement"]["authority_event_ref"]
    elif case == "response_revision":
        binding["response_world_revision"] += 1
    elif case == "response_hash":
        binding["response_payload_hash"] = "0" * 64
    elif case == "actor":
        binding["response"]["actor_ref"] = "actor:other"
    elif case == "response_text":
        binding["response"]["response_text"] = "not the chosen response"
    elif case == "model_result":
        binding["response"]["origin"]["model_result_ref"] = "model:unselected"
    elif case == "source_ref":
        binding["settlement"]["authority_event_ref"] = binding["response_event_ref"]
    elif case == "source_hash":
        binding["settlement"]["authority_payload_hash"] = "0" * 64
    elif case == "result_ref":
        binding["settlement"]["result_payload_ref"] = "payload:unsettled"
    elif case == "summary_hash":
        values["summary_payload_hash"] = "0" * 64
    elif case == "evidence":
        mutation["evidence_refs"] = mutation["evidence_refs"][:1]
    elif case == "privacy":
        values["privacy_class"] = "public"
    elif case == "downgrade":
        values["source_bindings"] = [binding["settlement"]]
        mutation["experience"]["authority_contract_version"] = "experience.1"
        mutation["policy_refs"] = ["policy:experience-v1"]
        mutation["evidence_refs"] = mutation["evidence_refs"][:1]
    elif case == "version":
        mutation["experience"]["authority_contract_version"] = "experience.1"
    else:
        raise AssertionError(case)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "response_ref",
        "response_revision",
        "response_hash",
        "actor",
        "response_text",
        "model_result",
        "source_ref",
        "source_hash",
        "result_ref",
        "summary_hash",
        "evidence",
        "privacy",
        "downgrade",
        "version",
    ],
)
async def test_public_acceptance_rejects_rehashed_false_composite_authority(
    tmp_path,
    monkeypatch,
    case,
):
    from companion_daemon.world_v2.errors import ConcurrencyConflict

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "world.sqlite"
    _, response_ref = await _accepted_response(path, "这是我自己的回应。")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        capture = _FailingCommit(ledger, 1)
        with pytest.raises(ConcurrencyConflict):
            _runtime(capture, store).accept(
                world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref
            )
        proposal = _rewrite_proposal(capture.candidate[0], lambda x: _corrupt(x, case))
        before_response = ledger.lookup_event_commit(response_ref)[0]
        with pytest.raises(ValueError, match="experience"):
            _submit_proposal_and_effect(ledger, proposal)
        assert ledger.project().experiences == ()
        assert ledger.lookup_event_commit(response_ref)[0] == before_response
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_another_experience_id_cannot_duplicate_the_same_paired_source(tmp_path, monkeypatch):
    from companion_daemon.world_v2.errors import ConcurrencyConflict

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "world.sqlite"
    _, response_ref = await _accepted_response(path, None)
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        capture = _FailingCommit(ledger, 1)
        with pytest.raises(ConcurrencyConflict):
            _runtime(capture, store).accept(
                world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref
            )
        original = capture.candidate[0]
        _runtime(ledger, store).accept(
            world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref
        )

        def another(mutation):
            mutation["evaluated_world_revision"] = ledger.project().world_revision
            for name in ("proposal_id", "acceptance_id", "change_id", "transition_id"):
                mutation[name] += ":duplicate-source"
            experience = mutation["experience"]
            experience["experience_id"] += ":duplicate-source"
            experience["origin"]["accepted_event_ref"] += ":duplicate-source"
            for name in ("change_id", "transition_id"):
                experience["origin"][name] = mutation[name]

        duplicate = _rewrite_proposal(original, another)
        duplicate = _event_like(
            duplicate, value=duplicate.payload(), identity="event:duplicate-source-proposal"
        )
        with pytest.raises(ValueError, match="source authority is already committed"):
            _submit_proposal_and_effect(ledger, duplicate)
        assert len(ledger.project().experiences) == 1
        assert ledger.rebuild() == ledger.project()
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_reader_proof_requires_original_role_audit_and_exact_summary(tmp_path, monkeypatch):
    from companion_daemon.world_v2.character_life_experience_contract import (
        validate_character_life_experience_summary,
    )
    from companion_daemon.world_v2.character_life_experience_runtime import (
        validate_character_life_experience_binding,
    )

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "world.sqlite"
    _, response_ref = await _accepted_response(path, "我自己的感受🫖。")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        _runtime(ledger, store).accept(
            world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref
        )
        p = ledger.project()
        (experience,) = p.experiences
        (binding,) = experience.values.source_bindings
        body = store.read_exact(content_ref=experience.values.summary_ref).text
        assert (
            validate_character_life_experience_summary(
                body, binding
            ).character_response.response_text
            == "我自己的感受🫖。"
        )
        for value in (body + " ", body.replace("我自己的感受", "世界发生了改变"), "{}"):
            with pytest.raises(ValueError, match="summary_mismatch"):
                validate_character_life_experience_summary(value, binding)
        # The reader proof cannot be replaced by trusting an embedded response
        # event hash alone when its independently recorded model audit is absent.
        for field in ("proposal_audits", "model_result_audits"):
            damaged_pin = p.model_copy(update={field: ()})
            with pytest.raises(ValueError):
                validate_character_life_experience_binding(
                    state=damaged_pin,
                    world_id=WORLD,
                    binding=binding,
                )
        with pytest.raises(ValueError):
            validate_character_life_experience_binding(
                state=p, world_id="world:other", binding=binding
            )
    finally:
        store.close()
        ledger.close()
