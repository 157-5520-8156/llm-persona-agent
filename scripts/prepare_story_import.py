#!/usr/bin/env python3
"""Prepare a reviewed incremental story or a lossless source coverage plan; never writes a World."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from companion_daemon.world_v2.prehistory_authoring import PrehistoryAuthoringBrief
from companion_daemon.world_v2.story_import_runner import prepare_story_import
from companion_daemon.world_v2.story_source_plan import plan_manuscript
from companion_daemon.world_v2.story_import_budget import StoryImportBudget, StoryBudgetExceeded
from companion_daemon.world_v2.prehistory_life_materials_runner import _private_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('--narrative', required=True)
    plan.add_argument('--output', required=True)
    plan.add_argument('--chapter-bytes', type=int, default=24000)
    prepare = commands.add_parser('prepare')
    for name in ('brief', 'narrative', 'archive-id', 'run-dir'):
        prepare.add_argument('--' + name, required=True)
    prepare.add_argument('--model', default='deepseek-flash')
    prepare.add_argument('--reviewer-model')
    prepare.add_argument('--max-revisions', type=int, default=1)
    prepare.add_argument('--budget-ledger', help='Share this ledger across batches to enforce one task ceiling.')
    prepare.add_argument('--batch-budget-cny', type=float, default=1.0)
    prepare.add_argument('--total-budget-cny', type=float, default=5.0)
    args = parser.parse_args(argv)
    source = Path(args.narrative).read_text(encoding='utf-8')
    if args.command == 'plan':
        result = plan_manuscript(source, chapter_bytes_limit=args.chapter_bytes)
        output = Path(args.output)
        with output.open('x', encoding='utf-8') as stream:
            stream.write(result.model_dump_json(indent=2) + '\n')
        output.chmod(0o600)
        print(json.dumps({'status': 'source_coverage_planned', 'chapters': len(result.chapters),
                          'source_sha256': result.source_sha256, 'world_writes': 0, 'provider_calls': 0}))
        return
    key = os.environ.get('DEEPSEEK_DEBUG_API_KEY', '').strip()
    if not key:
        parser.error('DEEPSEEK_DEBUG_API_KEY is required; it is never stored in the run manifest')
    brief = PrehistoryAuthoringBrief.model_validate_json(Path(args.brief).read_text(encoding='utf-8'))
    run_dir = Path(args.run_dir)
    budget = StoryImportBudget(Path(args.budget_ledger) if args.budget_ledger else run_dir / 'import-budget.sqlite',
        total_limit_cny=args.total_budget_cny, batch_limit_cny=args.batch_budget_cny)
    try:
        package = asyncio.run(prepare_story_import(brief=brief, narrative_text=source,
            archive_id=args.archive_id, run_dir=run_dir, api_key=key,
            model_name=args.model, reviewer_model=args.reviewer_model,
            base_url=os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
            max_revisions=args.max_revisions, budget=budget))
    except StoryBudgetExceeded as exc:
        print(json.dumps({'status': 'budget_exceeded', 'reason': str(exc),
            'budget_report': str((run_dir / 'budget-report.json').resolve()), 'world_writes': 0}))
        return 2
    finally:
        _private_json(run_dir / 'budget-report.json', budget.snapshot(), exclusive=False)
    print(json.dumps({'status': 'approved' if package else 'review_rejected',
        'package': str((Path(args.run_dir) / 'reviewed-package.json').resolve()) if package else None,
        'budget_report': str((run_dir / 'budget-report.json').resolve()),
        'world_writes': 0}, ensure_ascii=False))


if __name__ == '__main__':
    raise SystemExit(main())
