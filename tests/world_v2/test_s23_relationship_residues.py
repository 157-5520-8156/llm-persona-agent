from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.character_interior.inbound_appraisal_wire import (
    _proposal_from_draft,
)
from companion_daemon.world_v2.character_interior.inbound_author import _parse_combined
from companion_daemon.world_v2.character_interior.inbound_relationship import (
    InboundRelationshipSignalWorker,
)
from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
    _expand_compact_gate_payload,
)
from companion_daemon.world_v2.character_interior.relationship_context import (
    install_relationship_authored_residues,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.deliberation import (
    DeliberationResult,
    ModelInput,
    ModelRoute,
    TriggerMessage,
)
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.present_prompt import (
    attach_hitchhiked_relationship_residue,
    compile_slim_consider_payload,
    compile_slim_interior_envelope,
    json_schema_g4_metrics,
    slim_consider_instruction,
    slim_consider_json_schema,
)
from companion_daemon.world_v2.proposal_audit import (
    ProposalAuditContext,
    ProposalAuditRecorder,
)
from companion_daemon.world_v2.proposal_envelope import DecisionProposal
from companion_daemon.world_v2.relationship_acceptance_runtime import (
    RelationshipAcceptanceRuntime,
)
from companion_daemon.world_v2.relationship_proposal_compiler import (
    RelationshipProposalCompiler,
)
from companion_daemon.world_v2.schemas import (
    ProjectionCursor,
    RelationshipCommitmentProjection,
)
from companion_daemon.world_v2.unified_inbound_decision import (
    inspect_unified_inbound_decision,
)

from test_appraisal_authority import WORLD_ID, prepare_claimed_interaction
from test_proposal_audit import _digest, _result


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _relationship_context(*, extra: dict[str, object] | None = None) -> dict[str, object]:
    value = {
        "relationship_id": "relationship:user:primary",
        "subject_ref": "user:primary",
        "stage": "close_friend",
        "variables": {"closeness_bp": 8100, "trust_bp": 7600},
        "temperature": "ordinary",
        "hysteresis": {},
        "commitment_refs": ["commitment:close"],
        "last_adjusted_at": "2026-08-16T11:00:00+00:00",
    }
    if extra:
        value.update(extra)
    return {
        "world_id": "world:s23",
        "actor_ref": "actor:companion",
        "world_revision": 4,
        "deliberation_revision": 1,
        "ledger_sequence": 9,
        "logical_time": "2026-08-16T12:00:00+00:00",
        "slices": {
            "relationship_slice": {
                "availability": "available",
                "items": [
                    {
                        "item_ref": "relationship:user:primary",
                        "value": value,
                    }
                ],
            }
        },
    }


def test_snapshot_relationship_facet_keeps_authored_signal_and_commitment_prose() -> None:
    snapshot = compile_inner_life_snapshot(
        _relationship_context(
            extra={
                "recent_authored_signals": [
                    {
                        "signal_code": "他说那句话我心里一动",
                        "rationale_code": "不像随口敷衍",
                        "confidence_bp": 7200,
                        "suggested_deltas": {"closeness_bp": 400},
                    }
                ],
                "accepted_commitments": [
                    {
                        "committed_stage": "close_friend",
                        "commitment_code": "we_are_close_now",
                        "visible_text_span": "那就当你是我很熟的朋友了",
                    }
                ],
            }
        )
    ).model_view()

    relationship = snapshot["materials"]["relationship"][0]
    assert relationship["recent_authored_signals"] == [
        {
            "signal_code": "他说那句话我心里一动",
            "rationale_code": "不像随口敷衍",
            "confidence_bp": 7200,
        }
    ]
    assert relationship["accepted_commitments"] == [
        {
            "committed_stage": "close_friend",
            "commitment_code": "we_are_close_now",
            "visible_text_span": "那就当你是我很熟的朋友了",
        }
    ]
    serialized = str(snapshot["materials"]["relationship"])
    assert "suggested_deltas" not in serialized
    assert "你在暧昧" not in serialized
    assert snapshot["faculties"]["subjective_relationship"]["material_keys"] == [
        "relationship"
    ]


def test_snapshot_does_not_translate_bp_into_an_ambiguous_stage() -> None:
    snapshot = compile_inner_life_snapshot(_relationship_context()).model_view()
    relationship = snapshot["materials"]["relationship"][0]
    assert relationship["stage"] == "close_friend"
    assert "recent_authored_signals" not in relationship
    assert "accepted_commitments" not in relationship
    serialized = str(relationship)
    assert "ambiguous" not in serialized
    assert "lover" not in serialized
    assert "暧昧" not in serialized


def test_snapshot_drops_uninstalled_romantic_commitment_prose() -> None:
    snapshot = compile_inner_life_snapshot(
        _relationship_context(
            extra={
                "accepted_commitments": [
                    {
                        "committed_stage": "lover",
                        "commitment_code": "we_are_together",
                        "visible_text_span": "那我们在一起吧",
                    },
                    {
                        "committed_stage": "ambiguous",
                        "commitment_code": "maybe_more",
                        "visible_text_span": "好像有点不一样",
                    },
                ]
            }
        )
    ).model_view()

    relationship = snapshot["materials"]["relationship"][0]
    assert "accepted_commitments" not in relationship
    serialized = str(relationship)
    assert "lover" not in serialized
    assert "ambiguous" not in serialized


def test_present_stage_note_does_not_ask_her_to_overthink() -> None:
    instruction = slim_consider_instruction()
    assert "please overthink" not in instruction.lower()
    assert "relationship_signal" not in instruction
    assert "about_us" in instruction


def test_present_stage_note_steers_neither_toward_nor_away_from_closeness() -> None:
    """The stage is evidence; the host must not lobby for or against movement."""

    instruction = slim_consider_instruction()
    assert "no target stage and no preferred direction" in instruction
    assert "us_deltas" in instruction
    assert "这件事只有你能定" in instruction
    for discouragement in (
        "will not change relationship scores",
        "do not have to move the stage",
        "not a romance script",
    ):
        assert discouragement not in instruction


def test_join_folds_projection_residues_onto_the_relationship_slice() -> None:
    context = _relationship_context()
    projection = SimpleNamespace(
        relationship_signals=(
            SimpleNamespace(
                subject_ref="user:primary",
                signal_code="他说那句话我心里一动",
                rationale_code="不像随口敷衍",
                confidence_bp=7200,
                accepted_at=NOW,
            ),
            SimpleNamespace(
                subject_ref="npc:lin",
                signal_code="林那边另算",
                rationale_code="不是对你",
                confidence_bp=5100,
                accepted_at=NOW,
            ),
        ),
        relationship_commitments=(
            SimpleNamespace(
                status="active",
                subject_ref="user:primary",
                relationship_id="relationship:user:primary",
                committed_stage="close_friend",
                commitment_code="we_are_close_now",
                visible_text_span="那就当你是我很熟的朋友了",
                committed_at=NOW,
            ),
            SimpleNamespace(
                status="active",
                subject_ref="user:primary",
                relationship_id="relationship:user:primary",
                committed_stage="lover",
                commitment_code="we_are_together",
                visible_text_span="那我们在一起吧",
                committed_at=NOW,
            ),
        ),
    )

    installed = install_relationship_authored_residues(context, projection)
    snapshot = compile_inner_life_snapshot(installed).model_view()
    relationship = snapshot["materials"]["relationship"][0]
    assert relationship["recent_authored_signals"] == [
        {
            "signal_code": "他说那句话我心里一动",
            "rationale_code": "不像随口敷衍",
            "confidence_bp": 7200,
        }
    ]
    assert relationship["accepted_commitments"] == [
        {
            "committed_stage": "close_friend",
            "commitment_code": "we_are_close_now",
            "visible_text_span": "那就当你是我很熟的朋友了",
        }
    ]
    assert "lover" not in str(relationship)
    assert "林那边另算" not in str(relationship)


def test_relationship_commitment_schema_accepts_the_stages_she_declares() -> None:
    """ambiguous and lover are hers to name; only she can put them on the ledger."""

    accept = RelationshipCommitmentProjection.model_fields[
        "committed_stage"
    ].annotation
    assert "ambiguous" in accept.__args__
    assert "lover" in accept.__args__
    for stage in ("ambiguous", "lover", "close_friend"):
        assert (
            RelationshipCommitmentProjection.stage_uses_installed_commitment_protocol(
                stage
            )
            == stage
        )
    with pytest.raises(ValueError, match="installed stage"):
        RelationshipCommitmentProjection.stage_uses_installed_commitment_protocol(
            "spouse"
        )


def _slim_payload(**extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "messages": ["那就当你是我很熟的朋友了"],
        "felt": "心里还搁着刚才那句",
        "stuck_with_me": "他说完我就一直在想他到底怎么看我",
        "wants": "想把这件事慢慢看清楚",
        "photo": False,
    }
    payload.update(extra)
    return payload


def _slim_request(*, source_event, evaluated_world_revision: int) -> ModelInput:
    return ModelInput(
        call_id="call:s23-slim",
        attempt_id="attempt:s23-slim",
        route=ModelRoute(tier="flash", reason_code="test", router_version="test.1"),
        capsule_id="a" * 64,
        trigger_ref=source_event.event_id,
        evaluated_world_revision=evaluated_world_revision,
        evaluated_deliberation_revision=0,
        evaluated_ledger_sequence=3,
        model_content_json=json.dumps(
            {
                "logical_time": "2026-08-16T12:00:00+00:00",
                "slices": {
                    "relationship_slice": {
                        "availability": "available",
                        "items": [
                            {
                                "item_ref": "relationship:user:test",
                                "value": {
                                    "subject_ref": "user:test",
                                    "stage": "stranger",
                                },
                            }
                        ],
                    }
                },
            },
            separators=(",", ":"),
        ),
        trigger_message=TriggerMessage(
            event_ref=source_event.event_id,
            event_payload_hash="sha256:" + source_event.payload_hash,
            observation_ref="message:1",
            source_world_revision=1,
            actor="user:test",
            channel="direct_message",
            reply_target="qq:user:test",
            text="我今天还是特地来和你说一声。",
        ),
    )


def test_slim_schema_still_fits_g4_without_heavy_relationship_fields() -> None:
    required, total, depth = json_schema_g4_metrics(slim_consider_json_schema())
    assert required <= 3
    assert total <= 16  # descriptive slim shape; provider G4 is the compact tool
    assert depth <= 2
    properties = slim_consider_json_schema()["properties"]
    assert "relationship_signal" not in properties
    assert "relationship_commitment" not in properties
    assert "about_us" not in properties
    assert "we_are" not in properties


def test_slim_compile_keeps_prose_residue_off_appraisal() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(
            about_us="他说那句话我心里一动",
            why_us="不像随口敷衍",
            keep_impression=True,
            relationship_signal={
                "signal_code": "should_not_enter_slim",
                "rationale_code": "slim_must_not_write_deltas",
                "confidence_bp": 8000,
                "persistence": "durable",
                "suggested_deltas": {"closeness_bp": 400},
            },
        )
    )

    assert compiled is not None
    private_state = compiled["expression_draft"]["private_turn_state"]
    assert private_state["about_us"] == "他说那句话我心里一动"
    assert private_state["why_us"] == "不像随口敷衍"
    assert private_state["keep_impression"] is True
    assert "relationship_signal" not in compiled["appraisal_draft"]
    assert "relationship_commitment" not in compiled["appraisal_draft"]
    assert compiled["appraisal_draft"]["appraise"] is True
    assert compiled["appraisal_draft"]["affect"] == "no_change"
    assert compiled["appraisal_draft"]["meanings"][0]["meaning"] == "心里还搁着刚才那句"


