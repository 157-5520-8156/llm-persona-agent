from __future__ import annotations

from datetime import UTC, datetime, timedelta

from companion_daemon.world_v2.aspiration_view import active_aspiration_advisories
from companion_daemon.world_v2.life_development_runtime import compile_pressure_surfaces
from companion_daemon.world_v2.schemas import AspirationProjection, LedgerProjection
from test_pressure_surfaces import _manifest


NOW = datetime(2026, 8, 1, 8, tzinfo=UTC)


def _aspiration(**updates: object) -> AspirationProjection:
    return AspirationProjection.model_validate(
        {
            "aspiration_id": "aspiration:study",
            "entity_revision": 1,
            "owner_actor_ref": "actor:companion",
            "seed_id": "character-interior:study",
            "origin_kind": "character_authored",
            "text": "想认真考虑出国读书。",
            "privacy_class": "private",
            "planted_at": NOW,
            "planted_event_ref": "event:aspiration:planted",
            "source_event_ref": "event:experience:study",
            **updates,
        }
    )


def _projection(*aspirations: AspirationProjection) -> LedgerProjection:
    return LedgerProjection(
        world_id="world:aspiration-view",
        world_revision=5,
        deliberation_revision=0,
        ledger_sequence=8,
        logical_time=NOW + timedelta(days=14),
        aspirations=aspirations,
        semantic_hash="a" * 64,
    )


def test_revised_wish_has_current_authority_and_does_not_backdate_its_new_meaning() -> None:
    original = _aspiration()
    revised = _aspiration(
        entity_revision=2,
        text="我现在更想先在本地工作一年，再考虑读书。",
        revision_event_ref="event:aspiration:revised",
        last_revised_at=NOW + timedelta(days=13),
    )

    old = active_aspiration_advisories(_projection(original))[0]
    current = active_aspiration_advisories(_projection(revised))[0]

    assert current.source_refs == ("event:aspiration:revised",)
    assert current.candidate_refs != old.candidate_refs
    assert current.advisory_id != old.advisory_id
    summary = current.candidates[0].value
    assert revised.text in summary
    assert "2026-08-14" in summary
    assert "修订" in summary
    assert "心里存了大约 14 天" not in summary


def test_life_pressure_keeps_stable_wish_identity_separate_from_current_meaning_source() -> None:
    revised = _aspiration(
        entity_revision=2,
        text="我现在更想先在本地工作一年，再考虑读书。",
        revision_event_ref="event:aspiration:revised",
        last_revised_at=NOW + timedelta(days=13),
    )
    surfaces = compile_pressure_surfaces(
        manifest=_manifest(npc_plans=()),
        context={},
        projection=_projection(revised),
    )
    reading = surfaces["active_aspirations"][0]
    assert reading["source_ref"] == "event:aspiration:revised"
    assert reading["planted_event_ref"] == "event:aspiration:planted"
    assert reading["last_revised_at"] == "2026-08-14T08:00:00+00:00"
    assert reading["text"] == revised.text


def test_recent_revision_is_not_hidden_behind_more_recent_planting_dates() -> None:
    revised = _aspiration(
        entity_revision=2,
        text="现在的我更想先工作。",
        revision_event_ref="event:aspiration:revised",
        last_revised_at=NOW + timedelta(days=13),
    )
    other_wishes = tuple(
        _aspiration(
            aspiration_id=f"aspiration:other:{ordinal}",
            planted_at=NOW + timedelta(days=ordinal),
            planted_event_ref=f"event:aspiration:other:{ordinal}",
        )
        for ordinal in (1, 2, 3)
    )
    reading = active_aspiration_advisories(_projection(revised, *other_wishes))[0]
    assert reading.source_refs[0] == "event:aspiration:revised"


def test_reinforcement_keeps_current_text_authority_and_abandonment_removes_the_wish() -> None:
    reinforced = _aspiration(
        entity_revision=3,
        text="很" * 239 + "慢",
        revision_event_ref="event:aspiration:revised",
        last_revised_at=NOW + timedelta(days=10),
        reinforcement_count=1,
        last_reinforced_at=NOW + timedelta(days=13),
    )
    reading = active_aspiration_advisories(_projection(reinforced))[0]
    assert reading.source_refs == ("event:aspiration:revised",)
    assert reinforced.text in reading.candidates[0].value
    assert "2026-08-11 修订" in reading.candidates[0].value

    abandoned = reinforced.model_copy(
        update={
            "status": "abandoned",
            "abandoned_at": NOW + timedelta(days=14),
            "abandonment_event_ref": "event:aspiration:abandoned",
            "abandonment_summary": "我已不再把它当作自己的方向。",
            "abandonment_source_refs": ("event:experience:changed-direction",),
        }
    )
    assert active_aspiration_advisories(_projection(abandoned)) == ()
    assert compile_pressure_surfaces(
        manifest=_manifest(npc_plans=()), context={}, projection=_projection(abandoned),
    )["active_aspirations"] == []
