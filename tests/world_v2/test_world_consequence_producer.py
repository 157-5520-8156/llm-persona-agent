"""Public SQLite producer acceptance, with explicit model-provider fixtures.

Reviewer verdicts are stipulated protocol inputs, not evidence that a real
critic detects all unauthorized character actions. No accepted event is seeded.
"""

import hashlib
import json

import pytest

from companion_daemon.world_v2.life_content_store import (
    SQLiteImmutableLifeContentStore,
    StoredLifeContent,
)
from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentCapabilityManifest,
    parse_world_author_draft,
)
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.life_development_source_closure import (
    life_development_source_closure_messages,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import (
    NOW,
    OWNER,
    WORLD_ID,
    _PinnedCapsuleCompiler,
    _SequenceModel,
    _location_bound_world_draft,
    _location_capability,
    _novel_origin_review,
    _replace_event_payload,
    _seed_clock,
)
from test_world_author_request_audit import _CurrentManifest, _ReceivedAuthor, _json


ENVIRONMENT = ("冰雹打断了院内的树枝。", "冰雹很快停止，院内落着细小冰粒。")


def _draft(wake):
    value = json.loads(
        _location_bound_world_draft(
            wake=wake,
            capability=_location_capability(),
            timing={"mode": "now", "duration_minutes": 20},
            privacy_class="shareable",
            causal_authority="world_contingency",
            outcome_resolution_authority="world_contingency",
        )
    )
    value["premise"] = "院内开始降下冰雹。"
    value["claim_declarations"][0]["summary"] = "院内出现冰雹天气。"
    for outcome, text in zip(value["outcomes"], ENVIRONMENT, strict=True):
        outcome.pop("text")
        outcome["world_consequence"] = {
            "contract": "world-consequence.2",
            "environment_text": text,
        }
    return value


def _runtime(ledger, store, wake, author, general, focused):
    return LifeDevelopmentRuntime(
        ledger=ledger,
        content_store=store,
        world_author=author,
        character_interior=_SequenceModel(model="fixture:unused-character", outputs=()),
        source_closure_reviewer=general,
        novel_origin_critic=focused,
        capsule_compiler=_PinnedCapsuleCompiler(ledger=ledger),
        capability_manifest_compiler=_CurrentManifest(wake=wake),
        owner_actor_ref=OWNER,
    )


async def _advance(runtime, wake):
    return await runtime.advance_once(
        wake_event_ref=wake.event_id,
        trace_id="trace:consequence-producer",
        correlation_id="correlation:consequence-producer",
    )


def _assert_original_requests(ledger, store, author):
    audits = [json.loads(item.audit_json) for item in ledger.project().model_result_audits]
    for raw, before_call in zip(author.received, author.stored_before_call, strict=True):
        digest = hashlib.sha256(raw.encode()).hexdigest()
        assert before_call is not None
        assert before_call.content_kind == "raw_model_request"
        assert before_call.text == raw
        assert before_call.content_payload_hash == digest
        assert store.read_exact(content_ref="content:world-author-request:" + digest) == before_call
        assert sum(item["request_hash"] == digest for item in audits) == 1


def _assert_review_input(messages, original_request, expected, *, focused):
    user = json.loads(messages[-1]["content"])
    pinned_key = "pinned_authority" if focused else "pinned_source_evidence"
    assert user[pinned_key]["execution_authority"] == {
        "authority": original_request["execution_authority"],
        "execution_materials": original_request["execution_materials"],
    }
    assert user["evidence_packet_binding"]["contract"] == (
        "life-development-novel-origin-review-evidence-packet.7"
        if focused
        else "life-development-general-source-review-evidence-packet.4"
    )
    for surface, outcome in zip(
        user["reviewed_surface"]["outcomes"], expected["outcomes"], strict=True
    ):
        assert "text" not in surface
        assert surface["world_consequence"] == outcome["world_consequence"]
    if focused:
        assert user["review_contract"] == "life-development-novel-origin-review.6"
        assert (
            "outcomes.0.world_consequence.environment_text"
            in (user["parser_coordinate_catalog"]["outcome_prerequisite_paths"])
        )


def _assert_general_audit(ledger, result, original_request, expected):
    """Rebuild the public packet and match its actual deterministic audit hash."""
    proposal, _ = ledger.lookup_event_commit(result.proposal_event_ref)
    manifest = LifeDevelopmentCapabilityManifest.model_validate_json(
        _json(proposal.payload()["world_author_deliberation"]["capability_manifest"])
    )
    context = json.loads(
        _PinnedCapsuleCompiler(ledger=ledger)
        .compile_for_deliberation(None)
        .capsule.model_content_json
    )
    messages = life_development_source_closure_messages(
        context=context,
        manifest=manifest,
        draft=parse_world_author_draft(raw=_json(expected), manifest=manifest, logical_time=NOW),
        cited_events=(),
        execution_authority={
            "authority": original_request["execution_authority"],
            "execution_materials": original_request["execution_materials"],
        },
    )
    _assert_review_input(messages, original_request, expected, focused=False)
    digest = hashlib.sha256(_json(messages).encode()).hexdigest()
    audits = [json.loads(item.audit_json) for item in ledger.project().model_result_audits]
    matching = [item for item in audits if item["request_hash"] == digest]
    assert len(matching) == 1
    assert matching[0]["model_id"] == "deterministic:life-source-closure"


def _assert_occurrence(ledger, store, result, expected):
    assert result.status == "occurrence_committed"
    projection = ledger.project()
    assert projection.plans == ()
    assert projection.experiences == ()
    assert len(projection.world_occurrences) == 1
    occurrence = projection.world_occurrences[0]
    assert occurrence.occurrence_id == result.occurrence_id
    assert occurrence.status == "active"
    event_types = [item.event_type for item in projection.committed_world_event_refs]
    assert event_types.count("WorldOccurrenceCommitted") == 1
    assert event_types.count("WorldOccurrenceActivated") == 1
    expected_hashes = []
    for descriptor, outcome in zip(
        occurrence.candidate_outcomes, expected["outcomes"], strict=True
    ):
        canonical = _json(outcome["world_consequence"])
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        expected_hashes.append(digest)
        assert descriptor.result_contract == "world-consequence.2"
        assert descriptor.result_payload_hash == descriptor.content_payload_hash == digest
        stored = store.read_exact(content_ref=descriptor.content_ref)
        assert stored is not None and stored.content_kind == "outcome_candidate"
        assert stored.text == canonical
    event, _ = ledger.lookup_event_commit(result.proposal_event_ref)
    proposal = event.payload()
    assert proposal["possibility_authority_version"] == "life-development-possibility.8"
    assert (
        proposal["world_author_deliberation"]["world_consequence_content_hashes"] == expected_hashes
    )
    final_author_ref = proposal["world_author_deliberation"]["final_model_result_ref"]
    matches = [
        json.loads(value["response_text"])
        for item in projection.proposal_audits
        if (value := json.loads(item.proposal_json)).get("source_model_result") == final_author_ref
    ]
    assert len(matches) == 1
    assert matches[0]["model_role"] == "world_author"
    assert matches[0]["world_consequence_content_hashes"] == expected_hashes
    assert proposal["character_interior_decision"] is None
    return projection


@pytest.mark.asyncio
async def test_current_environment_passes_original_request_review_and_sqlite_acceptance(tmp_path):
    path = tmp_path / "producer.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        draft = _draft(wake)
        author = _ReceivedAuthor(store, (_json(draft),))
        general = _SequenceModel(model="fixture:general-must-not-be-called", outputs=())
        focused = _SequenceModel(
            model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),)
        )
        result = await _advance(_runtime(ledger, store, wake, author, general, focused), wake)
        before = _assert_occurrence(ledger, store, result, draft)
        assert len(author.received) == focused.calls == 1
        assert general.calls == 0
        _assert_original_requests(ledger, store, author)
        original_user = json.loads(json.loads(author.received[0])[1]["content"])
        assert original_user["execution_authority"]["execution_bindings"] == []
        assert original_user["execution_materials"] == []
        _assert_general_audit(ledger, result, original_user, draft)
        _assert_review_input(focused.messages[0], original_user, draft, focused=True)
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        cold_author = _ReceivedAuthor(store, ())
        cold_general = _SequenceModel(model="fixture:general", outputs=())
        cold_focused = _SequenceModel(model="fixture:focused", outputs=())
        repeated = await _advance(
            _runtime(ledger, store, wake, cold_author, cold_general, cold_focused), wake
        )
        assert repeated.proposal_event_ref == result.proposal_event_ref
        assert repeated.occurrence_id == result.occurrence_id
        assert repeated.status == "occurrence_committed"
        assert cold_author.received == []
        assert cold_general.calls == cold_focused.calls == 0
        assert ledger.project() == before
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_exact_character_authorship_rejection_returns_to_same_author_and_is_rereviewed(
    tmp_path,
):
    """The reviewer supplies this exact verdict; the test does not detect prose semantics."""
    path = tmp_path / "corrected-producer.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        corrected = _draft(wake)
        rejected = json.loads(_json(corrected))
        fragment = "她及时收回了手账。"
        rejected["outcomes"][0]["world_consequence"]["environment_text"] += fragment
        finding = {
            "prose_path": "outcomes.0.world_consequence.environment_text",
            "violation_kinds": ["character_interior_authorship"],
            "exact_fragments": [fragment],
        }
        author = _ReceivedAuthor(store, (_json(rejected), _json(corrected)))
        general = _SequenceModel(model="fixture:general-must-not-be-called", outputs=())
        focused = _SequenceModel(
            model="fixture:focused",
            outputs=(
                _novel_origin_review(
                    decision="unsupported",
                    unsupported_outcome_prerequisites=(finding,),
                    reason="No prior character execution authorizes retrieving the journal.",
                ),
                _novel_origin_review(decision="supported"),
            ),
        )
        result = await _advance(_runtime(ledger, store, wake, author, general, focused), wake)
        _assert_occurrence(ledger, store, result, corrected)
        assert len(author.received) == focused.calls == 2
        assert general.calls == 0
        _assert_original_requests(ledger, store, author)
        original_messages = json.loads(author.received[0])
        correction_messages = json.loads(author.received[1])
        original_user = json.loads(original_messages[1]["content"])
        assert correction_messages[:-2] == original_messages
        assert correction_messages[-2] == {"role": "assistant", "content": _json(rejected)}
        correction = json.loads(correction_messages[-1]["content"])
        assert correction["source_closure_failure"]["unsupported_outcome_prerequisites"] == [
            finding
        ]
        assert original_user["execution_authority"]["execution_bindings"] == []
        for index, draft in enumerate((rejected, corrected)):
            _assert_general_audit(ledger, result, original_user, draft)
            _assert_review_input(focused.messages[index], original_user, draft, focused=True)
        proposal, _ = ledger.lookup_event_commit(result.proposal_event_ref)
        payload = proposal.payload()
        assert payload["repair_ordinal"] == 1
        assert (
            payload["world_author_raw_output_hash"]
            == hashlib.sha256(_json(_json(corrected)).encode()).hexdigest()
        )
        # Both actual responses remain independently auditable. Only the repaired
        # canonical outcomes may enter the accepted occurrence.
        response_hashes = {
            json.loads(item.audit_json)["response_hash"]
            for item in ledger.project().model_result_audits
        }
        assert hashlib.sha256(_json(rejected).encode()).hexdigest() in response_hashes
        assert hashlib.sha256(_json(corrected).encode()).hexdigest() in response_hashes
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tamper", "failure"),
    [
        ("substitute", "descriptor changed its original author content"),
        ("missing_hashes", "lacks the ordered original author content hashes"),
        ("downgrade", "requires explicit possibility authority version .8"),
    ],
)
async def test_public_final_batch_cannot_rebind_original_author_proof(
    tmp_path, monkeypatch, tamper, failure
):
    path = tmp_path / "tamper.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        author = _ReceivedAuthor(store, (_json(_draft(wake)),))
        general = _SequenceModel(model="fixture:unused-general", outputs=())
        focused = _SequenceModel(
            model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),)
        )
        original = ledger.commit_at_cursor
        before_final = []

        def change_final(events, **kwargs):
            changed = []
            for event in events:
                payload = event.payload()
                if (
                    event.event_type == "ProposalRecorded"
                    and payload.get("proposal_kind") == "life_development"
                ):
                    before_final.append(ledger.project())
                    _assert_original_requests(ledger, store, author)
                    if tamper == "downgrade":
                        payload["possibility_authority_version"] = "life-development-possibility.7"
                    elif tamper == "missing_hashes":
                        authority = payload["world_author_deliberation"]
                        authority.pop("world_consequence_content_hashes")
                        payload["world_author_deliberation_hash"] = hashlib.sha256(
                            _json(authority).encode()
                        ).hexdigest()
                    else:
                        substitute = _json(
                            {
                                "contract": "world-consequence.2",
                                "environment_text": "她及时收回了手账。",
                            }
                        )
                        digest = hashlib.sha256(substitute.encode()).hexdigest()
                        ref = "content:fixture:unauthored-substitute"
                        store.put_if_absent(
                            StoredLifeContent(
                                content_ref=ref,
                                content_kind="outcome_candidate",
                                text=substitute,
                                content_payload_hash=digest,
                            )
                        )
                        descriptor = payload["possibility_authority"]["outcomes"][0]["descriptor"]
                        descriptor["content_ref"] = ref
                        descriptor["content_payload_hash"] = digest
                        descriptor["result_payload_hash"] = digest
                        for binding in payload["content_bindings"]:
                            if binding["role"] == "outcome:1":
                                binding["content_ref"] = ref
                                binding["content_payload_hash"] = digest
                        payload["possibility_authority_hash"] = hashlib.sha256(
                            _json(payload["possibility_authority"]).encode()
                        ).hexdigest()
                    event = _replace_event_payload(event, payload=payload)
                changed.append(event)
            return original(tuple(changed), **kwargs)

        monkeypatch.setattr(ledger, "commit_at_cursor", change_final)
        with pytest.raises(ValueError, match=failure):
            await _advance(_runtime(ledger, store, wake, author, general, focused), wake)
        assert len(before_final) == len(author.received) == focused.calls == 1
        assert ledger.project() == before_final[0]
        assert ledger.project().world_occurrences == ()
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_current_consequence_requires_an_installed_focused_critic(tmp_path):
    path = tmp_path / "no-critic.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        author = _ReceivedAuthor(store, (_json(_draft(wake)),))
        result = await _advance(_runtime(ledger, store, wake, author, None, None), wake)
        assert result.status == "technical_failure"
        assert result.reason_code == "life_development.world_consequence_critic_not_configured"
        assert len(author.received) == 1
        _assert_original_requests(ledger, store, author)
        assert ledger.project().world_occurrences == ()
        assert ledger.project().plans == ()
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_corrected_author_and_review_audits_cold_recover_before_final_effect(
    tmp_path, monkeypatch
):
    path = tmp_path / "corrected-recovery.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        corrected = _draft(wake)
        rejected = json.loads(_json(corrected))
        fragment = "她及时收回了手账。"
        rejected["outcomes"][0]["world_consequence"]["environment_text"] += fragment
        author = _ReceivedAuthor(store, (_json(rejected), _json(corrected)))
        general = _SequenceModel(model="fixture:general", outputs=())
        focused = _SequenceModel(
            model="fixture:focused",
            outputs=(
                _novel_origin_review(
                    decision="unsupported",
                    unsupported_outcome_prerequisites=(
                        {
                            "prose_path": "outcomes.0.world_consequence.environment_text",
                            "violation_kinds": ["character_interior_authorship"],
                            "exact_fragments": [fragment],
                        },
                    ),
                ),
                _novel_origin_review(decision="supported"),
            ),
        )
        original = ledger.commit_at_cursor
        checkpoints = []

        def stop_before_final(events, **kwargs):
            if any(
                event.event_type == "ProposalRecorded"
                and event.payload().get("proposal_kind") == "life_development"
                for event in events
            ):
                assert len(author.received) == focused.calls == 2
                _assert_original_requests(ledger, store, author)
                checkpoints.append(ledger.project())
                raise InterruptedError("fixture: before final consequence effect")
            return original(events, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(ledger, "commit_at_cursor", stop_before_final)
            with pytest.raises(InterruptedError, match="before final consequence effect"):
                await _advance(_runtime(ledger, store, wake, author, general, focused), wake)
        assert len(checkpoints) == 1
        assert checkpoints[0].world_occurrences == ()
        original_audits = tuple(item.audit_json for item in checkpoints[0].model_result_audits)
        original_proposal_audits = tuple(
            item.proposal_json for item in checkpoints[0].proposal_audits
        )
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        cold_author = _ReceivedAuthor(store, ())
        cold_general = _SequenceModel(model="fixture:general", outputs=())
        cold_focused = _SequenceModel(model="fixture:focused", outputs=())
        result = await _advance(
            _runtime(ledger, store, wake, cold_author, cold_general, cold_focused), wake
        )
        _assert_occurrence(ledger, store, result, corrected)
        assert cold_author.received == []
        assert cold_general.calls == cold_focused.calls == 0
        assert (
            tuple(item.audit_json for item in ledger.project().model_result_audits)
            == original_audits
        )
        assert (
            tuple(item.proposal_json for item in ledger.project().proposal_audits)
            == original_proposal_audits
        )
    finally:
        store.close()
        ledger.close()
