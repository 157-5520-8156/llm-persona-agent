"""Core's one correction keeps the original atomic v3 input prefix intact."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from companion_daemon.llm import provider_invocation_request_hash
from companion_daemon.world_v2.character_interior.inbound_author import (
    _InboundCharacterAuthor,
    _rejected_call_pin,
    _role_result_correction_instruction,
)
from companion_daemon.world_v2.visible_source_author_request import (
    verify_visible_source_author_request,
)
from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate
from test_launch_visible_source_gate import _app, _audits
from test_character_interior_inbound_author import _request
from test_visible_source_diagnostics_runtime import _DiagnosticHTTP
from test_whole_candidate_author import BEATS, _inbound


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_invalid", [False, True])
async def test_public_v3_core_correction_keeps_prefix_and_full_feedback(
    tmp_path, monkeypatch, repeat_invalid,
):
    owned = []
    propose = _InboundCharacterAuthor._propose_appraisal

    async def capture_owned(self, request, **kwargs):
        owned.append(request)
        return await propose(self, request, **kwargs)

    monkeypatch.setattr(_InboundCharacterAuthor, "_propose_appraisal", capture_owned)
    http = _DiagnosticHTTP(
        ["unclosed", "new_unclosed" if repeat_invalid else "pass"], tool_version="3",
    )
    http.review_version = "4"
    path = tmp_path / "world.sqlite"
    async with _app(path, http) as app:
        outcome = await app.respond(_inbound())
        assert (http.authors, http.reviews) == (2, 2)
        first, first_review, corrected, final_review = http.requests
        assert corrected["messages"][0] == first["messages"][0], (
            "Core correction must not insert feedback inside the original system prefix"
        )
        original_user, corrected_user = (
            json.loads(body["messages"][1]["content"]) for body in (first, corrected)
        )
        assert [message["role"] for message in corrected["messages"]] == ["system", "user"]
        assert list(corrected_user)[-1] == "role_result_correction"
        trailer = corrected_user.pop("role_result_correction")
        assert set(trailer) == {"instruction", "coordinate"}
        coordinate = trailer["coordinate"]
        assert coordinate["contract"] == "character-interior-role-result-correction.1"
        assert coordinate["task"] == "return_one_fresh_complete_role_result"
        assert coordinate["failure_code"] == "role_result_schema_invalid"
        assert trailer["instruction"] == _role_result_correction_instruction(
            coordinate, detail_in_coordinate=True,
        )
        assert coordinate["failure_detail"] not in trailer["instruction"]
        assert "coordinate.failure_detail" in trailer["instruction"]
        assert corrected_user == original_user
        assert _json(corrected_user) == first["messages"][1]["content"]
        assert corrected["messages"][1]["content"].startswith(
            first["messages"][1]["content"][:-1] + ',"role_result_correction":'
        )
        assert all(
            corrected[field] == first[field] for field in first if field != "messages"
        )

        # Only the outgoing two-message layout moves the coordinate. Core's
        # owned Context and the audit pin continue to retain the original one.
        assert len(owned) == 2
        owned_first, owned_corrected = (
            json.loads(request.model_content_json) for request in owned
        )
        assert owned_corrected["inner_life_snapshot"].pop("role_result_correction") == coordinate
        assert owned_corrected == owned_first
        assert _rejected_call_pin(owned[0]) == _rejected_call_pin(owned[1])
        before_packet, after_packet = (
            json.loads(body["messages"][1]["content"])
            for body in (first_review, final_review)
        )
        for field in ("source_materials", "source_reference_tables"):
            assert before_packet[field] == after_packet[field]
        evidence = app.export_replay_evidence()
        with sqlite3.connect(tmp_path / "usage.sqlite") as db:
            assert db.execute(
                "SELECT COUNT(*) FROM world_v2_model_usage WHERE billing_state='known'"
            ).fetchone() == (4,)
        if repeat_invalid:
            assert outcome.status != "action_authorized"
            assert evidence.projection.actions == ()
            assert evidence.projection.stored_message_payloads == ()
            return

        assert outcome.status == "action_authorized"
        assert tuple(p.text for p in evidence.projection.stored_message_payloads) == (
            "我想重新把自己的想法说完整。", BEATS[1],
        )
        audits = _audits(app)
        winner = next(row for row in audits if row.visible_source_review_json is not None)
        rejected = next(row for row in audits if (
            row.route.reason_code == "author_candidate.primary_initial.validation_rejected"
        ))
        assert winner.request_hash != rejected.request_hash
        assert winner.character_interior_lineage.author_parent_model_call_id == rejected.model_call_id
        review = json.loads(winner.visible_source_review_json)
        raw_carrier = review["author_request_json"]
        carrier = json.loads(raw_carrier)
        assert carrier["contract"] == "visible-source-author-request.1"
        for field in ("messages", "temperature", "tools", "tool_choice"):
            assert carrier[field] == corrected[field]
        assert provider_invocation_request_hash(**{
            key: value for key, value in carrier.items() if key != "contract"
        }) == winner.request_hash
        aliases = verify_visible_source_author_request(
            raw_carrier, expected_request_hash=winner.request_hash,
        )
        assert aliases == original_user["expression_hard_boundaries"]["source_ref_aliases"]
        forged = deepcopy(carrier)
        forged_user = json.loads(forged["messages"][1]["content"])
        forged_user["role_result_correction"]["coordinate"]["failure_detail"] = "forged"
        forged["messages"][1]["content"] = _json(forged_user)
        with pytest.raises(ValueError, match="hash differs"):
            verify_visible_source_author_request(
                json.dumps(forged, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                expected_request_hash=winner.request_hash,
            )
        decision = next(row for row in evidence.projection.proposal_audits if (
            row.proposal_kind == "decision"
        ))
        receipt_hash = verify_recorded_candidate(
            audit=decision, model_result_audits=evidence.projection.model_result_audits,
        )

    cold = _DiagnosticHTTP([], tool_version="3")
    cold.review_version = "4"
    async with _app(path, cold) as app:
        assert (await app.respond(_inbound())).status == "action_authorized"
        assert cold.requests == []
        recovered = app.export_replay_evidence()
        assert recovered.projection.model_result_audits == evidence.projection.model_result_audits
        assert verify_recorded_candidate(
            audit=decision, model_result_audits=recovered.projection.model_result_audits,
        ) == receipt_hash


@pytest.mark.asyncio
async def test_old_v3_correction_carrier_cold_verifies_without_recompiling(tmp_path, monkeypatch):
    import companion_daemon.world_v2.character_interior.inbound_author as wire

    def old_layout(messages, correction):
        # Recreate the prior compiler's exact insertion point; the coordinate
        # stays in the original snapshot. No recorded bytes are later upgraded.
        messages[0]["content"] += _role_result_correction_instruction(correction)

    path = tmp_path / "old-layout.sqlite"
    http = _DiagnosticHTTP(["unclosed", "pass"], tool_version="3")
    http.review_version = "4"
    with monkeypatch.context() as patch:
        patch.setattr(wire, "_append_atomic_v3_correction", old_layout)
        async with _app(path, http) as app:
            assert (await app.respond(_inbound())).status == "action_authorized"
            audits = _audits(app)
            winner = next(row for row in audits if row.visible_source_review_json is not None)
            carrier = json.loads(json.loads(winner.visible_source_review_json)["author_request_json"])
            user = json.loads(carrier["messages"][1]["content"])
            assert "role_result_correction" in user["inner_life_snapshot"]
            assert "role_result_correction" not in user
            evidence = app.export_replay_evidence()
            decision = next(row for row in evidence.projection.proposal_audits if row.proposal_kind == "decision")
            receipt_hash = verify_recorded_candidate(
                audit=decision, model_result_audits=evidence.projection.model_result_audits,
            )
    cold = _DiagnosticHTTP([], tool_version="3")
    cold.review_version = "4"
    async with _app(path, cold) as app:
        assert (await app.respond(_inbound())).status == "action_authorized"
        assert cold.requests == []
        recovered = app.export_replay_evidence()
        assert recovered.projection.model_result_audits == evidence.projection.model_result_audits
        assert verify_recorded_candidate(
            audit=decision, model_result_audits=recovered.projection.model_result_audits,
        ) == receipt_hash


class _CapturedRequest(BaseException):
    pass


class _CaptureCompiler:
    model = "deepseek-v4-flash"
    supports_required_tool_choice = True
    supports_strict_tool_choice = True

    async def complete_json_with_usage(self, messages, *, temperature, **kwargs):
        self.parameters = dict(messages=messages, temperature=temperature, **kwargs)
        raise _CapturedRequest()

    complete_json = complete_json_with_usage


async def _compiler_bytes():
    rows = {}
    for version, stream, forced in (
        ("1", False, True), ("2", False, True), ("3", False, True),
        ("1", True, True), ("1", False, False),
    ):
        for correction in (False, True):
            provider = _CaptureCompiler()
            provider.supports_required_tool_choice = forced
            author = _InboundCharacterAuthor(
                flash_model=provider, atomic_tool_envelope_version=version,
                whole_candidate_mode=True, visible_source_review_model=provider,
            )
            request = _request(revision=3, call="call:prefix-layout-baseline")
            context = json.loads(request.model_content_json)
            context["inner_life_snapshot"] = {"materials": {}}
            if correction:
                context["inner_life_snapshot"]["role_result_correction"] = {
                    "contract": "character-interior-role-result-correction.1",
                    "failure_code": "role_result_schema_invalid",
                    "failure_detail": "引用不能证明该动作已经完成。",
                    "task": "return_one_fresh_complete_role_result",
                }
            request = request.model_copy(update={
                "model_content_json": json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            })
            with pytest.raises(_CapturedRequest):
                await author._propose_appraisal(request, transport_provider=provider if stream else None)
            raw = json.dumps(provider.parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            rows[f"{version}:{stream}:{forced}:{correction}"] = sha256(raw.encode()).hexdigest()
    return rows


def test_legacy_compiler_bytes_stay_frozen_and_v3_reminder_has_new_identity():
    # Captured from c48d23d7 with the same synthetic compiler input. Legacy
    # tools have set-derived list order, so compare under the original seed.
    expected = {
        "1:False:False:False": "9f53b8ea6215fdde612c1e1e9081ae2ca90aeed48fd54e4bf43fb7d96ce42799",
        "1:False:False:True": "997b3c41452f47cafd05c4e76a0b7f2ca13ce968d21f2590400fb69ad40769cd",
        "1:False:True:False": "77f546d79c3b0b3b6b0798a3f464eea44c23be2381d3d4ead9e89ce7ad445629",
        "1:False:True:True": "63053978aac37e7959ffe3e38e42edbe4b76ca386e6644dd5c87188a11425d91",
        "1:True:True:False": "2793dd866bfe401f5d954a827994bdd59573dafbd59ef6fd26d6b08e8a789318",
        "1:True:True:True": "1f1cb26f47f317917abf41a5e1b014cf823d80cbc3e0277831edc1f8750196ba",
        "2:False:True:False": "afe2fb6bae3e9ae3cd2b12ce54d26bac84c7ac3e797e9469f66fbbd0957a4ad7",
        "2:False:True:True": "f1ae2dbad43c312177f523c55b4f2851cf3f25a43f1fed0d9cc96d2fcf8bfc2f",
    }
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", "import asyncio,json; from test_inbound_correction_prefix import _compiler_bytes; print(json.dumps(asyncio.run(_compiler_bytes())))"],
        cwd=root, check=True, capture_output=True, text=True,
        env={**os.environ, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
             "PYTHONPATH": os.pathsep.join(str(root / p) for p in ("src", "tests", "tests/world_v2", "tests/support"))},
    )
    current = json.loads(result.stdout)
    assert {key: current[key] for key in expected} == expected
    # Captured before the legacy-only repair. V3 retains the newer memory
    # authority (6622349f), single-episode guidance (51be7434), and one-copy
    # correction coordinate (057bb2b9). Old author requests retain their own
    # stored bytes; this compiler does not rebuild them during verification.
    assert current["3:False:True:False"] == "2e3ff34c61ee9dfc253bb03f896a31faf8e674a0ac7c5e9aed3061d5df9ab37a"
    assert current["3:False:True:True"] == "aa6e3035de2d08f23b2e4950f82c32c212f52f4e3c99330ff9c451a77666ac2e"