def test_slim_compile_does_not_invent_residue_when_she_omits_it() -> None:
    compiled = compile_slim_consider_payload(_slim_payload())
    assert compiled is not None
    private_state = compiled["expression_draft"]["private_turn_state"]
    assert "about_us" not in private_state
    assert "why_us" not in private_state
    assert "we_are" not in private_state
    assert "keep_impression" not in private_state
    attached = attach_hitchhiked_relationship_residue(compiled)
    assert "relationship_signal" not in attached["appraisal_draft"]
    assert "relationship_commitment" not in attached["appraisal_draft"]


def test_slim_compile_drops_an_incomplete_commitment() -> None:
    """All three fields or nothing; the host never completes a commitment."""

    for partial in (
        _slim_payload(we_are="friend"),
        _slim_payload(we_are="lover", calling_it="we_are_together"),
        _slim_payload(we_are="ambiguous", said_as="那就当你是我很熟的朋友了"),
    ):
        compiled = compile_slim_consider_payload(partial)
        assert compiled is not None
        private_state = compiled["expression_draft"]["private_turn_state"]
        assert "we_are" not in private_state
        attached = attach_hitchhiked_relationship_residue(compiled)
        assert "relationship_commitment" not in attached["appraisal_draft"]


def test_slim_compile_carries_the_stage_she_actually_declared() -> None:
    for stage in ("ambiguous", "lover"):
        compiled = compile_slim_consider_payload(
            _slim_payload(
                we_are=stage,
                calling_it="maybe_more",
                said_as="那我们现在算什么呢",
            )
        )
        assert compiled is not None
        assert compiled["expression_draft"]["private_turn_state"]["we_are"] == stage
        attached = attach_hitchhiked_relationship_residue(compiled)
        assert attached["appraisal_draft"]["relationship_commitment"][
            "target_stage"
        ] == stage


