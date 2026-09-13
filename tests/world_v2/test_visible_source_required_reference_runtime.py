"""Trial16-shaped uptake requires a model-selected reference in the v3 tool."""

from dataclasses import replace
import json
import sqlite3

import pytest

from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate
from test_launch_visible_source_gate import _app, _audits
from test_whole_candidate_author import _decision, _inbound
from test_world_stimulus_life_intent import _http_result


TEXTS = ("哈哈 你忙完还专门来找我聊天啊 那我得想想聊点什么配得上你这份心意", "不过你刚忙完 是忙什么去了")


class RequiredReferenceHTTP:
    tool_version = "3"
    review_version = "3"

    def __init__(self, *, fault=None):
        self.requests = []
        self.authors = self.reviews = 0
        self.fault = fault

    async def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        tool = body["tool_choice"]["function"]["name"]
        if tool == "character_inbound_initial_v3":
            self.authors += 1
            authored = _decision()
            for draft, text in zip(authored["expression_draft"]["beats"], TEXTS, strict=True):
                draft["text"] = text
            return _http_result(body, {"result": authored})
        assert tool == f"visible_beat_source_verdict_v{self.review_version}"
        self.reviews += 1
        packet = json.loads(body["messages"][-1]["content"])
        references = packet.get("source_references")
        if self.review_version in {"4", "5", "6", "7"}:
            references = [dict(zip(table["columns"], row, strict=True))
                          for table in packet["source_reference_tables"] for row in table["rows"]]
        source = next(row for row in references if row["kind"] == "current_counterpart_report")
        assert packet["source_materials"][source["material_index"]]["message"]["text"] == "我刚忙完，来找你说会儿话。你现在想聊点什么？"
        closed = {
            "beat_index": 0, "verdict": "closed", "semantic_role": "external_proposition",
            "subject_role": "counterpart", "first_source_ref_index": source["source_ref_index"],
            "additional_source_ref_indexes": [],
        }
        if self.fault == "missing_first":
            closed.pop("first_source_ref_index")
        if self.fault == "wrong_actor":
            closed["subject_role"] = "companion"
        if self.fault == "old_empty_refs":
            closed.pop("first_source_ref_index")
            closed.pop("additional_source_ref_indexes")
            closed["source_ref_indexes"] = []
        return _http_result(body, {
            "contract": f"visible-beat-source-verdict.{self.review_version}",
            "decisions": [closed, {"beat_index": 1, "verdict": "source_free", "semantic_role": "question", "subject_role": "counterpart"}],
            "rejections": [],
        })


@pytest.mark.asyncio
@pytest.mark.parametrize("review_version", ["3", "4", "5", "6", "7"])
@pytest.mark.parametrize("fault", [None, "missing_first", "old_empty_refs", "wrong_actor"])
async def test_real_shaped_closed_uptake_uses_model_index_and_keeps_actor_boundary(tmp_path, fault, review_version):
    http = RequiredReferenceHTTP(fault=fault)
    http.review_version = review_version
    inbound = replace(_inbound(), text="我刚忙完，来找你说会儿话。你现在想聊点什么？")
    async with _app(tmp_path / "world.sqlite", http) as app:
        outcome = await app.respond(inbound)
        assert (http.authors, http.reviews) == (1, 1)
        projection = app.export_replay_evidence().projection
        if fault:
            assert outcome.status != "action_authorized"
            assert not projection.actions
        else:
            assert outcome.status == "action_authorized"
            assert tuple(row.text for row in projection.stored_message_payloads) == TEXTS
            winner = next(a for a in _audits(app) if a.visible_source_review_json is not None)
            receipt = json.loads(winner.visible_source_review_json)["receipt"]
            assert receipt["contract"] == f"visible-source-review-receipt.{review_version}"
            audit = next(a for a in projection.proposal_audits if a.proposal_kind == "decision")
            assert verify_recorded_candidate(audit=audit, model_result_audits=projection.model_result_audits) == receipt["receipt_hash"]
        with sqlite3.connect(tmp_path / "usage.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM world_v2_model_usage WHERE billing_state='known'").fetchone() == (2,)
