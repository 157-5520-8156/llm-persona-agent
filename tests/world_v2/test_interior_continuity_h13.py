from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
    InboundToolContracts,
    _expand_compact_gate_payload,
)
from companion_daemon.world_v2.character_interior.inbound_wire import (
    _is_lossless_minimal_reply_draft,
    _stream_first_expression,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.expression_draft import (
    ExpressionDraft,
    QQ_NAPCAT_EXPRESSION_CAPABILITIES,
)
from companion_daemon.world_v2.present_prompt import (
    SLIM_REPLY_ONLY_MAX_TEXT_BEATS,
    compile_slim_consider_payload,
    compile_slim_interior_envelope,
    json_schema_g4_metrics,
)
from test_expectation_feelings import (
    HOPED,
    NOW as EXPECTATION_NOW,
    _expectation,
    _fake_projection,
)
from test_social_initiative import _compiler_fixture


def _slim_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "messages": ["那件事后来怎么样了"],
        "meaning_of_this": "他还没有接上那件事",
        "my_state": "我还挂着他没回的那句",
        "stuck_with_me": "我说完就在等他开口",
        "wants": "想听他怎么说",
        "photo": False,
    }
    payload.update(updates)
    if payload.get("waiting_for") and payload.get("wait") is not None:
        payload.setdefault("pressure_bp", 5_000)
        payload.setdefault("importance_bp", 5_000)
    return payload


def test_slim_waiting_for_becomes_a_declared_expectation() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(waiting_for="他回来把那件事说完", wait=45)
    )

    assert compiled is not None
    expectation = compiled["expression_draft"]["response_expectation"]
    assert expectation["hoped_response"] == "他回来把那件事说完"
    assert expectation["pressure_bp"] == 5_000
    assert expectation["importance_bp"] == 5_000
    assert expectation["wait_seconds"] == 45
    assert expectation["expires_after_seconds"] > expectation["wait_seconds"]
    envelope = compile_slim_interior_envelope(
        _slim_payload(waiting_for="他回来把那件事说完", wait=45),
        reply_only=True,
    )
    assert envelope is not None
    assert envelope["events"][0]["response_expectation"]["hoped_response"] == (
        "他回来把那件事说完"
    )


def test_slim_does_not_infer_expectation_from_a_question() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(messages=["那件事后来怎么样了？"])
    )

    assert compiled is not None
    assert compiled["expression_draft"].get("response_expectation") is None


def test_slim_wait_is_the_seconds_she_wrote() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(waiting_for="想听你怎么看这件事", wait=45)
    )

    assert compiled is not None
    expectation = compiled["expression_draft"]["response_expectation"]
    assert expectation["hoped_response"] == "想听你怎么看这件事"
    assert expectation["wait_seconds"] == 45
    assert expectation["expires_after_seconds"] == 105


def test_slim_wait_parses_the_duration_she_wrote() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(waiting_for="想听你怎么看这件事", wait="2分钟")
    )

    assert compiled is not None
    expectation = compiled["expression_draft"]["response_expectation"]
    assert expectation["wait_seconds"] == 120
    assert expectation["expires_after_seconds"] == 180


def test_slim_wait_soon_is_a_visible_failure() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_WAIT_NOT_A_DURATION

    with pytest.raises(ValueError, match=SLIM_WAIT_NOT_A_DURATION):
        compile_slim_consider_payload(
            _slim_payload(waiting_for="想听你怎么看这件事", wait="soon")
        )


def test_slim_waiting_for_prefix_without_wait_does_not_compile_a_hope() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(waiting_for="短: 想听你怎么看这件事")
    )

    assert compiled is not None
    assert compiled["expression_draft"].get("response_expectation") is None


def test_slim_unknown_wait_is_a_visible_failure() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_WAIT_NOT_A_DURATION

    with pytest.raises(ValueError, match=SLIM_WAIT_NOT_A_DURATION):
        compile_slim_consider_payload(
            _slim_payload(waiting_for="他回来把那件事说完", wait="whenever")
        )


