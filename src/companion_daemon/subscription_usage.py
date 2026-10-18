"""Durable token admission for a subscription route; never infer a CNY price."""

from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time
import uuid

import httpx

from .llm import DeepSeekChatModel, ProviderCircuitBreaker, ProviderResultFormatError, exact_json_object_text, admit_turn_provider_call, complete_with_timeout
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


class _BackgroundTokenBudget:
    """Shared durable admission and deadlines; provider adapters own their wire."""

    mirrors_debug_spend = False

    def __init__(self, *args, token_ledger, timeout_seconds=90, transport=None, **kwargs):
        self.token_ledger = token_ledger
        self.timeout_seconds = timeout_seconds
        self.background_timeout_seconds = timeout_seconds
        kwargs.setdefault("circuit_breaker", ProviderCircuitBreaker())
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


class SubscriptionAnthropicModel(_BackgroundTokenBudget, AnthropicChatModel):
    """The existing Anthropic subscription route and its original wire."""


class BackgroundGLMModel(_BackgroundTokenBudget, DeepSeekChatModel):
    """GLM Chat Completions using the shared transport and strict local validation.

    GLM-5.3 uses enabled thinking and auto tool selection. This is not a claim
    of provider-enforced JSON Schema or of the proxy's upstream identity.
    """

    provider = "glm"
    supports_strict_tool_choice = False
    supports_single_tool_result = True

    def __init__(self, *args, reasoning_effort="low", tool_result_carrier="native", **kwargs):
        if reasoning_effort not in {"low", "high", "max"}:
            raise ValueError("GLM-5.3 reasoning effort must be low, high or max")
        if tool_result_carrier not in {"native", "native_or_json_object"}:
            raise ValueError("unknown GLM tool result carrier")
        self._tool_result_carrier = tool_result_carrier
        super().__init__(*args, thinking_enabled=True, reasoning_effort=reasoning_effort, **kwargs)
        self.model_version = self.model + ":object-tool-root.2"
        if tool_result_carrier != "native":
            self.model_version += ":json-object-carrier.1"

    @property
    def single_tool_response_carrier(self):
        return self._tool_result_carrier

    def _response_message(self, payload):
        message = super()._response_message(payload)
        if isinstance(message, dict):
            message = {**message, "native_stop_reason": payload["choices"][0].get("finish_reason")}
        return message


    @staticmethod
    def _root_union(tool):
        schema = tool.get("function", {}).get("parameters", {})
        return any(key in schema for key in ("anyOf", "oneOf"))

    def request_payload(self, messages, *, temperature, json_object=False, tools=None, tool_choice=None):
        if tools:
            if isinstance(tool_choice, dict):
                # Reviewers also call this adapter directly. Preserve their
                # one named result while using GLM's advertised auto carrier.
                from .world_v2.character_interior.single_tool_transport import resolve_single_tool_transport
                name = tools[0].get("function", {}).get("name")
                tool_choice = resolve_single_tool_transport(
                    provider=self, tools=tools, tool_choice=tool_choice,
                    identity={"tool_name": name},
                ).tool_choice
            elif tool_choice is not None and tool_choice != "auto":
                raise ValueError("GLM supports only auto or one exact named result")
            tools = deepcopy(tools)
            for tool in tools:
                tool["function"].pop("strict", None)
        if tools and any(self._root_union(tool) for tool in tools):
            if len(tools) != 1:
                raise ValueError("GLM root-union transport requires one declared tool")
            # Reuse the schema-only expander before moving its root, so local
            # references retain their meaning. The original role schema is untouched.
            from .world_v2.character_interior.local_schema_references import expand_local_schema_references
            function = tools[0]["function"]
            function["parameters"] = {
                "type": "object",
                "properties": {"result": expand_local_schema_references(function["parameters"])},
                "required": ["result"], "additionalProperties": False,
            }
        return super().request_payload(messages, temperature=temperature, json_object=json_object,
                                       tools=tools, tool_choice=tool_choice)

    def _response_tool_content(self, message, *, tools, tool_choice, expected_tool_name):
        raw = super()._response_tool_content(message, tools=tools, tool_choice=tool_choice,
                                            expected_tool_name=expected_tool_name)
        if raw is None:
            if self._tool_result_carrier == "native" or message.get("tool_calls"):
                return None
            if (len(tools) != 1 or expected_tool_name is None
                    or tools[0].get("function", {}).get("name") != expected_tool_name
                    or not (tool_choice == "auto" or (isinstance(tool_choice, dict)
                        and tool_choice.get("type") == "function"
                        and tool_choice.get("function", {}).get("name") == expected_tool_name))
                    or message.get("native_stop_reason") != "stop"):
                raise ValueError("GLM JSON text carrier requires one exact auto schema and stop")
            # This is a declared text response, never a synthesized tool call.
            # Preserve all fields for the original role/source validators.
            return exact_json_object_text(message.get("content"))
        if not any(self._root_union(tool) for tool in tools):
            return raw
        def unique_members(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("GLM tool result contains duplicate keys")
                result[key] = value
            return result
        try:
            envelope = json.loads(raw, object_pairs_hook=unique_members)
            if not isinstance(envelope, dict) or set(envelope) != {"result"} or not isinstance(envelope["result"], dict):
                raise ValueError("GLM root-union tool needs its exact result object envelope")
            # Remove only the declared transport wrapper. Original role/source
            # validation still receives the complete model-authored result.
            return json.dumps(envelope["result"], ensure_ascii=False, allow_nan=False)
        except (ValueError, RecursionError) as exc:
            raise ProviderResultFormatError(raw_result=raw,
                detail=f"invalid GLM tool result envelope: {exc}") from exc
