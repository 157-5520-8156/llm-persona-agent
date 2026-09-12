"""Materialization must run inside the verified-counterpart binding.

The host itself synthesizes a ``relationship_signal`` from her authored
``about_us`` / ``why_us`` while it binds the proactive wire
(``bind_proactive_expression_wire`` -> ``hitchhike_proactive_authored_decisions``
-> ``attach_hitchhiked_relationship_residue``).  Binding that signal to a
``subject_ref`` needs the counterpart the host already holds and trusts, read
from the ``use_verified_proactive_counterpart`` contextvar.

``consider`` used to be the only code inside that manager, so the identical
proposal raised ``AppraisalDraft relationship_signal requires a verified
counterpart`` one step later, during materialization: her decision was already
terminal, yet nothing could ever be delivered.  These two tests pin both halves
of that seam without any provider call.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from companion_daemon.world_v2.character_interior import inbound_appraisal_wire
from companion_daemon.world_v2.character_interior.contracts import (
    InnerDecision,
    _InteriorAuthorLineage,
)
from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute
from companion_daemon.world_v2.expression_draft import TEXT_ONLY_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.proactive_action import (
    _CharacterInteriorProactiveTransport,
)
from companion_daemon.world_v2.proposal_envelope import (
    ProposalEvidenceRef,
    validate_proposal_envelope,
)
from companion_daemon.world_v2.schemas import ProjectionCursor


NOW = datetime(2026, 8, 4, 18, 0, tzinfo=UTC)
SOURCE = "event:clock:ambient:1"
COUNTERPART = "user:longitudinal-audit"
CURSOR = ProjectionCursor(world_revision=9, deliberation_revision=0, ledger_sequence=12)
SNAPSHOT_HASH = "e" * 64
SNAPSHOT_ID = f"inner-life-snapshot:sha256:{SNAPSHOT_HASH}"
SUMMARY = "想把今天这些小事自然讲给他听。"

# The one reading the host synthesizes the signal from, verbatim in shape from
# the captured R3 proactive provider output (APPENDIX-C §2.4).
ABOUT_US = "他早上随口一问，我答得短；可这一整天我攒下的都是想讲给他的小事"
WHY_US = "因为这份想讲给他听的心情，比早上那句短回复诚实"

_REAL_USE_VERIFIED_COUNTERPART = inbound_appraisal_wire.use_verified_proactive_counterpart

# Verbatim ``result.decision.payload`` of the captured R3 proactive provider
# response ``model-input:23e738c5e3dc40e69d25a4a0bcb53442`` (row 5 of
# ``output/release-readiness/APPENDIX-C-proactive-failure.md`` §2.4): a visible
# message, ``"appraisal_draft": null``, and her authored ``about_us``/``why_us``
# plus ``us_deltas``.  No ledger is built here, so its single ``current_world``
# claim still loses its lane exactly as §5.4 requires.
_R3_ROW5_PAYLOAD = json.loads(
    """
    {"timing_choice":"now","turn_posture":"interject","cadence":"conversational",
     "beats":[{"modality":"text","text":"今天下午说还没想好，结果被洗衣服这点事绊了两回","reaction_id":null,"sticker_id":null}],
     "delay_seconds":null,"expires_after_seconds":null,
     "stance":"松散自然，像把攒了一天的话顺手倒出来，不催他回",
     "brief_rationale":"早上回得短，是因为当时没攒够；现在一天的琐碎攒齐了，讲给他听才诚实",
     "impulse_summary":"想把今天这些小事自然讲给他听","confidence":6600,"variation_profile":null,
     "response_expectation":{"hoped_response":"他接一句，随便聊两句今天","pressure_bp":2200,"importance_bp":4200,"wait_seconds":1800,"expires_after_seconds":7200},
     "response_expectation_assessment":null,"revisit":null,
     "world_claims":[{"claim_text":"临时洗衣服务点贴出通知，楼层预约延长三十分钟，维修师傅排在第二天早上，今晚能用的机器是维修签字前的最后几台","scope":"current_world","source_refs":["s0","s1"]}],
     "media_request":"none","media_source_refs":[],"waiting_for":null,"wait":null,
     "pressure_bp":2200,"importance_bp":4200,
     "about_us":"他早上随口一问，我答得短；可这一整天我攒下的都是想讲给他的小事——原来安静里也有我自己那份选择",
     "why_us":"因为这份想讲给他听的心情，比早上那句短回复诚实",
     "us_deltas":{"trust_bp":0,"closeness_bp":150,"respect_bp":0,"reliability_bp":0,"mutuality_bp":100,"repair_confidence_bp":0},
     "we_are":null,"calling_it":null,"said_as":null,"keep_impression":true,
     "stuck_with_me":"早上那句\\"下午还没想好\\"不是没话，是当时还没攒够；今天这些琐碎攒齐了，我想讲给他听——这份心情比那句短回复诚实。",
     "noticed":"他那句\\"今天你打算干嘛\\"我到现在还没完全读透，可我已经不等读透就想开口了",
     "declared_display":null,"appraisal_draft":null}
    """
)


def _request() -> ModelInput:
    return ModelInput(
        call_id="call:proactive:counterpart-binding:1",
        attempt_id="attempt:proactive:counterpart-binding:1",
        route=ModelRoute(tier="flash", reason_code="test", router_version="test.1"),
        capsule_id="a" * 64,
        trigger_ref=SOURCE,
        evaluated_world_revision=CURSOR.world_revision,
        evaluated_deliberation_revision=CURSOR.deliberation_revision,
        evaluated_ledger_sequence=CURSOR.ledger_sequence,
        trigger_evidence=(
            ProposalEvidenceRef(
                ref_id=SOURCE,
                evidence_kind="committed_world_event",
                source_world_revision=CURSOR.world_revision,
                immutable_hash="sha256:" + "b" * 64,
            ),
        ),
        model_content_json=json.dumps(
            {
                "logical_time": NOW.isoformat(),
                "slices": {
                    "advisories": {
                        "items": [
                            {
                                "value": {
                                    "kind": "proactive_opportunity",
                                    "candidate_refs": ["ambient_presence:epoch:1"],
                                    "source_refs": [SOURCE],
                                    "candidates": [{"value": "ambient context"}],
                                }
                            }
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
    )


def _authored_payload() -> dict[str, object]:
    """Her decided payload: a visible message plus the relationship residue."""

    return {
        "timing_choice": "now",
        "turn_posture": "continue",
        "cadence": "conversational",
        "beats": [
            {
                "modality": "text",
                "text": "今天下午说还没想好，结果被洗衣服这点事绊了两回",
            }
        ],
        "delay_seconds": None,
        "expires_after_seconds": None,
        "stance": "随意的、带点自嘲的分享",
        "brief_rationale": "早上回得短，现在一天的琐碎攒齐了",
        "impulse_summary": "想把今天这些小事自然讲给他听",
        "confidence": 6600,
        "world_claims": [],
        "media_request": "none",
        "media_source_refs": [],
        "appraisal_draft": None,
        "about_us": ABOUT_US,
        "why_us": WHY_US,
    }


class _DecidedInterior:
    """A decided Interior double; records the binding it saw while deciding."""

    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self._payload = _authored_payload() if payload is None else payload
        self.calls = 0
        self.bindings_during_consider: list[str | None] = []

    async def consider(self, opportunity):  # type: ignore[no-untyped-def]
        # The old host bound the counterpart around exactly this call.
        with _REAL_USE_VERIFIED_COUNTERPART(COUNTERPART):
            self.calls += 1
            self.bindings_during_consider.append(
                inbound_appraisal_wire._VERIFIED_PROACTIVE_COUNTERPART.get()
            )
            payload = dict(self._payload)
            payload["contract"] = "character-interior-proactive-contact-decision.1"
            author = _InteriorAuthorLineage(
                model_id="character-model",
                model_version="character-model-2026-08",
                model_call_id="model-call:proactive:counterpart-binding:1",
                request_hash="sha256:" + "c" * 64,
                response_hash="sha256:" + "d" * 64,
                attempt_ordinal=0,
            )
            private_self = {"summary": SUMMARY, "attended_source_refs": (SOURCE,)}
            return InnerDecision(
                inner_turn_id=opportunity.inner_turn_ref,
                opportunity_ref=opportunity.opportunity_ref,
                actor_ref=opportunity.actor_ref,
                cursor=opportunity.cursor,
                snapshot_id=SNAPSHOT_ID,
                snapshot_hash=SNAPSHOT_HASH,
                status="decided",
                summary=SUMMARY,
                attended_source_refs=(SOURCE,),
                instant_private_self=private_self,
                private_self_lineage={
                    "relation": "single_pass",
                    "initial_private_self": private_self,
                    "initial_snapshot_id": SNAPSHOT_ID,
                    "initial_snapshot_hash": SNAPSHOT_HASH,
                    "initial_author_lineage": author,
                    "final_private_self": private_self,
                    "final_snapshot_id": SNAPSHOT_ID,
                    "final_snapshot_hash": SNAPSHOT_HASH,
                    "final_author_lineage": author,
                },
                decision={
                    "contract": "character-interior-purpose-decision.1",
                    "purpose": "proactive_contact",
                    "payload": payload,
                },
                author_lineage=author,
            )


def _transport(
    interior: _DecidedInterior,
    *,
    counterpart: str = COUNTERPART,
) -> _CharacterInteriorProactiveTransport:
    return _CharacterInteriorProactiveTransport(
        character_interior=interior,  # type: ignore[arg-type]
        world_id="world:test",
        actor_ref="character:zhizhi",
        target="user:primary",
        expression_capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES,
        counterpart_actor_ref=counterpart,
    )


def _signals(proposal):  # type: ignore[no-untyped-def]
    return tuple(
        change for change in proposal.proposed_changes if change.kind == "relationship_signal"
    )


@pytest.mark.asyncio
async def test_propose_materializes_the_host_synthesized_relationship_signal() -> None:
    """The fix: her authored about_us/why_us survives to a deliverable proposal."""

    interior = _DecidedInterior()

    output = await _transport(interior).propose(_request())

    proposal = validate_proposal_envelope(output.raw_proposal)
    signals = _signals(proposal)
    assert len(signals) == 1, [change.kind for change in proposal.proposed_changes]
    signal = signals[0].payload.value()
    assert signal["subject_ref"] == COUNTERPART
    assert signal["signal_code"] == ABOUT_US
    assert signal["rationale_code"] == WHY_US
    # Her expression is unchanged: the same now-timing, one visible beat.
    assert proposal.timing_choice == "now"
    assert proposal.action_intents
    assert all(intent.kind == "proactive_message" for intent in proposal.action_intents)
    assert output.model_id == "character-model"
    assert interior.calls == 1


@pytest.mark.parametrize("payload", [_authored_payload(), _R3_ROW5_PAYLOAD])
@pytest.mark.asyncio
async def test_materialization_outside_the_binding_raises_the_counterpart_error(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    """Old scope: she decides (bound) and the same proposal dies unbound."""

    def _unbound(actor_ref: str | None):
        del actor_ref
        return _NoBinding()

    monkeypatch.setattr(
        inbound_appraisal_wire, "use_verified_proactive_counterpart", _unbound
    )
    interior = _DecidedInterior(payload)

    with pytest.raises(
        ValueError, match="relationship_signal requires a verified counterpart"
    ):
        await _transport(interior).propose(_request())

    # Faithful to the old host: the counterpart *was* bound while she decided,
    # so the failure is materialization's scope and nothing earlier.
    assert interior.calls == 1
    assert interior.bindings_during_consider == [COUNTERPART]
    assert inbound_appraisal_wire._VERIFIED_PROACTIVE_COUNTERPART.get() is None


@pytest.mark.asyncio
async def test_the_captured_r3_payload_reaches_materialization_with_the_binding() -> None:
    """The real R3 row-5 bytes now materialize instead of dying unbound."""

    proposal = validate_proposal_envelope(
        (await _transport(_DecidedInterior(_R3_ROW5_PAYLOAD)).propose(_request())).raw_proposal
    )

    signals = _signals(proposal)
    assert len(signals) == 1, [change.kind for change in proposal.proposed_changes]
    value = signals[0].payload.value()
    assert value["subject_ref"] == COUNTERPART
    # Her authored reading and her own deltas, untouched by the host.
    assert value["signal_code"] == _R3_ROW5_PAYLOAD["about_us"]
    assert value["rationale_code"] == _R3_ROW5_PAYLOAD["why_us"]
    assert value["suggested_deltas"] == {
        field: _R3_ROW5_PAYLOAD["us_deltas"][field]
        for field in sorted(_R3_ROW5_PAYLOAD["us_deltas"])
    }
    # §5.4 stays closed: this request pins no ledger closure for her
    # ``current_world`` claim, so the claim lane rejects and no Action is
    # authorized.  The binding above is not a claim authority.
    assert proposal.proactive_grounding_outcome == "rejected"
    assert proposal.action_intents == ()


@pytest.mark.asyncio
async def test_the_binding_only_fills_the_signal_subject_ref() -> None:
    """Different verified counterparts move the subject, and nothing else."""

    first = validate_proposal_envelope(
        (await _transport(_DecidedInterior()).propose(_request())).raw_proposal
    )
    second = validate_proposal_envelope(
        (
            await _transport(
                _DecidedInterior(), counterpart="user:other-verified-counterpart"
            ).propose(_request())
        ).raw_proposal
    )

    def _deliverable_surface(proposal):  # type: ignore[no-untyped-def]
        return (
            proposal.timing_choice,
            proposal.turn_posture,
            proposal.behavior_tendency,
            proposal.display_strategy,
            proposal.stance,
            proposal.affect_decision,
            proposal.affect_tendencies,
            tuple(item.summary for item in proposal.appraisals),
            proposal.proactive_opportunity_decision,
            proposal.private_turn_state.model_dump(mode="json"),
            tuple(
                (change.kind, json.dumps(change.payload.value(), sort_keys=True))
                for change in proposal.proposed_changes
                if change.kind == "expression_plan_transition"
            ),
            tuple(
                (intent.kind, intent.payload_hash) for intent in proposal.action_intents
            ),
        )

    # Her beats, the expression plan and the Actions it authorizes are identical.
    assert _deliverable_surface(first) == _deliverable_surface(second)

    first_signal, second_signal = _signals(first)[0], _signals(second)[0]
    first_value = first_signal.payload.value()
    second_value = second_signal.payload.value()
    assert first_value["subject_ref"] == COUNTERPART
    assert second_value["subject_ref"] == "user:other-verified-counterpart"
    assert {
        key: value for key, value in first_value.items() if key != "subject_ref"
    } == {key: value for key, value in second_value.items() if key != "subject_ref"}

    # The appraisal draft identity hashes the bound counterpart, so its appraisal
    # / change / signal ids follow the subject.  That identity is the only other
    # thing the binding reaches.
    def _appraisal_identity(proposal):  # type: ignore[no-untyped-def]
        return tuple(
            (
                change.kind,
                change.change_id.removeprefix("change:appraisal-draft:"),
                change.target_id,
            )
            for change in proposal.proposed_changes
            if change.kind in {"appraisal_transition", "relationship_signal"}
        )

    assert _appraisal_identity(first) != _appraisal_identity(second)
    assert first_signal.target_id == (
        "signal:relationship-appraisal-draft:" + first_signal.change_id.split(":")[-1]
    )
    assert second_signal.target_id.endswith(second_signal.change_id.split(":")[-1])


class _NoBinding:
    """A manager that binds nothing: the old transport-level scope."""

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False