def test_slim_waiting_for_without_wait_does_not_compile_a_hope() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(waiting_for="他回来把那件事说完")
    )

    assert compiled is not None
    assert compiled["expression_draft"].get("response_expectation") is None


def test_slim_wait_without_waiting_for_is_a_visible_failure() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_WAIT_PAIR_INCOMPLETE

    with pytest.raises(ValueError, match=SLIM_WAIT_PAIR_INCOMPLETE) as caught:
        compile_slim_consider_payload(_slim_payload(wait=45))
    assert "这次缺了：waiting_for" in str(caught.value)


def test_slim_declared_wait_below_the_floor_still_wakes_at_thirty_seconds() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(waiting_for="想听你怎么看这件事", wait=10)
    )

    assert compiled is not None
    expectation = compiled["expression_draft"]["response_expectation"]
    assert expectation["wait_seconds"] == 30
    assert expectation["expires_after_seconds"] == 90


def test_slim_max_wait_is_not_an_open_hope() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(waiting_for="想听你怎么看这件事", wait=86_400)
    )

    assert compiled is not None
    expectation = compiled["expression_draft"]["response_expectation"]
    assert expectation["wait_seconds"] == 86_400
    assert expectation["expires_after_seconds"] == 86_460


def test_slim_still_pending_with_now_messages_is_an_in_turn_follow_up() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(
            messages=["那你到底怎么想的"],
            meaning_of_this="他只回了一句哦，这事还没落地",
            my_state="我还想把这件事问清楚",
            how_it_landed="still_pending",
            waiting_for="想听一句认真的",
            wait=45,
        )
    )

    assert compiled is not None
    expression = compiled["expression_draft"]
    assert expression["timing_choice"] == "now"
    assert expression["beats"][0]["text"] == "那你到底怎么想的"
    assert expression["response_expectation_assessment"]["status"] == "still_pending"
    assert expression["response_expectation"]["hoped_response"] == "想听一句认真的"
    assert expression["response_expectation"]["wait_seconds"] == 45


def test_slim_waiting_for_is_clipped_instead_of_failing_the_turn() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(waiting_for="他" * 200, wait=45)
    )

    assert compiled is not None
    assert compiled["expression_draft"]["response_expectation"]["hoped_response"] == (
        "他" * 128
    )


def test_slim_consider_schema_still_fits_g4_with_commitment_triplet() -> None:
    from companion_daemon.world_v2.present_prompt import slim_consider_json_schema

    required, total, depth = json_schema_g4_metrics(slim_consider_json_schema())
    # The slim object is descriptive text material, not the provider tool
    # schema.  The real G4 area cap is asserted on compact_gate_for below,
    # where payload_json is one string property.
    assert required <= 3
    assert total <= 25
    assert depth <= 2
    compact = InboundToolContracts().compact_gate_for(
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=True,
    )
    compact_required, compact_total, compact_depth = json_schema_g4_metrics(
        compact.provider_tools[0]["function"]["parameters"]
    )
    assert compact_required <= 3
    assert compact_total <= 8
    assert compact_depth <= 2


