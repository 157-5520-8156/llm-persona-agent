"""Read installed source through public Memory/Recall and evaluate cold replay.

No role prompts, model/provider calls, sample answers or delivery. Source
reachability is separate from natural retelling quality.
"""

from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import time
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.character_prehistory import digest, CompiledPrehistoryArchive
from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalCompiler
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.recall_corpus import (
    RecallCorpusCompiler,
    RecallCorpusSources,
    required_recall_authority_refs,
)
from companion_daemon.world_v2.recall_index import (
    RecallCursor,
    RecallSourceBinding,
    RecallQuery,
    InMemoryRecallIndex,
    FeatureHashRecallEmbedding,
)
from companion_daemon.world_v2.recall_model_reading import interior_recall_item
from companion_daemon.world_v2.replay_evaluator import ReplayEvaluator


def verify_frozen_source(state, packages: Path):
    """Require the installed records to be the exact reproduced source book."""
    manifest = json.loads((packages / "manifest.json").read_text())
    expected = {}
    for group in manifest["groups"]:
        package = CompiledPrehistoryArchive.model_validate_json(
            (packages / group["group"] / "compiled-package.json").read_text()
        )
        assert digest(package.document.manifest()) == group["manifest_hash"]
        expected.update((record.record_id, record) for record in package.document.records)
    actual = {
        row.record.record_id: row.record
        for row in state.prehistory_records
        if row.record.record_id.startswith("prehistory-record:celia-dense20-")
    }
    assert expected.keys() == actual.keys()
    assert all(record == actual[ref] for ref, record in expected.items())
    return {"exact_frozen_records": len(expected), "all_source_values_match": True}


