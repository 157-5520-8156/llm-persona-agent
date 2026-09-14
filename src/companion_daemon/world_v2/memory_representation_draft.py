"""Immutable, non-authorizing drafts for the future memory compression lifecycle.

Preparation reads the exact eligible candidate at a pinned cursor through the
existing memory compiler. Storage never makes a draft a MemoryCandidate, a
retrievable summary, an accepted character choice, or evidence for a Fact.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .life_content_store import ImmutableLifeContentStore, StoredLifeContent
from .memory_retrieval import MemoryRetrievalCompiler, MemoryRetrievalItem
from .schema_core import FrozenModel, canonicalize_json_value
from .schemas import ProjectionCursor

_HASH = r"^[0-9a-f]{64}$"
_KIND = "memory_representation_draft"
_REF_PREFIX = "memory-representation-draft:sha256:"


def _canonical(value: object) -> str:
    if isinstance(value, FrozenModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        canonicalize_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class MemoryRepresentationInput(FrozenModel):
    """Exact input to bind an eventual role proposal, not an author permission."""

    contract: Literal["memory-representation-input.1"] = "memory-representation-input.1"
    world_id: str = Field(min_length=1, max_length=256)
    actor_ref: str = Field(min_length=1, max_length=256)
    cursor: ProjectionCursor
    candidate_entity_revision: int = Field(ge=1)
    candidate_semantic_fingerprint: str = Field(pattern=_HASH)
    previous_summary_ref: str = Field(min_length=1, max_length=512)
    previous_summary_payload_hash: str = Field(pattern=_HASH)
    reading: MemoryRetrievalItem

    @model_validator(mode="after")
    def historical_actor_matches(self):
        if any(
            source.prehistory is not None and source.prehistory.actor_ref != self.actor_ref
            for source in self.reading.source_excerpts
        ):
            raise ValueError("memory representation input changes historical ownership")
        return self

    @property
    def input_hash(self) -> str:
        return _hash(self)


class MemoryRepresentationDraft(FrozenModel):
    """Proposed prose and a declared author-result association; neither verified."""

    contract: Literal["memory-representation-draft.1"] = "memory-representation-draft.1"
    authority: Literal["unaccepted_draft"] = "unaccepted_draft"
    operation: Literal["compress"] = "compress"
    input: MemoryRepresentationInput
    summary_text: str = Field(min_length=1, max_length=1800)
    role_result_ref: str = Field(min_length=1, max_length=512)
    role_result_hash: str = Field(pattern=_HASH)
    author_association: Literal["unverified"] = "unverified"

    @model_validator(mode="after")
    def summary_is_nonblank(self):
        if not self.summary_text.strip():
            raise ValueError("memory representation draft requires authored text")
        return self


class MemoryRepresentationDraftRef(FrozenModel):
    authority: Literal["unaccepted_draft"] = "unaccepted_draft"
    content_ref: str = Field(min_length=1, max_length=512)
    content_payload_hash: str = Field(pattern=_HASH)
    input_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def reference_is_content_addressed(self):
        if self.content_ref != _REF_PREFIX + self.content_payload_hash:
            raise ValueError("memory draft reference does not bind its content hash")
        return self


def prepare_memory_representation_input(
    *,
    ledger,
    candidate_id: str,
    actor_ref: str,
    cursor: ProjectionCursor,
    life_content_store: ImmutableLifeContentStore | None = None,
) -> MemoryRepresentationInput:
    """Read current eligible content, never an archive fallback or caller text."""
    projection = ledger.project_at(cursor)
    candidate = next(
        (item for item in projection.memory_candidates if item.candidate_id == candidate_id), None
    )
    if candidate is None:
        raise ValueError("memory representation candidate is unavailable at the cursor")
    result = MemoryRetrievalCompiler(ledger=ledger, life_content_store=life_content_store).compile(
        cursor=cursor,
        candidates=(candidate,),
        projection=projection,
        actor_ref=actor_ref,
        viewer_privacy_ceiling="private",
    )
    if result.suppressions or len(result.items) != 1:
        raise ValueError("memory representation requires a complete eligible reading")
    return MemoryRepresentationInput(
        world_id=ledger.world_id,
        actor_ref=actor_ref,
        cursor=cursor,
        candidate_entity_revision=candidate.entity_revision,
        candidate_semantic_fingerprint=candidate.semantic_fingerprint,
        previous_summary_ref=candidate.values.summary_ref,
        previous_summary_payload_hash=candidate.values.summary_payload_hash,
        reading=result.items[0],
    )


def persist_memory_representation_draft(
    *,
    store: ImmutableLifeContentStore,
    draft: MemoryRepresentationDraft,
) -> MemoryRepresentationDraftRef:
    """Persist unaccepted bytes using the existing immutable sidecar adapter."""
    # Revalidate even a model_copy or a test fixture; storage must not turn
    # an unchecked Python object into a stronger authority token.
    draft = MemoryRepresentationDraft.model_validate_json(draft.model_dump_json())
    text = _canonical(draft)
    digest = hashlib.sha256(text.encode()).hexdigest()
    reference = MemoryRepresentationDraftRef(
        content_ref=_REF_PREFIX + digest,
        content_payload_hash=digest,
        input_hash=draft.input.input_hash,
    )
    store.put_if_absent(
        StoredLifeContent(
            content_ref=reference.content_ref,
            content_kind=_KIND,
            content_payload_hash=digest,
            text=text,
        )
    )
    # The legacy sidecar tolerates identical bytes across some content lanes.
    # That does not grant permission to reuse another lane as a memory draft.
    if (
        read_memory_representation_draft(
            store=store, reference=reference, expected_input=draft.input
        )
        is None
    ):
        raise ValueError("memory draft store did not retain the written content")
    return reference


def read_memory_representation_draft(
    *,
    store: ImmutableLifeContentStore,
    reference: MemoryRepresentationDraftRef,
    expected_input: MemoryRepresentationInput,
) -> MemoryRepresentationDraft | None:
    """Audit read only. A caller still needs separate role and ledger acceptance."""
    reference = MemoryRepresentationDraftRef.model_validate_json(reference.model_dump_json())
    expected_input = MemoryRepresentationInput.model_validate_json(expected_input.model_dump_json())
    if reference.input_hash != expected_input.input_hash:
        raise ValueError("memory draft does not bind the expected input")
    record = store.read_exact(content_ref=reference.content_ref)
    if record is None:
        return None
    if (
        record.content_kind != _KIND
        or record.content_ref != reference.content_ref
        or record.content_payload_hash != reference.content_payload_hash
        or hashlib.sha256(record.text.encode()).hexdigest() != reference.content_payload_hash
    ):
        raise ValueError("memory draft sidecar does not match its exact lane and bytes")
    draft = MemoryRepresentationDraft.model_validate_json(record.text)
    if _canonical(draft) != record.text or draft.input != expected_input:
        raise ValueError("memory draft bytes or input binding changed")
    return draft
