"""Initial story lookup must search displayed prose, not Context bookkeeping."""

from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.recall_index import InMemoryRecallIndex
from test_recall_short_cues import LexicalOnly, corpus
from test_recall_index import CURSOR, _query
from test_world_outlook import draft_value, setup


def _item(value, privacy="personal"):
    return {"item_ref": "metadata:never-search", "privacy_class": privacy, "value": value}


def _context(topic):
    return {
        "actor_ref": "metadata:" + "a" * 3000,
        "slices": {
            "world_life": {"availability": "available", "items": [
                _item({"content": {"privacy_class": "personal", "world_consequence": {
                    "environment": {"text": f"校园社团发出了{topic}活动的通知。"}
                }}}),
            ]},
        },
    }


def _capture(runtime, ledger, context, threads=()):
    calls = []
    runtime._history_reader = lambda **kw: calls.append(kw) or ()
    runtime._history_material(ledger.project(), context, {"threads": threads}, "trigger:test")
    assert len(calls) == 1
    return calls[0]["query_text"]


@pytest.mark.parametrize("topic", ["校刊", "合唱", "木工", "潜水", "陶艺", "围棋"])
def test_initial_outlook_query_recovers_the_related_history_with_a_fixed_index(tmp_path, topic):
    ledger, _, _, _, runtime = setup(tmp_path, lambda _: [])
    context = _context(topic)
    before = deepcopy(context)
    try:
        query = _capture(runtime, ledger, context)
        assert topic in query
        assert "metadata:" not in query
        index = InMemoryRecallIndex(embedding=LexicalOnly())
        index.rebuild(cursor=CURSOR, documents=corpus(topic))
        old_query = json.dumps(context, ensure_ascii=False)[:1024]
        assert not index.search(_query(query_text=old_query, limit=2)).hits
        assert index.search(_query(query_text=query, limit=2)).hits[0].document.document_id == "remembered"
        assert context == before
    finally:
        runtime.close()


def test_query_budget_keeps_each_visible_source_and_excludes_hidden_or_metadata_text(tmp_path):
    ledger, _, _, _, runtime = setup(tmp_path, lambda _: [])
    context = _context("陶艺")
    context["slices"]["world_life"]["items"] = [
        _item({"content": {"text": "很长的生活记录" * 500}}),
        _item({"premise": {"text": "合唱团正在征集旧照片。"}}),
        _item({"accepted_intention": {"text": "想修好以前的木工作品。"}}),
        _item({"content": {"text": "WITHHELD"}}, privacy="withhold"),
        _item({"content": {"text": "NESTED_WITHHELD", "privacy_class": "withhold"}}),
        _item({"metadata": {"text": "META_SENTINEL"}, "source_ref": "SOURCE_SENTINEL"}),
    ]
    context["slices"]["recent_dialogue"] = {"availability": "unavailable", "items": [_item({"text": "UNAVAILABLE"})]}
    context["slices"]["relevant_facts"] = {"availability": "available", "items": [_item({"source_excerpt": "以前学过围棋。"})]}
    try:
        query = _capture(runtime, ledger, context)
        assert len(query) <= 1024
        assert all(word in query for word in ("合唱", "木工", "围棋"))
        assert all(word not in query for word in ("WITHHELD", "SENTINEL", "UNAVAILABLE"))
    finally:
        runtime.close()


def test_ongoing_outlook_search_reads_current_life_alongside_authored_threads(tmp_path):
    ledger, _, _, _, runtime = setup(tmp_path, lambda _: [])
    threads = [{"situation": "合唱团重聚", "open_question": "旧照片在哪里？"}]
    try:
        query = _capture(runtime, ledger, _context("陶艺"), threads)
        assert "陶艺" in query and "合唱团重聚 旧照片在哪里？" in query
        assert query != _capture(runtime, ledger, {"slices": {}}, threads)
        assert _capture(runtime, ledger, {"slices": {}}, threads) == "合唱团重聚 旧照片在哪里？"
        assert _capture(runtime, ledger, {"slices": {}}) == "生活"
    finally:
        runtime.close()


def test_closed_or_long_prior_threads_cannot_hide_current_life_from_search(tmp_path):
    ledger, _, _, _, runtime = setup(tmp_path, lambda _: [])
    threads = [
        {"situation": "旧记录" * 1000, "open_question": "未决问题？"},
        {"situation": "CLOSED_SENTINEL", "open_question": "已结束。", "status": "closed"},
    ]
    context = _context("陶艺")
    before = deepcopy((context, threads))
    try:
        query = _capture(runtime, ledger, context, threads)
        assert len(query) <= 1024 and "陶艺" in query and "旧记录" in query
        assert "CLOSED_SENTINEL" not in query
        assert (context, threads) == before
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("preparation", ["history_lookup", "request_size"])
@pytest.mark.parametrize("corrected", [False, True])
async def test_saved_response_recovers_without_preparing_a_new_query_or_request(
    tmp_path, monkeypatch, preparation, corrected,
):
    from companion_daemon.world_v2 import world_outlook_runtime as module
    from companion_daemon.world_v2.world_outlook import digest
    from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore
    from test_life_development_runtime import OWNER, WORLD_ID, _manifest, _projection_cursor

    class Interrupted(BaseException):
        pass

    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: ([{"invalid": True}] if corrected else [])
        + [{"decision": "revise", "draft": draft_value(w)}],
    )
    kwargs = dict(projection=ledger.project(), context=_context("陶艺"),
                  manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))

    def interrupt_install(*args, **kwargs):
        raise Interrupted()

    try:
        with monkeypatch.context() as patch:
            patch.setattr(store, "install", interrupt_install)
            with pytest.raises(Interrupted):
                await runtime.refresh(**kwargs)
        original_request_hash = digest(author.calls[-1])
        store.close()
        runtime.store = SQLiteWorldOutlookStore(
            path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref=OWNER,
        )

        def must_not_prepare(*args, **kwargs):
            raise ValueError("new query unavailable or new request would exceed 100000 bytes")

        if preparation == "history_lookup":
            monkeypatch.setattr(runtime, "_history_material", must_not_prepare)
        else:
            monkeypatch.setattr(module, "encode_outlook_request", must_not_prepare)
        assert (await runtime.refresh(**kwargs))["status"] == "ready"
        assert len(author.calls) == (2 if corrected else 1)
        assert runtime.store.head()[2]["request_hash"] == original_request_hash
        assert ledger.project() == kwargs["projection"]
    finally:
        runtime.close()
