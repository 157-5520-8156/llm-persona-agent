"""Original public sources and complete decisions bind one review record.

These are receipt mechanics and MockTransport evidence, not release policy or
real-model semantic qualification. Expected audit bindings are test fixtures.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel, model_provider_request_identity_scope
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.deliberation import TriggerMessage
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.schemas import Observation
from companion_daemon.world_v2.proposal_envelope import CanonicalTypedPayload
from companion_daemon.world_v2.expression_draft import (
    TEXT_ONLY_EXPRESSION_CAPABILITIES,
    materialize_expression_draft,
)
from companion_daemon.world_v2.visible_source_closure_protocol import (
    VisibleSourceClosureWireFailure,
)
from companion_daemon.world_v2.visible_source_composer import (
    VisibleSourceTable,
    compile_visible_source_table,
)
from companion_daemon.world_v2.visible_source_review_receipt import (
    VisibleReviewAuthorBinding,
    VisibleReviewInvocationBinding,
    VisibleSourceReviewReceipt,
    VisibleSourceReviewRejected,
    prepare_visible_source_review,
    record_visible_source_review,
    verify_visible_source_review_receipt,
)
from test_visible_selected_source_context import _sources
from test_visible_source_composer import _request
from test_chat_life_intent_runtime import INTENT, _run_http_journey
from test_completed_activity_context import open_journey_ledger
from test_world_stimulus_life_intent import _http_result


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _candidate(
    case, *, texts=("你取消了周五的报告。", "我想先听你说。"), rationale="完整候选测试。"
):
    return materialize_expression_draft(
        value={
            "timing_choice": "now",
            "stance": "present",
            "brief_rationale": rationale,
            "beats": [{"modality": "text", "text": text} for text in texts],
            "world_claims": [],
        },
        request=case.request,
        capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES,
    )


def _prepare(case, *, candidate=None, table=None, aliases=None):
    return prepare_visible_source_review(
        candidate=candidate or _candidate(case),
        source_table=table
        or compile_visible_source_table(request=case.request, capsule=case.capsule),
        source_ref_aliases={"S1": case.request.trigger_ref} if aliases is None else aliases,
    )


def _raw(prepared, *, verdict="closed", subject="counterpart"):
    value = prepared.as_dict()
    rows = VisibleSourceTable(payload_json=value["source_table_json"]).source_references()
    report = next(row for row in rows if row["kind"] == "current_counterpart_report")
    return _json(
        {
            "contract": "visible-beat-source-verdict.1",
            "decisions": [
                {
                    "beat_index": 0,
                    "verdict": verdict,
                    "semantic_role": "external_proposition",
                    "subject_role": subject,
                    "source_ref_indexes": [report["source_ref_index"]]
                    if verdict == "closed"
                    else [],
                },
                {
                    "beat_index": 1,
                    "verdict": "source_free",
                    "semantic_role": "commitment",
                    "subject_role": "companion",
                    "source_ref_indexes": [],
                },
            ],
        }
    )


def _bindings(prepared, raw, *, call="review:fixture"):
    value = prepared.as_dict()
    author = VisibleReviewAuthorBinding(
        model_call_id="author:fixture",
        request_hash="a" * 64,
        proposal_material_hash=_hash(value["candidate_json"]),
    )
    review = VisibleReviewInvocationBinding(
        parent_model_call_id=author.model_call_id,
        model_call_id=call,
        model_id="fixture-reviewer",
        model_version="fixture.1",
        request_hash=value["request_hash"],
        response_hash=_hash(raw),
    )
    return author, review


def _record(prepared, *, raw=None, call="review:fixture"):
    raw = _raw(prepared) if raw is None else raw
    author, review = _bindings(prepared, raw, call=call)
    receipt = record_visible_source_review(
        prepared=prepared, author=author, review=review, raw_verdict=raw
    )
    return receipt, author, review


def test_v1_frozen_receipt_restores_original_request_and_verdict_bytes():
    # This fixed offline record prevents same-version compiler/parser drift.
    # Its fixture audit anchors are not production invocation evidence.
    raw = (
        (Path(__file__).parent / "fixtures/visible_source_review_receipt_v1.json")
        .read_text()
        .removesuffix("\n")
    )
    assert _hash(raw) == "6bb35e49113924e8ffc66f09bd3e0e99977eb41772a622a0e9348961d2f5f7a9"
    receipt = VisibleSourceReviewReceipt.model_validate_json(raw, strict=True)
    assert _json(receipt.model_dump(mode="json")) == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["opaque_tail", "seventeen_beats"])
async def test_complete_coverage_never_skips_opaque_or_excess_beats(tmp_path, mode):
    async with _sources(tmp_path) as case:
        candidate = _candidate(case)
        changes = list(candidate.proposed_changes)
        position = next(
            index
            for index, change in enumerate(changes)
            if change.kind == "expression_plan_transition"
        )
        change = changes[position]
        payload = change.payload.value()
        intents = candidate.action_intents
        if mode == "opaque_tail":
            payload["beat_drafts"][-1].update(
                inline_text=None,
                payload_ref="payload:fixture:unread-tail",
                materialized_payload_ref=None,
            )
            tail_id = payload["beat_drafts"][-1]["beat_id"]
            intents = tuple(
                intent.model_copy(update={"payload_ref": "payload:fixture:unread-tail"})
                if intent.beat_ref == tail_id
                else intent
                for intent in intents
            )
            expected = "complete inline text"
        else:
            first = payload["beat_drafts"][0]
            payload["beat_drafts"].extend(
                {**first, "beat_id": f"beat:fixture:{index}"} for index in range(15)
            )
            expected = "one to sixteen"
        changes[position] = change.model_copy(
            update={
                "payload": CanonicalTypedPayload.from_value(
                    payload_schema=change.payload.payload_schema,
                    value=payload,
                )
            }
        )
        with pytest.raises(ValueError, match=expected):
            _prepare(
                case,
                candidate=candidate.model_copy(
                    update={
                        "proposed_changes": tuple(changes),
                        "action_intents": intents,
                    }
                ),
            )


@pytest.mark.asyncio
async def test_complete_original_candidate_and_actual_mock_request_round_trip(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    async with _sources(tmp_path) as case:
        prepared = _prepare(case)
        value = prepared.as_dict()
        requests = []

        async def provider(request):
            body = json.loads(request.content)
            requests.append(body)
            assert body["messages"] == value["request"]["messages"]
            assert body["tools"] == value["request"]["tools"]
            assert body["tool_choice"] == value["request"]["tool_choice"]
            return _http_result(body, json.loads(_raw(prepared)))

        model = DeepSeekChatModel(
            "fixture",
            "https://fixture.invalid",
            "deepseek-v4-flash",
            thinking_enabled=False,
            transport=httpx.MockTransport(provider),
        )
        try:
            with model_provider_request_identity_scope(
                request_hash=value["request_hash"],
                identity_extras=value["identity_extras"],
            ):
                raw, _usage = await model.complete_json_with_usage(**value["request"])
        finally:
            await model.aclose()
        receipt, author, review = _record(prepared, raw=raw)
        restored = VisibleSourceReviewReceipt.model_validate_json(
            receipt.model_dump_json(), strict=True
        )
        assert (
            verify_visible_source_review_receipt(
                receipt=restored,
                expected_prepared=prepared,
                expected_author=author,
                expected_review=review,
            )
            == receipt
        )
        assert len(requests) == 1
        assert len(receipt.verdict.segments) == 2
        assert [b["text"] for b in value["beat_mapping"]] == [
            "你取消了周五的报告。",
            "我想先听你说。",
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["tail", "order", "non_visible", "alias", "table_order", "row_scope"]
)
async def test_recomputed_self_consistent_record_cannot_replace_original_preparation(
    tmp_path, change
):
    async with _sources(tmp_path) as case:
        original = _prepare(case)
        original_receipt, original_author, original_review = _record(original)
        if change in {"tail", "order", "non_visible"}:
            texts = (
                ("你取消了周五的报告。", "我想读一会儿书。")
                if change == "tail"
                else (
                    ("我想先听你说。", "你取消了周五的报告。")
                    if change == "order"
                    else ("你取消了周五的报告。", "我想先听你说。")
                )
            )
            changed = _prepare(
                case, candidate=_candidate(case, texts=texts, rationale="不同的候选理由。")
            )
        elif change == "alias":
            changed = _prepare(case, aliases={"S2": case.request.trigger_ref})
        else:
            table = compile_visible_source_table(
                request=case.request, capsule=case.capsule
            ).as_dict()
            if change == "table_order":
                table["source_references"].reverse()
                for index, row in enumerate(table["source_references"]):
                    row["source_ref_index"] = index
            else:
                # Change a baseline row's retained authority scope, without
                # changing the eligible current report used by the verdict.
                material = table["source_materials"][0]
                material["material"]["authority"] = "changed_scope"
                if "time_comparison" in material["material"]:
                    # Keep the forged table internally consistent so this test
                    # still exercises the original receipt pin, not display
                    # provenance rejection (covered separately).
                    body = {key: value for key, value in material["material"].items()
                            if key != "time_comparison"}
                    material["material"]["time_comparison"]["original_material_identity"] = _hash(_json(body))
                material["material_identity"] = _hash(_json(material["material"]))
                for row in table["source_references"]:
                    if row["material_index"] == 0:
                        row["material_identity"] = material["material_identity"]
            table["table_hash"] = _hash(_json(table["source_references"]))
            table["materials_hash"] = _hash(_json(table["source_materials"]))
            changed = _prepare(case, table=VisibleSourceTable(payload_json=_json(table)))
        altered, _author, _review = _record(changed)
        assert altered.receipt_hash != original_receipt.receipt_hash
        with pytest.raises(ValueError, match="original complete preparation"):
            verify_visible_source_review_receipt(
                receipt=altered,
                expected_prepared=original,
                expected_author=original_author,
                expected_review=original_review,
            )


@pytest.mark.asyncio
async def test_fabricated_self_consistent_review_identity_needs_external_audit_anchor(tmp_path):
    async with _sources(tmp_path) as case:
        prepared = _prepare(case)
        original, author, review = _record(prepared)
        forged, _, _ = _record(prepared, call="review:invented")
        assert forged.review.request_hash == original.review.request_hash
        with pytest.raises(ValueError, match="immutable invocation audits"):
            verify_visible_source_review_receipt(
                receipt=forged,
                expected_prepared=prepared,
                expected_author=author,
                expected_review=review,
            )


@pytest.mark.asyncio
async def test_same_event_ref_activity_and_situation_cannot_exchange_original_review_rows(
    tmp_path, monkeypatch
):
    result, events, _, output, calls = await _run_http_journey(
        tmp_path,
        monkeypatch,
        intent={**INTENT, "duration_seconds": 600},
        duration_minutes=3,
        prefer_complete=True,
    )
    assert result["completed"] and calls == 1
    started = next(event for event in events if event["event_type"] == "ActivityStarted")
    ledger = open_journey_ledger(output)
    try:
        inbound = next(event for event in events if event["event_type"] == "ObservationRecorded")
        event, commit = ledger.lookup_event_commit(inbound["event_id"])
        observation = Observation.model_validate_json(event.payload_json, strict=True)
        capsule = context_capsule_compiler_from_ledger(
            ledger=ledger,
            relevance_scope=ContextRelevanceScope(actor_ref="agent:companion"),
        ).compile(
            query_from_projection(
                ledger.project(),
                actor_ref="agent:companion",
                trigger_ref=event.event_id,
            )
        )
        request = _request(capsule).model_copy(
            update={
                "trigger_message": TriggerMessage(
                    event_ref=event.event_id,
                    event_payload_hash="sha256:" + event.payload_hash,
                    source_world_revision=commit.world_revision,
                    observation_ref=observation.observation_id,
                    actor=observation.actor,
                    channel=observation.channel,
                    reply_target="fixture:local",
                    text=observation.text,
                )
            }
        )
        case = SimpleNamespace(request=request, capsule=capsule)
        candidate = _candidate(case, texts=("这段活动正在进行。", "我想先听你说。"))
        table = compile_visible_source_table(request=case.request, capsule=capsule)
        rows = table.source_references()
        same_ref = [row for row in rows if row["source_ref"] == started["event_id"]]
        assert len(same_ref) == 2
        eligible = next(row for row in same_ref if row["support_eligibility"] == "eligible")
        baseline = next(row for row in same_ref if row["support_eligibility"] == "baseline_only")

        def verdict(index):
            return _json(
                {
                    "contract": "visible-beat-source-verdict.1",
                    "decisions": [
                        {
                            "beat_index": 0,
                            "verdict": "closed",
                            "semantic_role": "external_proposition",
                            "subject_role": "companion",
                            "source_ref_indexes": [index],
                        },
                        {
                            "beat_index": 1,
                            "verdict": "source_free",
                            "semantic_role": "commitment",
                            "subject_role": "companion",
                            "source_ref_indexes": [],
                        },
                    ],
                }
            )

        original = _prepare(case, candidate=candidate, table=table)
        receipt, author, review = _record(original, raw=verdict(eligible["source_ref_index"]))
        with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
            _record(original, raw=verdict(baseline["source_ref_index"]))
        swapped = table.as_dict()
        left, right = eligible["source_ref_index"], baseline["source_ref_index"]
        stored_rows = swapped["source_references"]
        stored_rows[left], stored_rows[right] = stored_rows[right], stored_rows[left]
        for index, row in enumerate(stored_rows):
            row["source_ref_index"] = index
        swapped["table_hash"] = _hash(_json(stored_rows))
        alternate = _prepare(
            case, candidate=candidate, table=VisibleSourceTable(payload_json=_json(swapped))
        )
        forged, _, _ = _record(alternate, raw=verdict(right))
        assert forged.author == receipt.author
        with pytest.raises(ValueError, match="original complete preparation"):
            verify_visible_source_review_receipt(
                receipt=forged,
                expected_prepared=original,
                expected_author=author,
                expected_review=review,
            )
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_unclosed_is_semantic_rejection_and_bad_actor_or_coverage_is_technical(tmp_path):
    async with _sources(tmp_path) as case:
        prepared = _prepare(case)
        with pytest.raises(VisibleSourceReviewRejected) as rejected:
            _record(prepared, raw=_raw(prepared, verdict="unclosed"))
        assert rejected.value.verdict.segments[0].decision == "unclosed"
        with pytest.raises(VisibleSourceClosureWireFailure):
            _record(prepared, raw=_raw(prepared, subject="companion"))
        missing = json.loads(_raw(prepared))
        missing["decisions"].pop()
        with pytest.raises(VisibleSourceClosureWireFailure):
            _record(prepared, raw=_json(missing))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("request_hash", "b" * 64),
        ("response_hash", "c" * 64),
        ("parent_model_call_id", "author:other"),
        ("model_call_id", "author:fixture"),
    ],
)
async def test_unclosed_from_mismatched_invocation_is_not_character_reselection(
    tmp_path, field, value
):
    async with _sources(tmp_path) as case:
        prepared = _prepare(case)
        raw = _raw(prepared, verdict="unclosed")
        author, review = _bindings(prepared, raw)
        with pytest.raises(ValueError, match="invocation") as failure:
            record_visible_source_review(
                prepared=prepared,
                author=author,
                review=review.model_copy(update={field: value}),
                raw_verdict=raw,
            )
        assert not isinstance(failure.value, VisibleSourceReviewRejected)


@pytest.mark.asyncio
async def test_receipt_revalidates_copied_objects_and_full_tool_response_bytes(tmp_path):
    async with _sources(tmp_path) as case:
        prepared = _prepare(case)
        receipt, author, review = _record(prepared)
        changed = receipt.model_copy(update={"raw_verdict": receipt.raw_verdict + " "})
        with pytest.raises(ValueError, match="invocation"):
            verify_visible_source_review_receipt(
                receipt=changed,
                expected_prepared=prepared,
                expected_author=author,
                expected_review=review,
            )
        value = deepcopy(receipt.model_dump(mode="json"))
        value["review"]["request_hash"] = "0" * 64
        value["receipt_hash"] = _hash(
            _json({k: v for k, v in value.items() if k != "receipt_hash"})
        )
        with pytest.raises(ValueError, match="invocation"):
            VisibleSourceReviewReceipt.model_validate_json(_json(value), strict=True)
