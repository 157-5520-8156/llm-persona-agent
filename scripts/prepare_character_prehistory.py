#!/usr/bin/env python3
"""Offline prehistory author/review workflow. No API calls or database writes."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from companion_daemon.character import load_character
from companion_daemon.world_v2.character_prehistory import (
    PrehistoryArchiveAcceptedPayload, PrehistoryArchiveDocument, PrehistoryRecordImportedPayload,
)
from companion_daemon.world_v2.prehistory_authoring import (
    PrehistoryAuthoringBrief, PrehistoryCreationReview, author_request, package_reviewed, review_request,
    PrehistorySemanticReview, bind_semantic_review, semantic_review_request,
)
from companion_daemon.world_v2.schemas import WorldEvent


def brief_from_database(*, database, profile_path, world_id, actor_ref, born_at, author_ref, source_ref):
    """Read original start and accepted archives through SQLite's read-only URI."""
    starts, archives, records = [], {}, {}
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute(
            "SELECT event_json,event_hash FROM world_v2_events WHERE world_id=? "
            "AND json_extract(event_json,'$.event_type') IN "
            "('WorldStarted','CharacterPrehistoryArchiveAccepted','CharacterPrehistoryRecordImported') "
            "ORDER BY ledger_sequence", (world_id,),
        )
        for raw, event_hash in rows:
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() != event_hash:
                raise ValueError("source event bytes differ from their immutable hash")
            event = WorldEvent.model_validate_json(raw)
            if event.world_id != world_id:
                raise ValueError("event differs from selected World")
            if event.event_type == "WorldStarted":
                starts.append((event, event_hash))
            elif event.event_type == "CharacterPrehistoryArchiveAccepted":
                archive = PrehistoryArchiveAcceptedPayload.model_validate_json(event.payload_json)
                if archive.manifest.actor_ref == actor_ref:
                    archives[archive.manifest.archive_id] = archive.manifest
            else:
                record = PrehistoryRecordImportedPayload.model_validate_json(event.payload_json)
                records.setdefault(record.archive_id, []).append(record.record)
    if len(starts) != 1:
        raise ValueError("brief requires exactly one original WorldStarted")
    start, start_hash = starts[0]
    accepted = []
    for archive_id, manifest in archives.items():
        document = PrehistoryArchiveDocument(
            contract="character-prehistory-archive.1", archive_id=archive_id, world_id=world_id,
            actor_ref=actor_ref, source_artifact_ref=manifest.source_artifact_ref,
            entities=manifest.entities, records=tuple(records.get(archive_id, ())),
        )
        if document.manifest() != manifest:
            raise ValueError("accepted history differs from its committed manifest")
        accepted.append(document)
    profile_path = Path(profile_path).resolve()
    profile = load_character(str(profile_path))
    # Historical creation needs character constraints, not live-user or
    # conversational instructions. The exact full profile file remains pinned.
    historical_profile = profile.model_dump(include={
        "name", "identity", "appearance", "background", "canonical_facts",
        "personality", "values", "daily_life",
    })
    return PrehistoryAuthoringBrief(
        world_id=world_id, actor_ref=actor_ref, world_started_at=start.logical_time,
        world_start_event_ref=start.event_id, world_start_event_hash=start_hash,
        born_at=born_at, created_at=datetime.now(UTC), author_ref=author_ref,
        draft_source_ref=source_ref, profile_source_ref=str(profile_path),
        profile_sha256=hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        profile=historical_profile, accepted_archives=tuple(accepted),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    brief = commands.add_parser("brief")
    for flag in ("database", "profile", "world-id", "actor-ref", "born-at", "author-ref", "source-ref"):
        brief.add_argument("--" + flag, required=True)
    brief.add_argument("--out", required=True)
    for name in ("author-request", "review-request", "semantic-review-request", "package-reviewed"):
        command = commands.add_parser(name)
        command.add_argument("--brief", required=True)
        command.add_argument("--out", required=True)
        if name != "author-request":
            command.add_argument("--draft", required=True)
        if name == "package-reviewed":
            command.add_argument("--review", required=True)
            command.add_argument("--review-artifact-ref", required=True)
    binding = commands.add_parser("bind-semantic-review")
    for flag in ("request", "response", "reviewer-ref", "reviewed-at", "out"):
        binding.add_argument("--" + flag, required=True)
    args = parser.parse_args(argv)
    if args.command == "brief":
        value = brief_from_database(database=args.database, profile_path=args.profile,
            world_id=args.world_id, actor_ref=args.actor_ref,
            born_at=datetime.fromisoformat(args.born_at), author_ref=args.author_ref, source_ref=args.source_ref)
    elif args.command == "bind-semantic-review":
        value = bind_semantic_review(
            json.loads(Path(args.request).read_text()),
            PrehistorySemanticReview.model_validate_json(Path(args.response).read_text()),
            reviewer_ref=args.reviewer_ref, reviewed_at=datetime.fromisoformat(args.reviewed_at),
        )
    else:
        brief = PrehistoryAuthoringBrief.model_validate_json(Path(args.brief).read_text())
        if args.command == "author-request":
            value = author_request(brief)
        else:
            draft = PrehistoryArchiveDocument.model_validate_json(Path(args.draft).read_text())
            if args.command == "review-request":
                value = review_request(brief, draft)
            elif args.command == "semantic-review-request":
                value = semantic_review_request(brief, draft)
            else:
                review = PrehistoryCreationReview.model_validate_json(Path(args.review).read_text())
                value = package_reviewed(brief, draft, review, review_artifact_ref=args.review_artifact_ref)
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    with Path(args.out).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"written": str(Path(args.out).resolve()), "command": args.command,
                      "provider_calls": 0, "database_writes": 0}))


if __name__ == "__main__":
    main()
