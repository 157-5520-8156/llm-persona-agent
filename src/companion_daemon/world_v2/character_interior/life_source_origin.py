"""Retain the original Life Capsule privately across snapshot transformations.

The production projection supplies this origin at compilation time. It binds
selection bytes, not semantic truth or the later author's purpose-specific view.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal, TYPE_CHECKING

from pydantic import Field, model_validator

from ..schema_core import FrozenModel

if TYPE_CHECKING:
    from ..context_capsule import ContextCapsule

MAX_ORIGIN_BYTES = 1_048_576


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LifeSourceOrigin(FrozenModel):
    contract: Literal["life-source-origin.1"] = "life-source-origin.1"
    capsule_json: str = Field(min_length=2, max_length=MAX_ORIGIN_BYTES)
    capsule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def capsule(self) -> ContextCapsule:
        from ..context_capsule import ContextCapsule

        if self.contract != "life-source-origin.1":
            raise ValueError("unsupported Life source origin contract")
        if len(self.capsule_json.encode("utf-8")) > MAX_ORIGIN_BYTES:
            raise ValueError("Life source origin exceeds its byte bound; do not truncate")
        capsule = ContextCapsule.model_validate_json(self.capsule_json)
        if (
            capsule.provenance_kind != "trusted_resolver_compiled"
            or self.capsule_json != canonical(capsule.model_dump(mode="json"))
            or self.capsule_sha256 != digest(self.capsule_json)
        ):
            raise ValueError("Life source origin differs from its complete trusted Capsule")
        return capsule

    @model_validator(mode="after")
    def original_is_complete(self):
        self.capsule()
        return self

    @classmethod
    def from_capsule(cls, capsule: ContextCapsule) -> "LifeSourceOrigin":
        from ..context_capsule import ContextCapsule

        if not isinstance(capsule, ContextCapsule):
            raise ValueError("Life source origin needs the original typed Capsule")
        raw = canonical(capsule.model_dump(mode="json"))
        return cls(capsule_json=raw, capsule_sha256=digest(raw))

    def verify_snapshot(self, snapshot) -> None:
        capsule = self.capsule()
        if snapshot.cursor is None or (
            snapshot.world_id, snapshot.actor_ref, snapshot.logical_time,
            snapshot.cursor.world_revision, snapshot.cursor.deliberation_revision,
            snapshot.cursor.ledger_sequence,
        ) != (
            capsule.world_id, capsule.actor_ref, capsule.logical_time,
            capsule.world_revision, capsule.deliberation_revision, capsule.ledger_sequence,
        ):
            raise ValueError("Life source origin belongs to another snapshot prefix")