def verify(*, database: Path, packages: Path, output: Path):
    world = "world:companion-v2:qq-c2c:geoff"
    actor = "agent:companion"
    started = time.monotonic()
    ledger = SQLiteWorldLedger(path=database, world_id=world)
    try:
        state = ledger.project()
        frozen_source = verify_frozen_source(state, packages)
        cursor = ProjectionCursor(
            world_revision=state.world_revision,
            deliberation_revision=state.deliberation_revision,
            ledger_sequence=state.ledger_sequence,
        )
        sources = [json.loads(p.read_text()) for p in sorted(packages.glob("*/source.json"))]
        new_records = {
            r.record.record_id: r.record
            for r in state.prehistory_records
            if r.record.record_id.startswith("prehistory-record:celia-dense20-")
        }
        reader = MemoryRetrievalCompiler(ledger=ledger, max_excerpt_characters=480)
        memory = reader.compile(
            cursor=cursor,
            candidates=state.memory_candidates,
            projection=state,
            viewer_privacy_ceiling="private",
            actor_ref=actor,
        )
        shown = {
            e.source_id: e
            for item in memory.items
            for e in item.source_excerpts
            if e.source_kind == "prehistory"
        }
        assert new_records.keys() <= shown.keys()
        assert all(
            shown[k].text == r.statement and not shown[k].truncated for k, r in new_records.items()
        )
        assert all(
            item.assessment_scope == "initialization_defaults_not_character_judgment"
            for item in memory.items
            if any(e.source_id in new_records for e in item.source_excerpts)
        )
        hidden = [
            b["text"]
            for s in sources
            for scene in s["scenes"]
            for b in scene["blocks"]
            if b["knowledge"] == "world_only"
        ]
        serialized = memory.model_dump_json()
        assert not any(text in serialized for text in hidden)
        foreign = reader.compile(
            cursor=cursor,
            candidates=state.memory_candidates,
            projection=state,
            viewer_privacy_ceiling="private",
            actor_ref="actor:unrelated-reader",
        )
        assert not any(
            e.source_kind == "prehistory" for item in foreign.items for e in item.source_excerpts
        )
        refs = required_recall_authority_refs(
            RecallCorpusSources(active_memory_candidates=memory.items)
        )
        recall_sources = RecallCorpusSources(
            active_memory_candidates=memory.items,
            authority_bindings=tuple(
                RecallSourceBinding(
                    source_kind="committed_event",
                    authority_type=e.event_type,
                    ref=e.event_id,
                    source_world_revision=e.world_revision,
                    immutable_hash=e.payload_hash,
                )
                for e in state.committed_world_event_refs
                if e.event_id in refs
            ),
        )
        rc = RecallCursor(**cursor.model_dump())
        documents = RecallCorpusCompiler(prehistory_parent_context=True).compile(
            cursor=rc, actor_ref=actor, subject_refs=(actor,), sources=recall_sources
        )
        index = InMemoryRecallIndex(
            embedding=FeatureHashRecallEmbedding(), policy_version="world-v2-recall-index.hybrid.17"
        )
        index.rebuild(cursor=rc, documents=documents)
        latest = {}
        for archive in state.prehistory_archives:
            for episode in archive.manifest.episodes:
                if (
                    episode.episode_ref not in latest
                    or episode.revision > latest[episode.episode_ref].revision
                ):
                    latest[episode.episode_ref] = episode
        active = {d.source_item_ref.split(":window:")[0] for d in documents}
        rows = []
        total_pages = 0
        for episode in latest.values():
            if not any(ref in new_records for ref in episode.record_refs):
                continue
            opaque = "story-episode-view:" + digest([world, actor, episode.episode_ref])
            q = RecallQuery.model_validate_json(
                json.dumps(
                    {
                        "query_text": "读取这一段生活的具体经过",
                        "cursor": rc.model_dump(mode="json"),
                        "actor_ref": actor,
                        "subject_refs": [actor],
                        "viewer_privacy_ceiling": "private",
                        "at": state.logical_time.isoformat(),
                        "accessibility_seed": "dense20-public-reading",
                        "limit": 6,
                        "episode_window": {"episode_ref": opaque, "mode": "detail"},
                    }
                )
            )
            found = []
            pages = 0
            while True:
                result = index.search(q)
                if not result.hits:
                    break
                ids = [h.document.source_item_ref.split(":window:")[0] for h in result.hits]
                assert not set(ids) & set(found)
                found.extend(ids)
                pages += 1
                assert pages <= 128
                display = json.dumps(
                    [
                        interior_recall_item(h.document, index_version=result.index_version)
                        for h in result.hits
                    ],
                    ensure_ascii=False,
                )
                assert episode.title not in display
                q = q.model_copy(
                    update={
                        "episode_window": q.episode_window.model_copy(
                            update={"after_record_ref": ids[-1]}
                        )
                    }
                )
            expected = [ref for ref in episode.record_refs if ref in active]
            assert found == expected, (episode.episode_ref, len(found), len(expected))
            total_pages += pages
            rows.append({"episode_ref": episode.episode_ref, "members": len(found), "pages": pages})
        # Explicit acquisition guard: the baby-rain source was told in2011,
        # although its authored occurrence is2005. Neither as-of reading nor a
        # hidden cursor can make it a remembered event before then.
        baby = "story-episode-view:" + digest(
            [world, actor, "story-episode:celia-dense20-baby-rain"]
        )
        q = RecallQuery.model_validate_json(
            json.dumps(
                {
                    "query_text": "早年家里的雨天",
                    "cursor": rc.model_dump(mode="json"),
                    "actor_ref": actor,
                    "subject_refs": [actor],
                    "viewer_privacy_ceiling": "private",
                    "at": state.logical_time.isoformat(),
                    "knowledge_as_of": "2010-12-31T23:59:59+08:00",
                    "accessibility_seed": "acquisition-boundary",
                    "limit": 6,
                    "episode_window": {"episode_ref": baby, "mode": "detail"},
                }
            )
        )
        assert not index.search(q).hits
        assert (
            len(
                index.search(
                    q.model_copy(
                        update={
                            "knowledge_as_of": datetime.fromisoformat("2011-02-05T00:00:00+08:00")
                        }
                    )
                ).hits
            )
            == 1
        )
        result = {
            "scope": "public_explicit_memory_and_episode_reading_not_natural_dialogue",
            "new_records_exactly_readable": len(new_records),
            "frozen_source": frozen_source,
            "source_texts_complete": True,
            "world_only_absent": len(hidden),
            "foreign_actor_prehistory_empty": True,
            "reported_acquisition_boundary_verified": True,
            "new_or_shared_views": len(rows),
            "detail_pages": total_pages,
            "views": rows,
            "model_calls": 0,
            "seconds_before_replay": time.monotonic() - started,
        }
        output.mkdir(parents=True, exist_ok=True)
        (output / "public-reading.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(
            json.dumps({k: v for k, v in result.items() if k != "views"}, ensure_ascii=False),
            flush=True,
        )
        replay = ReplayEvaluator().evaluate(evidence=ledger.export_replay_evidence())
        cold = {
            "passed": replay.passed,
            "hash_matches": replay.replay_hash_matches,
            "findings": [str(x) for x in replay.findings],
            "seconds": time.monotonic() - started,
            "model_calls": 0,
            "semantic_hash": state.semantic_hash,
            "ledger_sequence": state.ledger_sequence,
        }
        (output / "cold-replay.json").write_text(json.dumps(cold, indent=2))
        print(json.dumps(cold), flush=True)
        assert replay.passed and replay.replay_hash_matches
    finally:
        ledger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("database", "packages", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    verify(**vars(parser.parse_args()))