def test_keep_impression_false_does_not_keep_the_impression_flag() -> None:
    kept = compile_slim_consider_payload(_slim_payload(keep_impression=True))
    dropped = compile_slim_consider_payload(_slim_payload(keep_impression=False))
    assert kept is not None
    assert dropped is not None
    assert kept["expression_draft"]["private_turn_state"]["keep_impression"] is True
    assert dropped["expression_draft"]["private_turn_state"]["keep_impression"] is False


def test_reply_only_envelope_carries_residue_into_appraisal() -> None:
    envelope = compile_slim_interior_envelope(
        _slim_payload(about_us="他说那句话我心里一动", why_us="不像随口敷衍"),
        reply_only=True,
    )
    assert envelope is not None
    assert envelope["appraisal_draft"]["relationship_signal"]["signal_code"] == (
        "他说那句话我心里一动"
    )
    assert envelope["appraisal_draft"]["relationship_signal"]["suggested_deltas"] == {
        "trust_bp": 0,
        "closeness_bp": 0,
        "respect_bp": 0,
        "reliability_bp": 0,
        "mutuality_bp": 0,
        "repair_confidence_bp": 0,
    }
    head = envelope["events"][0]
    assert isinstance(head, dict)
    state = head["private_turn_state"]
    assert isinstance(state, dict)
    assert state["about_us"] == "他说那句话我心里一动"
    assert state["why_us"] == "不像随口敷衍"


