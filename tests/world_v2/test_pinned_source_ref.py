from __future__ import annotations

from companion_daemon.world_v2.pinned_source_ref import (
    PinnedSourceCatalog,
    resolve_pinned_source_ref,
    resolve_pinned_source_ref_list,
    unpinned_source_failure_detail,
)

_OBS_HASH = "544acb33c0ffee" + "ab" * 28
_PLAN_HASH = "8c27ad02c0ffee" + "cd" * 28
_BEAT_HASH = "b7e7e7e7c0ffee" + "ef" * 28

CANONICAL_OBSERVATION = (
    f"dialogue:observation:observation:qq:2759284998:qq-coalesced:{_OBS_HASH}"
)
CANONICAL_BEAT_2 = (
    f"dialogue:expression:plan:expression:{_PLAN_HASH}:beat:chat-reply:{_BEAT_HASH}:2"
)
CANONICAL_BEAT_1 = (
    f"dialogue:expression:plan:expression:{_PLAN_HASH}:beat:chat-reply:{_BEAT_HASH}:1"
)
CANONICAL_ADVISORY = f"advisory:revisit-intention:{_PLAN_HASH[:16]}"


def _catalog(*refs: str) -> PinnedSourceCatalog:
    return PinnedSourceCatalog.from_refs(refs)


def test_exact_and_short_token_are_unique() -> None:
    catalog = _catalog(CANONICAL_OBSERVATION, CANONICAL_BEAT_2, CANONICAL_ADVISORY)
    assert catalog.token_to_ref["s0"] == CANONICAL_OBSERVATION
    exact = resolve_pinned_source_ref(CANONICAL_OBSERVATION, catalog)
    token = resolve_pinned_source_ref("s1", catalog)
    assert exact.status == "unique" and exact.source_ref == CANONICAL_OBSERVATION
    assert token.status == "unique" and token.source_ref == CANONICAL_BEAT_2


def test_doubled_observation_segment_collapses_when_unique() -> None:
    # Canonical production ids already contain one extra "observation" because
    # dialogue_id is dialogue:observation:{observation_id} and observation_id
    # itself starts with observation:.  An extra doubling is copy damage.
    catalog = _catalog(CANONICAL_OBSERVATION)
    extra = (
        "dialogue:observation:observation:observation:qq:2759284998:"
        f"qq-coalesced:{_OBS_HASH}"
    )
    outcome = resolve_pinned_source_ref(extra, catalog)
    assert outcome.status == "unique"
    assert outcome.source_ref == CANONICAL_OBSERVATION


def test_missing_beat_slot_matches_unique_trailing_index() -> None:
    catalog = _catalog(CANONICAL_BEAT_1, CANONICAL_BEAT_2)
    written = f"dialogue:expression:plan:expression:{_PLAN_HASH}:2"
    outcome = resolve_pinned_source_ref(written, catalog)
    assert outcome.status == "unique"
    assert outcome.source_ref == CANONICAL_BEAT_2


def test_missing_beat_does_not_guess_when_trailing_index_is_absent() -> None:
    catalog = _catalog(CANONICAL_BEAT_1, CANONICAL_BEAT_2)
    written = f"dialogue:expression:plan:expression:{_PLAN_HASH}:3"
    outcome = resolve_pinned_source_ref(written, catalog)
    assert outcome.status != "unique"
    assert outcome.source_ref is None


def test_truncated_hash_is_unique_only_when_one_catalog_item_fits() -> None:
    catalog = _catalog(CANONICAL_OBSERVATION, CANONICAL_ADVISORY)
    outcome = resolve_pinned_source_ref(
        f"dialogue:observation:qq:2759284998:qq-coalesced:{_OBS_HASH[:12]}",
        catalog,
    )
    assert outcome.status == "unique"
    assert outcome.source_ref == CANONICAL_OBSERVATION


def test_shared_plan_hash_without_index_is_ambiguous() -> None:
    catalog = _catalog(CANONICAL_BEAT_1, CANONICAL_BEAT_2)
    outcome = resolve_pinned_source_ref(_PLAN_HASH[:16], catalog)
    assert outcome.status in {"unknown", "ambiguous"}
    assert outcome.source_ref is None


def test_unknown_ref_stays_unresolved() -> None:
    catalog = _catalog(CANONICAL_ADVISORY)
    outcome = resolve_pinned_source_ref("dialogue:invented:nope", catalog)
    assert outcome.status == "unknown"
    restored, failures = resolve_pinned_source_ref_list(
        ["dialogue:invented:nope", CANONICAL_ADVISORY],
        catalog,
    )
    assert restored == ["dialogue:invented:nope", CANONICAL_ADVISORY]
    assert len(failures) == 1


def test_duplicate_damaged_writings_collapse_to_one_canonical() -> None:
    catalog = _catalog(CANONICAL_BEAT_2)
    written = f"dialogue:expression:plan:expression:{_PLAN_HASH}:2"
    restored, failures = resolve_pinned_source_ref_list(
        [written, "s0", CANONICAL_BEAT_2],
        catalog,
    )
    assert restored == [CANONICAL_BEAT_2]
    assert failures == ()


def test_inbound_alias_table_uniquely_restores_missing_beat_without_minting_s0() -> None:
    from companion_daemon.world_v2.expression_draft import SourceRefAliasTable

    catalog_refs = (CANONICAL_BEAT_1, CANONICAL_BEAT_2)
    aliases = SourceRefAliasTable(
        entries=(("S1", CANONICAL_BEAT_1), ("T1", CANONICAL_ADVISORY)),
        canonical_refs=frozenset((*catalog_refs, CANONICAL_ADVISORY)),
    )
    written = f"dialogue:expression:plan:expression:{_PLAN_HASH}:2"
    assert aliases.expand(written) == CANONICAL_BEAT_2
    assert aliases.expand("S1") == CANONICAL_BEAT_1
    resolution_catalog = PinnedSourceCatalog.for_resolution(
        catalog_refs,
        tokens=dict(aliases.entries),
    )
    assert "s0" not in resolution_catalog.token_to_ref


def test_failure_detail_lists_illegal_and_available_refs_in_chinese() -> None:
    catalog = _catalog(CANONICAL_OBSERVATION, CANONICAL_BEAT_2)
    _, failures = resolve_pinned_source_ref_list(
        ["dialogue:expression:plan:expression:deadbeef:9"],
        catalog,
    )
    detail = unpinned_source_failure_detail(
        code="attended_source_unpinned",
        field="attended_source_refs",
        failures=failures,
        catalog=catalog,
    )
    assert "attended_source_refs" in detail
    assert "deadbeef" in detail
    assert "s0" in detail
    assert "不要手写拼接" in detail
    assert "点名哪些" in detail