def test_silence_consider_reads_the_pending_expectation_she_declared() -> None:
    from companion_daemon.world_v2.response_expectation_view import (
        attach_pending_expectation_advisory,
    )

    projection = _fake_projection(
        logical_time=EXPECTATION_NOW + timedelta(minutes=10),
        expectation=_expectation(expires_at=EXPECTATION_NOW + timedelta(hours=1)),
    )
    context = {
        "world_id": "world:h13-silence",
        "actor_ref": "character:zhizhi",
        "world_revision": 5,
        "deliberation_revision": 0,
        "ledger_sequence": 1,
        "logical_time": (EXPECTATION_NOW + timedelta(minutes=10)).isoformat(),
        "consumer_scope": "deliberation_internal",
        "viewer_privacy_ceiling": "private",
        "context_compiler_version": "context-capsule-compiler:test",
        "truncation": {},
        "slices": {},
    }

    attached = attach_pending_expectation_advisory(
        context,
        projection,
        anchor_event_ref="event:receipt:invite",
    )
    snapshot = compile_inner_life_snapshot(attached)
    advisories = snapshot.materials.get("advisories")

    assert isinstance(advisories, list)
    assert advisories
    assert advisories[0]["kind"] == "response_expectation"
    assert HOPED in advisories[0]["candidates"][0]["value"]
    assert "he has not spoken" in advisories[0]["candidates"][0]["value"]


def test_slim_how_it_landed_becomes_an_assessment_on_the_same_inbound() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(
            messages=["嗯，我听完了"],
            meaning_of_this="他终于把那件事说清楚了",
            my_state="我听完以后放下心了",
            how_it_landed="fulfilled",
        )
    )

    assert compiled is not None
    assessment = compiled["expression_draft"]["response_expectation_assessment"]
    assert assessment["status"] == "fulfilled"
    assert assessment["reason"] == "我听完以后放下心了"
    envelope = compile_slim_interior_envelope(
        _slim_payload(
            messages=["嗯，我听完了"],
            meaning_of_this="他终于把那件事说清楚了",
            my_state="我听完以后放下心了",
            how_it_landed="fulfilled",
        ),
        reply_only=True,
    )
    assert envelope is not None
    assert envelope["events"][0]["response_expectation_assessment"]["status"] == (
        "fulfilled"
    )
    expanded = _expand_compact_gate_payload(
        {
            "result_kind": "reply_only",
            "payload_json": json.dumps(
                _slim_payload(
                    messages=["嗯，我听完了"],
                    meaning_of_this="他终于把那件事说清楚了",
                    my_state="我听完以后放下心了",
                    how_it_landed="fulfilled",
                ),
                ensure_ascii=False,
            ),
        }
    )
    assert expanded["events"][0]["response_expectation_assessment"]["status"] == (
        "fulfilled"
    )


def test_assessment_bearing_slim_reply_stays_lossless_for_minimal_recovery() -> None:
    draft = ExpressionDraft.model_validate_json(
        json.dumps(
            {
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "他终于把那件事说清楚了",
                    "attended_source_refs": [],
                },
                "timing_choice": "now",
                "cadence": "conversational",
                "beats": [{"modality": "text", "text": "嗯，我听完了"}],
                "stance": "acknowledge_briefly",
                "brief_rationale": "他终于把那件事说清楚了",
                "confidence": 5000,
                "world_claims": [],
                "media_request": "none",
                "media_source_refs": [],
                "response_expectation_assessment": {
                    "status": "fulfilled",
                    "reason": "他终于把那件事说清楚了",
                },
            },
            ensure_ascii=False,
        ),
        strict=True,
    )

    assert _is_lossless_minimal_reply_draft(draft) is True


@pytest.mark.asyncio
async def test_pending_expectation_does_not_mint_a_generic_idle_opportunity() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=True)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **kwargs: SimpleNamespace(
            selected_candidate_ref="delay:3600",
            draw_id="draw:h13-idle",
        )
    )
    action = SimpleNamespace(action_id="action:invite", kind="reply", state="delivered")
    receipt_ref = SimpleNamespace(
        event_id="event:receipt:invite",
        event_type="ExecutionReceiptRecorded",
        world_revision=2,
        logical_time=EXPECTATION_NOW,
    )
    projection.actions = (action,)
    projection.execution_receipts = (
        SimpleNamespace(action_id="action:invite", observed_state="delivered"),
    )
    projection.committed_world_event_refs = (receipt_ref,)
    projection.expression_plan_manifests = (
        SimpleNamespace(
            plan_id="plan:invite",
            acceptance_event_ref="event:acceptance:invite",
            recorded_at_world_revision=1,
            response_expectation=SimpleNamespace(
                source_beat_id="beat:invite",
                hoped_response=HOPED,
                pressure_bp=5_000,
                importance_bp=5_000,
                not_before=EXPECTATION_NOW + timedelta(minutes=1),
                expires_at=projection.logical_time + timedelta(hours=6),
                delivery_requirement="provider_accepted_or_delivered",
            ),
            beats=(SimpleNamespace(beat_id="beat:invite", action=action),),
        ),
    )
    projection.response_expectation_assessments = ()

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is None
    assert committed == []


