"""Real public offline chain: diagnostics reach the same role, never authorize speech."""

import json
import sqlite3

import pytest

from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate
from test_launch_visible_source_gate import _ReviewHTTP, _app, _audits
from test_whole_candidate_author import BEATS, _decision, _inbound
from test_world_stimulus_life_intent import _http_result


class _DiagnosticHTTP(_ReviewHTTP):
    review_version = "2"

    def __init__(self, verdicts, **kwargs):
        super().__init__(verdicts, **kwargs)
        self.introduce_new_fact = "new_unclosed" in verdicts

    async def __call__(self, request):
        body = json.loads(request.content)
        name = body["tool_choice"]["function"]["name"]
        if name.startswith("character_inbound_"):
            if self.introduce_new_fact and self.authors == 1:
                self.authors += 1
                self.requests.append(body)
                authored = _decision()
                authored["expression_draft"]["beats"][0]["text"] = "我想重新把自己的想法说完整。"
                authored["expression_draft"]["beats"][1]["text"] = "昨晚我已经在图书馆见过你了。"
                return _http_result(body, {"result": {
                    key: authored[key] for key in ("result_kind", "appraisal_draft", "expression_draft")
                }})
            return await super().__call__(request)
        self.requests.append(body)
        self.reviews += 1
        assert name.endswith(f"_v{self.review_version}"), name
        scenario = next(self.verdicts)
        packet = json.loads(body["messages"][-1]["content"])
        beats = packet["visible_beats"]
        rejected_index = 1 if scenario == "new_unclosed" else 0
        rejected = scenario in {"unclosed", "new_unclosed", "invalid_diagnostic"}
        decisions = [{
            "beat_index": i,
            "verdict": "unclosed" if rejected and i == rejected_index else "source_free",
            "semantic_role": "external_proposition" if rejected and i == rejected_index else "commitment",
            "subject_role": "companion", "source_ref_indexes": [],
        } for i in range(len(beats))]
        diagnostic = {
            "beat_index": rejected_index, "char_start": 0,
            "char_end": len(beats[rejected_index]["text"]),
            "related_source_ref_indexes": [], "source_problem": "材料没有支持该陈述所表达的已发生经历。",
        }
        if self.review_version in {"6", "7", "8"}:
            diagnostic.pop("char_start")
            diagnostic.pop("char_end")
            diagnostic["source_problem"] = "support_missing"
        if scenario == "invalid_diagnostic":
            if self.review_version in {"6", "7", "8"}:
                diagnostic["beat_index"] = len(beats)
            else:
                diagnostic["char_end"] += 1
        if self.review_version in {"3", "4", "5", "6", "7", "8"}:
            for decision in decisions:
                assert decision.pop("source_ref_indexes") == []
        return _http_result(body, {
            "contract": f"visible-beat-source-verdict.{self.review_version}", "decisions": decisions,
            "rejections": [diagnostic] if rejected else [],
        })


@pytest.mark.asyncio
@pytest.mark.parametrize("review_version", ["2", "3", "4", "5", "6", "7", "8"])
async def test_diagnostics_reach_same_role_and_complete_replacement_cold_verifies(tmp_path, review_version):
    http = _DiagnosticHTTP(["unclosed", "pass"], tool_version="3")
    http.review_version = review_version
    path = tmp_path / "world.sqlite"
    async with _app(path, http) as app:
        assert (await app.respond(_inbound())).status == "action_authorized"
        assert (http.authors, http.reviews) == (2, 2)
        first, initial_review, corrected, final_review = http.requests
        initial_context = json.loads(first["messages"][1]["content"])
        corrected_context = json.loads(corrected["messages"][1]["content"])
        correction = corrected_context["role_result_correction"]["coordinate"]
        detail = correction["failure_detail"]
        instruction = corrected_context["role_result_correction"]["instruction"]
        assert "coordinate.failure_detail" in instruction
        assert detail not in instruction
        assert corrected["messages"][0] == first["messages"][0]
        assert len(detail) <= 3900
        data = json.loads(detail.split("\n", 1)[1])
        assert data["rows"][0][:3] == [0, 0, len(BEATS[0])]
        assert BEATS[0].startswith(data["rows"][0][3])
        assert data["rows"][0][4] == ("support_missing" if review_version in {"6", "7", "8"} else "材料没有支持该陈述所表达的已发生经历。")
        assert corrected_context["request"] == initial_context["request"]
        assert corrected_context["inner_life_snapshot"]["materials"] == initial_context["inner_life_snapshot"]["materials"]
        assert corrected_context["expression_capabilities"] == initial_context["expression_capabilities"]
        first_packet = json.loads(initial_review["messages"][-1]["content"])
        last_packet = json.loads(final_review["messages"][-1]["content"])
        assert len(last_packet["visible_beats"]) == len(BEATS)
        assert first_packet["source_materials"] == last_packet["source_materials"]
        references_key = "source_reference_tables" if review_version in {"4", "5", "6", "7", "8"} else "source_references"
        assert first_packet[references_key] == last_packet[references_key]
        audits = _audits(app)
        assert len([a for a in audits if a.usage is not None]) == 4
        winner = next(a for a in audits if a.visible_source_review_json is not None)
        receipt = json.loads(winner.visible_source_review_json)["receipt"]
        assert receipt["contract"] == f"visible-source-review-receipt.{review_version}"
        evidence = app.export_replay_evidence()
        decision = next(a for a in evidence.projection.proposal_audits if a.proposal_kind == "decision")
        assert verify_recorded_candidate(
            audit=decision, model_result_audits=evidence.projection.model_result_audits,
        ) == receipt["receipt_hash"]
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
        with sqlite3.connect(tmp_path / "usage.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM world_v2_model_usage WHERE billing_state='known'").fetchone() == (4,)
    # Cold persisted evidence is sufficient: no new author or reviewer invocation.
    cold = _DiagnosticHTTP([], tool_version="3")
    cold.review_version = review_version
    async with _app(path, cold) as app:
        repeated = await app.respond(_inbound())
        assert repeated.status == "action_authorized"
        assert cold.requests == []
        assert app.export_replay_evidence().projection.semantic_hash == evidence.projection.semantic_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("review_version", ["2", "3", "4", "5", "6", "7", "8"])
