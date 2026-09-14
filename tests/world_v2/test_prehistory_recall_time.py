"""Dating uncertainty affects access, never the source's date or authority."""

from datetime import UTC, datetime, timedelta

import pytest

from companion_daemon.world_v2.prehistory_memory_source import PrehistoryMemoryReading
from companion_daemon.world_v2.recall_index import (
    FeatureHashRecallEmbedding,
    InMemoryRecallIndex,
    RecallCursor,
    RecallDocument,
    RecallQuery,
    RecallSourceBinding,
    SQLiteRecallIndex,
    _historical_window_accessibility,
)

NOW = datetime(2026, 9, 14, tzinfo=UTC)
CURSOR = RecallCursor(world_revision=7, deliberation_revision=0, ledger_sequence=10)
ACTOR = "agent:companion"


def document(name, *, youngest=1, oldest=7300):
    scope = PrehistoryMemoryReading(
        actor_ref=ACTOR,
        occurred_from=NOW - timedelta(days=oldest),
        occurred_until=NOW - timedelta(days=youngest),
        time_precision="interval",
        timezone_name="Asia/Shanghai",
        world_started_at=NOW,
        participant_refs=(),
        location_ref=None,
        related_record_refs=(),
        entities=(),
        archive_event_ref="event:archive",
        archive_world_revision=6,
        archive_payload_hash="a" * 64,
    )
    bindings = (
        RecallSourceBinding(
            source_kind="committed_event",
            authority_type="CharacterPrehistoryArchiveAccepted",
            ref="event:archive",
            source_world_revision=6,
            immutable_hash="a" * 64,
        ),
        RecallSourceBinding(
            source_kind="committed_event",
            authority_type="CharacterPrehistoryRecordImported",
            ref="event:" + name,
            source_world_revision=7,
            immutable_hash="b" * 64,
        ),
    )
    return RecallDocument(
        document_id="recall:" + name,
        memory_kind="episodic",
        source_item_ref=name,
        source_slice="active_memory_candidates",
        source_refs=tuple(sorted(b.ref for b in bindings)),
        source_bindings=bindings,
        source_world_revision=7,
        text="在学校借过一本书。",
        actor_ref=ACTOR,
        subject_refs=(ACTOR,),
        link_refs=(),
        occurred_from=scope.occurred_from,
        occurred_to=scope.occurred_until,
        privacy_class="private",
        epistemic_scope="character_prehistory",
        prehistory=scope,
    )


def query(**changes):
    return RecallQuery(
        query_text="学校借书",
        cursor=CURSOR,
        actor_ref=ACTOR,
        subject_refs=(ACTOR,),
        viewer_privacy_ceiling="private",
        at=NOW,
        accessibility_seed="fixture:time",
        **changes,
    )


@pytest.mark.parametrize(
    "young,old",
    [
        (1, 1),
        (1, 31),
        (30, 365),
        (1, 7300),
        (569, 571),
        (1000, 7300),
        (0, 0.000001),
        (1, 1.000000001),
    ],
)
def test_window_mean_matches_independent_numeric_integration(young, old):
    # Midpoint quadrature is independent of the production analytic integral.
    count = 20000
    expected = (
        sum(
            max(500, 10000 / (1 + (young + (i + 0.5) * (old - young) / count) / 30))
            for i in range(count)
        )
        / count
    )
    assert (
        abs(_historical_window_accessibility(youngest_days=young, oldest_days=old) - expected) <= 1
    )


def test_less_precise_past_window_cannot_gain_recentness():
    scores = [
        _historical_window_accessibility(youngest_days=1, oldest_days=old)
        for old in (1, 7, 30, 365, 7300)
    ]
    assert scores == sorted(scores, reverse=True)
    assert scores[-1] < 1000 < scores[0]


@pytest.mark.parametrize("persistent", [False, True])
def test_uncertain_old_memory_does_not_outrank_recent_precise_memory(tmp_path, persistent):
    docs = (document("vague"), document("recent", youngest=7, oldest=7))
    factory = (
        (
            lambda: SQLiteRecallIndex(
                path=tmp_path / "recall.sqlite",
                world_id="world:time-fixture",
                embedding=FeatureHashRecallEmbedding(),
            )
        )
        if persistent
        else (lambda: InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding()))
    )
    index = factory()
    try:
        index.rebuild(cursor=CURSOR, documents=docs)
        result = index.search(query())
        assert result.hits[0].document.source_item_ref == "recent"
        assert {h.document.source_item_ref: h.document for h in result.hits} == {
            d.source_item_ref: d for d in docs
        }
        assert "hybrid.6" in result.index_version
        if persistent:
            index.close()
            index = factory()
            assert index.search(query()).result_hash == result.result_hash
    finally:
        if persistent:
            index.close()


def test_temporal_filter_and_privacy_still_gate_uncertain_history():
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=CURSOR, documents=(document("old", youngest=1000, oldest=2000),))
    assert not index.search(query(occurred_from=NOW - timedelta(days=30))).hits
    assert not index.search(query().model_copy(update={"viewer_privacy_ceiling": "public"})).hits
    assert index.search(query(occurred_from=NOW - timedelta(days=1500))).hits


def test_recorded_current_activity_interval_keeps_its_observed_end():
    original = document("activity", youngest=7, oldest=365)
    lived = RecallDocument.model_validate(
        original.model_copy(
            update={
                "prehistory": None,
                "epistemic_scope": "world_fact",
                "source_slice": "recent_experiences",
            }
        ).model_dump()
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=CURSOR, documents=(lived,))
    (hit,) = index.search(query()).hits
    assert hit.temporal_score_bp == round(10000 / (1 + 7 / 30))
