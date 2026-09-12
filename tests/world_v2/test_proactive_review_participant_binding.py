"""Proactive clock review retains configured participant identity in its pin."""

import json

import pytest

from test_proactive_visible_source_gate import (
    COUNTERPART_HISTORY_TEXT,
    _run_scenario,
)


def _binding(case, **changes):
    from companion_daemon.world_v2.deliberation import VisibleReviewParticipantBinding

    return VisibleReviewParticipantBinding.model_validate({
        "contract": "visible-review-participant-binding.1",
        "world_id": case.capsule.world_id, "actor_ref": case.capsule.actor_ref,
        "counterpart_actor_ref": case.observation.actor,
        "capsule_id": case.capsule.capsule_id, "trigger_ref": case.capsule.trigger_ref,
        "world_revision": case.capsule.world_revision,
        "deliberation_revision": case.capsule.deliberation_revision,
        "ledger_sequence": case.capsule.ledger_sequence,
        **changes,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("review_version", ["3", "4"])
async def test_proactive_clock_can_cite_observed_counterpart_history(
    tmp_path, monkeypatch, review_version,
):
    evidence, authors, reviews, _delivery = await _run_scenario(
        tmp_path, monkeypatch, "valid_claim", review_version=review_version,
        history_subject="counterpart",
    )
    author = json.loads(authors[0]["messages"][-1]["content"])
    assert author["inner_turn"]["trigger_ref"].startswith("event:trigger:clock:")
    review = json.loads(reviews[-1]["messages"][-1]["content"])
    (claim,) = review["world_claims"]
    assert claim["claim_text"] == COUNTERPART_HISTORY_TEXT
    assert claim["scope"] == "counterpart_history"
    rows = ([dict(zip(table["columns"], row, strict=True))
             for table in review["source_reference_tables"] for row in table["rows"]]
            if review_version == "4" else review["source_references"])
    (source,) = [row for row in rows if row["source_ref"] == claim["source_refs"][0]]
    assert source["support_subject_ref"] == "user:geoff"
    assert source["support_subject_role"] == "counterpart"
    assert source["support_eligibility"] == "eligible"
    assert len([a for a in evidence.projection.actions if a.kind == "proactive_message"]) == 2
    from companion_daemon.world_v2.deliberation import ModelInput
    from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
    from companion_daemon.world_v2.visible_source_proactive import qualify_capability

    (audit,) = [RecordedModelResultAudit.model_validate_json(row.audit_json)
                for row in evidence.projection.model_result_audits
                if (lineage := RecordedModelResultAudit.model_validate_json(row.audit_json).character_interior_lineage)
                is not None and lineage.purpose == "proactive_contact"]
    carrier = json.loads(audit.visible_source_review_json)
    requirement = carrier["requirement_json"]
    original = ModelInput.model_validate_json(json.loads(requirement)["original_input_json"])
    assert original.trigger_message is None
    assert original.visible_review_participants.counterpart_actor_ref == "user:geoff"
    original = original.model_copy(update={"visible_source_requirement_json": requirement})
    for wrong in (None, "user:unrelated"):
        with pytest.raises(ValueError, match="differs from original review participant binding"):
            qualify_capability(
                request=original, payload={"counterpart_ref": wrong},
                world_id=original.visible_review_participants.world_id,
                actor_ref=original.visible_review_participants.actor_ref,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("review_version", ["3", "4"])
async def test_proactive_counterpart_source_cannot_be_relabelled_as_companion(
    tmp_path, monkeypatch, review_version,
):
    evidence, _authors, _reviews, _delivery = await _run_scenario(
        tmp_path, monkeypatch, "wrong_review_subject", review_version=review_version,
        history_subject="counterpart",
    )
    assert not [a for a in evidence.projection.actions if a.kind == "proactive_message"]


@pytest.mark.asyncio
async def test_clock_participant_binding_does_not_promote_an_unrelated_selected_actor(tmp_path):
    from companion_daemon.world_v2.deliberation import ModelInput
    from companion_daemon.world_v2.context_resolver import query_from_projection
    from companion_daemon.world_v2.ledger_context_resolver import (
        ContextRelevanceScope, context_capsule_compiler_from_ledger,
    )
    from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
    from test_visible_source_composer import _request
    from test_visible_selected_source_context import _sources

    async with _sources(tmp_path, extra=True) as case:
        # The default fixture intentionally selects only the configured user.
        # Select both independently committed actors to test qualification,
        # using the public resolver rather than altering any material bytes.
        actor = case.capsule.actor_ref
        compiler = context_capsule_compiler_from_ledger(
            ledger=case.ledger,
            relevance_scope=ContextRelevanceScope(
                actor_ref=actor, related_subject_refs=(case.observation.actor, "user:unrelated"),
            ),
        )
        case.capsule = compiler.compile_for_deliberation(query_from_projection(
            case.ledger.project(), actor_ref=actor, trigger_ref=case.wake.event_id,
        )).capsule
        old = _request(case.capsule)
        old_raw = old.model_dump_json()
        assert "visible_review_participants" not in old_raw
        assert ModelInput.model_validate_json(old_raw).model_dump_json() == old_raw
        old_rows = compile_visible_source_table(request=old, capsule=case.capsule).source_references()
        assert all(r["support_subject_role"] is None and r["support_eligibility"] == "baseline_only"
                   for r in old_rows if r.get("support_subject_ref") in {case.observation.actor, "user:unrelated"})
        request = old.model_copy(update={"visible_review_participants": _binding(case)})
        table = compile_visible_source_table(request=request, capsule=case.capsule)
        rows = table.source_references()
        own = [r for r in rows if r.get("support_subject_ref") == case.observation.actor]
        others = [r for r in rows if r.get("support_subject_ref") == "user:unrelated"]
        assert own and others, "public producer must retain both distinct source actors"
        assert any(r["support_eligibility"] == "eligible" for r in own)
        assert all(r["support_subject_role"] == "counterpart" for r in own)
        assert all(r["support_subject_role"] is None and r["support_eligibility"] == "baseline_only"
                   for r in others)
        assert not any(r["kind"] == "current_counterpart_report" for r in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    "world_id", "actor_ref", "capsule_id", "trigger_ref", "world_revision",
    "deliberation_revision", "ledger_sequence", "counterpart_actor_ref", "contract",
])
async def test_participant_binding_rejects_mismatched_original_pin(tmp_path, mutation):
    from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
    from test_visible_selected_source_context import _sources

    async with _sources(tmp_path) as case:
        values = _binding(case).model_dump()
        changed = (values[mutation] + 1 if type(values[mutation]) is int else
                   "0" * 64 if mutation == "capsule_id" else "other:" + values[mutation])
        with pytest.raises(ValueError):
            binding = _binding(case, **{mutation: changed})
            request = case.request.model_copy(update={"visible_review_participants": binding})
            compile_visible_source_table(request=request, capsule=case.capsule)


@pytest.mark.asyncio
@pytest.mark.parametrize("review_version", ["3", "4"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_participant_identity_cold_recovery_uses_the_original_audited_choice(
    tmp_path, monkeypatch, review_version, legacy,
):
    from test_proactive_visible_source_gate import (
        test_reviewed_proactive_cold_replay_has_no_new_calls_or_duplicate_actions as cold_replay,
    )

    await cold_replay(
        tmp_path, monkeypatch, pause_before_acceptance=True, review_version=review_version,
        legacy_claim_lanes=False, legacy_review_participants=legacy,
        scenario="source_free" if legacy else "valid_claim",
        history_subject="companion" if legacy else "counterpart",
    )


@pytest.mark.asyncio
async def test_participant_identity_upgrade_does_not_reuse_an_old_unsubmitted_checkpoint(
    tmp_path, monkeypatch,
):
    from test_proactive_visible_source_gate import (
        test_old_prepared_proactive_choice_cannot_bypass_new_claim_lanes as old_prepared,
    )

    await old_prepared(
        tmp_path, monkeypatch, review_required=True,
        legacy_claim_lanes=False, legacy_review_participants=True,
    )
