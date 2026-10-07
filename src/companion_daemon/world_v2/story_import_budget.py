"""Durable, per-batch and shared-task cost admission at the captured HTTP seam.

Only the source-preparation provider path is covered. Role retention and other
runtime calls retain their own budgets; this ledger never calls them free.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime, UTC
from decimal import Decimal, ROUND_CEILING
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from companion_daemon.usage_metrics import (
    CNY_PER_USD, estimate_provider_request_reserve_cny, resolve_model_price,
)


class StoryBudgetExceeded(RuntimeError):
    """No provider request was issued for this denied attempt."""


class StoryBudgetPolicyError(RuntimeError):
    """A changed scope or ambiguous previous request cannot gain a new grant."""


def _micros(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError('story budget must be a finite nonnegative CNY amount')
    return int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def _hash(value):
    return hashlib.sha256(value).hexdigest()


_ACTIVE_BUDGET: ContextVar[tuple | None] = ContextVar('story_import_budget', default=None)


class StoryImportBudget:
    """One immutable budget policy, shared by independently recoverable runs."""

    def __init__(self, path: Path, *, total_limit_cny=5, batch_limit_cny=1):
        self.path = Path(path).resolve()
        self.batch_limit = _micros(batch_limit_cny)
        total = _micros(total_limit_cny)
        if self.batch_limit > total:
            raise ValueError('batch budget cannot exceed the shared total budget')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        with self._transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), total_limit INTEGER NOT NULL, batch_limit INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE, binding TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, attempt TEXT NOT NULL UNIQUE,
                wire_hash TEXT NOT NULL, endpoint TEXT NOT NULL, model TEXT NOT NULL,
                reserve INTEGER NOT NULL, charged INTEGER, price_json TEXT NOT NULL,
                issued_at TEXT NOT NULL, response_hash TEXT, usage_json TEXT)''')
            db.execute('INSERT OR IGNORE INTO policy VALUES (1, ?, ?)', (total, self.batch_limit))
            row = db.execute('SELECT total_limit, batch_limit FROM policy WHERE id=1').fetchone()
            if tuple(row) != (total, self.batch_limit):
                raise StoryBudgetPolicyError('budget ledger limits differ; existing spend cannot be reset by reopening it')

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def scope(self, run_dir: Path, *, binding: str):
        root = Path(run_dir).resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        run_id = _hash(str(root).encode())
        policy_path = root / 'budget-binding.json'
        capture_only = not policy_path.exists() and any(root.rglob('provider-request.json'))
        policy = {'contract': 'story-import-budget-binding.1', 'ledger': str(self.path),
                  'run_id': run_id, 'binding': binding, 'legacy_capture_only': capture_only}
        if policy_path.exists():
            recorded = json.loads(policy_path.read_text())
            policy['legacy_capture_only'] = recorded.get('legacy_capture_only', False)
            if recorded != policy:
                raise StoryBudgetPolicyError('run is bound to a different budget ledger or input')
        else:
            from .prehistory_life_materials_runner import _private_json
            _private_json(policy_path, policy)
        with self._transaction() as db:
            db.execute('INSERT OR IGNORE INTO runs VALUES (?, ?, ?)', (run_id, str(root), binding))
            if db.execute('SELECT binding FROM runs WHERE run_id=?', (run_id,)).fetchone()[0] != binding:
                raise StoryBudgetPolicyError('budget run input changed')
        self.reconcile(run_id)
        token = _ACTIVE_BUDGET.set((self, run_id, root, policy['legacy_capture_only']))
        try:
            yield
        finally:
            _ACTIVE_BUDGET.reset(token)
            self.reconcile(run_id)

    def reserve(self, *, run_id, attempt: Path, request_body: bytes, endpoint: str):
        payload = json.loads(request_body)
        reserve = _micros(estimate_provider_request_reserve_cny(request_payload=payload))
        model = payload['model']
        now = datetime.now(UTC)
        price = asdict(resolve_model_price(model, at=now))
        price['cny_per_usd'] = CNY_PER_USD
        request_id = _hash((run_id + ':' + str(attempt.resolve())).encode())
        with self._transaction() as db:
            if db.execute('SELECT 1 FROM requests WHERE request_id=?', (request_id,)).fetchone():
                raise StoryBudgetPolicyError('attempt already reserved or billed; recover its capture without reissuing')
            limits = db.execute('SELECT total_limit, batch_limit FROM policy WHERE id=1').fetchone()
            total = db.execute('SELECT COALESCE(SUM(COALESCE(charged, reserve)), 0) FROM requests').fetchone()[0]
            batch = db.execute('SELECT COALESCE(SUM(COALESCE(charged, reserve)), 0) FROM requests WHERE run_id=?', (run_id,)).fetchone()[0]
            if total + reserve > limits['total_limit'] or batch + reserve > limits['batch_limit']:
                raise StoryBudgetExceeded(f'story import budget exhausted before transport; next reserve CNY {reserve / 1_000_000:.6f}')
            db.execute('INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL)',
                (request_id, run_id, str(attempt.resolve()), _hash(request_body), endpoint, model,
                 reserve, json.dumps(price, sort_keys=True), now.isoformat()))
        return request_id

    def settle_capture(self, request_id: str):
        with self._transaction() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise StoryBudgetPolicyError('capture has no budget reservation')
            attempt = Path(row['attempt'])
            request_file, response_file = attempt / 'provider-request.json', attempt / 'provider-response.json'
            if not request_file.exists() or not response_file.exists():
                return
            request, response = json.loads(request_file.read_text()), json.loads(response_file.read_text())
            if (_hash(request['body_utf8'].encode()) != row['wire_hash']
                    or request.get('body_sha256') != row['wire_hash'] or request.get('url') != row['endpoint']):
                raise StoryBudgetPolicyError('captured request differs from reserved wire bytes')
            raw = response['body_utf8'].encode()
            if response.get('body_sha256') != _hash(raw):
                raise StoryBudgetPolicyError('captured response hash changed')
            if row['response_hash'] is not None and row['response_hash'] != _hash(raw):
                raise StoryBudgetPolicyError('settled response changed')
            try:
                usage = json.loads(raw).get('usage', {})
            except (ValueError, AttributeError):
                return
            charged = _price_usage(usage, json.loads(row['price_json']))
            if charged is None:
                return  # Missing/invalid tokens retain the entire unknown reserve.
            db.execute('UPDATE requests SET charged=?, response_hash=?, usage_json=? WHERE request_id=?',
                (charged, _hash(raw), json.dumps(usage, sort_keys=True), request_id))

    def reconcile(self, run_id=None):
        with self._transaction() as db:
            rows = db.execute('SELECT request_id FROM requests' + (' WHERE run_id=?' if run_id else ''),
                              (run_id,) if run_id else ()).fetchall()
        for row in rows:
            self.settle_capture(row[0])

    def snapshot(self):
        with self._transaction() as db:
            policy = dict(db.execute('SELECT * FROM policy WHERE id=1').fetchone())
            rows = [dict(row) for row in db.execute('SELECT * FROM requests ORDER BY issued_at, request_id')]
            runs = [dict(row) for row in db.execute('SELECT * FROM runs ORDER BY run_id')]
        spent = sum(row['charged'] or 0 for row in rows)
        reserved = sum(row['reserve'] for row in rows if row['charged'] is None)
        return {'contract': 'story-import-budget-report.1', 'scope': 'source_preparation_only',
            'total_limit_cny': policy['total_limit'] / 1_000_000, 'batch_limit_cny': policy['batch_limit'] / 1_000_000,
            'spent_cny': spent / 1_000_000, 'reserved_cny': reserved / 1_000_000,
            'remaining_cny': max(0, policy['total_limit'] - spent - reserved) / 1_000_000,
            'unknown_or_inflight_count': sum(row['charged'] is None for row in rows),
            'request_count': len(rows), 'requests': rows,
            'excluded_stages': ['source_authoring', 'role_retention', 'embedding', 'runtime_recall'],
            'batches': [{**run, 'spent_cny': sum(r['charged'] or 0 for r in rows if r['run_id'] == run['run_id']) / 1_000_000,
                'reserved_cny': sum(r['reserve'] for r in rows if r['run_id'] == run['run_id'] and r['charged'] is None) / 1_000_000,
                'request_count': sum(r['run_id'] == run['run_id'] for r in rows)} for run in runs]}


def _price_usage(usage, price):
    if not isinstance(usage, dict):
        return None
    prompt, output = usage.get('prompt_tokens'), usage.get('completion_tokens')
    if type(prompt) is not int or prompt <= 0 or type(output) is not int or output < 0:
        return None
    hit, miss = usage.get('prompt_cache_hit_tokens'), usage.get('prompt_cache_miss_tokens')
    if type(hit) is not int or type(miss) is not int or min(hit, miss) < 0 or hit + miss != prompt:
        hit, miss = 0, prompt
    details = usage.get('completion_tokens_details') or {}
    reasoning = details.get('reasoning_tokens', 0) if isinstance(details, dict) else 0
    if type(reasoning) is not int or reasoning < 0:
        return None
    native = price['cache_hit_cny_per_million'] is not None
    unit, rate = ('cny', Decimal(1)) if native else ('usd', Decimal(str(price['cny_per_usd'])))
    micros = sum(Decimal(str(price[f'{name}_{unit}_per_million'])) * count
                 for name, count in (('cache_hit', hit), ('cache_miss', miss), ('output', max(output, reasoning)))) * rate
    return int(micros.to_integral_value(rounding=ROUND_CEILING))


def reserve_current_request(attempt, request_body, endpoint):
    active = _ACTIVE_BUDGET.get()
    if active is None:
        return None
    budget, run_id, root, capture_only = active
    if capture_only:
        raise StoryBudgetPolicyError('legacy capture-only run cannot issue new paid requests under an empty budget history')
    if not Path(attempt).resolve().is_relative_to(root):
        raise StoryBudgetPolicyError('provider attempt is outside the bound import run')
    return budget, budget.reserve(run_id=run_id, attempt=attempt, request_body=request_body, endpoint=endpoint)


def budgeted_story_import(function):
    @wraps(function)
    async def wrapped(*, budget=None, **kwargs):
        root = Path(kwargs['run_dir'])
        if budget is None and kwargs.get('completion') is not None:
            if (root / 'budget-binding.json').exists():
                raise StoryBudgetPolicyError('a metered run cannot switch to an unmetered completion')
            return await function(**kwargs)  # Existing offline completion fixtures.
        if kwargs.get('completion') is not None:
            raise StoryBudgetPolicyError('budgeted imports require the captured provider transport')
        budget = budget or StoryImportBudget(root / 'import-budget.sqlite')
        from .character_prehistory import digest
        binding = digest([digest(kwargs['brief']), kwargs['narrative_text'], kwargs['archive_id'],
            kwargs.get('model_name', 'deepseek-flash'), kwargs.get('reviewer_model'),
            kwargs.get('base_url', 'https://api.deepseek.com').rstrip('/'), kwargs.get('max_revisions', 1)])
        with budget.scope(root, binding=binding):
            return await function(**kwargs)
    return wrapped