@pytest.mark.asyncio
async def test_still_pending_assessment_does_not_block_quiet_gap() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=True)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **kwargs: SimpleNamespace(
            selected_candidate_ref="delay:3600",
            draw_id="draw:h13-idle-after-still-pending",
        )
    )
    action = SimpleNamespace(action_id="action:invite", kind="reply", state="delivered")
    receipt_ref = SimpleNamespace(
        event_id="event:receipt:invite",
        event_type="ExecutionReceiptRecorded",
        world_revision=2,
        logical_time=EXPECTATION_NOW,
    )
    projection.actions = (action,)
    projection.execution_receipts = (
        SimpleNamespace(action_id="action:invite", observed_state="delivered"),
    )
    projection.committed_world_event_refs = (receipt_ref,)
    projection.expression_plan_manifests = (
        SimpleNamespace(
            plan_id="plan:invite",
            acceptance_event_ref="event:acceptance:invite",
            recorded_at_world_revision=1,
            response_expectation=SimpleNamespace(
                source_beat_id="beat:invite",
                hoped_response=HOPED,
                pressure_bp=5_000,
                importance_bp=5_000,
                not_before=EXPECTATION_NOW + timedelta(minutes=1),
                expires_at=projection.logical_time + timedelta(hours=6),
                delivery_requirement="provider_accepted_or_delivered",
            ),
            beats=(SimpleNamespace(beat_id="beat:invite", action=action),),
        ),
    )
    projection.response_expectation_assessments = (
        SimpleNamespace(source_plan_id="plan:invite", status="still_pending"),
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "spontaneous_contact"
    assert committed == []


def test_slim_later_compiles_a_delayed_text_reply_only() -> None:
    compiled = compile_slim_consider_payload(_slim_payload(later=45))

    assert compiled is not None
    expression = compiled["expression_draft"]
    assert expression["timing_choice"] == "later"
    assert expression["delay_seconds"] == 45
    assert expression["expires_after_seconds"] > 45
    assert "revisit" not in expression
    envelope = compile_slim_interior_envelope(_slim_payload(later=45), reply_only=True)
    assert envelope is not None
    head = envelope["events"][0]
    assert head["timing_choice"] == "later"
    assert head["delay_seconds"] == 45
    assert head["beat"]["text"] == "那件事后来怎么样了"
    expanded = _expand_compact_gate_payload(
        {
            "result_kind": "reply_only",
            "payload_json": json.dumps(_slim_payload(later=90), ensure_ascii=False),
        }
    )
    assert expanded["events"][0]["timing_choice"] == "later"
    assert expanded["events"][0]["delay_seconds"] == 90


def test_slim_optional_nulls_are_omission_not_a_form_to_fill() -> None:
    from companion_daemon.world_v2.present_prompt import reply_only_slim_shape_specimen

    payload = reply_only_slim_shape_specimen()
    payload["messages"] = ["嗯"]
    payload["meaning_of_this"] = "这只是普通的一句"
    payload["my_state"] = "我没什么特别感觉"
    compiled = compile_slim_consider_payload(payload)

    assert compiled is not None
    expression = compiled["expression_draft"]
    assert expression["timing_choice"] == "now"
    assert expression.get("response_expectation") is None
    assert expression.get("revisit") is None
    assert "we_are" not in expression["private_turn_state"]
    assert compiled["appraisal_draft"]["affect"] == "no_change"


def test_slim_later_null_is_send_now() -> None:
    compiled = compile_slim_consider_payload(_slim_payload(later=None))

    assert compiled is not None
    assert compiled["expression_draft"]["timing_choice"] == "now"
    assert "delay_seconds" not in compiled["expression_draft"]


def test_slim_invalid_later_fails_closed_instead_of_sending_now() -> None:
    from companion_daemon.world_v2.present_prompt import (
        SLIM_LATER_NOT_A_DURATION,
        SLIM_LATER_REQUIRES_TEXT,
    )

    with pytest.raises(ValueError, match=SLIM_LATER_NOT_A_DURATION):
        compile_slim_consider_payload(_slim_payload(later=0))
    with pytest.raises(ValueError, match=SLIM_LATER_NOT_A_DURATION):
        compile_slim_consider_payload(_slim_payload(later=True))
    with pytest.raises(ValueError, match=SLIM_LATER_NOT_A_DURATION):
        compile_slim_consider_payload(_slim_payload(later=10))
    with pytest.raises(ValueError, match=SLIM_LATER_REQUIRES_TEXT):
        compile_slim_consider_payload(_slim_payload(later=45, photo=True))
    with pytest.raises(ValueError, match=SLIM_LATER_REQUIRES_TEXT):
        compile_slim_consider_payload(
            {
                "messages": [],
                "meaning_of_this": "这句话可以晚点处理",
                "my_state": "我现在不想回",
                "later": 45,
            }
        )


def test_slim_invalid_how_it_landed_is_a_visible_failure() -> None:
    from companion_daemon.world_v2.present_prompt import SLIM_HOW_IT_LANDED_INVALID

    compiled = compile_slim_consider_payload(_slim_payload())
    assert compiled is not None
    compiled_null = compile_slim_consider_payload(_slim_payload(how_it_landed=None))
    assert compiled_null is not None
    with pytest.raises(ValueError, match=SLIM_HOW_IT_LANDED_INVALID):
        compile_slim_consider_payload(_slim_payload(how_it_landed="ok"))


def test_slim_photo_true_binds_media_request_on_reply_only_and_full_turn() -> None:
    payload = _slim_payload(photo=True)
    compiled = compile_slim_consider_payload(payload)
    assert compiled is not None
    assert compiled["expression_draft"]["media_request"] == "consider_available_candidate"
    reply_only = compile_slim_interior_envelope(payload, reply_only=True)
    assert reply_only is not None
    assert reply_only["events"][0]["media_request"] == "consider_available_candidate"
    envelope = compile_slim_interior_envelope(payload, reply_only=False)
    assert envelope is not None
    assert envelope["events"][0]["media_request"] == "consider_available_candidate"


def test_slim_empty_messages_are_reply_only_silence() -> None:
    compiled = compile_slim_consider_payload(_slim_payload(messages=[]))
    assert compiled is not None
    assert compiled["expression_draft"]["timing_choice"] == "silent"
    assert compiled["expression_draft"]["beats"] == []
    envelope = compile_slim_interior_envelope(
        {
            "messages": [],
            "meaning_of_this": "这句话现在不用回应",
            "my_state": "我现在不想回",
        },
        reply_only=True,
    )
    assert envelope is not None
    assert envelope["events"][0]["timing_choice"] == "silent"
    assert envelope["events"][0]["beat"] is None
    expanded = _expand_compact_gate_payload(
        {
            "result_kind": "reply_only",
            "payload_json": json.dumps(
                {
                    "messages": [],
                    "meaning_of_this": "这句话现在不用回应",
                    "my_state": "我现在不想回",
                },
                ensure_ascii=False,
            ),
        }
    )
    assert expanded["events"][0]["timing_choice"] == "silent"


def test_slim_now_allows_multiple_text_beats() -> None:
    payload = {
        "messages": ["行", "你去吧"],
        "meaning_of_this": "他要先去处理自己的事",
        "my_state": "我随口应一声",
    }
    envelope = compile_slim_interior_envelope(payload, reply_only=True)
    assert envelope is not None
    head = envelope["events"][0]
    assert head["timing_choice"] == "now"
    assert head["beat"] is None
    assert [item["text"] for item in head["beats"]] == ["行", "你去吧"]
    expanded = _expand_compact_gate_payload(
        {
            "result_kind": "reply_only",
            "payload_json": json.dumps(payload, ensure_ascii=False),
        }
    )
    assert [item["text"] for item in expanded["events"][0]["beats"]] == ["行", "你去吧"]
    first = json.loads(
        _stream_first_expression(
            json.dumps(
                {
                    "result_kind": "reply_only",
                    "payload_json": json.dumps(payload, ensure_ascii=False),
                },
                ensure_ascii=False,
            )
        )
    )
    assert [item["text"] for item in first["expression_draft"]["beats"]] == ["行", "你去吧"]
    four = {
        "messages": ["一", "二", "三", "四"],
        "meaning_of_this": "这件事值得分开说",
        "my_state": "我有四句想连着讲",
    }
    four_envelope = compile_slim_interior_envelope(four, reply_only=True)
    assert four_envelope is not None
    assert len(four_envelope["events"][0]["beats"]) == 4


def test_slim_now_rejects_more_text_beats_than_the_installed_limit() -> None:
    payload = {
        "messages": [f"第{index}句" for index in range(SLIM_REPLY_ONLY_MAX_TEXT_BEATS + 1)],
        "meaning_of_this": "这件事值得展开",
        "my_state": "我想说很多句",
    }
    with pytest.raises(ValueError, match="exceeds text-only capability"):
        compile_slim_interior_envelope(payload, reply_only=True)


def test_slim_later_allows_multiple_text_beats() -> None:
    payload = _slim_payload(later=45)
    payload["messages"] = ["先这条", "再这条"]
    compiled = compile_slim_consider_payload(payload)
    assert compiled is not None
    assert compiled["expression_draft"]["timing_choice"] == "later"
    envelope = compile_slim_interior_envelope(payload, reply_only=True)
    assert envelope is not None
    head = envelope["events"][0]
    assert head["timing_choice"] == "later"
    assert [item["text"] for item in head["beats"]] == ["先这条", "再这条"]


def test_slim_authored_meaning_is_kept_as_her_reading() -> None:
    compiled = compile_slim_consider_payload(_slim_payload())
    assert compiled is not None
    appraisal = compiled["appraisal_draft"]
    assert appraisal["appraise"] is True
    assert appraisal["affect"] == "no_change"
    assert appraisal["meanings"] == [
        {"meaning": "他还没有接上那件事", "confidence": 5000}
    ]
    assert appraisal["attribution"] == "unknown"
    assert "hurt" not in json.dumps(appraisal, ensure_ascii=False)
    envelope = compile_slim_interior_envelope(_slim_payload(), reply_only=True)
    assert envelope is not None
    from companion_daemon.world_v2.character_interior.inbound_appraisal_wire import (
        canonicalize_appraisal_draft_wire,
    )

    canonical = canonicalize_appraisal_draft_wire(envelope["appraisal_draft"])
    assert canonical["appraise"] is True
    assert canonical["meanings"][0]["meaning"] == "他还没有接上那件事"


def test_slim_does_not_invent_a_reading_when_she_omits_meaning() -> None:
    with pytest.raises(ValueError, match="meaning_of_this"):
        compile_slim_consider_payload(
            {
                "messages": ["嗯"],
                "my_state": "我现在没什么特别感觉",
                "stuck_with_me": "他好像没说完",
                "photo": False,
            }
        )
