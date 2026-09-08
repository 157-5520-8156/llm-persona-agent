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


def _request_json(messages: list[dict[str, str]]) -> str:
    if not isinstance(messages, list) or not messages or any(
        not isinstance(message, dict)
        or set(message) != {"role", "content"}
        or message["role"] not in {"system", "user", "assistant"}
        or not isinstance(message["content"], str)
        for message in messages
    ):
        raise ValueError("world_author_request.invalid_messages")
    return json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_world_author_request(
    *, content_store: ImmutableLifeContentStore, messages: list[dict[str, str]],
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
) -> list[dict[str, str]]:
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