@pytest.mark.parametrize("verdicts,expected", [(["unclosed", "new_unclosed"], (2, 2)), (["invalid_diagnostic"], (1, 1))])
async def test_changed_other_beat_and_bad_locator_authorize_nothing(tmp_path, verdicts, expected, review_version):
    http = _DiagnosticHTTP(verdicts, tool_version="3")
    http.review_version = review_version
    async with _app(tmp_path / "world.sqlite", http) as app:
        assert (await app.respond(_inbound())).status != "action_authorized"
        assert (http.authors, http.reviews) == expected
        assert not app.export_replay_evidence().projection.actions
        # A terminal failure audit can retain aggregate author usage in addition
        # to the invocation record; physical billing counts the metered rows.
        with sqlite3.connect(tmp_path / "usage.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM world_v2_model_usage WHERE billing_state='known'").fetchone() == (sum(expected),)
        reviews = [a for a in _audits(app) if a.route.reason_code == "validation.source_review"]
        assert len(reviews) == expected[1]
        assert all(a.usage is not None and a.response_hash is not None for a in reviews)


@pytest.mark.asyncio
async def test_feedback_construction_failure_preserves_completed_review_usage(tmp_path, monkeypatch):
    import companion_daemon.world_v2.visible_source_rejection_feedback as feedback

    def broken_formatter(**kwargs):
        raise ValueError("offline feedback-construction fault")

    monkeypatch.setattr(feedback, "rejection_feedback", broken_formatter)
    http = _DiagnosticHTTP(["unclosed"], tool_version="3")
    async with _app(tmp_path / "world.sqlite", http) as app:
        assert (await app.respond(_inbound())).status != "action_authorized"
        assert (http.authors, http.reviews) == (1, 1)
        assert not app.export_replay_evidence().projection.actions
        audits = _audits(app)
        review = next(a for a in audits if a.route.reason_code == "validation.source_review")
        assert review.usage is not None and review.response_hash is not None
        assert any(a.failure_code == "source_review_exception" for a in audits)
        with sqlite3.connect(tmp_path / "usage.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM world_v2_model_usage WHERE billing_state='known'").fetchone() == (2,)


@pytest.mark.asyncio
async def test_final_evidence_size_failure_keeps_already_completed_invocations(tmp_path, monkeypatch):
    import companion_daemon.world_v2.visible_source_runtime as runtime

    class TooSmallEvidenceHTTP(_DiagnosticHTTP):
        async def __call__(self, request):
            response = await super().__call__(request)
            if self.reviews:
                # Inject the size fault after real preparation and a completed
                # metered review, at the final carrier boundary. This does not
                # claim a naturally oversized 512000-byte production example.
                monkeypatch.setattr(runtime, "MAX_EVIDENCE_BYTES", 1)
            return response

    http = TooSmallEvidenceHTTP(["pass"], tool_version="3")
    async with _app(tmp_path / "world.sqlite", http) as app:
        assert (await app.respond(_inbound())).status != "action_authorized"
        assert (http.authors, http.reviews) == (1, 1)
        assert not app.export_replay_evidence().projection.actions
        audits = _audits(app)
        review = next(a for a in audits if a.route.reason_code == "validation.source_review")
        assert review.usage is not None and review.response_hash is not None
        assert any(a.failure_code == "source_review_exception" for a in audits)
        with sqlite3.connect(tmp_path / "usage.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM world_v2_model_usage WHERE billing_state='known'").fetchone() == (2,)


