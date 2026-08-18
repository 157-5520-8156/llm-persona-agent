"""Closed accepted-manifest value for one persisted Appraisal proposal.

This is intentionally a narrow production seam.  It does not attempt to turn
the old generic ``DecisionProposal`` compiler into a second authority path;
instead it binds the already typed, source-provenanced appraisal proposal to
its one mutation. Appraisal-owned triggers complete in the same batch;
shared-owner triggers such as ``proactive_action_deliberation`` stay claimed
so the owning vertical can still authorize her visible action.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .schema_core import FrozenModel


APPRAISAL_ACCEPTANCE_MANIFEST_VERSION = "appraisal-acceptance.1"
APPRAISAL_TRIGGER_RETAINED_EVENT_PREFIX = "event:appraisal-trigger-retained:"
_RETAINED_SHARED_OWNER_PROCESS_KIND = "proactive_action_deliberation"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def canonical_appraisal_acceptance_value_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def appraisal_source_trigger_is_retained(completion_event_id: str) -> bool:
    """True when the owning vertical, not appraisal acceptance, closes the trigger."""

    return completion_event_id.startswith(APPRAISAL_TRIGGER_RETAINED_EVENT_PREFIX)


def retained_appraisal_trigger_payload(*, trigger_id: str) -> dict[str, str]:
    """Closed virtual completion body hashed into the accepted manifest.

    No ``TriggerProcessCompleted`` event is emitted for this payload.  The
    hash still binds the retained trigger identity so a two-event batch cannot
    silently drop a different trigger.
    """

    return {
        "status": "retained_by_owning_process",
        "trigger_id": trigger_id,
        "process_kind": _RETAINED_SHARED_OWNER_PROCESS_KIND,
    }


def canonical_appraisal_acceptance_manifest_hash(value: dict[str, object]) -> str:
    material = dict(value)
    material.pop("manifest_hash", None)
    material.setdefault("manifest_version", APPRAISAL_ACCEPTANCE_MANIFEST_VERSION)
    return canonical_appraisal_acceptance_value_hash(material)


class AppraisalAcceptanceManifest(FrozenModel):
    """A self-hashing, complete authority record for one accepted appraisal."""

    manifest_version: Literal["appraisal-acceptance.1"] = APPRAISAL_ACCEPTANCE_MANIFEST_VERSION
    status: Literal["accepted"] = "accepted"
    acceptance_id: str = Field(min_length=1, max_length=256)
    proposal_id: str = Field(min_length=1, max_length=256)
    proposal_event_ref: str = Field(min_length=1, max_length=512)
    proposal_event_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluated_world_revision: int = Field(ge=0)
    accepted_change_id: str = Field(min_length=1, max_length=256)
    accepted_change_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    trigger_id: str = Field(min_length=1, max_length=256)
    mutation_event_id: str = Field(min_length=1, max_length=512)
    mutation_event_type: Literal[
        "AppraisalAccepted", "AppraisalContradicted", "AppraisalSuperseded"
    ]
    mutation_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    completion_event_id: str = Field(min_length=1, max_length=512)
    completion_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def self_hash_is_exact(self) -> AppraisalAcceptanceManifest:
        expected = canonical_appraisal_acceptance_manifest_hash(self.model_dump(mode="json"))
        if self.manifest_hash != expected:
            raise ValueError("appraisal acceptance manifest hash is invalid")
        return self


def build_appraisal_acceptance_manifest(**values: object) -> AppraisalAcceptanceManifest:
    material = {
        "manifest_version": APPRAISAL_ACCEPTANCE_MANIFEST_VERSION,
        "status": "accepted",
        **values,
    }
    material["manifest_hash"] = canonical_appraisal_acceptance_manifest_hash(material)
    return AppraisalAcceptanceManifest.model_validate(material, strict=True)


__all__ = [
    "APPRAISAL_ACCEPTANCE_MANIFEST_VERSION",
    "APPRAISAL_TRIGGER_RETAINED_EVENT_PREFIX",
    "AppraisalAcceptanceManifest",
    "appraisal_source_trigger_is_retained",
    "build_appraisal_acceptance_manifest",
    "canonical_appraisal_acceptance_manifest_hash",
    "canonical_appraisal_acceptance_value_hash",
    "retained_appraisal_trigger_payload",
]
