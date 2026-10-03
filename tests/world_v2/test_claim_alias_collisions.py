from companion_daemon.world_v2.expression_draft import (
    SourceRefAliasTable,
    expand_expression_source_ref_aliases,
)


def test_distinct_offered_handles_for_one_source_do_not_create_invalid_duplicates():
    ref = "event:memory:accepted"
    aliases = SourceRefAliasTable(entries=(("S1", ref), ("S2", ref)), canonical_refs=frozenset({ref}))
    value = {"world_claims": [{"scope": "past_world", "source_refs": ["S1", "S2", ref], "text": "原话"}]}
    result = expand_expression_source_ref_aliases(value, aliases=aliases)
    assert result["world_claims"][0] == {**value["world_claims"][0], "source_refs": [ref]}
    assert value["world_claims"][0]["source_refs"] == ["S1", "S2", ref]


def test_alias_expansion_preserves_unknown_refs_and_original_malformed_duplicates():
    aliases = SourceRefAliasTable(entries=(("S1", "source:accepted"),), canonical_refs=frozenset({"source:accepted"}))
    value = {"world_claims": [{"source_refs": ["S1", "S1", "unknown", "source:other"]}]}
    result = expand_expression_source_ref_aliases(value, aliases=aliases)
    assert result["world_claims"][0]["source_refs"] == ["source:accepted", "source:accepted", "unknown", "source:other"]
