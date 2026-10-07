#!/usr/bin/env python3
"""Compile native authored history, render it, or import it into an explicit World."""
from __future__ import annotations

import argparse
from datetime import datetime, UTC
import json
from pathlib import Path

from companion_daemon.world_v2.native_story import NativeStorySource, compile_native_story
from companion_daemon.world_v2.native_story_catalogue import native_life_catalogue
from companion_daemon.world_v2.prehistory_authoring import PrehistoryAuthoringBrief
from companion_daemon.world_v2.character_prehistory import CompiledPrehistoryArchive
from companion_daemon.world_v2.prehistory_life_materials_runner import _private_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    compile_command = commands.add_parser('compile')
    for flag in ('source', 'brief', 'archive-id', 'out-dir'):
        compile_command.add_argument('--' + flag, required=True)
    importing = commands.add_parser('import')
    for flag in ('package', 'database', 'actor-ref'):
        importing.add_argument('--' + flag, required=True)
    args = parser.parse_args(argv)
    if args.command == 'compile':
        source = NativeStorySource.model_validate_json(Path(args.source).read_text())
        brief = PrehistoryAuthoringBrief.model_validate_json(Path(args.brief).read_text())
        package = compile_native_story(source, brief=brief, archive_id=args.archive_id)
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        _private_json(out / 'compiled-package.json', package.model_dump(mode='json'))
        stats, reading = native_life_catalogue(source, brief, package)
        _private_json(out / 'coverage.json', stats)
        with (out / 'life-reading.md').open('x') as stream:
            stream.write(reading)
        (out / 'life-reading.md').chmod(0o600)
        print(json.dumps({'status': 'compiled', 'records': len(package.document.records),
            'world_writes': 0, 'model_calls': 0, 'independent_model_review': False}, ensure_ascii=False))
    else:
        from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
        from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
        package = CompiledPrehistoryArchive.model_validate_json(Path(args.package).read_text())
        ledger = SQLiteWorldLedger(path=Path(args.database), world_id=package.document.world_id)
        try:
            before = ledger.project().ledger_sequence
            rows = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=args.actor_ref).import_compiled(
                package, created_at=datetime.now(UTC))
            print(json.dumps({'status': 'imported', 'records': len(rows),
                'events_added': ledger.project().ledger_sequence - before,
                'retention_choices': 0, 'model_calls': 0}, ensure_ascii=False))
        finally:
            ledger.close()


if __name__ == '__main__':
    main()
