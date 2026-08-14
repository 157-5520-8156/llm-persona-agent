from __future__ import annotations

from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.character_interior.production import (
    _CharacterInteriorBackgroundDriver,
)
from companion_daemon.world_v2.present_prompt import compile_slim_consider_payload
from companion_daemon.world_v2.private_impression_producer import (
    compile_paid_private_impression_draft,
)


def test_slim_stuck_with_me_compiles_into_a_private_impression_draft() -> None:
    compiled = compile_slim_consider_payload(
        {
            "messages": ["嗯"],
            "felt": "心里还搁着刚才那句",
            "stuck_with_me": "他说完我就一直在想他到底怎么看我",
            "wants": "想把这件事慢慢看清楚",
            "photo": False,
        }
    )

    assert compiled is not None
    assert compiled["expression_draft"]["impulse_summary"] == "想把这件事慢慢看清楚"
    draft = compile_paid_private_impression_draft(
        reflection_summary=str(
            compiled["expression_draft"]["private_turn_state"]["inner_state_summary"]
        ),
        offered_source_refs=("event:observation:1", "event:appraisal:1"),
    )
    assert draft is not None
    assert draft.decision == "retain"
    assert draft.reflection_summary == "他说完我就一直在想他到底怎么看我"
    assert draft.source_refs == ("event:observation:1", "event:appraisal:1")
    assert draft.predecessor_refs == ()


def test_empty_stuck_with_me_does_not_open_an_impression() -> None:
    assert (
        compile_paid_private_impression_draft(
            reflection_summary="   ",
            offered_source_refs=("event:observation:1",),
        )
        is None
    )
    assert (
        compile_paid_private_impression_draft(
            reflection_summary="还搁着",
            offered_source_refs=(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_production_private_impression_drain_does_not_call_the_model() -> None:
    called: list[str] = []
    driver = object.__new__(_CharacterInteriorBackgroundDriver)
    driver._private_impression = SimpleNamespace(
        advance_due_once=lambda: called.append("advance") or SimpleNamespace(status="accepted")
    )
    driver._private_impression_opener = SimpleNamespace(
        open_once=lambda: called.append("open")
    )

    result = await driver.drain_private_impression_once()

    assert result is None
    assert called == []
