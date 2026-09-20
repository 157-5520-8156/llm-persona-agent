from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).parents[2] / "scripts" / "run_memory_recall_eval.py"
_SPEC = importlib.util.spec_from_file_location("memory_recall_eval", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
assert_safe_eval_paths = _MODULE.assert_safe_eval_paths
_memory_slice_text = _MODULE._memory_slice_text
_score_negative_probe = _MODULE._score_negative_probe
_score_probe = _MODULE._score_probe


def test_eval_refuses_configured_production_database(tmp_path: Path) -> None:
    production = tmp_path / "production.sqlite"

    with pytest.raises(ValueError, match="configured production"):
        assert_safe_eval_paths(
            database=production,
            output=tmp_path / "results.jsonl",
            production_database=production,
        )


def test_eval_refuses_output_aliasing_scratch_database(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch.sqlite"

    with pytest.raises(ValueError, match="output must not overwrite"):
        assert_safe_eval_paths(
            database=scratch,
            output=scratch,
            production_database=tmp_path / "production.sqlite",
        )


def test_eval_refuses_existing_or_non_scratch_database(tmp_path: Path) -> None:
    existing = tmp_path / "existing.sqlite"
    existing.touch()

    with pytest.raises(ValueError, match="new scratch path"):
        assert_safe_eval_paths(
            database=existing,
            output=tmp_path / "results.jsonl",
            production_database=tmp_path / "production.sqlite",
        )
    with pytest.raises(ValueError, match="temporary root"):
        assert_safe_eval_paths(
            database=Path("/opt/not-a-scratch-ledger.sqlite"),
            output=tmp_path / "results.jsonl",
            production_database=tmp_path / "production.sqlite",
        )


def test_active_memory_candidate_counts_as_retrieval_not_dialogue_tail() -> None:
    model_context = {
        "slices": {
            "relevant_facts": {"items": []},
            "active_memory_candidates": {
                "items": [{"source_excerpts": [{"text": "最爱桂花乌龙"}]}]
            },
            "recent_dialogue": {"items": [{"text": "想喝点热的"}]},
        }
    }
    raw = json.dumps(model_context, ensure_ascii=False)
    memory = _memory_slice_text(raw)

    scored = _score_probe(
        {"expect_any": ["桂花乌龙"], "context_any": ["桂花乌龙"]},
        "那就泡桂花乌龙吧",
        raw,
        memory,
    )

    assert scored["verdict"] == "text_matched_from_memory"
    assert scored["semantic_correctness"] == "unassessed"
    assert scored["in_memory"] is True


def test_negative_probe_separates_wrong_injection_from_visible_expression() -> None:
    scored = _score_negative_probe(
        {"forbid_any": ["林星", "美术社"]},
        "窗外有点阴。",
        "旧候选：林星最近高三。",
    )

    assert scored["forbidden_text_in_memory"] is True
    assert scored["forbidden_text_in_reply"] is False
    assert scored["text_control_pass"] is False
    assert scored["semantic_correctness"] == "unassessed"


@pytest.mark.parametrize(("memory", "reply", "term"), [
    ("用户的妹妹林星今年高三。", "你今年高三，最近学业压力很大。", "高三"),
    ("用户喝咖啡会心悸。", "你喝咖啡不会心悸。", "心悸"),
])
def test_text_match_does_not_claim_subject_or_negation_correctness(memory, reply, term):
    result = _score_probe({"expect_any": [term], "context_any": [term]}, reply, memory, memory)
    assert result["verdict"] == "text_matched_from_memory"
    assert result["score_contract"] == "memory-recall-text-match.2"
    assert result["semantic_correctness"] == "unassessed"


def test_current_snapshot_memory_excludes_dialogue_and_trigger_echo():
    snapshot = {"materials": {
        "recent_dialogue": [{"text": "咖啡馆新开了"}],
        "relevant_facts": [{"text": "最爱桂花乌龙"}],
        "automatic_prefetch": {"items": [{"text": "妹妹林星"}]},
    }}
    raw = json.dumps(snapshot, ensure_ascii=False)
    memory = _memory_slice_text(raw)
    assert "桂花乌龙" in memory and "林星" in memory
    assert "咖啡馆" not in memory
    assert _MODULE._slice_stats(raw)["slices"]["relevant_facts"]["items"] == 1


def test_cache_split_dialogue_count_uses_both_stable_and_volatile_turns():
    raw = json.dumps({"materials": {"recent_dialogue": {
        "stable_turns": [{"text": "旧话题"}, {"text": "旧回应"}],
        "volatile_last_turn": {"text": "当前提问"},
    }}}, ensure_ascii=False)
    assert _MODULE._slice_stats(raw)["slices"]["recent_dialogue"]["items"] == 3
    assert _memory_slice_text(raw) == "[]"


def test_current_experience_material_is_not_reported_as_empty_legacy_slice():
    raw = json.dumps({"materials": {"recent_self_experiences": [{
        "source_ref": "occurrence:fixture", "context_kind": "settled_world_occurrence",
        "text": "An accepted environment fragment.",
    }]}})
    stats = _MODULE._slice_stats(raw)["slices"]
    assert stats["recent_self_experiences"]["items"] == 1
    assert stats["recent_experiences"]["availability"] == "unavailable"
    assert stats["recent_experiences"]["items"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", [
    "complete_with_usage", "complete_json_with_usage", "complete_json_stream_with_usage",
])
async def test_recording_model_observes_only_supported_metered_interfaces(method):
    from types import SimpleNamespace

    result = ("fixture response", {"input_tokens": 1})
    calls = []

    async def metered(messages, **kwargs):
        calls.append((messages, kwargs))
        return result

    provider = SimpleNamespace(**{method: metered}, supports_required_tool_choice=True)
    model = _MODULE.RecordingModel(provider)
    assert model.supports_required_tool_choice is True
    assert hasattr(model, "complete_json_stream_with_usage") == (
        method == "complete_json_stream_with_usage"
    )
    snapshot = {"materials": {"relevant_facts": [{"text": "桂花乌龙"}]}}
    messages = [{"role": "system", "content": "fixture"}, {
        "role": "user", "content": json.dumps({"inner_life_snapshot": snapshot}),
    }]
    assert await getattr(model, method)(messages, temperature=0.2) is result
    assert len(calls) == 1 and calls[0][1] == {"temperature": 0.2}
    assert [json.loads(value) for value in model.captures] == [snapshot]
    assert model.outputs == ["fixture response"]


@pytest.mark.asyncio
async def test_stub_eval_uses_current_stream_and_commits_fact_memory(tmp_path, monkeypatch):
    import httpx

    def blocked(*args, **kwargs):
        raise AssertionError("offline eval must not issue HTTP requests")

    async def async_blocked(*args, **kwargs):
        raise AssertionError("offline eval must not issue HTTP requests")

    monkeypatch.setattr(httpx.Client, "send", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_blocked)
    rows = await _MODULE.run(
        database=tmp_path / "world.sqlite",
        fixture_path=Path(__file__).parent / "fixtures" / "memory_recall_30_turns.json",
        output=tmp_path / "result.jsonl",
        stub=True, fast=True, gap_threshold_minutes=120, gap_ticks=8,
        background_units=1, turn_limit=3,
    )
    turns = [row for row in rows if "turn_id" in row]
    assert len(turns) == 3
    assert all(row["status"] == "action_authorized" and row["replies"] for row in turns)
    assert all(row["model_facing"]["status"] == "ok" for row in turns)
    assert rows[-1]["semantic_correctness"] == "unassessed"
    # run() also requires the real Fact -> MemoryCandidate acceptance chain
    # to match every plant; this used to fail after all thirty silent turns.
