"""The daemon's real offline provider must support its installed day opportunity."""

from datetime import timedelta
import json
import sqlite3

import pytest

from companion_daemon.llm import FakeCompanionModel
from companion_daemon.world_v2.character_interior.inbound_author import (
    _InboundCharacterAuthor as InboundCharacterAuthor,
)
from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from test_character_interior_inbound_author import _request
from test_day_open_self_directed_intent import build_app  # noqa: F401
from test_world_stimulus_life_intent import NOW


@pytest.mark.asyncio
async def test_daemon_fake_handles_actual_empty_day_open_without_degrading(
    tmp_path,
    build_app,  # noqa: F811 - imported fixture
):
    model = FakeCompanionModel()
    model.model = "offline-daemon-fixture"
    path = tmp_path / "fake-day.sqlite"
    app = build_app(path, model, ecology=True)
    try:
        at = NOW + timedelta(minutes=1)
        await app.tick(
            tick_id="fake-day",
            logical_time_from=NOW,
            logical_time_to=at,
            observed_at=at,
            trace_id="trace:fake-day",
            causation_id="clock:fake-day",
            correlation_id="fake-day",
            reason="test_clock",
        )
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            journal = json.loads(
                connection.execute("SELECT body FROM world_v2_day_open_opportunities").fetchone()[0]
            )
        assert journal["terminal_reason"] == "role_no_op", journal
        assert len(model.calls) == 1
        assert app.export_replay_evidence().projection.plans == ()
    finally:
        app.close()


@pytest.mark.asyncio
async def test_fake_required_tool_advertisement_preserves_actual_chat_head_and_tail():
    model = FakeCompanionModel()
    author = InboundCharacterAuthor(
        flash_model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
            update={"private_turn_state_mode": "required"}
        ),
        require_explicit_authored_decision_fields=True,
    )
    request = _request(revision=3, call="call:fake-inbound")
    head = await author.propose_stream_head(request)
    tail = await author.propose_stream_tail(
        request.model_copy(update={"call_id": "call:fake-inbound-tail"})
    )
    assert head.semantic_stream_part == "head"
    assert tail.semantic_stream_part == "tail"
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["initial", "after_recall", "final"])
async def test_fake_atomic_chat_tools_keep_full_canonical_fixture(phase):
    model = FakeCompanionModel()
    author = InboundCharacterAuthor(flash_model=model)
    # Obtain actual author messages, without introducing a fake prompt format.
    await author.propose(_request(revision=3, call="call:fake-atomic"))
    messages = model.calls[0]
    original = json.loads(await model.complete(messages))
    contract = InboundToolContracts().contract_for(
        phase=phase,
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=False,
    )
    raw = await model.complete_json(
        messages,
        tools=list(contract.provider_tools),
        tool_choice=contract.provider_tool_choice,
    )
    decoded = json.loads(contract.unwrap(raw))
    assert decoded["expression_draft"] == original["expression_draft"]
    assert decoded["appraisal_draft"] == {**original["appraisal_draft"], "affect": "no_change"}


@pytest.mark.asyncio
async def test_fake_unknown_tool_is_explicit_failure_and_legacy_complete_is_unchanged():
    model = FakeCompanionModel()
    messages = [{"role": "user", "content": "hello"}]
    assert await model.complete(messages) == "刚刚是不是忙完了？我在呢。"
    assert await model.complete_json(messages) == "刚刚是不是忙完了？我在呢。"
    with pytest.raises(ValueError, match="does not implement tool"):
        await model.complete_json(
            messages,
            tools=[{"type": "function", "function": {"name": "unimplemented_author"}}],
            tool_choice={"type": "function", "function": {"name": "unimplemented_author"}},
        )
    assert len(model.calls) == 2