def test_authored_us_deltas_reach_the_signal_on_the_production_cheap_path() -> None:
    """Prose alone never moves scores; her own numbers do, on reply_only."""

    envelope = compile_slim_interior_envelope(
        _slim_payload(
            about_us="今晚这段话让我们不太一样了",
            why_us="他记住了我随口说的事",
            us_deltas={"closeness_bp": 400, "trust_bp": 250},
        ),
        reply_only=True,
    )
    assert envelope is not None
    assert envelope["appraisal_draft"]["relationship_signal"]["suggested_deltas"] == {
        "trust_bp": 250,
        "closeness_bp": 400,
        "respect_bp": 0,
        "reliability_bp": 0,
        "mutuality_bp": 0,
        "repair_confidence_bp": 0,
    }


def test_authored_us_deltas_can_be_negative_when_he_cost_her_something() -> None:
    combined = _parse_combined(
        json.dumps(
            _slim_payload(
                about_us="他这么说让我往后退了一点",
                why_us="像是根本没在听",
                us_deltas={"trust_bp": -300, "closeness_bp": -150},
            ),
            ensure_ascii=False,
        )
    )
    deltas = combined["appraisal_draft"]["relationship_signal"]["suggested_deltas"]
    assert deltas["trust_bp"] == -300
    assert deltas["closeness_bp"] == -150


def test_malformed_us_deltas_move_nothing_rather_than_guessing() -> None:
    for broken in ({"closeness_bp": "400"}, {"unknown_bp": 300}, {}, {"trust_bp": True}):
        envelope = compile_slim_interior_envelope(
            _slim_payload(
                about_us="他说那句话我心里一动",
                why_us="不像随口敷衍",
                us_deltas=broken,
            ),
            reply_only=True,
        )
        assert envelope is not None
        assert not any(
            envelope["appraisal_draft"]["relationship_signal"][
                "suggested_deltas"
            ].values()
        )


def test_production_slim_author_lifts_prose_residue_without_score_deltas() -> None:
    combined = _parse_combined(
        json.dumps(
            _slim_payload(about_us="他说那句话我心里一动", why_us="不像随口敷衍"),
            ensure_ascii=False,
        )
    )
    signal = combined["appraisal_draft"]["relationship_signal"]
    assert signal == {
        "signal_code": "他说那句话我心里一动",
        "rationale_code": "不像随口敷衍",
        "confidence_bp": 5000,
        "persistence": "durable",
        "suggested_deltas": {
            "trust_bp": 0,
            "closeness_bp": 0,
            "respect_bp": 0,
            "reliability_bp": 0,
            "mutuality_bp": 0,
            "repair_confidence_bp": 0,
        },
    }


