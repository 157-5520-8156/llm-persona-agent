from __future__ import annotations

from pathlib import Path

from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.local_chronology import LocalChronology


def test_production_seed_can_open_a_life_arc_from_a_settled_outcome() -> None:
    catalog = ReviewedLifeSeedCatalog.from_yaml(
        path=Path("configs/world_seed.yaml"),
        chronology=LocalChronology("Asia/Shanghai"),
    )

    effect = catalog.life_arc_effect_for_settlement(
        activity_kind="career.publishing_intern_interview",
        candidate_result_ref="candidate:any:publishing-interview-offer",
    )
    declined = catalog.life_arc_effect_for_settlement(
        activity_kind="career.publishing_intern_interview",
        candidate_result_ref="candidate:any:publishing-interview-no-fit",
    )
    frozen = catalog.frozen_life_arc_effect_for_outcome(
        activity_kind="career.publishing_intern_interview",
        outcome_id="publishing-interview-offer",
    )

    assert catalog.version == "reviewed-life.14"
    assert effect is not None
    assert effect.context_tags == ("role:intern", "workplace:publishing")
    assert effect.duration_days == 30
    assert declined is None
    assert frozen is not None
    assert frozen.context_pack_ref == "life-context:publishing-internship"
    assert frozen.descriptor_hash == frozen.canonical_hash()