@pytest.mark.asyncio
@pytest.mark.parametrize("version,target,verdicts", [
    ("1", "2", ["pass"]), ("1", "2", ["unclosed", "pass"]), ("2", "1", ["pass"]),
    ("1", "3", ["pass"]), ("2", "3", ["unclosed", "pass"]),
    ("3", "1", ["pass"]), ("3", "2", ["unclosed", "pass"]),
    ("3", "4", ["pass"]), ("4", "3", ["unclosed", "pass"]),
    ("3", "4", ["closed"]), ("4", "3", ["closed"]),
])
async def test_fully_recompiled_version_forgery_cannot_replace_independent_review_audit(tmp_path, version, target, verdicts):
    from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
    from companion_daemon.world_v2.proposal_envelope import DecisionProposal
    from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
    from companion_daemon.world_v2.visible_source_review_receipt import (
        VisibleSourceReviewReceipt, prepare_visible_source_review, record_visible_source_review,
    )
    from companion_daemon.world_v2.visible_source_runtime import canonical, digest

    inbound = _inbound()
    if verdicts == ["closed"]:
        from dataclasses import replace
        from test_visible_source_required_reference_runtime import RequiredReferenceHTTP

        http = RequiredReferenceHTTP()
        inbound = replace(inbound, text="我刚忙完，来找你说会儿话。你现在想聊点什么？")
    else:
        http = (_ReviewHTTP if version == "1" else _DiagnosticHTTP)(verdicts, tool_version="3")
    http.review_version = version
    async with _app(tmp_path / "world.sqlite", http) as app:
        assert (await app.respond(inbound)).status == "action_authorized"
        projection = app.export_replay_evidence().projection
    audit = next(a for a in projection.proposal_audits if a.proposal_kind == "decision")
    original = next(a for a in projection.model_result_audits if a.model_result_ref == audit.model_result_ref)
    parent = RecordedModelResultAudit.model_validate_json(original.audit_json)
    value = json.loads(parent.visible_source_review_json)
    receipt = VisibleSourceReviewReceipt.model_validate_json(canonical(value["receipt"]))
    assert verify_recorded_candidate(audit=audit, model_result_audits=projection.model_result_audits) == receipt.receipt_hash
    prepared = json.loads(receipt.prepared_json)
    replacement = prepare_visible_source_review(
        candidate=DecisionProposal.model_validate_json(audit.proposal_json),
        source_table=VisibleSourceTable(payload_json=prepared["source_table_json"]),
        source_ref_aliases=prepared["source_ref_aliases"], review_version=target,
    )
    raw = json.loads(receipt.raw_verdict)
    raw["contract"] = f"visible-beat-source-verdict.{target}"
    if target in {"2", "3", "4", "5", "6", "7", "8"}:
        raw["rejections"] = []
    else:
        raw.pop("rejections")
    for decision in raw["decisions"]:
        if version in {"3", "4", "5", "6", "7", "8"}:
            refs = ([decision.pop("first_source_ref_index"), *decision.pop("additional_source_ref_indexes")]
                    if decision["verdict"] == "closed" else [])
        else:
            refs = decision.pop("source_ref_indexes")
        if target in {"3", "4", "5", "6", "7", "8"}:
            if decision["verdict"] == "closed":
                decision.update(first_source_ref_index=refs[0], additional_source_ref_indexes=refs[1:])
        else:
            decision["source_ref_indexes"] = refs
    raw = canonical(raw)
    forged = record_visible_source_review(
        prepared=replacement, author=receipt.author, raw_verdict=raw,
        review=receipt.review.model_copy(update={"request_hash": replacement.as_dict()["request_hash"], "response_hash": digest(raw)}),
    )
    # The forgery passes its own full schema, raw verdict and internal hashes.
    assert VisibleSourceReviewReceipt.model_validate_json(forged.model_dump_json(), strict=True) == forged
    value["receipt"] = forged.model_dump(mode="json")
    changed_parent = parent.model_copy(update={"visible_source_review_json": canonical(value)})
    changed_json = changed_parent.model_dump_json()
    rows = tuple(original.model_copy(update={"audit_json": changed_json, "audit_hash": digest(changed_json)})
                 if row.model_result_ref == original.model_result_ref else row
                 for row in projection.model_result_audits)
    assert tuple(row for row in rows if row.model_result_ref != original.model_result_ref) == tuple(
        row for row in projection.model_result_audits if row.model_result_ref != original.model_result_ref
    )
    with pytest.raises(ValueError, match="immutable invocation audits"):
        verify_recorded_candidate(audit=audit, model_result_audits=rows)