def test_compact_gate_reply_only_carries_relationship_residue() -> None:
    expanded = _expand_compact_gate_payload(
        {
            "result_kind": "reply_only",
            "payload_json": json.dumps(
                _slim_payload(about_us="他说那句话我心里一动", why_us="不像随口敷衍"),
                ensure_ascii=False,
            ),
        }
    )
    assert expanded["result_kind"] == "reply_only"
    assert expanded["appraisal_draft"]["relationship_signal"]["signal_code"] == (
        "他说那句话我心里一动"
    )
    head = expanded["events"][0]
    assert isinstance(head, dict)
    combined = _parse_combined(
        json.dumps(
            {
                "appraisal_draft": expanded["appraisal_draft"],
                "expression_draft": {
                    "private_turn_state": head["private_turn_state"],
                    "beats": [head["beat"]],
                },
            },
            ensure_ascii=False,
        )
    )
    assert combined["appraisal_draft"]["relationship_signal"]["signal_code"] == (
        "他说那句话我心里一动"
    )
    assert combined["appraisal_draft"]["relationship_signal"]["suggested_deltas"] == {
        "trust_bp": 0,
        "closeness_bp": 0,
        "respect_bp": 0,
        "reliability_bp": 0,
        "mutuality_bp": 0,
        "repair_confidence_bp": 0,
    }


def test_slim_ordinary_commitment_hitchhikes_when_she_writes_it() -> None:
    combined = _parse_combined(
        json.dumps(
            _slim_payload(
                we_are="friend",
                calling_it="we_are_close_now",
                said_as="那就当你是我很熟的朋友了",
            ),
            ensure_ascii=False,
        )
    )
    assert combined["appraisal_draft"]["relationship_commitment"] == {
        "target_stage": "friend",
        "commitment_code": "we_are_close_now",
        "persistence": "durable",
        "visible_text_span": "那就当你是我很熟的朋友了",
    }


def test_slim_commitment_materializes_without_inventing_a_romantic_stage() -> None:
    combined = _parse_combined(
        json.dumps(
            _slim_payload(
                we_are="friend",
                calling_it="we_are_close_now",
                said_as="那就当你是我很熟的朋友了",
            ),
            ensure_ascii=False,
        )
    )
    request = ModelInput(
        call_id="call:s23-commit",
        attempt_id="attempt:s23-commit",
        route=ModelRoute(tier="flash", reason_code="test", router_version="test.1"),
        capsule_id="a" * 64,
        trigger_ref="event:observation:relationship",
        evaluated_world_revision=3,
        model_content_json=json.dumps(
            {
                "logical_time": "2026-08-16T12:00:00+00:00",
                "slices": {
                    "relationship_slice": {
                        "availability": "available",
                        "items": [
                            {
                                "item_ref": "relationship:user:primary",
                                "value": {
                                    "subject_ref": "user:primary",
                                    "stage": "stranger",
                                },
                            }
                        ],
                    }
                },
            },
            separators=(",", ":"),
        ),
        trigger_message=TriggerMessage(
            event_ref="event:observation:relationship",
            event_payload_hash="sha256:" + "b" * 64,
            observation_ref="observation:relationship",
            source_world_revision=3,
            actor="user:primary",
            channel="qq:c2c",
            reply_target="qq:user:primary",
            text="我今天还是特地来和你说一声。",
        ),
    )
    proposal = DecisionProposal.model_validate_json(
        json.dumps(
            _proposal_from_draft(
                raw=json.dumps(combined["appraisal_draft"], ensure_ascii=False),
                request=request,
            )
        )
    )
    change = next(
        item for item in proposal.proposed_changes if item.kind == "relationship_commitment"
    )
    payload = change.payload.value()
    assert payload["target_stage"] == "friend"
    assert payload["visible_text_span"] == "那就当你是我很熟的朋友了"
    assert payload["subject_ref"] == "user:primary"
    assert "ambiguous" not in str(payload)
    assert "lover" not in str(payload)


