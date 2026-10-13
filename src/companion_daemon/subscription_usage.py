"""Durable token admission for a subscription route; never infer a CNY price."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
import uuid

import httpx

from .llm import admit_turn_provider_call, complete_with_timeout
from .llm_anthropic import AnthropicChatModel


class SubscriptionTokenLedger:
    def __init__(self, path, *, five_hour_tokens=150_000_000, weekly_tokens=500_000_000,
                 clock=time.time):
        if min(five_hour_tokens, weekly_tokens) <= 0:
            raise ValueError("subscription token limits must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.five_hour_tokens = five_hour_tokens
        self.weekly_tokens = weekly_tokens
        self.clock = clock
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS subscription_requests (
                reservation_id TEXT PRIMARY KEY, created_at REAL NOT NULL,
                purpose TEXT NOT NULL, model TEXT NOT NULL, reserved_tokens INTEGER NOT NULL,
                reported_tokens INTEGER, state TEXT NOT NULL, status TEXT NOT NULL DEFAULT '',
                latency_ms INTEGER NOT NULL DEFAULT 0)""")

    @staticmethod
    def _used(connection, since):
        return connection.execute("""SELECT COALESCE(SUM(CASE
            WHEN state='not_billed' THEN 0 WHEN state='known' THEN reported_tokens
            ELSE MAX(reserved_tokens, COALESCE(reported_tokens, 0)) END), 0)
            FROM subscription_requests WHERE created_at >= ?""", (since,)).fetchone()[0]

    def reserve(self, *, purpose, model, tokens):
        if not purpose or purpose in {'unclassified', 'generic'}:
            raise ValueError('subscription request requires an attributable purpose')
        if type(tokens) is not int or tokens <= 0:
            raise ValueError('subscription reservation must be positive')
        now, token = self.clock(), 'subscription:' + uuid.uuid4().hex
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            for seconds, limit in ((5 * 3600, self.five_hour_tokens),
                                   (7 * 86400, self.weekly_tokens)):
                if self._used(connection, now - seconds) + tokens > limit:
                    raise ValueError('subscription_token_limit_exceeded')
            connection.execute('INSERT INTO subscription_requests VALUES (?,?,?,?,?,NULL,?,?,?)',
                               (token, now, purpose, model, tokens, 'pending', '', 0))
        return token

    def record(self, usage):
        if usage.billing_state not in {'known', 'unknown', 'not_billed'}:
            raise ValueError('invalid subscription usage state')
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT purpose,model,state FROM subscription_requests '
                                     'WHERE reservation_id=?',
                                     (usage.budget_reservation_id,)).fetchone()
            if row is None or row[:2] != (usage.purpose, usage.model):
                raise ValueError('subscription usage has no matching admission')
            # A completed reservation cannot be overwritten by a duplicate callback.
            if row[2] in {'known', 'not_billed'}:
                raise ValueError('subscription reservation already settled')
            connection.execute('UPDATE subscription_requests SET reported_tokens=?,state=?, '
                               'status=?,latency_ms=? WHERE reservation_id=?',
                               (usage.total_tokens, usage.billing_state, usage.status,
                                usage.latency_ms, usage.budget_reservation_id))

    def snapshot(self):
        now = self.clock()
        with closing(sqlite3.connect(self.path)) as connection:
            known, unknown, count = connection.execute("""SELECT
                COALESCE(SUM(CASE WHEN state='known' THEN reported_tokens ELSE 0 END),0),
                COALESCE(SUM(state IN ('unknown','pending')),0), COUNT(*)
                FROM subscription_requests""").fetchone()
            return {'accounting': 'subscription_reported_tokens', 'cny_cost': None,
                    'quota_scope': 'this_ledger_only', 'requests': count,
                    'known_reported_tokens': known, 'unknown_or_pending_requests': unknown,
                    'reserved_or_used_tokens_5h': self._used(connection, now - 5 * 3600),
                    'reserved_or_used_tokens_7d': self._used(connection, now - 7 * 86400)}


class SubscriptionAnthropicModel(AnthropicChatModel):
    """Same native protocol with token reservations and an independent time budget."""

    mirrors_debug_spend = False

    def __init__(self, *args, token_ledger, timeout_seconds=90, transport=None, **kwargs):
        self.token_ledger = token_ledger
        self.timeout_seconds = timeout_seconds
        super().__init__(*args, usage_observer=token_ledger.record, transport=transport,
                         client=httpx.AsyncClient(timeout=timeout_seconds, trust_env=False,
                                                  transport=transport), **kwargs)

    def _admit_world_v2_model_call(self, *, purpose, call_meta, request_payload):
        admit_turn_provider_call(world_id=str(call_meta.get('world_id') or ''),
                                 turn_id=str(call_meta.get('turn_id') or ''))
        # UTF-8 bytes plus the output ceiling and framing is a conservative text envelope.
        tokens = (len(json.dumps(request_payload, ensure_ascii=False).encode('utf-8'))
                  + self.max_completion_tokens + 1024)
        return self.token_ledger.reserve(purpose=purpose, model=self.model, tokens=tokens)

    async def _complete(self, *args, **kwargs):
        return await complete_with_timeout(super()._complete(*args, **kwargs),
                                           timeout_seconds=self.timeout_seconds)
