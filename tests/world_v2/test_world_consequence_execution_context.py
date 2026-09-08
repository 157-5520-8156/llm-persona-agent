"""Readable execution scope comes from the original role's accepted intention."""

from __future__ import annotations

import pytest

import current_activity_fixture
from current_activity_fixture import accepted_current_activity
from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore


@pytest.mark.asyncio
async def test_open_life_execution_reads_full_role_intention_without_world_author_candidates(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    intention = "我想仔细读公告里的投稿要求。" * 50
    monkeypatch.setattr(current_activity_fixture, "CURRENT_ACTIVITY_INTENTION", intention)
    ledger, store, _, source_ref = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    from companion_daemon.world_v2.world_consequence_execution_context import (
        build_world_consequence_execution_materials,
    )

    source = ledger.lookup_event_commit(source_ref)[0]
    values = build_world_consequence_execution_materials(
        ledger=ledger, content_store=store, pinned_state=ledger.project(),
        actor_ref="actor:companion", source_events=(source,),
    )
    assert len(values) == 1 and values[0].status == "available"
    material = values[0]
    assert material.authorized_intention.text == intention
    assert len(material.authorized_intention.text) > 480
    assert material.authorized_intention.model_result_ref
    assert material.authorized_intention.proposal_event_ref
    rendered = material.model_dump_json()
    assert current_activity_fixture.UNSETTLED_OUTCOME_TEXT not in rendered
    assert current_activity_fixture.CURRENT_ACTIVITY_PREMISE not in rendered
    assert material.authorized_intention.epistemic_scope == (
        "authorized_intention_not_embedded_history_or_execution_success"
    )


def test_receipt_has_no_readable_action_scope_without_a_content_reader(tmp_path):
    from companion_daemon.world_v2.world_consequence_execution_context import (
        build_world_consequence_execution_materials,
    )
    from test_world_consequence_authority import _receipt_case

    ledger, _, case = _receipt_case(tmp_path)
    values = build_world_consequence_execution_materials(
        ledger=ledger, content_store=InMemoryImmutableLifeContentStore(),
        pinned_state=ledger.project(), actor_ref="actor:companion",
        source_events=case["source_events"],
    )
    assert values[0].status == "unavailable"
    assert values[0].unavailable_reason == "receipt_content_reader_not_installed"
    assert values[0].authorized_intention is None