def test_full_appraisal_signal_still_keeps_authored_deltas() -> None:
    request = ModelInput(
        call_id="call:s23-full",
        attempt_id="attempt:s23-full",
        route=ModelRoute(tier="flash", reason_code="test", router_version="test.1"),
        capsule_id="a" * 64,
        trigger_ref="event:observation:relationship",
        evaluated_world_revision=3,
        model_content_json='{"logical_time":"2026-08-16T12:00:00+00:00"}',
        trigger_message=TriggerMessage(
            event_ref="event:observation:relationship",
            event_payload_hash="sha256:" + "b" * 64,
            observation_ref="observation:relationship",
            source_world_revision=3,
            actor="user:primary",
            channel="qq:c2c",
            reply_target="qq:user:primary",
            text="我今天还是特地来和你说一声。",
        ),
    )
    proposal = DecisionProposal.model_validate_json(
        json.dumps(
            _proposal_from_draft(
                raw=json.dumps(
                    {
                        "appraise": False,
                        "brief_rationale": "这句话不需要另开情绪，但她确实重新理解了彼此。",
                        "behavior_tendency": "自然接住",
                        "stance": "更愿意靠近",
                        "display_strategy": "不刻意宣告",
                        "confidence": 7300,
                        "relationship_signal": {
                            "signal_code": "她把这次持续出现理解成更可靠的互相惦记",
                            "confidence_bp": 7300,
                            "persistence": "durable",
                            "rationale_code": "这不是一次性的礼貌",
                            "suggested_deltas": {
                                "trust_bp": 120,
                                "closeness_bp": 180,
                                "respect_bp": 40,
                                "reliability_bp": 130,
                                "mutuality_bp": 160,
                                "repair_confidence_bp": 20,
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                request=request,
            )
        )
    )
    shape = inspect_unified_inbound_decision(
        proposal.model_copy(update={"timing_choice": "silent"})
    )
    assert shape.relationship is not None
    assert shape.relationship.payload.value()["suggested_deltas"]["closeness_bp"] == 180


@pytest.mark.asyncio
async def test_slim_prose_residue_lands_and_the_next_snapshot_can_read_it() -> None:
    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD_ID, accepted_batch_issuer=issuer)
    prepare_claimed_interaction(ledger)
    source_event = ledger.lookup_event_commit("message-event:1")[0]
    head = ledger.project()
    combined = _parse_combined(
        json.dumps(
            _slim_payload(about_us="他说那句话我心里一动", why_us="不像随口敷衍"),
            ensure_ascii=False,
        )
    )
    proposal = DecisionProposal.model_validate_json(
        json.dumps(
            _proposal_from_draft(
                raw=json.dumps(combined["appraisal_draft"], ensure_ascii=False),
                request=_slim_request(
                    source_event=source_event,
                    evaluated_world_revision=head.world_revision,
                ),
            )
        )
    ).model_copy(update={"timing_choice": "silent"})
    base = _result()
    result = DeliberationResult(
        result_id="deliberation:"
        + _digest(
            {
                "capsule_id": base.capsule_id,
                "proposal_hash": proposal.proposal_hash,
                "attempt_audits": [base.audit.model_dump(mode="json")],
            }
        ),
        capsule_id=base.capsule_id,
        proposal=proposal,
        audit=base.audit,
        attempt_audits=(base.audit,),
    )
    head = ledger.project()
    recorded = ProposalAuditRecorder(ledger=ledger).record(
        result,
        ProposalAuditContext(
            world_id=WORLD_ID,
            trigger_ref=source_event.event_id,
            logical_time=NOW,
            created_at=NOW,
            actor="agent:companion",
            source="test:s23-slim",
            trace_id="trace:s23-slim",
            causation_id=source_event.event_id,
            correlation_id="correlation:s23-slim",
            evaluated_world_revision=head.world_revision,
            expected_commit_world_revision=head.world_revision,
            expected_deliberation_revision=head.deliberation_revision,
            expected_ledger_sequence=head.ledger_sequence,
        ),
    )
    worker = InboundRelationshipSignalWorker(
        ledger=ledger,
        compiler=RelationshipProposalCompiler(ledger=ledger),
        acceptance=RelationshipAcceptanceRuntime(ledger=ledger, batch_issuer=issuer),
        owner_id="worker:character-interior-relationship",
    )
    accepted = await worker.process(
        world_id=WORLD_ID,
        audit_cursor=recorded.cursor,
        current_cursor=ProjectionCursor(
            world_revision=ledger.project().world_revision,
            deliberation_revision=ledger.project().deliberation_revision,
            ledger_sequence=ledger.project().ledger_sequence,
        ),
        proposal_id=proposal.proposal_id,
        source_event=source_event,
    )

    assert accepted.status == "accepted"
    signal = ledger.project().relationship_signals[0]
    assert signal.signal_code == "他说那句话我心里一动"
    assert signal.rationale_code == "不像随口敷衍"
    assert signal.suggested_deltas.model_dump() == {
        "trust_bp": 0,
        "closeness_bp": 0,
        "respect_bp": 0,
        "reliability_bp": 0,
        "mutuality_bp": 0,
        "repair_confidence_bp": 0,
    }
    assert ledger.project().relationship_adjustments == ()
    context = _relationship_context()
    context["slices"]["relationship_slice"]["items"][0]["value"]["subject_ref"] = (
        "user:test"
    )
    snapshot = compile_inner_life_snapshot(
        install_relationship_authored_residues(context, ledger.project())
    ).model_view()
    relationship = snapshot["materials"]["relationship"][0]
    assert relationship["recent_authored_signals"] == [
        {
            "signal_code": "他说那句话我心里一动",
            "rationale_code": "不像随口敷衍",
            "confidence_bp": 5000,
        }
    ]
    assert "suggested_deltas" not in str(relationship)
    assert "ambiguous" not in str(relationship)
    assert "lover" not in str(relationship)


@pytest.mark.asyncio
async def test_omitted_slim_residue_does_not_open_relationship_work() -> None:
    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD_ID, accepted_batch_issuer=issuer)
    prepare_claimed_interaction(ledger)
    source_event = ledger.lookup_event_commit("message-event:1")[0]
    head = ledger.project()
    combined = _parse_combined(json.dumps(_slim_payload(), ensure_ascii=False))
    proposal = DecisionProposal.model_validate_json(
        json.dumps(
            _proposal_from_draft(
                raw=json.dumps(combined["appraisal_draft"], ensure_ascii=False),
                request=_slim_request(
                    source_event=source_event,
                    evaluated_world_revision=head.world_revision,
                ),
            )
        )
    ).model_copy(update={"timing_choice": "silent"})
    base = _result()
    result = DeliberationResult(
        result_id="deliberation:"
        + _digest(
            {
                "capsule_id": base.capsule_id,
                "proposal_hash": proposal.proposal_hash,
                "attempt_audits": [base.audit.model_dump(mode="json")],
            }
        ),
        capsule_id=base.capsule_id,
        proposal=proposal,
        audit=base.audit,
        attempt_audits=(base.audit,),
    )
    head = ledger.project()
    recorded = ProposalAuditRecorder(ledger=ledger).record(
        result,
        ProposalAuditContext(
            world_id=WORLD_ID,
            trigger_ref=source_event.event_id,
            logical_time=NOW,
            created_at=NOW,
            actor="agent:companion",
            source="test:s23-slim-omit",
            trace_id="trace:s23-slim-omit",
            causation_id=source_event.event_id,
            correlation_id="correlation:s23-slim-omit",
            evaluated_world_revision=head.world_revision,
            expected_commit_world_revision=head.world_revision,
            expected_deliberation_revision=head.deliberation_revision,
            expected_ledger_sequence=head.ledger_sequence,
        ),
    )
    worker = InboundRelationshipSignalWorker(
        ledger=ledger,
        compiler=RelationshipProposalCompiler(ledger=ledger),
        acceptance=RelationshipAcceptanceRuntime(ledger=ledger, batch_issuer=issuer),
        owner_id="worker:character-interior-relationship",
    )
    result = await worker.process(
        world_id=WORLD_ID,
        audit_cursor=recorded.cursor,
        current_cursor=ProjectionCursor(
            world_revision=ledger.project().world_revision,
            deliberation_revision=ledger.project().deliberation_revision,
            ledger_sequence=ledger.project().ledger_sequence,
        ),
        proposal_id=proposal.proposal_id,
        source_event=source_event,
    )

    assert result.status == "no_change"
    assert ledger.project().relationship_signals == ()
    snapshot = compile_inner_life_snapshot(_relationship_context()).model_view()
    relationship = snapshot["materials"]["relationship"][0]
    assert "recent_authored_signals" not in relationship
