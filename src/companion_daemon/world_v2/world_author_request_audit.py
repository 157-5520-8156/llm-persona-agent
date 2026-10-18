"""Exact model-input sidecars, never reconstructed from a later prompt.

These records prove bytes only. The caller must resolve the corresponding
committed ModelResult and Proposal before treating their request hash as
authority. Nothing in this module grants prose or execution authority.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from .life_content_store import (
    ImmutableLifeContentStore,
    StoredLifeContent,
    life_content_payload_hash,
)
from .schema_core import FrozenModel


class WorldAuthorRequestBinding(FrozenModel):
    contract: Literal["world-author-request.1"] = "world-author-request.1"
    content_ref: str = Field(min_length=1, max_length=512)
    content_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    utf8_bytes: int = Field(ge=1)


def _request_json(messages: list[dict[str, object]]) -> str:
    if not isinstance(messages, list) or not messages:
        raise ValueError("world_author_request.invalid_messages")
    pending = None
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("world_author_request.invalid_messages")
        if pending is not None:
            if (set(message) != {"role", "tool_call_id", "content"}
                    or message["role"] != "tool" or message["tool_call_id"] != pending
                    or not isinstance(message["content"], str)):
                raise ValueError("world_author_request.invalid_messages")
            pending = None
        elif (set(message) == {"role", "content"}
                and message["role"] in ("system", "user", "assistant")
                and isinstance(message["content"], str)):
            continue
        else:
            try:
                source = json.loads(messages[1]["content"])
                name = source["world_author_wire"]["tool_name"]
                calls = message["tool_calls"]
                if (set(message) != {"role", "content", "tool_calls"}
                        or message["role"] != "assistant" or message["content"] is not None
                        or not isinstance(name, str) or not name
                        or not isinstance(calls, list) or len(calls) != 1):
                    raise ValueError
                call = calls[0]
                function = call["function"]
                if (set(call) != {"id", "type", "function"} or call["type"] != "function"
                        or not isinstance(call["id"], str) or not call["id"]
                        or set(function) != {"name", "arguments"} or function["name"] != name
                        or not isinstance(function["arguments"], str) or not function["arguments"]):
                    raise ValueError
                pending = call["id"]
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                raise ValueError("world_author_request.invalid_messages") from exc
    if pending is not None:
        raise ValueError("world_author_request.invalid_messages")
    return json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_world_author_request(
    *, content_store: ImmutableLifeContentStore, messages: list[dict[str, object]],
) -> WorldAuthorRequestBinding:
    """Persist the complete bounded input before making a new-protocol call."""
    text = _request_json(messages)
    digest = life_content_payload_hash(text)
    binding = WorldAuthorRequestBinding(
        content_ref="content:world-author-request:" + digest,
        content_payload_hash=digest,
        utf8_bytes=len(text.encode("utf-8")),
    )
    content_store.put_if_absent(StoredLifeContent(
        content_ref=binding.content_ref,
        content_kind="raw_model_request",
        content_payload_hash=digest,
        text=text,
    ))
    # Some adapters permit identical bytes under historical content lanes.
    # An audit request must retain its own exact kind, including on retries.
    read_world_author_request(
        content_store=content_store, binding=binding, expected_request_hash=digest,
    )
    return binding


def read_world_author_request(
    *, content_store: ImmutableLifeContentStore, binding: WorldAuthorRequestBinding,
    expected_request_hash: str,
) -> list[dict[str, object]]:
    binding = WorldAuthorRequestBinding.model_validate_json(binding.model_dump_json())
    stored = content_store.read_exact(content_ref=binding.content_ref)
    if (
        binding.content_ref != "content:world-author-request:" + expected_request_hash
        or binding.content_payload_hash != expected_request_hash
        or stored is None
        or stored.content_kind != "raw_model_request"
        or stored.content_payload_hash != expected_request_hash
        or life_content_payload_hash(stored.text) != expected_request_hash
        or len(stored.text.encode("utf-8")) != binding.utf8_bytes
    ):
        raise ValueError("world_author_request.exact_bytes_unavailable")
    messages = json.loads(stored.text)
    if _request_json(messages) != stored.text:
        raise ValueError("world_author_request.noncanonical_bytes")
    return messages
