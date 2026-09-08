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
        assert name.endswith("_v2"), name
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
        if scenario == "invalid_diagnostic":
            diagnostic["char_end"] += 1
        return _http_result(body, {
            "contract": "visible-beat-source-verdict.2", "decisions": decisions,
            "rejections": [diagnostic] if rejected else [],
        })


@pytest.mark.asyncio
async def test_v2_diagnostics_reach_same_role_and_complete_replacement_cold_verifies(tmp_path):
    http = _DiagnosticHTTP(["unclosed", "pass"], tool_version="3")
    path = tmp_path / "world.sqlite"
    async with _app(path, http) as app:
        assert (await app.respond(_inbound())).status == "action_authorized"
        assert (http.authors, http.reviews) == (2, 2)
        first, initial_review, corrected, final_review = http.requests
        initial_context = json.loads(first["messages"][1]["content"])
        corrected_context = json.loads(corrected["messages"][1]["content"])
        correction = corrected_context["inner_life_snapshot"]["role_result_correction"]
        detail = correction["failure_detail"]
        assert detail in corrected["messages"][0]["content"]
        assert len(detail) <= 3900
        data = json.loads(detail.split("\n", 1)[1])
        assert data["rows"][0][:3] == [0, 0, len(BEATS[0])]
        assert BEATS[0].startswith(data["rows"][0][3])
        assert data["rows"][0][4] == "材料没有支持该陈述所表达的已发生经历。"
        assert corrected_context["request"] == initial_context["request"]
        assert corrected_context["inner_life_snapshot"]["materials"] == initial_context["inner_life_snapshot"]["materials"]
        assert corrected_context["expression_capabilities"] == initial_context["expression_capabilities"]
        first_packet = json.loads(initial_review["messages"][-1]["content"])
        last_packet = json.loads(final_review["messages"][-1]["content"])
        assert len(last_packet["visible_beats"]) == len(BEATS)
        assert first_packet["source_materials"] == last_packet["source_materials"]
        assert first_packet["source_references"] == last_packet["source_references"]
        audits = _audits(app)
        assert len([a for a in audits if a.usage is not None]) == 4
        winner = next(a for a in audits if a.visible_source_review_json is not None)
        receipt = json.loads(winner.visible_source_review_json)["receipt"]
        assert receipt["contract"] == "visible-source-review-receipt.2"
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
    async with _app(path, cold) as app:
        repeated = await app.respond(_inbound())
        assert repeated.status == "action_authorized"
        assert cold.requests == []
        assert app.export_replay_evidence().projection.semantic_hash == evidence.projection.semantic_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("verdicts,expected", [(["unclosed", "new_unclosed"], (2, 2)), (["invalid_diagnostic"], (1, 1))])
async def test_v2_changed_other_beat_and_bad_locator_authorize_nothing(tmp_path, verdicts, expected):
    http = _DiagnosticHTTP(verdicts, tool_version="3")
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
